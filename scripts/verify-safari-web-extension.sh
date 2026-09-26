#!/usr/bin/env bash
# Verify the signed Safari Web Extension bundle before staging it and after the
# containing AI Matrx app is packaged.

set -euo pipefail

APPEX_PATH="${1:?usage: scripts/verify-safari-web-extension.sh <Safari-extension.appex>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/desktop/scripts/safari-web-extension-source.sh"
EXPECTED_BUNDLE_ID="com.aimatrx.desktop.safari.Extension"
EXPECTED_SOURCE_REPOSITORY="$MATRX_SAFARI_EXTENSION_SOURCE_REPOSITORY"
EXPECTED_SOURCE_REVISION="$MATRX_SAFARI_EXTENSION_SOURCE_REVISION"

[[ -d "$APPEX_PATH" ]] || { echo "ERROR: Safari extension bundle is missing: $APPEX_PATH" >&2; exit 1; }
INFO_PLIST="$APPEX_PATH/Contents/Info.plist"
[[ -f "$INFO_PLIST" ]] || { echo "ERROR: Safari extension Info.plist is missing: $INFO_PLIST" >&2; exit 1; }

plist_value() {
    /usr/libexec/PlistBuddy -c "Print :$2" "$1"
}

[[ "$(plist_value "$INFO_PLIST" CFBundleIdentifier)" == "$EXPECTED_BUNDLE_ID" ]] || { echo "ERROR: Safari extension bundle identifier must be $EXPECTED_BUNDLE_ID." >&2; exit 1; }
[[ "$(plist_value "$INFO_PLIST" 'NSExtension:NSExtensionPointIdentifier')" == "com.apple.Safari.web-extension" ]] || { echo "ERROR: Safari extension has an unexpected extension point." >&2; exit 1; }
[[ "$(plist_value "$INFO_PLIST" MatrxSafariExtensionSourceRepository)" == "$EXPECTED_SOURCE_REPOSITORY" ]] || { echo "ERROR: Safari extension provenance repository does not match the pinned public source." >&2; exit 1; }
[[ "$(plist_value "$INFO_PLIST" MatrxSafariExtensionSourceRevision)" == "$EXPECTED_SOURCE_REVISION" ]] || { echo "ERROR: Safari extension provenance revision does not match $EXPECTED_SOURCE_REVISION." >&2; exit 1; }
[[ ! -e "$APPEX_PATH/Contents/embedded.provisionprofile" ]] || { echo "ERROR: Safari extension must not carry a provisioning profile; Developer ID signing is the production contract." >&2; exit 1; }

codesign --verify --strict --verbose=2 "$APPEX_PATH"
SIGNATURE="$(codesign -dvv "$APPEX_PATH" 2>&1)"
[[ "$SIGNATURE" == *"Authority=Developer ID Application:"* ]] || { echo "ERROR: Safari extension is not signed with a Developer ID Application identity." >&2; exit 1; }
[[ "$SIGNATURE" == *"flags=0x10000(runtime)"* ]] || { echo "ERROR: Safari extension is missing the hardened runtime signature option." >&2; exit 1; }
[[ "$SIGNATURE" == *"Timestamp="* ]] || { echo "ERROR: Safari extension signature is missing a secure timestamp." >&2; exit 1; }

echo "PASS signed Safari Web Extension: $APPEX_PATH"
