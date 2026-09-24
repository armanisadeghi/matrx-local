# Merge conflicts from scripts/sync-main.py

This folder is permanent. When every list below is empty, nothing from `scripts/sync-main.py` is
open in this repo. (`scripts/sync-main.py` removes empty folders left inside it on every run.)

## What the items are
- **Held file** — `_conflicts/<stamp>/<path>.held`. LOCAL and GITHUB changed the same code.
  GITHUB's version is live in the repo at `<path>`; LOCAL's version is inside the `.held` file,
  below a FACTS block computed from git. Both versions stay in git permanently; each `.held` file
  has the `git show` commands that print either one.
- **Docs/comments, both versions kept** — a file in the repo where a clashing passage now holds
  both versions between three marker lines (LOCAL first, then GITHUB).

## Marking an item done
- Held file: the final code is in `<path>`, the `.held` file is deleted, its line below is deleted.
- Docs/comments: the passage is edited, the three marker lines are deleted, its line below is deleted.
- `python3 scripts/check-conflict-markers.py` lists everything still open, or prints `clean`.
- `python3 scripts/sync-main.py` commits and syncs.

## Escalation
An item is passed up by moving its line to the next section (Needs a manager -> Needs the boss
agent -> Needs Arman) with ` — <question> — <what was checked> — <who>` added to the end of it.
Its files stay as they are.

## Held files
- _conflicts/2026-09-24-144022/config/runtime-manifests/image-gen-aarch64-apple-darwin.json.held — LOCAL latest 2026-09-24 10:48; GITHUB latest 2026-09-24 10:05; LOCAL lacks 1 of GITHUB's 1 new lines; GITHUB lacks 1 of LOCAL's 1 new lines; recover: git show d0ee5ddc8d:'config/runtime-manifests/image-gen-aarch64-apple-darwin.json' / 9142f1f2f6:'config/runtime-manifests/image-gen-aarch64-apple-darwin.json'
- _conflicts/2026-09-24-144022/config/runtime-manifests/image-gen-x86_64-pc-windows-msvc.json.held — LOCAL latest 2026-09-24 10:48; GITHUB latest 2026-09-24 10:05; LOCAL lacks 1 of GITHUB's 1 new lines; GITHUB lacks 1 of LOCAL's 1 new lines; recover: git show d0ee5ddc8d:'config/runtime-manifests/image-gen-x86_64-pc-windows-msvc.json' / 9142f1f2f6:'config/runtime-manifests/image-gen-x86_64-pc-windows-msvc.json'
- _conflicts/2026-09-24-144022/config/runtime-manifests/image-gen-x86_64-unknown-linux-gnu.json.held — LOCAL latest 2026-09-24 10:48; GITHUB latest 2026-09-24 10:05; LOCAL lacks 1 of GITHUB's 1 new lines; GITHUB lacks 1 of LOCAL's 1 new lines; recover: git show d0ee5ddc8d:'config/runtime-manifests/image-gen-x86_64-unknown-linux-gnu.json' / 9142f1f2f6:'config/runtime-manifests/image-gen-x86_64-unknown-linux-gnu.json'
- _conflicts/replay-2026-09-24-145022/config/runtime-manifests/image-gen-aarch64-apple-darwin.requirements.txt.held — LOCAL latest 2026-09-24 10:48; GITHUB latest 2026-09-24 10:05; LOCAL lacks 38 of GITHUB's 38 new lines; GITHUB lacks 38 of LOCAL's 38 new lines; recover: git show d0ee5ddc8d:'config/runtime-manifests/image-gen-aarch64-apple-darwin.requirements.txt' / 9142f1f2f6:'config/runtime-manifests/image-gen-aarch64-apple-darwin.requirements.txt'
- _conflicts/replay-2026-09-24-145022/config/runtime-manifests/image-gen-x86_64-pc-windows-msvc.requirements.txt.held — LOCAL latest 2026-09-24 10:48; GITHUB latest 2026-09-24 10:05; LOCAL lacks 39 of GITHUB's 39 new lines; GITHUB lacks 39 of LOCAL's 39 new lines; recover: git show d0ee5ddc8d:'config/runtime-manifests/image-gen-x86_64-pc-windows-msvc.requirements.txt' / 9142f1f2f6:'config/runtime-manifests/image-gen-x86_64-pc-windows-msvc.requirements.txt'
- _conflicts/replay-2026-09-24-145022/config/runtime-manifests/image-gen-x86_64-unknown-linux-gnu.requirements.txt.held — LOCAL latest 2026-09-24 10:48; GITHUB latest 2026-09-24 10:05; LOCAL lacks 38 of GITHUB's 38 new lines; GITHUB lacks 38 of LOCAL's 38 new lines; recover: git show d0ee5ddc8d:'config/runtime-manifests/image-gen-x86_64-unknown-linux-gnu.requirements.txt' / 9142f1f2f6:'config/runtime-manifests/image-gen-x86_64-unknown-linux-gnu.requirements.txt'
- _conflicts/replay-2026-09-24-145022/desktop/pnpm-lock.yaml.held — LOCAL latest 2026-09-24 10:48; GITHUB latest 2026-09-24 10:03; LOCAL lacks 14 of GITHUB's 14 new lines; GITHUB lacks 40 of LOCAL's 40 new lines; recover: git show d0ee5ddc8d:'desktop/pnpm-lock.yaml' / 9142f1f2f6:'desktop/pnpm-lock.yaml'

## Needs a manager

## Needs the boss agent

## Needs Arman

## Docs and comments — both versions kept
