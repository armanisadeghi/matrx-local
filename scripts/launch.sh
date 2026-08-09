#!/usr/bin/env bash
# launch.sh — Start Matrx Local (engine + desktop)
#
# Usage:
#   bash scripts/launch.sh          # normal launch
#   bash scripts/launch.sh --engine # engine only (no desktop)
#
# Safely detects any already-running Matrx processes before starting.
# Uses ~/.matrx/local.json (written by run.py) + cmdline verification
# to identify OUR processes — never kills by port alone.

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RESET='\033[0m'

ok()   { echo -e "${GREEN}  ✓${RESET}  $*"; }
info() { echo -e "${BLUE}  →${RESET}  $*"; }
warn() { echo -e "${YELLOW}  ⚠${RESET}  $*"; }
fail() { echo -e "${RED}  ✗${RESET}  $*"; }
step() { echo -e "\n${BOLD}${BLUE}$*${RESET}"; }

# ── Resolve project root ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DISCOVERY_FILE="$HOME/.matrx/local.json"
ENGINE_LOG="/tmp/matrx-engine.log"
ENGINE_ONLY=false
[[ "${1:-}" == "--engine" ]] && ENGINE_ONLY=true

# ── Detect OS ─────────────────────────────────────────────────────────────────
IS_LINUX=false
IS_MAC=false
IS_WSL=false
IS_WINDOWS=false
case "$(uname -s)" in
  Linux*)
    IS_LINUX=true
    if [[ -f /proc/version ]] && grep -qi microsoft /proc/version 2>/dev/null; then
      IS_WSL=true
    fi
    ;;
  Darwin*) IS_MAC=true ;;
  CYGWIN*|MINGW*|MSYS*) IS_WINDOWS=true ;;
esac

# ── Helpers ───────────────────────────────────────────────────────────────────

# Return the process working directory for a given PID.
# Outputs an empty string if the PID doesn't exist or we can't read its cwd.
proc_cwd() {
  local pid="$1"
  if $IS_LINUX || $IS_WSL; then
    readlink -f "/proc/$pid/cwd" 2>/dev/null || true
  elif $IS_MAC; then
    lsof -p "$pid" 2>/dev/null | awk '$4=="cwd"{print $NF}' || true
  fi
}

# Return the full cmdline string for a PID.
proc_cmdline() {
  local pid="$1"
  if $IS_LINUX || $IS_WSL; then
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true
  elif $IS_MAC; then
    ps -p "$pid" -o args= 2>/dev/null || true
  fi
}

# Find the PID listening on a given TCP port. Returns empty string if none.
pid_on_port() {
  local port="$1"
  local pid=""
  if $IS_LINUX || $IS_WSL; then
    # ss is more reliable than lsof on Linux
    pid=$(ss -tlnp "sport = :$port" 2>/dev/null \
          | grep -oP 'pid=\K[0-9]+' | head -1 || true)
    # fallback to lsof if ss didn't find it
    if [[ -z "$pid" ]] && command -v lsof &>/dev/null; then
      pid=$(lsof -ti ":$port" 2>/dev/null | head -1 || true)
    fi
  elif $IS_MAC; then
    if command -v lsof &>/dev/null; then
      pid=$(lsof -ti ":$port" 2>/dev/null | head -1 || true)
    fi
  fi
  echo "$pid"
}

# Gracefully kill a PID: SIGTERM → wait up to 5s → SIGKILL.
kill_gracefully() {
  local pid="$1"
  local label="${2:-process}"
  info "Sending SIGTERM to $label (PID $pid)..."
  kill -TERM "$pid" 2>/dev/null || true
  local i=0
  while kill -0 "$pid" 2>/dev/null && (( i < 10 )); do
    sleep 0.5
    (( i++ )) || true
  done
  if kill -0 "$pid" 2>/dev/null; then
    warn "$label did not exit cleanly — sending SIGKILL"
    kill -KILL "$pid" 2>/dev/null || true
    sleep 0.5
  fi
  ok "$label stopped"
}

