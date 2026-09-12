#!/usr/bin/env bash
# verify-macos-artifact.sh — final-artifact signing verification for macOS.
#
# The signing contract lives in build scripts; this script proves the SHIPPED
# artifact honors it. The May 2026 regression (helper signed as a separate
# TCC principal → false "Full Disk Access missing" for every user) passed CI
# green because nothing ever inspected the built .app. This is that gate.
#
# Usage:
#   scripts/verify-macos-artifact.sh <path-to-.app | path-to-.app.tar.gz> [--dev]
#
#   --dev  skip Gatekeeper/notarization checks (steps 4-5) so a rung-5
#          `smoke.sh packaged` build — deliberately unsigned/un-notarized for
#          Apple-notarization checks — can still exercise steps 1-3.
#
# Checks:
#   1. codesign --verify --deep --strict on the app (nested signatures valid)
#   2. Parent ↔ Helper designated-requirement equivalence: both must carry
#      the identifier from desktop/src-tauri/tauri.conf.json and the same
#      certificate anchor — ONE TCC identity, one Full Disk Access grant.
#   3. Helper entitlements present (sidecar.entitlements.plist keys)
#   4. Native Vault provider has its own bundle identity, capability/profile,
#      and never grants its provider-only Keychain group to the host
#   5. spctl --assess (Gatekeeper accepts the app as notarized Developer ID)
#   6. xcrun stapler validate (notarization ticket stapled)
#
# Exit non-zero on any failure — in release.yml that keeps the draft release
# unpublished.

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "verify-macos-artifact.sh must run on macOS" >&2
    exit 2
fi

ARTIFACT="${1:?usage: verify-macos-artifact.sh <.app or .app.tar.gz> [--dev]}"
DEV_MODE="${2:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT_IDENTIFIER="$(python3 -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["identifier"])' "$REPO_ROOT/desktop/src-tauri/tauri.conf.json")"

WORKDIR=""
cleanup() {
    if [[ -n "$WORKDIR" ]]; then
        rm -rf "$WORKDIR"
    fi
}
trap cleanup EXIT

APP_PATH="$ARTIFACT"
if [[ "$ARTIFACT" == *.tar.gz ]]; then
    WORKDIR="$(mktemp -d)"
    echo "Extracting updater archive: $ARTIFACT"
    tar -xzf "$ARTIFACT" -C "$WORKDIR"
    APP_PATH="$(find "$WORKDIR" -maxdepth 2 -name "*.app" -type d | head -1)"
    [[ -n "$APP_PATH" ]] || { echo "ERROR: no .app found inside $ARTIFACT" >&2; exit 1; }
fi
[[ -d "$APP_PATH" ]] || { echo "ERROR: not an .app bundle: $APP_PATH" >&2; exit 1; }

echo "=== Verifying macOS artifact: $APP_PATH"
echo "    Expected identifier: $PARENT_IDENTIFIER"

HELPER_APP="$APP_PATH/Contents/Frameworks/Matrx Engine.app"
[[ -d "$HELPER_APP" ]] || { echo "ERROR: nested helper missing at $HELPER_APP" >&2; exit 1; }
VAULT_PROVIDER="$APP_PATH/Contents/PlugIns/AI Matrx Vault Provider.appex"
[[ -d "$VAULT_PROVIDER" ]] || { echo "ERROR: native Vault provider missing at $VAULT_PROVIDER" >&2; exit 1; }

# 1. Deep, strict signature verification (covers every nested code object).
echo "--- [1/6] codesign --verify --deep --strict"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

# 2. Designated-requirement equivalence: parent and helper must both carry
#    the parent identifier and identical anchor clauses (same TCC identity).
echo "--- [2/6] designated-requirement equivalence"
req_of() { codesign -dr - "$1" 2>&1 | sed -n 's/^designated => //p'; }
anchor_of() { req_of "$1" | sed -E 's/^identifier "[^"]*" and //'; }
PARENT_REQ="$(req_of "$APP_PATH")"
HELPER_REQ="$(req_of "$HELPER_APP")"
for name_req in "parent:$PARENT_REQ" "helper:$HELPER_REQ"; do
    who="${name_req%%:*}"; req="${name_req#*:}"
    if [[ "$req" != *"identifier \"$PARENT_IDENTIFIER\""* ]]; then
        echo "ERROR: $who designated requirement lacks identifier \"$PARENT_IDENTIFIER\":" >&2
        echo "  $req" >&2
        exit 1
    fi
done
if [[ "$(anchor_of "$APP_PATH")" != "$(anchor_of "$HELPER_APP")" ]]; then
    echo "ERROR: parent and helper anchor clauses differ — separate TCC identities:" >&2
    echo "  parent: $PARENT_REQ" >&2
    echo "  helper: $HELPER_REQ" >&2
    exit 1
