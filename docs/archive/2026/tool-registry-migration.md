# matrx-local Migration Guide — Tool Registry Redesign

> ARCHIVED 2026-08-20 — moved from `docs/research/` during the matrx-local centralization
> sweep (doc-migration board row 33). Historical (2026); the `matrx-local:<local>` naming
> this guide proposed is live in code (`app/tools/tool_sync.py`,
> `app/services/ai/local_tool_bridge.py`). Index: [ARCHITECTURE.md](../official/ARCHITECTURE.md).
> Companion docs below were aidream-platform guides — may not exist in this repo.

> Audience: matrx-local (desktop app) developers, server-side and client-side.
>
> Companion docs: [TOOL_REGISTRY_REDESIGN.md](TOOL_REGISTRY_REDESIGN.md), [CLIENT_REGISTRATION_GUIDE.md](CLIENT_REGISTRATION_GUIDE.md), [MATRX_EXTEND_MIGRATION_GUIDE.md](MATRX_EXTEND_MIGRATION_GUIDE.md), [MATRX_FRONTEND_MIGRATION_GUIDE.md](MATRX_FRONTEND_MIGRATION_GUIDE.md).

---

## TL;DR — what's broken and what's not

After migration 0022 + the cleanup script, **all 78 of your tools still work today** (no immediate breakage). What's broken from the *new system's* perspective is naming and metadata:

- **62 tools** prefixed `local_*` (e.g. `local_browser_click`, `local_bash`) carry redundant prefixes that should become explicit namespaces.
- **16 tools** were re-labeled by the cleanup script (`source_app: 'matrx-extend' → 'matrx_local'`) — they're yours but were misfiled. The names themselves stay (`clipboard`, `computer`, `navigate`, etc.).
- **All 78** need to be renamed to the canonical `matrx-local:<local>` form across both the DB and your code, in lockstep.

**No urgency to break things — bare names still resolve.** The migration unlocks: surface gating, executor-based dispatch, bundle membership, and clean deprecation paths. Plan it as a series of small PRs, not a big-bang.

---

## Conceptual shift — your role in the new architecture

matrx-local is **a host of matrx-ai**, just like aidream is. Concretely:

```
   ┌────────────────────────────────────────────────────────────────────┐
   │                          matrx-ai (PyPI)                           │
   │  Tool registry, executors, capabilities, providers, orchestrator   │
   └────────────────────────────────────────────────────────────────────┘
       ▲                          ▲                         ▲
       │ imports & configures     │ imports & configures    │ imports
       │                          │                         │
   ┌────────┐               ┌──────────────┐         ┌─────────────────┐
   │ aidream│               │ matrx-local  │         │   third-party   │
   └────────┘               └──────────────┘         └─────────────────┘
       │                          │
       │ talks via HTTP API       │ exposes a public port
       ▼                          ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │   matrx-frontend (cloud agent)  ◄──►  matrx-extend (browser)       │
   │                                                                    │
   │   matrx-local talks to BOTH directly — peer relationships, not     │
   │   through aidream.                                                 │
   └────────────────────────────────────────────────────────────────────┘
```

Three implications:

1. **You import matrx-ai directly.** Anything matrx-ai exports — `matrx_ai.configure()`, `ToolRegistryV2`, `ExecutorKind`, `Capability` types — is yours to use. Anything aidream-specific (e.g. `aidream.api.context_objects`) **is not**, by design.
2. **You register your own ui_client + ui_surface rows.** matrx-local is its own client; the aidream team will not seed your data. See [CLIENT_REGISTRATION_GUIDE.md](CLIENT_REGISTRATION_GUIDE.md).
3. **You talk peer-to-peer with matrx-frontend (cloud agent) and matrx-extend (browser).** The cloud agent uses the same canonical names and namespacing as you do; the protocol-level handshake exchanges executor declarations (`{"executors": ["matrx-local.bridge", "matrx-ai.core", ...]}`) so both sides agree on what each can run.

---

## What stays the same

