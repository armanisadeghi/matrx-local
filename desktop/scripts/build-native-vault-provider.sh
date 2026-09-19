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

DEVELOPER_DIR="${DEVELOPER_DIR:-/Library/Developer/CommandLineTools}"
[[ -d "$DEVELOPER_DIR" ]] || {
  echo "ERROR: macOS Command Line Tools are required at $DEVELOPER_DIR. Set DEVELOPER_DIR to a usable toolchain." >&2
  exit 1
}
export DEVELOPER_DIR
SWIFTC="$(xcrun --find swiftc 2>/dev/null || true)"
[[ -n "$SWIFTC" ]] || {
  echo "ERROR: no Swift compiler is available through $DEVELOPER_DIR. Install Command Line Tools or set DEVELOPER_DIR." >&2
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
BRIDGE_TARGET="$($ROOT/scripts/build-native-vault-bridge.sh "$TARGET_ARCH")"
RUST_TARGET="${TARGET_ARCH/arm64/aarch64}-apple-darwin"
RUST_LIB="$SOURCE/core/target/$RUST_TARGET/release"
OUTPUT="$SOURCE/build/AI Matrx Vault Provider.appex"
CONTENTS="$OUTPUT/Contents"
rm -rf "$OUTPUT"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

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
  -framework CryptoKit \
  -framework LocalAuthentication \
  -framework Security \
  -I "$BRIDGE_TARGET" \
  -Xcc "-fmodule-map-file=$BRIDGE_TARGET/native_vault_coreFFI.modulemap" \
  -L "$RUST_LIB" \
  -Xlinker -force_load \
  -Xlinker "$RUST_LIB/libnative_vault_core.a" \
  "$BRIDGE_TARGET/native_vault_core.swift" \
  "$SOURCE/NativeVaultBridgeConsumer.swift" \
  "$SOURCE/NativeVaultCodec.swift" \
  "$SOURCE/NativeVaultEnrollmentLifecycle.swift" \
  "$SOURCE/NativeVaultState.swift" \
  "$SOURCE/NativeVaultPrivateSession.swift" \
  "$SOURCE/NativeVaultPassword.swift" \
  "$SOURCE/CredentialProviderViewController.swift" \
  -o "$CONTENTS/MacOS/VaultProvider"
cp "$SOURCE/Info.plist" "$CONTENTS/Info.plist"
# Entitlements are codesign input, not a bundle-root payload. A root-level
# non-code file becomes an unsealed nested component during release signing.
cp "$SOURCE/VaultProvider.entitlements" "$CONTENTS/Resources/VaultProvider.entitlements"
VAULT_PUBLISHABLE_KEY="${VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY:-}"
if [[ -z "$VAULT_PUBLISHABLE_KEY" || ! "$VAULT_PUBLISHABLE_KEY" =~ ^[!-~]+$ ]]; then
  echo "ERROR: VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY must be supplied as an allowlisted printable public build input." >&2
  exit 1
fi
/usr/libexec/PlistBuddy -c "Set :MatrxVaultSupabasePublishableKey $VAULT_PUBLISHABLE_KEY" "$CONTENTS/Info.plist"
plutil -lint "$CONTENTS/Info.plist" >/dev/null
plutil -lint "$CONTENTS/Resources/VaultProvider.entitlements" >/dev/null

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
