from __future__ import annotations

import json
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

    environment = runtime_installer.locked_target_install_environment(target)

    control = tmp_path / "image-gen-runtime" / "installer-control"
    assert environment["UV_PYTHON_INSTALL_DIR"] == str(control / "python")
    assert environment["UV_CACHE_DIR"] == str(control / "cache")
    assert environment["UV_PYTHON_DOWNLOADS"] == "automatic"
    assert environment["UV_MANAGED_PYTHON"] == "1"
    assert environment["UV_LINK_MODE"] == "copy"
    assert "UV_OFFLINE" not in environment
