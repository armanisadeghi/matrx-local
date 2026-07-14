---
status: active
updated: 2026-07-14
repos: [matrx-local, aidream]
owner-context: W7 — collapse matrx-local's ~115 flat tools into actions + bundles, and wire the desktop into the platform delegation system exactly like matrx-extend
---

# Actions, delegation & tool bundles for matrx-local — handoff

## Arman's vision (verbatim intent)

"The tools system has changed significantly and we are now using 'actions' so
that we have far fewer tools. Also, this system needs to be updated to
properly use the delegation system and we'll need to create tool bundles
properly, just like Matrx Extend does."

And the standing frame: "It's our portal to the user's computer for
everything — private, confidential data such as chat they don't want leaving
their computer, private files, private data, disk access, desktop access,
local models, local codebase."

## Disambiguation (read this before writing any code)

"Action" means three things in this platform; only two matter here:

1. **Action-enum mega-tools** — one `tool.definition` row whose `parameters`
   is a discriminated union keyed on `action`. Reference: the `note` tool
   (`packages/matrx-ai/matrx_ai/tools/implementations/notes.py:302-350`,
   `action` ∈ list/get/create/update/patch/delete, `$variants` in DB
   parameters). `tabs`, `ai`, `data`, `workbook`, `document` all ship this
   shape. THIS collapses sibling tools.
2. **Capability + discovery-loader + DB bundles** — matrx-extend exposes ~95
   browser tools as ONE `load_browser_tools(category)` tool
   (`matrx_ai/tools/implementations/browser_discovery.py:330-503`) declared
   by the `browser-dom` Capability (`matrx_ai/capabilities/browser_dom.py`),
   backed by `tool.bundle` rows with `platform.associations` member edges
   (`aidream/db/tool_managers.py:78-143`) expanded per-surface by
   `aidream/services/tooling/surface_resolver.py:201-280`. THIS collapses the
   advertised surface.
3. **"Matrx Actions"** (`aidream/services/tooling/matrx_actions.py`,
   apply-policy cascade in `matrx_ai/capabilities/models.py:111-138`) — an
   output-directive system (agent emits `verb:noun` in text; a dispatcher
   applies it). A different axis. NOT required for this workstream; do not
   conflate.

## What already exists (verified 2026-07-14, file:line)

- `matrx-local` is a registered `tool.executor` AND is in matrx-ai's
  `_CLIENT_EXECUTOR_ROOTS` (`matrx_ai/tools/registry.py:1260-1279`) — the
  delegation engine already treats the desktop as a first-class client kind.
- 115 tools bound in the cloud (`tool.binding`, executor `matrx-local`) via
  the cloud-is-canon `app/tools/tool_sync.py` changeset flow.
- Delegation resolution is DB-driven, one pass:
  `apply_unified_tools` (`aidream/services/tooling/tool_merge.py:305-789`) →
  `_resolve_active_client_kinds` (`:979-1044`) →
  `resolve_executor_binding` (`registry.py:722-755`); tools with no viable
  executor are DROPPED pre-flight (`matrx_ai/tools/merge.py:566-608`).
- Suspend/resume wire contract is live server-side:
  `_suspend_for_delegation` (`matrx_ai/orchestrator/executor.py:464-526`)
  marks the call `delegated` and ends the stream; the client POSTs
  `/conversations/{id}/tool_results` (`aidream/api/routers/conversations.py:475-713`)
  then `/resume` on `continuation_needed` (`:1015-1126`). matrx-extend's
  client half: `matrx-extend/src/lib/tools/dispatch.ts`; canonical doc
  `matrx-frontend/features/agents/docs/CLIENT_TOOL_SUSPEND_RESUME.md`.
- matrx-local's execution primitive already exists: the `tool` command in
  `app/api/extension_handlers.py:157+` (reachable via `/extension/rpc`, WS,
  and Supabase Broadcast) executes one call and returns a structured result.
- `client.capabilities` envelope shape: `ClientContext`
  (`matrx_ai/capabilities/models.py:73-117`) — surface, capabilities,
  state payloads, amendments, mcp, apply_policy; unknown capability → 422.

## The gap — CLOSED for steps 1-4 (2026-07-14)

