---
type: Reference
title: "projects — lanes: split a project, hold a lane, take a stuck one"
description: "How a project too big for one session is split into lanes that separate sessions run under Arman or under a lead, how a lane's holder keeps working and checks in, and how a quiet lane is noticed and taken over."
tags: [projects, lanes, coordination, fleet]
timestamp: 2026-11-24T00:00:00Z
---

# Lanes

The rule is [projects rule 10](/policies/projects.md). This page is the how. It is written so that any
agent, in Claude Code, Codex or a cloud session, gets the same result by following the steps in order.

| You were told | Read |
|---|---|
| "take lane X", or you were pasted a lane's starting message | §1 |
| a session start or the board shows a stuck lane | §2 |
| "split this project into lanes", or a project has stopped moving because it is too big | §3 |
| "where do the lanes stand" | §4 |

## 1. Hold a lane

1. Pull main. Run `python3 common-docs/meta/scripts/project_board.py --take-lane <project>/<lane>`.
   No session id in your environment: add `--session "<harness> <conversation id>"`.
   `TAKEN` means another session holds it and its clock is under 48 hours. If Arman gave you this
   lane in this chat (he pasted its starting message, or the earlier holder was your own session before
   a restart), that is his order: run it again with `--force`. Otherwise tell him who has it and stop.
2. Read the lane file in full. It is the plan. The chat is not, and the project's long pages are
   background you open only when a task points to them.
3. **The loop. Repeat until step 4 lets you stop.**
   1. Take the first unticked task with no `(waits: ...)` on it whose `(after: N)` tasks are all ticked.
   2. Do it to its "Done when". Check that yourself, by running it or looking at it. Ticking a task
      says its "Done when" is true. It never says a row of the project's register or bar is accepted:
      that takes the yes of an agent that did not do the work (a fresh helper agent given only the
      row and your evidence), written under Results.
   3. Tick the task. Under Results write three lines at most: what is now true, the commit or link
      that shows it, what it leaves open.
   4. Run `python3 common-docs/meta/scripts/project_board.py --checkin <project>/<lane> --next "<the next task, one sentence>"`.
      That saves the lane file and pushes it to main. It is the check-in the alarms read, and the
      only thing that counts as one. Read what it prints: "pushed to main" is checked against main
      itself; anything else means nobody can see your work yet, so fix that before the next task.
4. **You may stop only when one of these is true:**
   - no open task is left: check in with `--status done`. With a task open the board ignores `done`;
   - every open task carries `(waits: ...)`: check in with `--status waiting --waiting "<see below>"`.

   Anything else is not a reason to stop. Not "a good checkpoint", not "awaiting review", not "the
   next task is large", not "I reported my findings". Take the next task.
5. **A task you cannot finish.** Try a second, different way. Still stuck: write on its line
   `(waits: <who or what>, <the two things you tried>, <exactly what is needed>)`, check in, and take
   the next task. A problem you can fix yourself is not a wait: fix it.
6. **Something only Arman can do** (a signature, a payment, a decision, an account only he holds):
   one line under Needs Arman that he can answer in a word, with your recommendation first and the
   link to the exact page. Then keep working the other tasks.
7. **Something another lane owns:** do not change it. Add one line under "Requests from other lanes"
   in THAT lane's file, signed with your lane's name, and push it. Read your own Requests section at
   the start of every turn; each request is a task.
8. **A task that turns out bigger than one session:** replace it with the smaller tasks it really
   is, each with its own "Done when", and carry on with the first.
9. **Commits for the lane's code** carry the line `Lane: <project>/<lane>`. They count as check-ins.
10. **Leaving on purpose:** check in, then `--release-lane <project>/<lane>`.

