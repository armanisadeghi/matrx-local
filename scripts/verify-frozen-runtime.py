#!/usr/bin/env python3
"""Verify a PyInstaller sidecar archive and execute its managed-runtime probe."""

from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config" / "runtime-manifests" / "image-gen-contract.json"
)
SENTINEL = "MATRX_FROZEN_RUNTIME_VERIFY="
OFFICE_SENTINEL = "MATRX_FROZEN_OFFICE_VERIFY="
TARGETS = (
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "x86_64-unknown-linux-gnu",
)


def _extension_module_name(archive_name: str) -> str | None:
    """Map a PyInstaller native-binary entry back to its import name."""
    normalized = archive_name.replace("\\", "/")
    for suffix in sorted(importlib.machinery.EXTENSION_SUFFIXES, key=len, reverse=True):
        if normalized.endswith(suffix):
            stem = normalized[: -len(suffix)]
            stem = re.sub(r"\.(?:cpython-\d+[^./]*|cp\d+[^./]*|abi3)$", "", stem)
            return stem.replace("/", ".")
    # Normally verification is target-native. These conservative fallbacks
    # also keep archive-inspection tooling useful across build hosts.
    for suffix in (".pyd", ".so"):
        if normalized.endswith(suffix):
            stem = normalized[: -len(suffix)]
            stem = re.sub(r"\.(?:cpython-\d+[^./]*|cp\d+[^./]*|abi3)$", "", stem)
            return stem.replace("/", ".")
    return None


def _open_archive(binary: Path):
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise RuntimeError("PyInstaller is required to inspect the frozen archive") from exc
    return CArchiveReader(str(binary))


def archive_modules(binary: Path) -> set[str]:
    archive = _open_archive(binary)
    pyz = archive.open_embedded_archive("PYZ.pyz")
    modules = set(pyz.toc)
    # Extension modules are binary entries in the outer CArchive, never in the
    # pure-Python PYZ table. Both tables are required to prove whole packages.
    for name, entry in archive.toc.items():
        if entry[-1] != "b":
            continue
        module = _extension_module_name(name)
        if module:
            modules.add(module)
    return modules


def archive_data_files(binary: Path) -> set[str]:
    """Return archive-relative paths of every extractable payload entry.

    Data files never appear in the PYZ module table, so a module-only check
    passes on an artifact whose python-docx/python-pptx default templates never
    shipped — the exact failure that only surfaces when a user creates a
    document. PyInstaller may classify ZIP-based Office templates as binary
    entries (``b``) on Windows and data entries (``x``) on Unix, even though
    both are extracted payload files. Exact path checks below distinguish the
    required templates from extension modules.
    """
    archive = _open_archive(binary)
    return {
        name.replace("\\", "/")
        for name, entry in archive.toc.items()
        if entry[-1] in {"b", "x"}
    }


def check_office_archive(binary: Path) -> None:
    """Prove the Office codec and its default templates reached the artifact.

    The codec and its renderers are lazily imported inside functions, so
    PyInstaller's static analysis reaches none of them and only an explicit
    check on the built archive can prove the specs' collection worked.
    """
    sys.path.insert(0, str(ROOT / "specs"))
    from _office_bundle import (
        OFFICE_PACKAGES,
        OFFICE_REQUIRED_DATA_FILES,
        office_parent_relative_dirs,
    )

    modules = archive_modules(binary)
    required_modules = {
        "matrx_files.specific_handlers.office",
        "matrx_files.specific_handlers.office.extract",
        "matrx_files.specific_handlers.office.generate",
        *OFFICE_PACKAGES,
    }
    missing_modules = sorted(required_modules - modules)
    if missing_modules:
        raise RuntimeError(
            "frozen archive is missing Office codec modules: "
            + ", ".join(missing_modules)
        )

    datas = archive_data_files(binary)
    missing_datas = sorted(
        f"{package}/{relative}"
        for package, relative in OFFICE_REQUIRED_DATA_FILES.items()
        if f"{package}/{relative}" not in datas
    )
    if missing_datas:
        raise RuntimeError(
            "frozen archive is missing Office default templates (document "
            "creation would fail at runtime): " + ", ".join(missing_datas)
        )

    # python-docx / python-pptx open templates through "<subpackage>/../
    # templates/…". The OS resolves that literal path only when <subpackage> is
    # a real directory under sys._MEIPASS — and frozen modules live in the PYZ,
    # not on disk. Every such directory therefore needs at least one extracted
    # file, or the template is present and still unreachable.
    missing_dirs = sorted(
        directory
        for directory in office_parent_relative_dirs()
        if not any(entry.startswith(directory + "/") for entry in datas)
    )
    if missing_dirs:
        raise RuntimeError(
            "frozen archive has no extracted file under these Office package "
            "directories, so their '../templates/…' lookups cannot resolve: "
            + ", ".join(missing_dirs)
        )


def run_office_probe(binary: Path) -> dict:
    """Execute the Office read/write probe inside the frozen process.

    Deliberately independent of the managed image runtime so every target is
    gated, including the ones whose managed runtime is archive-only.
    """
    environment = os.environ.copy()
    environment["MATRX_FROZEN_OFFICE_VERIFY"] = "1"
    process = subprocess.run(
        [str(binary)],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
    )
    payload = None
    for line in process.stdout.splitlines():
        if line.startswith(OFFICE_SENTINEL):
            payload = json.loads(line[len(OFFICE_SENTINEL) :])
    if process.returncode != 0 or not payload or not payload.get("ok"):
        raise RuntimeError(
            f"frozen Office codec probe failed (exit={process.returncode}):\n"
            + process.stdout[-12000:]
        )
    return payload


