#!/usr/bin/env bash
# Build the production Safari Web Extension from one immutable public source
# revision, sign it independently, and stage it for Tauri's release overlay.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_ROOT/.." && pwd)"
DEVELOPER_ROOT="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
CONVERTER="$DEVELOPER_ROOT/usr/bin/safari-web-extension-converter"
source "$SCRIPT_DIR/safari-web-extension-source.sh"
SOURCE_REPOSITORY="$MATRX_SAFARI_EXTENSION_SOURCE_REPOSITORY"
SOURCE_REVISION="$MATRX_SAFARI_EXTENSION_SOURCE_REVISION"
APP_NAME="MatrxExtendSafari"
BUNDLE_IDENTIFIER="com.aimatrx.desktop.safari"
CHILD_BUNDLE_IDENTIFIER="$BUNDLE_IDENTIFIER.Extension"
OUTPUT_DIR="${MATRX_SAFARI_WEB_EXTENSION_OUTPUT_DIR:-$DESKTOP_ROOT/safari-web-extension/build}"
OUTPUT_APPEX="$OUTPUT_DIR/Matrx Extend Safari.appex"
SIGNING_IDENTITY="${MATRX_SAFARI_EXTENSION_SIGNING_IDENTITY:-${APPLE_SIGNING_IDENTITY:-}}"

require() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "ERROR: $1 is required to build the production Safari Web Extension." >&2
        exit 1
    }
}

resolve_signing_identity() {
    [[ -n "$SIGNING_IDENTITY" ]] || {
        echo "ERROR: MATRX_SAFARI_EXTENSION_SIGNING_IDENTITY is required for production Safari extension signing." >&2
        exit 1
    }
    security find-identity -v -p codesigning | grep -F "$SIGNING_IDENTITY" | grep -F "Developer ID Application" >/dev/null || {
        echo "ERROR: configured Safari extension signing identity is not a valid installed Developer ID Application identity." >&2
        exit 1
    }
}

invalidate_previous_output() {
    if [[ -e "$OUTPUT_APPEX" || -L "$OUTPUT_APPEX" ]]; then
        rm -rf "$OUTPUT_APPEX"
    fi
}

# Clear the canonical staging path before every possible build failure.
invalidate_previous_output

require git
require pnpm
require xcodebuild
require codesign
require security
require plutil

[[ "$(uname -s)" == "Darwin" ]] || { echo "ERROR: production Safari extension builds require macOS." >&2; exit 1; }
[[ -x "$CONVERTER" ]] || { echo "ERROR: Safari converter not found at $CONVERTER. Set DEVELOPER_DIR to a full Xcode Developer directory." >&2; exit 1; }

resolve_signing_identity

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/matrx-safari-production.XXXXXX")"
cleanup() {
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

SOURCE_ROOT="$WORKDIR/matrx-extend"
PROJECT_ROOT="$WORKDIR/xcode-project"
BUILD_ROOT="$WORKDIR/xcode-build"

echo "Fetching pinned public Matrx Extend source $SOURCE_REVISION..."
git init -q "$SOURCE_ROOT"
git -C "$SOURCE_ROOT" remote add origin "$SOURCE_REPOSITORY"
git -C "$SOURCE_ROOT" fetch --depth 1 origin "$SOURCE_REVISION"
[[ "$(git -C "$SOURCE_ROOT" rev-parse FETCH_HEAD)" == "$SOURCE_REVISION" ]] || { echo "ERROR: fetched Matrx Extend revision does not match $SOURCE_REVISION." >&2; exit 1; }
git -C "$SOURCE_ROOT" checkout -q --detach "$SOURCE_REVISION"
[[ "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" == "$SOURCE_REVISION" ]] || { echo "ERROR: checked-out Matrx Extend revision does not match $SOURCE_REVISION." >&2; exit 1; }

echo "Installing locked Safari extension dependencies..."
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
[[ -f "$RAW_EXTENSION/manifest.json" ]] || { echo "ERROR: WXT did not produce Safari manifest at $RAW_EXTENSION/manifest.json." >&2; exit 1; }

echo "Converting pinned Safari source into an extension target..."
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
[[ -d "$XCODE_PROJECT" ]] || { echo "ERROR: converter did not produce expected Xcode project at $XCODE_PROJECT." >&2; exit 1; }

echo "Building only the unsigned Safari extension target..."
DEVELOPER_DIR="$DEVELOPER_ROOT" xcodebuild \
    -project "$XCODE_PROJECT" \
    -target "$APP_NAME Extension" \
    -configuration Release \
    -sdk macosx \
    "SYMROOT=$BUILD_ROOT" \
    CODE_SIGNING_ALLOWED=NO \
    build

APPEX_PATH="$BUILD_ROOT/Release/$APP_NAME Extension.appex"
[[ -d "$APPEX_PATH" ]] || { echo "ERROR: Xcode did not produce expected extension at $APPEX_PATH." >&2; exit 1; }
INFO_PLIST="$APPEX_PATH/Contents/Info.plist"
[[ -f "$INFO_PLIST" ]] || { echo "ERROR: Safari extension Info.plist is missing." >&2; exit 1; }
[[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$INFO_PLIST")" == "$CHILD_BUNDLE_IDENTIFIER" ]] || { echo "ERROR: Safari extension bundle identifier must be $CHILD_BUNDLE_IDENTIFIER." >&2; exit 1; }
/usr/libexec/PlistBuddy -c "Add :MatrxSafariExtensionSourceRepository string $SOURCE_REPOSITORY" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :MatrxSafariExtensionSourceRevision string $SOURCE_REVISION" "$INFO_PLIST"
plutil -lint "$INFO_PLIST" >/dev/null

echo "Signing production Safari extension with Developer ID..."
codesign --force --timestamp --options runtime --sign "$SIGNING_IDENTITY" "$APPEX_PATH"
"$REPO_ROOT/scripts/verify-safari-web-extension.sh" "$APPEX_PATH"

mkdir -p "$OUTPUT_DIR"
PUBLISH_STAGING="$OUTPUT_DIR/.Matrx Extend Safari.appex.$$"
rm -rf "$PUBLISH_STAGING"
cp -R "$APPEX_PATH" "$PUBLISH_STAGING"
mv "$PUBLISH_STAGING" "$OUTPUT_APPEX"

echo "Built signed production Safari extension: $OUTPUT_APPEX"
