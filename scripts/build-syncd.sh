#!/usr/bin/env bash
#
# Build the Rust folder-sync daemon (matrx-syncd) as a Tauri externalBin
# sidecar of the desktop app.
#
# The binary lands at desktop/src-tauri/sidecar/matrx-syncd-<target-triple>[.exe],
# which is the naming rule Tauri requires for `bundle.externalBin` entries
# (https://v2.tauri.app/develop/sidecar/). Without this file the Tauri build
# FAILS — the sidecar is declared in tauri.conf.json on every platform.
#
# Usage:
#   ./scripts/build-syncd.sh
#   ./scripts/build-syncd.sh --target x86_64-apple-darwin
#
# The daemon is NOT spawned by the app (folder-sync spike FS-C1: packaging
# only). It is a cargo workspace member; the target directory comes from
# cargo itself, so this script never hardcodes .cargo/config.toml's choice.
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
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target) OVERRIDE_TARGET="${2:-}"; shift 2 ;;
        *) shift ;;
    esac
done

TARGET_TRIPLE="${OVERRIDE_TARGET:-$(detect_target)}"
case "$TARGET_TRIPLE" in
    unsupported-*) echo "ERROR: unsupported build host: $TARGET_TRIPLE" >&2; exit 1 ;;
esac

# Ask cargo where artifacts go rather than mirroring .cargo/config.toml here.
TARGET_DIR="$(cd "$PROJECT_ROOT" && cargo metadata --format-version 1 --no-deps \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])')"
[[ -n "$TARGET_DIR" ]] || { echo "ERROR: could not resolve the cargo target directory." >&2; exit 1; }

echo "Building matrx-syncd for $TARGET_TRIPLE (target dir: $TARGET_DIR)"
BUILD_ARGS=(build -p matrx-syncd --release)
ARTIFACT_DIR="$TARGET_DIR/release"
if [[ -n "$OVERRIDE_TARGET" ]]; then
    BUILD_ARGS+=(--target "$TARGET_TRIPLE")
    ARTIFACT_DIR="$TARGET_DIR/$TARGET_TRIPLE/release"
fi
(cd "$PROJECT_ROOT" && cargo "${BUILD_ARGS[@]}")

EXE_SUFFIX=""
case "$TARGET_TRIPLE" in *windows*) EXE_SUFFIX=".exe" ;; esac

SRC="$ARTIFACT_DIR/matrx-syncd$EXE_SUFFIX"
[[ -f "$SRC" ]] || { echo "ERROR: built binary not found at $SRC" >&2; exit 1; }

mkdir -p "$SIDECAR_DIR"
DEST="$SIDECAR_DIR/matrx-syncd-$TARGET_TRIPLE$EXE_SUFFIX"
# Idempotent: cargo already no-ops when nothing changed; skip the copy too so
# repeated build/dev runs do not rewrite a 400 KB file for nothing.
if cmp -s "$SRC" "$DEST"; then
    echo "Up to date: $DEST"
else
    cp -f "$SRC" "$DEST"
    chmod +x "$DEST"
fi

echo "OK: $DEST"
"$DEST" --version
