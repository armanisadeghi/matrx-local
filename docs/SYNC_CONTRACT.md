# SYNC_CONTRACT.md — The Matrx Local Sync Contract

> **This is the ratified doctrine for every byte that moves between this machine
> and the cloud.** If you are about to change anything under
> `app/services/documents/`, `app/services/local_db/`, or
> `app/services/cloud_sync/`, read this first. The characterization tests in
> `tests/characterization/` and the invariants in
> `tests/parity/test_sync_contract.py` enforce it — if your change turns one
> red, either you broke the contract or you are deliberately amending it (say
> so in the commit message and update this document in the same change).

---

## The doctrine

**Everything lives in the cloud. The local SQLite database (`~/.matrx/matrx.db`)
and the local files (`~/matrx-notes/`, `~/.matrx/settings.json`) are a full
FIRST-ACCESS REPLICA — not a competing server.**

- **Offline is unnoticeable.** Zero connectivity for minutes, hours, or days
  must not stop work: full read AND full write against the local replica.
- **Reconnect syncs everything.** With no conflicts, nothing misses a beat.
- **Conflicts are NEVER destructive.** When both sides changed, store both
  versions and let the user resolve. No silent last-writer-wins on user
  content, no auto-merge that discards a side.
- **Loss of connection means slightly older data, never work stoppage.**
- Cloud is the durable source of truth; local is the working copy the user
  actually touches. A write lands locally first, then propagates.

## Per-entity matrix

