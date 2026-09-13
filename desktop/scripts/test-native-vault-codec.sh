#!/usr/bin/env bash
# Compile and execute the real extension's strict envelope implementation.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "native Vault codec corpus is macOS-only; skipping on $(uname -s)"
  exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/native-vault-provider"
DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
[[ -d "$DEVELOPER_DIR" ]] || { echo "ERROR: Xcode is required at $DEVELOPER_DIR." >&2; exit 1; }
export DEVELOPER_DIR
SWIFTC="$(xcrun --find swiftc)"
SDK_PATH="$(xcrun --sdk macosx --show-sdk-path)"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

"$SWIFTC" \
  -parse-as-library \
  -target "$(uname -m)-apple-macosx15.0" \
  -sdk "$SDK_PATH" \
  "$SOURCE/NativeVaultCodec.swift" \
  "$SOURCE/tests/NativeVaultCodecCorpus.swift" \
  -o "$WORKDIR/native-vault-codec-corpus"
"$WORKDIR/native-vault-codec-corpus"
