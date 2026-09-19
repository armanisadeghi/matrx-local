#!/usr/bin/env bash
# stage-macos-helpers.sh — stage (and optionally sign) the macOS helper
# executables that ship inside Contents/MacOS.
#
# WHY THIS EXISTS (the v1.4.137–v1.4.170 outage, feedback 696c1213)
# -----------------------------------------------------------------
# tauri-bundler signs EVERY `bundle.externalBin` entry with the ONE
# `bundle.macOS.entitlements` file chosen for the HOST app
# (tauri-bundler/src/bundle/macos/sign.rs::sign — the same `settings.macos()
# .entitlements` is passed for every SignTarget). Once the release picked
# `Entitlements.vault.plist`, every helper inherited the host's
# profile-backed keys (com.apple.application-identifier,
# com.apple.developer.team-identifier, com.apple.security.application-groups,
# com.apple.developer.authentication-services.autofill-credential-provider).
# A bare Mach-O cannot carry a provisioning profile, so AMFI SIGKILLed each
# one at exec: exit 137 for matrx-syncd, matrx-egress, cloudflared,
# llama-server and uv in every sealed build.
#
# The fix is the model the nested engine already proved: helpers are NOT
# `externalBin` on macOS. They are pre-signed here with
# `sidecar/sidecar.entitlements.plist` and handed to the bundler as
# `bundle.macOS.files` ("MacOS/<name>"), which tauri-bundler copies verbatim
# (copy_custom_files_to_bundle is never added to sign_paths) and never
# re-signs (its codesign invocation carries no --deep). Windows and Linux
# keep `externalBin`, where no such entitlements problem exists.
#
# Usage:
#   scripts/stage-macos-helpers.sh [--target <triple>] [--sign <identity>] [--require-all]
#
#   --target        target triple; defaults to TAURI_ENV_TARGET_TRIPLE, then
#                   the host architecture.
#   --sign          Developer ID identity. When given, every staged helper is
#                   re-signed hardened + timestamped with the sidecar
#                   entitlements. Without it (local dev / unsigned smoke) the
#                   source binary's existing signature is preserved.
#   --require-all   fail when any helper input is missing (release builds).

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "stage-macos-helpers.sh: not macOS — nothing to stage"
    exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_TAURI="$REPO_ROOT/desktop/src-tauri"
STAGE_DIR="$SRC_TAURI/macos-helpers"
ENTITLEMENTS="$SRC_TAURI/sidecar/sidecar.entitlements.plist"

TARGET="${TAURI_ENV_TARGET_TRIPLE:-}"
IDENTITY=""
REQUIRE_ALL=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target) TARGET="${2:?--target needs a triple}"; shift 2 ;;
        --sign) IDENTITY="${2:?--sign needs an identity}"; shift 2 ;;
        --require-all) REQUIRE_ALL=1; shift ;;
        *) echo "stage-macos-helpers.sh: unknown argument $1" >&2; exit 2 ;;
    esac
done
if [[ -z "$TARGET" ]]; then
    case "$(uname -m)" in
        arm64) TARGET="aarch64-apple-darwin" ;;
        x86_64) TARGET="x86_64-apple-darwin" ;;
        *) echo "stage-macos-helpers.sh: unsupported architecture $(uname -m)" >&2; exit 2 ;;
    esac
fi
[[ -f "$ENTITLEMENTS" ]] || { echo "ERROR: missing $ENTITLEMENTS" >&2; exit 1; }

# name  =>  source path relative to desktop/src-tauri, with -$TARGET appended.
HELPERS=(
    "matrx-syncd:sidecar/matrx-syncd"
    "matrx-egress:sidecar/matrx-egress"
    "cloudflared:sidecar/cloudflared"
    "llama-server:binaries/llama-server"
    "uv:binaries/uv"
)

mkdir -p "$STAGE_DIR"
echo "=== Staging macOS helpers for $TARGET into $STAGE_DIR"
MISSING=0
for entry in "${HELPERS[@]}"; do
    name="${entry%%:*}"
    src="$SRC_TAURI/${entry#*:}-$TARGET"
    dest="$STAGE_DIR/$name"
    if [[ ! -f "$src" ]]; then
        rm -f "$dest"
        if [[ "$REQUIRE_ALL" == "1" ]]; then
            echo "ERROR: helper input missing: $src" >&2
            MISSING=1
        else
            echo "  ⏭️  $name — no input at $src (skipped; dev checkout)"
        fi
        continue
    fi
    cp -f "$src" "$dest"
    chmod +x "$dest"
    if [[ -n "$IDENTITY" ]]; then
        # Per-file timeout (MXL-D-054): codesign can hang forever on one file.
        perl -e 'alarm 120; exec @ARGV' codesign \
            --force \
            --timestamp \
            --options runtime \
            --entitlements "$ENTITLEMENTS" \
            --sign "$IDENTITY" \
            "$dest" || { echo "ERROR: codesign failed or timed out on $dest" >&2; exit 1; }
        codesign --verify --strict --verbose=2 "$dest"
        # The whole point of this script: the helper must NOT carry a
        # profile-backed key. Prove it on the bytes we just produced.
        ENTS="$(codesign -d --entitlements - --xml "$dest" 2>/dev/null || true)"
        for key in com.apple.application-identifier \
                   com.apple.developer.team-identifier \
                   com.apple.security.application-groups \
                   com.apple.developer.authentication-services.autofill-credential-provider; do
            if [[ "$ENTS" == *"$key"* ]]; then
                echo "ERROR: staged helper $name carries the profile-backed entitlement '$key' — macOS will SIGKILL it at exec." >&2
                exit 1
            fi
        done
        echo "  ✅ $name — staged and signed with sidecar entitlements"
    else
        echo "  ✅ $name — staged (existing signature preserved; no --sign identity)"
    fi
done
[[ "$MISSING" == "0" ]] || exit 1
echo "=== macOS helpers staged"
