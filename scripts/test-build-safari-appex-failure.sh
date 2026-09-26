#!/usr/bin/env bash
# Proves a failed Safari development build cannot leave a stale .appex that
# looks usable to an acceptance tester.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDER="$SCRIPT_DIR/build-safari-appex.sh"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/matrx-safari-appex-failure.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

OUTPUT_DIR="$WORKDIR/output"
STALE_APPEX="$OUTPUT_DIR/MatrxExtendSafariDevelopment.appex"
mkdir -p "$STALE_APPEX"
touch "$STALE_APPEX/stale-marker"

if MATRX_EXTEND_DIR="$WORKDIR/missing-matrx-extend" \
    MATRX_SAFARI_APPEX_OUTPUT_DIR="$OUTPUT_DIR" \
    "$BUILDER" >"$WORKDIR/build.log" 2>&1; then
    echo "ERROR: expected missing sibling source checkout to fail." >&2
    exit 1
fi

[[ ! -e "$STALE_APPEX" ]] || {
    echo "ERROR: failed build left stale .appex at $STALE_APPEX." >&2
    exit 1
}
grep -Fq "sibling matrx-extend checkout not found" "$WORKDIR/build.log" || {
    echo "ERROR: missing-source probe failed for an unexpected reason." >&2
    exit 1
}

echo "Safari .appex failure-path probe passed."