~~No `ui.ui_surface` row has `executor_name='matrx-local'`.~~ FIXED —
`matrx-local/desktop` is live (aidream 0170). The cloud registry now carries
19 action-enum mega-tools bound to `matrx-local` (115 flat rows retired),
9 desktop bundles with member edges, and surface defaults. What's still
missing for end-to-end delegation: a matrx-ai RELEASE carrying
`desktop-native`/`load_desktop_tools` (sits as aidream local commit
`f99caf82b`), the suspend/resume client half in the engine (step 5), and
the acceptance drill (step 6).

## Progress log (2026-07-14, W7 agent — steps 1-4 DONE + EXERCISED)

### ✅ 1. Desktop surface seeded (EXERCISED live)
aidream migration `0170_seed_matrx_local_surface.sql` (aidream local commit
`a9ac784dc`), applied via Supabase MCP + ledgered in
`public._schema_migrations`. Live-verified: `ui.ui_client` `matrx-local`,
`ui.ui_surface` `matrx-local/desktop` (executor `matrx-local`, parent
`matrx-default/default`), `tool.surface_defaults` row (filled by step 4).

### ✅ 2. 115 → 19 action-enum mega-tools (EXERCISED live, pushed)
matrx-local commits `b8c936098` + `e87a21114` (pushed to origin main):
- `app/tools/actions.py` — `ACTION_GROUPS` (19 groups covering all 115
  legacy tools exactly; build fails loudly on orphans), generic fan-out
  handler factory (wraps legacy handlers via `dispatch()`; arg aliases
  `tab_action`/`window_action`/`service_action` for the three tools that
  already had an `action` param), schema composers emitting BOTH the
  standard `input_schema` and the flat cloud dialect with `$variants`
  (verified byte-shape against the platform `note` row).
- Catalog: `advertised` flag + `cloud_parameters`; legacy entries stay
  dispatchable (extension RPC transition) but unadvertised.
- tool_sync: advertised-only diff + `RETIRED` class emitting ACTIVE
  deactivation SQL.
- CLOUD APPLIED + VERIFIED: 19 NEW rows inserted, 115 legacy rows retired
  (binding + definition `is_active=false`; pre-checked only matrx-local
  binds them); `tool_sync status` drift-clean against the live route
  (serves exactly 19). Mega fan-out exercised in-process (File list/grep,
  bad-action error, legacy Read still dispatches). 257 parity/
  characterization tests green; local bridge builds 19 ToolDefinitions and
  matrx-ai's own `_build_json_schema` renders clean provider schemas
  ($variants skipped, action enum + required present).
- Categories aligned to the 9-bundle taxonomy (desktop-files/-shell/
  -system/-input/-web/-media/-ner/-mac/-windows) in code AND the live rows.

### ✅ 3. desktop-native capability + load_desktop_tools (code EXERCISED in-proc; NOT released)
aidream local commit `f99caf82b` (packages/matrx-ai — DO NOT push aidream
main without coordination; see tracker):
- `matrx_ai/capabilities/desktop_native.py` — `DesktopNativePayload`
  (platform, engine_version, instance_id, tunnel_state,
  permissions_granted, loaded_categories) + `DESKTOP_NATIVE` capability;
  factory advertises ONLY `load_desktop_tools`.
- `matrx_ai/tools/implementations/desktop_discovery.py` — category-routed
  loader mirroring browser_discovery (registry-driven, platform-gates
  local_mac_apps/local_windows_ps against payload.platform,
  loaded_categories short-circuit, `queue_tool_changes(add=…, remove=self)`).
- `_generated_declarations.py` `LoadDesktopToolsArgs` (9-category Literal)
  + `_reg`; `built_in.py` registers the capability.
- DB row `load_desktop_tools` (definition + matrx-ai-core binding, enum
  matches declaration) applied live + verified.
- Exercised: capability registration, factory, arg validation, payload
  model, module import; matrx-ai capability+tools suites 278 passed.
