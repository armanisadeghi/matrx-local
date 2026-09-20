#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ARCH="$(uname -m)"
BRIDGE="$(bash "$ROOT/scripts/build-native-vault-bridge.sh" "$ARCH")"
SOURCE="$ROOT/native-vault-provider"
xcrun swiftc -parse-as-library -target "$ARCH-apple-macosx26.0" \
  -I "$BRIDGE" -Xcc "-fmodule-map-file=$BRIDGE/native_vault_coreFFI.modulemap" \
  "$BRIDGE/native_vault_core.swift" \
  "$SOURCE/NativeVaultCodec.swift" \
  "$SOURCE/NativeVaultEnrollmentLifecycle.swift" \
  "$SOURCE/NativeVaultState.swift" \
  "$SOURCE/NativeVaultTransport.swift" \
  "$SOURCE/NativeVaultPrivateSession.swift" \
  "$SOURCE/NativeVaultSessionAccess.swift" \
  "$SOURCE/NativeVaultPasskeyCodec.swift" \
  "$SOURCE/NativeVaultIdentity.swift" \
  "$SOURCE/NativeVaultExport.swift" \
  "$SOURCE/NativeVaultImportFile.swift" \
  "$SOURCE/NativeVaultImportParser.swift" \
  "$SOURCE/NativeVaultImportJournal.swift" \
  "$SOURCE/NativeVaultImport.swift" \
  "$SOURCE/NativeVaultImportTransport.swift" \
  "$SOURCE/NativeVaultImportHost.swift" \
  "$SOURCE/NativeVaultExchangeHost.swift" \
  "$SOURCE/tests/NativeVaultImportHostCorpus.swift" \
  -L "$SOURCE/core/target/${ARCH/arm64/aarch64}-apple-darwin/release" \
  -Xlinker -force_load -Xlinker "$SOURCE/core/target/${ARCH/arm64/aarch64}-apple-darwin/release/libnative_vault_core.a" \
  -framework AppKit -framework AuthenticationServices -framework LocalAuthentication -framework Security \
  -o "$WORK/corpus"
"$WORK/corpus"
