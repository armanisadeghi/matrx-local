#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
for ARCH in arm64 x86_64; do
xcrun swiftc -parse-as-library -target "$ARCH-apple-macosx26.0" \
  "$ROOT/native-vault-provider/NativeVaultImportJournal.swift" \
  "$ROOT/native-vault-provider/NativeVaultImport.swift" \
  "$ROOT/native-vault-provider/tests/NativeVaultImportCorpus.swift" \
  -o "$WORK/corpus-$ARCH"
done
"$WORK/corpus-$(uname -m)"
if xcrun swiftc -typecheck -target arm64-apple-macosx26.0 \
  "$ROOT/native-vault-provider/NativeVaultImportJournal.swift" \
  "$ROOT/native-vault-provider/NativeVaultImport.swift" \
  "$ROOT/native-vault-provider/tests/NativeVaultImportClosedReasonNegative.swift"; then
  echo "arbitrary native import failure reason compiled" >&2
  exit 1
fi
