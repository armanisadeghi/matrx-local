#!/usr/bin/env bash
# Verifies the extension build input and a nested .appex without asserting that
# it is signed, installed, enabled, or able to present on a real macOS system.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { echo "ERROR: $*" >&2; exit 1; }
SELF_TEST=false
REQUIRE_PROFILE=false
EXPECTED_ARCH=""
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --self-test) SELF_TEST=true ;;
    --require-profile) REQUIRE_PROFILE=true ;;
    --arch)
      EXPECTED_ARCH="${2:-}"
      [[ -n "$EXPECTED_ARCH" ]] || { echo "ERROR: --arch requires arm64 or x86_64" >&2; exit 1; }
      shift
      ;;
    *) fail "unknown option: $1" ;;
  esac
  shift
done
APPEX="${1:-$ROOT/native-vault-provider/build/AI Matrx Vault Provider.appex}"
INFO="$APPEX/Contents/Info.plist"
ENTITLEMENTS="$APPEX/Contents/Resources/VaultProvider.entitlements"
BINARY="$APPEX/Contents/MacOS/VaultProvider"

[[ -f "$INFO" ]] || fail "provider Info.plist is missing: $INFO"
[[ -f "$ENTITLEMENTS" ]] || fail "provider entitlements are missing: $ENTITLEMENTS"
[[ -f "$BINARY" ]] || fail "provider executable is missing: $BINARY"
nm -gU "$BINARY" | awk '$NF == "_uniffi_native_vault_core_fn_constructor_nativeoperation_new" { found = 1 } END { exit !found }' || fail "provider must retain the statically linked native Vault UniFFI operation symbol"
otool -hv "$BINARY" | awk '$5 == "EXECUTE" { found = 1 } END { exit !found }' || fail "provider executable must be an MH_EXECUTE app-extension executable"
otool -l "$BINARY" | awk '$1 == "cmd" && $2 == "LC_MAIN" { found = 1 } END { exit !found }' || fail "provider executable must carry LC_MAIN"
nm -u "$BINARY" | awk '$NF == "_NSExtensionMain" { found = 1 } END { exit !found }' || fail "provider executable must enter through _NSExtensionMain"
if [[ -n "$EXPECTED_ARCH" ]]; then
  ACTUAL_ARCHES="$(lipo -archs "$BINARY")"
  [[ "$ACTUAL_ARCHES" == "$EXPECTED_ARCH" ]] || fail "provider architecture must be $EXPECTED_ARCH, got: $ACTUAL_ARCHES"
fi

plist_value() { /usr/libexec/PlistBuddy -c "Print :$2" "$1"; }
[[ "$(plist_value "$INFO" CFBundleIdentifier)" == "com.aimatrx.desktop.vault-provider" ]] || fail "unexpected provider bundle identifier"
[[ "$(plist_value "$INFO" LSMinimumSystemVersion)" == "15.0" ]] || fail "provider must retain macOS 15 minimum"
[[ "$(plist_value "$INFO" 'NSExtension:NSExtensionPointIdentifier')" == "com.apple.authentication-services-credential-provider-ui" ]] || fail "unexpected extension point"
python3 - "$INFO" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as source:
    info = plistlib.load(source)
attributes = info.get("NSExtension", {}).get("NSExtensionAttributes", {})
capabilities = attributes.get("ASCredentialProviderExtensionCapabilities", {})
if not isinstance(capabilities, dict) or any(
    capabilities.get(key) is not True for key in ("ProvidesPasswords", "ProvidesPasskeys", "ShowsConfigurationUI")
):
    raise SystemExit("ERROR: password/configuration capabilities must be Boolean true")
if "ASCredentialProviderExtensionShowsConfigurationUI" in attributes:
    raise SystemExit("ERROR: obsolete configuration UI key is forbidden")
