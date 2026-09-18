# Scope price acceptance review — 2026-09-17

## Verdicts

- **Source accounting and quality: PASS.** Backend commits `f31453180` and `8f1061748`, desktop commit `b6e15caef`, and web commit `4a70f14d0d` preserve priced estimates through cells, bins, projects, conversations, and workers. A response-bearing unknown rate remains unavailable; an unknown activity-only row no longer poisons a priced project. Both UIs show an unavailable selected scope when a response-bearing cell has no estimate and ignore zero-usage activity-only rows when summing a selected scope.
- **Live acceptance: PENDING.** The repaired Matrx Local source has no later package/release commit, and no installed-app or deployed populated-UI retake was performed. The earlier populated UI mismatch therefore remains the live failure evidence, not proof of the repaired build. The requested `sanitized-ui-acceptance-20260917.md` receipt was not present in the workspace during this review.

## Evidence

- Focused backend suite: `5 passed` in `0.08s`.
- Focused desktop component suite: `6 passed` in `1.19s`.
- Focused web ESLint: passed with no findings.
- Diff checks for all reviewed commits: clean.
- Direct edge probe against production `_aggregate`: priced row plus unknown zero-usage activity row => `0.008`; priced row plus unknown response-bearing row => `null`.
- Fresh bounded `collect_usage` run for 2026-09-14 13:31–13:40 UTC: complete; 77 responses; 227.245 estimated standard credits; 5 cells; 4 bins; 2 projects; 2 workers; 0 unknown response-bearing cells; project rollup equivalence `2/2`.
- The first independent real-data run caught the activity-only defect before `8f1061748`: one 46-response project returned `null` although its priced response-bearing cells summed to `179.81274`. The same window passed after the correction.

## Quality assessment

The backend regression fixture now forces both sides of the distinction: the priced project retains `950` despite an unknown activity-only row, while a project with an unknown response-bearing model stays unavailable. The desktop test separately exercises unavailable and activity-only scope behavior. The web guard has no dedicated component regression test in its commit, but its source condition is equivalent to the exercised desktop rule and focused lint passes.

Standard-credit values remain scenario estimates rather than measured Pro allowance debits. Unknown response-bearing rates are never rendered as zero in a selected scope.
