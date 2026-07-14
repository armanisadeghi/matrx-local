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

## The gap (why none of this works for the desktop today)

**No `ui.ui_surface` row has `executor_name='matrx-local'`.** Only
`chrome-extension/assistant|pilot` exist. So the active-executor set never
contains `matrx-local`, every desktop tool resolves to "server", and the
viability gate drops all 115 on a real agent turn. The desktop is reachable
only through the chrome-extension `desktop` bundle's single
`desktop_run_command` bridge tool. Everything below hangs off fixing that.

## Priority work queue

### 1. Seed the desktop surface (small, unblocks everything)
`ui.ui_surface` row `matrx-local/desktop` (parent `matrx-default/default`,
`executor_name='matrx-local'`) + `tool_surface_defaults` for it. Follow the
seeding pattern the connect-matrx-extend skill documents. Applied live via
Supabase MCP + verified, same session (house migration rule).

### 2. Collapse siblings into action-enum mega-tools (the `note` pattern)
Target shape (~115 → ~25-30 definitions):
- `file` — action ∈ read/write/edit/glob/grep/move/copy/delete/rename/mkdir/list
  (the 11 file_ops tools, incl. the 2026-07-13 additions)
- `window`, `process`, `input`, `audio`, `clipboard`, `screen`,
  `browser` (local Playwright), `net`, `monitor`, `schedule`,
  `documents`, `media`, platform clusters (`mac_apps`, `windows_ps`)
Each: one discriminated-union arg model (`$variants`), one dispatcher-side
fan-out to the existing `tool_*` handlers (do NOT rewrite handlers — wrap
them; the `note` tool at notes.py:341-348 is the template). Cloud rows
updated via `tool_sync emit-changeset` (retire the old per-tool rows in the
same changeset — no dual registrations; cloud is canon).
The dispatcher keeps accepting the legacy PascalCase names from existing
surfaces (extension RPC) during the transition — but the ADVERTISED registry
is mega-tools only.

### 3. Desktop capability + discovery loader (the matrx-extend pattern)
- New capability `desktop-native` (matrx-ai `capabilities/` module +
  registration): payload carries platform, granted OS permissions, engine
  version, tunnel state, loaded_categories — mirror `BrowserDomPayload`.
- One `load_desktop_tools(category)` discovery tool (copy
  `browser_discovery.py` per its own docstring instruction), categories =
  the mega-tool groups above. `queue_tool_changes(add=…, remove=[self])`.
- Result: an agent turn starts with ONE desktop tool advertised.

### 4. Bundles in the DB
`tool.bundle` rows (`desktop-files`, `desktop-system`, `desktop-input`,
`desktop-media`, `desktop-browser`, …) with `platform.associations` member
edges; reference from `matrx-local/desktop` surface defaults
(`always_include_bundles[]`). Optional `bundle:list_desktop_*` rows backed by
the generic `bundle_lister` for MCP-style discovery parity.

### 5. Suspend/resume client half in matrx-local
When a cloud agent delegates a desktop tool: matrx-local (via Broadcast rpc /
tunnel) executes with the existing `tool` command primitive, then POSTs
`/conversations/{id}/tool_results` and `/resume` on `continuation_needed` —
port matrx-extend's `dispatch.ts` logic into the engine (Python, in the
cross-component router path) so the desktop works headless (no UI required).
Set `max_client_wait_seconds` per tool class (slow desktop ops: downloads,
media gen, long shell commands).

### 6. Verification (acceptance)
From the web app with the desktop online: an agent turn advertises
`load_desktop_tools`, loads `desktop-files`, calls `file(action="move", …)`,
the call suspends → executes on this machine → resumes, and the file actually
moved on disk. Then the same over Broadcast with the tunnel down. Pin the
merge behavior with a characterization test on the aidream side (surface
`matrx-local/desktop` yields delegated bindings, not drops).

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
