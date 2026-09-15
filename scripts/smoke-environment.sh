#!/usr/bin/env bash

# Build the environment used to launch an isolated packaged smoke app.
#
# HOME stays isolated on every platform because packaged startup reads native
# user folders and app data through it. macOS Keychain is intentionally absent
# in this world. The smoke run instead supplies a random, run-scoped Fernet key
# that the authenticated helper accepts only behind both isolated-test gates.
smoke_build_isolated_env() {
  local os_home="$1"
  local matrx_home="$2"
  local engine_port_base="$3"
  local run_id="$4"
  local db_encryption_key="$5"

  SMOKE_ISOLATED_ENV=(
    "MATRX_ISOLATED_TEST=1"
    "MATRX_HOME_DIR=$matrx_home"
    "MATRX_PORT=$engine_port_base"
    "MATRX_PORT_BASE=$engine_port_base"
    "MATRX_SKIP_ORPHAN_SCAN=1"
    "MATRX_INSTANCE_SALT=smoke-$run_id"
    "MATRX_CLOUD_PARTICIPATION=0"
    "MATRX_ISOLATED_DB_ENCRYPTION_KEY=$db_encryption_key"
    "TEST_MODE=1"
    "HOME=$os_home"
    "USERPROFILE=$os_home"
    "APPDATA=$os_home/AppData/Roaming"
    "LOCALAPPDATA=$os_home/AppData/Local"
    "XDG_DATA_HOME=$os_home/.local/share"
    "XDG_CONFIG_HOME=$os_home/.config"
    "XDG_CACHE_HOME=$os_home/.cache"
  )
}

smoke_verify_isolated_encryption_environment() {
  local db_encryption_key="$1"
  # A Fernet key is 32 random bytes encoded as 44 URL-safe base64 characters.
  [ "${#db_encryption_key}" -eq 44 ] && [ "${db_encryption_key:43:1}" = "=" ]
}

smoke_tauri_config() {
  # Incognito is load-bearing on macOS: WKWebView does not derive its persistent
  # data store from the process HOME, so HOME isolation alone can reuse the
  # installed app's signed-in browser session.
  printf '%s' '{"app":{"windows":[{"incognito":true}]},"bundle":{"createUpdaterArtifacts":false}}'
}

smoke_acquire_build_lock() {
  local requested_lock_dir="$1"
  mkdir -p "$(dirname "$requested_lock_dir")"
  if ! mkdir "$requested_lock_dir" 2>/dev/null; then
    return 1
  fi
  SMOKE_BUILD_LOCK_DIR="$requested_lock_dir"
  SMOKE_BUILD_LOCK_OWNED=1
}

smoke_release_build_lock() {
  if [ "${SMOKE_BUILD_LOCK_OWNED:-0}" -eq 1 ]; then
    rmdir "$SMOKE_BUILD_LOCK_DIR" 2>/dev/null || true
    SMOKE_BUILD_LOCK_OWNED=0
  fi
}
