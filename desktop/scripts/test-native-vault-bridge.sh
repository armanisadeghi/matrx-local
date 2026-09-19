#!/usr/bin/env bash
# Real Swift -> UniFFI -> maintained Rust -> Python Fido2Server proof.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="aarch64-apple-darwin"
BRIDGE="$($ROOT/scripts/build-native-vault-bridge.sh arm64)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
xcrun swiftc -application-extension -parse-as-library -target arm64-apple-macosx15.0 \
  -I "$BRIDGE" -Xcc "-fmodule-map-file=$BRIDGE/native_vault_coreFFI.modulemap" \
  -L "$ROOT/native-vault-provider/core/target/$TARGET/release" -lnative_vault_core \
  "$BRIDGE/native_vault_core.swift" "$ROOT/native-vault-provider/tests/NativeVaultBridgeFido2.swift" \
  -framework CryptoKit -o "$WORK/native-vault-bridge-fido2"
uv run --no-project --with 'fido2==2.2.1' python "$ROOT/native-vault-provider/core/provenance/fido2_swift_bridge_verifier.py" "$WORK/native-vault-bridge-fido2"
ACCEPTANCE_OUTPUT="$("$WORK/native-vault-bridge-fido2" acceptance 2>&1)"
if [[ "$ACCEPTANCE_OUTPUT" != "PASS native Swift bridge acceptance" ]]; then
  echo "Native bridge acceptance emitted unexpected diagnostics" >&2
  exit 1
fi
printf '%s\n' "$ACCEPTANCE_OUTPUT"
