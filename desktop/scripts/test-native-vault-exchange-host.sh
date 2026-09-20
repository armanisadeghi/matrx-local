#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ARCH="$(uname -m)"
bash "$ROOT/scripts/build-native-vault-exchange.sh" "$ARCH" "$WORK"
xcrun swiftc -parse-as-library -target "$ARCH-apple-macosx15.0" \
  "$ROOT/native-vault-provider/tests/NativeVaultExchangeHostCorpus.swift" \
  -L "$WORK" -lmatrx_vault_exchange \
  -L "$ROOT/native-vault-provider/core/target/${ARCH/arm64/aarch64}-apple-darwin/release" \
  -Xlinker -force_load -Xlinker "$ROOT/native-vault-provider/core/target/${ARCH/arm64/aarch64}-apple-darwin/release/libnative_vault_core.a" \
  -framework AppKit -framework AuthenticationServices -framework LocalAuthentication -framework Security \
  -o "$WORK/corpus"
"$WORK/corpus"
