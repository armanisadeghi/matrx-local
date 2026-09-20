#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
xcrun swiftc -parse-as-library "$ROOT/native-vault-provider/NativeVaultImportFile.swift" "$ROOT/native-vault-provider/tests/NativeVaultImportFileCorpus.swift" -o "$WORK/corpus"
"$WORK/corpus"