| Entity | Store(s) | Direction | Conflict strategy | Tombstones | Checkpoint | Offline write |
|---|---|---|---|---|---|---|
| **Notes (bodies + metadata)** | `.md` files under the notes dir + SQLite `notes` + cloud `workbench.notes` | **Bidirectional, automatic**: engine-owned auto-sync loop (10-min incremental pull + pending push, daily full reconcile, credentials from the persisted `auth_tokens` row), Supabase Realtime on `workbench.notes`/`note_folders` (frontend hook, own-device echoes skipped via `last_device_id`), auto-started file watcher, plus manual `push_all` / `pull_all` / `full_sync` / per-note triggers | Content-hash three-way compare against last-known-hash; a remote row whose `last_device_id` is THIS device can never conflict with or overwrite local work (own-push guard — local divergence is unsynced work and is pushed). Pushes of previously-synced notes are OPTIMISTIC-CONCURRENCY updates (conditional on the last-synced `content_hash`, live rows only) — a failed precondition conflicts instead of last-writer-winning; edit-vs-remote-delete resurrects the edit. Pathless (web-authored) cloud notes get a collision-free allocated local path, written back to the cloud row. Unprovable local state from another device → conflict saved to `.sync/conflicts/<id>/{local,remote}.md`, BOTH copies preserved. User resolves: `keep_local` / `keep_remote` / `merge` / `append` / `split` / `exclude` | Soft-delete both sides: SQLite `is_deleted=1`, cloud `deleted_at` (mapped to `is_deleted` at the wire boundary in `supabase_client._normalize_note_row`). Local offline deletes propagate on next `full_sync`; remote deletes arrive via `get_notes_since` and remove the local file + tombstone the row | `last_pull_at` (cloud `updated_at` watermark) in `.sync/state.json`; `platform._touch_row` stamps `updated_at` on EVERY write **including soft-delete PATCHes**, so `pull_changes` is truly incremental. (`sync_version` is a PER-ROW edit counter bumped only on content/label change — never use it as a global cursor; that bug made incremental pull blind to most remote edits and all deletes.) `full_sync` is a full reconciliation by design | Full: file written first, `sync_status` → `pending_push`/`failed`, retried by `push_all`/`full_sync` via `list_pending_push` |
| **Note versions** | SQLite `note_versions` (local, offline-capable) + cloud `history.row_versions` via the `platform._version_capture` trigger on `workbench.notes` | Local snapshots on save; cloud history is trigger-captured server-side (the old push to `public.note_versions` 404'd — table graveyarded 2026-07, client calls deleted) | N/A — append-only history | N/A | version_number monotonic per note | Local version history works fully offline (migration V7) |
| **Chat system (conversations, messages, user_requests, tool_calls, media, artifacts, …)** | The CANONICAL LOCAL MIRROR: per-schema SQLite file `~/.matrx/mirror/chat.db` ATTACHed as `chat`, tables generated from the live cloud schema (`schema_mirror/snapshot.json` → `scripts/generate_mirror_schema.py` → `mirror_schema.py`). Local ids ARE the canonical cloud ids. Written by `SQLiteConversationStore` + `ConversationsRepo`/`MessagesRepo` (compat shapes preserved) | **Bidirectional, automatic** (2026-07-13; guarded writes 2026-07-18): every local-origin write enqueues the `sync_queue` outbox; `app/services/chat_sync/engine.py` pulls incremental keyset pages on `(updated_at\|created_at, pk)`, then drains parents before children. New rows use batched insert-ignore; existing versioned rows use conditional PATCH on `(id, version)`. The payload is projected through an explicit per-table desktop-authoring allowlist; newly-added cloud columns are pull-only by default. Non-UUID ids/other-user rows are dead-lettered; permanent payload errors dead-letter after 5 attempts | Pulls retain timestamp LWW only when no local write is pending. If cloud and local both changed, neither is overwritten: the outbox row becomes `action='conflict'` and stores both projected local and complete remote snapshots. A lost insert response is recognized idempotently when all desktop-authored values already match. `chat.media` has no version column and is insert-only from desktop; a differing existing row conflicts. Field-clears remain a known limit because the codec omits `NULL` values | Soft-delete both sides via `deleted_at`; `ConversationsRepo.delete` tombstones (repo reads filter live rows); nothing in the pull path ever hard-deletes | `sync_meta` rows keyed `chat.<table>`; keyset cursor JSON in `last_hash`; conflicts are durable `sync_queue` rows and exposed in mirror status | Full local write; outbox drains on reconnect (V10 migration also seeded the outbox with all pre-cutover local history) |
| **Settings** | `~/.matrx/settings.json` + cloud `public.app_settings` (one row per user+instance) | **Bidirectional**, whole-blob | Timestamp compare (`updated_at`); newer side wins. Acceptable ONLY because the cloud row is scoped per-instance — the only competing writer for a row is this instance itself. Pull paths stamp the CLOUD timestamp locally (origin-echo suppression) so pull→push ping-pong cannot occur | N/A (blob upsert) | `updated_at` on both sides | Full: local JSON always writable; sync retried on next startup/trigger |
| **Instance registry / heartbeat** | cloud `public.app_instances` | Push-only (register + heartbeat + tunnel URLs). **Fleet reporting:** registration carries `app_version` (the ONE resolver — `instance_manager.current_app_version()` → `app.api.routes._APP_VERSION`; never re-resolve), and the heartbeat folds remote-config provenance into `metadata` (`app_config_tier`, `catalogs_tier`, `catalog_entry_count`, `provenance_reported_at`) so `app_config.min_supported_app_version` gating and config-health are not blind. Both ride EXISTING writes — no new timer, no new request | Upsert `on_conflict=user_id,instance_id`. `metadata` is MERGED over the server copy captured from the registration response, never clobbered; the heartbeat carries it only when the provenance content CHANGED (`provenance_reported_at` is excluded from that comparison — including it would make every heartbeat a write), and a non-2xx leaves it staged for retry | `is_active` flag | `last_seen` | App runs; orphan state flagged loudly, never blocking. Provenance is best-effort — it never raises into the heartbeat. `GET /cloud/instance/dry-run` shows the exact body a sessionless engine would write |
| **Notes device id / dir mappings / sync log / shares** | Local only: `.sync/state.json` (`device_id`), `.sync/mappings.json` | None — the cloud tables (`note_devices`, `note_directory_mappings`, `note_sync_log`, `note_shares`) were graveyarded 2026-07; all client calls deleted. Sharing UI removed; `/shares` write routes return 501 pending iam-based sharing | N/A | N/A | N/A | Fully local |
| **Models / agents / tools cache** | SQLite `ai_models`, `prompt_builtins`, `agents`, `tools` | **Pull-only cache** from AIDream API (tools from the local catalog) | Cloud wins unconditionally — `delete_missing` prunes rows absent from the feed. This is doctrine-compliant: it is a cache of cloud-authoritative catalog data, not user work. Empty-feed guard keeps the cache instead of wiping it | N/A — pruning IS the delete mechanism | `sync_meta` per-entity `last_synced_at` + content hash | Read-only offline: stale cache served, sync skipped with a loud log, `sync_meta.status='skipped'/'offline'` recorded |
| **API keys (providers)** | SQLite `app_settings` blob key `api_keys`, keychain-encrypted (`secret_store.protect`, Fernet DEK in OS keychain; base64 fallback) | **Local-only, NEVER synced** — deliberately. `cloud_sync/settings_sync.py` pushes `~/.matrx/settings.json` only; `api_keys` lives in the *SQLite* settings blob, which no sync path reads. Do not "unify" these two stores | N/A | N/A | N/A | Full local write |
| **Auth tokens (JWT)** | SQLite `auth_tokens`, keychain-encrypted | Local-only (written by React via `POST /auth/token`) | N/A | Cleared on logout | `expires_at` | N/A |
| **Scrape pages** | SQLite `scrape_pages` → remote scraper server | Push-only with local-first persistence | N/A (append-only) | Local soft-delete (`is_deleted`/`deleted_at` columns) | `cloud_sync_status` per row (`pending`/`synced`/`failed`), retried on startup | Full: row written locally immediately, pushed later |
| **Downloads** | SQLite `downloads` | Local-only (device-specific artifacts) | N/A | Status `cancelled` | N/A | Full |
| **User files (matrx-files replica)** | `~/Documents/Matrx/Files` + ATTACHed mirror `files.files`/`files.folders` + SQLite `file_sync_state` (V11) | **Bidirectional, mode-gated** (`file_sync_mode`: `off\|pointers\|full`, default pointers): engine-owned loop (`app/services/file_sync/engine.py`, Phase 2e) pulls the matrx-files change feed (`GET /files/sync/changes`, opaque keyset cursor in `sync_meta` entity `files.files`), watcher (watchfiles) turns local create/modify/delete into pending ops drained through the matrx-files API with the user JWT. Bytes ONLY via the DownloadManager (category `file_sync`), URLs ONLY from the service's envelope. Pointer mode = zero-byte placeholders, hydrate-on-access via the tool seam (`hydration.ensure_hydrated`) | Content-hash compare against `last_synced_hash` (= cloud checksum at last SUCCESSFUL sync, never optimistic). Both-changed → `conflict` state, local kept, remote copy in `.sync/conflicts/<file_id>/`, user resolves `keep_local\|keep_remote`. Edit-vs-remote-delete → edit resurrects as a new upload. Watcher echo suppression is state-based (event hash == mirror checksum ⇒ own pull) | Cloud soft-delete (`deleted_at`, rides the feed because `platform._touch_row` stamps `updated_at`); local removals go to the OS trash (send2trash), never unlinked | Feed's opaque `next_cursor` in `sync_meta` (`files.files`) | Full: local writes land immediately; push intents queue in `file_sync_state.pending_op` and drain on reconnect; hydrated files read/write fully offline |

## How the pieces map to code

**The canonical mirror doctrine (2026-07-13):** the local store for cloud-owned
entity systems is a STRUCTURAL MIRROR of the canonical cloud schemas — same
table names, same column names, SQLite-compatible types, RLS replaced by
"this user's rows only". The DDL is GENERATED from a checked-in introspection
snapshot of the live cloud DB (`schema_mirror/` + `scripts/
generate_mirror_schema.py`), so drift is mechanically detectable
(`--check`, plus loud runtime drift errors in `app/services/local_db/
mirror.py`). Mirrored schemas live in per-schema files ATTACHed under the
schema name, so local SQL uses the canonical qualified names
(`chat.conversation`). The chat system was cut over 2026-07-13 (bespoke
tables annihilated in migration V10); `workbench.*` and `ai.*` are captured
in the snapshot and are follow-up cutovers.

Four sync subsystems, deliberately separate:

1. **`app/services/local_db/sync_engine.py`** — the pull-only replica engine.
   Pulls models/agents/tools from AIDream into SQLite on startup + every 10
   minutes. Writes `sync_meta` status per entity. The ONLY component allowed
   to write cloud catalog data into SQLite.
2. **`app/services/documents/`** — the notes subsystem.
   `sync_engine.py` (bidirectional push/pull/full_sync, conflict handling,
   file watcher), `file_manager.py` (files, `.sync/state.json`, conflicts
   dir, access guard), `supabase_client.py` (PostgREST wire client for
   `workbench.notes` — all remote↔local column mapping lives here, e.g.
   `created_by`↔`user_id`, `deleted_at`→`is_deleted`). ~40 endpoints in
   `app/api/document_routes.py`. Recently stabilized (9ca565245 corruption
   fixes, 722f4889d workbench repoint): **codify, don't rearchitect.**
3. **`app/services/cloud_sync/`** — settings + instance sync.
   `settings_sync.py` (whole-blob settings sync, instance registration,
   heartbeat), `instance_manager.py`. Carries the origin-echo suppression fix
   from 9ca565245: `_save_local(updated_at=<cloud timestamp>)` on every pull
   path. Removing that parameter reintroduces an unreachable `in_sync` state
   and a last-writer-wins ping-pong across devices.
4. **`app/services/chat_sync/`** — the chat-mirror subsystem.
   `engine.py` (outbox drain + incremental keyset pull, LWW with
   pending-outbox protection, engine-owned background loop with credentials
   from the persisted `auth_tokens` row, managed service `chat_sync`),
   `client.py` (PostgREST wire client for the `chat` schema profile —
   pushes strip the cloud-trigger-owned columns `organization_id` /
   `created_by` / `updated_by` / `version`). Status:
   `GET /chat/mirror/status`; manual cycle: `POST /chat/mirror/sync`.

Related local-first stores that ride their own queues: `scrape_pages`
(scraper retry queue) and `downloads` (download manager).

## AGENT-PROOFING — read before you "fix" anything

**Why local SQLite exists:** it is the first-access replica that makes the app
instant and offline-proof. It is NOT a competing server and NOT the system of
record for anything the cloud also stores. Docstrings that once said "SQLite
is the single source of truth" described read-path routing (all local reads go
through SQLite), not data ownership — they have been corrected.

