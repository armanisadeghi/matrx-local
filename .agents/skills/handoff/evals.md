---
type: Reference
title: "handoff — proof record (RED baseline, GREEN with the skill)"
description: "The skill-authoring §5 run for the handoff skill: two paper scenarios (outgoing stalled lead with sub-agents; incoming lead handed a misleading handoff), three reps without the skill and three with it per side, an independent grader, the gaps found and the rerun after fixing them. Rerun after any edit."
tags: [skills, evals, handoff]
timestamp: 2026-11-02T00:00:00Z
---

# handoff — proof record

**Source.** Owner request (2026-11-02): lead changes after credits run out or a lead stalls lose most
of what the old lead knew; leads with a dozen or more sub-agents in separate cloud sessions lose
those lanes' unpushed work outright. No transcript of a failed handoff was used; the scenarios are
synthesized with the pressures he described.

**Scenarios** (paper exercises, nothing executed). Lane `standard`, model Sonnet, 2026-11-02.

- **O — outgoing.** A stalled "quote builder" lead, account nearly out of credits. Four owner messages
  including two corrections; a lead-made table decision; an approval flow hidden not removed against
  his word; a discount bug that survived three fixes; four uncommitted files; four sub-agents (one
  pushed and verified, one with unpushed commits, one of unknown state, one in-process helper
  mid-edit); a self-scheduled 30-minute check-in. Rubric items O1–O13 (round 2 adds O14–O18).
- **I — incoming.** The same project handed over through a short, misleading handoff ("nearly
  done", a lead decision stated as settled, approvals and PDF export claimed as his wishes). Rubric
  items I1–I11 (round 2 adds I12–I14).

The rubric and the reps' outputs were graded by an agent that wrote none of them.

## Round 1 — RED (no skill) vs GREEN (skill)

| Arm | Score | Reps (agent id) |
|---|---|---|
| RED-O | 28/39 | `aa64471080aa5c26a` 10 · `aa1d1c24d988bc58a` 9 · `a617e8a1c9980c593` 9 |
| GREEN-O | 39/39 | `a5ffa44799e1f0148` · `a06d14ff49437280f` · `ad2e69a3eb5c2318e` |
| RED-I | 13/33 | `a424bbfb89ec9c424` 3 · `a7b736ebf0b1efa72` 5 · `af0e4e496c072df5b` 5 |
| GREEN-I | 33/33 | `a37581f50d68e9ac9` · `a62d08b8e776dc5d5` · `aa769d8fdebc58767` |

Grader `a685799dd6fa6a784`; fresh-reader review (docs §8.3) `a2a65456e0fd9c74f`: 0 findings.

**RED failures.** Outgoing: no checked / believed / guessed marks, no "why it stalled", advice mixed
into facts, no takeover line (3/3); one left the in-process helper's files unsecured; one told a lane
to push to main "if the build passes". Incoming: 3/3 verified mostly by themselves, had no fresh-eyes
section, never had the plan attacked, and began building — turning approvals on and building PDF
export — without the owner approving a plan.

**RED rationalizations (verbatim).**

| Excuse | Reality |
|---|---|
| "Flag flip is my call after the test; I tell Arman after." | A previous lead's unruled feature is a question for him, not a test result. |
| "Not asking Arman unless it is a real product fork." | The handoff claimed his wishes; only his words settle them. |
| "I keep the architecture decision and the final verification for myself." | Done is checked by someone who did not build it. |
| "I ask it to push to main only if its type check and build pass." | Unfinished lane work goes to its own branch; main ships within the half hour. |
| "The full handoff is written and saved with the project notes, so whoever takes over can start" | Without a takeover line he has to find and point to it himself. |

**Gaps the grader found in GREEN, fixed before round 2.** The wind-down message was sent to the
in-process helper (which has no branch) and leads waited on it; no rule for the lead's own check-in;
his words paraphrased into VISION.md; "checked" claimed for things not re-seen; placeholder paths in
the takeover line; "small, clear defects are fixed now" let all three incoming leads fix a bug that
had already beaten three attempts, and one announced it live before any check.

## Round 2 — GREEN after the fixes

| Arm | Score | Reps (agent id) |
|---|---|---|
| GREEN-O (O1–O18) | 51/54, 17 each | `a2693716dca88bbf6` · `a3ed76c562a3b3247` · `ab05ddf58e23b699c` |
| GREEN-I (I1–I14) | 39/42, 13 each | `ab8df050eaedd0070` · `a1269fdf444c79a1e` · `a34d50575588169f3` |

Grader `ab08aa89114633a53`. Every round-1 gap closed: no wind-down message to the in-process helper,
check-in cancelled first, his words verbatim in VISION.md, real takeover paths, rounding fix held for
the plan (3/3), fresh summary by a separate lane.

**Still failing, and what changed.**
- O17 (3/3): leads marked their own actions "checked (I wrote it)". SKILL.md now says "checked"
  names the commit, link or moment, and that "I wrote it" is not a check. Not yet rerun.
- I10 (3/3) fails a rubric item that asked for the living HANDOFF.md "from the start". The skill
  deliberately starts it when step 3 finishes, so the living page is never a copy of an unverified
  handoff; the rubric item is wrong, the skill unchanged.
- One incoming rep would have put recovered unfinished work "directly on main per repo rules";
  incoming.md now keeps unfinished work found on branches off main until verified. Not yet rerun.

## Red flags

- "The helper's reply will tell me what to commit." — Commit its work yourself; it has no branch.
- "I saw it once, so it's checked." — Say when and where, or mark it believed.
- "His four messages, summarized, are in VISION.md." — Verbatim and dated, or they are lost.
- "It's a small fix; I'll do it before the plan." — Not if anyone already failed to fix it.
- "The path is `<node>/…`; he'll figure it out." — The takeover line carries a real path.
