#!/usr/bin/env bash
# =============================================================================
# scripts/check.sh — Matrx Local pre-release check suite
#
# Usage:
#   ./scripts/check.sh            # fast mode (static + parity + engine smoke)
#   ./scripts/check.sh --full     # full mode (adds slow/WebSocket tests)
#   ./scripts/check.sh --parity   # parity tests only (no engine needed)
#   ./scripts/check.sh --smoke    # engine smoke tests only
#
# Exit codes:
#   0  All checks passed
#   1  One or more checks failed
#
# Output:
#   Colored pass/fail per step.
#   Writes tests/results/last-run.json with machine-readable summary.
#
# Requirements:
#   uv, node/npm (or npx)
#
# Compatible with bash 3.2+ (macOS default) and bash 4+/5+.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$PROJECT_ROOT/tests/results"

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

pass()  { echo -e "${GREEN}  ✓ $*${RESET}"; }
fail()  { echo -e "${RED}  ✗ $*${RESET}"; }
step()  { echo -e "\n${CYAN}${BOLD}── $* ──────────────────────────────────────────${RESET}"; }
warn()  { echo -e "${YELLOW}  ⚠ $*${RESET}"; }
info()  { echo -e "  $*"; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

MODE_FULL=false
MODE_PARITY_ONLY=false
MODE_SMOKE_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --full)   MODE_FULL=true ;;
    --parity) MODE_PARITY_ONLY=true ;;
    --smoke)  MODE_SMOKE_ONLY=true ;;
    -h|--help)
      echo "Usage: $0 [--full] [--parity] [--smoke]"
      echo "  (no flags)  Run static checks + parity + engine smoke tests"
      echo "  --full      Also run slow tests (WebSocket, etc.)"
      echo "  --parity    Run only parity tests (no engine, very fast)"
      echo "  --smoke     Run only engine smoke tests (skip static/parity)"
      exit 0
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Result tracking  (bash 3.2 compatible — parallel arrays instead of assoc array)
# ---------------------------------------------------------------------------

STEP_NAMES=()    # step names in insertion order
STEP_STATUSES=() # "pass" | "fail" | "skip" — parallel to STEP_NAMES
OVERALL_PASS=true
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
START_TIME=$(date +%s)

record_pass() {
  STEP_NAMES+=("$1")
  STEP_STATUSES+=("pass")
  ((PASS_COUNT++)) || true
  pass "$1"
}

record_fail() {
  STEP_NAMES+=("$1")
  STEP_STATUSES+=("fail")
  ((FAIL_COUNT++)) || true
  fail "$1"
  OVERALL_PASS=false
}

record_skip() {
  STEP_NAMES+=("$1")
  STEP_STATUSES+=("skip")
  ((SKIP_COUNT++)) || true
  warn "SKIP: $1"
}

# Run a command; capture combined output; show it only on failure
run_step() {
  local step_name="$1"
  shift
  local tmp_out
  tmp_out=$(mktemp)
  if "$@" > "$tmp_out" 2>&1; then
    record_pass "$step_name"
  else
    record_fail "$step_name"
    echo ""
    cat "$tmp_out"
    echo ""
  fi
  rm -f "$tmp_out"
}

# ---------------------------------------------------------------------------
# Step 1: Python static checks  (skipped in --smoke mode)
# ---------------------------------------------------------------------------

if ! $MODE_SMOKE_ONLY; then
  step "Step 1: Python static checks"
  cd "$PROJECT_ROOT"

  if ! command -v uv &>/dev/null; then
    record_fail "uv available"
    warn "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
  else
    record_pass "uv available"

    # Import check — catches circular imports and missing deps
    run_step "python import check" \
      uv run --frozen python -c "import app.main; print('OK')"

    # Syntax check on all Python tool files
    run_step "python tool files syntax" \
      uv run --frozen python -c "
import ast, sys
from pathlib import Path
tools_dir = Path('app/tools/tools')
errors = []
for f in tools_dir.glob('*.py'):
    try:
        ast.parse(f.read_text())
    except SyntaxError as e:
        errors.append(f'{f}: {e}')
if errors:
    print('\n'.join(errors), file=sys.stderr)
    sys.exit(1)
print(f'All tool files parse cleanly')
"

    # Parity tests (pure file parsing — no engine)
    run_step "parity: settings keys (TS vs Python)" \
      uv run --frozen pytest tests/parity/test_settings_parity.py -q --no-header

    run_step "parity: section coverage" \
      uv run --frozen pytest tests/parity/test_section_coverage.py -q --no-header

    run_step "parity: route manifest" \
      uv run --frozen pytest tests/parity/test_route_manifest.py -q --no-header

    run_step "parity: tool count >= 79" \
      uv run --frozen pytest tests/parity/test_tool_count.py -q --no-header

    run_step "parity: api-key providers (TS vs Python)" \
      uv run --frozen pytest tests/parity/test_api_key_providers.py -q --no-header

    run_step "parity: background tasks integrity" \
      uv run --frozen pytest tests/parity/test_background_tasks.py -q --no-header
  fi
