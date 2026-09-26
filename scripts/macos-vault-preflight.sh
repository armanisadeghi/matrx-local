#!/usr/bin/env bash
# Read-only, secret-free preflight for a supplied packaged AI Matrx app.
# It reports package identity, signing, Gatekeeper, and the narrow PluginKit
# registration records for credential-provider and Safari Web Extension bundles
# embedded in that app. It never reads Vault data or changes macOS settings.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/macos-vault-preflight.sh --app /path/to/AI\ Matrx.app
       scripts/macos-vault-preflight.sh --self-test

The command is read-only. It checks only the supplied app and the PluginKit
records for extension identifiers declared inside that app.
EOF
}

fail() {
    echo "ERROR: $*" >&2
    exit 2
}

plist_value() {
    /usr/libexec/PlistBuddy -c "Print :$2" "$1" 2>/dev/null
}

extension_identities() {
    local app_path="$1"
    local plugins="$app_path/Contents/PlugIns"
    local appex info identifier point

    [[ -d "$plugins" ]] || return 0
    while IFS= read -r -d '' appex; do
        info="$appex/Contents/Info.plist"
        [[ -f "$info" ]] || continue
        identifier="$(plist_value "$info" CFBundleIdentifier || true)"
        point="$(plist_value "$info" 'NSExtension:NSExtensionPointIdentifier' || true)"
        [[ -n "$identifier" && -n "$point" ]] || continue
        case "$point" in
            com.apple.authentication-services-credential-provider-ui)
                printf 'credential_provider|%s|%s\n' "$identifier" "$point"
                ;;
            com.apple.Safari.web-extension)
                printf 'safari_web_extension|%s|%s\n' "$identifier" "$point"
                ;;
        esac
    done < <(/usr/bin/find "$plugins" -type d -name '*.appex' -print0)
}

plugin_registration() {
    local identifier="$1"
    local point="$2"
    local output line

    if [[ ! -x /usr/bin/pluginkit ]]; then
        printf 'unknown'
        return 0
    fi
    if ! output="$(/usr/bin/pluginkit -m -p "$point" -i "$identifier" 2>/dev/null)"; then
        printf 'unknown'
        return 0
    fi
    line="$(printf '%s\n' "$output" | /usr/bin/grep -F "$identifier" | /usr/bin/head -n 1 || true)"
    if [[ -z "$line" ]]; then
        printf 'absent'
    else
        plugin_election_status "$line"
    fi
}

plugin_election_status() {
    case "$1" in
        +*) printf 'registered' ;;
        -*) printf 'not_enabled' ;;
        \?*|=*) printf 'unknown' ;;
        *) printf 'unknown' ;;
    esac
}

check_signature() {
    local app_path="$1"
    if ! command -v codesign >/dev/null 2>&1; then
        printf 'unknown'
    elif codesign --verify --deep --strict --verbose=2 "$app_path" >/dev/null 2>&1; then
        printf 'valid'
    else
        printf 'invalid'
    fi
}

check_gatekeeper() {
    local app_path="$1"
    local result
    local status
    if ! command -v spctl >/dev/null 2>&1; then
        printf 'unknown'
    elif result="$(spctl --no-cache --assess --type execute --verbose=4 "$app_path" 2>&1)"; then
        printf 'accepted'
    else
        status=$?
        if [[ "$status" -eq 3 ]]; then
            printf 'rejected'
        else
            printf 'unknown'
        fi
    fi
}

