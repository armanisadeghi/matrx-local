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

<!-- rules:stamp -->
> Rules from `common-docs/policies/projects.md` — edit there, never here (`python3 common-docs/meta/scripts/one_home.py --write`).

1. **Every project has one card.** It is frontmatter on the project's front page: the one file an
   incoming agent reads first (the handoff if there is one, else the register, else the plan). Exactly
   one file in a project folder carries `project:`.

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
4. **The card is updated when a step lands, not at the end.** A session can die without warning, so
   `next` always names the step after the last one pushed. A card is never left for a wind-down.
5. **His words are kept once, and captured where he says them.** The agent he is talking to adds
   what he said to the `VISION.md` that already serves the project (its own, else the nearest one
   above it), verbatim and dated, in the same turn, and records `origin` on the card the first time.
   A second vision file is never made by copying the first, and no plan, register or handoff text is
   ever copied into one.
6. **The bar is the measure, and its first line never changes.** `VISION.md` has a section
   `## The bar`: a numbered list in his own words with dates. Line 1 is why the project exists, taken
   from the conversation where he first described it; nobody edits it. Every other line is one thing
   he said must be true when it is done. A line is added or replaced only by a sentence he typed,
   with where he typed it; his pick among an agent's options is a decision for the plan and never a
   line of the bar.
7. **The builder never says how close it is.** No lead, lane or handoff reports closeness to the
   vision, percent done, or "nearly there". The only closeness statement is the last vision check's
   verdict on line 1 and its one sentence, quoted with its date.
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
<!-- rules:stamp-end -->

The board is one command, computed fresh each run from the cards, the checkers' files and git:

```bash
python3 common-docs/meta/scripts/project_board.py            # everything
python3 common-docs/meta/scripts/project_board.py --stalled  # free to take, then waiting on him
python3 common-docs/meta/scripts/project_board.py --project <slug>
python3 common-docs/meta/scripts/project_board.py --claim <slug>
```

A cloud or sandbox checkout has only a few hours of history. The board then prints a NOTE on its
first line and marks nothing stalled. Add `--deepen` once (about two minutes) for the real numbers.

## Which part you need: read only that

| You were told | Do |
|---|---|
| "take / resume / pick up <project>" | §1 |
| "find stalled projects", "what needs progress", "pick one and run" | §2 |
| "how close is <project>", "compare it to what I asked for", or the board says a check is owed | [vision-check.md](vision-check.md) |
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

1. Folder: `projects/<slug>/` inside the home it belongs to, or the bundle's top `projects/` while
   it spans homes ([docs skill §1](/skills/docs/SKILL.md)).
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
- Finished means a check found the whole bar met, his words moved to the home's vision file, and
  the folder deleted. A `finished` card still on the board is a deletion owed.
