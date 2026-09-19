"""Pin the macOS Helper app's Full Disk Access signing contract.

The visible parent receives the user's TCC grant, while the nested Python
engine performs the file I/O. The bundles keep distinct metadata identifiers,
but signed release code must share the parent's signing identifier so macOS
treats both processes as one responsible application.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


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
TAURI_CONFIG = REPO_ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
NATIVE_VAULT_HOOK = REPO_ROOT / "desktop" / "scripts" / "build-native-vault-provider-hook.cjs"
NATIVE_VAULT_HOOK_CHECK = (
    REPO_ROOT / "desktop" / "scripts" / "check-native-vault-provider-hook.cjs"
)


def test_native_vault_provider_hook_is_cross_platform() -> None:
    """Windows invokes bundle hooks through cmd.exe, not Bash.

    The provider itself is macOS-only, but its gate must be expressed in a
    runtime available on every release runner so Windows builds do not parse a
    POSIX conditional as a cmd command.
    """
    command = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))["build"][
        "beforeBundleCommand"
    ]

    assert command == "node scripts/build-native-vault-provider-hook.cjs"
    hook = NATIVE_VAULT_HOOK.read_text(encoding="utf-8")
    assert 'targetPlatform !== "macos" && targetPlatform !== "darwin"' in hook
    assert '"scripts/build-native-vault-provider.sh"' in hook
    assert "node -e" not in command

    # Exercise the command's Windows path without requiring a Windows runner.
    subprocess.run(
        ["node", str(NATIVE_VAULT_HOOK)],
        env={**os.environ, "TAURI_ENV_PLATFORM": "windows"},
        check=True,
    )
    subprocess.run(["node", str(NATIVE_VAULT_HOOK_CHECK)], check=True)


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


def test_direct_app_verification_cleanup_cannot_flip_success_to_failure() -> None:
    """An EXIT trap inherits its final command's status. The first macOS
    release gate verifies a direct .app, so cleanup must also return success
    when no updater-archive work directory was created."""
    verify = VERIFY_SCRIPT.read_text(encoding="utf-8")

    assert 'if [[ -n "$WORKDIR" ]]; then' in verify
    assert 'cleanup() { [[ -n "$WORKDIR" ]] &&' not in verify


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


def test_host_entitlements_without_profile_carry_no_restricted_keys() -> None:
    """A Developer ID host with NO embedded provisioning profile is killed at
    launch by macOS if it claims profile-backed entitlements (v1.4.92:
    SIGKILL, "Launchd job spawn failed", while Gatekeeper said accepted).
    The default Entitlements.plist is the no-profile host; the App-Group /
    identifier claims live only in Entitlements.vault.plist, which only the
    sealed release config (with the host profile) selects."""
    import plistlib

    src_tauri = TAURI_CONFIG.parent
    restricted = {
        "com.apple.application-identifier",
        "com.apple.developer.team-identifier",
        "com.apple.security.application-groups",
    }
    base = plistlib.loads((src_tauri / "Entitlements.plist").read_bytes())
    assert not (restricted & set(base)), restricted & set(base)
    vault = plistlib.loads((src_tauri / "Entitlements.vault.plist").read_bytes())
    assert restricted <= set(vault)
    sealed = json.loads((src_tauri / "tauri.release.macos.conf.json").read_text(encoding="utf-8"))
    assert sealed["bundle"]["macOS"]["entitlements"] == "Entitlements.vault.plist"
    assert "embedded.provisionprofile" in sealed["bundle"]["macOS"]["files"]
    host_only = json.loads((src_tauri / "tauri.release.macos.host-only.conf.json").read_text(encoding="utf-8"))
    assert "entitlements" not in host_only["bundle"]["macOS"]


# ---------------------------------------------------------------------------
# macOS helper executables are never `externalBin` (feedback 696c1213)
#
# tauri-bundler signs EVERY externalBin entry with the ONE
# bundle.macOS.entitlements file chosen for the HOST app
# (tauri-bundler/src/bundle/macos/sign.rs::sign passes the same
# settings.macos().entitlements for every SignTarget). Once the release started
# signing the host with Entitlements.vault.plist, every helper inherited four
# profile-backed keys a bare Mach-O cannot carry, and AMFI SIGKILLed each one
# at exec — exit 137 for matrx-syncd, matrx-egress, cloudflared, llama-server
# and uv, in every release from v1.4.137 (2026-09-16) to v1.4.170, while
# Gatekeeper and the stapled notarization ticket both said "accepted".
# ---------------------------------------------------------------------------

MACOS_OVERLAY = TAURI_CONFIG.parent / "tauri.macos.conf.json"
STAGE_SCRIPT = REPO_ROOT / "scripts" / "stage-macos-helpers.sh"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke.sh"

MACOS_HELPERS = ("matrx-syncd", "matrx-egress", "cloudflared", "llama-server", "uv")
PROFILE_BACKED_KEYS = (
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
    "com.apple.security.application-groups",
    "com.apple.developer.authentication-services.autofill-credential-provider",
)


def _macos_overlay() -> dict:
    return json.loads(MACOS_OVERLAY.read_text(encoding="utf-8"))


def test_macos_ships_no_helper_as_an_external_bin() -> None:
    """The class fix. An externalBin entry on macOS gets the HOST's
    entitlements, whatever those happen to be that week."""
    external = _macos_overlay()["bundle"]["externalBin"]
    assert external == [], (
        "macOS must declare NO externalBin: tauri-bundler signs each one with "
        f"the host entitlements file. Found: {external}"
    )


def test_every_macos_helper_ships_as_a_prestaged_bundle_file() -> None:
    files = _macos_overlay()["bundle"]["macOS"]["files"]
    for helper in MACOS_HELPERS:
        key = f"MacOS/{helper}"
        assert key in files, f"{helper} must ship as a bundle.macOS.files entry at {key}"
        assert files[key] == f"macos-helpers/{helper}", (
            f"{key} must come from the signed staging directory produced by "
            f"scripts/stage-macos-helpers.sh, not {files[key]!r}"
        )
    # The model this fix copies: the nested engine app was the ONE macOS helper
    # that kept working, precisely because it was a files entry, not a sidecar.
    assert files["Frameworks/Matrx Engine.app"] == "sidecar/Matrx Engine.app"


def test_staging_script_signs_helpers_with_the_sidecar_entitlements() -> None:
    script = STAGE_SCRIPT.read_text(encoding="utf-8")
    assert "sidecar/sidecar.entitlements.plist" in script
    assert '--entitlements "$ENTITLEMENTS"' in script
    assert "--options runtime" in script
    assert "--timestamp" in script
    # MXL-D-054: every codesign invocation is bounded.
    assert "perl -e 'alarm" in script
    # It must refuse to emit a helper carrying a profile-backed key.
    for key in PROFILE_BACKED_KEYS:
        assert key in script, f"staging script must reject {key}"
    for helper in MACOS_HELPERS:
        assert f'"{helper}:' in script, f"staging script must stage {helper}"


def test_release_stages_and_signs_helpers_before_tauri_builds() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "./scripts/stage-macos-helpers.sh" in workflow
    assert "--require-all" in workflow, (
        "release CI must fail on a missing helper input rather than ship an "
        "app with a helper silently absent"
    )
    stage = workflow.index("Stage and sign macOS helper executables")
    build = workflow.index("uses: tauri-apps/tauri-action@action-v1.0.0")
    assert stage < build, "helpers must be staged and signed BEFORE tauri builds"
    # tauri.conf.json's beforeBuildCommand runs INSIDE `tauri build`, after CI
    # has already staged SIGNED helpers. Wiring staging there would overwrite
    # them with unsigned copies.
    before_build = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))["build"][
        "beforeBuildCommand"
    ]
    assert "ensure:macos-helpers" not in before_build


def test_artifact_gate_execs_every_bundled_helper() -> None:
    """Entitlement text is not proof. v1.4.137-v1.4.170 passed every existing
    check because nothing ever ran a helper."""
    verify = VERIFY_SCRIPT.read_text(encoding="utf-8")
    assert 'find "$APP_PATH/Contents/MacOS"' in verify
    assert "CFBundleExecutable" in verify, "the host executable must be excluded"
    assert '"$helper" --version' in verify
    assert '"$HELPER_STATUS" == "137"' in verify, "exit 137 must be named and failed"
    assert 'HELPER_COUNT" -gt 0' in verify, (
        "finding zero helpers must fail, or the gate can silently stop checking"
    )
    for key in PROFILE_BACKED_KEYS:
        assert key in verify


def test_packaged_smoke_execs_every_bundled_helper() -> None:
    smoke = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "./scripts/stage-macos-helpers.sh" in smoke
    assert "bundled helper(s) could not execute" in smoke
    assert "bundled helpers execute" in smoke


def test_host_bundle_helper_lookup_finds_the_host_macos_directory() -> None:
    """The engine runs from the NESTED Matrx Engine.app, so helpers beside the
    HOST executable are three directories up. Without this the shipped
    cloudflared and matrx-egress are never found at all."""
    import sys
    import tempfile
    from unittest import mock

    from app.common import platform_ctx

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        host_macos = root / "AI Matrx.app" / "Contents" / "MacOS"
        engine_macos = (
            root
            / "AI Matrx.app"
            / "Contents"
            / "Frameworks"
            / "Matrx Engine.app"
            / "Contents"
            / "MacOS"
        )
        host_macos.mkdir(parents=True)
        engine_macos.mkdir(parents=True)
        with mock.patch.object(platform_ctx, "_sys_platform", "darwin"), mock.patch.object(
            sys, "executable", str(engine_macos / "Matrx Engine")
        ):
            assert platform_ctx.host_bundle_macos_dir() == host_macos
        # A source run (no nested bundle) must resolve to None, never a guess.
        with mock.patch.object(platform_ctx, "_sys_platform", "darwin"), mock.patch.object(
            sys, "executable", str(root / "python")
        ):
            assert platform_ctx.host_bundle_macos_dir() is None
