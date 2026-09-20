#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ARCH="$(uname -m)"
TARGET="${ARCH/arm64/aarch64}-apple-darwin"
BRIDGE="$(bash "$ROOT/scripts/build-native-vault-bridge.sh" "$ARCH")"
xcrun swiftc -parse-as-library -target "$ARCH-apple-macosx26.0" \
  -I "$BRIDGE" -Xcc "-fmodule-map-file=$BRIDGE/native_vault_coreFFI.modulemap" \
  -L "$ROOT/native-vault-provider/core/target/$TARGET/release" -lnative_vault_core \
  "$BRIDGE/native_vault_core.swift" \
  "$ROOT/native-vault-provider/NativeVaultImportJournal.swift" \
  "$ROOT/native-vault-provider/NativeVaultImport.swift" \
  "$ROOT/native-vault-provider/NativeVaultImportFile.swift" \
  "$ROOT/native-vault-provider/NativeVaultImportParser.swift" \
  "$ROOT/native-vault-provider/tests/NativeVaultImportParserCorpus.swift" -o "$WORK/corpus"
"$WORK/corpus"
