# File mirror — RETIRED 2026-09-24; only its one-time cleanup remains

**Ruling (Arman, 2026-09-24):** turn off the old desktop mirror — it put placeholders
into iCloud, and the new folder sync replaces it. Folder sync from now on: the person
picks the exact folders; nothing syncs by default; synced files never appear in the
person's Recents. The new sync is the Rust daemon (`crates/matrx-sync`,
`crates/matrx-syncd`); its campaign lives in common-docs `projects/folder-sync/`.

## What the old mirror was

A background engine (Phase 2e in `app/main.py`, `/file-sync/*` routes, a File Sync card
in Configurations, setting `file_sync_mode`, default `pointers`) that copied the person's
WHOLE cloud Files tree into the Files folder (`~/Documents/Matrx/Files`, inside iCloud
"Desktop & Documents" on most Macs) — one zero-byte placeholder ("pointer") per cloud file
it had not downloaded. On Arman's Mac: 61,364 placeholders (60,475 of them coding-session
scratch files) next to 81 real files, and 135 false conflicts. Engine, routes, hydration
seam, UI and setting are deleted (no-legacy); nothing depends on them — the Rust folder
sync and notes sync (`app/services/documents/`) never read any of it.

## What is left here

| Piece | File | Notes |
|---|---|---|
| One-time cleanup | `retirement.py` | `retire_file_mirror()`, started in the background at Phase 2e. |
| Guard | `tests/unit/test_file_mirror_retirement.py` | Mixed synthetic tree: tracked placeholders, one that gained bytes, synced files, untracked files, a path escaping the root, a symlink leading out. |

The matrx-files REST client the mirror used lives on at `app/services/matrx_files/`
(shared by artifacts, book capture and the coding-session recorder).

## Cleanup invariants

- Removes ONLY a path the mirror's own tracking table (`file_sync_state`) records as
  `pointer`, that is a regular file (never a symlink or directory), still zero bytes, and
  inside the Files folder after resolving symlinks. A placeholder that gained content, a
  synced file, an untracked file (even an empty one) and a `conflict` row's file all stay.
- Then removes the folders that held a removed placeholder, and their parents up to (never
  including) the Files folder, only while empty (`os.rmdir` refuses anything else).
- Tracking table absent/unreadable → touches nothing, logs why. Files folder unreachable →
  touches nothing, keeps the rows, retries next start.
- Removed and already-gone placeholders lose their tracking rows; completion is recorded
  in `sync_meta` entity `file_sync.retirement` (`done`), so it runs once. A pass with
  errors records `partial` and runs again on the next start. One log line with counts,
  tagged `[file_sync_retirement]`.

When every installed copy has run it, delete this package, the `file_sync_state` table
(new migration) and the `file_sync` download category.
