#!/usr/bin/env bash
# Install the Claude Code pin extractor into ~/.claude, WITH the shared scope
# rule it imports.
#
# TWO FILES, and the order matters. The extractor imports the rule from
# claude_scope.py beside it; if only the script landed, it would publish no pin
# verdict at all (loudly — never a second, drifting copy of the rule). So the
# module is written FIRST, then the script.
#
# Every replaced file is kept as <name>.bak-<UTC stamp>. The launchd session-sync
# agent re-reads these on its next pass; nothing needs restarting.
#
# Prove the change before installing it:
#   ~/.claude/.sync-venv/bin/python3 scripts/claude_code_pins_extract.py \
#       --dry-run --diff
# Anything but 0 differences against the app's own starred list means STOP.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${CLAUDE_HOME:-$HOME/.claude}"
stamp="$(date -u +%Y%m%d-%H%M%S)"

install_one() {
  local src="$1" dst="$2"
  [ -f "$src" ] || { echo "missing source: $src" >&2; exit 1; }
  if [ -f "$dst" ] && ! cmp -s "$src" "$dst"; then
    cp -p "$dst" "$dst.bak-$stamp"
    echo "backed up $dst -> $dst.bak-$stamp"
  fi
  cp "$src" "$dst"
  echo "installed $dst"
}

install_one "$repo_root/app/services/coding_sessions/claude_scope.py" \
            "$target/claude_scope.py"
install_one "$repo_root/scripts/claude_code_pins_extract.py" \
            "$target/claude-code-pins-extract.py"
chmod +x "$target/claude-code-pins-extract.py"
