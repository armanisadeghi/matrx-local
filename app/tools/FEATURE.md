# FEATURE — Local Tools (dispatcher + catalog + actions + tool_sync)

115 legacy tools + **19 action-enum mega-tools** (the W7 collapse, 2026-07-14),
one registry, four files that matter. Before touching any of this, know the
two truths:

- **Code-side truth:** `catalog.py::get_catalog()` — built automatically from
  `dispatcher.py::TOOL_HANDLERS` + signature introspection (`tool_schemas.py`).
  One `CatalogEntry` per dispatched tool: `cloud_name` (canonical `local_*`),
  `dispatcher_name` (PascalCase), `input_schema` (Pydantic arg model wins over
  introspection), category/tags/platforms/timeout. The build FAILS LOUDLY on a
  `_META` key with no dispatcher tool, duplicate cloud names, a malformed
  generated name, or a dispatcher tool not covered by any action group.
- **Cloud canon:** Supabase `tool.definition` ⨝ `tool.binding` (executor
  `matrx-local`) is what the platform advertises. Descriptions live in the DB
  (house rule); the catalog strings are only the push payload for new rows and
  the offline fallback.

## The action collapse (W7, 2026-07-14) — read before touching tools

The ADVERTISED surface is **19 action-enum mega-tools** (`local_file`,
`local_shell`, `local_window`, `local_process`, `local_input`, `local_audio`,
`local_clipboard`, `local_screen`, `local_system`, `local_browser`,
`local_web`, `local_net`, `local_monitor`, `local_schedule`,
`local_documents`, `local_media`, `local_ner`, `local_mac_apps`,
`local_windows_ps`), each a discriminated union keyed on `action` with a
`$variants` per-action contract in `tool.definition.parameters` — the platform
`note`-tool shape. The 115 flat cloud rows were RETIRED (definition + binding
`is_active=false`) in the same changeset; the live route serves exactly 19.

- **Spec + fan-out:** `actions.py::ACTION_GROUPS` maps every legacy tool into
  exactly one group; `make_group_handler` validates `action` and re-dispatches
  through `dispatch()` — mega handlers WRAP legacy handlers, never rewrite
  them (all hooks in the underlying handlers, e.g. file-sync hydration, ride
  along).
- **Catalog flags:** legacy entries carry `advertised=False` (dispatchable,
  not advertised); mega entries carry `advertised=True` +
  `cloud_parameters` (the flat `$variants` dialect stored in the DB, distinct
  from the standard `input_schema` used by local consumers). Advertised-only
  accessor: `get_advertised_catalog()`.
- **Arg aliases:** three legacy tools already had an `action` param —
  BrowserTabs (`tab_action`), MinimizeWindow (`window_action`), ServiceControl
  (`service_action`). The mega variant exposes the alias; the handler renames
  it back before fan-out.
- **Legacy PascalCase names stay dispatchable** (extension RPC transition);
  the extension `capabilities` surface still lists them alongside the megas.
- Adding an ACTION to an existing group = add the legacy tool as usual, add
  it to the group's `actions` map, regenerate the snapshot, emit + apply the
  changeset (the mega row's parameters CHANGED).

## The rules

1. **The desktop NEVER writes the tool tables.** `tool_sync.py` diffs
   code vs cloud and emits a reviewable changeset
   (`.matrx/tool_registry_changeset.sql`) that the operator applies via the
   Supabase MCP. No exceptions, no direct writes.
2. **62 legacy cloud names are PINNED verbatim in `_META`** — never rename a
   pinned `cloud_name`; the cloud row is the identity.
3. **`LOCAL_TOOL_MANIFEST` is DEAD** (deleted 2026-07-10). Consumers read the
   catalog: `app/services/ai/local_tool_bridge.py` (registers ALL tools —
   legacy + mega — as executors under cloud names; builds DEFINITIONS for
   advertised entries only), the engine Phase-A½ backfill
   (`app/services/ai/engine.py`), `GET /chat/local-tools`, and local-DB
   `sync_tools` (`app/services/local_db/sync_engine.py`). Do not reintroduce a
   parallel list.
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
   After applying, rerun `status`. Do not leave an applyable SQL file behind
   once cloud and code are in sync; replace it with a no-op applied record or
   regenerate only when drift exists.
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