| Piece | Status |
|---|---|
| Your Python tool handler functions | **Unchanged.** Same signatures, same return types. |
| Your local server (FastAPI / matrx-connect-based) | **Unchanged.** |
| The way you import and configure matrx-ai (`matrx_ai.configure(db_models=..., ...)`) | **Unchanged**, with one new kwarg (`capabilities=...` if you ship custom capabilities — same as aidream does). |
| Your client UI's stream-event handling for tool delegation | Mostly unchanged; alias-mapping additions (see below). |
| Your DB schema for matrx-local-private state (if any) | Not touched by this migration. |

---

## What changes

### 1. Tool canonical names

| Before | After |
|---|---|
| `local_bash` | `matrx-local:bash` |
| `local_browser_click` | `matrx-local:browser_click` |
| `clipboard` (mislabeled) | `matrx-local:clipboard` |
| `navigate` (mislabeled) | `matrx-local:navigate` |
| `read_pdf` (mislabeled) | `matrx-local:read_pdf` |

The full migration table is at the bottom of this doc.

### 2. Executor declaration

You need a `tl_executor_kind` row to dispatch your tools:

```sql
INSERT INTO public.tl_executor_kind (name, description, is_client_side)
VALUES ('matrx-local.bridge', 'matrx-local desktop bridge runtime', true)
ON CONFLICT (name) DO NOTHING;
```

Every tool of yours then gets a `tool_handlers` row pointing at this executor. The seed pattern in [`db/migrations/_seed_0022_browser_dom.py`](db/migrations/_seed_0022_browser_dom.py) (sections 2 + 4) is your template — copy and adapt.

### 3. Wire format `:` ↔ `__`

Same rule as matrx-extend (see [MATRX_EXTEND_MIGRATION_GUIDE.md](MATRX_EXTEND_MIGRATION_GUIDE.md) §1 of "What changes"). Tool names cross provider APIs as `matrx-local__bash`, never `matrx-local:bash`. The translation layer is in matrx-ai's provider snapshot module — you don't touch it. Just be aware of both forms.

### 4. Bundle aliasing

After Step 2 of the redesign ships, your tools may arrive (in delegation events) under a custom bundle's namespace. If a user creates a "research" bundle that includes `matrx-local:fetch_url`, you'll see `research__fetch_url` on the wire. Same handling as matrx-extend — prefer the `canonicalName` field on the event payload over wire-name parsing.

### 5. Surface registration

matrx-local is its own `ui_client`. You decide your sub-surfaces. Reasonable starter set:

```sql
INSERT INTO public.ui_client (name, description, sort_order)
VALUES ('matrx-local', 'matrx-local desktop app', 300)
ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO public.ui_surface (name, client_name, description) VALUES
  ('matrx-local/agent',      'matrx-local', 'Local agent (offline-capable)'),
  ('matrx-local/cloud-sync', 'matrx-local', 'Cloud-synced agent (paired with matrx-frontend)')
ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description;
```

Adjust to your actual UI structure. After registering, every tool of yours needs a `tl_def_surface` row pointing at the right surface(s).

### 6. Bundles for matrx-local

You may want to expose tool bundles users can subscribe to — e.g. a `local_files` bundle (file ops + archive + media), `local_browser` bundle (Playwright tools), `local_system` bundle (system info + clipboard + battery). These are admin-managed `tl_bundle` rows, with `is_system=true` if you want them shipped by default. Pattern from the browser-dom seed.

### 7. Connection to matrx-frontend's cloud agent

When a user pairs matrx-local with their cloud agent on matrx-frontend, the handshake should:

1. matrx-local sends its declared executors: `{"executors": ["matrx-local.bridge", "matrx-ai.core"], "state": {...}}`.
2. matrx-frontend's session adds matrx-local's executors to the active set for that user.
3. Subsequent tool dispatches that resolve to `matrx-local.bridge` route through the WebSocket connection to matrx-local.
4. matrx-local executes the tool and posts the result back via the standard `/conversations/{id}/tool_results` endpoint.

Step 9 of the redesign covers the matrx-frontend side of this UI; this section is your contract for the connection.

---

