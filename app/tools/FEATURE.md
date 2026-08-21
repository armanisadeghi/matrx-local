# FEATURE — Local Tools (dispatcher + catalog + actions + tool_sync)

116 legacy tools + **19 action-enum mega-tools** (the W7 collapse, 2026-07-14),
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
  exactly one group; `make_group_handler` validates `action`, validates the
  selected action through its real Pydantic arg model when one exists, and
  re-dispatches through `dispatch()` (whose real handler signature enforces
  model-less actions) — mega handlers WRAP legacy handlers, never rewrite them
  (all hooks in the underlying handlers, e.g. file-sync hydration, ride along).
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
6. **`ToolResult.metadata` is NOT a UI-only channel.** `local_tool_bridge`
   hands it to the model beside the tool output, so anything a handler puts
   there is paid for in every agent's context. A bulk payload (a table set, a
   link graph, a parse tree) must be **opt-in behind a flag** and **capped
   with the truncation reported**, never returned by default. `Scrape` is the
   worked example — see below.

## `Scrape` result metadata — the opt-in extraction contract

`_scrape_result_to_metadata` in `tools/network.py`. The parse produces the
whole rich `ScrapeResult` either way; these flags only decide what travels.

| Flag | Adds |
|---|---|
| *(none)* | `status`, `url`, `status_code`, `content_type`, `title`, `cms`, `firewall`, `error` — ~250 bytes |
| `get_links` | `links` (8 URL buckets, capped per bucket) + `link_counts` (the TRUE totals) |
| `get_overview` | `overview` |
| `get_extraction` | `document_outline`, `tables` (rows + `rows_total`), `images`, `videos`, `audios`, `code_blocks`, `markdown_renderable`, `page_metadata`, `redirect_chain`, `hashes`, `main_image`, `response_url`, `scraped_at`/`published_at`/`modified_at` |

`output_mode="research"` forces all three off. Deliberately never forwarded:
`organized_data` (the whole parse tree), `ai_content`/`ai_research_*`
(duplicates of `output`), `markdown_renderable_by_header` (the same markdown
re-sliced), `link_records` (~2000 anchor rows), `raw_html`/`raw_body`.

Two invariants, both pinned by `tests/unit/test_scrape_metadata_payload.py`:

- **A cap never lies.** Anything dropped is reported — `truncated: {section:
  true}`, `link_counts` (real totals beside a capped bucket), `rows_total` on
  a row-capped table. A consumer must be able to say "500 of 12,431".
- **Absent means "this page had none".** A PDF/image/JSON scrape carries its
  text in `raw_text` and has no outline, tables or links; those keys are
  omitted rather than sent empty, so a UI can tell "nothing here" from
  "nothing extracted" and skip the panels entirely.

Consumer: `desktop/src/lib/scrape-extraction.ts` (the ONE place this payload
becomes typed data) → `desktop/src/components/scraping/`.

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

Run the release-grade guard directly:

```bash
uv run --frozen python scripts/check_tool_db_drift.py
```

It reads active `matrx-local` / `matrx-local.*` bindings plus the
`matrx-local/desktop` defaults directly from the live Supabase `tool` schema
(`Accept-Profile: tool`) using the app's shipped public connection defaults;
no opt-in env flag or privileged credential is required. It compares those
rows with the 19 advertised mega-tools and the real Pydantic models / fallback
handler signatures their dispatcher enforces. It checks name/binding parity,
`tier`, `admin_only`, `category`, and each top-level argument's type, required
flag, enum members, and default. It also rejects an `always_include_tools` name
without an active binding, except `load_desktop_tools`, which intentionally
executes on `matrx-ai-core`.

The script exits 1 on drift and prints a large warning, but release/CI call it
as a non-blocking signal. A network/registry failure is reported as "could not
verify" and exits 0; it is not misclassified as drift. Retired bindings are
never read, so the 115 granular `local_*` rows stay retired.

`uv run python -m app.tools.tool_sync status|diff|emit-changeset` remains the
operator workflow for explaining drift and preparing a reviewable DB change;
the release guard is read-only and never writes or suggests silently syncing
code into the database.

`tool_schemas._handler_signature` raises `ToolAnnotationResolutionError` when
annotations cannot be resolved. **Never substitute the raw string annotations:**
that advertises a valid-looking string schema for a tool that requested another type.

## Change log

- 2026-08-21 — Tool schema generation refuses unresolved annotations instead of string-typing them.
