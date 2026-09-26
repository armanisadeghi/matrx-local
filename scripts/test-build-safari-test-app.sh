#!/usr/bin/env bash
# Proves test-app identity and source-preflight failures cannot leave a stale
# containing app that looks safe to install on a dedicated Mac.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDER="$SCRIPT_DIR/build-safari-test-app.sh"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/matrx-safari-test-app-probe.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT
APP_NAME="MatrxExtendSafariDevelopment"
BUNDLE_IDENTIFIER="com.aimatrx.desktop.safari-development"
CHILD_BUNDLE_IDENTIFIER="$BUNDLE_IDENTIFIER.Extension"
REAL_CODESIGN="$(command -v codesign)"

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

assert_published_app_is_accepted() {
    local output_dir="$1"
    local app="$output_dir/$APP_NAME.app"
    local appex="$app/Contents/PlugIns/$APP_NAME Extension.appex"
    local parent_id
    local child_id

    [[ -d "$app" && -d "$appex" ]] || {
        echo "ERROR: successful build did not publish the containing app and extension." >&2
        exit 1
    }

    parent_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist")"
    child_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$appex/Contents/Info.plist")"
    [[ "$parent_id" == "$BUNDLE_IDENTIFIER" ]] || {
        echo "ERROR: published parent identifier was $parent_id, expected $BUNDLE_IDENTIFIER." >&2
        exit 1
    }
    [[ "$child_id" == "$CHILD_BUNDLE_IDENTIFIER" ]] || {
        echo "ERROR: published extension identifier was $child_id, expected $CHILD_BUNDLE_IDENTIFIER." >&2
        exit 1
    }

    "$REAL_CODESIGN" --verify --deep --strict --verbose=2 "$app"
    spctl --assess --type execute --verbose=4 "$app"
}

make_late_codesign_failure() {
    local bin_dir="$WORKDIR/late-codesign-bin"
    local attempt_file="$WORKDIR/late-codesign-attempt"
    mkdir -p "$bin_dir"
    cat >"$bin_dir/codesign" <<EOF
#!/usr/bin/env bash
set -euo pipefail

for argument in "\$@"; do
    if [[ "\$argument" == "--sign" ]]; then
        attempt=0
        [[ -f "$attempt_file" ]] && attempt="\$(<"$attempt_file")"
        if (( attempt >= 1 )); then
            echo "forced late codesign failure" >&2
            exit 70
        fi
        printf '%s\\n' "\$((attempt + 1))" >"$attempt_file"
        break
    fi
done

exec "$REAL_CODESIGN" "\$@"
EOF
    chmod 755 "$bin_dir/codesign"
    printf '%s' "$bin_dir"
}

"$BUILDER" --check-signing-identity

SUCCESS_OUTPUT_DIR="$WORKDIR/success-output"
MATRX_SAFARI_TEST_APP_OUTPUT_DIR="$SUCCESS_OUTPUT_DIR" "$BUILDER"
assert_published_app_is_accepted "$SUCCESS_OUTPUT_DIR"

expect_failure_without_output invalid-identity \
    env MATRX_SAFARI_TEST_APP_SIGNING_IDENTITY=0000000000000000000000000000000000000000 \
    "$BUILDER"
grep -Fq "no valid Developer ID Application signing identity matches" "$WORKDIR/invalid-identity.log"

expect_failure_without_output missing-source \
    env MATRX_EXTEND_DIR="$WORKDIR/missing-matrx-extend" \
    "$BUILDER"
grep -Fq "sibling matrx-extend checkout not found" "$WORKDIR/missing-source.log"

LATE_CODESIGN_BIN="$(make_late_codesign_failure)"
expect_failure_without_output late-signing \
    env "PATH=$LATE_CODESIGN_BIN:$PATH" \
    "$BUILDER"
grep -Fq "forced late codesign failure" "$WORKDIR/late-signing.log"

echo "Safari test-app full-build and failure-path probes passed."