## What dies (eventually — not today)

These all happen in PR 4+, not today:

- The `local_*` prefix on tool names becomes redundant (the namespace explicitly says `matrx-local:`). Drop the prefix when you rename: `local_bash` → `matrx-local:bash`, not `matrx-local:local_bash`.
- The `source_app='matrx_local'` filter pattern in admin queries — replaced by `WHERE name LIKE 'matrx-local:%'`.
- Single-`_` separator anywhere in tool name parsing — moved to `__`.

---

## Migration sequence

Each PR can be reviewed and shipped independently.

### PR 1 — Register matrx-local as a client + executor

- Insert `ui_client('matrx-local', ...)` row.
- Insert `ui_surface('matrx-local/agent', ...)` row(s) for whatever surfaces matrx-local exposes.
- Insert `tl_executor_kind('matrx-local.bridge', ...)` row.
- Verify: dashboard sees your client; tool admin can target `matrx-local/*` surfaces.

### PR 2 — Rename tools to the new canonical form

Per the migration table below, for each tool:

1. `UPDATE public.tools SET name = 'matrx-local:<new>', source_app = 'matrx-local' WHERE name = '<old>'` — wraps the rename in a transaction (`tools.name` has UNIQUE; clashes fail loudly).
2. `INSERT INTO public.tool_handlers (tool_id, surface, ...) VALUES (<id>, 'matrx-local.bridge', 'matrx_ai.tools.implementations.<dispatch_module>.<handler>', ...)` — point each tool at the new executor. (Or update the existing row if one already exists.)
3. `INSERT INTO public.tl_def_surface (tool_id, surface_name) VALUES (<id>, 'matrx-local/agent')` — assign to your surface(s).

