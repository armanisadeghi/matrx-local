#!/usr/bin/env bash
# Build an unsigned Safari Web Extension .appex for local development and
# acceptance checks. This intentionally does not build, sign, or package the
# desktop app.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXTENSION_ROOT="${MATRX_EXTEND_DIR:-$REPO_ROOT/../matrx-extend}"
DEVELOPER_ROOT="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
CONVERTER="$DEVELOPER_ROOT/usr/bin/safari-web-extension-converter"
APP_NAME="MatrxExtendSafariDevelopment"
BUNDLE_IDENTIFIER="com.aimatrx.desktop.safari-development"
OUTPUT_DIR="${MATRX_SAFARI_APPEX_OUTPUT_DIR:-$REPO_ROOT/.safari-appex}"
OUTPUT_APPEX="$OUTPUT_DIR/$APP_NAME.appex"

require() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "ERROR: $1 is required to build the Safari development extension." >&2
        exit 1
    }
}

require git
require pnpm
require xcodebuild
require plutil

[[ "$(uname -s)" == "Darwin" ]] || {
    echo "ERROR: Safari development extension builds require macOS." >&2
    exit 1
}
[[ -d "$EXTENSION_ROOT/.git" ]] || {
    echo "ERROR: sibling matrx-extend checkout not found at $EXTENSION_ROOT." >&2
    exit 1
}
[[ -x "$CONVERTER" ]] || {
    echo "ERROR: Safari converter not found at $CONVERTER. Set DEVELOPER_DIR to an Xcode Developer directory." >&2
    exit 1
}

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/matrx-safari-appex.XXXXXX")"
cleanup() {
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

SOURCE_ROOT="$WORKDIR/matrx-extend"
PROJECT_ROOT="$WORKDIR/xcode-project"
BUILD_ROOT="$WORKDIR/xcode-build"

# Build only tracked source at sibling HEAD. This excludes stale .output files,
# local credentials, and concurrent uncommitted changes from the input.
mkdir -p "$SOURCE_ROOT"
git -C "$EXTENSION_ROOT" archive --format=tar HEAD | tar -xf - -C "$SOURCE_ROOT"

echo "Installing locked Safari source dependencies..."
(
    cd "$SOURCE_ROOT"
    pnpm install --frozen-lockfile --ignore-scripts
)

echo "Building fresh Safari Web Extension source..."
(
    cd "$SOURCE_ROOT"
    pnpm exec wxt build --browser safari
)

RAW_EXTENSION="$SOURCE_ROOT/.output/safari-mv2"
[[ -f "$RAW_EXTENSION/manifest.json" ]] || {
    echo "ERROR: WXT did not produce Safari manifest at $RAW_EXTENSION/manifest.json." >&2
    exit 1
}

echo "Converting fresh Safari source into an Xcode extension target..."
DEVELOPER_DIR="$DEVELOPER_ROOT" xcrun safari-web-extension-converter "$RAW_EXTENSION" \
    --project-location "$PROJECT_ROOT" \
    --app-name "$APP_NAME" \
    --bundle-identifier "$BUNDLE_IDENTIFIER" \
    --macos-only \
    --copy-resources \
    --no-open \
    --no-prompt \
    --force

XCODE_PROJECT="$PROJECT_ROOT/$APP_NAME/$APP_NAME.xcodeproj"
[[ -d "$XCODE_PROJECT" ]] || {
    echo "ERROR: converter did not produce expected Xcode project at $XCODE_PROJECT." >&2
    exit 1
}

echo "Building only the unsigned extension target..."
DEVELOPER_DIR="$DEVELOPER_ROOT" xcodebuild \
    -project "$XCODE_PROJECT" \
    -target "$APP_NAME Extension" \
    -configuration Release \
    -sdk macosx \
    "SYMROOT=$BUILD_ROOT" \
    CODE_SIGNING_ALLOWED=NO \
    build

APPEX_PATH="$BUILD_ROOT/Release/$APP_NAME Extension.appex"
[[ -d "$APPEX_PATH" ]] || {
    echo "ERROR: xcodebuild did not produce expected extension at $APPEX_PATH." >&2
    exit 1
}

PLIST_PATH="$APPEX_PATH/Contents/Info.plist"
[[ -f "$PLIST_PATH" ]] || {
    echo "ERROR: extension is missing Info.plist at $PLIST_PATH." >&2
    exit 1
}
BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PLIST_PATH")"
[[ "$BUNDLE_ID" == com.aimatrx.desktop* ]] || {
    echo "ERROR: extension bundle identifier must begin with com.aimatrx.desktop; got $BUNDLE_ID." >&2
    exit 1
}

mkdir -p "$OUTPUT_DIR"
rm -rf "$OUTPUT_APPEX"
cp -R "$APPEX_PATH" "$OUTPUT_APPEX"

echo "Built unsigned Safari development extension: $OUTPUT_APPEX"
echo "Bundle identifier: $BUNDLE_ID"
