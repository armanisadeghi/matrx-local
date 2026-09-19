#!/usr/bin/env bash
#
# smoke.sh — build it, run it, watch what it logs, hand the logs back.
#
# WHY THIS EXISTS
#   v1.3.104 shipped a crash that killed the app on boot for every user.
#   Typecheck passed, the bundle built, CI was green — because nothing in the
#   pipeline ever RAN the app. This script runs it, and turns "what did it log
#   on startup" into a single artifact an agent can read without a human
#   relaying screenshots back and forth.
#
# THIS IS NOT WIRED INTO release.sh. It is opt-in until it has earned trust.
#
# MODES
#   web       (default, ~1-2 min) Production Vite bundle in a real browser via
#             Playwright. Catches render crashes, router bugs, uncaught errors,
#             console errors. Does NOT exercise Rust or the Python engine.
#   packaged  (~10-20 min; macOS + Windows/Git Bash + Linux) The whole thing:
#             PyInstaller sidecar + a real
#             `tauri build`, then LAUNCH the packaged .app, capture every line
#             Rust and the engine print, probe the engine, quit it, and verify
#             nothing was orphaned (the lifecycle-ownership contract in
#             CLAUDE.md). This is the one that catches startup errors that only
#             exist in the compiled artifact.
#   all       web, then packaged.
#
# USAGE
#   ./scripts/smoke.sh                 # web
#   ./scripts/smoke.sh packaged        # full build + launch + log capture
#   ./scripts/smoke.sh packaged --no-build   # reuse the last build (fast reruns)
#   ./scripts/smoke.sh all
#   Windows: scripts\smoke.ps1 [same args]  (wrapper → this script, via Git Bash)
#
# BOTH MODES run in a private TEST world. They never read/write ~/.matrx or
# ~/.matrx-dev and never probe 22140-22259. Packaged smoke may run safely while
# the installed app and development engines are active.
#
# OUTPUT
#   .smoke/runs/<timestamp>/  — summary.md (read this first), app.log, web.log
#   Exit 0 = clean. Exit 1 = something failed; summary.md says what.
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/scripts/smoke-environment.sh"
source "$REPO_ROOT/scripts/smoke-http.sh"
source "$REPO_ROOT/scripts/smoke-syncd.sh"
SMOKE_BUILD_LOCK_OWNED=0
# The full app/daemon cleanup handler is installed after its helpers below.
# Keep this minimal handler for preflight exits that happen before then.
trap smoke_release_build_lock EXIT
SMOKE_SYNCD_CLEANUP_NEEDED=0
SMOKE_APP_CLEANUP_NEEDED=0
SMOKE_APP_PID=""
SMOKE_APP_QUIESCED=1
SMOKE_APP_GRACEFUL=1

MODE="${1:-web}"
[[ "$MODE" == -* ]] && MODE="web"
NO_BUILD=0
for arg in "$@"; do [[ "$arg" == "--no-build" ]] && NO_BUILD=1; done

# Include the shell PID: two agents can start smoke within the same second,
# and sharing a run directory lets their logs/summaries overwrite each other.
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_DIR="$REPO_ROOT/.smoke/runs/$RUN_ID"
mkdir -p "$RUN_DIR"
SUMMARY="$RUN_DIR/summary.md"
SMOKE_OS_HOME="$RUN_DIR/os-home"
SMOKE_MATRX_HOME="$RUN_DIR/matrx-home"
mkdir -p "$SMOKE_OS_HOME" "$SMOKE_MATRX_HOME"
# A fresh encrypted test world gets a fresh DEK. It is never printed or stored;
# the run-scoped SQLite database becomes intentionally unreadable after smoke.
SMOKE_DB_ENCRYPTION_KEY="$(node -e 'process.stdout.write(require("crypto").randomBytes(32).toString("base64url") + "=")')"

# A third, run-specific world: live=22140+, dev=22240+, smoke=23000-65000.
# Space concurrent smoke runs 20 ports apart. The exact engine port is forced,
# so it fails closed if another process wins the tiny check→launch race.
SMOKE_BUILD_PORT_MARKER="$REPO_ROOT/desktop/src-tauri/target/.matrx-isolated-smoke-port"
if [ "$NO_BUILD" -eq 1 ] && [ "$MODE" != "web" ]; then
  # A no-build run consumes both the marker and the artifact it describes.
  # Lock before either read so a builder cannot replace them between reads.
  if ! smoke_acquire_build_lock "$REPO_ROOT/desktop/src-tauri/target/.matrx-smoke-build.lock"; then
    echo "smoke: another packaged smoke run owns the shared build output" >&2
    exit 2
  fi
  if [ ! -f "$SMOKE_BUILD_PORT_MARKER" ]; then
    echo "smoke: --no-build requires a previously built isolated smoke app" >&2
    exit 2
  fi
  SMOKE_ENGINE_PORT_BASE="$(tr -dc '0-9' < "$SMOKE_BUILD_PORT_MARKER")"
