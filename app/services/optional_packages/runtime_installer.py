"""Release-owned installer executable for frozen managed Python runtimes."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BUNDLED_UV_ENV = "MATRX_BUNDLED_UV_PATH"


@dataclass(frozen=True, slots=True)
class RuntimeInstallerContract:
    version: str
    python_minor: str
    targets: frozenset[str]


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
    return RuntimeInstallerContract(version, python_minor, frozenset(artifacts))


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
        "--python-platform",
        target,
        "--only-binary",
        ":all:",
        "--no-cache",
        "--no-progress",
        "--no-config",
        "--native-tls",
    ]
