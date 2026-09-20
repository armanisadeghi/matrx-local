#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ARCH="$(uname -m)"
bash "$ROOT/scripts/build-native-vault-exchange.sh" "$ARCH" "$WORK"
TARGET="${ARCH/arm64/aarch64}-apple-darwin"
rustc --edition=2021 "$ROOT/src-tauri/tests/native_vault_exchange_bridge.rs" \
  -L "$WORK" -l static=matrx_vault_exchange \
  -L "$ROOT/native-vault-provider/core/target/$TARGET/release" -l static=native_vault_core \
  -L /usr/lib/swift -l framework=AppKit -l framework=AuthenticationServices \
  -l framework=LocalAuthentication -l framework=Security \
  -C link-arg=-Wl,-rpath,/usr/lib/swift \
  -o "$WORK/corpus"
"$WORK/corpus"