**Invariants that must never be "fixed" away:**

- Local write first, cloud propagation second. A failed network never blocks
  or fails a local request.
- A conflict produces TWO preserved copies (`.sync/conflicts/<id>/`), never an
  overwrite. If the last-known-sync hash is missing or stale, the code MUST
  assume an unsynced local edit exists and conflict, not clobber
  (pinned: `test_pull_conflicts_when_last_known_hash_missing`).
- Deletions are tombstones (SQLite `is_deleted`, cloud `deleted_at`), and the
  tombstone must survive long enough to propagate to the other side.
- Every remote note read path passes through `_normalize_note_row` — the wire
  speaks `deleted_at`, the engine speaks `is_deleted`. Skipping the mapping
  resurrects deleted notes (this regression shipped once already).
- Offline sync skips are LOUD and recorded (`sync_meta.status`), never
  silently swallowed; caches are kept, not wiped, on skip/empty-feed.
- Echo suppression stays: pull paths stamp the cloud timestamp
  (`settings_sync._save_local(updated_at=...)`), the documents engine
  skips pulls whose content hash matches `_last_push_hashes`, the pull path
  skips rows whose `last_device_id` is this device, and the frontend realtime
  hook skips events stamped with this device's id.
- `note_hashes` in `.sync/state.json` means "content hash at the last
  SUCCESSFUL cloud sync" — it is written ONLY after a push actually reaches
  Supabase (or after a pull writes the file). Recording it optimistically
  before/without the push makes offline edits look synced and lets the next
  pull clobber them with older cloud content (fixed + pinned 2026-07-13).