PY
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.security.app-sandbox')" == "true" ]] || fail "App Sandbox entitlement missing"
! /usr/libexec/PlistBuddy -c 'Print :com.apple.app-sandbox' "$ENTITLEMENTS" >/dev/null 2>&1 || fail "obsolete App Sandbox entitlement key is forbidden"
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.application-identifier')" == "JH83UH9P4D.com.aimatrx.desktop.vault-provider" ]] || fail "unexpected provider application identifier"
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.developer.team-identifier')" == "JH83UH9P4D" ]] || fail "unexpected provider team identifier"
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.developer.authentication-services.autofill-credential-provider')" == "true" ]] || fail "AutoFill provider entitlement missing"
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.security.network.client')" == "true" ]] || fail "network client entitlement missing"
[[ "$(plist_value "$ENTITLEMENTS" 'com.apple.security.application-groups:0')" == "group.com.aimatrx.desktop.vault-status" ]] || fail "unexpected App Group"
[[ "$(plist_value "$ENTITLEMENTS" 'keychain-access-groups:0')" == "JH83UH9P4D.com.aimatrx.desktop.vault-provider" ]] || fail "unexpected provider Keychain group"

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
  /usr/libexec/PlistBuddy -c 'Delete :com.apple.developer.authentication-services.autofill-credential-provider' "$WORKDIR/missing-entitlement.appex/Contents/Resources/VaultProvider.entitlements"
  expect_failure "missing AutoFill entitlement" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/missing-entitlement.appex"
  cp -R "$APPEX" "$WORKDIR/wrong-sandbox-key.appex"
  /usr/libexec/PlistBuddy -c 'Delete :com.apple.security.app-sandbox' "$WORKDIR/wrong-sandbox-key.appex/Contents/Resources/VaultProvider.entitlements"
  /usr/libexec/PlistBuddy -c 'Add :com.apple.app-sandbox bool true' "$WORKDIR/wrong-sandbox-key.appex/Contents/Resources/VaultProvider.entitlements"
  expect_failure "obsolete App Sandbox entitlement key" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/wrong-sandbox-key.appex"
  for capability in ProvidesPasswords ProvidesPasskeys ShowsConfigurationUI; do
    cp -R "$APPEX" "$WORKDIR/missing-$capability.appex"
    /usr/libexec/PlistBuddy -c "Delete :NSExtension:NSExtensionAttributes:ASCredentialProviderExtensionCapabilities:$capability" "$WORKDIR/missing-$capability.appex/Contents/Info.plist"
    expect_failure "missing $capability" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/missing-$capability.appex"
    for invalid_value in 'string true' 'integer 1' 'bool false'; do
      /usr/libexec/PlistBuddy -c "Add :NSExtension:NSExtensionAttributes:ASCredentialProviderExtensionCapabilities:$capability $invalid_value" "$WORKDIR/missing-$capability.appex/Contents/Info.plist"
      expect_failure "$invalid_value $capability" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/missing-$capability.appex"
      /usr/libexec/PlistBuddy -c "Delete :NSExtension:NSExtensionAttributes:ASCredentialProviderExtensionCapabilities:$capability" "$WORKDIR/missing-$capability.appex/Contents/Info.plist"
    done
  done
  cp -R "$APPEX" "$WORKDIR/obsolete-configuration.appex"
  /usr/libexec/PlistBuddy -c 'Add :NSExtension:NSExtensionAttributes:ASCredentialProviderExtensionShowsConfigurationUI bool true' "$WORKDIR/obsolete-configuration.appex/Contents/Info.plist"
  expect_failure "obsolete configuration UI key" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/obsolete-configuration.appex"
  cp -R "$APPEX" "$WORKDIR/missing-provider.appex"
  rm "$WORKDIR/missing-provider.appex/Contents/MacOS/VaultProvider"
  expect_failure "missing provider executable" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/missing-provider.appex"
  cp -R "$APPEX" "$WORKDIR/wrong-macho.appex"
  cp /usr/bin/true "$WORKDIR/wrong-macho.appex/Contents/MacOS/VaultProvider"
  expect_failure "non-executable provider image" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/wrong-macho.appex"
  cp -R "$APPEX" "$WORKDIR/missing-lc-main.appex"
  python3 - "$WORKDIR/missing-lc-main.appex/Contents/MacOS/VaultProvider" <<'PY'
import struct
import sys

path = sys.argv[1]
data = bytearray(open(path, "rb").read())
magic, _, _, _, ncmds, _, _, _ = struct.unpack_from("<IiiIIIII", data)
if magic != 0xFEEDFACF:
    raise SystemExit("expected a 64-bit little-endian Mach-O executable")
offset = 32
for _ in range(ncmds):
    command, size = struct.unpack_from("<II", data, offset)
    if command == 0x80000028:  # LC_MAIN
        struct.pack_into("<I", data, offset, 0x80000029)
        open(path, "wb").write(data)
        break
    offset += size
else:
    raise SystemExit("LC_MAIN not found")
PY
  expect_failure "missing LC_MAIN" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/missing-lc-main.appex"
  cp -R "$APPEX" "$WORKDIR/wrong-entry.appex"
  python3 - "$WORKDIR/wrong-entry.appex/Contents/MacOS/VaultProvider" <<'PY'
import sys

path = sys.argv[1]
data = open(path, "rb").read()
before = b"_NSExtensionMain"
after = b"_XSExtensionMain"
if before not in data:
    raise SystemExit("_NSExtensionMain symbol not found")
open(path, "wb").write(data.replace(before, after))
PY
  expect_failure "wrong extension entry point" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/wrong-entry.appex"
  cp -R "$APPEX" "$WORKDIR/missing-bridge-symbol.appex"
  python3 - "$WORKDIR/missing-bridge-symbol.appex/Contents/MacOS/VaultProvider" <<'PY'
import sys

path = sys.argv[1]
before = b"_uniffi_native_vault_core_fn_constructor_nativeoperation_new"
after = b"_xniffi_native_vault_core_fn_constructor_nativeoperation_new"
data = open(path, "rb").read()
if before not in data:
    raise SystemExit("native Vault bridge symbol not found")
open(path, "wb").write(data.replace(before, after))
PY
  expect_failure "missing native Vault bridge symbol" "$ROOT/scripts/verify-native-vault-provider.sh" "$WORKDIR/missing-bridge-symbol.appex"
  expect_failure "missing signed-provider profile" "$ROOT/scripts/verify-native-vault-provider.sh" --require-profile "$APPEX"
  echo "Self-test passed: wrong bundle, missing AutoFill, missing password/configuration capabilities, obsolete App Sandbox key, missing executable, non-executable Mach-O, missing LC_MAIN, wrong entry point, missing bridge symbol, and missing required profile were rejected."
fi
