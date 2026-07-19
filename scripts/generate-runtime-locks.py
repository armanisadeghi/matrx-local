#!/usr/bin/env python3
"""Generate hash-locked, wheel-only image-runtime locks for every release target."""

from __future__ import annotations

import argparse
import html.parser
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from packaging.tags import compatible_tags, cpython_tags, mac_platforms
from packaging.utils import parse_wheel_filename


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "config" / "runtime-manifests"
INPUT = MANIFEST_DIR / "image-gen.in"
TARGETS = (
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "x86_64-unknown-linux-gnu",
)
REQ_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")
HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})")


class _LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def _target_tags(target: str):
    if target == "aarch64-apple-darwin":
        # Torchvision 0.25's CPython 3.13 wheel requires macOS 12. This minimum
        # is recorded by the target lock; release policy must not claim image
        # generation below the wheel set's actual baseline.
        platforms = list(mac_platforms((12, 0), "arm64"))
    elif target == "x86_64-apple-darwin":
        platforms = list(mac_platforms((10, 13), "x86_64"))
    elif target == "x86_64-pc-windows-msvc":
        platforms = ["win_amd64"]
    elif target == "x86_64-unknown-linux-gnu":
        platforms = [
            *(f"manylinux_2_{minor}_x86_64" for minor in range(35, 16, -1)),
            "manylinux2014_x86_64",
            "linux_x86_64",
        ]
    else:  # pragma: no cover - TARGETS is closed
        raise ValueError(target)
    return set(cpython_tags((3, 13), platforms=platforms)) | set(
        compatible_tags((3, 13), interpreter="cp313", platforms=platforms)
    )


def _pypi_wheels(name: str, version: str) -> list[dict[str, str]]:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    return [
        {
            "filename": item["filename"],
            "url": item["url"],
            "sha256": item["digests"]["sha256"],
        }
        for item in payload.get("urls", ())
        if item.get("packagetype") == "bdist_wheel"
    ]


def _pytorch_wheels(name: str) -> list[dict[str, str]]:
    base = f"https://download.pytorch.org/whl/cpu/{name.replace('-', '_')}/"
    with urllib.request.urlopen(base, timeout=30) as response:
        body = response.read().decode("utf-8")
    parser = _LinkParser()
    parser.feed(body)
    wheels: list[dict[str, str]] = []
    for link in parser.links:
        absolute = urljoin(base, link)
        parsed = urlparse(absolute)
        filename = unquote(Path(parsed.path).name)
        hash_match = re.search(r"(?:^|&)sha256=([0-9a-f]{64})(?:&|$)", parsed.fragment)
        if filename.endswith(".whl") and hash_match:
            wheels.append(
                {"filename": filename, "url": absolute, "sha256": hash_match.group(1)}
            )
    return wheels


def _compatible_wheels(package: dict[str, object], target: str) -> list[dict[str, str]]:
    name = str(package["name"])
    version = str(package["version"])
    hashes = set(package["sha256"])
    candidates = _pypi_wheels(name, version)
    if name in {"torch", "torchvision", "torchaudio"} and target in {
        "x86_64-pc-windows-msvc",
        "x86_64-unknown-linux-gnu",
    }:
        candidates += _pytorch_wheels(name)
    accepted_tags = _target_tags(target)
    compatible: list[dict[str, str]] = []
    for wheel in candidates:
        if wheel["sha256"] not in hashes:
            continue
        try:
            _distribution, wheel_version, _build, tags = parse_wheel_filename(
                wheel["filename"]
            )
        except Exception:
            continue
        normalized_version = version.split("+", 1)[0]
        if str(wheel_version).split("+", 1)[0] != normalized_version:
            continue
        if tags & accepted_tags:
            compatible.append(wheel)
    if not compatible:
        raise RuntimeError(
            f"{name}=={version} has no hash-locked CPython 3.13 wheel for {target}"
        )
    return sorted(compatible, key=lambda item: item["filename"])


