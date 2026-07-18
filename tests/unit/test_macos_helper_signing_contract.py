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

    assert 'json.loads(pathlib.Path("desktop/src-tauri/tauri.conf.json").read_text())' in script
    assert '["identifier"]' in script
    assert '--identifier "$PARENT_SIGNING_IDENTIFIER"' in script
    assert 'codesign -dr - "$HELPER_APP_PATH"' in script
    assert 'identifier \\"$PARENT_SIGNING_IDENTIFIER\\"' in script


def test_ci_cannot_silently_build_a_separate_helper_tcc_identity() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert '"${GITHUB_ACTIONS:-}" == "true"' in script
    assert "APPLE_SIGNING_IDENTITY is required for macOS release builds" in script


def test_identity_alignment_happens_before_tauri_copy_input() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    signing_step = script.index("PARENT_SIGNING_IDENTIFIER=")
    copy_step = script.index('SRC_APP="dist/$HELPER_APP_NAME"')
    assert signing_step < copy_step


VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify-macos-artifact.sh"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def test_final_artifact_verification_exists_and_gates_the_release() -> None:
    """Source-text contracts are not enough: the May 2026 helper-identity
    regression shipped while every script-grep test was green. The release
    workflow must extract and verify the FINAL artifact."""
    assert VERIFY_SCRIPT.is_file()
    verify = VERIFY_SCRIPT.read_text(encoding="utf-8")
    assert "codesign --verify --deep --strict" in verify
    assert "spctl --assess" in verify
    assert "stapler validate" in verify
    assert "Contents/Frameworks/Matrx Engine.app" in verify

    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "verify-macos-artifact.sh" in workflow, (
        "release.yml must invoke the final-artifact verification"
    )
    # It must verify BOTH the bundle .app and the updater archive users
    # actually receive.
    assert workflow.count("./scripts/verify-macos-artifact.sh") >= 2


def test_release_gate_runs_unit_tests() -> None:
    """tests/unit historically never ran in CI — the signing-contract and
    access-state tests were green-by-assumption. Pin that both gates run them."""
    for wf in (RELEASE_WORKFLOW, REPO_ROOT / ".github" / "workflows" / "ci.yml"):
        assert "pytest tests/smoke tests/parity tests/unit" in wf.read_text(
            encoding="utf-8"
        ), f"{wf.name} must run tests/unit"


def test_dylib_signing_loops_have_per_file_timeouts() -> None:
    """MXL-D-054: codesign can hang forever on one dylib. Both re-sign loops
    must bound each invocation and fail loudly on timeout."""
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "perl -e 'alarm" in script
    assert "MXL-D-054" in script

    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "perl -e 'alarm 120; exec @ARGV' codesign" in workflow
