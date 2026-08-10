# FEATURE — Local SQLite Replica (`~/.matrx/matrx.db`)

**Doctrine first:** read [`docs/SYNC_CONTRACT.md`](../../../docs/SYNC_CONTRACT.md)
before changing anything here. The one-line version: **the cloud is the durable
source of truth; SQLite is a FIRST-ACCESS REPLICA** — it makes reads instant
and offline-proof, it is never a competing server and never the system of
record for anything the cloud also stores. Docstrings claiming "SQLite is the
single source of truth" described read-path routing, not ownership — the
parity test `test_no_sqlite_source_of_truth_claims_in_sync_modules` bans the
phrase in the sync directories.

## What lives here

- `database.py` — connection/bootstrap for `~/.matrx/matrx.db`.
- `schema.py` — versioned migrations `_V1.._V7+` (core catalog tables,
  auth_tokens/prompts/notes, conversation persistence, notes-sync metadata,
  local version history, the opaque canonical executable-agent cache in V14,
  and the dedicated coding-session hook outbox in V19). Additive, applied at
  startup.
- `repositories.py` — typed repo layer; ALL reads elsewhere in the app go
  through these (the replica IS the read path — never route local reads
  through the cloud "for consistency").
- `sync_engine.py` — the pull-only replica engine: AIDream `/api/ai-models` +
  `/api/agents` → `ai_models`/`prompt_builtins`/`agents`; the local tool
  catalog (`app.tools.catalog.get_catalog`, 108 entries) → `tools`. Runs at
  startup + every 10 min (`DEFAULT_SYNC_INTERVAL = 600`). It is the ONLY
  component allowed to write cloud catalog data into SQLite. Initial and
  periodic failures are logged and retried on the next interval; an
  independent task completion callback also screams if the loop itself ever
  exits unexpectedly.
  NOTE (2026-07): `/api/ai-models` was reshaped server-side (aidream
  c7cfe4349) — `endpoints`/`provider`/`pricing` now live under
  `metadata.legacy`, `api_class` is the routing field. `sync_models` maps
  both shapes and caches `api_class`/`pricing`/`controls` in `raw_json`
  because the `ai_models` table now ALSO feeds matrx-ai's model routing via
  `app/services/ai/model_catalog.py` (SqliteModelCatalog) — change the
  cached fields and AI model resolution breaks, not just the models list.
- `secret_store.py` — keychain-backed Fernet encryption for `api_keys` (in the
  SQLite `app_settings` blob) and `auth_tokens`. **These never sync, never
  leave the machine.** Do not merge them into the synced settings blob.
- `agent_execution_definitions` — fetch-through cache of the complete,
  authenticated AIDream execution definition consumed by matrx-ai's
  `ExecutionAgentSource`. It is deliberately separate from `agents` /
  `prompt_builtins`: those are picker projections and must never be interpreted
  as models, prompts, tools, or other executable policy.

## Rules

- **Cloud wins unconditionally for catalog data** — `delete_missing` prunes
  rows absent from the feed (it's a cache, not user work). Empty-feed guard
  keeps the cache instead of wiping it.
- **Executable agents fail closed** — cached definitions are hash-checked and
  must include a top-level model plus authored messages. Corrupt/incomplete
  cache rows are deleted and fetched once from the authenticated canonical
  endpoint; an invalid authoritative definition is never partially executed.
  A complete hash-valid stale definition remains usable while offline, matching
  the first-access replica doctrine.
- **Offline skips are LOUD and recorded** in `sync_meta.status`
  (`skipped`/`offline`); stale cache is served, never wiped. Agents sync needs
  a user JWT (from `auth_tokens`); no token ⇒ loud skip, cache kept.
- **Do NOT delete `sync_queue` or `SyncMetaRepo`'s queue methods** — dormant,
  but it is the designated outbox for future offline-write push pipelines
  (conversations first; contract gap #1).
- `coding_session_bridge_outbox` is separate from `sync_queue` because it
  persists an exact provider adapter envelope to one authenticated aidream
  route, not a row mutation for generic PostgREST mirror sync. Its success
  boundary is a short `synchronous=FULL` transaction on the same SQLite file
  first, then an ordered, schema-validated cloud acknowledgement. Do not move
  its 202 boundary onto the shared repository transaction: an unrelated
  coroutine can commit or roll back that connection while the route awaits.
- `chat.coding_session` and `chat.coding_session_entry` are owner-only raw
  ledgers and are never structural-mirror tables. The generator excludes both
  by name and chat sync has a second runtime refusal guard.
- Conversations/messages are local-only today (gap #1) — hard-delete is fine
  there because no cloud counterpart exists yet.
- Known wart: `sync_all()` reports `"success"` for skipped entities (gap #6,
  pinned by characterization) — truth lives in `sync_meta.status`.

**Enforcement:** `tests/characterization/test_local_db_sync_characterization.py`
(11 tests) + `tests/parity/test_sync_contract.py`. Red test = contract
conversation, not a test edit.
