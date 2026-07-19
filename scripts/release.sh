#!/usr/bin/env bash
# release.sh — Bump version, commit, tag, push, and start one release workflow.
# The release commit skips push-triggered workflows; release.sh explicitly
# dispatches the tag's Release workflow after the atomic push succeeds.
#
# Source of truth: pyproject.toml
# Files kept in sync:
#   pyproject.toml
#   desktop/src-tauri/tauri.conf.json
#   desktop/src-tauri/Cargo.toml
#   desktop/package.json  (via pnpm version)
#
# Dynamic (no update needed):
#   run.py, app/api/routes.py — read via importlib.metadata
#   All renderer surfaces read __APP_VERSION__ injected by Vite from the
#   canonical pyproject.toml version via desktop/src/lib/app-version.tsx.
# 
# Usage:
#   ./scripts/release.sh              # patch bump  (default)
#   ./scripts/release.sh --patch      # patch bump
#   ./scripts/release.sh --minor      # minor bump
#   ./scripts/release.sh --major      # major bump
#   ./scripts/release.sh --message "feat: something"   # custom commit message
#   ./scripts/release.sh --dry-run    # preview without changes
#   ./scripts/release.sh --monitor    # push then poll GitHub Actions until done
#   ./scripts/release.sh --monitor-only # just monitor the latest tag (no release)
#   ./scripts/release.sh X.Y.Z       # set exact version
set -euo pipefail

VERSION_MUTATION_STARTED=false
RELEASE_COMMITTED=false
# Keep this list in sync with the push.paths-ignore release-only list in
# .github/workflows/ci.yml. The Release workflow verifies these
# commits, so starting the separate CI workflow would duplicate that work.
VERSION_FILES=(
    pyproject.toml
    desktop/src-tauri/tauri.conf.json
    desktop/src-tauri/Cargo.toml
    desktop/src-tauri/Cargo.lock
    desktop/package.json
    uv.lock
)

_restore_failed_version_bump() {
    local exit_code=$?
    if [[ "$exit_code" -ne 0 ]] && $VERSION_MUTATION_STARTED && ! $RELEASE_COMMITTED; then
        git restore --staged --worktree -- "${VERSION_FILES[@]}" 2>/dev/null || true
        echo -e "\033[1;33m[RECOVERY]\033[0m Restored version manifests and lockfiles after the failed bump." >&2
    fi
}
trap _restore_failed_version_bump EXIT

# ── Failure trap ─────────────────────────────────────────────────────────────
_on_error() {
    local exit_code=$?
    local line_no=${1:-}
    echo "" >&2
    echo -e "\033[0;31m╔══════════════════════════════════════════════════════════════╗\033[0m" >&2
    echo -e "\033[0;31m║                    RELEASE SCRIPT FAILED                    ║\033[0m" >&2
    echo -e "\033[0;31m╠══════════════════════════════════════════════════════════════╣\033[0m" >&2
    echo -e "\033[0;31m║  Exit code : ${exit_code}$(printf '%*s' $((61 - ${#exit_code})) '')║\033[0m" >&2
    [[ -n "$line_no" ]] && \
    echo -e "\033[0;31m║  Line      : ${line_no}$(printf '%*s' $((61 - ${#line_no})) '')║\033[0m" >&2
    if $RELEASE_COMMITTED; then
        echo -e "\033[0;31m║  Release commit exists; inspect push/workflow status above.  ║\033[0m" >&2
    else
        echo -e "\033[0;31m║  No version was committed, tagged, or pushed.               ║\033[0m" >&2
    fi
    echo -e "\033[0;31m╚══════════════════════════════════════════════════════════════╝\033[0m" >&2
    echo "" >&2
}
trap '_on_error $LINENO' ERR

# ── Resolve repo root ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PROJECT_NAME="matrx-local"
GITHUB_REPO="armanisadeghi/matrx-local"
VERSION_FILE="pyproject.toml"
REMOTE="origin"
BRANCH="main"

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()    { echo -e "${RED}[FAIL]${NC}  $*" >&2; exit 1; }
preview() { echo -e "${YELLOW}[DRY]${NC}   $*"; }

# Like fail(), but for failures AFTER the release commit + tag were created.
# Clears the ERR trap so the generic "nothing was committed" box does not print
# (it would be a lie — the release exists locally, it just was not pushed).
die_after_commit() {
    trap - ERR
    echo "" >&2
    echo -e "${RED}╔══════════════════════════════════════════════════════════════╗${NC}" >&2
    echo -e "${RED}║               RELEASE INCOMPLETE — SEE BELOW              ║${NC}" >&2
    echo -e "${RED}╚══════════════════════════════════════════════════════════════╝${NC}" >&2
    echo "" >&2
    echo -e "$*" >&2
    echo "" >&2
    exit 1
}

# Print a side-by-side summary of how local and remote have diverged.
diverge_summary() {
    echo "  Your commits not on $REMOTE/$BRANCH:" >&2
    git log --oneline "$REMOTE/$BRANCH..$BRANCH" | sed 's/^/    /' >&2
    echo "  $REMOTE/$BRANCH commits not in your branch:" >&2
    git log --oneline "$BRANCH..$REMOTE/$BRANCH" | sed 's/^/    /' >&2
}

# ── Portable in-place sed ────────────────────────────────────────────────────
sedi() {
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}