def check_archive(binary: Path, contract: dict, *, target: str) -> None:
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

    sys.path.insert(0, str(ROOT / "specs"))
    from _managed_runtime_bundle import (
        managed_runtime_excluded_packages,
        managed_runtime_shared_packages,
    )

    leaked_managed = {
        package: sorted(
            module
            for module in modules
            if module == package or module.startswith(package + ".")
        )[:20]
        for package in managed_runtime_excluded_packages(target)
    }
    leaked_managed = {
        package: found for package, found in leaked_managed.items() if found
    }
    if leaked_managed:
        raise RuntimeError(
            "managed-only packages leaked into frozen archive: "
            + "; ".join(
                f"{package}: {found}" for package, found in leaked_managed.items()
            )
        )

    missing_shared: dict[str, list[str]] = {}
    for package in managed_runtime_shared_packages(target):
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


def run_frozen_probe(
    binary: Path, runtime_path: Path, contract: dict, *, target: str
) -> None:
    environment = os.environ.copy()
    environment["MATRX_FROZEN_RUNTIME_VERIFY"] = "1"
    environment["MATRX_FROZEN_RUNTIME_PATH"] = str(runtime_path)
    environment["MATRX_FROZEN_RUNTIME_TARGET"] = target
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


def load_target_manifest(target: str, contract: dict) -> dict:
    path = ROOT / "config" / "runtime-manifests" / f"image-gen-{target}.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("target") != target:
        raise RuntimeError(f"target manifest mismatch: {manifest.get('target')!r} != {target!r}")
    if manifest.get("contract_sha256") != contract.get("contract_sha256"):
        raise RuntimeError(f"target manifest is stale for canonical contract: {target}")
    return manifest


def install_exact_target_runtime(
    uv: Path, target: str, target_manifest: dict, destination: Path
) -> None:
    lock_path = (
        ROOT / "config" / "runtime-manifests" / str(target_manifest["lock_file"])
    )
    actual_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    if actual_digest != target_manifest.get("lock_sha256"):
        raise RuntimeError("target requirements digest does not match its manifest")
    installer_manifest = json.loads(
        (ROOT / "config" / "runtime-manifests" / "runtime-installer.json").read_text()
    )
    sys.path.insert(0, str(ROOT))
    from app.services.optional_packages.runtime_installer import (
        executable_sha256,
        locked_target_install_environment,
    )

    uv = uv.resolve(strict=True)
    expected_uv_hash = installer_manifest["artifacts"][target]["executable_sha256"]
    if executable_sha256(uv) != expected_uv_hash:
        raise RuntimeError("staged bundled uv failed executable identity validation")
    subprocess.run(
        [
            str(uv),
            "pip",
            "install",
            "--python",
            "3.13",
            "--managed-python",
            "--python-version",
            "3.13",
            "--python-platform",
            target,
            "--target",
            str(destination),
            "--only-binary",
            ":all:",
            "--require-hashes",
            "--requirement",
            str(lock_path),
            "--no-cache",
            "--no-progress",
            "--no-config",
            "--native-tls",
            "--index-strategy",
            "unsafe-best-match",
        ],
        cwd=ROOT,
        env=locked_target_install_environment(destination),
        check=True,
        timeout=1800,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--runtime-path", type=Path)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--archive-only", action="store_true")
    args = parser.parse_args()

    binary = args.binary.resolve(strict=True)
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    check_archive(binary, contract, target=args.target)
    check_office_archive(binary)
    print(f"archive verified: {binary}")

    # The Office probe needs no managed runtime and no network, so it gates
    # every target — including the archive-only ones — and runs before the
    # expensive locked-runtime install. Every caller builds and verifies on the
    # target's own platform, so the artifact is always executable here.
    office = run_office_probe(binary)
    print(
        "frozen Office codec verified: generated "
        + ", ".join(
            f"{kind} {size}B"
            for kind, size in sorted(office["generated_bytes"].items())
        )
    )

    if not args.archive_only:
        target_manifest = load_target_manifest(args.target, contract)
        if target_manifest is not None and target_manifest.get("supported") is False:
            print(
                f"managed runtime explicitly unsupported for {args.target}; "
                "archive verification is the complete release gate for this target"
            )
            return 0
        if args.runtime_path:
            run_frozen_probe(
                binary,
                args.runtime_path.resolve(strict=True),
                contract,
                target=args.target,
            )
        else:
            if args.uv is None:
                parser.error("--uv is required for a full locked runtime probe")
            with tempfile.TemporaryDirectory(prefix=f"matrx-locked-{args.target}-") as temp:
                runtime_path = (
                    Path(temp) / "image-gen-runtime" / "slots" / ".verification"
                )
                install_exact_target_runtime(
                    args.uv, args.target, target_manifest, runtime_path
                )
                run_frozen_probe(binary, runtime_path, contract, target=args.target)
        print(
            "frozen managed-runtime probe verified: "
            f"{contract['contract_sha256'][:12]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
