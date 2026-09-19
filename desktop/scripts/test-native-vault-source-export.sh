#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
core="$root/native-vault-provider/core"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

# The harness writes a deterministic synthetic fixture only to this private
# temporary directory and prints neither key nor DER. OpenSSL independently
# parses the RFC 5958 document and derives the public SPKI for byte comparison.
(cd "$core" && cargo run --quiet --features source-export-test-harness --bin source-export-harness -- "$scratch/key.der" "$scratch/expected-public.der")
openssl pkey -inform DER -in "$scratch/key.der" -pubout -outform DER -out "$scratch/actual-public.der"
cmp "$scratch/expected-public.der" "$scratch/actual-public.der"