# ── Preflight: check gh CLI is available ──────────────────────────────
require_gh() {
    if ! command -v gh &>/dev/null; then
        echo ""
        echo -e "${RED}╔══════════════════════════════════════════════════════════════╗${NC}" >&2
        echo -e "${RED}║              GitHub CLI (gh) is not installed                ║${NC}" >&2
        echo -e "${RED}╠══════════════════════════════════════════════════════════════╣${NC}" >&2
        echo -e "${RED}║  Releases require gh to start the single Release workflow.  ║${NC}" >&2
        echo -e "${RED}║  --monitor and --monitor-only also use it to poll status.    ║${NC}" >&2
        echo -e "${RED}║                                                              ║${NC}" >&2
        echo -e "${RED}║  Install on macOS:                                           ║${NC}" >&2
        echo -e "${RED}║    brew install gh                                           ║${NC}" >&2
        echo -e "${RED}║                                                              ║${NC}" >&2
        echo -e "${RED}║  Install on Ubuntu/Debian/WSL:                               ║${NC}" >&2
        echo -e "${RED}║    sudo apt install gh                                       ║${NC}" >&2
        echo -e "${RED}║   (or: sudo apt-get install gh)                              ║${NC}" >&2
        echo -e "${RED}║                                                              ║${NC}" >&2
        echo -e "${RED}║  After installing, authenticate with:                        ║${NC}" >&2
        echo -e "${RED}║    gh auth login                                             ║${NC}" >&2
        echo -e "${RED}╚══════════════════════════════════════════════════════════════╝${NC}" >&2
        echo ""
        return 1
    fi
    if ! gh auth status &>/dev/null; then
        echo ""
        echo -e "${YELLOW}[WARN]${NC}  gh is installed but not authenticated." >&2
        echo -e "        Run ${CYAN}gh auth login${NC} to authenticate, then retry." >&2
        echo -e "        GitHub Actions: ${CYAN}https://github.com/${GITHUB_REPO}/actions${NC}" >&2
        echo ""
        return 1
    fi
    return 0
}

