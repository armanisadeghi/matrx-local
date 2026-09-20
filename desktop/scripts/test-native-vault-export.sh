#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
case "$(uname -m)" in
  arm64) TARGET="aarch64-apple-darwin" ;;
  x86_64) TARGET="x86_64-apple-darwin" ;;
  *) echo "unsupported architecture" >&2; exit 2 ;;
esac
BRIDGE="${NATIVE_VAULT_BRIDGE_DIR:-$ROOT/native-vault-provider/build/native-vault-bridge/$TARGET}"
LIB="$ROOT/native-vault-provider/core/target/$TARGET/release"
test -f "$BRIDGE/native_vault_core.swift"
test -f "$BRIDGE/native_vault_coreFFI.modulemap"
test -f "$LIB/libnative_vault_core.a"
xcrun swiftc -parse-as-library -target "$(uname -m)-apple-macosx26.0" \
  -I "$BRIDGE" -Xcc "-fmodule-map-file=$BRIDGE/native_vault_coreFFI.modulemap" \
  -L "$LIB" -lnative_vault_core \
  "$BRIDGE/native_vault_core.swift" \
  "$ROOT/native-vault-provider/NativeVaultCodec.swift" \
  "$ROOT/native-vault-provider/NativeVaultEnrollmentLifecycle.swift" \
  "$ROOT/native-vault-provider/NativeVaultState.swift" \
  "$ROOT/native-vault-provider/NativeVaultPrivateSession.swift" \
  "$ROOT/native-vault-provider/NativeVaultTransport.swift" \
  "$ROOT/native-vault-provider/NativeVaultSessionAccess.swift" \
  "$ROOT/native-vault-provider/NativeVaultPasskeyCodec.swift" \
  "$ROOT/native-vault-provider/NativeVaultExport.swift" \
  "$ROOT/native-vault-provider/tests/NativeVaultExportCorpus.swift" \
  -o "$WORK/corpus"
"$WORK/corpus"