- API keys and auth tokens never leave the machine.

**Do-not list:**

- Do NOT make SQLite (or the notes dir) authoritative over the cloud.
- Do NOT delete the `sync_queue` table or `SyncMetaRepo`'s queue methods —
  it IS the live outbox for the chat mirror (writers: `outbox.enqueue_change`
  callers in the conversation store and repos). Remote-origin writes (pull /
  echo apply paths in `chat_sync/engine.py`) must NEVER enqueue.
- Do NOT auto-resolve conflicts destructively (no silent keep_remote /
  keep_local defaults, no hash-less overwrites).
- Do NOT remove offline write paths or add hard dependencies on connectivity.
- Do NOT hard-delete where a tombstone path exists.
- Do NOT route local DB reads through the cloud "for consistency" — the
  replica IS the read path.
- Do NOT merge the api_keys store into the synced settings blob.

**Enforcement:** `tests/characterization/test_documents_sync_characterization.py`
(22 tests), `tests/characterization/test_local_db_sync_characterization.py`
(11 tests), `tests/parity/test_sync_contract.py`, and
`tests/parity/test_settings_parity.py`. They pin today's behavior; a red test
means a contract conversation, not a test edit.

## Current gaps (honest divergence from doctrine)

Closed 2026-07-13: folder delete now tombstones contained notes (MXL-D-002),
and note sync is fully automatic (engine auto-sync loop + Realtime + watcher).

