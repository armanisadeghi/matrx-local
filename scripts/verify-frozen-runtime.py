#!/usr/bin/env python3
"""Verify a PyInstaller sidecar archive and execute its managed-runtime probe."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config" / "runtime-manifests" / "image-gen-contract.json"
)
SENTINEL = "MATRX_FROZEN_RUNTIME_VERIFY="


def archive_modules(binary: Path) -> set[str]:
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise RuntimeError("PyInstaller is required to inspect the frozen archive") from exc
    archive = CArchiveReader(str(binary))
    pyz = archive.open_embedded_archive("PYZ.pyz")
    return set(pyz.toc)


def check_archive(binary: Path, contract: dict) -> None:
    modules = archive_modules(binary)
    missing_critical = sorted(set(contract["critical_frozen_modules"]) - modules)
    if missing_critical:
        raise RuntimeError(
            "frozen archive is missing critical modules: "
            + ", ".join(missing_critical)
        )

    # The build contract promises whole shared packages. Compare the executable,
    # not merely the spec inputs, against the exact build environment.
    from PyInstaller.utils.hooks import collect_submodules

    missing_shared: dict[str, list[str]] = {}
    for package in contract["shared_import_packages"]:
        expected = set(collect_submodules(package))
        if not expected:
            raise RuntimeError(f"required shared package absent on build host: {package}")
        missing = sorted(expected - modules)
        if missing:
            missing_shared[package] = missing[:20]
    if missing_shared:
        details = "; ".join(
            f"{package}: {items}" for package, items in missing_shared.items()
        )
        raise RuntimeError(f"frozen shared-package collection is incomplete: {details}")


def run_frozen_probe(binary: Path, runtime_path: Path, contract: dict) -> None:
    environment = os.environ.copy()
    environment["MATRX_FROZEN_RUNTIME_VERIFY"] = "1"
    environment["MATRX_FROZEN_RUNTIME_PATH"] = str(runtime_path)
    process = subprocess.run(
        [str(binary)],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    payload = None
    for line in process.stdout.splitlines():
        if line.startswith(SENTINEL):
            payload = json.loads(line[len(SENTINEL) :])
    if process.returncode != 0 or not payload or not payload.get("ok"):
        raise RuntimeError(
            f"frozen runtime probe failed (exit={process.returncode}):\n"
            + process.stdout[-12000:]
        )
    if payload.get("contract") != contract["contract_sha256"]:
        raise RuntimeError(
            "frozen artifact embeds a stale runtime contract: "
            f"{payload.get('contract')} != {contract['contract_sha256']}"
        )


def find_site_packages(python: Path) -> Path:
    result = subprocess.run(
        [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=30,
    )
    path = Path(result.stdout.strip()).resolve(strict=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--runtime-path", type=Path)
    parser.add_argument("--archive-only", action="store_true")
    args = parser.parse_args()

    binary = args.binary.resolve(strict=True)
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    check_archive(binary, contract)
    print(f"archive verified: {binary}")
    if not args.archive_only:
        runtime_path = (
            args.runtime_path.resolve(strict=True)
            if args.runtime_path
            else find_site_packages(args.python)
        )
        run_frozen_probe(binary, runtime_path, contract)
        print(
            "frozen managed-runtime probe verified: "
            f"{contract['contract_sha256'][:12]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
