# FEATURE — Documents / Notes Sync

**Before you touch ANY file in this directory, read
[`docs/SYNC_CONTRACT.md`](../../../docs/SYNC_CONTRACT.md).** It is the ratified
doctrine; the characterization tests
(`tests/characterization/test_documents_sync_characterization.py`, 22 tests)
and `tests/parity/test_sync_contract.py` enforce it. A red test means a
contract conversation, not a test edit.

## What lives here

- `sync_engine.py` — bidirectional notes sync (`push_all` / `pull_all` /
  `full_sync` / per-note), content-hash three-way conflict handling, file
  watcher. Manual-trigger + watcher; no automatic periodic reconnect sync
  (contract gap #7, by design today).
- `file_manager.py` — note files under `~/matrx-notes/`, `.sync/state.json`
  checkpoint, `.sync/conflicts/<id>/{local,remote}.md`, access guard.
- `supabase_client.py` — the ONLY wire client for cloud `workbench.notes`.
  ALL remote↔local column mapping lives in `_normalize_note_row`
  (`created_by`↔`user_id`, `deleted_at`→`is_deleted`). Skipping it resurrects
  deleted notes — that regression shipped once already.

Routes: ~40 endpoints in `app/api/document_routes.py` (outside this dir but
bound by the same contract).

## The invariants (never "fix" these away)

- **Local write first**; a failed network never blocks or fails a request.
- **Conflicts preserve BOTH copies** — no silent last-writer-wins, no
  hash-less overwrite. Missing/stale last-known-hash ⇒ assume unsynced local
  edit and conflict (pinned: `test_pull_conflicts_when_last_known_hash_missing`).
  Resolution modes: `keep_local` / `keep_remote` / `merge` / `append` /
  `split` / `exclude`.
- **Deletes are tombstones** on both sides (SQLite `is_deleted`, cloud
  `deleted_at`); tombstones must survive long enough to propagate.
- **Echo suppression stays:** pulls whose content hash matches
  `_last_push_hashes` are skipped.
- Cloud is durable truth; local is the first-access replica the user actually
  touches. Never invert that.

## Known sharp edges

- **Folder delete is destructive/untombstoned** (contract gap #2, open defect
  `KNOWN_DEFECTS.md` MXL-D-002) — `delete_folder` hard-deletes locally with no
  per-note tombstones; an offline folder delete resurrects on `full_sync`.
- **`full_sync` does not pull remote deletions** (gap #4) — the incremental
  `pull_changes` path is the delete-propagation channel.
- This subsystem was stabilized in 9ca565245/722f4889d: **codify, don't
  rearchitect.**