# ── Monitor function ────────────────────────────────────────────────────────
monitor_build() {
    local tag="$1"
    local version="$2"
    local repo="$3"
    local start_epoch
    start_epoch=$(date +%s)

    # Build job keys and labels — 1 verification gate, 4 platform builds,
    # and 1 rename/publish post-job.
    local VERIFY_KEY="verify"
    local VERIFY_LABEL="Verification"
    local PLATFORM_KEYS=("aarch64-apple-darwin" "x86_64-apple-darwin" "ubuntu" "windows")
    local PLATFORM_LABELS=("macOS ARM" "macOS x86" "Linux" "Windows")
    local POST_JOB_KEY="rename-assets"
    local POST_JOB_LABEL="Rename assets"

    # ── Status icon helper ──────────────────────────────────────────────
    status_icon() {
        local status="$1" conclusion="$2"
        if [[ "$status" == "completed" ]]; then
            case "$conclusion" in
                success)   echo -e "${GREEN}✅${NC}" ;;
                failure)   echo -e "${RED}❌${NC}" ;;
                cancelled) echo -e "⚪" ;;
                skipped)   echo -e "⏭️" ;;
                *)         echo -e "${RED}❌${NC}" ;;
            esac
        elif [[ "$status" == "in_progress" ]]; then
            echo -e "${YELLOW}🔨${NC}"
        else
            echo -e "${CYAN}🔵${NC}"
        fi
    }

    # ── Status color helper ─────────────────────────────────────────────
    status_color() {
        local status="$1" conclusion="$2"
        if [[ "$status" == "completed" ]]; then
            case "$conclusion" in
                success) echo "$GREEN" ;;
                failure) echo "$RED" ;;
                *)       echo "$YELLOW" ;;
            esac
        elif [[ "$status" == "in_progress" ]]; then
            echo "$YELLOW"
        else
            echo "$CYAN"
        fi
    }

    # ── Current step helper ─────────────────────────────────────────────
    current_step() {
        local jobs_json="$1" job_index="$2"
        # Find the last in_progress step, or the last completed step
        local step
        step=$(echo "$jobs_json" | jq -r ".jobs[$job_index].steps[] | select(.status==\"in_progress\") | .name" 2>/dev/null | tail -1)
        if [[ -z "$step" ]]; then
            step=$(echo "$jobs_json" | jq -r ".jobs[$job_index].steps[] | select(.status==\"completed\") | .name" 2>/dev/null | tail -1)
        fi
        echo "${step:-waiting...}"
    }

    # ── Fetch and print tail logs for a failed job ───────────────────────
    # Strategy: first failure gets a full 60-line tail (the real diagnostic).
    # Subsequent failures get only ##[error] lines — if they all fail the same
    # way (common in matrix builds) this avoids drowning in repeated output.
    fetch_failure_logs() {
        local repo="$1" run_id="$2" jobs_json="$3"
        local LOG_TAIL=60
        local first_failure=true

        echo ""
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${RED}  📋 FAILURE LOGS${NC}"
        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

        # Build a combined list: platform keys+labels + rename post-job
        local all_keys=("$VERIFY_KEY" "${PLATFORM_KEYS[@]}" "$POST_JOB_KEY")
        local all_labels=("$VERIFY_LABEL" "${PLATFORM_LABELS[@]}" "$POST_JOB_LABEL")

        local i
        for i in "${!all_keys[@]}"; do
            local key="${all_keys[$i]}"
            local label="${all_labels[$i]}"

            local job_index
            job_index=$(echo "$jobs_json" | jq -r \
                "[.jobs[].name] | to_entries[] | select(.value | test(\"$key\")) | .key" 2>/dev/null | head -1)
            [[ -z "$job_index" ]] && continue

            local j_conclusion
            j_conclusion=$(echo "$jobs_json" | jq -r ".jobs[$job_index].conclusion // \"\"")
            [[ "$j_conclusion" == "success" || "$j_conclusion" == "skipped" ]] && continue

            local job_id
            job_id=$(echo "$jobs_json" | jq -r ".jobs[$job_index].databaseId // .jobs[$job_index].id // \"\"")
            [[ -z "$job_id" ]] && continue

            local raw_log
            raw_log=$(gh run view "$run_id" --repo "$repo" --job "$job_id" --log 2>/dev/null \
                | sed 's/^[^\t]*\t[^\t]*\t//')

            echo ""
            if $first_failure; then
                echo -e "${BOLD}  ── ${label} — last ${LOG_TAIL} lines ────────────────────────────${NC}"
                echo ""
                echo "$raw_log" | tail -"$LOG_TAIL" | sed "s/^/    /" \
                    || echo -e "    ${YELLOW}(could not fetch logs — check the URL above)${NC}"
                first_failure=false
            else
                # Extract only error/warning annotation lines for subsequent failures
                local errors
                errors=$(echo "$raw_log" | grep -E "##\[error\]|##\[warning\]|^error\[|^\s+error:" | head -20)
                echo -e "${BOLD}  ── ${label} — errors only (same root cause?) ────────────────${NC}"
                echo ""
                if [[ -n "$errors" ]]; then
                    echo "$errors" | sed "s/^/    /"
                else
                    echo "    (no ##[error] lines found — may be same failure as above)"
                fi
            fi
            echo ""
        done

        echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
    }

    # ── Wait for workflow run to appear ─────────────────────────────────
    echo ""
    info "Waiting for GitHub Actions workflow to start for tag ${BOLD}${tag}${NC}..."
    local run_id=""
    for attempt in $(seq 1 24); do  # up to ~2 minutes
        run_id=$(
            gh run list --repo "$repo" --limit 5 \
                --json databaseId,headBranch,name,status \
            | jq -r ".[] | select(.headBranch==\"$tag\" and .name==\"Release\") | .databaseId" \
            | head -1
        )
        [[ -n "$run_id" ]] && break
        sleep 5
    done

    if [[ -z "$run_id" ]]; then
        warn "Could not find a workflow run for tag ${tag} after 2 minutes."
        echo -e "  Check manually: ${CYAN}https://github.com/${repo}/actions${NC}"
        return 1
    fi

    ok "Found workflow run ${BOLD}#${run_id}${NC}"
    echo ""

    # Track which failed jobs we've already printed logs for so we
    # print each failure exactly once, immediately when detected.
    local reported_failures=""
    local build_failed=false
    # When true, skip the screen-clear so printed failure logs remain visible.
    local preserve_screen=false

    # ── Poll loop ───────────────────────────────────────────────────────
    local all_done=false
    while ! $all_done; do
        local now_epoch
        now_epoch=$(date +%s)
        local elapsed=$(( now_epoch - start_epoch ))
        local mins=$(( elapsed / 60 ))
        local secs=$(( elapsed % 60 ))
        local elapsed_str
        elapsed_str=$(printf "%02d:%02d" "$mins" "$secs")

        # Fetch job data. Never turn a GitHub API failure into an empty,
        # apparently successful run.
        local jobs_json
        if ! jobs_json=$(gh run view "$run_id" --repo "$repo" --json jobs,status,conclusion 2>/dev/null); then
            warn "Could not fetch job status from GitHub; retrying in 15s."
            sleep 15
            continue
        fi
        local job_count
        job_count=$(echo "$jobs_json" | jq '.jobs | length')

        # Clear screen unless we just printed failure logs that the user should keep reading.
        if ! $preserve_screen; then
            printf '\033[2J\033[H'
        else
            echo ""
            echo -e "  ${RED}─────────────────────────────────────────────────────────────${NC}"
            echo -e "  ${RED}  ↑ FAILURE LOGS ABOVE  |  CURRENT STATUS BELOW  ↑${NC}"
            echo -e "  ${RED}─────────────────────────────────────────────────────────────${NC}"
        fi
        preserve_screen=false
        echo ""
        echo -e "${BOLD}  📦 ${PROJECT_NAME} ${version} — Build Monitor${NC}"
        echo -e "  ─────────────────────────────────────────────────────────────"
        echo -e "  Tag: ${GREEN}${tag}${NC}    Elapsed: ${CYAN}${elapsed_str}${NC}    Jobs: ${BOLD}${job_count}/6${NC}"
        echo -e "  ─────────────────────────────────────────────────────────────"
        echo ""

        local completed_count=0
        local any_failed=false
        local failed_platforms=()
        local new_failures=()

        if [[ "$job_count" -eq 0 ]]; then
            echo -e "  ${CYAN}🔵 Waiting for jobs to be created...${NC}"
        else
            # ── Pre-release verification gate ──────────────────────────
            local verify_index
            verify_index=$(echo "$jobs_json" | jq -r \
                '[.jobs[].name] | to_entries[] | select(.value=="verify") | .key' 2>/dev/null | head -1)

            local padded_verify
            padded_verify=$(printf '%-14s' "$VERIFY_LABEL")
            if [[ -z "$verify_index" ]]; then
                echo -e "  🔵 ${CYAN}${padded_verify}${NC}  waiting..."
            else
                local v_status v_conclusion v_icon v_color v_step
                v_status=$(echo "$jobs_json" | jq -r ".jobs[$verify_index].status")
                v_conclusion=$(echo "$jobs_json" | jq -r ".jobs[$verify_index].conclusion // \"\"")
                v_icon=$(status_icon "$v_status" "$v_conclusion")
                v_color=$(status_color "$v_status" "$v_conclusion")
                v_step=$(current_step "$jobs_json" "$verify_index")
                [[ ${#v_step} -gt 40 ]] && v_step="${v_step:0:37}..."
                echo -e "  ${v_icon} ${v_color}${padded_verify}${NC}  ${v_step}"

                if [[ "$v_status" == "completed" ]]; then
                    completed_count=$((completed_count + 1))
                    if [[ "$v_conclusion" != "success" ]]; then
                        any_failed=true
                        failed_platforms+=("$VERIFY_LABEL")
                        if [[ "$reported_failures" != *"|${VERIFY_KEY}|"* ]]; then
                            new_failures+=("$VERIFY_KEY")
                            reported_failures="${reported_failures}|${VERIFY_KEY}|"
                        fi
                    fi
                fi
            fi

            # ── Platform build jobs ─────────────────────────────────────
            local i
            for i in 0 1 2 3; do
                local key="${PLATFORM_KEYS[$i]}"
                local label="${PLATFORM_LABELS[$i]}"
                local padded_label
                padded_label=$(printf '%-14s' "$label")

                local job_index
                job_index=$(echo "$jobs_json" | jq -r \
                    "[.jobs[].name] | to_entries[] | select(.value | test(\"$key\")) | .key" 2>/dev/null | head -1)

                if [[ -z "$job_index" ]]; then
                    echo -e "  🔵 ${CYAN}${padded_label}${NC}  waiting..."
                    continue
                fi

                local j_status j_conclusion
                j_status=$(echo "$jobs_json" | jq -r ".jobs[$job_index].status")
                j_conclusion=$(echo "$jobs_json" | jq -r ".jobs[$job_index].conclusion // \"\"")

                local icon color step
                icon=$(status_icon "$j_status" "$j_conclusion")
                color=$(status_color "$j_status" "$j_conclusion")
                step=$(current_step "$jobs_json" "$job_index")

                [[ ${#step} -gt 40 ]] && step="${step:0:37}..."
                echo -e "  ${icon} ${color}${padded_label}${NC}  ${step}"

                if [[ "$j_status" == "completed" ]]; then
                    completed_count=$((completed_count + 1))
                    if [[ "$j_conclusion" != "success" ]]; then
                        any_failed=true
                        failed_platforms+=("$label")
                        if [[ "$reported_failures" != *"|${key}|"* ]]; then
                            new_failures+=("$key")
                            reported_failures="${reported_failures}|${key}|"
                        fi
                    fi
                fi
            done

            # ── Post-build: rename-assets job ────────────────────────────
            local rename_index
            rename_index=$(echo "$jobs_json" | jq -r \
                "[.jobs[].name] | to_entries[] | select(.value | test(\"${POST_JOB_KEY}\")) | .key" 2>/dev/null | head -1)

            if [[ -z "$rename_index" ]]; then
                local padded_post
                padded_post=$(printf '%-14s' "$POST_JOB_LABEL")
                if [[ "$completed_count" -ge 4 ]]; then
                    echo -e "  🔵 ${CYAN}${padded_post}${NC}  waiting for builds..."
                fi
            else
                local r_status r_conclusion
                r_status=$(echo "$jobs_json" | jq -r ".jobs[$rename_index].status")
                r_conclusion=$(echo "$jobs_json" | jq -r ".jobs[$rename_index].conclusion // \"\"")

                local r_icon r_color r_step
                r_icon=$(status_icon "$r_status" "$r_conclusion")
                r_color=$(status_color "$r_status" "$r_conclusion")
                r_step=$(current_step "$jobs_json" "$rename_index")

                local padded_post
                padded_post=$(printf '%-14s' "$POST_JOB_LABEL")
                [[ ${#r_step} -gt 40 ]] && r_step="${r_step:0:37}..."
                echo -e "  ${r_icon} ${r_color}${padded_post}${NC}  ${r_step}"

                if [[ "$r_status" == "completed" ]]; then
                    completed_count=$((completed_count + 1))
                    if [[ "$r_conclusion" != "success" ]]; then
                        any_failed=true
                        failed_platforms+=("$POST_JOB_LABEL")
                        if [[ "$reported_failures" != *"|${POST_JOB_KEY}|"* ]]; then
                            new_failures+=("$POST_JOB_KEY")
                            reported_failures="${reported_failures}|${POST_JOB_KEY}|"
                        fi
                    fi
                fi
            fi
        fi

        echo ""
        echo -e "  ─────────────────────────────────────────────────────────────"

        # ── Immediately print logs for newly-detected failures ───────
        # First new failure: full 60-line tail.  Subsequent ones: errors only.
        if [[ ${#new_failures[@]} -gt 0 ]]; then
            local LOG_TAIL=60
            local inline_first=true
            for fail_key in "${new_failures[@]}"; do
                local fail_label="$fail_key"
                local all_keys=("$VERIFY_KEY" "${PLATFORM_KEYS[@]}" "$POST_JOB_KEY")
                local all_labels=("$VERIFY_LABEL" "${PLATFORM_LABELS[@]}" "$POST_JOB_LABEL")
                local fi_idx
                for fi_idx in "${!all_keys[@]}"; do
                    if [[ "${all_keys[$fi_idx]}" == "$fail_key" ]]; then
                        fail_label="${all_labels[$fi_idx]}"
                        break
                    fi
                done

                local fail_job_index
                fail_job_index=$(echo "$jobs_json" | jq -r \
                    "[.jobs[].name] | to_entries[] | select(.value | test(\"$fail_key\")) | .key" 2>/dev/null | head -1)
                [[ -z "$fail_job_index" ]] && continue

                local fail_job_id
                fail_job_id=$(echo "$jobs_json" | jq -r ".jobs[$fail_job_index].databaseId // .jobs[$fail_job_index].id // \"\"")
                [[ -z "$fail_job_id" ]] && continue

                local raw_log
                raw_log=$(gh run view "$run_id" --repo "$repo" --job "$fail_job_id" --log 2>/dev/null \
                    | sed 's/^[^\t]*\t[^\t]*\t//')

                echo ""
                echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
                if $inline_first; then
                    echo -e "${RED}  ❌ ${fail_label} FAILED — last ${LOG_TAIL} lines:${NC}"
                    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
                    echo ""
                    echo "$raw_log" | tail -"$LOG_TAIL" | sed "s/^/    /" \
                        || echo -e "    ${YELLOW}(could not fetch logs — check GitHub Actions)${NC}"
                    inline_first=false
                else
                    echo -e "${RED}  ❌ ${fail_label} FAILED — errors only:${NC}"
                    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
                    echo ""
                    local errs
                    errs=$(echo "$raw_log" | grep -E "##\[error\]|##\[warning\]" | head -15)
                    if [[ -n "$errs" ]]; then
                        echo "$errs" | sed "s/^/    /"
                    else
                        echo "    (likely same failure as above)"
                    fi
                fi
                echo ""
                echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
            done
            echo ""
            echo -e "  ${CYAN}Other builds still in progress. Monitoring continues...${NC}"
            echo -e "  ${CYAN}Debug: https://github.com/${repo}/actions/runs/${run_id}${NC}"
        fi

        # Read overall status and conclusion from the same API snapshot as the
        # jobs above. A second request can observe the run as completed before
        # the first response's jobs list catches up, falsely reporting 5/6.
        local run_status run_conclusion
        run_status=$(echo "$jobs_json" | jq -r '.status // "unknown"')
        run_conclusion=$(echo "$jobs_json" | jq -r '.conclusion // ""')

        # The workflow conclusion is authoritative once GitHub marks the run
        # complete. Its final job verifies the published release and assets, so
        # a successful conclusion must not be downgraded by stale job details.
        if [[ "$run_status" == "completed" && "$run_conclusion" != "success" && "$any_failed" == "false" ]]; then
            any_failed=true
            failed_platforms+=("Workflow conclusion: ${run_conclusion:-unknown}")
        fi

        if [[ "$run_status" == "completed" ]] || [[ "$completed_count" -ge 6 && "$job_count" -ge 6 ]]; then
            all_done=true

            # Final elapsed time
            now_epoch=$(date +%s)
            elapsed=$(( now_epoch - start_epoch ))
            mins=$(( elapsed / 60 ))
            secs=$(( elapsed % 60 ))
            elapsed_str=$(printf "%02d:%02d" "$mins" "$secs")

            echo ""
            if $any_failed; then
                build_failed=true
                echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
                echo -e "${RED}  ❌ BUILD FAILED  (${elapsed_str})${NC}"
                echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
                echo ""
                echo -e "  Failed platforms:"
                for fp in "${failed_platforms[@]}"; do
                    echo -e "    ${RED}✗${NC} ${fp}"
                done
                echo ""
                echo -e "  Debug: ${CYAN}https://github.com/${repo}/actions/runs/${run_id}${NC}"
                fetch_failure_logs "$repo" "$run_id" "$jobs_json"
            else
                local RELEASE_URL="https://github.com/${repo}/releases/tag/${tag}"
                echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
                echo -e "${GREEN}  ✅ ALL BUILDS PASSED  (${elapsed_str})${NC}"
                echo -e "${GREEN}  🚀 Release is LIVE!${NC}"
                echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
                echo ""
                echo -e "  🔗 ${BOLD}${RELEASE_URL}${NC}"
                echo ""
                echo -e "  Mac Trick: ${CYAN}xattr -cr '/Applications/AI Matrx.app'${NC}"
            fi
            echo ""
        else
            # If we just printed failure logs, preserve the screen on the next
            # iteration so the logs remain visible above the refreshed status table.
            if [[ ${#new_failures[@]} -gt 0 ]]; then
                preserve_screen=true
                echo ""
                echo -e "  ${CYAN}Refreshing in 30s (failure logs above will remain visible)...${NC}"
                echo -e "  ${CYAN}Press Ctrl-C to stop monitoring.${NC}"
                echo ""
                sleep 30
            else
                echo -e "  ${CYAN}Refreshing in 15s...  Press Ctrl-C to stop monitoring.${NC}"
                echo ""
                sleep 15
            fi
        fi
    done

    if $build_failed; then
        return 1
    fi
}

# ── Parse flags ──────────────────────────────────────────────────────────────
BUMP_TYPE="patch"
CUSTOM_MESSAGE=""
DRY_RUN=false
MONITOR=false
MONITOR_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --patch)   BUMP_TYPE="patch"; shift ;;
        --minor)   BUMP_TYPE="minor"; shift ;;
        --major)   BUMP_TYPE="major"; shift ;;
        --message|-m)
            [[ -n "${2:-}" ]] || fail "--message requires an argument."
            CUSTOM_MESSAGE="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --monitor) MONITOR=true; shift ;;
        --monitor-only) MONITOR_ONLY=true; shift ;;
        -h|--help)
            sed -n '2,/^set -euo pipefail$/p' "$0" | sed -E '$d; s/^# ?//'
            exit 0 ;;
        [0-9]*)    BUMP_TYPE="exact"; EXACT_VERSION="$1"; shift ;;
        *) fail "Unknown flag: $1. Use --patch, --minor, --major, --message, --monitor, --monitor-only, --dry-run, or X.Y.Z." ;;
    esac
done

# ── Monitor-only shortcut ────────────────────────────────────────────────────
if $MONITOR_ONLY; then
    require_gh || exit 1
    [[ -f "$VERSION_FILE" ]] || fail "$VERSION_FILE not found."
    CURRENT_VERSION=$(grep -m1 '^version' "$VERSION_FILE" | sed 's/.*"\(.*\)".*/\1/')
    [[ -n "$CURRENT_VERSION" ]] || fail "Could not read version from $VERSION_FILE."
    LATEST_TAG="v${CURRENT_VERSION}"
    info "Monitor-only mode — watching builds for ${BOLD}${LATEST_TAG}${NC}"
    monitor_build "$LATEST_TAG" "$CURRENT_VERSION" "$GITHUB_REPO"
    exit $?
fi

# ── Pre-flight checks ────────────────────────────────────────────────────────
# The release is dispatched explicitly so its push can skip the redundant CI
# workflow. Fail before changing anything if that dispatch is unavailable.
if ! $DRY_RUN; then
    require_gh || exit 1
fi

[[ -f "$VERSION_FILE" ]] || fail "$VERSION_FILE not found."

for required_tool in pnpm uv cargo; do
    command -v "$required_tool" &>/dev/null \
        || fail "$required_tool is required for a recoverable release. Install it before retrying."
done

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] \
    || fail "Not on '$BRANCH' branch (currently on '$CURRENT_BRANCH'). Switch first."

if [[ -n "$(git diff --cached --name-only)" ]]; then
    fail "Staged but uncommitted changes detected. Commit or unstage them first."
fi

if ! git diff --quiet; then
    fail "Uncommitted changes detected. Commit them first."
fi

if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    fail "Untracked files detected. Commit or remove them so required release files cannot be omitted."
fi

# ── Sync with remote (do-no-harm: runs BEFORE any commit/tag is created) ──────
# Nothing has been bumped, committed, or tagged yet, so any abort here leaves
# the working tree exactly as the user left it. We only proceed past this block
# if the local branch is in a state that will push cleanly.
info "Fetching $REMOTE/$BRANCH to check sync state..."
git fetch "$REMOTE" "$BRANCH" 2>/dev/null \
    || fail "Could not reach $REMOTE. Check your connection, then re-run. Nothing has been changed."

LOCAL_SHA=$(git rev-parse "$BRANCH")
REMOTE_SHA=$(git rev-parse "$REMOTE/$BRANCH")
BASE_SHA=$(git merge-base "$BRANCH" "$REMOTE/$BRANCH")

if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
    ok "Already in sync with $REMOTE/$BRANCH."
elif [[ "$LOCAL_SHA" == "$BASE_SHA" ]]; then
    # Local is strictly behind remote — fast-forward is safe and lossless.
    if $DRY_RUN; then
        preview "$REMOTE/$BRANCH is ahead — would fast-forward local $BRANCH."
    else
        info "$REMOTE/$BRANCH is ahead. Fast-forwarding local $BRANCH..."
        git merge --ff-only "$REMOTE/$BRANCH" >/dev/null 2>&1 \
            || fail "Fast-forward unexpectedly failed. Resolve manually. Nothing has been changed."
        ok "Fast-forwarded to $(git rev-parse --short HEAD)."
    fi
elif [[ "$REMOTE_SHA" == "$BASE_SHA" ]]; then
    # Remote is strictly behind — local is purely ahead, a normal push will work.
    ok "Local is ahead of $REMOTE/$BRANCH by $(git rev-list --count "$REMOTE/$BRANCH..$BRANCH") commit(s) — ready to release."
else
    # Diverged. Try a clean rebase of local commits onto remote. If it would
    # conflict, abort and tell the user — never force, never half-finish.
    if $DRY_RUN; then
        # Probe whether a clean rebase is possible without mutating anything.
        if git merge-tree --write-tree "$REMOTE/$BRANCH" "$BRANCH" >/dev/null 2>&1; then
            preview "Diverged from $REMOTE/$BRANCH — a clean rebase looks possible; would rebase."
        else
            warn "Diverged from $REMOTE/$BRANCH — a rebase would likely conflict; would abort and ask you to resolve."
        fi
    else
        warn "Local and $REMOTE/$BRANCH have diverged. Attempting a clean rebase..."
        if git rebase "$REMOTE/$BRANCH" >/dev/null 2>&1; then
            ok "Clean rebase succeeded — linear history restored on top of $REMOTE/$BRANCH."
        else
            git rebase --abort >/dev/null 2>&1 || true
            echo "" >&2
            diverge_summary
            echo "" >&2
            fail "$(cat <<EOF
Diverged from $REMOTE/$BRANCH and an automatic rebase would hit conflicts.
Nothing has been changed — your tree is exactly as you left it.

Resolve by hand, then re-run this script:
    git rebase $REMOTE/$BRANCH      # fix the conflicts
    ./scripts/release.sh            # re-run the release
EOF
)"
        fi
    fi
fi

# ── Ensure cloudflared sidecar binary exists for current platform ─────────────
info "Ensuring cloudflared sidecar binary is present..."
if [[ -f "scripts/download-cloudflared.sh" ]]; then
    chmod +x scripts/download-cloudflared.sh
    ./scripts/download-cloudflared.sh --current
    ok "cloudflared sidecar ready."
else
    warn "scripts/download-cloudflared.sh not found — skipping cloudflared download."
fi

# ── Ensure llama-server sidecar binary exists for current platform ────────────
info "Ensuring llama-server sidecar binary is present..."
if [[ -f "scripts/download-llama-server.sh" ]]; then
    chmod +x scripts/download-llama-server.sh
    ./scripts/download-llama-server.sh --current
    ok "llama-server sidecar ready."
else
    warn "scripts/download-llama-server.sh not found — skipping llama-server download."
fi

# ── pnpm lockfile freshness check ────────────────────────────────────────────
# CI runs `pnpm install --frozen-lockfile` and will fail if pnpm-lock.yaml is
# out of sync with package.json. Catch this locally before pushing.
info "Checking pnpm-lock.yaml is up to date with package.json..."
LOCKFILE_CHECK=$(cd desktop && pnpm install --frozen-lockfile 2>&1) || {
    echo "$LOCKFILE_CHECK" >&2
    fail "pnpm-lock.yaml is out of sync or install failed. Fix and commit it before releasing."
}
ok "pnpm-lock.yaml is up to date."

# pyproject.toml is the one release authority. The other manifests are derived
# copies required by their respective toolchains; never begin a release from a
# drifted tree.
info "Checking release version manifests agree with pyproject.toml..."
./scripts/check-version-sync.sh || fail "Version manifests have drifted. Sync them before releasing."
ok "Version manifests are synchronized."

# ── TypeScript type-check ────────────────────────────────────────────────────
info "Running TypeScript type-check (pnpm tsc -b)..."
if ! (cd desktop && pnpm tsc -b 2>&1); then
    echo ""
    fail "TypeScript errors detected. Fix them before releasing (shown above)."
fi
ok "TypeScript check passed."

# Downloads, installs, and typechecking must not silently create source changes
# that the explicit release staging list would omit.
if [[ -n "$(git diff --cached --name-only)" ]] || ! git diff --quiet \
    || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    fail "Release preparation changed the worktree. Review and commit those files before retrying."
fi

# ── Read current version ─────────────────────────────────────────────────────
CURRENT_VERSION=$(grep -m1 '^version' "$VERSION_FILE" | sed 's/.*"\(.*\)".*/\1/')
[[ -n "$CURRENT_VERSION" ]] || fail "Could not read version from $VERSION_FILE."

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

# ── Calculate new version ────────────────────────────────────────────────────
case "$BUMP_TYPE" in
    patch) NEW_VERSION="${MAJOR}.${MINOR}.$((PATCH + 1))" ;;
    minor) NEW_VERSION="${MAJOR}.$((MINOR + 1)).0" ;;
    major) NEW_VERSION="$((MAJOR + 1)).0.0" ;;
    exact) NEW_VERSION="$EXACT_VERSION" ;;
