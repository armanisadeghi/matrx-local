---
type: Reference
title: "handoff — the outgoing lead"
description: "What the lead being replaced does, in priority order: secure the work, wind down sub-agents, extract the owner's words, write the handoff, give the owner the takeover line. Companion to the handoff skill."
tags: [handoff, coordination, wind-down]
timestamp: 2026-11-02T00:00:00Z
---

# The outgoing lead

You may be cut off at any moment, so the steps run in this order and each one is finished and
pushed before the next. Your mode (Normal / Stalled / Emergency) comes from the table in SKILL.md.
Do no new building, polishing or extra verification — the window exists for securing and recording.

## 1. Secure your own work — always first

- Cancel your own scheduled check-ins and wake-ups; one that fires mid-handoff spends the window.
- Finished and verified → commit and push to where the work normally lands.
- Unfinished, in a cloud session → push to **this session's own branch, never main** (main ships
  about every 30 minutes). In-process helpers share your machine, so their changes are yours to
  push here too.
- Unfinished, in a shared local checkout → the files survive on disk; never commit unverified work
  to main; list each path and its state in the lanes table (section 3).

**Done when** `git status` is clean or every leftover path is listed, and `git log origin/<branch>`
shows your commits on the server.

## 2. Wind down sub-agents (Normal and Stalled; skipped in Emergency)

1. **Census without waking anyone.** List every sub-agent and session you started: your own
   dispatches, `ListAgents`, the cloud session list (`get_session`, latest turns via `list_events`),
   and pushed branches. Mark each **pushed and done** / **unsaved work** / **unknown**.
2. **Leave pushed-and-done lanes asleep.** Waking costs money and gains nothing.
3. **In-process helpers** (they share your machine and have no branch of their own): never send them
   the message below and never wait on them. Stop any still running, then commit their changes
   yourself under step 1 and give each a row in the lanes table.
4. **Separate sessions with unsaved or unknown work:** send the message below — they alone get it. How to reach each
   kind of session: [take-command §0.R step 3](/skills/take-command/SKILL.md). Only the account
   that started a cloud session can reach it, and only pushed work outlives its machine — this
   window is the last chance.
5. **While replies arrive, do steps 3 and 4.**
6. **Each reply becomes a row** in the lanes table. A lane still silent when step 4 is pushed
   becomes "not saved — <what its transcript shows it was doing>". Do not chase it.
7. **Never archive a session** until its branch is confirmed on the server (`git ls-remote`).

The wind-down message — send it as written, with the project and the lane filled from your own
records:

```text
Wind-down: [project] is changing leads and your lane, [lane], is being handed over. Start no new work. If the step you are in finishes in a few minutes, finish it; otherwise stop where you are. Push finished, verified work where it normally goes, and everything else to your own session branch, never main; nothing may stay only on your machine. Make your last commit "wind-down: [lane]" with a body that says what you were asked to do, what is done, what is half-done and exactly where you stopped, your next step, and anything that surprised you. Then reply to me in five lines or fewer: branch, done, not done, next step, must-know. No polishing, no extra verification. Then stop.
```

**Done when** every lane you started has a row saying where its work is.

## 3. Extract his words

Run [user-ground-truth §1](/skills/user-ground-truth/SKILL.md) on this conversation's transcript
and any earlier sessions of this project you know of (a cloud session's transcript is
`~/.claude/projects/-home-user-<repo>/<session-id>.jsonl`; the script is
`common-docs/meta/scripts/transcript_user_messages.py`). Store the output as evidence (SKILL.md,
*Where it lives*) — the next lead may be in another account and unable to open these conversations
at all. Copy the rulings that define done, and **every correction he made**, into the project node's
VISION.md **verbatim and dated** — never a summary; that committed copy is the one every later lead
can open ([docs §5](/skills/docs/SKILL.md)).

**Done when** the extraction is stored and linked, and the VISION.md update is pushed.

## 4. Write the handoff

Sections and location: SKILL.md. Point to evidence — links, commits, conversations — instead of
writing from memory, and mark every claim checked / believed / guessed.

- **Normal:** every section.
- **Stalled:** every section, and this is a record, not advice. Sections 5 (what was tried, with
  exact errors) and 7's **Why it stalled** carry the weight; anything you would recommend goes only
  under **Theories**. List every time he corrected you. An honest record is the most useful thing
  you can leave; a flattering one gets the next lead stuck in the same place.
- **Emergency:** sections 0–3, then push. Add the rest only if you are still running.

**Done when** HANDOFF.md is pushed and every row in sections 2 and 3 has evidence or a
checked / believed / guessed mark.

## 5. Tell Arman

In chat, plain English: what was secured, any lane whose work was not saved, and the takeover line in
a code block he can paste unchanged, with the real project name and path (never a placeholder):

```text
Take over [project] using the handoff skill. The handoff is at [path].
```
