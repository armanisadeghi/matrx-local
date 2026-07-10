# FEATURE — Local Tools (dispatcher + catalog + tool_sync)

108 local tools, one registry, three files that matter. Before touching any of
this, know the two truths:

- **Code-side truth:** `catalog.py::get_catalog()` — built automatically from
  `dispatcher.py::TOOL_HANDLERS` + signature introspection (`tool_schemas.py`).
  One `CatalogEntry` per dispatched tool: `cloud_name` (canonical `local_*`),
  `dispatcher_name` (PascalCase), `input_schema` (Pydantic arg model wins over
  introspection), category/tags/platforms/timeout. The build FAILS LOUDLY on a
  `_META` key with no dispatcher tool, duplicate cloud names, or a malformed
  generated name.
- **Cloud canon:** Supabase `tool.definition` ⨝ `tool.binding` (executor
  `matrx-local`) is what the platform advertises. Descriptions live in the DB
  (house rule); the catalog strings are only the push payload for new rows and
  the offline fallback.

## The rules

1. **The desktop NEVER writes the tool tables.** `tool_sync.py` diffs
   code vs cloud and emits a reviewable changeset
   (`.matrx/tool_registry_changeset.sql`) that the operator applies via the
   Supabase MCP. No exceptions, no direct writes.
2. **62 legacy cloud names are PINNED verbatim in `_META`** — never rename a
   pinned `cloud_name`; the cloud row is the identity.
3. **`LOCAL_TOOL_MANIFEST` is DEAD** (deleted 2026-07-10). Consumers read the
   catalog: `app/services/ai/local_tool_bridge.py` (registers all tools under
   cloud names), the engine Phase-A½ backfill (`app/services/ai/engine.py`),
   `GET /chat/local-tools`, and local-DB `sync_tools`
   (`app/services/local_db/sync_engine.py`). Do not reintroduce a parallel list.
4. **PEP 563 trap:** tool modules use `from __future__ import annotations`, so
   any signature introspection MUST pass `eval_str=True`
   (see `dispatcher._coerce_tool_input` and `tool_schemas`) — without it every
   param types as `"string"` and coercion silently no-ops. That was a real bug.
5. **Platform gating is advertisement, not enforcement.** `platforms=("darwin",)`
   in `_META` describes; the handler itself must ALSO self-gate at runtime with
   a clear "only available on <OS>" error envelope.

## Adding a tool

1. Implement `tool_xxx()` in `app/tools/tools/<module>.py`.
2. Wire it into `TOOL_HANDLERS` in `dispatcher.py`.
3. (Recommended) add a Pydantic arg model in `arg_models/` and reference it in
   `catalog.py::_META` — richer schema (descriptions, enums, bounds).
4. `uv run python -m app.tools.tool_sync status` → `emit-changeset`; operator
   applies the SQL via Supabase MCP.
5. Run `uv run pytest tests/parity/test_tool_count.py` — it enforces
   dispatcher = catalog = source exact-set parity and schema validity; the
   characterization snapshot must be regenerated when the set changes.

## Drift checking

`uv run python -m app.tools.tool_sync status|diff` — LOUD + NON-BLOCKING
(exit 1 = drift signal, 0 = in-sync or cloud unreachable; "couldn't check" is
not drift — mirrors matrx-extend `check-tool-db-drift.ts`). Read path is
`GET {AIDREAM_SERVER_URL}/ai-tools/app/matrx-local/all`; when that route is
unreachable it falls back to the embedded 49-name baseline in names-only mode
and says so loudly. Full semantics in the `tool_sync.py` module docstring.