esac

NEW_TAG="v${NEW_VERSION}"

# ── Check tag doesn't already exist ──────────────────────────────────────────
if git rev-parse "$NEW_TAG" &>/dev/null; then
    fail "Tag $NEW_TAG already exists. Resolve manually or choose a different bump type."
fi

# ── Build commit message ─────────────────────────────────────────────────────
if [[ -n "$CUSTOM_MESSAGE" ]]; then
    COMMIT_MSG="$CUSTOM_MESSAGE"
else
    COMMIT_MSG="release: ${NEW_TAG}"
fi
# GitHub applies skip markers only to push/pull_request events. This suppresses
# both push-triggered workflows (CI for the branch and Release for the tag),
# after which we dispatch exactly one Release workflow for NEW_TAG below.
if [[ "$COMMIT_MSG" != *"[skip actions]"* ]]; then
    COMMIT_MSG="${COMMIT_MSG} [skip actions]"
fi

# ── Preview ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  ${PROJECT_NAME} release${NC}"
echo -e "  ─────────────────────────────────────────────"
echo -e "  Bump type  : ${CYAN}${BUMP_TYPE}${NC}"
echo -e "  Old version: ${YELLOW}${CURRENT_VERSION}${NC}"
echo -e "  New version: ${GREEN}${NEW_VERSION}${NC}"
echo -e "  Tag        : ${GREEN}${NEW_TAG}${NC}"
echo -e "  Commit msg : ${CYAN}${COMMIT_MSG}${NC}"
$DRY_RUN && echo -e "  Mode       : ${YELLOW}DRY RUN — nothing will be changed${NC}"
$MONITOR  && echo -e "  Monitor    : ${CYAN}Will poll GitHub Actions every 15s${NC}"
echo -e "  ─────────────────────────────────────────────"
echo ""

