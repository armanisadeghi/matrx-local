#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="$(mktemp -d)"; trap 'rm -rf "$WORKDIR"' EXIT
SWIFTC="$(xcrun --find swiftc)"; SDK="$(xcrun --sdk macosx --show-sdk-path)"
ARCH="$(uname -m)"
BRIDGE="$($ROOT/scripts/build-native-vault-bridge.sh "$ARCH")"
RUST_ARCH="${ARCH/arm64/aarch64}"
"$SWIFTC" -parse-as-library -sdk "$SDK" -target "$(uname -m)-apple-macosx15.0" \
  -I "$BRIDGE" -Xcc "-fmodule-map-file=$BRIDGE/native_vault_coreFFI.modulemap" \
  -L "$ROOT/native-vault-provider/core/target/$RUST_ARCH-apple-darwin/release" -lnative_vault_core \
  "$BRIDGE/native_vault_core.swift" \
  "$ROOT/native-vault-provider/NativeVaultCodec.swift" \
  "$ROOT/native-vault-provider/NativeVaultEnrollmentLifecycle.swift" \
  "$ROOT/native-vault-provider/NativeVaultState.swift" \
  "$ROOT/native-vault-provider/NativeVaultTransport.swift" \
  "$ROOT/native-vault-provider/NativeVaultPrivateSession.swift" \
  "$ROOT/native-vault-provider/NativeVaultSessionAccess.swift" \
  "$ROOT/native-vault-provider/NativeVaultPassword.swift" \
  "$ROOT/native-vault-provider/NativeVaultPasskeyCodec.swift" \
  "$ROOT/native-vault-provider/NativeVaultPasskey.swift" \
  "$ROOT/native-vault-provider/NativeVaultIdentity.swift" \
  "$ROOT/native-vault-provider/CredentialProviderViewController.swift" \
  "$ROOT/native-vault-provider/tests/NativeVaultConfigurationCorpus.swift" \
  -o "$WORKDIR/corpus"
"$WORKDIR/corpus"
