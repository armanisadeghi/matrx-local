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

# A third, run-specific world: live=22140+, dev=22240+, smoke=23000-65000.
# Space concurrent smoke runs 20 ports apart. The exact engine port is forced,
# so it fails closed if another process wins the tiny check→launch race.
SMOKE_BUILD_PORT_MARKER="$REPO_ROOT/desktop/src-tauri/target/.matrx-isolated-smoke-port"
if [ "$NO_BUILD" -eq 1 ] && [ "$MODE" != "web" ]; then
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

# ── Log triage ───────────────────────────────────────────────────────────────
# Lines that mean "this build is broken". Kept deliberately tight: a smoke
# signal nobody trusts is a smoke signal nobody reads. Add a pattern here the
# moment a real startup failure slips through with a distinctive line.
FATAL_PATTERNS='panicked at|Traceback \(most recent call last\)|Something went wrong|may be used only in the context of|ended unexpectedly|Uncaught (TypeError|ReferenceError|Error)|ModuleNotFoundError|ImportError|PackageNotFoundError|No package metadata was found|Address already in use|\[launcher\] [a-z-]+ → failed|\[sigterm_then_kill\].*did NOT exit.*SIGKILL|\[shutdown\].*did NOT complete.*force-killing'
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
      local app_dir
      app_dir="$(find desktop/src-tauri/target -type d -name "*.app" -path "*bundle/macos*" 2>/dev/null | head -1)"
      [ -z "$app_dir" ] && return 1
      # Run the Mach-O directly, NOT `open -a`: `open` hands the process to
      # launchd and its stdout/stderr — the whole point — is lost.
      #
      # Contents/MacOS holds the SIDECARS too (cloudflared, llama-server), not
      # just the app binary. A bare `head -1` can pick cloudflared, which exits
      # in seconds printing "unable to find config file" — a phantom "app died
      # on startup" that has nothing to do with the app. Exclude the known
      # sidecars so we launch the actual Tauri binary (aimatrx-desktop).
      find "$app_dir/Contents/MacOS" -type f -perm +111 2>/dev/null \
        | grep -vE '/(cloudflared|llama-server|matrx-engine)[^/]*$' | head -1
      ;;
    windows)
      # tauri build leaves the runnable .exe in target/release; the NSIS/MSI
      # bundles under bundle/ are INSTALLERS, not the app.
      find desktop/src-tauri/target/release -maxdepth 1 -type f -name "*.exe" 2>/dev/null \
        | grep -vi -e setup -e installer -e uninstall | head -1
      ;;
    linux)
      find desktop/src-tauri/target/release -maxdepth 1 -type f -executable \
        -name "*matrx*" 2>/dev/null | head -1
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

  if [ "$NO_BUILD" -eq 0 ]; then
    info "Building the Python sidecar (PyInstaller — several minutes)…"
    if ! ./scripts/build-sidecar.sh > "$build_log" 2>&1; then
      record_fail "packaged: sidecar build failed" "$(tail -30 "$build_log")"
      echo "Full build log: \`$build_log\`" >> "$SUMMARY"
      return 1
    fi
    ok "sidecar built"

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
    local no_updater_cfg='{"bundle":{"createUpdaterArtifacts":false}}'

    info "Packaging the desktop app (tauri build — several minutes)…"
    if ! ( cd desktop && \
      VITE_MATRX_ISOLATED_SMOKE=1 \
      VITE_MATRX_TEST_ENGINE_PORT_BASE="$SMOKE_ENGINE_PORT_BASE" \
      pnpm tauri build $bundle_flag --config "$no_updater_cfg" ) >> "$build_log" 2>&1; then
      record_fail "packaged: tauri build failed" "$(tail -30 "$build_log")"
      echo "Full build log: \`$build_log\`" >> "$SUMMARY"
      return 1
    fi
    printf '%s\n' "$SMOKE_ENGINE_PORT_BASE" > "$SMOKE_BUILD_PORT_MARKER"
    record_ok "packaged: sidecar + app built"
  else
    warn "--no-build: reusing the last packaged build"
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

  local -a isolated_env=(
    "MATRX_ISOLATED_TEST=1"
    "MATRX_HOME_DIR=$SMOKE_MATRX_HOME"
    "MATRX_PORT=$SMOKE_ENGINE_PORT_BASE"
    "MATRX_PORT_BASE=$SMOKE_ENGINE_PORT_BASE"
    "MATRX_SKIP_ORPHAN_SCAN=1"
    "MATRX_INSTANCE_SALT=smoke-$RUN_ID"
    "MATRX_CLOUD_PARTICIPATION=0"
    "TEST_MODE=1"
    "HOME=$SMOKE_OS_HOME"
    "USERPROFILE=$SMOKE_OS_HOME"
    "APPDATA=$SMOKE_OS_HOME/AppData/Roaming"
    "LOCALAPPDATA=$SMOKE_OS_HOME/AppData/Local"
    "XDG_DATA_HOME=$SMOKE_OS_HOME/.local/share"
    "XDG_CONFIG_HOME=$SMOKE_OS_HOME/.config"
    "XDG_CACHE_HOME=$SMOKE_OS_HOME/.cache"
  )
  env "${isolated_env[@]}" "$bin" > "$log" 2>&1 &
  local pid=$!

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
    if [ -n "$engine_url" ] && curl -sf --max-time 2 "$engine_url/health" > "$RUN_DIR/health.json" 2>/dev/null; then
      break
    fi
    sleep 2; waited=$((waited + 2))
  done

  if [ -s "$RUN_DIR/health.json" ]; then
    record_ok "packaged: app stayed up and the engine answered /health in ${waited}s ($engine_url)"
  else
    record_fail "packaged: engine never answered /health within ${waited}s" "$(tail -30 "$log")"
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
  if [ -n "$engine_url" ] && curl -sf --max-time 5 "$engine_url/admin/status" > "$RUN_DIR/admin-status.json" 2>/dev/null; then
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
  fi

  # Existing image-generation installs may need a mandatory compatibility
  # migration after an app update. It runs in the background so engine startup
  # stays responsive, but quitting here would deliberately interrupt pip and
  # leave a partial managed runtime. If this boot started that migration, wait
  # for its terminal state and make any repair failure a release blocker.
  if [ -n "$engine_url" ] && curl -sf --max-time 5 -H "Authorization: Bearer smoke-local" "$engine_url/image-gen/install/status" > "$RUN_DIR/image-install-status.json" 2>/dev/null; then
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
        if curl -sf --max-time 5 -H "Authorization: Bearer smoke-local" "$engine_url/image-gen/install/status" > "$RUN_DIR/image-install-status.json" 2>/dev/null; then
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
          record_fail "packaged: lost the mandatory image-runtime migration while waiting (status=${image_install_status:-missing})"
          ;;
      esac
    elif [ "$image_install_status" = "error" ]; then
      image_install_error="$(node -p "require('$RUN_DIR/image-install-status.json').error || require('$RUN_DIR/image-install-status.json').message || 'no error recorded'" 2>/dev/null)"
      record_fail "packaged: image-runtime installer is in an error state" "$image_install_error"
    elif [ "$image_install_status" != "idle" ] && [ "$image_install_status" != "complete" ]; then
      record_fail "packaged: image-runtime installer returned an invalid status (${image_install_status:-missing})" "$(cat "$RUN_DIR/image-install-status.json" 2>/dev/null)"
    fi
  else
    record_fail "packaged: could not read /image-gen/install/status — background runtime migration unverified"
  fi

  # Let it settle so late-startup errors land in the log, then quit it the way
  # a user would — the app's own graceful shutdown chain is what we're testing.
  sleep 8
  info "Quitting the app and checking for orphans…"
  terminate_pid "$pid"
  local t=0
  # graceful_shutdown_sync contains a 20-second engine TERM→KILL ladder and
  # the detached ownership safety net completes at T+26s. Allow both contracts
  # to finish, with headroom for Whisper/LLM teardown, before declaring a hang.
  while pid_alive "$pid" && [ $t -lt 40 ]; do sleep 1; t=$((t + 1)); done
  if pid_alive "$pid"; then
    force_kill_pid "$pid"
    record_fail "packaged: app did not exit within 40s of a graceful quit signal (had to force-kill)"
  else
    record_ok "packaged: app exited cleanly on a graceful quit signal in ${t}s"
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