if $DRY_RUN; then
    preview "Would check: pnpm-lock.yaml freshness (auto-fix and stage if stale)"
    preview "Would run: pnpm tsc -b (TypeScript check)"
    preview "Would update $VERSION_FILE: $CURRENT_VERSION → $NEW_VERSION"
    preview "Would update desktop/src-tauri/tauri.conf.json"
    preview "Would update desktop/src-tauri/Cargo.toml"
    preview "Would update desktop/package.json"
    preview "Would commit: '$COMMIT_MSG'"
    preview "Would create tag: $NEW_TAG"
    preview "Would push to $REMOTE/$BRANCH"
    preview "Would dispatch one Release workflow for $NEW_TAG"
    $MONITOR && preview "Would monitor GitHub Actions builds until completion"
    echo ""
    echo -e "  ${CYAN}run.py and app/api/routes.py read the version dynamically${NC}"
    echo -e "  ${CYAN}from importlib.metadata — no update needed.${NC}"
    echo ""
    preview "Dry run complete. No changes made."
    exit 0
fi

# ── Update pyproject.toml ────────────────────────────────────────────────────
VERSION_MUTATION_STARTED=true
info "Bumping version in $VERSION_FILE..."
sedi "s/^version = \"[^\"]*\"/version = \"${NEW_VERSION}\"/" pyproject.toml
ok "pyproject.toml → $NEW_VERSION"

