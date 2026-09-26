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
#   4. EVERY bundled helper in Contents/MacOS carries no profile-backed
#      entitlement AND actually EXECUTES (exit 137 = AMFI SIGKILL)
#   4. Native Vault provider has its own bundle identity, capability/profile,
#      and never grants its provider-only Keychain group to the host
#   5. Native Vault provider remains a distinct app extension with its profile
#   6. Safari Web Extension is present, independently signed, and pinned
#   7. spctl --assess (Gatekeeper accepts the app as notarized Developer ID)
#   8. xcrun stapler validate (notarization ticket stapled)
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
SAFARI_EXTENSION="$APP_PATH/Contents/PlugIns/Matrx Extend Safari.appex"
# Release CI sets MATRX_NATIVE_VAULT_PROVIDER=absent when the provider's
# profiles were not supplied: the host then ships without the provider and an
# unverified provider must not be present. Any other value keeps the provider
# a hard requirement.
NATIVE_VAULT_PROVIDER="${MATRX_NATIVE_VAULT_PROVIDER:-}"
if [[ "$NATIVE_VAULT_PROVIDER" == "absent" ]]; then
    [[ ! -e "$VAULT_PROVIDER" ]] || { echo "ERROR: native Vault provider present at $VAULT_PROVIDER although its release inputs were absent — an unverified provider must never ship" >&2; exit 1; }
    echo "    ⚠️  native Vault provider NOT shipped (release inputs absent); host-only verification"
else
    [[ -d "$VAULT_PROVIDER" ]] || { echo "ERROR: native Vault provider missing at $VAULT_PROVIDER" >&2; exit 1; }
fi

# 1. Deep, strict signature verification (covers every nested code object).
echo "--- [1/7] codesign --verify --deep --strict"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

# 2. Designated-requirement equivalence: parent and helper must both carry
#    the parent identifier and identical anchor clauses (same TCC identity).
echo "--- [2/7] designated-requirement equivalence"
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
echo "--- [3/7] helper entitlements"
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

# 4. Every shipped helper in Contents/MacOS must (a) carry none of the four
#    profile-backed entitlements and (b) actually run. A bare Mach-O cannot
#    carry a provisioning profile, so AMFI SIGKILLs one that claims a
#    profile-backed key: exit 137 at exec, with Gatekeeper and the
#    notarization ticket both still saying "accepted". v1.4.137 through
#    v1.4.170 shipped exactly that for matrx-syncd, matrx-egress,
#    cloudflared, llama-server and uv, because tauri-bundler signs every
#    `externalBin` with the HOST's entitlements file. Entitlement text alone
#    is not proof — this step EXECS each helper.
echo "--- [4/7] bundled helper entitlements + exec"
HOST_EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP_PATH/Contents/Info.plist")"
HELPER_DYLIBS="$APP_PATH/Contents/Resources/binaries"
HELPER_FAILED=0
HELPER_COUNT=0
while IFS= read -r helper; do
    helper_name="$(basename "$helper")"
    [[ "$helper_name" == "$HOST_EXECUTABLE" ]] && continue
    file "$helper" | grep -q "Mach-O" || continue
    HELPER_COUNT=$((HELPER_COUNT + 1))
    echo "    • $helper_name"

    HELPER_ENTS="$(codesign -d --entitlements - --xml "$helper" 2>/dev/null || true)"
    for key in com.apple.application-identifier \
               com.apple.developer.team-identifier \
               com.apple.security.application-groups \
               com.apple.developer.authentication-services.autofill-credential-provider keychain-access-groups com.apple.security.keychain-access-groups; do
        if [[ "$HELPER_ENTS" == *"$key"* ]]; then
            echo "ERROR: helper '$helper_name' carries the profile-backed entitlement '$key'. A bare Mach-O cannot embed a provisioning profile, so macOS will SIGKILL it at exec. Ship it as a pre-signed bundle.macOS.files entry (scripts/stage-macos-helpers.sh), never as bundle.externalBin." >&2
            HELPER_FAILED=1
        fi
    done

    # Every helper answers --version and exits. Bound it: a hung helper must
    # fail loudly, not wedge the release job.
    set +e
    HELPER_OUT="$(DYLD_LIBRARY_PATH="$HELPER_DYLIBS" perl -e 'alarm 30; exec @ARGV' "$helper" --version 2>&1)"
    HELPER_STATUS=$?
    set -e
    if [[ "$HELPER_STATUS" == "137" ]]; then
        echo "ERROR: helper '$helper_name' was SIGKILLed at exec (exit 137). macOS refused to launch it — almost always a restricted entitlement it cannot back with a provisioning profile. Output: ${HELPER_OUT:-<none>}" >&2
        HELPER_FAILED=1
    elif [[ "$HELPER_STATUS" -gt 128 ]]; then
        echo "ERROR: helper '$helper_name' died from signal $((HELPER_STATUS - 128)) at exec (exit $HELPER_STATUS). Output: ${HELPER_OUT:-<none>}" >&2
        HELPER_FAILED=1
    elif [[ "$HELPER_STATUS" != "0" ]]; then
        echo "ERROR: helper '$helper_name' --version exited $HELPER_STATUS. Output: ${HELPER_OUT:-<none>}" >&2
        HELPER_FAILED=1
    else
        echo "      ✅ no restricted entitlements; execs: $(printf '%s' "$HELPER_OUT" | head -1)"
    fi
