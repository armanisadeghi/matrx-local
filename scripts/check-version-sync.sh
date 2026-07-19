#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

python3 - <<'PY'
import json
import re
from pathlib import Path
from typing import Optional


def match_version(path: str, pattern: str, *, package: Optional[str] = None) -> str:
    text = Path(path).read_text(encoding="utf-8")
    if package is not None:
        block = re.search(
            rf'\[\[package\]\]\s*\nname = "{re.escape(package)}"\s*\nversion = "([^"]+)"',
            text,
        )
        if not block:
            raise SystemExit(f"Could not find {package!r} version in {path}")
        return block.group(1)
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise SystemExit(f"Could not read version from {path}")
    return match.group(1)


canonical = match_version("pyproject.toml", r'^version\s*=\s*"([^"]+)"')
versions = {
    "pyproject.toml (canonical)": canonical,
    "desktop/package.json": json.loads(Path("desktop/package.json").read_text())["version"],
    "desktop/src-tauri/tauri.conf.json": json.loads(
        Path("desktop/src-tauri/tauri.conf.json").read_text()
    )["version"],
    "desktop/src-tauri/Cargo.toml": match_version(
        "desktop/src-tauri/Cargo.toml", r'^version\s*=\s*"([^"]+)"'
    ),
    "desktop/src-tauri/Cargo.lock": match_version(
        "desktop/src-tauri/Cargo.lock", "", package="aimatrx-desktop"
    ),
    "uv.lock": match_version("uv.lock", "", package="matrx-local"),
}

drift = {path: version for path, version in versions.items() if version != canonical}
if drift:
    print(f"Version drift detected; canonical pyproject.toml is {canonical}:")
    for path, version in drift.items():
        print(f"  {path}: {version}")
    raise SystemExit(1)

print(f"Version sync OK: {canonical}")
PY
