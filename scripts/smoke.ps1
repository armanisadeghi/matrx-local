# smoke.ps1 — Windows entry point for the smoke harness.
#
# This is a thin wrapper, NOT a second implementation. scripts/smoke.sh is the
# single source of truth and runs on Windows under Git Bash — the same shell CI
# already uses to drive build-sidecar.sh on windows-latest. Two parallel
# implementations would drift, and the one nobody runs would rot.
#
# Usage:
#   .\scripts\smoke.ps1                   # web
#   .\scripts\smoke.ps1 packaged          # full build + launch + log capture
#   .\scripts\smoke.ps1 packaged --no-build
#
# See docs/SMOKE_HARNESS.md.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$smokeSh = Join-Path $repoRoot "scripts/smoke.sh"

# Git Bash ships with Git for Windows; every dev here already has it.
$bash = $null
foreach ($candidate in @(
        "$env:ProgramFiles\Git\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe")) {
    if (Test-Path $candidate) { $bash = $candidate; break }
}
if (-not $bash) {
    $cmd = Get-Command bash.exe -ErrorAction SilentlyContinue
    if ($cmd) { $bash = $cmd.Source }
}
if (-not $bash) {
    Write-Host "bash.exe not found. Install Git for Windows (https://git-scm.com/download/win)" -ForegroundColor Red
    Write-Host "— the smoke harness runs scripts/smoke.sh under Git Bash." -ForegroundColor Red
    exit 2
}

& $bash $smokeSh @args
exit $LASTEXITCODE
