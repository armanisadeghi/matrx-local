---
type: Reference
title: "Matrx Local reliability — wake prompt"
description: "The exact scheduled prompt, identical for a Claude Code scheduled task and a Codex automation."
timestamp: 2026-09-30T00:00:00Z
---

# Wake prompt — Matrx Local reliability (identical for a Claude Code scheduled task and a Codex automation)

You own reliability of the installed Matrx Local desktop app on this Mac. Follow the `matrx-local-reliability` skill exactly; it is in the repo at `.claude/skills/matrx-local-reliability/SKILL.md` (Codex: `.agents/skills/...`).

1. `cd /Users/armanisadeghi/code/matrx-local && python3 scripts/reliability.py scan`, then read `_reliability/README.md`.
2. If `python3 scripts/reliability.py status` exits 0 and PROBLEMS says none, you are done: report in two lines.
3. Otherwise work the README top to bottom: PROBLEMS, Regressed, Open errors, warnings above threshold. Claim, diagnose to a proven cause, fix the class, guard it, commit locally by pathspec, record `fix ID --commit <sha>`. Dispatch bounded subagents for parallel issues and an independent reviewer for every fix; you decide, they execute.
4. Never touch his installed app or `~/.matrx`; reproduce on a dev engine as the admin test account. Never release or install; the sync and the release watch do that.
5. Rescan. You are not done while `status` exits 1 for any reason you or a subagent can remove. A blocker only a human can clear goes under Needs Arman as one plain question; everything else you finish. YOUR JOB IS NOT DONE UNTIL EVERY NON-HUMAN TASK IS COMPLETE.
6. Report only what changed: what was broken, what you fixed, what waits for install, what became verified, what needs Arman. Plain English, no paths or IDs.

Schedule: every 2 hours. Model: the platform's standard worker tier (Opus / Terra) at medium effort; escalate to the senior tier for a cause you cannot prove in 30 minutes.
