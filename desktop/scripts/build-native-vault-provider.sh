#!/usr/bin/env bash
# Build the extension independently of the host. Development builds are
# deliberately unsigned. Release CI supplies the provider's private profile
# and signing identity before Tauri seals the separately signed provider into
# the host app.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "native Vault provider is macOS-only; skipping on $(uname -s)"
  exit 0
fi

DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
[[ -d "$DEVELOPER_DIR" ]] || {
  echo "ERROR: Xcode is required at $DEVELOPER_DIR. Set DEVELOPER_DIR to a full Xcode installation." >&2
  exit 1
}
export DEVELOPER_DIR
SWIFTC="$(xcrun --find swiftc 2>/dev/null || true)"
[[ -n "$SWIFTC" ]] || {
  echo "ERROR: no Swift compiler is available through $DEVELOPER_DIR. Install full Xcode or set DEVELOPER_DIR." >&2
  exit 1
}
SDK_PATH="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)"
[[ -n "$SDK_PATH" ]] || {
  echo "ERROR: no macOS SDK is available through $DEVELOPER_DIR." >&2
  exit 1
}

# Tauri sets TAURI_ENV_ARCH for cross-architecture release legs. Do not let
# the build machine's architecture silently produce the wrong nested artifact.
case "${TAURI_ENV_ARCH:-$(uname -m)}" in
  arm64|aarch64|aarch64-apple-darwin) TARGET_ARCH="arm64" ;;
  x86_64|x86_64-apple-darwin) TARGET_ARCH="x86_64" ;;
  *)
    echo "ERROR: unsupported native Vault provider architecture: ${TAURI_ENV_ARCH:-$(uname -m)}. Expected arm64/aarch64 or x86_64." >&2
    exit 1
    ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/native-vault-provider"
OUTPUT="$SOURCE/build/AI Matrx Vault Provider.appex"
CONTENTS="$OUTPUT/Contents"
rm -rf "$OUTPUT"
mkdir -p "$CONTENTS/MacOS"

"$SWIFTC" \
  -application-extension \
  -emit-executable \
  -Xlinker -e \
  -Xlinker _NSExtensionMain \
  -parse-as-library \
  -module-name VaultProvider \
  -target "$TARGET_ARCH-apple-macosx15.0" \
  -sdk "$SDK_PATH" \
  -framework AppKit \
  -framework AuthenticationServices \
  "$SOURCE/CredentialProviderViewController.swift" \
  -o "$CONTENTS/MacOS/VaultProvider"
cp "$SOURCE/Info.plist" "$CONTENTS/Info.plist"
cp "$SOURCE/VaultProvider.entitlements" "$CONTENTS/VaultProvider.entitlements"
plutil -lint "$CONTENTS/Info.plist" >/dev/null
plutil -lint "$CONTENTS/VaultProvider.entitlements" >/dev/null

# A release provider has a distinct identity/profile from its Tauri host. Do
# not make local source builds require private release inputs, but make a
# partially supplied release configuration fail before the host is signed.
if [[ -n "${MATRX_VAULT_PROVIDER_PROFILE_FILE:-}" || -n "${MATRX_APPLE_SIGNING_IDENTITY:-}" ]]; then
  [[ -n "${MATRX_VAULT_PROVIDER_PROFILE_FILE:-}" ]] || {
    echo "ERROR: MATRX_VAULT_PROVIDER_PROFILE_FILE is required when signing the native Vault provider." >&2
    exit 1
  }
  [[ -f "$MATRX_VAULT_PROVIDER_PROFILE_FILE" ]] || {
    echo "ERROR: native Vault provider provisioning profile is missing: $MATRX_VAULT_PROVIDER_PROFILE_FILE" >&2
    exit 1
  }
  [[ -n "${MATRX_APPLE_SIGNING_IDENTITY:-}" ]] || {
    echo "ERROR: MATRX_APPLE_SIGNING_IDENTITY is required when signing the native Vault provider." >&2
    exit 1
  }
  /usr/bin/ditto "$MATRX_VAULT_PROVIDER_PROFILE_FILE" "$CONTENTS/embedded.provisionprofile"
  codesign \
    --force \
    --timestamp \
    --options runtime \
    --entitlements "$SOURCE/VaultProvider.entitlements" \
    --sign "$MATRX_APPLE_SIGNING_IDENTITY" \
    "$OUTPUT"
  codesign --verify --strict --verbose=2 "$OUTPUT"
  echo "Built signed $TARGET_ARCH native Vault provider: $OUTPUT"
else
  echo "Built unsigned $TARGET_ARCH native Vault provider: $OUTPUT"
fi

"$ROOT/scripts/verify-native-vault-provider.sh" --arch "$TARGET_ARCH" "$OUTPUT"