done < <(find "$APP_PATH/Contents/MacOS" -maxdepth 1 -type f -perm -u+x | sort)
[[ "$HELPER_FAILED" == "0" ]] || exit 1
[[ "$HELPER_COUNT" -gt 0 ]] || { echo "ERROR: no bundled helpers found in Contents/MacOS — the app ships matrx-syncd, matrx-egress, cloudflared, llama-server and uv; finding none means the bundle layout changed and this gate stopped checking anything." >&2; exit 1; }
echo "    ✅ $HELPER_COUNT bundled helpers carry no profile-backed entitlement and all execute"

# 5. The provider is a distinct app extension. It has the AutoFill capability
# and its own Keychain group; the host also needs AutoFill permission but
# shares the protected session only within the reviewed native containing process.
# Final release verification validates each profile's
# Apple CMS signature, current validity, exact team/bundle/capabilities, and
# the certificate that actually signed that code object.
echo "--- [5/7] native Vault provider boundary"
if [[ "$NATIVE_VAULT_PROVIDER" == "absent" ]]; then
    echo "    ⚠️  SKIPPED: provider not shipped in this artifact (release inputs absent)"
    # A host WITHOUT an embedded provisioning profile must not carry
    # profile-backed (restricted) entitlements: macOS refuses to spawn such a
    # process (SIGKILL, "Launchd job spawn failed") while Gatekeeper and the
    # notarization ticket still say "accepted". v1.4.92 shipped exactly that.
    HOST_ENTS="$(codesign -d --entitlements - --xml "$APP_PATH" 2>/dev/null || true)"
    for key in com.apple.application-identifier com.apple.developer.team-identifier com.apple.security.application-groups com.apple.developer.authentication-services.autofill-credential-provider keychain-access-groups com.apple.security.keychain-access-groups; do
        if [[ "$HOST_ENTS" == *"$key"* ]]; then
            echo "ERROR: host carries the profile-backed entitlement '$key' but ships no provisioning profile — macOS will kill it at launch. Build the host with Entitlements.plist (no restricted keys) when the provider is absent." >&2
            exit 1
        fi
    done
    [[ ! -e "$APP_PATH/Contents/embedded.provisionprofile" ]] || { echo "ERROR: host embeds a provisioning profile although release inputs were absent" >&2; exit 1; }
    echo "    ✅ host carries no profile-backed entitlements"
    if [[ "$DEV_MODE" == "--dev" ]]; then
        echo "--- [6/7] spctl assess: SKIPPED (--dev)"
        echo "--- [7/7] stapler validate: SKIPPED (--dev)"
        echo "=== ✅ artifact verification passed (dev mode: signature + identity only)"
        exit 0
    fi
else
PROVIDER_INFO="$VAULT_PROVIDER/Contents/Info.plist"
plist_value() { /usr/libexec/PlistBuddy -c "Print :$2" "$1"; }
[[ "$(plist_value "$PROVIDER_INFO" CFBundleIdentifier)" == "com.aimatrx.desktop.vault-provider" ]] || { echo "ERROR: unexpected Vault provider bundle identifier" >&2; exit 1; }
[[ "$(plist_value "$PROVIDER_INFO" LSMinimumSystemVersion)" == "15.0" ]] || { echo "ERROR: Vault provider must retain macOS 15 minimum" >&2; exit 1; }
[[ "$(plist_value "$PROVIDER_INFO" 'NSExtension:NSExtensionPointIdentifier')" == "com.apple.authentication-services-credential-provider-ui" ]] || { echo "ERROR: unexpected Vault provider extension point" >&2; exit 1; }
"$REPO_ROOT/desktop/scripts/verify-native-vault-provider.sh" "$VAULT_PROVIDER"

if [[ "$DEV_MODE" == "--dev" ]]; then
    echo "    (release-profile validation skipped for explicitly unsigned dev artifact)"
    echo "--- [6/7] spctl assess: SKIPPED (--dev)"
    echo "--- [7/7] stapler validate: SKIPPED (--dev)"
    echo "=== ✅ artifact verification passed (dev mode: signature + identity only)"
    exit 0
fi

HOST_PROFILE="$APP_PATH/Contents/embedded.provisionprofile"
PROVIDER_PROFILE="$VAULT_PROVIDER/Contents/embedded.provisionprofile"
[[ -f "$HOST_PROFILE" ]] || { echo "ERROR: signed host is missing its provisioning profile" >&2; exit 1; }
[[ -f "$PROVIDER_PROFILE" ]] || { echo "ERROR: signed Vault provider is missing its provisioning profile" >&2; exit 1; }
python3 "$REPO_ROOT/scripts/verify-apple-provisioning-profile.py" \
    --kind host --profile "$HOST_PROFILE" --code "$APP_PATH"
python3 "$REPO_ROOT/scripts/verify-apple-provisioning-profile.py" \
    --kind provider --profile "$PROVIDER_PROFILE" --code "$VAULT_PROVIDER"
echo "    ✅ provider bundle, typed entitlement split, profiles, and signed certificates verified"

# The Safari extension is separately signed before Tauri copies it as a custom
# macOS bundle file. Its Developer ID contract deliberately has no profile and
# is independent from the native Vault provider's profile-backed capability.
echo "--- [6/8] Safari Web Extension boundary"
"$REPO_ROOT/scripts/verify-safari-web-extension.sh" "$SAFARI_EXTENSION"
fi

# 7. Gatekeeper acceptance — proves notarization is visible to the OS.
echo "--- [7/8] spctl --assess"
spctl --assess --type execute -vv "$APP_PATH"

# 8. Notarization ticket stapled to the artifact users download.
echo "--- [8/8] stapler validate"
xcrun stapler validate "$APP_PATH"

echo "=== ✅ artifact verification passed"