# ── Update tauri.conf.json ───────────────────────────────────────────────────
info "Syncing desktop/src-tauri/tauri.conf.json..."
sedi "s/^  \"version\": \"[^\"]*\"/  \"version\": \"${NEW_VERSION}\"/" \
    desktop/src-tauri/tauri.conf.json
ok "tauri.conf.json → $NEW_VERSION"

# ── Update Cargo.toml ────────────────────────────────────────────────────────
info "Syncing desktop/src-tauri/Cargo.toml..."
sedi "s/^version = \"[^\"]*\"/version = \"${NEW_VERSION}\"/" \
    desktop/src-tauri/Cargo.toml
ok "Cargo.toml → $NEW_VERSION"

# ── Update desktop/package.json ──────────────────────────────────────────────
info "Syncing desktop/package.json..."
cd desktop
pnpm version "$NEW_VERSION" --no-git-tag-version --allow-same-version 2>/dev/null
cd "$REPO_ROOT"
ok "package.json → $NEW_VERSION"

# ── Refresh lockfiles after the project version changes ─────────────────────
info "Refreshing uv.lock..."
command -v uv &>/dev/null || fail "uv is required to refresh uv.lock."
uv lock >/dev/null
ok "uv.lock → $NEW_VERSION"

info "Refreshing desktop/src-tauri/Cargo.lock..."
command -v cargo &>/dev/null || fail "cargo is required to refresh Cargo.lock."
(cd desktop/src-tauri && cargo update -p aimatrx-desktop >/dev/null)
ok "Cargo.lock → $NEW_VERSION"