# Ask yes/no. Returns 0 for yes, 1 for no.
ask_kill() {
  local prompt="$1"
  echo -e "${YELLOW}$prompt${RESET}"
  printf "  Kill it? [y/N] "
  local answer
  read -r answer </dev/tty || answer="n"
  [[ "$answer" =~ ^[Yy]$ ]]
}

# ── Engine conflict check ──────────────────────────────────────────────────────
# Identifies OUR engine by:
#   1. PID recorded in ~/.matrx/local.json
#   2. That PID's cmdline contains run.py
#   3. That run.py path is inside our $ROOT
check_and_handle_engine() {
  [[ -f "$DISCOVERY_FILE" ]] || return 0

  local json_pid json_port
  json_pid=$(python3 -c "import json,sys; d=json.load(open('$DISCOVERY_FILE')); print(d.get('pid',''))" 2>/dev/null || true)
  json_port=$(python3 -c "import json,sys; d=json.load(open('$DISCOVERY_FILE')); print(d.get('port',''))" 2>/dev/null || true)

  [[ -n "$json_pid" ]] || return 0

  # Check the PID is still alive
  kill -0 "$json_pid" 2>/dev/null || {
    # Stale discovery file — engine exited without cleaning up
    info "Stale discovery file found (PID $json_pid is gone) — removing it"
    rm -f "$DISCOVERY_FILE"
    return 0
  }

  # Verify the cmdline belongs to our run.py
  local cmdline
  cmdline=$(proc_cmdline "$json_pid")
  if [[ "$cmdline" != *"run.py"* ]]; then
    # PID was reused by something else; clean up stale file
    info "Discovery file PID $json_pid no longer points to run.py — removing stale file"
    rm -f "$DISCOVERY_FILE"
    return 0
  fi

  # Confirm it's inside our project root
  if [[ "$cmdline" != *"$ROOT"* ]]; then
    warn "A different Matrx engine is running from another location (PID $json_pid)."
    warn "Cmdline: $cmdline"
    warn "Not touching it — this does not belong to $ROOT"
    echo ""
    fail "Cannot continue: a Matrx engine from a different project root is occupying the port."
    echo ""
    echo "  To resolve this manually, run:"
    echo ""
    echo "    # Graceful stop"
    echo "    kill $json_pid"
    echo ""
    echo "    # Force kill (if the above doesn't work)"
    echo "    kill -9 $json_pid"
    echo ""
    echo "  Then re-run:  bash scripts/launch.sh"
    echo ""
    exit 1
  fi

  # It's ours
  if ask_kill "Engine is already running (PID $json_pid, port ${json_port:-?}) from this project."; then
    kill_gracefully "$json_pid" "Matrx engine"
    rm -f "$DISCOVERY_FILE"
  else
    echo "  Leaving engine running. Exiting."
    exit 0
  fi
}

# ── Desktop / Vite conflict check ─────────────────────────────────────────────
# Identifies OUR Vite server by:
#   1. PID listening on port 1420
#   2. That PID's working directory is $ROOT/desktop
VITE_PORT=1420

check_and_handle_desktop() {
  local pid
  pid=$(pid_on_port "$VITE_PORT")
  [[ -n "$pid" ]] || return 0

  # Verify it's our desktop by checking its cwd
  local cwd
  cwd=$(proc_cwd "$pid")

  if [[ "$cwd" != "$ROOT/desktop"* ]]; then
    warn "Port $VITE_PORT is in use by PID $pid (cwd: ${cwd:-unknown}), but that is NOT our desktop."
    warn "Not touching it."
    echo ""
    fail "Cannot start the desktop app: port $VITE_PORT is occupied by another process."
    echo ""
    echo "  To resolve this manually, run:"
    echo ""
    echo "    # See what's using port $VITE_PORT"
    echo "    lsof -ti :$VITE_PORT"
    echo ""
    echo "    # Graceful stop"
    echo "    kill $pid"
    echo ""
    echo "    # Force kill (if the above doesn't work)"
    echo "    kill -9 $pid"
    echo ""
    echo "  Then re-run:  bash scripts/launch.sh"
    echo ""
    exit 1
  fi

  # It's ours
  if ask_kill "Desktop dev server is already running (PID $pid, :$VITE_PORT) from this project."; then
    kill_gracefully "$pid" "Vite dev server"
    # Give the OS a moment to release the port
    sleep 1
  else
    echo "  Leaving desktop running. Exiting."
    exit 0
  fi
}

