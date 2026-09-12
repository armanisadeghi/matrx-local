#!/usr/bin/env bash
# Verifies the extension build input and a nested .appex without asserting that
# it is signed, installed, enabled, or able to present on a real macOS system.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SELF_TEST=false
REQUIRE_PROFILE=false
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --self-test) SELF_TEST=true ;;
    --require-profile) REQUIRE_PROFILE=true ;;
    *) fail "unknown option: $1" ;;
  esac
  shift
done
APPEX="${1:-$ROOT/native-vault-provider/build/AI Matrx Vault Provider.appex}"
INFO="$APPEX/Contents/Info.plist"
ENTITLEMENTS="$APPEX/Contents/VaultProvider.entitlements"
BINARY="$APPEX/Contents/MacOS/VaultProvider"

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ -f "$INFO" ]] || fail "provider Info.plist is missing: $INFO"
[[ -f "$ENTITLEMENTS" ]] || fail "provider entitlements are missing: $ENTITLEMENTS"
[[ -f "$BINARY" ]] || fail "provider executable is missing: $BINARY"
otool -hv "$BINARY" | awk '$5 == "BUNDLE" { found = 1 } END { exit !found }' || fail "provider executable must be an MH_BUNDLE app-extension image"

plist_value() { /usr/libexec/PlistBuddy -c "Print :$2" "$1"; }
[[ "$(plist_value "$INFO" CFBundleIdentifier)" == "com.aimatrx.desktop.vault-provider" ]] || fail "unexpected provider bundle identifier"
[[ "$(plist_value "$INFO" LSMinimumSystemVersion)" == "15.0" ]] || fail "provider must retain macOS 15 minimum"
[[ "$(plist_value "$INFO" 'NSExtension:NSExtensionPointIdentifier')" == "com.apple.authentication-services-credential-provider-ui" ]] || fail "unexpected extension point"
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.developer.authentication-services.autofill-credential-provider')" == "true" ]] || fail "AutoFill provider entitlement missing"
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.security.network.client')" == "true" ]] || fail "network client entitlement missing"
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.security.application-groups:0')" == "group.com.aimatrx.desktop.vault-status" ]] || fail "unexpected App Group"
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.security.keychain-access-groups:0')" == "JH83UH9P4D.com.aimatrx.desktop.vault-provider" ]] || fail "unexpected provider Keychain group"

SIGNATURE_INFO="$(codesign -dv --verbose=4 "$APPEX" 2>&1 || true)"
# swiftc produces an ad-hoc linker signature even for this deliberately
# unsigned build. Only a real signing identity creates the profile obligation.
if [[ "$REQUIRE_PROFILE" == true || ( "$SIGNATURE_INFO" != *"Signature=adhoc"* && "$SIGNATURE_INFO" == *"Authority="* ) ]]; then
  [[ -f "$APPEX/Contents/embedded.provisionprofile" ]] || fail "signed provider is missing its provisioning profile"
fi
echo "Verified native Vault provider structure: $APPEX"

if [[ "$SELF_TEST" == true ]]; then
  WORKDIR="$(mktemp -d)"
  trap 'rm -rf "$WORKDIR"' EXIT
  expect_failure() {
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
      fail "self-test did not reject $label"
    fi
  }
  cp -R "$APPEX" "$WORKDIR/wrong-bundle.appex"
  /usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier com.example.wrong' "$WORKDIR/wrong-bundle.appex/Contents/Info.plist"
  expect_failure "wrong bundle identifier" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/wrong-bundle.appex"
  cp -R "$APPEX" "$WORKDIR/missing-entitlement.appex"
  /usr/libexec/PlistBuddy -c 'Delete :com.apple.developer.authentication-services.autofill-credential-provider' "$WORKDIR/missing-entitlement.appex/Contents/VaultProvider.entitlements"
  expect_failure "missing AutoFill entitlement" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/missing-entitlement.appex"
  cp -R "$APPEX" "$WORKDIR/missing-provider.appex"
  rm "$WORKDIR/missing-provider.appex/Contents/MacOS/VaultProvider"
  expect_failure "missing provider executable" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/missing-provider.appex"
  cp -R "$APPEX" "$WORKDIR/wrong-macho.appex"
  cp /usr/bin/true "$WORKDIR/wrong-macho.appex/Contents/MacOS/VaultProvider"
  expect_failure "non-bundle provider executable" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/wrong-macho.appex"
  expect_failure "missing signed-provider profile" "$ROOT/scripts/verify-native-vault-provider.sh" --require-profile "$APPEX"
  echo "Self-test passed: wrong bundle, missing entitlement, missing executable, non-bundle Mach-O, and missing required profile were rejected."
fi
