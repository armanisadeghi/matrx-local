#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="$(mktemp -d)"; trap 'rm -rf "$WORKDIR"' EXIT
SWIFTC="$(xcrun --find swiftc)"; SDK="$(xcrun --sdk macosx --show-sdk-path)"
"$SWIFTC" -parse-as-library -sdk "$SDK" -target "$(uname -m)-apple-macosx15.0" \
  "$ROOT/native-vault-provider/NativeVaultCodec.swift" \
  "$ROOT/native-vault-provider/NativeVaultEnrollmentLifecycle.swift" \
  "$ROOT/native-vault-provider/NativeVaultState.swift" \
  "$ROOT/native-vault-provider/NativeVaultPrivateSession.swift" \
  "$ROOT/native-vault-provider/NativeVaultPassword.swift" \
  "$ROOT/native-vault-provider/CredentialProviderViewController.swift" \
  "$ROOT/native-vault-provider/tests/NativeVaultPasswordCorpus.swift" \
  -o "$WORKDIR/corpus"
"$WORKDIR/corpus"