# ── Wait for engine to be healthy ─────────────────────────────────────────────
wait_for_engine() {
  local port="$1"
  local retries=120  # 120 × 0.5s = 60s timeout (scraper bootstrap can be slow on WSL)
  local i=0
  info "Waiting for engine on port $port (up to 60s)..."
  while (( i < retries )); do
    if curl -sf "http://127.0.0.1:$port/" -o /dev/null 2>/dev/null; then
      ok "Engine is up at http://127.0.0.1:$port/"
      return 0
    fi
    # Every 10s print a progress dot so the user knows it's still working
    if (( i % 20 == 19 )); then
      echo -n "  still starting (${i}s)..."$'\n'
    fi
    sleep 0.5
    (( i++ )) || true
  done
  fail "Engine did not become healthy within 60s."
  echo ""
  echo "  Last engine log:"
  tail -30 "$ENGINE_LOG" 2>/dev/null || true
  return 1
}

# Read the port the engine chose from the discovery file (written on startup).
read_engine_port() {
  local retries=20
  local i=0
  while (( i < retries )); do
    if [[ -f "$DISCOVERY_FILE" ]]; then
      local port
      port=$(python3 -c "import json; print(json.load(open('$DISCOVERY_FILE')).get('port',''))" 2>/dev/null || true)
      [[ -n "$port" ]] && echo "$port" && return 0
    fi
    sleep 0.5
    (( i++ )) || true
  done
  # Fallback to default
  echo "22140"
}

# ══════════════════════════════════════════════════════════════════════════════
# Terminal launch — platform detection and dispatch
#
# Priority order per platform:
#
#   WSL          → Windows Terminal (wt.exe) tabs  [best: real separate windows]
#                  → tmux split inside current terminal
#                  → fallback: single terminal
#
#   macOS        → Terminal.app (osascript)  [always present]
#                  → iTerm2 (osascript, if installed)
#                  → tmux split
#                  → fallback
#
#   Linux GUI    → first found: gnome-terminal / xterm / konsole /
#                                xfce4-terminal / tilix / alacritty / kitty
#                  → tmux split
#                  → fallback
#
#   Linux headless / SSH → tmux split (attach in place)
#                          → screen (detached)
#                          → fallback: single terminal
#
# The fallback always works: engine stays backgrounded, desktop runs in the
# foreground of this terminal, Ctrl+C kills both via trap.
# ══════════════════════════════════════════════════════════════════════════════

# ── Shared helpers: write temp scripts ───────────────────────────────────────
# Using temp .sh files instead of inline bash -c strings avoids all quoting
# issues when commands pass through Windows (wt.exe/cmd.exe) or AppleScript.
# Each function writes a self-contained script and echoes its path.

_write_engine_log_script() {
  local port="$1"
  local f; f=$(mktemp /tmp/matrx-engine-XXXXXX)
  cat > "$f" << SCRIPT
#!/usr/bin/env bash
echo ""
echo "── Matrx Engine Log (port ${port}) ──"
echo "Log file: ${ENGINE_LOG}"
echo ""
tail -n 40 -f "${ENGINE_LOG}"
SCRIPT
  chmod +x "$f"
  echo "$f"
}