# Prove every derived manifest received the canonical bump before committing.
info "Verifying bumped version manifests..."
./scripts/check-version-sync.sh || fail "Release version synchronization failed after bump."
ok "All release versions match pyproject.toml."

# ── Commit ───────────────────────────────────────────────────────────────────
info "Committing..."
git add \
    pyproject.toml \
    desktop/src-tauri/tauri.conf.json \
    desktop/src-tauri/Cargo.toml \
    desktop/src-tauri/Cargo.lock \
    desktop/package.json \
    uv.lock
git commit -m "$COMMIT_MSG"
RELEASE_COMMITTED=true
ok "Committed: '$COMMIT_MSG'"

# ── Tag ──────────────────────────────────────────────────────────────────────
info "Creating tag $NEW_TAG..."
git tag "$NEW_TAG"
ok "Tag $NEW_TAG created"

# ── Push (branch + tag atomically; reconcile once if the remote raced us) ─────
# --atomic guarantees the branch and tag push together or not at all, so a
# rejection never leaves a half-pushed state. The pre-flight block above makes
# rejection rare; this only triggers if the remote moved during the few seconds
# we spent bumping/committing/tagging.
info "Pushing to $REMOTE/$BRANCH..."
if git push --atomic "$REMOTE" "$BRANCH" "$NEW_TAG" 2>/dev/null; then
    ok "Pushed to $REMOTE/$BRANCH with tag $NEW_TAG"
