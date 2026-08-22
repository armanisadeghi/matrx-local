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
- `file_manager.py` — note files under `MATRX_NOTES_DIR` (default `~/Documents/Matrx/Notes/`), `.sync/state.json`
  checkpoint, `.sync/conflicts/<id>/{local,remote}.md`. Records access
  evidence into the canonical access-health service; holds NO access state.
- `access_resources.py` — registers the documents-domain resources with the
  access-health authority: `notes-canonical` (mirrors into the `notes_sync`
  registry service) and one `notes-mapping:<folder_id>:<sha8>` per mapped
  directory (registered on create/load, unregistered on delete).

## Access-degraded is a STATE — owned by `app/services/access_health` (2026-07-18)

All access-health state lives in the canonical service
(`app/services/access_health/FEATURE.md`). The old module-level
`notes_access_guard` was **deleted** — it was a single global boolean that a
failure in ANY mapped dir poisoned (global false "Full Disk Access" banner)
and a success anywhere cleared. Rules for this package:

- `_atomic_write` is **pure** (raises OSError untouched, zero state side
  effects). Callers wrap it in `observing(<their resource>, REPLACE, ...)` so
  evidence lands on the right resource. Never re-add state mutations to it.
- `sync_to_mapped_dirs` observes **per mapping** (pass `folder_id`); a bad
  external dir degrades only its own `notes-mapping:*` resource.
- **Read paths degrade, never raise.** `.exists()`/`.is_file()` stats are
  themselves denied while blocked and must sit INSIDE try/except
  (`list_conflicts`, `list_folders`, `load_local_mappings`) with a
  `record(...)` failure observation; an unguarded stat escaped
  `GET /notes/sync/status` as a raw 500 once.
- Auto-sync skips quietly while degraded (`_access_skipped` →
  `should_attempt`, one probe op per 60 s) and the watcher **restarts
  immediately** on the degraded→ok transition (`ensure_recovery_hook`) —
  never wait for the next 600 s tick.
- Startup (main.py Phase 2c) probes `notes-canonical` + all mappings BEFORE
  reporting `notes_sync` ready/degraded. Never claim READY unprobed.
- API: `GET /access/health`, `POST /access/recheck`, `POST /access/reset`
  (`app/api/access_routes.py`) — plus the stable
  `notes_access_degraded/reason/kind` keys on `GET /notes/sync/status`.
  The old `/notes/access*` endpoints are deleted; do not reintroduce a
  notes-only access view.
- UI: one store — `AccessHealthContext` (single poll owner,
  generation-fenced). `NotesAccessPrompt` and `AppActionBanner` are pure
  renderers; copy comes from `deriveAccessPresentation` and only claims FDA
  on a positive engine diagnosis. Never show empty lists while degraded.

Pinned by `tests/smoke/test_access_health.py`,
`tests/characterization/test_notes_access_cutover.py` (engine) and
`desktop/src/hooks/use-access-health.test.ts` +
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
- **Cloud-delete propagation is budgeted — the mass-delete circuit breaker
  (2026-08-08).** Every cloud soft-delete this engine emits (full_sync
  tombstone branch, the watcher's `_handle_external_delete`, folder deletes,
  the `DELETE /notes/{id}` route) MUST pass
  `SyncEngine.allow_cloud_delete(note_id)`. Budget: max(25, 10% of the live
  remote corpus) per rolling 24h; exceeding it TRIPS the breaker — further
  cloud deletes are blocked (local tombstones are preserved and propagate
  later), the tripped state persists in `.sync/state.json` across restarts,
  and ONLY the explicit `POST /documents/sync/delete-breaker/reset` clears
  it. Written after 2026-08-06..08, when local tombstone propagation
  soft-deleted 2,600+ cloud notes on one account in four device-stamped
  waves (MXL-D-074). Never add a cloud-delete call site without the gate,
  and never auto-reset the breaker from any loop. Pinned:
  `tests/unit/test_mass_delete_breaker.py`.
- **Never push the local `folder_id` verbatim.** `document_routes._folder_id_for_name`
  mints a deterministic uuid5-of-name; the cloud's `workbench.note_folders.id`
  is a random uuid4, so the local id is (almost) never a real folder row and
  trips `notes_folder_id_fkey` → PostgREST **409**. `_push_note` runs every
  folder_id through `_resolve_remote_folder_id` first: keep it only if it's a
  live remote folder, else match by folder NAME to a real id, else push
  **null** (the `folder_name` text column carries the organization). The
  desktop never fabricates a cloud folder from a push. Cached ~60s; a lookup
  failure resolves to null and never blocks the push.

## Known sharp edges

- **`full_sync` does not pull remote deletions** (gap #4) — the incremental
  `pull_changes` path is the delete-propagation channel.
- This subsystem was stabilized in 9ca565245/722f4889d: **codify, don't
  rearchitect.**