run_self_test() {
    local workdir app info identities expected
    workdir="$(mktemp -d "${TMPDIR:-/tmp}/matrx-macos-preflight.XXXXXX")"
    trap "rm -rf '$workdir'" EXIT
    app="$workdir/Fixture.app"
    mkdir -p "$app/Contents/PlugIns/Credential.appex/Contents"
    info="$app/Contents/PlugIns/Credential.appex/Contents/Info.plist"
    cp "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/desktop/native-vault-provider/Info.plist" "$info"
    mkdir -p "$app/Contents/PlugIns/Safari.appex/Contents"
    cp "$info" "$app/Contents/PlugIns/Safari.appex/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier com.aimatrx.desktop.safari.fixture' "$app/Contents/PlugIns/Safari.appex/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c 'Set :NSExtension:NSExtensionPointIdentifier com.apple.Safari.web-extension' "$app/Contents/PlugIns/Safari.appex/Contents/Info.plist"
    identities="$(extension_identities "$app")"
    expected=$'credential_provider|com.aimatrx.desktop.vault-provider|com.apple.authentication-services-credential-provider-ui\nsafari_web_extension|com.aimatrx.desktop.safari.fixture|com.apple.Safari.web-extension'
    [[ "$identities" == "$expected" ]] || fail "self-test did not classify fixture extension identities"
    mkdir -p "$workdir/Empty.app/Contents"
    [[ -z "$(extension_identities "$workdir/Empty.app")" ]] || fail "self-test did not reject an app with no supported extensions"
    [[ "$(plugin_election_status '+ registered')" == registered ]] || fail "self-test did not classify a registered PluginKit election"
    [[ "$(plugin_election_status '- disabled')" == not_enabled ]] || fail "self-test did not classify a disabled PluginKit election"
    [[ "$(plugin_election_status '? unknown')" == unknown ]] || fail "self-test did not preserve an unknown PluginKit election"
    [[ "$(plugin_election_status '= superseded')" == unknown ]] || fail "self-test did not preserve a superseded PluginKit election"
    echo "Self-test passed: extension identities and PluginKit registered/not-enabled/unknown states are classified; absent extensions stay absent."
}

APP_PATH=""
SELF_TEST=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --app)
            [[ $# -ge 2 ]] || fail "--app requires a path"
            APP_PATH="$2"
            shift 2
            ;;
        --self-test)
            SELF_TEST=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "unknown option: $1"
            ;;
    esac
done

[[ "$(uname -s)" == Darwin ]] || fail "macOS is required"
if [[ "$SELF_TEST" == true ]]; then
    [[ -z "$APP_PATH" ]] || fail "--self-test does not take --app"
    run_self_test
    exit 0
fi
[[ -n "$APP_PATH" ]] || { usage >&2; fail "--app is required"; }
[[ -d "$APP_PATH" ]] || fail "app path does not exist or is not a directory: $APP_PATH"
[[ "$APP_PATH" == *.app ]] || fail "app path must end in .app: $APP_PATH"
[[ -f "$APP_PATH/Contents/Info.plist" ]] || fail "app Info.plist is missing: $APP_PATH/Contents/Info.plist"

APP_PATH="$(cd "$(dirname "$APP_PATH")" && pwd -P)/$(basename "$APP_PATH")"
APP_ID="$(plist_value "$APP_PATH/Contents/Info.plist" CFBundleIdentifier || true)"
[[ -n "$APP_ID" ]] || fail "app bundle identifier is missing: $APP_PATH"

echo "preflight=read_only"
echo "macos.version=$(sw_vers -productVersion)"
echo "macos.build=$(sw_vers -buildVersion)"
echo "macos.arch=$(uname -m)"
echo "app.path=$APP_PATH"
echo "app.bundle_id=$APP_ID"
echo "app.signature=$(check_signature "$APP_PATH")"
echo "app.gatekeeper=$(check_gatekeeper "$APP_PATH")"

credential_count=0
safari_count=0
while IFS='|' read -r kind identifier point; do
    [[ -n "$kind" ]] || continue
    case "$kind" in
        credential_provider) credential_count=$((credential_count + 1)) ;;
        safari_web_extension) safari_count=$((safari_count + 1)) ;;
    esac
    echo "$kind.bundle_id=$identifier"
    echo "$kind.plugin_registration=$(plugin_registration "$identifier" "$point")"
    echo "$kind.ui_enablement=unknown"
done < <(extension_identities "$APP_PATH")

if [[ "$credential_count" -eq 0 ]]; then
    echo "credential_provider.bundle_id=absent"
    echo "credential_provider.plugin_registration=absent"
    echo "credential_provider.ui_enablement=absent"
fi
if [[ "$safari_count" -eq 0 ]]; then
    echo "safari_web_extension.bundle_id=absent"
    echo "safari_web_extension.plugin_registration=absent"
    echo "safari_web_extension.ui_enablement=absent"
fi
echo "note=PluginKit registration is not proof of AutoFill or Safari Settings enablement; this read-only command reports that UI state as unknown."
