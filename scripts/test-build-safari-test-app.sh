#!/usr/bin/env bash
# Proves test-app identity and source-preflight failures cannot leave a stale
# containing app that looks safe to install on a dedicated Mac.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDER="$SCRIPT_DIR/build-safari-test-app.sh"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/matrx-safari-test-app-probe.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

make_stale_app() {
    local output_dir="$1"
    local stale_app="$output_dir/MatrxExtendSafariDevelopment.app"
    mkdir -p "$stale_app"
    touch "$stale_app/stale-marker"
}

expect_failure_without_output() {
    local name="$1"
    shift
    local output_dir="$WORKDIR/$name-output"
    local stale_app="$output_dir/MatrxExtendSafariDevelopment.app"
    make_stale_app "$output_dir"

    if MATRX_SAFARI_TEST_APP_OUTPUT_DIR="$output_dir" "$@" >"$WORKDIR/$name.log" 2>&1; then
        echo "ERROR: $name probe unexpectedly succeeded." >&2
        exit 1
    fi
    [[ ! -e "$stale_app" ]] || {
        echo "ERROR: $name probe left stale test app at $stale_app." >&2
        exit 1
    }
}

"$BUILDER" --check-signing-identity

expect_failure_without_output invalid-identity \
    env MATRX_SAFARI_TEST_APP_SIGNING_IDENTITY=0000000000000000000000000000000000000000 \
    "$BUILDER"
grep -Fq "no valid Developer ID Application signing identity matches" "$WORKDIR/invalid-identity.log"

expect_failure_without_output missing-source \
    env MATRX_EXTEND_DIR="$WORKDIR/missing-matrx-extend" \
    "$BUILDER"
grep -Fq "sibling matrx-extend checkout not found" "$WORKDIR/missing-source.log"

echo "Safari test-app identity and failure-path probes passed."