Group the renames into logical PRs (e.g. one for `local_browser_*`, one for `local_file_*`, one for the misfiled bare-name 16). Behavior parity is the test: every renamed tool must continue to dispatch correctly through both the old name (until it's deleted) and the new name.

**Don't delete the old rows in this PR.** Keep them as `is_active=false` aliases until your client code is on the new names.

### PR 3 — Update wire-format handling

In matrx-local's client UI:

- Recognize `matrx-local__<name>` wire format → strip prefix → dispatch.
- Recognize bundle-aliased names like `research__fetch_url` → use `canonicalName` from event payload → dispatch.
- Same alias map pattern as matrx-extend; mirror their [`aliases.ts`](file:///Users/armanisadeghi/code/matrx-extend/src/lib/tools/aliases.ts) approach.

In matrx-local's server (Python):

- If you have any code that constructs tool names by string concatenation, switch to the `:` canonical form with `__` only at the wire boundary.
- The matrx-ai provider snapshot handles the translation for you when you go through the registry; only worry about this if you bypass matrx-ai's runtime.

### PR 4 — Cloud-agent connection

This depends on Step 9 of the redesign (matrx-frontend's pairing UI). When that ships:

- matrx-local exposes a pairing-token endpoint.
- Frontend collects the token, opens a WebSocket, exchanges executor declarations.
- matrx-local accepts dispatched tools, runs them, posts results.

Contract details TBD as Step 9 progresses.

### PR 5 — Cleanup (after the new names are stable)

- Mark the old bare-name rows (`local_bash`, `clipboard`, etc.) `is_active=false` (don't DELETE — `cx_tool_call` history references them).
- Set `deactivated_at = now()` on each.
- Remove the temporary fall-back code paths in matrx-local that resolve old names.

---

## Direct matrx-ai integration — what you can and can't do

You are a host of matrx-ai. The injection contract:

```python
import matrx_ai

matrx_ai.configure(
    db_models={"AiModel": YourAiModel, ...},
    db_bases={"ToolsBase": YourToolsBase, ...},
    settings=your_settings,
    get_supabase_client=your_supabase_factory,
    file_handler_class=YourFileHandler,
    capabilities=[...],   # optional: matrx-local-specific capabilities
)
```

Things you DO get from matrx-ai:
- The full `ToolRegistryV2` mechanism — register your tools at startup (or load from your DB).
- All providers (Anthropic, OpenAI, Google, …) and the unified client.
- Streaming + emitter infrastructure via matrx-connect.
- The merge primitive, capability resolver, agent projection, dynamic drain.
- The bundle alias map (decision 28) for runtime renaming.

Things you DON'T get from matrx-ai (because they're aidream-specific):
- `aidream.api.context_objects` (aidream context shapes)
- `aidream.services.*` (aidream business logic)
- `aidream.api.utils.tool_merge.apply_unified_tools` — but you can mirror it; matrx-ai exposes the building blocks (`ToolSpec`, `merge_request_tools`, `resolve_client_capabilities`, `resolve_agent_specs`) directly.

If you find yourself wanting an aidream import, the answer is: build the equivalent in matrx-local (you have the same primitives), or open a PR against matrx-ai to expose the missing primitive cleanly via `configure(...)` injection.

---

## The 78-tool migration table

Source: live query against `public.tools WHERE source_app='matrx_local'`. Categories preserved as-is.

| Old name | New canonical | Category |
|---|---|---|
| `evaluate_javascript` | `matrx-local:evaluate_javascript` | advanced |
| `memory` | `matrx-local:memory` | advanced |
| `record_gif` | `matrx-local:record_gif` | advanced |
| `resize_window` | `matrx-local:resize_window` | advanced |
| `computer` | `matrx-local:computer` | core |
| `form_input` | `matrx-local:form_input` | core |
| `navigate` | `matrx-local:navigate` | core |
| `tabs` | `matrx-local:tabs` | core |
| `downloads` | `matrx-local:downloads` | files |
| `drop_file` | `matrx-local:drop_file` | files |
| `read_pdf` | `matrx-local:read_pdf` | files |
| `upload_file` | `matrx-local:upload_file` | files |
| `get_element_details` | `matrx-local:get_element_details` | inspection |
| `get_request_body` | `matrx-local:get_request_body` | inspection |
| `read_network_requests` | `matrx-local:read_network_requests` | inspection |
| `clipboard` | `matrx-local:clipboard` | interaction |
| `local_browser_click` | `matrx-local:browser_click` | local_browser |
| `local_browser_eval` | `matrx-local:browser_eval` | local_browser |
| `local_browser_extract` | `matrx-local:browser_extract` | local_browser |
| `local_browser_navigate` | `matrx-local:browser_navigate` | local_browser |
| `local_browser_screenshot` | `matrx-local:browser_screenshot` | local_browser |
| `local_browser_tabs` | `matrx-local:browser_tabs` | local_browser |
| `local_browser_type` | `matrx-local:browser_type` | local_browser |
| `local_list_document_folders` | `matrx-local:list_document_folders` | local_documents |
| `local_list_documents` | `matrx-local:list_documents` | local_documents |
| `local_read_document` | `matrx-local:read_document` | local_documents |
| `local_search_documents` | `matrx-local:search_documents` | local_documents |
| `local_write_document` | `matrx-local:write_document` | local_documents |
| `local_bash` | `matrx-local:bash` | local_execution |
| `local_bash_output` | `matrx-local:bash_output` | local_execution |
| `local_task_stop` | `matrx-local:task_stop` | local_execution |
| `local_edit_file` | `matrx-local:edit_file` | local_file_ops |
| `local_glob` | `matrx-local:glob` | local_file_ops |
| `local_grep` | `matrx-local:grep` | local_file_ops |
| `local_list_directory` | `matrx-local:list_directory` | local_file_ops |
| `local_read_file` | `matrx-local:read_file` | local_file_ops |
| `local_write_file` | `matrx-local:write_file` | local_file_ops |
| `local_hotkey` | `matrx-local:hotkey` | local_input |
| `local_mouse_click` | `matrx-local:mouse_click` | local_input |
| `local_mouse_move` | `matrx-local:mouse_move` | local_input |
| `local_type_text` | `matrx-local:type_text` | local_input |
| `local_archive_create` | `matrx-local:archive_create` | local_media |
| `local_archive_extract` | `matrx-local:archive_extract` | local_media |
| `local_image_ocr` | `matrx-local:image_ocr` | local_media |
| `local_image_resize` | `matrx-local:image_resize` | local_media |
| `local_pdf_extract` | `matrx-local:pdf_extract` | local_media |
| `local_fetch_url` | `matrx-local:fetch_url` | local_network |
| `local_fetch_with_browser` | `matrx-local:fetch_with_browser` | local_network |
| `local_mdns_discover` | `matrx-local:mdns_discover` | local_network |
| `local_network_info` | `matrx-local:network_info` | local_network |
| `local_network_scan` | `matrx-local:network_scan` | local_network |
| `local_port_scan` | `matrx-local:port_scan` | local_network |
| `local_research` | `matrx-local:research` | local_network |
| `local_scrape` | `matrx-local:scrape` | local_network |
| `local_search` | `matrx-local:search` | local_network |
| `local_applescript` | `matrx-local:applescript` | local_os |
| `local_get_installed_apps` | `matrx-local:get_installed_apps` | local_os |
| `local_powershell` | `matrx-local:powershell` | local_os |
| `local_focus_app` | `matrx-local:focus_app` | local_process |
| `local_kill_process` | `matrx-local:kill_process` | local_process |
| `local_launch_app` | `matrx-local:launch_app` | local_process |
| `local_list_ports` | `matrx-local:list_ports` | local_process |
| `local_list_processes` | `matrx-local:list_processes` | local_process |
| `local_battery_status` | `matrx-local:battery_status` | local_system |
| `local_clipboard_read` | `matrx-local:clipboard_read` | local_system |
| `local_clipboard_write` | `matrx-local:clipboard_write` | local_system |
| `local_disk_usage` | `matrx-local:disk_usage` | local_system |
| `local_notify` | `matrx-local:notify` | local_system |
| `local_open_path` | `matrx-local:open_path` | local_system |
| `local_open_url` | `matrx-local:open_url` | local_system |
| `local_screenshot` | `matrx-local:screenshot` | local_system |
| `local_system_info` | `matrx-local:system_info` | local_system |
| `local_system_resources` | `matrx-local:system_resources` | local_system |
| `local_top_processes` | `matrx-local:top_processes` | local_system |
| `local_focus_window` | `matrx-local:focus_window` | local_window |
| `local_list_windows` | `matrx-local:list_windows` | local_window |
| `local_minimize_window` | `matrx-local:minimize_window` | local_window |
| `local_move_window` | `matrx-local:move_window` | local_window |

To regenerate this table from the live DB:

```sql
SELECT
  name AS old_name,
  'matrx-local:' || REGEXP_REPLACE(name, '^local_', '') AS new_canonical,
  COALESCE(category, '-') AS category
FROM public.tools
WHERE source_app = 'matrx_local'
ORDER BY category, name;
```

---

## Verification checklist

For each PR:

- [ ] PR 1 (registration): `SELECT * FROM ui_client WHERE name='matrx-local'` → 1 row; `tl_executor_kind WHERE name='matrx-local.bridge'` → 1 row.
- [ ] PR 2 (rename): `SELECT count(*) FROM tools WHERE name LIKE 'matrx-local:%'` matches the count of bare-name `matrx_local` tools you migrated.
- [ ] PR 3 (wire format): end-to-end test — agent calls `matrx-local__bash` → matrx-local runs `bash` handler → result returns successfully.
- [ ] PR 4 (cloud-agent connection): pairing token flow works end-to-end with matrx-frontend.
- [ ] PR 5 (cleanup): `WHERE name = 'local_bash' AND is_active = true` → 0 rows after cleanup.

For the whole migration done:
- [ ] No code reads `source_app = 'matrx_local'` for filtering (use `name LIKE 'matrx-local:%'`).
- [ ] No code references the old `local_` prefix on tool names.
- [ ] All matrx-local tools have a `tl_def_surface` row for at least one `matrx-local/*` surface.
- [ ] All matrx-local tools have a `tool_handlers` row pointing at `matrx-local.bridge`.
