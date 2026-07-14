#!/usr/bin/env bash
# dev.sh — run the Python engine in DEV isolation (the sanctioned way).
#
# Since the MXL-D-043 fix, `uv run python run.py` already self-isolates
# (home ~/.matrx-dev, ports 22240-22259, orphan scan off, salted instance
# id) — this wrapper adds the multi-agent conveniences on top:
#
#   ./scripts/dev.sh              shared dev world (~/.matrx-dev)
#   ./scripts/dev.sh --fresh      throwaway PRIVATE home just for this run —
#                                 use when several agents need engines whose
#                                 local DB / settings must not interact
#   ./scripts/dev.sh --live      run in the LIVE position (~/.matrx, 22140)
#                                 on purpose. Refuses if the installed app
#                                 is running. Almost never what you want.
#
# Anything after the flags is passed through to run.py.
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="dev"
if [[ "${1:-}" == "--fresh" ]]; then
  MODE="fresh"
  shift
elif [[ "${1:-}" == "--live" ]]; then
  MODE="live"
  shift
fi

case "$MODE" in
  fresh)
    FRESH_HOME="$(mktemp -d "${TMPDIR:-/tmp}/matrx-dev-home.XXXXXX")"
    export MATRX_HOME_DIR="$FRESH_HOME"
    echo "[dev.sh] FRESH isolated home: $FRESH_HOME (deleted on your own schedule — mktemp dirs are not auto-cleaned)"
    ;;
  live)
    # Refuse to fight the installed app for the live position.
    if curl -sf --max-time 2 "http://127.0.0.1:22140/health" >/dev/null 2>&1; then
      echo "[dev.sh] REFUSING --live: something already answers /health on 22140 (the installed app?)." >&2
      echo "[dev.sh] Quit the installed app first, then re-run." >&2
      exit 1
    fi
    export MATRX_LIVE_ENGINE=1
    echo "[dev.sh] LIVE position requested — this engine will own ~/.matrx and ports 22140+."
    ;;
  dev)
    : # run.py's isolation guard does everything.
    ;;
esac

exec uv run python run.py "$@"