else
  SMOKE_ENGINE_PORT_BASE="$(node -e '
const net=require("net");
const span=2100;
const start=((process.pid+Date.now())%span);
const probe=(port)=>new Promise((resolve)=>{
  const s=net.createServer();
  s.once("error",()=>resolve(false));
  s.listen(port,"127.0.0.1",()=>s.close(()=>resolve(true)));
});
(async()=>{
  for(let i=0;i<span;i++){
    const base=23000+(((start+i)%span)*20);
    if(base>65000) continue;
    if(await probe(base)){ console.log(base); return; }
  }
  process.exit(1);
})()' 2>/dev/null)"
fi
if [ -z "$SMOKE_ENGINE_PORT_BASE" ]; then
  echo "smoke: could not allocate an isolated engine port base" >&2
  exit 2
fi

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
info() { echo -e "${CYAN}▸${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; }

FAILURES=0
{
  echo "# Smoke run $RUN_ID"
  echo
  echo "- mode: \`$MODE\`"
  echo "- commit: \`$(git rev-parse --short HEAD 2>/dev/null || echo unknown)\`"
  echo "- version: \`$(node -p "require('$REPO_ROOT/desktop/package.json').version" 2>/dev/null || echo unknown)\`"
  echo "- isolated home: \`$SMOKE_MATRX_HOME\`"
  echo "- isolated engine ports: \`$SMOKE_ENGINE_PORT_BASE-$((SMOKE_ENGINE_PORT_BASE + 19))\`"
  echo
} > "$SUMMARY"

record_fail() { FAILURES=$((FAILURES + 1)); echo "## ❌ $1" >> "$SUMMARY"; echo >> "$SUMMARY"; { [ -n "${2:-}" ] && { echo '```'; echo "$2"; echo '```'; echo; }; } >> "$SUMMARY"; fail "$1"; }
record_ok()   { echo "## ✅ $1" >> "$SUMMARY"; echo >> "$SUMMARY"; ok "$1"; }

smoke_cleanup_private_syncd() {
  [ "$SMOKE_SYNCD_CLEANUP_NEEDED" -eq 1 ] || return 0
  if smoke_shutdown_private_syncd "$SMOKE_MATRX_HOME"; then
    SMOKE_SYNCD_CLEANUP_NEEDED=0
    record_ok "packaged: private dev sync daemon stopped and removed its boundary"
    return 0
  fi
  record_fail "packaged: private sync daemon cleanup failed"
  return 1
}

smoke_stop_owned_app() {
  [ "$SMOKE_APP_CLEANUP_NEEDED" -eq 1 ] || return 0
  local waited=0 forced_waited=0
  if pid_alive "$SMOKE_APP_PID"; then
    terminate_pid "$SMOKE_APP_PID"
    while pid_alive "$SMOKE_APP_PID" && [ "$waited" -lt 40 ]; do
      sleep 1
      waited=$((waited + 1))
    done
  fi
  if pid_alive "$SMOKE_APP_PID"; then
    force_kill_pid "$SMOKE_APP_PID"
    while pid_alive "$SMOKE_APP_PID" && [ "$forced_waited" -lt 5 ]; do
      sleep 1
      forced_waited=$((forced_waited + 1))
    done
  fi
  if pid_alive "$SMOKE_APP_PID"; then
    SMOKE_APP_QUIESCED=0
    record_fail "packaged: app remained alive after its owned force-kill"
    return 1
  fi
  SMOKE_APP_CLEANUP_NEEDED=0
  SMOKE_APP_QUIESCED=1
  if [ "$forced_waited" -gt 0 ] || [ "$waited" -ge 40 ]; then
    SMOKE_APP_GRACEFUL=0
    record_fail "packaged: app did not exit within 40s of a graceful quit signal (had to force-kill)"
    return 1
  fi
  SMOKE_APP_GRACEFUL=1
  record_ok "packaged: app exited cleanly on a graceful quit signal in ${waited}s"
  return 0
}

smoke_on_exit() {
  local original_status="$?" cleanup_status=0
  trap - EXIT
  if ! smoke_stop_owned_app; then
    cleanup_status=1
  fi
  if [ "$SMOKE_APP_QUIESCED" -eq 1 ]; then
    smoke_cleanup_private_syncd || cleanup_status=1
  else
    cleanup_status=1
  fi
  smoke_release_build_lock
  if [ "$original_status" -ne 0 ] || [ "$cleanup_status" -ne 0 ]; then
    exit 1
  fi
  exit 0
}

trap smoke_on_exit EXIT

# ── Log triage ───────────────────────────────────────────────────────────────
# Lines that mean "this build is broken". Kept deliberately tight: a smoke
# signal nobody trusts is a smoke signal nobody reads. Add a pattern here the
# moment a real startup failure slips through with a distinctive line.
FATAL_PATTERNS='panicked at|Traceback \(most recent call last\)|Something went wrong|may be used only in the context of|ended unexpectedly|Uncaught (TypeError|ReferenceError|Error)|ModuleNotFoundError|ImportError|PackageNotFoundError|No package metadata was found|Address already in use|\[launcher\] [a-z-]+ → failed|\[sigterm_then_kill\].*did NOT exit.*SIGKILL|\[shutdown\].*did NOT complete'
# Noise that is expected on a dev machine and is NOT a build defect.
BENIGN_PATTERNS='DeprecationWarning|urllib3|NotOpenSSLWarning|ExperimentalWarning'

# ── Platform abstraction (macOS + Windows/Git Bash + Linux) ──────────────────
# Windows runs this under Git Bash — the same shell CI already uses to invoke
# build-sidecar.sh on windows-latest, so there is exactly one packaged-mode
# code path to keep correct instead of two that drift.
case "$(uname -s)" in
  Darwin)              OS=macos ;;
  MINGW*|MSYS*|CYGWIN*) OS=windows ;;
  Linux)               OS=linux ;;
  *)                   OS=unknown ;;
