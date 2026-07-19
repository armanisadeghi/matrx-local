#!/usr/bin/env python3
"""Stage the release-owned uv sidecar that installs managed media wheels.

The customer machine must not supply Python, pip, or uv. Release builds fetch
the exact target-native uv artifact declared in ``runtime-installer.json``,
verify its pinned upstream SHA-256, and place it where Tauri's ``externalBin``
contract expects ``uv-<target>[.exe]``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT / "config" / "runtime-manifests" / "runtime-installer.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "desktop" / "src-tauri" / "binaries"


@dataclass(frozen=True, slots=True)
class InstallerArtifact:
    target: str
    version: str
    archive: str
    sha256: str
    url: str

    @property
    def executable_name(self) -> str:
        return "uv.exe" if "windows" in self.target else "uv"

    @property
    def staged_name(self) -> str:
        suffix = ".exe" if "windows" in self.target else ""
        return f"uv-{self.target}{suffix}"

    @property
    def archive_member(self) -> str:
        return f"uv-{self.target}/{self.executable_name}"


def load_artifact(manifest_path: Path, target: str) -> InstallerArtifact:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read runtime installer manifest: {exc}") from exc
    if manifest.get("schema_version") != 1 or manifest.get("tool") != "uv":
        raise RuntimeError("Runtime installer manifest has an unsupported schema")
    version = manifest.get("version")
    base_url = manifest.get("release_base_url")
    artifacts = manifest.get("artifacts")
    if not isinstance(version, str) or not isinstance(base_url, str):
        raise RuntimeError("Runtime installer manifest is missing release identity")
    if not isinstance(artifacts, dict) or target not in artifacts:
        raise RuntimeError(f"No bundled runtime installer is declared for {target}")
    entry = artifacts[target]
    if not isinstance(entry, dict):
        raise RuntimeError(f"Runtime installer artifact for {target} is malformed")
    archive = entry.get("archive")
    digest = entry.get("sha256")
    if not isinstance(archive, str) or PurePosixPath(archive).name != archive:
        raise RuntimeError(f"Runtime installer archive for {target} is invalid")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise RuntimeError(f"Runtime installer checksum for {target} is invalid")
    return InstallerArtifact(
        target=target,
        version=version,
        archive=archive,
        sha256=digest,
        url=f"{base_url.rstrip('/')}/{archive}",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_verified(artifact: InstallerArtifact, destination: Path) -> None:
    request = urllib.request.Request(
        artifact.url,
        headers={"User-Agent": "Matrx-Local-release-builder/1"},
    )
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open(
            "wb"
        ) as output:
            while block := response.read(1024 * 1024):
                output.write(block)
                digest.update(block)
    except OSError as exc:
        raise RuntimeError(f"Could not download {artifact.url}: {exc}") from exc
    actual = digest.hexdigest()
    if actual != artifact.sha256:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"uv artifact checksum mismatch for {artifact.target}: "
            f"expected {artifact.sha256}, received {actual}"
        )


def extract_executable(
    artifact: InstallerArtifact, archive_path: Path, destination: Path
) -> None:
    member_name = artifact.archive_member
    try:
        if artifact.archive.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                info = archive.getinfo(member_name)
                if info.is_dir():
                    raise RuntimeError(f"{member_name} is not a file")
                with archive.open(info) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        elif artifact.archive.endswith(".tar.gz"):
            with tarfile.open(archive_path, mode="r:gz") as archive:
                info = archive.getmember(member_name)
                if not info.isfile():
                    raise RuntimeError(f"{member_name} is not a regular file")
                source = archive.extractfile(info)
                if source is None:
                    raise RuntimeError(f"Cannot read {member_name}")
                with source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        else:
            raise RuntimeError(f"Unsupported uv archive type: {artifact.archive}")
    except (KeyError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise RuntimeError(
            f"Cannot extract {member_name} from authenticated uv artifact: {exc}"
        ) from exc
    destination.chmod(0o755)


def verify_executable(path: Path, artifact: InstallerArtifact) -> None:
    try:
        result = subprocess.run(
            [str(path), "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Staged uv cannot execute: {exc}") from exc
    expected = f"uv {artifact.version}"
    reported = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if result.returncode != 0 or not reported.startswith(expected):
        raise RuntimeError(
            f"Staged runtime installer is not {expected}: "
            f"exit={result.returncode}, output={result.stdout[-2000:]!r}"
        )


def stage(artifact: InstallerArtifact, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / artifact.staged_name
    with tempfile.TemporaryDirectory(prefix="matrx-uv-") as temp_dir:
        temp = Path(temp_dir)
        archive_path = temp / artifact.archive
        executable_path = temp / artifact.executable_name
        download_verified(artifact, archive_path)
        extract_executable(artifact, archive_path, executable_path)
        verify_executable(executable_path, artifact)
        staged_temp = destination.with_name(destination.name + f".tmp-{os.getpid()}")
        shutil.copy2(executable_path, staged_temp)
        staged_temp.chmod(0o755)
        os.replace(staged_temp, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    artifact = load_artifact(args.manifest.resolve(strict=True), args.target)
    destination = args.output_dir.resolve(strict=False) / artifact.staged_name
    if args.check:
        if not destination.is_file():
            raise RuntimeError(f"Bundled runtime installer is missing: {destination}")
        verify_executable(destination, artifact)
    else:
        destination = stage(artifact, args.output_dir.resolve(strict=False))
    print(
        f"bundled runtime installer ready: {destination} "
        f"({artifact.target}, uv {artifact.version})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
