---
name: disk-groom
subject: operations/disk-grooming
type: Skill
title: "disk-groom — leftover Docker builds and agent worktrees"
description: "Permanent-delete grooming for leftover Docker builds, agent worktrees, and .next caches. Use when creating or finishing a git worktree, after a local docker build, when the disk is full, or when /tmp or .wt trees were left behind."
tags: [operations, docker, worktrees, disk]
timestamp: 2026-09-19T00:00:00Z
---

<!-- SYNCED COPY — do not edit here.
     Canonical: common-docs/skills/disk-groom/SKILL.md
     This file is distributed to every consuming repo by
     common-docs/meta/scripts/sync_skills.py. Edit the canonical, run the
     sync, and commit each repo. Edits made here are overwritten and lost. -->

# disk-groom — leftover Docker builds and agent worktrees

The disk fills because agents build Docker images 20–30 times a day and
leave worktrees / `.next` trees behind. Deletes are permanent. Never
Trash.

Read the register: `common-docs/operations/disk-grooming.md`.

## When you create a worktree

1. Put it at `<repo>/.wt/<slug>` only.
2. Register it:
   `python3 /Users/armanisadeghi/code/common-docs/meta/scripts/disk-groom/groom.py register <path> --purpose "<why>" --ttl-hours 24`
3. When the task ends, `groom.py remove <path>`. Not Finder. Not Trash.

## When you build a Docker image locally

After the build (or after compose up):

`python3 /Users/armanisadeghi/code/common-docs/meta/scripts/disk-groom/groom.py docker --apply`

That keeps running containers and prunes unused images, anonymous volumes
and BuildKit cache down to 8 GB. Named volumes (local site databases) stay
unless you pass `--volumes`. Old SHA tags from the last twenty pushes are the
thing that filled a 4 TB disk. The hourly run repeats the prune whenever
Docker is up and the last good one is over 24 h old, because Docker is
usually off at the 04:15 daily run.

## When the disk is tight

`python3 /Users/armanisadeghi/code/common-docs/meta/scripts/disk-groom/groom.py temp --apply`
then `groom.py dev-caches --apply`.

launchd already runs `temp` hourly and `dev-caches` daily at 04:15. Do not
invent a second cleaner. Do not `mv` anything to `~/.Trash`.

`temp` never deletes a git work tree (the entry itself or within 2 levels, e.g.
`/private/tmp/lane.*/x`) while it has uncommitted changes, commits not on its
remote, a process holding it open, or is under 48 h old by newest mtime. Each
skip is logged as `KEEP git work tree: <reason>`. After 48 h a clean one is
renamed to `<name>.groom-deleting` and then removed, so it is never left
half-deleted. Prove it with `groom.py selftest-git-guard`.

## When your script parks copies

Put throwaway copies in `mktemp -d` and delete them in an EXIT trap. If they
must outlive a reboot and live under `$HOME`, add the folder to
`SCRATCH_RUN_ROOTS` in `groom.py` (keep newest N), never leave them unbounded.

## Never put code where Apple syncs or backs up

Nothing coding-related (repos, `node_modules`, `.venv`, Codex or Claude
workspaces, scratch) goes under `~/Documents`, `~/Desktop` or iCloud Drive:
on 2026-11-06, 1.85M Codex automation files there were syncing to every Mac.
Codex workspaces live in `~/code/agent-chats/codex`. The daily run re-applies
Time Machine exclusions and prints `ICLOUD CODE <path>` (and fails) when code
shows up in iCloud Drive. A new Mac gets the schedule with
`zsh common-docs/meta/scripts/disk-groom/install_launchd.sh`.
