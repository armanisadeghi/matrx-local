#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="$(mktemp -d)"; trap 'rm -rf "$WORKDIR"' EXIT
"$(xcrun --find swiftc)" -parse-as-library -sdk "$(xcrun --sdk macosx --show-sdk-path)" \
  -target "$(uname -m)-apple-macosx15.0" \
  "$ROOT/native-vault-provider/NativeVaultCodec.swift" \
  "$ROOT/native-vault-provider/NativeVaultPasskeyCodec.swift" \
  "$ROOT/native-vault-provider/tests/NativeVaultPasskeyCodecCorpus.swift" \
  -o "$WORKDIR/corpus"
"$WORKDIR/corpus"