else
    warn "Push rejected — $REMOTE/$BRANCH moved while we were releasing. Reconciling once..."
    git fetch "$REMOTE" "$BRANCH" 2>/dev/null || die_after_commit "$(cat <<EOF
Push was rejected and we could not re-fetch $REMOTE.
Your release commit and tag $NEW_TAG exist locally; nothing was force-pushed.
Once you are back online:
    git pull --rebase $REMOTE $BRANCH
    git tag -f $NEW_TAG HEAD
    git push --atomic $REMOTE $BRANCH $NEW_TAG
EOF
)"

    if git rebase "$REMOTE/$BRANCH" >/dev/null 2>&1; then
        # The rebase rewrote our release commit, so the tag now points at the
        # old (orphaned) SHA — move it onto the new HEAD before retrying.
        git tag -f "$NEW_TAG" HEAD >/dev/null
        info "Rebased onto updated $REMOTE/$BRANCH and re-pointed $NEW_TAG. Retrying push..."
        if git push --atomic "$REMOTE" "$BRANCH" "$NEW_TAG" 2>/dev/null; then
            ok "Pushed to $REMOTE/$BRANCH with tag $NEW_TAG"
        else
            die_after_commit "$(cat <<EOF
Rejected again right after a clean rebase — $REMOTE/$BRANCH is moving rapidly
(someone else is pushing at the same moment). Your history is clean and linear
locally; just push by hand when the dust settles:
    git push --atomic $REMOTE $BRANCH $NEW_TAG
EOF
)"
        fi
    else
        git rebase --abort >/dev/null 2>&1 || true
        echo "" >&2
        diverge_summary
        die_after_commit "$(cat <<EOF
Push was rejected and an automatic rebase onto the new $REMOTE/$BRANCH conflicts.
Your release commit and tag $NEW_TAG exist locally; nothing was force-pushed.
Resolve by hand:
    git rebase $REMOTE/$BRANCH        # fix the conflicts
    git tag -f $NEW_TAG HEAD          # re-point the tag onto the rebased commit
    git push --atomic $REMOTE $BRANCH $NEW_TAG
EOF
)"
    fi
fi

# ── Dispatch the one release workflow ────────────────────────────────────────
# [skip actions] prevented push-triggered CI and Release runs. A manual
# workflow_dispatch is not affected by commit skip instructions.
info "Starting the Release workflow for $NEW_TAG..."
DISPATCHED=false
DISPATCH_ERROR=""
for attempt in $(seq 1 12); do
    if DISPATCH_ERROR=$(gh workflow run release.yml \
        --repo "$GITHUB_REPO" \
        --ref "$NEW_TAG" 2>&1); then
        DISPATCHED=true
        break
    fi
    [[ "$attempt" -lt 12 ]] && sleep 5
done

if ! $DISPATCHED; then
    die_after_commit "$(cat <<EOF
The release commit and tag $NEW_TAG were pushed, but GitHub did not accept the
Release workflow dispatch after 12 attempts:

$DISPATCH_ERROR

No CI or build workflow was started. Retry safely with:
    gh workflow run release.yml --repo $GITHUB_REPO --ref $NEW_TAG
EOF
)"
fi
ok "Started one Release workflow for $NEW_TAG"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Released ${PROJECT_NAME} ${NEW_VERSION}${NC}"
echo -e "${GREEN}  GitHub Actions will build desktop binaries.${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  Monitor : ${CYAN}https://github.com/${GITHUB_REPO}/actions${NC}"
echo -e "  Releases: ${CYAN}https://github.com/${GITHUB_REPO}/releases${NC}"
echo -e "  Mac Trick: ${CYAN}Use: xattr -cr '/Applications/AI Matrx.app' after installation and putting it in Applications folder ${NC}"
echo ""

# ── Start monitor if requested ───────────────────────────────────────────────
if $MONITOR; then
    # require_gh already passed at pre-flight — this is just a safety net
    require_gh && monitor_build "$NEW_TAG" "$NEW_VERSION" "$GITHUB_REPO"
fi
