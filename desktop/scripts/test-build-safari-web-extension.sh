#!/usr/bin/env bash
# Forces the pinned public-source production Safari extension build and proves
# invalid signing identity failures cannot leave a stale staged bundle.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_ROOT/.." && pwd)"
BUILDER="$SCRIPT_DIR/build-safari-web-extension.sh"
VERIFIER="$REPO_ROOT/scripts/verify-safari-web-extension.sh"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/matrx-safari-production-test.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

IDENTITY="$(security find-identity -v -p codesigning | awk '/Developer ID Application/ { print $2; exit }')"
[[ -n "$IDENTITY" ]] || { echo "ERROR: a local Developer ID Application identity is required for this test." >&2; exit 1; }

SUCCESS_OUTPUT="$WORKDIR/success"
MATRX_SAFARI_EXTENSION_SIGNING_IDENTITY="$IDENTITY" \
MATRX_SAFARI_WEB_EXTENSION_OUTPUT_DIR="$SUCCESS_OUTPUT" \
"$BUILDER"
"$VERIFIER" "$SUCCESS_OUTPUT/Matrx Extend Safari.appex"

FAILURE_OUTPUT="$WORKDIR/invalid-identity"
STALE_APPEX="$FAILURE_OUTPUT/Matrx Extend Safari.appex"
mkdir -p "$STALE_APPEX"
touch "$STALE_APPEX/stale-marker"
if MATRX_SAFARI_EXTENSION_SIGNING_IDENTITY=0000000000000000000000000000000000000000 \
    MATRX_SAFARI_WEB_EXTENSION_OUTPUT_DIR="$FAILURE_OUTPUT" \
    "$BUILDER" >"$WORKDIR/invalid-identity.log" 2>&1; then
    echo "ERROR: invalid signing identity unexpectedly succeeded." >&2
    exit 1
fi
[[ ! -e "$STALE_APPEX" ]] || { echo "ERROR: invalid signing identity left a stale Safari extension bundle." >&2; exit 1; }
grep -Fq "not a valid installed Developer ID Application identity" "$WORKDIR/invalid-identity.log"

echo "Safari production extension full-build and identity-failure probes passed."
