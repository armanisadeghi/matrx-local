"""Release-owned installer executable for frozen managed Python runtimes."""

from __future__ import annotations

import json
import hashlib
import os
import platform
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BUNDLED_UV_ENV = "MATRX_BUNDLED_UV_PATH"


@dataclass(frozen=True, slots=True)
class RuntimeInstallerContract:
    version: str
    python_minor: str
    executable_sha256: dict[str, str]

    @property
    def targets(self) -> frozenset[str]:
        return frozenset(self.executable_sha256)


def executable_sha256(path: Path) -> str:
    """Hash executable code while ignoring a replaceable Mach-O signature.

    Apple notarization requires Tauri to replace uv's upstream ad-hoc signature
    with the AI Matrx Developer ID signature. That changes only the
    ``LC_CODE_SIGNATURE`` payload and its size fields. Normalizing precisely
    those bytes keeps the release-pinned executable identity stable across
    signing; Windows/Linux use ordinary whole-file SHA-256.
    """
    data = bytearray(path.read_bytes())
    # 64-bit little-endian Mach-O (the only two supported macOS targets).
    if data[:4] == b"\xcf\xfa\xed\xfe" and len(data) >= 32:
        ncmds = struct.unpack_from("<I", data, 16)[0]
        offset = 32
        signature: tuple[int, int] | None = None
        for _ in range(ncmds):
            if offset + 8 > len(data):
                raise RuntimeError("Bundled uv has a truncated Mach-O load-command table")
            command, command_size = struct.unpack_from("<II", data, offset)
            if command_size < 8 or offset + command_size > len(data):
                raise RuntimeError("Bundled uv has an invalid Mach-O load command")
            if command == 0x1D:  # LC_CODE_SIGNATURE
                if command_size < 16:
                    raise RuntimeError("Bundled uv has an invalid code-signature command")
                data_offset, data_size = struct.unpack_from("<II", data, offset + 8)
                if data_offset + data_size > len(data):
                    raise RuntimeError("Bundled uv has an invalid code-signature range")
                # Preserve the LC_CODE_SIGNATURE command identity but normalize
                # the signer-owned location/size fields before hashing.
                data[offset + 8 : offset + 16] = b"\0" * 8
                signature = (data_offset, data_size)
            elif command == 0x19 and command_size >= 72:  # LC_SEGMENT_64
                segment_name = bytes(data[offset + 8 : offset + 24]).rstrip(b"\0")
                if segment_name == b"__LINKEDIT":
                    # Re-signing resizes the signature at the end of __LINKEDIT,
                    # which updates this segment's vmsize/filesize even though
                    # executable code and all other link-edit data are unchanged.
                    data[offset + 32 : offset + 40] = b"\0" * 8  # vmsize
                    data[offset + 48 : offset + 56] = b"\0" * 8  # filesize
            offset += command_size
        if signature is not None:
            start, size = signature
            digest = hashlib.sha256()
            digest.update(data[:start])
            digest.update(data[start + size :])
            return digest.hexdigest()
    return hashlib.sha256(data).hexdigest()


def runtime_target_id() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    if sys.platform == "darwin" and machine in {"x86_64", "amd64"}:
        return "x86_64-apple-darwin"
    if sys.platform == "win32" and machine in {"x86_64", "amd64"}:
        return "x86_64-pc-windows-msvc"
    if sys.platform.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "x86_64-unknown-linux-gnu"
    raise RuntimeError(
        f"No bundled runtime installer target for platform={sys.platform!r} "
        f"machine={machine!r}"
    )


def _contract_candidates() -> tuple[Path, ...]:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    filename = "runtime-installer.json"
    return (
        bundle_root / "runtime-manifests" / filename,
        bundle_root / "config" / "runtime-manifests" / filename,
        Path(__file__).resolve().parents[3]
        / "config"
        / "runtime-manifests"
        / filename,
    )


