# FEATURE — Local Tools (dispatcher + catalog + actions + tool_sync)

Cross-repo system-of-record: /Users/armanisadeghi/code/common-docs/systems/agents/agent-tools/STATE.md — read it before touching this feature in ANY repo.

Code-side truth is `catalog.py::get_catalog()`, built from `dispatcher.py::TOOL_HANDLERS`
plus signature introspection (`tool_schemas.py`). The build FAILS LOUDLY on a `_META` key with
no dispatcher tool, duplicate cloud names, a malformed generated name, or a dispatcher tool not
covered by any action group.

## The rules

1. **The desktop NEVER writes the tool tables.** `tool_sync.py` diffs code vs cloud and emits a
   reviewable changeset (`.matrx/tool_registry_changeset.sql`) that the operator applies via the
   Supabase MCP. No exceptions, no direct writes.
2. **62 legacy cloud names are PINNED verbatim in `_META`** — never rename a pinned
   `cloud_name`; the cloud row is the identity.
3. **`LOCAL_TOOL_MANIFEST` is DEAD** (deleted 2026-07-10). Consumers read the catalog:
   `app/services/ai/local_tool_bridge.py`, the engine Phase-A½ backfill
   (`app/services/ai/engine.py`), `GET /chat/local-tools`, and local-DB `sync_tools`
   (`app/services/local_db/sync_engine.py`). Do not reintroduce a parallel list.
4. **PEP 563 trap:** tool modules use `from __future__ import annotations`, so any signature
   introspection MUST pass `eval_str=True` (see `dispatcher._coerce_tool_input` and
   `tool_schemas`). Without it every param types as `"string"` and coercion silently no-ops —
   that was a real bug. `tool_schemas._handler_signature` raises `ToolAnnotationResolutionError`
   when annotations cannot be resolved; **never substitute the raw string annotations.**
5. **Platform gating is advertisement, not enforcement.** `platforms=("darwin",)` in `_META`
   describes; the handler itself must ALSO self-gate at runtime with a clear
   "only available on <OS>" error envelope.
6. **`ToolResult.metadata` is NOT a UI-only channel.** `local_tool_bridge` hands it to the model
   beside the tool output, so anything a handler puts there is paid for in every agent's
   context. A bulk payload (a table set, a link graph, a parse tree) must be **opt-in behind a
   flag** and **capped with the truncation reported**, never returned by default. `Scrape` is
   the worked example: `_scrape_result_to_metadata` in `tools/network.py`, invariants pinned by
   `tests/unit/test_scrape_metadata_payload.py` — **a cap never lies** (report `truncated`,
   `link_counts`, `rows_total`) and **absent means "this page had none"** (omit, never send
   empty). Never forward `organized_data`, `ai_content`/`ai_research_*`,
   `markdown_renderable_by_header`, `link_records`, `raw_html`/`raw_body`. Typed consumer:
   `desktop/src/lib/scrape-extraction.ts`.
7. **Mega handlers WRAP legacy handlers, never rewrite them** — `make_group_handler` in
   `actions.py` re-dispatches through `dispatch()` so every hook in the underlying handler
   (e.g. file-sync hydration) rides along. Legacy PascalCase names stay dispatchable.
   Adding an ACTION to an existing group means the mega row's parameters CHANGED: add the
   legacy tool, add it to the group's `actions` map, regenerate the snapshot, emit + apply the
   changeset.

## Adding a tool

1. Implement `tool_xxx()` in `app/tools/tools/<module>.py`.
2. Wire it into `TOOL_HANDLERS` in `dispatcher.py`.
3. (Recommended) add a Pydantic arg model in `arg_models/` and reference it in
   `catalog.py::_META` — richer schema (descriptions, enums, bounds).
4. `uv run python -m app.tools.tool_sync status` → `emit-changeset`; operator applies the SQL
   via Supabase MCP. Rerun `status` after applying. Do not leave an applyable SQL file behind
   once cloud and code are in sync.
5. `uv run pytest tests/parity/test_tool_count.py` — enforces dispatcher = catalog = source
   exact-set parity and schema validity; regenerate the characterization snapshot when the set
   changes.

## Drift checking

```bash
uv run --frozen python scripts/check_tool_db_drift.py
```

Read-only, needs no env flag or privileged credential. Exits 1 on drift with a large warning;
release/CI call it non-blocking. A network/registry failure reports "could not verify" and
exits 0 — never misclassified as drift. Retired bindings are never read.
`load_desktop_tools` is the one allowed `always_include_tools` name without an active
`matrx-local` binding (it executes on `matrx-ai-core`).

`uv run python -m app.tools.tool_sync status|diff|emit-changeset` remains the operator workflow
for explaining drift and preparing a reviewable DB change.

## Change log

- 2026-08-25 — Cut to local mechanics by the `agent-tools` consolidation; the registry model,
  the W7 action-collapse narrative, and the cross-repo drift-guard contract moved to the node's
  STATE.md.
- 2026-08-21 — Tool schema generation refuses unresolved annotations instead of string-typing them.
