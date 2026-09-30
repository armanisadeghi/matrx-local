#!/usr/bin/env bash
# Dispatch the canonical release path to GitHub's hosted Linux runner.
# This script performs only lightweight local state/API checks; it never builds,
# installs dependencies, or typechecks on the caller's machine.
set -euo pipefail

REPO="armanisadeghi/matrx-local"
WORKFLOW="off-host-release.yml"
BUMP="patch"
VERSION=""
MESSAGE="release: off-host"
DRY_RUN=false

fail() {
    echo "dispatch-off-host-release: $*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --patch) BUMP="patch"; shift ;;
        --minor) BUMP="minor"; shift ;;
        --major) BUMP="major"; shift ;;
        --message|-m)
            [[ -n "${2:-}" ]] || fail "--message requires an argument"
            MESSAGE="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        [0-9]*)
            [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
                || fail "exact version must have the form X.Y.Z"
            VERSION="$1"; shift ;;
        *) fail "unsupported release argument: $1" ;;
    esac
done

if $DRY_RUN; then
    if [[ -n "$VERSION" ]]; then
        echo "dispatch-off-host-release: would dispatch exact version $VERSION from main"
    else
        echo "dispatch-off-host-release: would dispatch a $BUMP release from main"
    fi
    echo "dispatch-off-host-release: no local build, install, typecheck, commit, tag, or push"
    exit 0
fi

command -v gh >/dev/null 2>&1 || fail "GitHub CLI (gh) is required"
gh auth status >/dev/null 2>&1 || fail "GitHub CLI is not authenticated"

# Fail closed before dispatching anything that would queue a second version.
# release.yml owns platform build/publish; this workflow owns the version bump.
for workflow in release.yml "$WORKFLOW"; do
    for status in queued pending waiting in_progress requested action_required; do
        run_count="$(gh run list --repo "$REPO" --workflow "$workflow" \
            --status "$status" --limit 100 --json databaseId --jq length 2>/dev/null)" \
            || fail "could not verify the $workflow release slot"
        [[ "$run_count" =~ ^[0-9]+$ ]] \
            || fail "received an invalid $workflow release-slot response"
        if [[ "$run_count" -gt 0 ]]; then
            echo "RELEASE SLOT BUSY: $workflow has an active or queued run" >&2
            exit 75
        fi
    done
done

args=(--repo "$REPO" --ref main -f "bump=$BUMP" -f "message=$MESSAGE" \
    -f "refresh_matrx_packages=true")
if [[ -n "$VERSION" ]]; then
    args+=(-f "version=$VERSION")
fi

output="$(gh workflow run "$WORKFLOW" "${args[@]}" 2>&1)" \
    || fail "GitHub rejected the hosted release dispatch: $output"
[[ -n "$output" ]] && echo "$output"
echo "dispatch-off-host-release: hosted release requested; no heavy work ran locally"
