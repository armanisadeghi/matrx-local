#!/usr/bin/env bash
# Native containing-app code only; no provider UI or private IPC surface.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-$(uname -m)}" in
  arm64|aarch64|aarch64-apple-darwin) ARCH=arm64 ;;
  x86_64|x86_64-apple-darwin) ARCH=x86_64 ;;
  *) echo 'Unsupported native exchange architecture' >&2; exit 1 ;;
esac
OUT="${2:?output directory required}"
mkdir -p "$OUT"
SOURCE="$ROOT/native-vault-provider"
BRIDGE="$(bash "$ROOT/scripts/build-native-vault-bridge.sh" "$ARCH")"
SOURCES=()
for file in NativeVaultCodec NativeVaultEnrollmentLifecycle NativeVaultState NativeVaultTransport NativeVaultPrivateSession NativeVaultSessionAccess NativeVaultPasskeyCodec NativeVaultIdentity NativeVaultExport NativeVaultImportFile NativeVaultImportParser NativeVaultImportJournal NativeVaultImport NativeVaultImportTransport NativeVaultImportHost NativeVaultExchangeHost; do
  SOURCES+=("$SOURCE/$file.swift")
done
"$(xcrun --find swiftc)" -emit-library -static -parse-as-library \
  -module-name MatrxVaultExchange -target "$ARCH-apple-macosx15.0" \
  -sdk "$(xcrun --sdk macosx --show-sdk-path)" \
  -I "$BRIDGE" -Xcc "-fmodule-map-file=$BRIDGE/native_vault_coreFFI.modulemap" \
  "$BRIDGE/native_vault_core.swift" "${SOURCES[@]}" \
  -o "$OUT/libmatrx_vault_exchange.a"