esac

# PIDs of engine-spawned children we care about (lifecycle-ownership contract).
child_pids() {
  if [ "$OS" = "windows" ]; then
    powershell -NoProfile -Command \
      "Get-Process cloudflared,llama-server -ErrorAction SilentlyContinue | ForEach-Object { \$_.Id }" \
      2>/dev/null | tr -d '\r' | sort
  else
    pgrep -f 'cloudflared|llama-server' 2>/dev/null | sort
  fi
}

describe_pid() {
  if [ "$OS" = "windows" ]; then
    powershell -NoProfile -Command \
      "Get-Process -Id $1 -ErrorAction SilentlyContinue | ForEach-Object { \"\$(\$_.Id) \$(\$_.ProcessName)\" }" \
      2>/dev/null | tr -d '\r'
  else
    ps -p "$1" -o pid=,command= 2>/dev/null
  fi
}

pid_alive() {
  if [ "$OS" = "windows" ]; then
    powershell -NoProfile -Command "if (Get-Process -Id $1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >/dev/null 2>&1
  else
    kill -0 "$1" 2>/dev/null
  fi
}

# Ask the app to quit the way a user's Quit does, so its own graceful-shutdown
# chain runs (that chain is what we're actually testing).
terminate_pid() {
  if [ "$OS" = "windows" ]; then
    taskkill //PID "$1" //T >/dev/null 2>&1
  else
    kill -TERM "$1" 2>/dev/null
  fi
}

force_kill_pid() {
  if [ "$OS" = "windows" ]; then
    taskkill //PID "$1" //T //F >/dev/null 2>&1
  else
    kill -KILL "$1" 2>/dev/null
  fi
}