`waiting_on` takes one of three forms, and the board checks them:
`arman: <the one line he must answer>` (the same line is under Needs Arman), or
`outside: <who> (check again YYYY-MM-DD)`, or `lane: <other lane> (check again YYYY-MM-DD)`. The date is
at most 14 days out. On that date you look again and check in. A task that repeats ("each working
day, look at the portal") carries no waits mark, so its lane checks in every day.

A lane file never uses the `(taken: ...)` marks of a project without lanes (rulebook §3b): the lane's
holder is the one name on it.

Done when: `project_board.py --lanes <project>` shows your lane with a check-in in the last hour and
a `next` that names a real task.

## 2. A stuck lane

`python3 common-docs/meta/scripts/project_board.py --stuck` lists them worst first. The clock is the
time since the lane's last check-in on main, or since its present holder took it when that is later.

| Hours | The board says | What you do |
|---|---|---|
| 24 | QUIET | If it is your lane: check in now. If not: nothing yet. |
| 48 | STUCK: free to take | If Arman has given you no task in this chat, take it (§1). The old holder loses it, and you get a fresh clock. |
| 72 | ABANDONED: take it now | No task from Arman in this chat: take it now (§1). Working on something he gave you: keep that, tell Arman in one line which lane and who had it, and start a session on it with `--lane-prompt` if your harness can start sessions. |

- **A lane that says waiting while a task is workable is on the clock like any other.** The board
  says so. Taking it means doing those tasks.
- **An abandoned lane sends Arman one text**, from the sweep, the first run after it passes 72 hours.
- **A lane waiting on Arman is never taken.** At 72 hours the board says to remind him: put its
  one line in front of him.
- **A lane that says done with a task open** is on the clock too. Taking it means finishing the tasks.
- **A lane never started** shows as "not started". Under `commander: arman` it is his to start and
  is never taken: remind him, with its starting message. Under `commander: lead` it follows the clock.
- **Who commands does not change** when a lane changes hands. Tell Arman, in one line, which lane you
  took and who had it.
- When you take a lane, the first task is the old holder's unfinished one. Read Results first, and
  check any step marked done before you build on it.

Done when: the lane has a new holder and a check-in, or Arman has been told why it does not.

## 3. Split a project into lanes

Do this when a project has more work than one session can hold, when it has stopped moving, or when
he asks. It is the one part that needs judgment, so do it carefully once.

1. **Cut by ownership, not by topic.** Two lanes never change the same files, the same database
   objects or the same outside account. List what each lane owns; anything two lanes both need has
   one owner, and the other asks through Requests. Work that touches the same controls stays in one
   lane. A row two lanes share closes only when both have had their part checked. Write the first
   requests between lanes into the files yourself, so no lane starts by waiting to be asked.
2. **Three to eight lanes.** Fewer, larger lanes stall less than many small ones. Work that waits
   on something outside for weeks is its own lane, so it never blocks the others.
3. **Name the commander of each lane.** `arman` when he will start and steer the session himself
   (the usual case for a large project run across several chats). `lead` when one agent runs helper
   lanes itself. A project may mix both.
4. **Make each file:** `project_board.py --new-lane <project>/<lane> --title "..."`, then fill in:
   - **Goal:** what is true when the lane is finished.
   - **Owns:** task ids, folders, tables, accounts.
   - **Tasks:** five to fifteen, in order, each one session or less. Each line says what to do,
     where, what it needs, and "Done when" as a check anyone can repeat. The first task is always
     small and certain, so the lane starts by finishing something. No task is "review X" or
     "continue Y": name the result. A task that needs earlier ones ends `(after: 3, 4)`. A task
     that needs access says how to get it. The last task has an agent that did not do the work
     check each finished row before it is closed.
   - **Needs Arman:** anything already known.
5. **Every task in the project belongs to exactly one lane.** Check by listing the project's open
   items against the lanes' Owns sections; an item in no lane gets a lane or a line saying who has it.
6. **The project's front page gets a `## Lanes` section:** one line per lane with a link to its
   file and what it owns. Nothing else about lanes is kept there; standing is computed (§4).
7. **Have an agent that did not write the split attack it** ([plan-attack](/skills/plan-attack/SKILL.md)):
   overlapping ownership, a task with no check, a first task that can stall, work in no lane.
8. Push. Then give Arman, in chat, each lane's starting message (`--lane-prompt <project>/<lane>`)
   as its own block to paste, with your recommendation of which to start first.

Done when: `project_board.py --lanes <project>` lists every lane with no flags except "not started",
and he has the starting messages. A lane file that is not on main does not exist: push before you hand him anything.

## 4. Where the lanes stand

`python3 common-docs/meta/scripts/project_board.py --lanes <project>`: holder, hours since the last
check-in, tasks done and open, `next`, and what each waits on. Report that, and for anything on
Arman give him its one line. Never report a lane from memory of the chat.

## Traps

- "I will update the file at the end." A session can die at any moment; the check-in follows each task.
- Reporting to a lead or to Arman in chat is not a check-in. Only a pushed lane file is.
- A lane file that grows a history is being used wrong. Results holds a few lines per finished
  task; raw output goes in `tmp/<task>/` and anything worth keeping in storage, linked.
- A second list of the lane's tasks, in a chat, a task tool or another page, is a list that will
  disagree with the file. There is one list.
