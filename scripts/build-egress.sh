#!/usr/bin/env bash
#
# Build the AI Matrx Home Connection helper (matrx-egress).
#
# Two products from one build, which is the point of contract rule 6 ("One helper
# implementation"):
#
#   ./scripts/build-egress.sh                     the standalone helper binary
#   ./scripts/build-egress.sh --sidecar           …also copied to
#                                                 desktop/src-tauri/sidecar/matrx-egress-<triple>[.exe]
#   ./scripts/build-egress.sh --target <triple>   cross/explicit target (sets the artifact dir)
#   ./scripts/build-egress.sh --debug             a debug build, for local runs
#
# The sidecar name is the naming rule Tauri requires for `bundle.externalBin`
# (https://v2.tauri.app/develop/sidecar/) — the same convention scripts/build-syncd.sh
# follows. This script never declares the sidecar in tauri.conf.json: wiring the
# desktop app is a separate change with a separate owner.
#
# The crate is a cargo workspace member, so the target directory comes from cargo
# itself and this script never mirrors .cargo/config.toml's choice.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SIDECAR_DIR="$PROJECT_ROOT/desktop/src-tauri/sidecar"

command -v cargo >/dev/null 2>&1 || { echo "ERROR: cargo is not installed." >&2; exit 1; }

detect_target() {
    local os arch
    os="$(uname -s)"; arch="$(uname -m)"
    case "$os" in
        Linux)  case "$arch" in
                    x86_64)  echo "x86_64-unknown-linux-gnu" ;;
                    aarch64) echo "aarch64-unknown-linux-gnu" ;;
                    *) echo "unsupported-linux-$arch" ;;
                esac ;;
        Darwin) case "$arch" in
                    x86_64) echo "x86_64-apple-darwin" ;;
                    arm64)  echo "aarch64-apple-darwin" ;;
                    *) echo "unsupported-darwin-$arch" ;;
                esac ;;
        MINGW*|MSYS*|CYGWIN*) echo "x86_64-pc-windows-msvc" ;;
        *) echo "unsupported-$os" ;;
    esac
}

OVERRIDE_TARGET=""
SIDECAR=0
PROFILE="release"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target) OVERRIDE_TARGET="${2:-}"; shift 2 ;;
        --sidecar) SIDECAR=1; shift ;;
        --debug) PROFILE="debug"; shift ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "ERROR: unknown argument $1" >&2; exit 2 ;;
    esac
done

TARGET_TRIPLE="${OVERRIDE_TARGET:-$(detect_target)}"
case "$TARGET_TRIPLE" in
    unsupported-*) echo "ERROR: unsupported build host: $TARGET_TRIPLE" >&2; exit 1 ;;
esac

TARGET_DIR="$(cd "$PROJECT_ROOT" && cargo metadata --format-version 1 --no-deps \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])')"
[[ -n "$TARGET_DIR" ]] || { echo "ERROR: could not resolve the cargo target directory." >&2; exit 1; }

echo "Building matrx-egress ($PROFILE) for $TARGET_TRIPLE (target dir: $TARGET_DIR)"
BUILD_ARGS=(build -p matrx-egress)
[[ "$PROFILE" == "release" ]] && BUILD_ARGS+=(--release)
ARTIFACT_DIR="$TARGET_DIR/$PROFILE"
if [[ -n "$OVERRIDE_TARGET" ]]; then
    BUILD_ARGS+=(--target "$TARGET_TRIPLE")
    ARTIFACT_DIR="$TARGET_DIR/$TARGET_TRIPLE/$PROFILE"
fi
(cd "$PROJECT_ROOT" && cargo "${BUILD_ARGS[@]}")

EXE_SUFFIX=""
case "$TARGET_TRIPLE" in *windows*) EXE_SUFFIX=".exe" ;; esac

SRC="$ARTIFACT_DIR/matrx-egress$EXE_SUFFIX"
[[ -f "$SRC" ]] || { echo "ERROR: built binary not found at $SRC" >&2; exit 1; }
echo "OK: $SRC"
"$SRC" --version

if [[ "$SIDECAR" -eq 1 ]]; then
    mkdir -p "$SIDECAR_DIR"
    DEST="$SIDECAR_DIR/matrx-egress-$TARGET_TRIPLE$EXE_SUFFIX"
    # Idempotent: cargo already no-ops when nothing changed, so skip the copy too rather than
    # rewriting the file on every dev run.
    if cmp -s "$SRC" "$DEST"; then
        echo "Up to date: $DEST"
    else
        cp -f "$SRC" "$DEST"
        chmod +x "$DEST"
        echo "OK: $DEST"
    fi
fi