# The packaged executable, per platform.
find_app_binary() {
  case "$OS" in
    macos)
      local app_dir app_executable app_binary
      app_dir="$(find desktop/src-tauri/target -type d -name "*.app" -path "*bundle/macos*" 2>/dev/null | head -1)"
      [ -z "$app_dir" ] && return 1
      # Run the Mach-O directly, NOT `open -a`: `open` hands the process to
      # launchd and its stdout/stderr — the whole point — is lost.
      #
      # Contents/MacOS also holds every external binary. Filtering known names
      # is not a contract: adding bundled uv made the harness launch uv and
      # report a phantom app crash. Info.plist is the authoritative executable.
      app_executable="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' \
        "$app_dir/Contents/Info.plist" 2>/dev/null || true)"
      if [ -z "$app_executable" ]; then
        app_executable="$(plutil -extract CFBundleExecutable raw \
          "$app_dir/Contents/Info.plist" 2>/dev/null || true)"
      fi
      case "$app_executable" in
        ""|*/*) return 1 ;;
      esac
      app_binary="$app_dir/Contents/MacOS/$app_executable"
      [ -f "$app_binary" ] && [ -x "$app_binary" ] || return 1
      printf '%s\n' "$app_binary"
      ;;
    windows)
      # tauri build leaves the runnable .exe in target/release; the NSIS/MSI
      # bundles under bundle/ are INSTALLERS, not the app.
      # matrx-syncd.exe is a cargo workspace sibling that lands in the same
      # directory (FS-C1) — it is a sidecar, never the app.
      find desktop/src-tauri/target/release -maxdepth 1 -type f -name "*.exe" 2>/dev/null \
        | grep -vi -e setup -e installer -e uninstall -e matrx-syncd | head -1
      ;;
    linux)
      # "*matrx*" also matches the matrx-syncd workspace sibling (FS-C1).
      find desktop/src-tauri/target/release -maxdepth 1 -type f -executable \
        -name "*matrx*" ! -name "matrx-syncd*" 2>/dev/null | head -1
      ;;
  esac
}

scan_log() { # scan_log <file> <label>
  local file="$1" label="$2" hits
  [ -s "$file" ] || { warn "$label: no output captured"; return 0; }
  hits="$(grep -nEi "$FATAL_PATTERNS" "$file" | grep -vEi "$BENIGN_PATTERNS" | head -40)"
  if [ -n "$hits" ]; then
    record_fail "$label: fatal lines in the log" "$hits"
  else
    record_ok "$label: no fatal lines in the log"
  fi
}

# ── web ──────────────────────────────────────────────────────────────────────
run_web() {
  info "Building the production bundle and booting it in a real browser…"
  local log="$RUN_DIR/web.log"
  local smoke_port
  smoke_port="$(node -e "const s=require('net').createServer();s.listen(0,'127.0.0.1',()=>{console.log(s.address().port);s.close()})")"
  ( cd desktop && \
    SMOKE_PREVIEW=1 \
    SMOKE_PORT="$smoke_port" \
    VITE_MATRX_ISOLATED_SMOKE=1 \
    VITE_MATRX_TEST_ENGINE_PORT_BASE="$SMOKE_ENGINE_PORT_BASE" \
    pnpm exec playwright test boot.spec.ts --reporter=list ) > "$log" 2>&1
  local rc=$?
  if [ $rc -eq 0 ]; then
    record_ok "web: production bundle boots clean (no crash screen, no uncaught errors)"
  else
    record_fail "web: boot smoke FAILED (exit $rc)" "$(tail -40 "$log")"
  fi
  echo "Full log: \`$log\`" >> "$SUMMARY"; echo >> "$SUMMARY"
}

# ── packaged ─────────────────────────────────────────────────────────────────
run_packaged() {
  if [ "$OS" = "unknown" ]; then
    warn "packaged mode: unsupported platform $(uname -s) — skipping"
    echo "## ⏭️ packaged: skipped (unsupported platform)" >> "$SUMMARY"; echo >> "$SUMMARY"
    return 0
  fi

  local log="$RUN_DIR/app.log" build_log="$RUN_DIR/build.log"

  # Snapshot live ownership only to prove it remains untouched. The test never
  # adopts this URL or reads this file for its own engine discovery.
  local live_discovery="$HOME/.matrx/local.json" live_url_before="" live_pid_before=""
  if [ -f "$live_discovery" ]; then
    live_url_before="$(node -p "try{require('$live_discovery').url||''}catch(e){''}" 2>/dev/null)"
    live_pid_before="$(node -p "try{require('$live_discovery').pid||''}catch(e){''}" 2>/dev/null)"
  fi
  # Anything of ours already alive is pre-existing, not an orphan we created.
  # Snapshot it so the post-shutdown check can diff instead of blaming.
  local pre_children
  pre_children="$(child_pids | tr '\n' ' ')"
  [ -n "$pre_children" ] && warn "pre-existing cloudflared/llama-server PIDs (will be ignored): $pre_children"

  # Both builders and --no-build consumers use shared target artifacts. Hold
  # exclusive ownership until the packaged app has fully shut down so another
  # smoke cannot replace its sidecar or bundle during launch.
  if [ "$SMOKE_BUILD_LOCK_OWNED" -ne 1 ] && \
     ! smoke_acquire_build_lock "$REPO_ROOT/desktop/src-tauri/target/.matrx-smoke-build.lock"; then
    record_fail "packaged: another smoke run owns the shared build output" \
      "Wait for that packaged smoke run to finish, then retry."
    return 1
  fi

  if [ "$NO_BUILD" -eq 0 ]; then
    info "Building the Python sidecar (PyInstaller — several minutes)…"
    if ! ./scripts/build-sidecar.sh > "$build_log" 2>&1; then
      record_fail "packaged: sidecar build failed" "$(tail -30 "$build_log")"
      echo "Full build log: \`$build_log\`" >> "$SUMMARY"
      return 1
    fi
    ok "sidecar built"

    # The Rust folder-sync daemon is an externalBin on every platform, so the
    # -$TARGET_TRIPLE file must exist before `tauri build`. tauri.conf.json's
    # beforeBuildCommand already ensures it; doing it here too keeps the
    # harness independent of a hand-built, gitignored artifact and costs
    # ~0.5s when cargo has nothing to do.
    info "Ensuring the matrx-syncd sidecar is present…"
    if ! ./scripts/build-syncd.sh >> "$build_log" 2>&1; then
      record_fail "packaged: matrx-syncd sidecar build failed" "$(tail -30 "$build_log")"
      echo "Full build log: \`$build_log\`" >> "$SUMMARY"
      return 1
    fi
    ok "matrx-syncd sidecar ready"

    # macOS helpers are NOT externalBin (tauri-bundler would sign them with the
    # HOST entitlements — the v1.4.137 exit-137 class). They ship as
    # bundle.macOS.files, staged here. Unsigned smoke keeps each binary's own
    # signature; release CI stages with --sign.
    if [ "$OS" = "macos" ]; then
      info "Staging the macOS helper executables…"
      if ! ./scripts/stage-macos-helpers.sh >> "$build_log" 2>&1; then
        record_fail "packaged: macOS helper staging failed" "$(tail -30 "$build_log")"
        echo "Full build log: \`$build_log\`" >> "$SUMMARY"
        return 1
      fi
      ok "macOS helpers staged"
    fi

    # --bundles app: build ONLY the runnable app, nothing else.
    #   * No DMG. `bundle_dmg.sh` MOUNTS the disk image and pops a real Finder
    #     window ("drag the app to Applications") in the middle of the run —
    #     it hijacks your screen and can interfere with the launch we're about
    #     to measure. A smoke run must never touch the desktop.
    #   * No updater tarball. That one demands TAURI_SIGNING_PRIVATE_KEY and
    #     hard-fails the build without it — AFTER the .app is already built.
    #     Installers and update signing belong to release.yml, not to a
    #     "does it start" check. We only ever need the binary.
    # Platform bundle names: `app` on macOS, `nsis` on Windows (both produce
    # the runnable artifact without a signing key).
    local bundle_flag="--bundles app"
    [ "$OS" = "windows" ] && bundle_flag="--no-bundle"
    [ "$OS" = "linux" ] && bundle_flag="--no-bundle"

    # tauri.conf.json sets createUpdaterArtifacts:true for the REAL release
    # (release.yml signs the updater tarball with TAURI_SIGNING_PRIVATE_KEY).
    # That flag forces the updater artifact even under `--bundles app`, so the
    # build hard-fails on the missing key AFTER the .app is built — which is
    # exactly the "does it start" signal we need. Override it OFF for the smoke
    # build only, via Tauri's inline config merge; the committed config (and
    # thus the real release) is untouched.
    local isolated_tauri_cfg
    isolated_tauri_cfg="$(smoke_tauri_config)"
    local provider_config=""
    local provider_mode="${MATRX_NATIVE_VAULT_PROVIDER:-}"
    if [ "$OS" = "macos" ] && [ -z "$provider_mode" ]; then
      # A local smoke machine may have only Command Line Tools. In that case
      # the macOS credential-provider extension cannot be compiled, so prove
      # the explicit host-only artifact instead of failing late at Tauri's
      # file-copy step. Release CI still opts into `sealed` with full Xcode,
      # its provisioning profile, and its signing identity.
      if ! DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}" \
        xcrun --find swiftc >/dev/null 2>&1; then
        provider_mode="absent"
        warn "Full Xcode is unavailable; packaging the explicit host-only macOS smoke artifact"
      fi
    fi
    if [ "$OS" = "macos" ] && [ "$provider_mode" = "absent" ]; then
      # Match release.yml's explicit host-only profile when the optional signed
      # credential provider is not part of this artifact.
      provider_config="src-tauri/tauri.release.macos.host-only.conf.json"
    fi

    info "Packaging the desktop app (tauri build — several minutes)…"
    if [ -n "$provider_config" ]; then
      ( cd desktop && \
        VITE_MATRX_ISOLATED_SMOKE=1 \
        VITE_MATRX_TEST_ENGINE_PORT_BASE="$SMOKE_ENGINE_PORT_BASE" \
        MATRX_NATIVE_VAULT_PROVIDER="$provider_mode" \
        pnpm tauri build $bundle_flag --config "$provider_config" --config "$isolated_tauri_cfg" ) >> "$build_log" 2>&1
    else
      ( cd desktop && \
        VITE_MATRX_ISOLATED_SMOKE=1 \
        VITE_MATRX_TEST_ENGINE_PORT_BASE="$SMOKE_ENGINE_PORT_BASE" \
        MATRX_NATIVE_VAULT_PROVIDER="$provider_mode" \
        pnpm tauri build $bundle_flag --config "$isolated_tauri_cfg" ) >> "$build_log" 2>&1
    fi
    if [ "$?" -ne 0 ]; then
      record_fail "packaged: tauri build failed" "$(tail -30 "$build_log")"
      echo "Full build log: \`$build_log\`" >> "$SUMMARY"
      return 1
    fi
    printf '%s\n' "$SMOKE_ENGINE_PORT_BASE" > "$SMOKE_BUILD_PORT_MARKER"
    record_ok "packaged: sidecar + app built"
  else
    warn "--no-build: reusing the last packaged build"
  fi

  # Every bundled helper must actually EXEC. v1.4.137-v1.4.170 shipped five
  # helpers macOS SIGKILLed at exec (exit 137) while the app itself started
  # fine, Gatekeeper accepted the bundle and notarization was stapled: nothing
  # in the harness had ever run one. Now it does.
  if [ "$OS" = "macos" ]; then
    local smoke_app
    smoke_app="$(find desktop/src-tauri/target -type d -name "*.app" -path "*bundle/macos*" 2>/dev/null | head -1)"
    if [ -n "$smoke_app" ]; then
      local helper_failures=""
      local helper_seen=0
      local host_exe
      host_exe="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$smoke_app/Contents/Info.plist" 2>/dev/null)"
      for helper in "$smoke_app"/Contents/MacOS/*; do
        [ -f "$helper" ] || continue
        [ "$(basename "$helper")" = "$host_exe" ] && continue
        file "$helper" | grep -q "Mach-O" || continue
        helper_seen=$((helper_seen + 1))
        DYLD_LIBRARY_PATH="$smoke_app/Contents/Resources/binaries" \
          perl -e 'alarm 30; exec @ARGV' "$helper" --version >/dev/null 2>&1
        local helper_status=$?
        if [ "$helper_status" -ne 0 ]; then
          helper_failures="$helper_failures\n  $(basename "$helper") exited $helper_status"
        fi
      done
      if [ -n "$helper_failures" ]; then
        record_fail "packaged: bundled helper(s) could not execute" \
          "$(printf 'Exit 137 means macOS SIGKILLed the helper at exec — check its entitlements (scripts/stage-macos-helpers.sh).%b' "$helper_failures")"
      elif [ "$helper_seen" -gt 0 ]; then
        record_ok "packaged: all $helper_seen bundled helpers execute"
      else
        record_fail "packaged: no bundled helpers found in the .app" \
          "The app ships matrx-syncd, matrx-egress, cloudflared, llama-server and uv; finding none means this check stopped checking anything."
      fi
    fi
  fi

  local bin
  bin="$(find_app_binary)"
  if [ -z "$bin" ]; then
    record_fail "packaged: no packaged executable found under desktop/src-tauri/target (build first, or drop --no-build)"
    return 1
  fi

  # A separate DEV-world engine is allowed and may own cloudflared/LLM
  # children. The launch-time snapshot is authoritative: replacing the earlier
  # baseline avoids both concurrent-dev false positives and stale-PID reuse.
  pre_children="$(child_pids | tr '\n' ' ')"
  info "Launching $(basename "$bin") and capturing everything it logs…"

  smoke_build_isolated_env \
    "$SMOKE_OS_HOME" "$SMOKE_MATRX_HOME" "$SMOKE_ENGINE_PORT_BASE" "$RUN_ID" \
    "$SMOKE_DB_ENCRYPTION_KEY"
  if ! smoke_verify_isolated_encryption_environment "$SMOKE_DB_ENCRYPTION_KEY"; then
    record_fail "packaged: could not create an isolated database encryption key"
    return 1
  fi
  env "${SMOKE_ISOLATED_ENV[@]}" "$bin" > "$log" 2>&1 &
  local pid=$!
  SMOKE_APP_PID="$pid"
  SMOKE_APP_CLEANUP_NEEDED=1
  SMOKE_APP_QUIESCED=0
  SMOKE_APP_GRACEFUL=0
  SMOKE_SYNCD_CLEANUP_NEEDED=1

  # Give it a real startup window: Rust setup + sidecar spawn + engine boot.
  # A cold machine may load the Whisper model and a large local LLM before the
  # sidecar reaches FastAPI, then initialize the remote-backed AI/tool registry.
  # Sixty seconds proved too tight (a healthy build reached /health at 58s), and
  # a 120s run was still making forward progress through Phase 2 when the harness
  # interrupted it. Five minutes distinguishes that legitimate cold path from a
  # real startup hang while remaining a finite release gate.
  local waited=0 engine_url=""
  while [ $waited -lt 300 ]; do
    if ! pid_alive "$pid"; then
      record_fail "packaged: the app DIED during startup after ${waited}s" "$(tail -40 "$log")"
      echo "Full app log: \`$log\`" >> "$SUMMARY"; echo >> "$SUMMARY"
      scan_log "$log" "packaged app log"
      return 1
    fi
    engine_url="$(node -p "try{require('$SMOKE_MATRX_HOME/local.json').url}catch(e){''}" 2>/dev/null)"
    if [ -n "$engine_url" ] && smoke_http_get "$engine_url/health" "$RUN_DIR/health.json" "$RUN_DIR/health.curl.log" 2; then
      break
    fi
    sleep 2; waited=$((waited + 2))
  done

  if [ -s "$RUN_DIR/health.json" ]; then
    record_ok "packaged: app stayed up and the engine answered /health in ${waited}s ($engine_url)"
  else
    record_fail "packaged: engine never answered /health within ${waited}s" "$(tail -30 "$log")
$(smoke_http_diagnostic "$RUN_DIR/health.curl.log")"
  fi

  # /health is a LIAR for our purposes: it returns {"status":"ok"} while the AI
  # engine is dead on the floor. That is exactly how v1.3.105 shipped with a
  # broken ai_engine (PyInstaller dropped replicate's metadata). The launcher
  # registry at /admin/status is the authoritative per-service state — check it.
  #
  # failed vs degraded, and why they are not the same:
  #   failed   = the build is broken. Hard fail.
  #   degraded = graceful degradation, which is DESIGNED behaviour (Hard Rule 3:
  #              the engine runs without Postgres / Brave / a reachable remote).
  #              A degraded service is often environmental — scraper_retry_queue
  #              goes degraded when the REMOTE server 500s, which has nothing to
  #              do with the build under test. Failing on that would make this
  #              signal flaky, and a flaky signal gets ignored. So: report it
  #              loudly in the summary, never fail on it.
  if [ -n "$engine_url" ] && smoke_http_get "$engine_url/admin/status" "$RUN_DIR/admin-status.json" "$RUN_DIR/admin-status.curl.log" 5; then
    local failed_svcs degraded_svcs
    failed_svcs="$(node -e "
      const s = require('$RUN_DIR/admin-status.json').services || {};
      console.log(Object.values(s).filter(v => v.state === 'failed')
        .map(v => \`\${v.name}: \${v.error || 'no error recorded'}\`).join('\n'));
    " 2>/dev/null)"
    degraded_svcs="$(node -e "
      const s = require('$RUN_DIR/admin-status.json').services || {};
      console.log(Object.values(s).filter(v => v.state === 'degraded')
        .map(v => \`\${v.name}: \${v.error || 'no error recorded'}\`).join('\n'));
    " 2>/dev/null)"

    if [ -n "$failed_svcs" ]; then
      record_fail "packaged: engine service(s) FAILED to start" "$failed_svcs"
    else
      record_ok "packaged: no engine service in state=failed (/admin/status)"
    fi
    if [ -n "$degraded_svcs" ]; then
      warn "degraded services (not a build failure — check if expected):"
      echo "$degraded_svcs" | sed 's/^/    /'
      { echo "## ⚠️ packaged: degraded services (informational, not a failure)"; echo;
        echo '```'; echo "$degraded_svcs"; echo '```'; echo; } >> "$SUMMARY"
    fi
  else
    warn "could not read /admin/status — per-service health unverified"
    { echo "## ⚠️ packaged: could not read /admin/status — per-service health unverified"; echo;
      echo '```'; smoke_http_diagnostic "$RUN_DIR/admin-status.curl.log"; echo '```'; echo; } >> "$SUMMARY"
  fi

  # Existing image-generation installs may need a mandatory compatibility
  # migration after an app update. It runs in the background so engine startup
  # stays responsive, but quitting here would deliberately interrupt pip and
  # leave a partial managed runtime. If this boot started that migration, wait
  # for its terminal state and make any repair failure a release blocker.
  if [ -n "$engine_url" ] && smoke_http_get "$engine_url/image-gen/install/status" "$RUN_DIR/image-install-status.json" "$RUN_DIR/image-install-status.curl.log" 5 smoke-local; then
    local image_install_status image_install_error image_waited=0
    image_install_status="$(node -p "require('$RUN_DIR/image-install-status.json').status || ''" 2>/dev/null)"
    if [ "$image_install_status" = "running" ]; then
      info "Waiting for the mandatory image-runtime migration to finish…"
      while [ "$image_install_status" = "running" ] && [ $image_waited -lt 900 ]; do
        if ! pid_alive "$pid"; then
          image_install_status="app-exited"
          break
        fi
        sleep 2
        image_waited=$((image_waited + 2))
        if smoke_http_get "$engine_url/image-gen/install/status" "$RUN_DIR/image-install-status.json" "$RUN_DIR/image-install-status.curl.log" 5 smoke-local; then
          image_install_status="$(node -p "require('$RUN_DIR/image-install-status.json').status || ''" 2>/dev/null)"
        else
          image_install_status="unreadable"
        fi
      done

      case "$image_install_status" in
        complete)
          record_ok "packaged: mandatory image-runtime migration completed in ${image_waited}s"
          ;;
        error)
          image_install_error="$(node -p "require('$RUN_DIR/image-install-status.json').error || require('$RUN_DIR/image-install-status.json').message || 'no error recorded'" 2>/dev/null)"
          record_fail "packaged: mandatory image-runtime migration FAILED" "$image_install_error"
          ;;
        running)
          record_fail "packaged: mandatory image-runtime migration did not finish within ${image_waited}s" "$(node -p "require('$RUN_DIR/image-install-status.json').message || 'still running'" 2>/dev/null)"
          ;;
        *)
          record_fail "packaged: lost the mandatory image-runtime migration while waiting (status=${image_install_status:-missing})" "$(smoke_http_diagnostic "$RUN_DIR/image-install-status.curl.log")"
          ;;
      esac
    elif [ "$image_install_status" = "error" ]; then
      image_install_error="$(node -p "require('$RUN_DIR/image-install-status.json').error || require('$RUN_DIR/image-install-status.json').message || 'no error recorded'" 2>/dev/null)"
      record_fail "packaged: image-runtime installer is in an error state" "$image_install_error"
    elif [ "$image_install_status" != "idle" ] && [ "$image_install_status" != "complete" ]; then
      record_fail "packaged: image-runtime installer returned an invalid status (${image_install_status:-missing})" "$(cat "$RUN_DIR/image-install-status.json" 2>/dev/null)"
    fi
  else
    record_fail "packaged: could not read /image-gen/install/status — background runtime migration unverified" "$(smoke_http_diagnostic "$RUN_DIR/image-install-status.curl.log")"
  fi

  # Let it settle so late-startup errors land in the log, then quit it the way
  # a user would — the app's own graceful shutdown chain is what we're testing.
  sleep 8
  info "Quitting the app and checking for orphans…"
  smoke_stop_owned_app || true
  if [ "$SMOKE_APP_QUIESCED" -eq 1 ]; then
    smoke_cleanup_private_syncd || true
  fi

  # Lifecycle-ownership contract (CLAUDE.md): when the app goes down it takes
  # every child with it. A survivor here is a real, shippable bug.
  sleep 2
  local orphans="" pid_now
  for pid_now in $(child_pids); do
    # Only children that did NOT exist before we launched are ours.
    case " $pre_children " in
      *" $pid_now "*) continue ;;
    esac
    orphans+="$(describe_pid "$pid_now")"$'\n'
  done
  orphans="$(echo "$orphans" | sed '/^[[:space:]]*$/d')"
  if [ -n "$orphans" ]; then
    record_fail "packaged: ORPHANED child processes survived shutdown (lifecycle-ownership violation)" "$orphans"
  else
    record_ok "packaged: no orphaned children after shutdown"
  fi

  scan_log "$log" "packaged app log"

  if [ -n "$live_pid_before" ]; then
    local live_pid_after live_url_after
    live_pid_after="$(node -p "try{require('$live_discovery').pid||''}catch(e){''}" 2>/dev/null)"
    live_url_after="$(node -p "try{require('$live_discovery').url||''}catch(e){''}" 2>/dev/null)"
    if [ "$live_pid_after" = "$live_pid_before" ] && [ -n "$live_url_after" ] && \
       curl -sf --max-time 2 "$live_url_after/health" >/dev/null 2>&1; then
      record_ok "packaged: pre-existing live engine remained healthy and PID-stable ($live_pid_before)"
    else
      record_fail "packaged: pre-existing live engine changed during isolated smoke" \
        "before pid=$live_pid_before url=$live_url_before; after pid=$live_pid_after url=$live_url_after"
    fi
  fi
  echo "Full app log: \`$log\`" >> "$SUMMARY"; echo >> "$SUMMARY"
  smoke_release_build_lock
}

case "$MODE" in
  web)      run_web ;;
  packaged) run_packaged ;;
  all)      run_web; run_packaged ;;
  *) echo "usage: $0 [web|packaged|all] [--no-build]" >&2; exit 2 ;;
esac

# ── Report ───────────────────────────────────────────────────────────────────
echo >> "$SUMMARY"
if [ "$FAILURES" -eq 0 ]; then
  echo "**Result: CLEAN** — $MODE smoke passed with no failures." >> "$SUMMARY"
else
  echo "**Result: $FAILURES FAILURE(S)** — see the sections above." >> "$SUMMARY"
fi

echo
echo "────────────────────────────────────────────────────────────"
cat "$SUMMARY"
echo "────────────────────────────────────────────────────────────"
echo
if [ "$FAILURES" -eq 0 ]; then
  ok "Smoke CLEAN. Artifacts: $RUN_DIR"
  exit 0
fi
fail "$FAILURES failure(s). Read: $SUMMARY"
exit 1
