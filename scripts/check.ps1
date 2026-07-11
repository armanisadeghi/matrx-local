# =============================================================================
# scripts/check.ps1 — Matrx Local pre-release check suite (Windows)
#
# Usage:
#   .\scripts\check.ps1            # fast mode
#   .\scripts\check.ps1 -Full      # includes slow tests
#   .\scripts\check.ps1 -Parity    # parity tests only (no engine)
#   .\scripts\check.ps1 -Smoke     # engine smoke tests only
#
# Exit codes:
#   0  All checks passed
#   1  One or more checks failed
# =============================================================================

[CmdletBinding()]
param(
    [switch]$Full,
    [switch]$Parity,
    [switch]$Smoke
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ResultsDir = Join-Path $ProjectRoot "tests\results"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Pass([string]$msg) {
    Write-Host "  [PASS] $msg" -ForegroundColor Green
}
function Write-Fail([string]$msg) {
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}
function Write-Skip([string]$msg) {
    Write-Host "  [SKIP] $msg" -ForegroundColor Yellow
}
function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "-- $msg " -ForegroundColor Cyan -NoNewline
    Write-Host "$(("-" * [Math]::Max(0, 50 - $msg.Length)))" -ForegroundColor Cyan
}
function Write-Info([string]$msg) {
    Write-Host "  $msg"
}

# Result tracking
$StepResults = [System.Collections.Specialized.OrderedDictionary]::new()
$OverallPass = $true
$StartTime = Get-Date

function Record-Pass([string]$name) {
    $script:StepResults[$name] = "pass"
    Write-Pass $name
}
function Record-Fail([string]$name) {
    $script:StepResults[$name] = "fail"
    Write-Fail $name
    $script:OverallPass = $false
}
function Record-Skip([string]$name) {
    $script:StepResults[$name] = "skip"
    Write-Skip $name
}

function Run-Step([string]$name, [scriptblock]$cmd) {
    $output = & $cmd 2>&1
    if ($LASTEXITCODE -eq 0) {
        Record-Pass $name
    } else {
        Record-Fail $name
        Write-Host ""
        $output | ForEach-Object { Write-Host "    $_" }
        Write-Host ""
    }
}

# ---------------------------------------------------------------------------
# Step 1: Python static checks
# ---------------------------------------------------------------------------

if (-not $Smoke) {
    Write-Step "Step 1: Python static checks"
    Set-Location $ProjectRoot

    # Check uv
    $uvPath = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvPath) {
        Record-Fail "uv available"
        Write-Info "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
    } else {
        Record-Pass "uv available"

        # Import check
        Run-Step "python import check" {
            & uv run --frozen python -c "import app.main; print('OK')"
        }

        # Tool file syntax check
        Run-Step "python tool files syntax" {
            & uv run --frozen python -c @"
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
print(f'All {len(list(tools_dir.glob(chr(42) + chr(46) + chr(112) + chr(121))))} tool files parse cleanly')
"@
        }

        # Parity tests
        Run-Step "parity: settings keys (TS vs Python)" {
            & uv run --frozen pytest tests/parity/test_settings_parity.py -q --no-header
        }
        Run-Step "parity: section coverage" {
            & uv run --frozen pytest tests/parity/test_section_coverage.py -q --no-header
        }
        Run-Step "parity: route manifest" {
            & uv run --frozen pytest tests/parity/test_route_manifest.py -q --no-header
        }
        Run-Step "parity: tool count >= 79" {
            & uv run --frozen pytest tests/parity/test_tool_count.py -q --no-header
        }
        Run-Step "parity: api-key providers (TS vs Python)" {
            & uv run --frozen pytest tests/parity/test_api_key_providers.py -q --no-header
        }
        Run-Step "parity: background tasks integrity" {
            & uv run --frozen pytest tests/parity/test_background_tasks.py -q --no-header
        }
    }
}

# ---------------------------------------------------------------------------
# Step 2: Frontend static checks
# ---------------------------------------------------------------------------

if (-not $Smoke -and -not $Parity) {
    Write-Step "Step 2: Frontend static checks"
    $DesktopDir = Join-Path $ProjectRoot "desktop"

    if (-not (Test-Path (Join-Path $DesktopDir "node_modules"))) {
        Write-Info "node_modules not found -- running pnpm install..."
        Push-Location $DesktopDir
        pnpm install --silent 2>&1 | Out-Null
        Pop-Location
    }

    Run-Step "tsc -b" {
        Push-Location $DesktopDir
        npx tsc -b
        Pop-Location
    }

    Run-Step "vite build" {
        Push-Location $DesktopDir
        pnpm run build
        Pop-Location
    }
}

# ---------------------------------------------------------------------------
# Step 3: Engine smoke tests
# ---------------------------------------------------------------------------

if (-not $Parity) {
    Write-Step "Step 3: Engine smoke tests"
    Set-Location $ProjectRoot

    $markerFilter = if ($Full) { "" } else { "-m 'not slow'" }

    Run-Step "engine smoke tests" {
        if ($Full) {
            & uv run --frozen pytest tests/smoke/ -q --no-header --timeout=60
        } else {
            & uv run --frozen pytest tests/smoke/ -m "not slow" -q --no-header --timeout=60
        }
    }
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

$EndTime = Get-Date
$Elapsed = [int]($EndTime - $StartTime).TotalSeconds

Write-Host ""
Write-Host "============================================================" -ForegroundColor White
Write-Host "  Results  ($($Elapsed)s)" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor White

$PassCount = 0; $FailCount = 0; $SkipCount = 0

foreach ($entry in $StepResults.GetEnumerator()) {
    switch ($entry.Value) {
        "pass" { Write-Host "  [+] $($entry.Key)" -ForegroundColor Green; $PassCount++ }
        "fail" { Write-Host "  [x] $($entry.Key)" -ForegroundColor Red;   $FailCount++ }
        "skip" { Write-Host "  [-] $($entry.Key)" -ForegroundColor Yellow; $SkipCount++ }
    }
}

Write-Host ""
Write-Host "  $PassCount passed, $FailCount failed, $SkipCount skipped" -ForegroundColor White

# ---------------------------------------------------------------------------
# Write machine-readable result JSON
# ---------------------------------------------------------------------------

if (-not (Test-Path $ResultsDir)) {
    New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null
}

$Timestamp = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
$OverallStatus = if ($OverallPass) { "pass" } else { "fail" }
$ModeStr = if ($Full) { "full" } elseif ($Parity) { "parity" } elseif ($Smoke) { "smoke" } else { "fast" }

$StepsJson = ($StepResults.GetEnumerator() | ForEach-Object {
    "`"$($_.Key.Replace('"','\"'))`":`"$($_.Value)`""
}) -join ","

$JsonContent = @"
{
  "timestamp": "$Timestamp",
  "elapsed_seconds": $Elapsed,
  "status": "$OverallStatus",
  "mode": "$ModeStr",
  "passed": $PassCount,
  "failed": $FailCount,
  "skipped": $SkipCount,
  "steps": {$StepsJson}
}
"@

$JsonContent | Set-Content -Path (Join-Path $ResultsDir "last-run.json") -Encoding UTF8

Write-Info "Results written to tests\results\last-run.json"

if ($OverallPass) {
    Write-Host ""
    Write-Host "  ALL CHECKS PASSED -- safe to release" -ForegroundColor Green
    Write-Host ""
    exit 0
} else {
    Write-Host ""
    Write-Host "  CHECKS FAILED -- do not release" -ForegroundColor Red
    Write-Host ""
    exit 1
}
