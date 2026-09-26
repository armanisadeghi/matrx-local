#!/usr/bin/env bash
# Build a signed Safari Web Extension containing app for a dedicated Mac.
# This is development/acceptance tooling only: it never installs, launches,
# notarizes, or changes the desktop release workflow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXTENSION_ROOT="${MATRX_EXTEND_DIR:-$REPO_ROOT/../matrx-extend}"
DEVELOPER_ROOT="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
CONVERTER="$DEVELOPER_ROOT/usr/bin/safari-web-extension-converter"
APP_NAME="MatrxExtendSafariDevelopment"
BUNDLE_IDENTIFIER="com.aimatrx.desktop.safari-development"
CHILD_BUNDLE_IDENTIFIER="$BUNDLE_IDENTIFIER.Extension"
OUTPUT_DIR="${MATRX_SAFARI_TEST_APP_OUTPUT_DIR:-$REPO_ROOT/.safari-test-app}"
OUTPUT_APP="$OUTPUT_DIR/$APP_NAME.app"
SIGNING_IDENTITY_OVERRIDE="${MATRX_SAFARI_TEST_APP_SIGNING_IDENTITY:-}"

CHECK_IDENTITY_ONLY=false
case "${1:-}" in
    "") ;;
    --check-signing-identity) CHECK_IDENTITY_ONLY=true ;;
    *)
        echo "usage: $0 [--check-signing-identity]" >&2
        exit 2
        ;;
esac

require() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "ERROR: $1 is required to build the Safari test app." >&2
        exit 1
    }
}

resolve_signing_identity() {
    if [[ -n "$SIGNING_IDENTITY_OVERRIDE" ]]; then
        SIGNING_IDENTITY="$SIGNING_IDENTITY_OVERRIDE"
    else
        SIGNING_IDENTITY="$(security find-identity -v -p codesigning | awk '/Developer ID Application/ { print $2; exit }')"
    fi

    [[ -n "$SIGNING_IDENTITY" ]] || {
        echo "ERROR: no valid Developer ID Application signing identity is installed." >&2
        exit 1
    }
    security find-identity -v -p codesigning | awk -v identity="$SIGNING_IDENTITY" '
        $2 == identity && /Developer ID Application/ { found = 1 }
        END { exit(found ? 0 : 1) }
    ' || {
        echo "ERROR: no valid Developer ID Application signing identity matches the configured identity." >&2
        exit 1
    }
}

invalidate_previous_output() {
    # A failed build must not leave a prior success-looking app available for
    # installation or acceptance. Publishing happens only after every check.
    if [[ -e "$OUTPUT_APP" || -L "$OUTPUT_APP" ]]; then
        rm -rf "$OUTPUT_APP"
    fi
}

# A failed build must never leave a prior app. Identity-only inspection does
# not build or publish, so it intentionally leaves the current artifact alone.
if ! $CHECK_IDENTITY_ONLY; then
    invalidate_previous_output
fi

require git
require pnpm
require xcodebuild
require plutil
require codesign
require spctl
require security

[[ "$(uname -s)" == "Darwin" ]] || {
    echo "ERROR: Safari test app builds require macOS." >&2
    exit 1
}
[[ -x "$CONVERTER" ]] || {
    echo "ERROR: Safari converter not found at $CONVERTER. Set DEVELOPER_DIR to an Xcode Developer directory." >&2
    exit 1
}

resolve_signing_identity
if $CHECK_IDENTITY_ONLY; then
    echo "Developer ID Application signing identity is available."
    exit 0
fi

[[ -d "$EXTENSION_ROOT/.git" ]] || {
    echo "ERROR: sibling matrx-extend checkout not found at $EXTENSION_ROOT." >&2
    exit 1
}

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/matrx-safari-test-app.XXXXXX")"
cleanup() {
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

SOURCE_ROOT="$WORKDIR/matrx-extend"
PROJECT_ROOT="$WORKDIR/xcode-project"
BUILD_ROOT="$WORKDIR/xcode-build"

# Build only tracked sibling source. This excludes stale .output files, local
# credentials, and concurrent uncommitted changes from the build input.
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

echo "Converting fresh Safari source into an Xcode containing app..."
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
PBX_PROJECT="$XCODE_PROJECT/project.pbxproj"
[[ -f "$PBX_PROJECT" ]] || {
    echo "ERROR: converter did not produce expected Xcode project at $XCODE_PROJECT." >&2
    exit 1
}

# Safari's converter derives a parent identifier from the app name. The parent
# must instead be the exact prefix of the generated child identifier.
GENERATED_PARENT_BUNDLE_IDENTIFIER="com.aimatrx.desktop.$APP_NAME"
PARENT_IDENTIFIER_MATCHES="$(grep -F "PRODUCT_BUNDLE_IDENTIFIER = $GENERATED_PARENT_BUNDLE_IDENTIFIER;" "$PBX_PROJECT" | wc -l | tr -d ' ')"
[[ "$PARENT_IDENTIFIER_MATCHES" == "2" ]] || {
    echo "ERROR: converter parent bundle identifier shape changed; expected two $GENERATED_PARENT_BUNDLE_IDENTIFIER settings, found $PARENT_IDENTIFIER_MATCHES." >&2
    exit 1
}
perl -pi -e "s/\Q$GENERATED_PARENT_BUNDLE_IDENTIFIER\E/$BUNDLE_IDENTIFIER/g" "$PBX_PROJECT"

echo "Building the complete unsigned Safari containing-app scheme..."
DEVELOPER_DIR="$DEVELOPER_ROOT" xcodebuild \
    -project "$XCODE_PROJECT" \
    -scheme "$APP_NAME" \
    -configuration Release \
    -sdk macosx \
    "SYMROOT=$BUILD_ROOT" \
    CODE_SIGNING_ALLOWED=NO \
    build

APP_PATH="$BUILD_ROOT/Release/$APP_NAME.app"
APPEX_PATH="$APP_PATH/Contents/PlugIns/$APP_NAME Extension.appex"
[[ -d "$APP_PATH" && -d "$APPEX_PATH" ]] || {
    echo "ERROR: full scheme did not produce containing app and nested extension." >&2
    exit 1
}
PARENT_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP_PATH/Contents/Info.plist")"
CHILD_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APPEX_PATH/Contents/Info.plist")"
[[ "$PARENT_ID" == "$BUNDLE_IDENTIFIER" ]] || {
    echo "ERROR: containing app bundle identifier must be $BUNDLE_IDENTIFIER; got $PARENT_ID." >&2
    exit 1
}
[[ "$CHILD_ID" == "$CHILD_BUNDLE_IDENTIFIER" ]] || {
    echo "ERROR: extension bundle identifier must be $CHILD_BUNDLE_IDENTIFIER; got $CHILD_ID." >&2
    exit 1
}

echo "Signing nested extension and containing app with Developer ID..."
codesign --force --options runtime --timestamp --sign "$SIGNING_IDENTITY" "$APPEX_PATH"
codesign --force --options runtime --timestamp --sign "$SIGNING_IDENTITY" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
spctl --assess --type execute --verbose=4 "$APP_PATH"

mkdir -p "$OUTPUT_DIR"
PUBLISH_STAGING="$OUTPUT_DIR/.${APP_NAME}.app.$$"
rm -rf "$PUBLISH_STAGING"
cp -R "$APP_PATH" "$PUBLISH_STAGING"
mv "$PUBLISH_STAGING" "$OUTPUT_APP"

echo "Built signed Safari test app: $OUTPUT_APP"
echo "Parent bundle identifier: $PARENT_ID"
echo "Extension bundle identifier: $CHILD_ID"
