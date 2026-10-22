#!/usr/bin/env bash
# ship.sh — sync this checkout with GitHub, then release. (Arman, 2026-09-24)
#
#   1. scripts/sync-main.py   commits everything uncommitted ("local work not committed by agents
#                             who made them"), merges origin/main, sorts every conflict into
#                             _conflicts/ (auto-fixed / both-versions-kept / held), pushes.
#   2. hosted release         scripts/dispatch-off-host-release.sh queues the canonical
#                             release path on GitHub Linux. No build, install, or typecheck
#                             runs on this Mac.
#   3. the open items         prints what is open in _conflicts/README.md, if anything, for the
#                             agent that resolves conflicts.
#
# Usage:
#   ./ship.sh                                   # sync + release with the default note
#   ./ship.sh "Added new chat surface"          # sync + release with a note
#   ./ship.sh "note" --minor                    # release flags pass through
#   ./ship.sh 1.5.0                             # exact version; default note
#   ./ship.sh "note" --dry-run                  # NO sync; release --dry-run only
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
export RELEASE_STAGE_CALLER_PWD="$PWD"
# THE RELEASE PATH IS NEVER BLOCKED (Arman 2026-10-02): git hooks on this run's pushes report, never refuse.
export MATRX_RELEASE_PATH=1

NOTE="sync and release"
if [[ $# -gt 0 && "$1" != --* && ! "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    NOTE="$1"
    shift
fi

DRY_RUN=false
for arg in "$@"; do [[ "$arg" == "--dry-run" ]] && DRY_RUN=true; done

RELEASE="$ROOT/scripts/dispatch-off-host-release.sh"

# ── 1. sync ──────────────────────────────────────────────────────────────────
if $DRY_RUN; then
    echo "ship.sh: --dry-run, so the sync was skipped (it commits and pushes for real)."
    SYNC_RC=0
else
    # Package catch-up runs in the hosted workflow. Keeping it out of this
    # sync prevents pnpm installation work from running on the Mac.
    python3 "$ROOT/scripts/sync-main.py" --skip-matrx-packages
    SYNC_RC=$?
    if [[ $SYNC_RC -ne 0 ]]; then
        echo ""
        echo "ship.sh: the sync did not finish (exit $SYNC_RC; its reason is printed above)."
        echo "ship.sh: releasing origin/main anyway (the release path never blocks, Arman 2026-10-02); this checkout's unpushed commits are not in it."
        python3 "$HOME/code/matrx-ship/scripts/release-scream.py" --repo matrx-local --where "ship.sh sync" \
            --check "sync-main did not push (exit $SYNC_RC)" --detail "the hosted release ships origin/main without this checkout's local commits" || true
    fi
fi

# ── 2. release ───────────────────────────────────────────────────────────────
echo ""
bash "$RELEASE" --message "$NOTE" "$@"
RELEASE_RC=$?

# ── 3. open items ────────────────────────────────────────────────────────────
echo ""
if ! $DRY_RUN; then
    if python3 "$ROOT/scripts/check-conflict-markers.py" >/tmp/ship-conflicts.$$ 2>&1; then
        echo "ship.sh: nothing open in _conflicts/."
    else
        echo "ship.sh: open items in _conflicts/README.md:"
        sed 's/^/  /' /tmp/ship-conflicts.$$
    fi
    rm -f /tmp/ship-conflicts.$$
fi

echo "ship.sh: sync exit $SYNC_RC, release exit $RELEASE_RC"
exit $RELEASE_RC
