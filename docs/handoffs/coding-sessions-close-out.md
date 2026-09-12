# Coding Sessions — close-out

**Owner:** the session Arman asked on 2026-09-11 to "get this done and closed."
**Goal:** the Coding Sessions screen shows every conversation on this Mac, says
truthfully whether each one is in AI Matrx, and syncs everything in one action —
with nothing invisible, nothing racing, and nothing slow enough to feel broken.
**Rule for whoever picks this up:** finish the open items below in order, tick
them here as you go, push after each one. Do not run a release — the deploy
agent ships from `main`.

## Where it stands (measured 2026-09-11, live disk, not docs)

| Fact | Value |
|---|---|
| Transcripts on this Mac | 1,594 |
| …with a Claude sidebar index record (visible on the screen) | 1,514 |
| …with NO index record anywhere (**invisible on the screen**) | **80**, 113 MB — real work: matrx-trials runs, matrx-frontend, aidream |
| Coding Sessions page | `desktop/src/pages/CodingSessions.tsx` (714 lines), judged against the server's own inventory since 2026-09-08 |
| Overview endpoint | `GET /coding-session/claude/overview` → `app/services/coding_sessions/claude_overview.py` |
| One-button sync | `POST /coding-session/claude/sync` → `ClaudeHistoryImporter.sync_all()` |
| Typecheck | clean |
| Repo smoke gate (`./scripts/smoke.sh`) | **RED** — but for another lane (see Blocked, below) |

Already shipped earlier in this thread and NOT to be redone: the "database is
locked" fix + CORS-visible 500s + failure logging (`a916aeaf6`), the
one-list-one-button screen (`f835747a4`), all four providers + a real scroll
region (`ec5e4dc19`), and the 2026-09-08 cloud-truth rework by a sibling
session (`9c3026d61`, `33756565d`).

## Open items — do these, in this order

### 1. The 80 invisible conversations  — [ ]
A conversation only appears if Claude Desktop wrote a sidebar index record for
it. CLI-only sessions never get one, so 80 transcripts (growing: 60 on 08-30,
80 on 09-11) exist on disk, can be read, and can be synced — but the screen has
never listed them and the sync never touched them. Arman's standing rule is
"its job is to take everything we have and sync it."

Fix: `claude_overview.overview()` iterates the index (`entries`). After that
loop, add every transcript in `_transcripts()` whose id is not in `entries`,
as a row with `title` = first user message (fall back to "Untitled"),
`project` = decoded folder name, `last_activity_at` = file mtime, and a new
`source: "transcript_only"` field so the UI can label it "Not in Claude's
sidebar". Then make `sync_all()` include them — it walks
`_discover_sources()` which already reads the projects tree, so verify they
are already picked up there and only the screen was hiding them.

Proof: overview totals.conversations == transcripts on disk (1,594 today);
a Playwright assertion that the count in the header equals the number of
`.jsonl` files under `~/.claude/projects`.

### 2. MXL-D-085 — 12 unguarded write spans in `title_sync.py`  — [ ]
`a916aeaf6` put the bridge's short connections and `_start_operation` behind
the in-process write gate. 12 more `execute…commit()` spans in
`app/services/coding_sessions/title_sync.py` still write on the shared
connection without it and can hit "database is locked" under hook load.
Each span = first write statement → `await self._db.commit()`; wrap each in
`async with write_gate():`. One at a time; keep every span's transaction
boundary exactly where it is. Proof: `grep -c "write_gate()"` == number of
commit sites (13), plus the existing tests green.

### 3. Cold start — [x] code landed, live timing still owed
`claude_overview.warm_index_cache()` is the public warm-up; `app/main.py`'s
lifespan fires it as a background task at Phase 2h.2, so the read happens
while nobody is waiting and the first open hits a warm cache. It never blocks
startup and logs a warning if it fails (the screen still reads on demand).
Unit proof: `tests/unit/test_claude_overview_state.py::test_warm_index_cache_fills_the_cache_so_the_first_screen_open_is_free`.

Two related fixes went with it: `MAX_INDEX_FILES` is 250,000 (this Mac's
50,000+ records were hitting the old 50,000 cap and silently truncating the
list), and the overview now reports `totals.index_limit_reached` so the screen
says so out loud if it is ever hit again.

**Still owed (needs a running engine — item 4's lane):** the live timing, first
page open after engine start returns in < 2s.

### 4. Verify on the real thing — [ ]
Web smoke has no engine, so `e2e/coding-sessions-diagnosis.spec.ts` can only
pass against a running engine. Run the dev engine against a COPY of
`~/.matrx/matrx.db` (`cp`, not sqlite `.backup`) with `MATRX_HOME_DIR` set —
it runs with cloud participation off, so it is a safe replica — and run both
coding-session specs against it. Then run `./scripts/smoke.sh`.

### 5. Close the books — [ ]
Delete MXL-D-085 from `FOUND_DEFECTS.md`, add the completed line to
`.matrx/AGENT_TASKS.md`, update `app/services/coding_sessions/FEATURE.md`
if the overview contract changed, delete this file.

## Blocked — not this lane's to fix

**The repo smoke gate is red on `main` today because of the agent picker,
not Coding Sessions.** `e2e/boot.spec.ts` "authenticated shell boots with no
uncaught errors" fails on a `console.error` from the offline agent catalog:
`agx_resolve_agent_address` is deliberately not mirrored offline, the catalog
reports that loudly by design (`desktop/src/lib/agent-catalog.ts`, "nothing
silent, nothing faked"), and the boot test treats any `console.error` as
fatal. Two contracts from two lanes disagree. The right call is the agent
picker lane's (log the known offline limitation as a state, or don't resolve
the default row while offline); silencing it here would be reverting a
safety gate. Filed as MXL-D-087 in `FOUND_DEFECTS.md`. Until it is fixed,
smoke will show 1 failure that is not ours — do not let that stop pushes.
