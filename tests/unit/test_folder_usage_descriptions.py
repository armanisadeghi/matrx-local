"""Tripwire: macOS Files & Folders usage strings must exist on BOTH bundles.

macOS gates Documents/Desktop/Downloads (and network/removable volumes)
per-app via TCC, SEPARATELY from Full Disk Access. A bundle that triggers
access without the matching NS*UsageDescription key is denied SILENTLY with
EPERM — no prompt, nothing in Console.

Shipped bug (2026-08): the engine helper had none of these keys, so
enumerating ~/Documents/Matrx/Notes failed with errno 1 on every launch —
notes and file sync were fully degraded — while Full Disk Access was granted
and every other capability (create/read/write/replace/delete) worked.

The keys must live on:
  - the engine helper (specs/matrx-engine-*-apple-darwin.spec) — it performs
    the actual notes/files I/O, and macOS resolves the prompt/verdict against
    the bundle that triggered the call;
  - the parent app (desktop/src-tauri/Info.plist) — Rust-side file tools and
    prompt attribution.

If this test fails you removed or renamed a key. Do NOT delete assertions to
make it pass — the silent-EPERM bug ships back with it.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FOLDER_KEYS = (
    "NSDocumentsFolderUsageDescription",
    "NSDesktopFolderUsageDescription",
    "NSDownloadsFolderUsageDescription",
    "NSNetworkVolumesUsageDescription",
    "NSRemovableVolumesUsageDescription",
)

MAC_SPECS = (
    "specs/matrx-engine-aarch64-apple-darwin.spec",
    "specs/matrx-engine-x86_64-apple-darwin.spec",
)


def test_engine_helper_specs_declare_folder_usage_keys() -> None:
    for rel in MAC_SPECS:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        missing = [k for k in FOLDER_KEYS if k not in text]
        assert not missing, (
            f"{rel} is missing folder usage keys {missing} — macOS will "
            "silently EPERM the engine on those folders (no prompt)."
        )


def test_parent_app_infoplist_declares_folder_usage_keys() -> None:
    text = (REPO_ROOT / "desktop/src-tauri/Info.plist").read_text(encoding="utf-8")
    missing = [k for k in FOLDER_KEYS if k not in text]
    assert not missing, (
        f"desktop/src-tauri/Info.plist is missing folder usage keys "
        f"{missing} — macOS will silently EPERM those folders (no prompt)."
    )