| # | Gap | Where | Status |
|---|---|---|---|
| 1 | ~~Conversations/messages are local-only.~~ **CLOSED 2026-07-13**: the canonical chat mirror + `chat_sync` engine push local turns to `chat.*` and pull cloud conversations incrementally; the `sync_queue` outbox has real writers. Remaining nuance: conversation content flattens non-text message parts to text locally (`_normalize_message`), and `chat.request` rows are not produced by a client host (the user_request↔conversation link rides `user_request.metadata.conversation_id`). | `app/services/chat_sync/`, `app/services/ai/conversation_handler.py` | Closed; characterization-pinned (`test_chat_mirror_characterization.py`) |
| 3 | **Settings conflict strategy is timestamp LWW,** not both-versions-preserved. Acceptable because the cloud row is per-instance (self vs self), but a frontend remote-edit feature would need real conflict handling. | `app/services/cloud_sync/settings_sync.py` | Documented as accepted divergence |
| 4 | **`full_sync` does not pull remote deletions** — `get_all_notes_with_hashes` filters to live rows by design; a remotely-deleted note whose file still exists locally gets re-pushed (cloud row stays tombstoned; the delete then arrives via the next `pull_changes`). Eventually consistent, but a `full_sync`-only user sees deleted notes linger locally. | `app/services/documents/sync_engine.py::full_sync` | Documented; incremental path (`pull_changes`) is the delete-propagation channel |
| 5 | **Stale "SQLite is the single source of truth" claims** remain in out-of-scope files: `app/api/chat_routes.py`, `app/services/ai/conversation_handler.py`, `app/config.py` (notes comment). | see left | Tracked in `.matrx/AGENT_TASKS.md` |
| 6 | **`sync_all()` reports `"success"` for skipped entities** (no client / no JWT); the truthful state lives only in `sync_meta.status='skipped'`. | `app/services/local_db/sync_engine.py` | Pinned as characterization (`test_sync_all_with_no_client_reports_success_everywhere`); candidate for a truthful `"skipped"` aggregate later |
