"""Pin the macOS Helper app's Full Disk Access signing contract.

The visible parent receives the user's TCC grant, while the nested Python
engine performs the file I/O. The bundles keep distinct metadata identifiers,
but signed release code must share the parent's signing identifier so macOS
treats both processes as one responsible application.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-sidecar.sh"
MACOS_SPECS = (
    REPO_ROOT / "specs" / "matrx-engine-aarch64-apple-darwin.spec",
    REPO_ROOT / "specs" / "matrx-engine-x86_64-apple-darwin.spec",
)


def test_helper_keeps_unique_bundle_metadata_on_both_architectures() -> None:
    for spec_path in MACOS_SPECS:
        spec = spec_path.read_text(encoding="utf-8")
        assert "bundle_identifier='com.aimatrx.desktop.engine'" in spec
        assert "'CFBundleIdentifier': 'com.aimatrx.desktop.engine'" in spec


def test_release_signing_aligns_helper_with_visible_parent() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'PARENT_SIGNING_IDENTIFIER="com.aimatrx.desktop"' in script
    assert '--identifier "$PARENT_SIGNING_IDENTIFIER"' in script
    assert 'codesign -dr - "$HELPER_APP_PATH"' in script
    assert 'identifier \\"$PARENT_SIGNING_IDENTIFIER\\"' in script


def test_identity_alignment_happens_before_tauri_copy_input() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    signing_step = script.index("PARENT_SIGNING_IDENTIFIER=")
    copy_step = script.index('SRC_APP="dist/$HELPER_APP_NAME"')
    assert signing_step < copy_step
