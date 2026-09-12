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
- `sync_engine.py` — the pull-only replica engine: the Supabase RPC
  `public.agx_get_list_full()` → `agents` (THE agent catalog; see
  `app/services/agent_catalog/FEATURE.md`), AIDream `/api/ai-models` →
  `ai_models`; the local tool
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
  leave the machine.** Credential writes raise `SecretEncryptionUnavailableError`
  when the OS keychain/Fernet backend is unavailable; plaintext/base64 is not an
  equivalent fallback. Historical plaintext remains readable but is never newly written.
  Do not merge these secrets into the synced settings blob.
- `agents` — the EXACT offline mirror of `agx_get_list_full()`: the 19
  platform columns with the platform's names, the optional platform column
  `orchestra` (V33), plus `user_id`, `catalog_position`, `raw_json`,
  `synced_at`. 🚨 Ruling D4 (Arman, 2026-09-08): matrx-local is never an
  exception — offline changes WHERE the catalog lives, never WHAT it is.
  Never add, drop, rename, or re-sort a column here. Before V32 this table
  was a 7-column projection of a different catalog that could not see shared
  or org-shared agents. The platform column list is a REQUIRED MINIMUM, not
  an exact set: an EXTRA platform column is stored in `raw_json` and served
  verbatim, because one additive platform migration must never take every
  installed desktop's catalog down. A MISSING required column is still a
  loud refusal.
- `agent_execution_details` — fetch-through cache (V33) of
  `public.agx_get_execution_full(p_agent_id)`, stored and served VERBATIM by
  `GET /agents/catalog/{agent_id}/execution`. Filled LAZILY on the first
  request for an agent, TTL 600s — never 468 RPC calls at startup. It
  replaced `prompt_builtins`, a hand-made `{variable_defaults, settings}`
  projection of the aidream `GET /agents` listing route (dropped in V33 with
  `AIDreamClient.fetch_agents`).
- `agent_execution_definitions` — fetch-through cache of the complete,
  authenticated AIDream execution definition consumed by matrx-ai's
  `ExecutionAgentSource`. It is deliberately separate from `agents` /
  `agent_execution_details`: those are the picker's catalog and its
  variables/settings form data, and must never be interpreted as models,
  prompts, tools, or other executable policy.

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
- **A lock error on the shared connection is rolled back at the database
  layer, never left open.** `LocalDatabase.execute/executemany/commit` roll the
  implicit transaction back after `SQLITE_BUSY`/`SQLITE_LOCKED` and re-raise.
  Why: Python's sqlite3 opens a transaction for the failed write and never
  closes it; every later read then pins a WAL snapshot, the next hook commit
  makes it stale, and every write on the connection fails instantly with
  `SQLITE_BUSY_SNAPSHOT` (busy timeout never consulted) for the life of the
  process -- 18 failures across 10 callers on 2026-09-12. Guard:
  `tests/unit/test_local_db_lock_race_rollback.py` (fails without the rollback).
  Top-level write spans still take `write_gate()`; the rollback is what stops
  a lost race from becoming permanent.
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
  Explicit Claude history imports reuse this same table and one atomic FULL-sync multi-row
  transaction; they do not add a second local database or sync engine.
- `chat.coding_session` and `chat.coding_session_entry` are owner-only raw
  ledgers and are never structural-mirror tables. The generator excludes both
  by name and chat sync has a second runtime refusal guard.
- Conversations/messages are local-only today (gap #1) — hard-delete is fine
  there because no cloud counterpart exists yet.
- Known wart: `sync_all()` reports `"success"` for skipped entities (gap #6,
  pinned by characterization) — truth lives in `sync_meta.status`.

**Enforcement:** `tests/characterization/test_local_db_sync_characterization.py`
+ `tests/parity/test_sync_contract.py` + `tests/parity/test_agent_catalog_contract.py`
(the 19-key shape, RPC order, and the shared-agent membership guard). Red test = contract
conversation, not a test edit.

## Change log

- 2026-09-12 — Lock errors on the shared connection roll the open transaction
  back (was: one lost race poisoned every later write with
  `SQLITE_BUSY_SNAPSHOT` until restart — catalog/tools sync, token save,
  history scan, pin reconciler, capture backfill all dead on 1.4.86). The
  history-scan writers (`begin_scan`/`fail_scan`/`set_source_revisions`) and
  the capture reconciler's `_record_attempt` took the write gate.
- 2026-09-08 — Migration V33: the `agents` mirror tolerates a SUPERSET of the
  platform columns (`orchestra` promoted to a first-class nullable column;
  anything else rides `raw_json` verbatim); `agent_execution_details` replaced
  `prompt_builtins`, moving per-agent variables/settings off the aidream
  `GET /agents` route onto `agx_get_execution_full()`.
- 2026-09-08 — Migration V32: `agents` became the exact mirror of
  `agx_get_list_full()`; its source moved off the aidream `GET /agents` route,
  which could not see shared or org-shared agents (ruling D4).
- 2026-08-21 — Credential writes refuse plaintext/base64 substitution when keychain encryption is unavailable.