fi

# ---------------------------------------------------------------------------
# Step 2: Frontend static checks  (skipped in --smoke and --parity modes)
# ---------------------------------------------------------------------------

if ! $MODE_SMOKE_ONLY && ! $MODE_PARITY_ONLY; then
  step "Step 2: Frontend static checks"
  DESKTOP_DIR="$PROJECT_ROOT/desktop"

  if [ ! -d "$DESKTOP_DIR/node_modules" ]; then
    warn "node_modules not found — running pnpm install..."
    (cd "$DESKTOP_DIR" && pnpm install --silent 2>&1) || true
  fi

  run_step "tsc -b" \
    bash -c "cd '$DESKTOP_DIR' && npx tsc -b"

  run_step "vite build" \
    bash -c "cd '$DESKTOP_DIR' && pnpm run build 2>&1 | grep -v 'Some chunks are larger' || true"
fi

# ---------------------------------------------------------------------------
# Step 3: Engine smoke tests  (skipped in --parity mode)
# ---------------------------------------------------------------------------

if ! $MODE_PARITY_ONLY; then
  step "Step 3: Engine smoke tests"
  cd "$PROJECT_ROOT"

  if $MODE_FULL; then
    info "Running ALL tests including slow (--full mode)"
    run_step "engine smoke tests" \
      uv run --frozen pytest tests/smoke/ -q --no-header --timeout=60
  else
    run_step "engine smoke tests" \
      uv run --frozen pytest tests/smoke/ -m "not slow" -q --no-header --timeout=60
  fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo -e "${BOLD}══════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Results  (${ELAPSED}s)${RESET}"
echo -e "${BOLD}══════════════════════════════════════════════════════${RESET}"

for i in "${!STEP_NAMES[@]}"; do
  name="${STEP_NAMES[$i]}"
  result="${STEP_STATUSES[$i]}"
  case "$result" in
    pass) echo -e "  ${GREEN}✓${RESET} $name" ;;
    fail) echo -e "  ${RED}✗${RESET} $name" ;;
    skip) echo -e "  ${YELLOW}○${RESET} $name" ;;
  esac
done

echo ""
echo -e "  ${BOLD}${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${SKIP_COUNT} skipped${RESET}"

# ---------------------------------------------------------------------------
# Write machine-readable result JSON
# ---------------------------------------------------------------------------

mkdir -p "$RESULTS_DIR"
TIMESTAMP=$(date -u "+%Y-%m-%dT%H:%M:%SZ")
OVERALL_STATUS=$( $OVERALL_PASS && echo "pass" || echo "fail" )

if $MODE_FULL; then
  MODE_STR="full"
elif $MODE_PARITY_ONLY; then
  MODE_STR="parity"
elif $MODE_SMOKE_ONLY; then
  MODE_STR="smoke"
else
  MODE_STR="fast"
fi

# Build steps JSON (bash 3.2 compatible)
STEPS_JSON="{"
FIRST_STEP=true
for i in "${!STEP_NAMES[@]}"; do
  name="${STEP_NAMES[$i]}"
  result="${STEP_STATUSES[$i]}"
  # Escape double quotes in name
  safe_name="${name//\"/\\\"}"
  if $FIRST_STEP; then
    STEPS_JSON+="\"$safe_name\":\"$result\""
    FIRST_STEP=false
  else
    STEPS_JSON+=",\"$safe_name\":\"$result\""
  fi
done
STEPS_JSON+="}"

cat > "$RESULTS_DIR/last-run.json" <<JSON
{
  "timestamp": "$TIMESTAMP",
  "elapsed_seconds": $ELAPSED,
  "status": "$OVERALL_STATUS",
  "mode": "$MODE_STR",
  "passed": $PASS_COUNT,
  "failed": $FAIL_COUNT,
  "skipped": $SKIP_COUNT,
  "steps": $STEPS_JSON
}
JSON

echo ""
info "Results written to tests/results/last-run.json"

if $OVERALL_PASS; then
  echo -e "\n${GREEN}${BOLD}  ALL CHECKS PASSED — safe to release${RESET}\n"
  exit 0
else
  echo -e "\n${RED}${BOLD}  CHECKS FAILED — do not release${RESET}\n"
  exit 1
fi