def _compile(target: str, output: Path) -> None:
    command = [
        "uv",
        "pip",
        "compile",
        str(INPUT),
        "--python-version",
        "3.13",
        "--python-platform",
        target,
        "--only-binary",
        ":all:",
        "--generate-hashes",
        "--no-strip-markers",
        "--no-header",
        "--output-file",
        str(output),
    ]
    # Linux/Windows releases intentionally use CPU Torch. The dedicated index
    # participates only for packages it owns; PyPI remains the default index.
    if target in ("x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu"):
        command += [
            "--index",
            "https://download.pytorch.org/whl/cpu",
            "--index-strategy",
            "unsafe-best-match",
        ]
    subprocess.run(command, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def _parse_requirements(path: Path) -> list[dict[str, object]]:
    logical: list[str] = []
    current = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current += stripped[:-1].strip() + " " if stripped.endswith("\\") else stripped
        if not stripped.endswith("\\"):
            logical.append(current.strip())
            current = ""
    if current:
        raise RuntimeError(f"unterminated requirement continuation in {path}")

    packages: list[dict[str, object]] = []
    for requirement in logical:
        match = REQ_RE.match(requirement)
        if not match:
            raise RuntimeError(f"non-exact requirement in {path}: {requirement}")
        hashes = sorted(set(HASH_RE.findall(requirement)))
        if not hashes:
            raise RuntimeError(f"requirement has no hashes in {path}: {requirement}")
        packages.append(
            {"name": match.group(1).lower().replace("_", "-"), "version": match.group(2), "sha256": hashes}
        )
    return packages


def _write_target_manifest(target: str, lock_path: Path, packages: list[dict[str, object]]) -> None:
    contract = json.loads((MANIFEST_DIR / "image-gen-contract.json").read_text())
    manifest_path = MANIFEST_DIR / f"image-gen-{target}.json"
    manifest = {
        "schema_version": 1,
        "target": target,
        "python_minor": "3.13",
        "contract_sha256": contract["contract_sha256"],
        "lock_file": lock_path.name,
        "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "packages": [
            {
                "name": package["name"],
                "version": package["version"],
                "wheels": _compatible_wheels(package, target),
            }
            for package in packages
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _validate_target(target: str) -> list[str]:
    errors: list[str] = []
    lock_path = MANIFEST_DIR / f"image-gen-{target}.requirements.txt"
    manifest_path = MANIFEST_DIR / f"image-gen-{target}.json"
    if not lock_path.is_file() or not manifest_path.is_file():
        return [f"{target}: lock or manifest missing"]
    try:
        packages = _parse_requirements(lock_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        contract = json.loads((MANIFEST_DIR / "image-gen-contract.json").read_text())
        if manifest.get("target") != target:
            errors.append(f"{target}: target field mismatch")
        if manifest.get("python_minor") != "3.13":
            errors.append(f"{target}: Python ABI is not 3.13")
        if manifest.get("contract_sha256") != contract.get("contract_sha256"):
            errors.append(f"{target}: runtime contract is stale")
        if manifest.get("lock_file") != lock_path.name:
            errors.append(f"{target}: lock filename mismatch")
        if manifest.get("lock_sha256") != hashlib.sha256(lock_path.read_bytes()).hexdigest():
            errors.append(f"{target}: lock digest mismatch")
        inventory = manifest.get("packages")
        if not isinstance(inventory, list) or len(inventory) != len(packages):
            errors.append(f"{target}: package inventory mismatch")
        else:
            locked = {(item["name"], item["version"]): set(item["sha256"]) for item in packages}
            for package in inventory:
                key = (package.get("name"), package.get("version"))
                wheels = package.get("wheels")
                if key not in locked or not isinstance(wheels, list) or not wheels:
                    errors.append(f"{target}: invalid package entry {key}")
                    continue
                for wheel in wheels:
                    if wheel.get("sha256") not in locked[key] or not wheel.get("url"):
                        errors.append(f"{target}: wheel is not authorized by lock for {key}")
    except Exception as exc:
        errors.append(f"{target}: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--target", choices=TARGETS)
    args = parser.parse_args()
    selected_targets = (args.target,) if args.target else TARGETS

    if args.check:
        errors = [error for target in selected_targets for error in _validate_target(target)]
        if errors:
            print("runtime lock validation failed:\n- " + "\n- ".join(errors), file=sys.stderr)
            return 1
        print("validated four hash-locked CPython 3.13 image runtime targets")
        return 0

    failures: list[str] = []
    for target in selected_targets:
        try:
            lock_path = MANIFEST_DIR / f"image-gen-{target}.requirements.txt"
            with tempfile.TemporaryDirectory(prefix=f"matrx-runtime-{target}-") as temp:
                generated = Path(temp) / lock_path.name
                _compile(target, generated)
                content = generated.read_text(encoding="utf-8")
            lock_path.write_text(content, encoding="utf-8")
            packages = _parse_requirements(lock_path)
            _write_target_manifest(target, lock_path, packages)
            print(f"generated {target}: {len(packages)} packages")
        except Exception as exc:
            failures.append(f"{target}: {exc}")
    if failures:
        print("runtime lock generation failed:\n- " + "\n- ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
