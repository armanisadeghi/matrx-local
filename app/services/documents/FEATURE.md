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
  watcher, and the **engine-owned auto-sync loop** (`start_background_sync`,
  main.py Phase 2c: 10-min incremental pull + pending push, daily full
  reconcile, credentials from the persisted `auth_tokens` row). The watcher
  auto-starts on documents traffic and in the auto-sync tick.
- `file_manager.py` — note files under `~/matrx-notes/`, `.sync/state.json`
  checkpoint, `.sync/conflicts/<id>/{local,remote}.md`, access guard.

## Access-degraded is a STATE, not an error stream (2026-07-13)

When the OS blocks the notes dir (macOS without Full Disk Access, folder
permissions, or a missing folder), `notes_access_guard` in `file_manager.py`
is the single choke point:

- **One WARN per degradation** + `registry.degraded("notes_sync", reason)`;
  a successful op (or probe) clears back to READY. Never per-op ERROR spam.
- Carries `kind` (`"permission"` | `"missing_dir"`) and a
  **platform-appropriate** reason (FDA wording only on `darwin`).
- Auto-sync skips quietly while degraded (`_access_skipped`) and lets one
  probe op through per 60 s (`should_skip_sync`) → recovers WITHOUT restart.
- **Read paths degrade, never raise.** `.exists()`/`.is_file()` stats are
  themselves denied while degraded and must sit INSIDE the guard's
  try/except (`list_conflicts`, `list_folders`, `load_local_mappings`);
  an unguarded stat escaped `GET /notes/sync/status` as a raw 500 once.
- API: `GET /notes/access` (state), `POST /notes/access/recheck`
  (active probe; `create_dir` = the "Create folder" action), and
  `notes_access_degraded/reason/kind` on `GET /notes/sync/status`.
- UI: `desktop/src/components/documents/NotesAccessPrompt.tsx` renders the
  full-page prompt on the Documents page (System Settings deep-link via the
  permissions system, Check again + 10 s auto-poll). Never show empty lists
  while degraded.

Pinned by `tests/unit/test_notes_access_state.py` (engine) and
`desktop/src/components/documents/notes-access-prompt.test.tsx` (UI).
- `supabase_client.py` — the ONLY wire client for cloud `workbench.notes`.
  ALL remote↔local column mapping lives in `_normalize_note_row`
  (`created_by`↔`user_id`, `deleted_at`→`is_deleted`). Skipping it resurrects
  deleted notes — that regression shipped once already.
  The graveyarded cloud tables (`note_versions`, `note_devices`,
  `note_directory_mappings`, `note_sync_log`, `note_shares`) have NO client
  methods — do not reintroduce them; every request 404s. Versions: local
  SQLite + the cloud `platform._version_capture` trigger. Shares: 501 until
  iam-based sharing.

The frontend counterpart is `desktop/src/hooks/use-realtime-sync.ts` —
subscribed to `workbench.notes` / `workbench.note_folders` filtered on
`created_by`, with own-push echoes skipped via `last_device_id` and a
catch-up `pullChanges` on every (re)subscribe.

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
  `_last_push_hashes` are skipped; pulls of rows whose `last_device_id` is
  THIS device are skipped (own-push guard — never a conflict against your own
  save); the realtime hook skips events stamped with this device's id.
- **`note_hashes` = hash at last SUCCESSFUL sync.** It is written only after
  a push actually reaches Supabase (or a pull writes the file). Recording it
  optimistically makes offline edits look synced and lets pulls clobber them.
- Cloud is durable truth; local is the first-access replica the user actually
  touches. Never invert that.

## Known sharp edges

- **`full_sync` does not pull remote deletions** (gap #4) — the incremental
  `pull_changes` path is the delete-propagation channel.
- This subsystem was stabilized in 9ca565245/722f4889d: **codify, don't
  rearchitect.**
