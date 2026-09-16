#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="$(mktemp -d)"; trap 'rm -rf "$WORKDIR"' EXIT
SWIFTC="$(xcrun --find swiftc)"; SDK="$(xcrun --sdk macosx --show-sdk-path)"
"$SWIFTC" -parse-as-library -sdk "$SDK" -target "$(uname -m)-apple-macosx15.0" \
  "$ROOT/native-vault-provider/NativeVaultCodec.swift" \
  "$ROOT/native-vault-provider/NativeVaultState.swift" \
  "$ROOT/native-vault-provider/tests/NativeVaultLockContentionCorpus.swift" \
  -o "$WORKDIR/corpus"
STATE_ROOT="$WORKDIR/state"
mkdir -m 700 "$STATE_ROOT"
"$WORKDIR/corpus" hold "file://$STATE_ROOT/" >"$WORKDIR/holder.log" 2>&1 &
HOLDER_PID=$!
for _ in {1..50}; do
  rg -q '^READY$' "$WORKDIR/holder.log" && break
  sleep 0.05
done
rg -q '^READY$' "$WORKDIR/holder.log"
"$WORKDIR/corpus" contend "file://$STATE_ROOT/"
wait "$HOLDER_PID"