fi
echo "    ✅ parent and helper share identifier + anchor (one TCC identity)"

# 3. Helper entitlements: every key in the sidecar entitlements plist must be
#    present in the signed helper.
echo "--- [3/6] helper entitlements"
ENTITLEMENTS_FILE="$REPO_ROOT/desktop/src-tauri/sidecar/sidecar.entitlements.plist"
if [[ -f "$ENTITLEMENTS_FILE" ]]; then
    HELPER_ENTS="$(codesign -d --entitlements - --xml "$HELPER_APP" 2>/dev/null || true)"
    MISSING=0
    while IFS= read -r key; do
        [[ -z "$key" ]] && continue
        if [[ "$HELPER_ENTS" != *"$key"* ]]; then
            echo "ERROR: helper is missing expected entitlement: $key" >&2
            MISSING=1
        fi
    done < <(python3 -c '
import plistlib, sys
with open(sys.argv[1], "rb") as f:
    for k in plistlib.load(f):
        print(k)
' "$ENTITLEMENTS_FILE")
    [[ "$MISSING" == "0" ]] || exit 1
    echo "    ✅ helper carries all expected entitlements"
else
    echo "    (no sidecar entitlements file — skipping)"
fi

# 4. The provider is a distinct app extension. It has the AutoFill capability
# and its own Keychain group; the host is deliberately limited to the
# nonsecret App Group. A signed provider must carry the matching profile.
echo "--- [4/6] native Vault provider boundary"
PROVIDER_INFO="$VAULT_PROVIDER/Contents/Info.plist"
PROVIDER_ENTS="$(codesign -d --entitlements - --xml "$VAULT_PROVIDER" 2>/dev/null || true)"
HOST_ENTS="$(codesign -d --entitlements - --xml "$APP_PATH" 2>/dev/null || true)"
plist_value() { /usr/libexec/PlistBuddy -c "Print :$2" "$1"; }
[[ "$(plist_value "$PROVIDER_INFO" CFBundleIdentifier)" == "com.aimatrx.desktop.vault-provider" ]] || { echo "ERROR: unexpected Vault provider bundle identifier" >&2; exit 1; }
[[ "$(plist_value "$PROVIDER_INFO" LSMinimumSystemVersion)" == "15.0" ]] || { echo "ERROR: Vault provider must retain macOS 15 minimum" >&2; exit 1; }
[[ "$(plist_value "$PROVIDER_INFO" 'NSExtension:NSExtensionPointIdentifier')" == "com.apple.authentication-services-credential-provider-ui" ]] || { echo "ERROR: unexpected Vault provider extension point" >&2; exit 1; }
for required in \
  "com.apple.developer.authentication-services.autofill-credential-provider" \
  "group.com.aimatrx.desktop.vault-status" \
  "JH83UH9P4D.com.aimatrx.desktop.vault-provider"; do
  [[ "$PROVIDER_ENTS" == *"$required"* ]] || { echo "ERROR: Vault provider missing signed entitlement: $required" >&2; exit 1; }
done
[[ "$HOST_ENTS" == *"group.com.aimatrx.desktop.vault-status"* ]] || { echo "ERROR: host missing nonsecret Vault status App Group" >&2; exit 1; }
[[ "$HOST_ENTS" != *"JH83UH9P4D.com.aimatrx.desktop.vault-provider"* ]] || { echo "ERROR: host must not access the provider-only Keychain group" >&2; exit 1; }
PROVIDER_SIGNATURE="$(codesign -dv --verbose=4 "$VAULT_PROVIDER" 2>&1 || true)"
if [[ "$PROVIDER_SIGNATURE" != *"Signature=adhoc"* && "$PROVIDER_SIGNATURE" == *"Authority="* ]]; then
  [[ -f "$VAULT_PROVIDER/Contents/embedded.provisionprofile" ]] || { echo "ERROR: signed Vault provider is missing its provisioning profile" >&2; exit 1; }
fi
echo "    ✅ provider bundle, entitlement split, and signed-profile requirement verified"

if [[ "$DEV_MODE" == "--dev" ]]; then
    echo "--- [5/6] spctl assess: SKIPPED (--dev)"
    echo "--- [6/6] stapler validate: SKIPPED (--dev)"
    echo "=== ✅ artifact verification passed (dev mode: signature + identity only)"
    exit 0
fi

# 4. Gatekeeper acceptance — proves notarization is visible to the OS.
echo "--- [5/6] spctl --assess"
spctl --assess --type execute -vv "$APP_PATH"

# 5. Notarization ticket stapled to the artifact users download.
echo "--- [6/6] stapler validate"
xcrun stapler validate "$APP_PATH"

echo "=== ✅ artifact verification passed"