def load_runtime_installer_contract() -> RuntimeInstallerContract:
    path = next((candidate for candidate in _contract_candidates() if candidate.is_file()), None)
    if path is None:
        raise RuntimeError("The frozen app is missing its runtime installer contract")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"The runtime installer contract cannot be read: {exc}") from exc
    if raw.get("schema_version") != 1 or raw.get("tool") != "uv":
        raise RuntimeError("The runtime installer contract has an unsupported schema")
    version = raw.get("version")
    python_minor = raw.get("python_minor")
    artifacts = raw.get("artifacts")
    if (
        not isinstance(version, str)
        or not isinstance(python_minor, str)
        or not isinstance(artifacts, dict)
    ):
        raise RuntimeError("The runtime installer contract is incomplete")
    executable_hashes: dict[str, str] = {}
    for target, artifact in artifacts.items():
        digest = artifact.get("executable_sha256") if isinstance(artifact, dict) else None
        if (
            not isinstance(target, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise RuntimeError(f"The runtime installer identity for {target!r} is invalid")
        executable_hashes[target] = digest
    return RuntimeInstallerContract(version, python_minor, executable_hashes)


def bundled_uv_path() -> Path:
    """Return the Tauri-bundled uv executable after identity validation.

    Frozen builds deliberately never search PATH. The Tauri parent resolves
    its authenticated external binary and passes the absolute path to the
    engine. This prevents a customer-installed or PATH-shadowing uv from
    becoming part of the managed-runtime trust chain.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Bundled uv is only used by the frozen application")
    value = os.getenv(BUNDLED_UV_ENV)
    if not value:
        raise RuntimeError(
            "This application package is missing its bundled runtime installer. "
            "Reinstall or update AI Matrx; installing Python or uv cannot repair it."
        )
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise RuntimeError("The bundled runtime installer path is invalid")
    contract = load_runtime_installer_contract()
    target = runtime_target_id()
    if target not in contract.targets:
        raise RuntimeError(f"The bundled runtime installer does not support {target}")
    actual_hash = executable_sha256(path)
    expected_hash = contract.executable_sha256[target]
    if actual_hash != expected_hash:
        raise RuntimeError(
            "The bundled runtime installer failed executable integrity validation: "
            f"expected {expected_hash}, received {actual_hash}. "
            "Reinstall or update AI Matrx."
        )
    try:
        result = subprocess.run(
            [str(path), "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"The bundled runtime installer cannot execute: {exc}") from exc
    expected = f"uv {contract.version}"
    reported = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if result.returncode != 0 or not reported.startswith(expected):
        raise RuntimeError(
            f"The application package contains the wrong runtime installer: "
            f"expected {expected}, received {reported!r}"
        )
    return path


def locked_target_install_command(target_dir: Path) -> list[str]:
    """Build a wheel-only install command without discovering host Python."""
    contract = load_runtime_installer_contract()
    target = runtime_target_id()
    if target not in contract.targets:
        raise RuntimeError(f"The managed runtime does not support {target}")
    return [
        str(bundled_uv_path()),
        "pip",
        "install",
        "--target",
        str(target_dir),
        "--python-version",
        contract.python_minor,
        "--python",
        contract.python_minor,
        "--managed-python",
        "--python-platform",
        target,
        "--only-binary",
        ":all:",
        "--no-cache",
        "--no-progress",
        "--no-config",
        "--native-tls",
    ]


def locked_target_install_environment(target_dir: Path) -> dict[str, str]:
    """Isolate uv's interpreter and cache inside the app-owned runtime root."""
    # The target is always <runtime-root>/slots/<staging-slot>. Keep the
    # installer-only interpreter beside slots so cleanup/repair is deterministic
    # and never borrows ~/.local/share/uv or another customer environment.
    slots_dir = target_dir.resolve(strict=False).parent
    runtime_root = slots_dir.parent
    control_root = runtime_root / "installer-control"
    empty_path = control_root / "empty-path"
    empty_path.mkdir(parents=True, exist_ok=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_"))
        and key
        not in {"VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "PYTHONHOME", "PYTHONPATH"}
    }
    environment.update(
        {
            "UV_PYTHON_INSTALL_DIR": str(control_root / "python"),
            "UV_CACHE_DIR": str(control_root / "cache"),
            "UV_PYTHON_DOWNLOADS": "automatic",
            "UV_MANAGED_PYTHON": "1",
            "UV_LINK_MODE": "copy",
            "UV_NATIVE_TLS": "1",
            "UV_NO_CONFIG": "1",
            # The executable path is absolute and wheel-only installation does
            # not need shell tools. Hiding customer PATH prevents uv from
            # borrowing a uv-managed Python installed for an unrelated project.
            "PATH": str(empty_path),
        }
    )
    return environment
