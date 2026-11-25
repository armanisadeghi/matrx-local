---
name: instruction-files
type: Skill
title: Instruction files (CLAUDE.md)
description: "The rules for every CLAUDE.md in the repo. Use when adding, changing or removing any line in a CLAUDE.md, creating one for a new project, or asked to add a rule, law or notice that agents must follow. NOT for FEATURE.md or other docs (use docs)."
---

<!-- SYNCED COPY — do not edit here.
     Canonical: common-docs/skills/instruction-files/SKILL.md
     This file is distributed to every consuming repo by
     common-docs/meta/scripts/sync_skills.py. Edit the canonical, run the
     sync, and commit each repo. Edits made here are overwritten and lost. -->

# Instruction files (CLAUDE.md)

The root `CLAUDE.md` loads for every agent on every task. A project's `CLAUDE.md` loads for every agent that works in that project, after the root. Every line costs every one of those agents, so a line earns its place or it goes somewhere that loads only when needed.

## Where a new rule goes

Work down this list and stop at the first that fits.

1. **It is already written.** Search the root file, the project file and the skills for the idea and its other words. Change that one line. Never add a second statement of a rule.
2. **Nearly every agent needs it, whatever the task.** One line under "How to work" in the root file. Adding a rule there is Arman's call: propose the exact line to him first.
3. **Nearly every agent working in one project needs it.** One line under that project's rules.
4. **It matters for one kind of task.** Put the rule in that task's skill or page. Add a row to the task table only when no row already covers the task. A row points to one skill or one page.
5. **It describes how something works.** It is not a rule. It goes in the `FEATURE.md` beside the code.

## The shape of the files

- **Root:** temporary rules and urgent notices at the bottom; what the repo is and its tree; the two principles; live addresses; where things go; how to work; the task table; commands.
- **A project:** one or two sentences on what it is; the stack; rules for all work in the project; the task table; commands. Nothing else.
- A project file never repeats the root file. An agent has always read the root first.
- A block a tool writes into a file (`<!-- BEGIN:... -->` to `<!-- END:... -->`) is left exactly as it is.

## How a line is written

- State the fact. One line. Name the command, component or page.
- No dates, no quotes, no names of who decided it, no story of how it came about, no incident.
- No capitals for emphasis and no alarm marks.
- No path that exists on one person's machine. Paths are relative to the repo or the project.
- Never an open list of pages to "read once". A page is linked from a task row or not at all.
- Every path, skill and command you name must exist. Check before you write it.

## Limits

- The root file stays under 220 lines. A project file stays under 180.
- A rule is one line of about 300 characters at most.
- When a file is at its limit, something moves out before anything comes in.

## Taking something out

Nothing is deleted. Text that leaves an instruction file is moved word for word into the `FEATURE.md` beside the code it describes, and its relative links are re-pointed to work from there. Only an exact repeat of the root file is dropped.

## Temporary rules

The section before urgent notices in the root file. It holds a rule every agent needs now, where the real fix is something that is not built yet.

- One line per rule. It says what to do, then ends with `Until: <what replaces it>.`
- A rule goes here only when you can name what replaces it. A rule with no replacement is a permanent rule and follows "Where a new rule goes".
- Adding one is Arman's call, like any root line.
- Whoever ships the replacement deletes the line in the same change.
- A line in a project file needs no approval from Arman; a line in the root file does.

## Urgent notices

The last section of the root file. One line per item, written by hand or by the release tooling.

- Tell Arman about every line that is not muted.
- When he says to mute one, add `(muted until <date and time>)` to the end of the line.
- Whoever fixes the problem deletes its line.
- Nothing else is ever written into an instruction file by a script.

## Before you finish

- Run `python3 scripts/check_instruction_files.py` from the repo root. It reports; it never blocks.
- In the web app, also run `pnpm check:doc-claims`: it reads that project's file.
- Search for any other script or test that reads the file you changed, and run it.
