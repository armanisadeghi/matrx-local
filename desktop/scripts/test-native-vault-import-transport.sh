#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
SOURCES=()
for file in NativeVaultCodec NativeVaultEnrollmentLifecycle NativeVaultState NativeVaultPrivateSession NativeVaultTransport NativeVaultSessionAccess NativeVaultPasskeyCodec NativeVaultIdentity NativeVaultImportJournal NativeVaultImport NativeVaultImportTransport; do
  SOURCES+=("$ROOT/native-vault-provider/$file.swift")
done
for ARCH in arm64 x86_64; do
  xcrun swiftc -parse-as-library -target "$ARCH-apple-macosx26.0" "${SOURCES[@]}" \
    "$ROOT/native-vault-provider/tests/NativeVaultImportTransportCorpus.swift" -o "$WORK/corpus-$ARCH"
done
"$WORK/corpus-$(uname -m)"