- **NOT exercised:** aidream startup drift gate (needs a configured boot —
  W2's dirty worktree blocks a clean live boot), and NOTHING runs it in
  prod until a **matrx-ai release > 0.4.0** ships this module and the
  server + matrx-local upgrade. That release is the gate for steps 5-6.

### ✅ 4. Bundles + surface defaults (EXERCISED live)
aidream migration `0171_seed_desktop_bundles.sql` (local commit
`fc46f94fb`), applied + ledgered. 9 `tool.bundle` rows with
`bundle:list_desktop-*` listers (generic bundle_lister family) and
`platform.associations` member edges covering all 19 megas — live-verified
member counts files=2 shell=1 system=7 input=1 web=3 media=2 ner=1 mac=1
windows=1. `matrx-local/desktop` surface defaults:
`always_include_tools=[load_desktop_tools]`,
`always_include_bundles=[desktop-files, desktop-shell]`.

### Known cosmetic nits (not worth blocking)
- Two mega descriptions have truncated first-sentence fragments
  ("hotkey: Send a keyboard shortcut (e.g"; local_windows_ps repeats
  "(Windows only) (Windows only)"). Descriptions are DB-canonical — fix
  with a one-line UPDATE whenever convenient.

## Remaining work queue

### 5. Suspend/resume client half in matrx-local (NOT STARTED)
When a cloud agent delegates a desktop tool: matrx-local (via Broadcast rpc /
tunnel) executes with the existing `tool` command primitive, then POSTs
`/conversations/{id}/tool_results` and `/resume` on `continuation_needed` —
port matrx-extend's `dispatch.ts` logic into the engine (Python, in the
cross-component router path) so the desktop works headless (no UI required).
Set `max_client_wait_seconds` per tool class (slow desktop ops: downloads,
media gen, long shell commands).

Open design question the next agent must answer first: HOW does the
delegated-call notification reach a headless desktop? matrx-extend receives
the suspension inside the stream it opened; a headless desktop is not in the
stream. Candidates: (a) aidream emits a Broadcast rpc envelope to the
desktop's channel when a matrx-local-bound call suspends (server-side work
in aidream), (b) the desktop polls pending delegated calls. Inspect
`_suspend_for_delegation` (`matrx_ai/orchestrator/executor.py:464-526`) and
`app/api/cross_component_router.py` before writing anything. Then: execute
via the existing `tool` command primitive (mega names ARE dispatcher names
now: File/Shell/… — the Broadcast rpc path dispatches by PascalCase, cloud
names by the local bridge), POST `/conversations/{id}/tool_results`, then
`/resume` on `continuation_needed`. Set `max_client_wait_seconds` per tool
class (downloads, media gen, long shell).

### 6. Verification (acceptance) — GATED on a matrx-ai release
**Gate:** publish matrx-ai (>0.4.0) carrying `desktop-native` +
`load_desktop_tools` (aidream local commit `f99caf82b`), deploy the server,
bump matrx-local's floor. Also observe the aidream startup tool-drift gate
green on first boot (declaration ↔ DB row parity was constructed but not
boot-verified).
Then, from the web app with the desktop online: an agent turn advertises
`load_desktop_tools`, loads `desktop-files`, calls
`local_file(action="move", …)`, the call suspends → executes on this
machine → resumes, and the file actually moved on disk. Then the same over
Broadcast with the tunnel down. Pin the merge behavior with a
characterization test on the aidream side (surface `matrx-local/desktop`
yields delegated bindings, not drops).

## Contracts

- Cloud is canon for tool rows; every change flows through
  `tool_sync emit-changeset` → Supabase MCP → drift-clean verification.
- The `client.capabilities` envelope shape is shared across matrx-frontend /
  matrx-extend / matrx-local — byte-compatible; changes go through the
  connect-matrx-extend skill's process on the aidream side.
- matrx-ai floor: whatever release carries the new capability module —
  coordinate with the client-host handoff
  (`aidream/docs/handoffs/matrx-ai-client-host-completion.md`); its optional
  item 4 (serve `tool.binding` rows to clients) becomes REQUIRED here for
  delegation parity on the desktop's own agent loop.

## Coordination

- W2 (chat mirror) owns `chat.*` local tables the resume flow may read;
  W3 (file sync) owns pointer hydration inside file tools — the `file`
  mega-tool wrapper must preserve those hooks (they live in the underlying
  handlers, so wrapping keeps them).
- Update your row in `docs/handoffs/00-INTEGRATION-TRACKER.md` when you stop.
