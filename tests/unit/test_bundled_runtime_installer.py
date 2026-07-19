from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.optional_packages import runtime_installer


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config" / "runtime-manifests" / "runtime-installer.json"
TARGETS = {
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "x86_64-unknown-linux-gnu",
}


def test_runtime_installer_manifest_pins_every_release_target() -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["tool"] == "uv"
    assert raw["python_minor"] == "3.13"
    assert set(raw["artifacts"]) == TARGETS
    for target, artifact in raw["artifacts"].items():
        assert target in artifact["archive"]
        assert len(artifact["sha256"]) == 64
        int(artifact["sha256"], 16)
        assert len(artifact["executable_sha256"]) == 64
        int(artifact["executable_sha256"], 16)


def test_staging_script_runs_directly_like_release_build() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "stage_bundled_uv.py"), "--help"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--target" in result.stdout


def _synthetic_signed_macho(*, signature: bytes, linkedit_size: int) -> bytes:
    header = bytearray(32)
    header[:4] = b"\xcf\xfa\xed\xfe"
    struct.pack_into("<I", header, 16, 2)  # ncmds
    struct.pack_into("<I", header, 20, 88)  # sizeofcmds
    segment = bytearray(72)
    struct.pack_into("<II", segment, 0, 0x19, 72)
    segment[8:18] = b"__LINKEDIT"
    struct.pack_into("<Q", segment, 32, linkedit_size)
    struct.pack_into("<Q", segment, 48, linkedit_size)
    code_signature = bytearray(16)
    struct.pack_into("<IIII", code_signature, 0, 0x1D, 16, 128, len(signature))
    return bytes(header + segment + code_signature + b"CODEHERE" + signature)


def test_macho_executable_hash_survives_required_resigning(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream-uv"
    developer_id = tmp_path / "developer-id-uv"
    upstream.write_bytes(
        _synthetic_signed_macho(signature=b"UPSTREAM", linkedit_size=4096)
    )
    developer_id.write_bytes(
        _synthetic_signed_macho(signature=b"A MUCH LONGER DEVELOPER SIGNATURE", linkedit_size=8192)
    )

    assert runtime_installer.executable_sha256(
        upstream
    ) == runtime_installer.executable_sha256(developer_id)

    tampered = bytearray(developer_id.read_bytes())
    tampered[120] ^= 0x01
    developer_id.write_bytes(tampered)
    assert runtime_installer.executable_sha256(
        upstream
    ) != runtime_installer.executable_sha256(developer_id)


def test_frozen_installer_never_falls_back_to_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_installer.sys, "frozen", True, raising=False)
    monkeypatch.delenv(runtime_installer.BUNDLED_UV_ENV, raising=False)
    monkeypatch.setattr(
        runtime_installer.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("must not probe PATH"),
    )

    with pytest.raises(RuntimeError, match="missing its bundled runtime installer"):
        runtime_installer.bundled_uv_path()


def test_locked_target_command_needs_no_python_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    uv_path = tmp_path / "uv"
    uv_path.write_bytes(b"executable")
    monkeypatch.setattr(runtime_installer, "bundled_uv_path", lambda: uv_path)
    monkeypatch.setattr(
        runtime_installer,
        "load_runtime_installer_contract",
        lambda: runtime_installer.RuntimeInstallerContract(
            version="0.10.8",
            python_minor="3.13",
            executable_sha256={"aarch64-apple-darwin": "a" * 64},
        ),
    )
    monkeypatch.setattr(
        runtime_installer, "runtime_target_id", lambda: "aarch64-apple-darwin"
    )

    command = runtime_installer.locked_target_install_command(tmp_path / "slot")

    assert command[:3] == [str(uv_path), "pip", "install"]
    assert command[command.index("--python-version") + 1] == "3.13"
    assert command[command.index("--python-platform") + 1] == "aarch64-apple-darwin"
    assert "--only-binary" in command
    assert "--no-config" in command
    assert command[command.index("--python") + 1] == "3.13"
    assert "--managed-python" in command
    assert all("python" not in part.lower() for part in command[:1])


def test_correct_version_string_cannot_bypass_executable_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\necho 'uv 0.10.8'\n", encoding="utf-8")
    fake_uv.chmod(0o755)
    monkeypatch.setattr(runtime_installer.sys, "frozen", True, raising=False)
    monkeypatch.setenv(runtime_installer.BUNDLED_UV_ENV, str(fake_uv))
    monkeypatch.setattr(
        runtime_installer, "runtime_target_id", lambda: "aarch64-apple-darwin"
    )
    monkeypatch.setattr(
        runtime_installer,
        "load_runtime_installer_contract",
        lambda: runtime_installer.RuntimeInstallerContract(
            version="0.10.8",
            python_minor="3.13",
            executable_sha256={"aarch64-apple-darwin": "0" * 64},
        ),
    )

    with pytest.raises(RuntimeError, match="integrity validation"):
        runtime_installer.bundled_uv_path()


def test_locked_target_environment_is_app_owned_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "image-gen-runtime" / "slots" / ".staging-test"
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/customer/uv/python")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("VIRTUAL_ENV", "/customer/project/.venv")
    monkeypatch.setenv("PYTHONPATH", "/customer/site-packages")

    environment = runtime_installer.locked_target_install_environment(target)

    control = tmp_path / "image-gen-runtime" / "installer-control"
    assert environment["UV_PYTHON_INSTALL_DIR"] == str(control / "python")
    assert environment["UV_CACHE_DIR"] == str(control / "cache")
    assert environment["UV_PYTHON_DOWNLOADS"] == "automatic"
    assert environment["UV_MANAGED_PYTHON"] == "1"
    assert environment["UV_LINK_MODE"] == "copy"
    assert environment["PATH"] == str(control / "empty-path")
    assert (control / "empty-path").is_dir()
    assert "UV_OFFLINE" not in environment
    assert "VIRTUAL_ENV" not in environment
    assert "PYTHONPATH" not in environment
