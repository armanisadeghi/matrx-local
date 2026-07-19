#!/usr/bin/env python3
"""Prove the bundled uv can bootstrap a managed installer Python from zero."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path


def _record_hash(content: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
    return f"sha256={digest.decode('ascii')}"


def build_probe_wheel(destination: Path) -> str:
    files = {
        "matrx_installer_probe/__init__.py": b"VERIFIED = True\n",
        "matrx_installer_probe-1.0.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: matrx-installer-probe\n"
            b"Version: 1.0.0\nRequires-Python: >=3.13,<3.14\n"
        ),
        "matrx_installer_probe-1.0.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: Matrx release verifier\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record_buffer = io.StringIO()
    writer = csv.writer(record_buffer, lineterminator="\n")
    for name, content in files.items():
        writer.writerow((name, _record_hash(content), len(content)))
    record_name = "matrx_installer_probe-1.0.0.dist-info/RECORD"
    writer.writerow((record_name, "", ""))
    files[record_name] = record_buffer.getvalue().encode("utf-8")
    with zipfile.ZipFile(destination, mode="w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for name, content in files.items():
            wheel.writestr(name, content)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def verify(uv: Path, target: str, python_minor: str) -> None:
    uv = uv.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="matrx-uv-bootstrap-") as temp_value:
        temp = Path(temp_value)
        empty_path = temp / "empty-path"
        empty_path.mkdir()
        wheel = temp / "matrx_installer_probe-1.0.0-py3-none-any.whl"
        digest = build_probe_wheel(wheel)
        requirements = temp / "probe.requirements.txt"
        requirements.write_text(
            f"{wheel.as_uri()} --hash=sha256:{digest}\n", encoding="utf-8"
        )
        target_dir = temp / "installed"
        python_dir = temp / "installer-control" / "python"
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("UV_", "PIP_"))
            and key
            not in {
                "VIRTUAL_ENV",
                "CONDA_PREFIX",
                "CONDA_DEFAULT_ENV",
                "PYTHONHOME",
                "PYTHONPATH",
            }
        }
        environment.update(
            {
                "PATH": str(empty_path),
                "UV_PYTHON_INSTALL_DIR": str(python_dir),
                "UV_CACHE_DIR": str(temp / "installer-control" / "cache"),
                "UV_PYTHON_DOWNLOADS": "automatic",
                "UV_MANAGED_PYTHON": "1",
                "UV_LINK_MODE": "copy",
                "UV_NATIVE_TLS": "1",
                "UV_NO_CONFIG": "1",
            }
        )
        command = [
            str(uv),
            "pip",
            "install",
            "--target",
            str(target_dir),
            "--python",
            python_minor,
            "--managed-python",
            "--python-version",
            python_minor,
            "--python-platform",
            target,
            "--only-binary",
            ":all:",
            "--no-progress",
            "--no-config",
            "--native-tls",
            "--require-hashes",
            "--requirement",
            str(requirements),
        ]
        result = subprocess.run(
            command,
            cwd=temp,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Bundled uv could not bootstrap without host Python/PATH:\n"
                + result.stdout[-12000:]
            )
        if not (target_dir / "matrx_installer_probe" / "__init__.py").is_file():
            raise RuntimeError("Bundled uv reported success without installing probe wheel")
        if not python_dir.is_dir() or not any(python_dir.iterdir()):
            raise RuntimeError(
                "Bundled uv borrowed a host Python instead of its app-owned managed "
                "runtime:\n" + result.stdout[-12000:]
            )
        print(
            f"bundled uv bootstrap verified: target={target} python={python_minor} "
            f"installer_root={python_dir}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--python-minor", default="3.13")
    args = parser.parse_args()
    verify(args.uv, args.target, args.python_minor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
