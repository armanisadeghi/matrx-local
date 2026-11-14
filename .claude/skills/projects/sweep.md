---
type: Reference
title: "projects — the scheduled sweep"
description: "What the scheduled project sweep does on each run: find stalled projects and cold conversations, repair their cards from evidence, and report."
tags: [projects, schedule, sweep]
timestamp: 2026-11-14T00:00:00Z
---

# The project sweep

You build nothing and decide nothing. You make the board true and tell Arman what is waiting.
Why it exists: a dead session cannot update its notes.

## Each run

1. `python3 common-docs/meta/scripts/project_board.py --json` gives `{"note": ..., "projects": [...]}`.
   A non-empty `note` means this clone's history is short: run once with `--deepen` first. Each
   project's `path` is relative to `common-docs/`. Work only on projects flagged `stalled` or
   `lead gone quiet`, or listed by `--check`. At most 8 a run, in this order: `last_swept` empty
   first, then oldest `last_swept` first, so every stalled project is reached in turn.
2. **Find what happened last, per project.** In this order, stopping when you know:
   - the commits: `git log -15 --format='%cs %s%n%b' --grep='^Project: <slug>$'` and
     `git log -15 -- common-docs/<path>`, ignoring any commit with a `Project-Board:` line;
   - the sessions. On a machine that holds the transcripts (`~/.claude/projects/` exists):
     `python3 common-docs/meta/scripts/transcript_user_messages.py ~/.claude/projects/*/*.jsonl --list --grep "<the project's name as he would say it>" --since <30 days ago>`,
     then read only the last turns of the newest match. If this machine cannot reach his coding
     sessions, say so in the report and work from commits alone.
3. **Repair the card** to match the evidence, these three lines only:
   - `next`: the step after the last one that landed;
   - `waiting_on`: if his reply was awaited, `arman: <the question, in one line>`;
   - `lead`: `project_board.py --release <slug> --force` when the board says the lead has gone quiet.

   The marker goes inside the quotes, at the end, on `next` and `waiting_on` only:
   `next: "run the live walk (sweep, believed)"`. Never touch `state`. Never mark a step done: a
   commit saying something is finished or verified is the builder's claim, not evidence. Never
   change a `VISION.md`, a plan's intent, a decision, or code. No evidence of anything newer than
   the card: leave it exactly as it is.
4. **Cold conversations with no project** (only where step 2 could read his sessions): sessions
   from the last 14 days whose final turn is the agent asking him something or reporting a failure,
   with nothing after it for 3 days. Match each to a project by its words; one that matches none is
   listed in your report as "work with no project".
5. **Vision checks owed.** You have now read builders' notes, so you may not judge. If the board
   flags `never checked against the vision` or `new hands since the last vision check`, dispatch
   ONE fresh lane with the brief in [vision-check.md](vision-check.md), verbatim, for the flagged
   project that moved most recently. It commits its own result.
6. **Commit** each repaired project by pathspec, message `project sweep: <slug>: <what changed>`,
   with the lines `Project-Board: sweep` and `Project: <slug>`. Push. A project you looked at and
   left alone gets no commit.
7. **Report in chat**, plain names and no paths, in this order: what is waiting on him (each as one
   question he can answer in a word); what is stalled and free for any agent; what you repaired;
   the vision check's verdict if one ran; work with no project. Nothing changed and nothing waiting:
   say so in one line.
8. **Tell him only when something new waits on him.** If this run found a question for him that
   was not on a card before, send ONE notification through the `notify_me` tool: the count and the
   first question, plain words. Otherwise send nothing.

## Limits

Stop at 8 projects, one vision check, or 40 minutes, whichever comes first,
and say what was left. A project you cannot work out from the evidence stays as it is and is named
in the report; a guess is never written on a card without `(sweep, believed)`.
