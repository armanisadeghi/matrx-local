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
| **Notes (bodies + metadata)** | `.md` files under the notes dir + SQLite `notes` + cloud `workbench.notes` | **Bidirectional**, manually triggered (`push_all` / `pull_all` / `full_sync` / per-note) + file watcher | Content-hash three-way compare against last-known-hash. Unprovable local state → conflict saved to `.sync/conflicts/<id>/{local,remote}.md`, BOTH copies preserved. User resolves: `keep_local` / `keep_remote` / `merge` / `append` / `split` / `exclude` | Soft-delete both sides: SQLite `is_deleted=1`, cloud `deleted_at` (mapped to `is_deleted` at the wire boundary in `supabase_client._normalize_note_row`). Local offline deletes propagate on next `full_sync`; remote deletes arrive via `get_notes_since` and remove the local file + tombstone the row | `last_sync_version` in `.sync/state.json`; cloud trigger `trigger_notes_sync_version` bumps `sync_version` on every update **including soft-deletes**, so `pull_changes` is truly incremental. `full_sync` is a full reconciliation by design | Full: file written first, `sync_status` → `pending_push`/`failed`, retried by `push_all`/`full_sync` via `list_pending_push` |
| **Note versions** | SQLite `note_versions` + cloud `public.note_versions` | Push-only (snapshot before each cloud overwrite) | N/A — append-only history | N/A | version_number monotonic per note | Local version history works fully offline (migration V7) |
| **Conversations / messages** | SQLite `conversations`, `messages`, `user_requests`, `tool_call_logs` via `LocalConversationHandler` | **Local-only (GAP — see below)** | N/A | Hard delete local (`ConversationsRepo.delete`) — no cloud counterpart to protect | None | Full local write, never leaves the machine |
| **Settings** | `~/.matrx/settings.json` + cloud `public.app_settings` (one row per user+instance) | **Bidirectional**, whole-blob | Timestamp compare (`updated_at`); newer side wins. Acceptable ONLY because the cloud row is scoped per-instance — the only competing writer for a row is this instance itself. Pull paths stamp the CLOUD timestamp locally (origin-echo suppression) so pull→push ping-pong cannot occur | N/A (blob upsert) | `updated_at` on both sides | Full: local JSON always writable; sync retried on next startup/trigger |
| **Instance registry / heartbeat** | cloud `public.app_instances` | Push-only (register + heartbeat + tunnel URLs) | Upsert `on_conflict=user_id,instance_id` | `is_active` flag | `last_seen` | App runs; orphan state flagged loudly, never blocking |
| **Models / agents / tools cache** | SQLite `ai_models`, `prompt_builtins`, `agents`, `tools` | **Pull-only cache** from AIDream API (tools from the local catalog) | Cloud wins unconditionally — `delete_missing` prunes rows absent from the feed. This is doctrine-compliant: it is a cache of cloud-authoritative catalog data, not user work. Empty-feed guard keeps the cache instead of wiping it | N/A — pruning IS the delete mechanism | `sync_meta` per-entity `last_synced_at` + content hash | Read-only offline: stale cache served, sync skipped with a loud log, `sync_meta.status='skipped'/'offline'` recorded |
| **API keys (providers)** | SQLite `app_settings` blob key `api_keys`, keychain-encrypted (`secret_store.protect`, Fernet DEK in OS keychain; base64 fallback) | **Local-only, NEVER synced** — deliberately. `cloud_sync/settings_sync.py` pushes `~/.matrx/settings.json` only; `api_keys` lives in the *SQLite* settings blob, which no sync path reads. Do not "unify" these two stores | N/A | N/A | N/A | Full local write |
| **Auth tokens (JWT)** | SQLite `auth_tokens`, keychain-encrypted | Local-only (written by React via `POST /auth/token`) | N/A | Cleared on logout | `expires_at` | N/A |
| **Scrape pages** | SQLite `scrape_pages` → remote scraper server | Push-only with local-first persistence | N/A (append-only) | Local soft-delete (`is_deleted`/`deleted_at` columns) | `cloud_sync_status` per row (`pending`/`synced`/`failed`), retried on startup | Full: row written locally immediately, pushed later |
| **Downloads** | SQLite `downloads` | Local-only (device-specific artifacts) | N/A | Status `cancelled` | N/A | Full |

## How the pieces map to code

Three sync subsystems, deliberately separate:

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
  (`settings_sync._save_local(updated_at=...)`), and the documents engine
  skips pulls whose content hash matches `_last_push_hashes`.
- API keys and auth tokens never leave the machine.

**Do-not list:**

- Do NOT make SQLite (or the notes dir) authoritative over the cloud.
- Do NOT delete the `sync_queue` table or `SyncMetaRepo`'s queue methods —
  dormant today (see gaps), but it is the designated outbox for future
  offline-write push pipelines (conversations first).
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

| # | Gap | Where | Status |
|---|---|---|---|
| 1 | **Conversations/messages are local-only forever.** `LocalConversationHandler` writes SQLite; no path pushes them to the cloud on reconnect. The `sync_queue` outbox table exists (schema V1) but has zero writers. | `app/services/ai/conversation_handler.py`, `app/services/local_db/schema.py` | Documented; a conversation-push pipeline is future work, deliberately NOT built in Phase 6 |
| 2 | **Folder delete is destructive and untombstoned.** `DELETE /documents/folders/{id}` hard-deletes the local directory and fire-and-forgets the cloud delete; contained notes get no SQLite tombstones. Offline folder delete → notes resurrect on next `full_sync`. | `app/api/document_routes.py::delete_folder` | Tracked in `.matrx/AGENT_TASKS.md` (route file outside Phase 6 scope) |
| 3 | **Settings conflict strategy is timestamp LWW,** not both-versions-preserved. Acceptable because the cloud row is per-instance (self vs self), but a frontend remote-edit feature would need real conflict handling. | `app/services/cloud_sync/settings_sync.py` | Documented as accepted divergence |
| 4 | **`full_sync` does not pull remote deletions** — `get_all_notes_with_hashes` filters to live rows by design; a remotely-deleted note whose file still exists locally gets re-pushed (cloud row stays tombstoned; the delete then arrives via the next `pull_changes`). Eventually consistent, but a `full_sync`-only user sees deleted notes linger locally. | `app/services/documents/sync_engine.py::full_sync` | Documented; incremental path (`pull_changes`) is the delete-propagation channel |
| 5 | **Stale "SQLite is the single source of truth" claims** remain in out-of-scope files: `app/api/chat_routes.py`, `app/services/ai/conversation_handler.py`, `app/config.py` (notes comment). | see left | Tracked in `.matrx/AGENT_TASKS.md` |
| 6 | **`sync_all()` reports `"success"` for skipped entities** (no client / no JWT); the truthful state lives only in `sync_meta.status='skipped'`. | `app/services/local_db/sync_engine.py` | Pinned as characterization (`test_sync_all_with_no_client_reports_success_everywhere`); candidate for a truthful `"skipped"` aggregate later |
| 7 | **Note sync is manual-trigger only** (plus the file watcher); there is no automatic periodic reconnect sync for notes, so "on reconnect everything syncs" currently requires a user/UI trigger. | `app/services/documents/sync_engine.py` | Documented as current design |