_write_desktop_script() {
  local port="$1"
  local f; f=$(mktemp /tmp/matrx-desktop-XXXXXX)
  # When bash runs this as a plain script (not an interactive shell), .bashrc is
  # NOT sourced automatically. That means nvm, pnpm, and cargo are absent from
  # PATH — only Windows /mnt/c/... binaries are visible, which fail with
  # "Permission denied" or "exec: node: not found".
  # We bootstrap PATH explicitly using well-known Linux install locations.
  cat > "$f" << 'HEREDOC'
#!/usr/bin/env bash

# ── Bootstrap PATH for non-interactive shell ─────────────────────────────────
# Source .bashrc safely: set PS1 so interactive-only [ -z "$PS1" ] guards pass.
export PS1="matrx"
[[ -f "$HOME/.bashrc" ]] && source "$HOME/.bashrc" 2>/dev/null || true
unset PS1

# Belt-and-suspenders fallbacks for the three tools pnpm tauri:dev needs:

# 1. Rust / cargo
[[ -f "$HOME/.cargo/env" ]] && source "$HOME/.cargo/env" 2>/dev/null || true
export PATH="$HOME/.cargo/bin:$PATH"

# 2. nvm / node — load if not already active
if [[ -z "${NVM_DIR:-}" ]]; then
  export NVM_DIR="$HOME/.nvm"
  [[ -s "$NVM_DIR/nvm.sh" ]] && source "$NVM_DIR/nvm.sh" 2>/dev/null || true
fi

# 3. pnpm — if the resolved pnpm is a Windows binary (/mnt/*), prefer Linux one
if ! command -v pnpm &>/dev/null || [[ "$(command -v pnpm)" == /mnt/* ]]; then
  if [[ -f "$HOME/.local/share/pnpm/pnpm" ]]; then
    export PNPM_HOME="$HOME/.local/share/pnpm"
    export PATH="$PNPM_HOME:$PATH"
  elif command -v node &>/dev/null; then
    NODE_BIN="$(dirname "$(command -v node)")"
    [[ -f "$NODE_BIN/pnpm" ]] && export PATH="$NODE_BIN:$PATH"
  fi
fi
HEREDOC

  # Append the project-specific part (with variable expansion for $port and $ROOT)
  cat >> "$f" << SCRIPT

echo ""
echo "── Matrx Desktop (pnpm tauri:dev) ──"
echo "Engine: http://127.0.0.1:${port}/"
echo "Web UI: http://localhost:1420  (available after Vite starts, ~10s)"
echo "Note:   First Rust compile takes 60-90 seconds."
echo ""
echo "node:  \$(node --version 2>/dev/null || echo NOT FOUND)"
echo "pnpm:  \$(pnpm --version 2>/dev/null || echo NOT FOUND)"
echo "cargo: \$(cargo --version 2>/dev/null || echo NOT FOUND)"
echo ""
cd "${ROOT}/desktop"
pnpm tauri:dev
echo ""
echo "Desktop exited. Press Enter to close this window."
read -r _x
SCRIPT
  chmod +x "$f"
  echo "$f"
}

# ── WSL / Windows helpers ─────────────────────────────────────────────────────
_wsl_distro() {
  grep '^NAME=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "Ubuntu"
}

_wt_available() {
  $IS_WSL && command -v wt.exe &>/dev/null
}

# ── macOS helpers ─────────────────────────────────────────────────────────────
_iterm2_available() {
  $IS_MAC && osascript -e 'tell application "System Events" to (name of processes) contains "iTerm2"' 2>/dev/null | grep -q true
}

# ── Linux GUI helpers ─────────────────────────────────────────────────────────
_has_display() {
  [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]
}

_first_gui_terminal() {
  # Returns the first GUI terminal emulator found on PATH.
  local candidates=(gnome-terminal xterm konsole xfce4-terminal tilix alacritty kitty lxterminal mate-terminal)
  for t in "${candidates[@]}"; do
    command -v "$t" &>/dev/null && echo "$t" && return 0
  done
  return 1
}

# ── Summary printer (used by launchers that open external windows) ────────────
_print_launch_summary() {
  local port="$1"
  local method="$2"
  echo ""
  echo "  ${method} — windows/tabs opened:"
  echo "    • Matrx Engine   — live engine log"
  $ENGINE_ONLY || echo "    • Matrx Desktop  — Tauri dev build"
  echo ""
  echo "  Engine:       http://127.0.0.1:${port}/"
  echo "  API docs:     http://127.0.0.1:${port}/docs"
  $ENGINE_ONLY || echo "  Web UI:       http://localhost:1420  (ready after Vite starts, ~10s)"
  echo "  Engine log:   $ENGINE_LOG"
  echo "  Stop engine:  kill \$(python3 -c \"import json; print(json.load(open('${DISCOVERY_FILE}')).get('pid',''))\")"
}

# ══════════════════════════════════════════════════════════════════════════════
# Launcher implementations
# ══════════════════════════════════════════════════════════════════════════════

# ── WSL: Windows Terminal tabs ────────────────────────────────────────────────
launch_with_wt() {
  local port="$1"
  local distro; distro=$(_wsl_distro)

  # Write commands to temp scripts — avoids all quoting issues when args pass
  # through the Windows→WSL boundary (wt.exe → wsl.exe → bash).
  local log_script; log_script=$(_write_engine_log_script "$port")
  local desk_script; desk_script=$(_write_desktop_script "$port")

  info "Opening Windows Terminal tabs (distro: $distro)..."

  if $ENGINE_ONLY; then
    wt.exe new-tab --title "Matrx Engine" \
      wsl.exe -d "$distro" -- bash "$log_script" 2>/dev/null &
  else
    # Single wt.exe call opens both tabs atomically with ';' separator
    wt.exe new-tab --title "Matrx Engine" \
        wsl.exe -d "$distro" -- bash "$log_script" \; \
      new-tab --title "Matrx Desktop" \
        wsl.exe -d "$distro" -- bash "$desk_script" \
      2>/dev/null &
  fi

  sleep 1
  ok "Windows Terminal tabs opened."
  _print_launch_summary "$port" "Windows Terminal"
}

# ── Windows Native: start cmd ─────────────────────────────────────────────────
launch_with_windows_start() {
  local port="$1"
  local log_script; log_script=$(_write_engine_log_script "$port")
  local desk_script; desk_script=$(_write_desktop_script "$port")

  info "Opening Windows command prompts..."
  if $ENGINE_ONLY; then
    start "Matrx Engine" bash "$log_script"
  else
    start "Matrx Engine" bash "$log_script"
    start "Matrx Desktop" bash "$desk_script"
  fi
  ok "Windows prompts opened."
  _print_launch_summary "$port" "Windows start"
}

# ── macOS: iTerm2 (if running) ────────────────────────────────────────────────
launch_with_iterm2() {
  local port="$1"
  local log_script; log_script=$(_write_engine_log_script "$port")
  local desk_script; desk_script=$(_write_desktop_script "$port")

  info "Opening iTerm2 tabs..."

  osascript 2>/dev/null <<APPLESCRIPT
tell application "iTerm2"
  tell current window
    create tab with default profile
    tell current session of current tab
      write text "bash ${log_script}"
    end tell
  end tell
end tell
APPLESCRIPT

  if ! $ENGINE_ONLY; then
    osascript 2>/dev/null <<APPLESCRIPT
tell application "iTerm2"
  tell current window
    create tab with default profile
    tell current session of current tab
      write text "bash ${desk_script}"
    end tell
  end tell
end tell
APPLESCRIPT
  fi

  ok "iTerm2 tabs opened."
  _print_launch_summary "$port" "iTerm2"
}

# ── macOS: Terminal.app ───────────────────────────────────────────────────────
launch_with_terminal_app() {
  local port="$1"
  local log_script; log_script=$(_write_engine_log_script "$port")
  local desk_script; desk_script=$(_write_desktop_script "$port")

  info "Opening Terminal.app windows..."

  osascript 2>/dev/null <<APPLESCRIPT
tell application "Terminal"
  do script "bash ${log_script}"
  activate
end tell
APPLESCRIPT

  if ! $ENGINE_ONLY; then
    osascript 2>/dev/null <<APPLESCRIPT
tell application "Terminal"
  do script "bash ${desk_script}"
  activate
end tell
APPLESCRIPT
  fi

  ok "Terminal.app windows opened."
  _print_launch_summary "$port" "Terminal.app"
}

# ── Linux GUI: generic terminal emulator ─────────────────────────────────────
launch_with_gui_terminal() {
  local port="$1"
  local term="$2"
  local log_script; log_script=$(_write_engine_log_script "$port")
  local desk_script; desk_script=$(_write_desktop_script "$port")

  info "Opening ${term} windows..."

  # All terminals receive a script path — no inline quoting needed.
  _open_term_script() {
    local script="$1"
    case "$term" in
      gnome-terminal)  gnome-terminal  -- bash "$script" 2>/dev/null & ;;
      xterm)           xterm           -e  bash "$script" 2>/dev/null & ;;
      konsole)         konsole         -e  bash "$script" 2>/dev/null & ;;
      xfce4-terminal)  xfce4-terminal  -e "bash $script"  2>/dev/null & ;;
      tilix)           tilix           -e "bash $script"  2>/dev/null & ;;
      alacritty)       alacritty       -e  bash "$script" 2>/dev/null & ;;
      kitty)           kitty               bash "$script" 2>/dev/null & ;;
      lxterminal)      lxterminal      -e "bash $script"  2>/dev/null & ;;
      mate-terminal)   mate-terminal   -e "bash $script"  2>/dev/null & ;;
      *)               "$term"         -e  bash "$script" 2>/dev/null & ;;
    esac
  }

  _open_term_script "$log_script"
  sleep 0.3
  $ENGINE_ONLY || _open_term_script "$desk_script"
  sleep 0.5

  ok "${term} windows opened."
  _print_launch_summary "$port" "$term"
}

# ── tmux split panes (attach in place) ───────────────────────────────────────
launch_with_tmux() {
  local port="$1"
  local session="matrx"
  local log_script; log_script=$(_write_engine_log_script "$port")
  local desk_script; desk_script=$(_write_desktop_script "$port")

  tmux kill-session -t "$session" 2>/dev/null || true
  info "Starting tmux session '$session'..."

  tmux new-session -d -s "$session" -x 220 -y 50 "bash '$log_script'"

  if ! $ENGINE_ONLY; then
    tmux split-window -h -t "$session" "bash '$desk_script'"
    tmux select-layout -t "$session" even-horizontal 2>/dev/null || true
  fi

  ok "tmux session '$session' ready."
  echo ""
  echo -e "  ${BOLD}Ctrl+B then D${RESET} to detach (processes keep running)."
  echo -e "  ${BOLD}tmux attach -t $session${RESET} to re-attach later."
  echo ""
  sleep 0.3
  tmux attach-session -t "$session"
}

# ── screen (headless fallback when tmux unavailable) ─────────────────────────
launch_with_screen() {
  local port="$1"
  local log_script; log_script=$(_write_engine_log_script "$port")
  local desk_script; desk_script=$(_write_desktop_script "$port")

  info "Starting screen sessions..."

  screen -dmS matrx-engine bash "$log_script"
  ok "Engine log screen:  screen -r matrx-engine"

  if ! $ENGINE_ONLY; then
    screen -dmS matrx-desktop bash "$desk_script"
    ok "Desktop screen:     screen -r matrx-desktop"
  fi

  echo ""
  echo "  Both processes are running in detached screen sessions."
  echo "  Attach:       screen -r matrx-engine  /  screen -r matrx-desktop"
  echo "  Engine:       http://127.0.0.1:${port}/"
  echo "  API docs:     http://127.0.0.1:${port}/docs"
  $ENGINE_ONLY || echo "  Web UI:       http://localhost:1420  (ready after Vite starts)"
}

# ── Final fallback: single terminal ──────────────────────────────────────────
# Engine stays backgrounded; desktop runs in the foreground here.
# Ctrl+C kills both via trap.
launch_fallback() {
  local port="$1"

  if $ENGINE_ONLY; then
    info "Engine is running in the background."
    echo ""
    echo "  Engine:  http://127.0.0.1:${port}/"
    echo "  Docs:    http://127.0.0.1:${port}/docs"
    echo "  Log:     tail -f $ENGINE_LOG"
    echo "  Stop:    kill \$(python3 -c \"import json; print(json.load(open('$DISCOVERY_FILE')).get('pid',''))\")"
    echo ""
    tail -f "$ENGINE_LOG"
    return
  fi

  warn "No supported terminal launcher found — running desktop in this terminal."
  info "Engine log: $ENGINE_LOG"
  echo ""

  local engine_pid
  engine_pid=$(python3 -c "import json; print(json.load(open('$DISCOVERY_FILE')).get('pid',''))" 2>/dev/null || true)

  _cleanup_fallback() {
    echo ""
    info "Shutting down..."
    [[ -n "${engine_pid:-}" ]] && kill_gracefully "$engine_pid" "Matrx engine" 2>/dev/null || true
    rm -f "$DISCOVERY_FILE"
  }
  trap _cleanup_fallback EXIT INT TERM

  echo "  Engine:       http://127.0.0.1:${port}/"
  echo "  Web UI:       http://localhost:1420  (ready after Vite starts)"
  echo -e "  ${BOLD}Engine log:${RESET}  tail -f $ENGINE_LOG"
  echo -e "  ${BOLD}Ctrl+C${RESET} stops both engine and desktop."
  echo ""

  source "$HOME/.cargo/env" 2>/dev/null || true
  export PATH="$HOME/.cargo/bin:$PATH"
  cd "$ROOT/desktop"
  pnpm tauri:dev
}

# ── Master dispatch ───────────────────────────────────────────────────────────
launch_terminals() {
  local port="$1"

  # ── Windows Native ──────────────────────────────────────────────────────────
  if $IS_WINDOWS; then
    launch_with_windows_start "$port"
    return
  fi

  # ── WSL (Windows Subsystem for Linux) ──────────────────────────────────────
  if $IS_WSL; then
    if _wt_available; then
      launch_with_wt "$port"
      return
    fi
    # WSL without Windows Terminal: fall through to tmux/screen/fallback below
  fi

  # ── macOS ───────────────────────────────────────────────────────────────────
  if $IS_MAC; then
    if _iterm2_available; then
      launch_with_iterm2 "$port"
      return
    fi
    # Terminal.app is always present on macOS
    launch_with_terminal_app "$port"
    return
  fi

  # ── Linux (native or WSL without wt.exe) ───────────────────────────────────
  # Try a GUI terminal if a display is available
  if _has_display; then
    local gui_term
    if gui_term=$(_first_gui_terminal 2>/dev/null); then
      launch_with_gui_terminal "$port" "$gui_term"
      return
    fi
  fi

  # No GUI available (headless, SSH, no display): use multiplexers
  if command -v tmux &>/dev/null; then
    launch_with_tmux "$port"
    return
  fi

  if command -v screen &>/dev/null; then
    launch_with_screen "$port"
    return
  fi

  # Absolute last resort
  launch_fallback "$port"
}

# ── Main ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Matrx Local — Launch${RESET}"
echo "  Project: $ROOT"
$ENGINE_ONLY && echo "  Mode:    engine only"
echo ""

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  fail "uv is not installed. Run bash scripts/setup.sh first."
  exit 1
fi

if [[ ! -f "$ROOT/.env" ]]; then
  fail ".env not found. Run bash scripts/setup.sh first."
  exit 1
fi

if ! $ENGINE_ONLY; then
  if ! command -v pnpm &>/dev/null; then
    fail "pnpm is not installed. Run bash scripts/setup.sh first."
    exit 1
  fi
  if [[ ! -d "$ROOT/desktop/node_modules" ]]; then
    fail "desktop/node_modules not found. Run bash scripts/setup.sh first."
    exit 1
  fi

  # Verify a native Linux cargo is available.
  # Running pnpm tauri:dev with only a Windows cargo.exe (via WSL interop) produces
  # "Permission denied (os error 13)" deep in the Tauri build process.
  # Check ~/.cargo/bin/cargo by path first — it exists after rustup install even
  # when ~/.cargo/bin isn't in PATH yet for this shell session.
  _find_native_cargo_launch() {
    local bin=""
    if [[ -f "$HOME/.cargo/bin/cargo" ]]; then
      bin="$HOME/.cargo/bin/cargo"
    elif [[ -f "$HOME/.cargo/bin/cargo.exe" ]]; then
      bin="$HOME/.cargo/bin/cargo.exe"
    else
      bin=$(command -v cargo 2>/dev/null || true)
      if [[ -z "$bin" ]]; then
          bin=$(command -v cargo.exe 2>/dev/null || true)
      fi
    fi
    [[ -z "$bin" ]] && return 1

    local resolved
    resolved=$(readlink -f "$bin" 2>/dev/null || echo "$bin")

    if $IS_WSL; then
        local magic
        magic=$(od -A n -t x1 -N 2 "$resolved" 2>/dev/null | tr -d ' ' || true)
        if [[ "$magic" == "4d5a" ]]; then
            return 1 # It's a Windows binary
        fi
    fi
    return 0
  }

  if _find_native_cargo_launch; then
    # Ensure cargo is on PATH for the pnpm tauri:dev subprocess
    # shellcheck source=/dev/null
    source "$HOME/.cargo/env" 2>/dev/null || export PATH="$HOME/.cargo/bin:$PATH"
  else
    # Scan for Windows cargo.exe on /mnt/* PATH entries
    WINDOWS_CARGO=$(command -v cargo.exe 2>/dev/null || true)
    if [[ -z "$WINDOWS_CARGO" ]]; then
      while IFS= read -r _dir; do
        if [[ "$_dir" == /mnt/* && -f "$_dir/cargo.exe" ]]; then
          WINDOWS_CARGO="$_dir/cargo.exe"
          break
        fi
      done < <(echo "$PATH" | tr ':' '\n')
    fi
    if [[ -n "$WINDOWS_CARGO" ]]; then
      fail "Only a Windows cargo.exe was found (${WINDOWS_CARGO})."
      echo ""
      echo "  Running Windows cargo.exe from WSL causes:"
      echo "    'Permission denied (os error 13)'"
      echo "  when Tauri invokes it during the build."
      echo ""
      echo "  Run the automated setup to fix this:"
      echo "    bash scripts/setup.sh"
    else
      fail "cargo not found. Run setup first:"
      echo "    bash scripts/setup.sh"
    fi
    exit 1
  fi
fi

# ── Conflict detection ────────────────────────────────────────────────────────
step "Checking for running processes..."

check_and_handle_engine
if ! $ENGINE_ONLY; then
  check_and_handle_desktop
fi

# ── Start engine ──────────────────────────────────────────────────────────────
step "Starting engine..."

# Rotate log file (keep last run)
[[ -f "$ENGINE_LOG" ]] && mv "$ENGINE_LOG" "${ENGINE_LOG}.prev"

cd "$ROOT"

# Unset DATABASE_URL from the environment before starting the engine.
# If the developer's shell exports one (e.g. sourced from ~/.env.global), a
# component that reads it would try to reach whatever remote DB it names and
# hang lifespan startup when that server is unreachable. The project's own .env
# is the correct source; let uv/dotenv load it from there.
unset DATABASE_URL

nohup uv run python run.py > "$ENGINE_LOG" 2>&1 &
ENGINE_BG_PID=$!
disown "$ENGINE_BG_PID" 2>/dev/null || true

info "Engine started in background (PID $ENGINE_BG_PID)"
info "Log: $ENGINE_LOG"

# Wait for discovery file to appear and become healthy
ENGINE_PORT=$(read_engine_port)

if ! wait_for_engine "$ENGINE_PORT"; then
  fail "Engine failed to start. Check the log above."
  exit 1
fi

# ── Engine only mode — no desktop ─────────────────────────────────────────────
if $ENGINE_ONLY; then
  echo ""
  ok "Engine is running on port $ENGINE_PORT"
  echo ""
  echo "  API docs:  http://127.0.0.1:$ENGINE_PORT/docs"
  echo "  Health:    curl http://127.0.0.1:$ENGINE_PORT/"
  echo "  Log:       tail -f $ENGINE_LOG"
  echo "  Stop:      kill \$(python3 -c \"import json; print(json.load(open('$DISCOVERY_FILE')).get('pid',''))\")"
  echo ""
  launch_terminals "$ENGINE_PORT"
  exit 0
fi

# ── Launch desktop ────────────────────────────────────────────────────────────
step "Launching desktop app..."
echo ""
echo "  Note: First Rust compile takes 60-90 seconds."
echo ""

launch_terminals "$ENGINE_PORT"
