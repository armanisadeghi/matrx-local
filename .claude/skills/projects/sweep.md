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

0. **One run per slot.** With the `schedule_claim` tool: claim `project-sweep`; refused means stop.
   Without it (a cloud run): read `scheduler.agent_schedule_claim` through the Supabase tool; a row
   for `project-sweep` claimed in the last 40 minutes means another run has this slot: stop. The
   limits below then come from this page, not from a claim.

1. `python3 common-docs/meta/scripts/project_board.py --sweep-batch` prints the 8 projects this run
   takes, each in full; successive runs take successive batches, so do not pick your own. If it says
   history is too short, run it once with `--deepen`. Also take any project `--check` lists. Each
   project's folder is the path shown beside its name.
2. **Find what happened last, per project.** In this order, stopping when you know:
   - the commits: `git log -15 --format='%cs %s%n%b' --grep='^Project: <slug>$'` and
     `git log -15 -- common-docs/<path>`, ignoring any commit with a `Project-Board:` line;
   - the sessions. On a machine that holds the transcripts (`~/.claude/projects/` exists):
     `python3 common-docs/meta/scripts/transcript_user_messages.py ~/.claude/projects/*/*.jsonl --list --grep "<the project's name as he would say it>" --since <30 days ago>`,
     then read only the last turns of the newest match. Anywhere else (a cloud run), read the copy
     every coding session leaves in the database: `chat.coding_session` (one row per session,
     `last_seen_at`, `conversation_id`) and its turns in `chat.message` (`role`, `position`,
     `user_content`, `content`), through the Supabase tool, read-only. Search the turns for the
     project's name. If neither reaches his sessions, say so in the report and work from commits.
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
   with nothing after it for 3 days. In the database, read-only, this query (fast; do not widen it):

   ```sql
   with s as (select conversation_id, last_seen_at from chat.coding_session
              where deleted_at is null and provider = 'claude_code' and origin = 'matrx_desktop'
                and last_seen_at between now() - interval '14 days' and now() - interval '3 days'),
   last as (select distinct on (m.conversation_id) m.conversation_id, m.role, s.last_seen_at,
                   left(coalesce(m.content::text, ''), 400) as last_words
            from chat.message m join s using (conversation_id)
            where m.deleted_at is null order by m.conversation_id, m.position desc)
   select * from last where role = 'assistant' order by last_seen_at desc limit 40;
   ```

   Judge each from `last_words`. Many sessions share one import time; that is the copy's stamp,
   not his activity, so rank by what the last words ask, not by the time. Match each to a project by its words; one that matches none is
   listed in your report as "work with no project".
4a. **Stuck lanes** ([lanes.md](lanes.md) §2). `python3 common-docs/meta/scripts/project_board.py --stuck`.
   - STUCK (48 h): leave the file as it is; any agent may already take it. Name it in the report.
   - ABANDONED (72 h): start ONE fresh subagent for the lane that has waited longest, with the text
     `--lane-prompt <project>/<lane>` prints as its whole brief, plus "run the take with --force".
     It takes the lane and checks in itself. One per run; the rest are named in your report.
   - **Text him once per abandoned lane.** The board marks a lane `TEXT ARMAN` on the first run
     after it passes 72 hours. For each, send ONE text with the `send_text` tool: the lane's
     name, who held it, how long it has been quiet, and whether a session was started on it. No
     mark, no text.
   - "says waiting but N tasks are not blocked", "says done but N tasks are open": on the clock
     like any other. Do not edit the file.
   - Waiting on Arman, or not started under `commander: arman`: his. First in the report.
   You never tick a lane's task and never change its Tasks, Goal or Results.
5. **Vision checks owed: always dispatch one.** You have read builders' notes, so you must not judge
   yourself; a fresh lane that has read none of them must. If the board flags any project
   `never checked against the vision` or `new hands since the last vision check`, start ONE fresh
   subagent (the Agent tool) with the brief in [vision-check.md](vision-check.md), verbatim, for the
   flagged project that moved most recently. It commits its own result. Skipping this step is a
   failed run unless no project is flagged.
6. **Commit** each repaired project by pathspec, message `project sweep: <slug>: <what changed>`,
   with the lines `Project-Board: sweep` and `Project: <slug>`. Push. A project you looked at and
   left alone gets no commit.
7. **Report in chat**, plain names and no paths, in this order: what is waiting on him (each as one
   question he can answer in a word); lanes stuck or abandoned and what you did about each; what is stalled and free for any agent; what you repaired;
   the vision check's verdict if one ran; work with no project. Nothing changed and nothing waiting:
   say so in one line.
8. **Tell him only when something new waits on him.** If this run found a question for him that
   was not on a card before, or a lane that passed 48 or 72 hours since the last run (name the lane, its holder and how long), send ONE notification through the `notify_me` tool: the count and the
   first question, plain words. Otherwise send nothing.

## Limits

Your claim returns `limits`; hold to them. They are set for 8 projects, one dispatched vision check, one
dispatched lane session and 40 minutes. Stop at whichever comes first,
and say what was left. A project you cannot work out from the evidence stays as it is and is named
in the report; a guess is never written on a card without `(sweep, believed)`.
