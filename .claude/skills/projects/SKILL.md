---
name: projects
subject: policies/projects
type: Skill
title: "projects — take a project, find the stalled ones, check one against the vision"
description: "The project board and card. Use when told to take, resume or pick up a project or lane, to find stalled or cold projects, to say how close a project is to what Arman asked for, when starting a new project, or when committing work that belongs to one. NOT for a full lead-to-lead handoff (use handoff)."
tags: [projects, coordination, fleet, vision, handoff]
timestamp: 2026-11-14T00:00:00Z
---

<!-- SYNCED COPY — do not edit here.
     Canonical: common-docs/skills/projects/SKILL.md
     This file is distributed to every consuming repo by
     common-docs/meta/scripts/sync_skills.py. Edit the canonical, run the
     sync, and commit each repo. Edits made here are overwritten and lost. -->

# projects

Part of a coding project? The rules live in [ai-dream-coding-projects](/skills/ai-dream-coding-projects/SKILL.md): a new project starts there, and where this skill disagrees with it, the rulebook wins.

<!-- rules:stamp -->
> Rules from `common-docs/policies/projects.md` — edit there, never here (`python3 common-docs/meta/scripts/one_home.py --write`).

1. **Every project has one card.** It is frontmatter on the project's front page: the one file an
   incoming agent reads first (the handoff if there is one, else the register, else the plan). Exactly
   one file in a project folder carries the card (the one with `state:`); other pages may name the
   project with `project:` alone.

   ```yaml
   project: <folder name>
   state: proposed | ready | building | paused | finished
   next: "<the very next step, one sentence a stranger can act on>"
   waiting_on: "<nothing | arman: what he must decide or do | project: slug>"
   lead: "<set by --claim: the session that has it now>"
   lead_since: "YYYY-MM-DD"
   origin: "<link or id of the conversation where Arman first described it>"
   ```

   `proposed` = a plan he has not approved. `ready` = approved, nobody has started. `building` = work
   under way. `paused` = parked on purpose; `waiting_on` says for what. `finished` = a vision check
   found every line of the bar met; his words move to the node's `VISION.md` and the folder is
   deleted in the same change (rule 3 of the bundle).

   **Linked projects and "Remind Arman" lines.** `waiting_on` records what this project waits on.
   The front page also carries `## Linked projects`: each linked project is named on both sides, and
   when finishing work here unblocks other projects, a one-line `Remind Arman: <A> and <B> depend on
   this; they need <point X>.` Any agent working on a task that touches such a line tells him, in
   chat, what finishing it will unblock. The two are different: the field tracks waiting, the line
   makes an agent remind him ([ai-dream-coding-projects](/skills/ai-dream-coding-projects/SKILL.md) §9).

   **The to-do list lives in the project.** The project doc keeps its own detailed to-do list of what
   is not yet live, deleting each line as it becomes true. A node task file holds only work nobody is
   doing now; a task a project takes on moves into the project's list (Arman, 2026-11-17). The list
   is written for a stranger with no access to the lead's chat: each line says what to do, where it is
   done (the exact repo, Arman's Mac, the live site, or needs Arman), what it needs (access, a key, a
   release), the link to its plan and how you know it is done, and lines are grouped by where they
   are done. No line is only "not done": it has a plan or names what it waits on, and a wait on Arman
   names the exact question. A lane that is not the lead takes one task without claiming the
   project: it writes `(taken: <who/where>, YYYY-MM-DD)` on the line and pushes that to main before
   working; a mark with no commit for 2 days is free again. The lane deletes the line when the task
   is live, even when the work was in another repo: the project page is the one list, and every other
   repo the project touches carries one pointer line to it in its `CLAUDE.md` or `AGENTS.md`
   ([his words](/policies/VISION.md#ai-dream-coding-projects), 2026-11-19; rulebook §3b).
2. **Stalled is computed, never written.** A `ready` or `building` project with no movement for 5
   days is stalled. Movement is a commit carrying the line `Project: <slug>`, or one that touches the
   project's folder and is not bulk housekeeping. Claims, the sweep's repairs and vision checks carry
   `Project-Board:` and never count.
3. **Write your name before you work.** `project_board.py --claim <slug>` puts your session on the
   card, pushes, and says whether the claim reached main. From then on the commit hook adds
   `Project: <slug>` to your commits by itself. A lead holds a project for 2 days from the claim and
   from each real commit for it; after that anyone may claim it. A claim that is only on a branch is
   invisible to everyone else until it merges: say so to Arman. Leaving for any reason you can
   foresee = `--release <slug>` with `state`, `next` and `waiting_on` brought up to the minute.
   While you hold it, the project-focus hook (`aidream/scripts/hooks/project_focus_hook.py`) puts the
   card in front of you at every start, reset and message: open it first, answer against it, and
   update `next` and `waiting_on` before ending each turn (rulebook §14).
4. **The card is updated when a step lands, not at the end.** A session can die without warning, so
   `next` always names the step after the last one pushed. A card is never left for a wind-down.
5. **His words are kept once, and captured where he says them.** The agent he is talking to adds
   what he said to the `VISION.md` that already serves the project (its own, else the nearest one
   above it), verbatim and dated, in the same turn, and records `origin` on the card the first time.
   A second vision file is never made by copying the first, and no plan, register or handoff text is
   ever copied into one. The one exception: an agent running the
   [ai-dream-coding-projects](/skills/ai-dream-coding-projects/SKILL.md) skill with him live may clean
   up his words in the project's vision, keeping his meaning and talking with him about any clean-up
   that could change it. Outside that skill (and the vision close-out interview,
   [vision-closeout](/skills/vision-closeout/SKILL.md)), nobody changes his words.
6. **The bar is the measure, and its first line never changes.** `VISION.md` has a section
   `## The bar`: a numbered list in his own words with dates. Line 1 is why the project exists, taken
   from the conversation where he first described it; nobody edits it. Every other line is one thing
   he said must be true when it is done. A line is added or replaced by a sentence he typed, with where he typed
   it, or by an agent's suggestion he clearly agreed to, marked `(agreed YYYY-MM-DD)` (Arman,
   2026-11-17). His pick among an agent's options is a decision for the plan, not a line of the bar.
7. **The builder never says how close it is.** No lead, lane or handoff reports closeness to the
   vision, percent done, or "nearly there". The only closeness statement is the last vision check's
   verdict on line 1 and its one sentence, quoted with its date. A completion percentage, when
   one is given, is only the count defined in
   [ai-dream-coding-projects](/skills/ai-dream-coding-projects/SKILL.md) §7: items of his latest
   vision that are built as he said, ruled so by an adversarial review, and tested live against
   his vision and passing, out of all items.
8. **A vision check is made and recorded by an agent that did not build it.** It is given the
   project's `VISION.md` and the live product, and nothing the builders wrote. It writes its own
   result to `VISION-CHECK.md` in the project folder and commits that file itself; the board reads
   the result from there, never from the card, and calls out a check committed by a session that also
   worked on the project. A check is due when new hands have worked on the project since the last
   one (the board detects this from the commits), before any project is called `finished`, and when
   he asks. No bar yet is no reason to wait: the check then runs on his words as they stand.
9. **The sweep repairs what a dead session left.** A scheduled agent finds stalled projects and
   conversations that went cold, brings `next`, `waiting_on` and `lead` on each card up to what the
   commits and the session's last turns show, marks each repaired line `(sweep, believed)` inside its
   quotes, and takes the projects it visited longest ago first. It never marks work done on a
   builder's say-so, never runs a vision check itself (it dispatches one), and never changes a
   `VISION.md`, a plan's intent, or code.
10. **A project too big for one session is split into lanes, and every lane says who commands it.**
    A lane is one file, `lanes/<lane>.md` in the project folder: its goal, what it owns, an ordered
    list of tasks each small enough for one session with a "Done when" anyone can repeat, what it
    needs from Arman, requests from other lanes, and results. `commander: arman` means he starts and
    steers that lane himself and no agent stands between them; `commander: lead` means the
    project's lead agent does. Whoever holds a lane, its commander does not change. The lane file
    is the lane's one home: its holder writes there, so lanes on different machines never edit the
    same file, and a lane asks another lane for something by adding a line to that lane's Requests.
    - **A check-in is `project_board.py --checkin`:** the lane file pushed to main in a commit that
      carries `Lane: <project>/<lane>`. It is made after every finished task and before every turn
      ends. A chat report is not a check-in, and neither is any other commit that happens to touch
      the file.
    - **A holder stops only when no task is workable.** A task it cannot finish gets
      `(waits: who or what, what was tried, what is needed)` and the holder takes the next one. A
      task that needs earlier ones says `(after: 3, 4)`. A lane is `waiting` only when every open
      task carries a waits mark, and its `waiting_on` is `arman: <the line he must answer>` (also
      written under Needs Arman), or `outside: ...` or `lane: ...` with a `check again YYYY-MM-DD`
      date no more than 14 days out. A lane with a task open is never `done`.
    - **The holder never accepts its own work.** A row of the project's bar or register closes only
      on the yes of an agent that did not do the work (rules 7 and 8).
    - **Alarms are computed, never written.** The clock runs from the last check-in, or from the
      moment the present holder took the lane when that is later. 24 hours: quiet. 48 hours: stuck,
      and any agent may take it. 72 hours: abandoned; an agent with no task from Arman takes it at
      once, and an agent that has one tells him in one line and starts a session on it where its
      harness can. A lane waiting on Arman, and a lane he commands that nobody has started, are
      his: never taken, and he is reminded. Every session start shows the stuck lanes, and the sweep
      acts on them and tells him. A lane that reaches 72 hours also sends him one text.
    ([his words](/policies/VISION.md#ai-dream-coding-projects), 2026-11-24.)
<!-- rules:stamp-end -->

The board is one command, computed fresh each run from the cards, the checkers' files and git:

```bash
python3 common-docs/meta/scripts/project_board.py            # everything
python3 common-docs/meta/scripts/project_board.py --stalled  # free to take, then waiting on him
python3 common-docs/meta/scripts/project_board.py --project <slug>
python3 common-docs/meta/scripts/project_board.py --claim <slug>
python3 common-docs/meta/scripts/project_board.py --lanes <slug>   # a split project's lanes
python3 common-docs/meta/scripts/project_board.py --stuck          # lanes with no check-in for 24 h or more
```

A cloud or sandbox checkout has only a few hours of history. The board then prints a NOTE on its
first line and marks nothing stalled. Add `--deepen` once (about two minutes) for the real numbers.

## Which part you need: read only that

| You were told | Do |
|---|---|
| "take / resume / pick up <project>" | §1 |
| "find stalled projects", "what needs progress", "pick one and run" | §2 |
| "how close is <project>", "compare it to what I asked for", or the board says a check is owed | [vision-check.md](vision-check.md) |
| "take lane X", a stuck lane shows at session start, "split this into lanes", "where do the lanes stand" | [lanes.md](lanes.md) |
| you are the scheduled project sweep | [sweep.md](sweep.md) |
| he is describing something new that will take more than one session | §3 |
| you are committing work for a project | §4 |

## 1. Take a project

1. `project_board.py --project <name>`. A wrong name prints the near matches, including work that is
   tracked at a feature's own folder instead of a project folder; never start a new project because
   a name missed. Read the file it lists under "his words" in full, before any plan or register.
2. `project_board.py --claim <slug>`, before anything else.
   - `TAKEN`: tell Arman which session has it and since when, and stop unless he says to take it
     anyway (`--force`).
   - "on main": you have it.
   - "pushed on branch … but NOT on main": no one else can see your claim. Open the pull request
     now and tell Arman; if the card on main names someone else by the time it merges, stand down.
3. **New hands mean a vision check.** Dispatch it now ([vision-check.md](vision-check.md)) and keep
   reading while it runs. You never run it yourself: you are the builder from this moment.
4. The board's flags are your first work. `no vision captured`: his words are in the conversations
   the card's `origin` and the board's session list point to; extract them
   ([user-ground-truth §1](/skills/user-ground-truth/SKILL.md)). Cannot reach those conversations
   from this account: say so to Arman and ask him for the words. Never fill a vision file from a
   plan, a register, a handoff, or another vision file.
5. A `next` that ends `(sweep, believed)` is a guess from notes. Check it against the front page
   and the code before acting on it.
6. Was there a lead before you and a `HANDOFF.md`? Continue with [handoff](/skills/handoff/SKILL.md)
   as the incoming lead; that skill owns the takeover from here.
7. Tell Arman in chat: where it stands today, the vision check's verdict on line 1 and its misses,
   your next step. Then work. Each time a step lands, update `next` in the same push (rule 4).

Done when: `--project <slug>` shows you as lead and a vision check dated after your claim is in the
folder or has been dispatched.

## 2. Find the stalled ones

1. `project_board.py --deepen --stalled`. Three lists: free for any agent, waiting on Arman, waiting
   on another project.
2. Rank the free list by reach, never by size of the folder or number of commits
   ([impact is reach](/policies/impact-is-reach-not-volume.md)): a stalled primitive outranks a
   stalled feature.
3. For the top few, read the front page and say in one line each what it would take to move.
4. **Told to bring a list:** give him the list in chat, plain names, no paths: what it is, how long
   it has been quiet, your recommendation first. Each item waiting on him is a question for the
   question tool, not a row to read.
   **Told to pick one and run:** take the top free one through §1, and tell him which one and why
   before building past its first step. A project he walked away from may be one he dropped.

Done when: he has the list, or `--claim` succeeded.

## 3. Start a project

The rules for a new project (his words first, then search for an existing project and ask him
"are these the same thing?") are the rulebook's §1–§5
([ai-dream-coding-projects](/skills/ai-dream-coding-projects/SKILL.md)). This section is only the
board mechanics. A side task never starts a project: it joins the running project it belongs to
(rulebook §0).

1. Folder: `projects/<slug>/` inside the home it belongs to (the node's folder in `common-docs`;
   create the `projects/` folder there if it is missing). Only a project that cannot have a permanent
   home goes in the bundle's top `projects/` ([rule 3](/policies/document-types.md)). Every project,
   wherever it lives, follows the same rules and shows on the board.
2. His words first. If the home already has a `VISION.md`, add to it; else create one in the
   project (`type: Vision`, `authority: owner`). Verbatim and dated, pulled from the conversation
   itself, never from memory of it ([user-ground-truth §1](/skills/user-ground-truth/SKILL.md); a
   session with no local transcripts reads its own turns).
3. `## The bar` in that file. Line 1: why this exists, his sentence from this conversation. Then one
   line per thing he said must be true when it is done, each his own words with the date. Show him
   the list in chat and ask what is missing or wrong; what he types back goes in as typed.
4. The front page with the card: `state: proposed` until he approves a plan, `origin` set to this
   conversation. Then `--claim <slug>`.

Done when: `project_board.py --check` prints nothing for the folder and the bar has been shown to him.

## 4. Commit for a project

Once you have claimed, the commit hook adds `Project: <slug>` to every commit you make, code
included. A lane you dispatch, or a session with no id in its environment, writes that line itself.
It is how the board knows the project moved and that you still hold it.

## Traps

- A status word is not evidence; only the board's "moved" figure and a vision check are.
- "Nearly done" from the agent doing the work is the one sentence rule 7 forbids. Quote the last
  vision check's verdict with its date, or say none has been run.
- A lead that has gone quiet does not hold a project. Do not wait for it.
- A project that stopped moving because it is one long page is split into lanes ([lanes.md](lanes.md) §3), not handed to a bigger agent.
- Finished means a check found the whole bar met, his words moved to the home's vision file, and
  the folder deleted. A `finished` card still on the board is a deletion owed.
