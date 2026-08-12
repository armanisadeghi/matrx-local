# Cloud Chat as a platform Surface (matrx-local/desktop)

> Status 2026-07-18: CORE LIVE. Cloud Chat now declares this machine as a
> platform surface on every cloud request, cloud agents get the desktop
> tools automatically (with agent + user overrides), and the composer has a
> working "+" menu. This doc is the map + the remaining-work ledger.

## Architecture (mirror of matrx-extend, inverted advertisement)

The client never pushes tool schemas. Every cloud request carries one
envelope, and the aidream server injects tools from the DB:

```jsonc
"client": {
  "surface": "matrx-local/desktop",          // ui.ui_surface row (aidream mig 0170)
  "capabilities": ["desktop-native"],         // matrx_ai/capabilities/desktop_native.py
  "state": { "desktop-native": { platform, engine_version, instance_id } },
  "amendments": { "remove": [...] }           // per-conversation tool exclusions
}
```

- Envelope builder: `desktop/src/lib/desktop-client-context.ts` (identity
  from engine `/health`; keep in sync with the Python twin in
  `app/services/delegation/engine.py::_build_client_context`).
- Attached in `use-cloud-chat.ts buildCloudChatRequest()` on all three request
  shapes (conversation / agent / bare chat), cloud target only.
- Agent and bare-chat starts go through
  `desktop/src/lib/conversation-start.ts`, which mints the required UUID and
  sends `conversation_id` + `is_new: true` + `store: true`. Follow-up turns
  use `/conversations/{id}` and do not resend the start-only assertion.
- Server side (already existed): surface defaults give agents
  `load_desktop_tools` + `local_file` + `local_shell`; the rest load on
  demand by category. Agent overrides: `agx_agent.tool_config`
  (`auto_tools_disabled`, `excluded_tools`) — small models simply opt out.
- Tool taxonomy: TWO categories since 2026-07-17 — `desktop` (machine
  control, 16 mega-tools) and `desktop-web` (browser/web/net, 3). Source:
  `app/tools/actions.py` + live `tool.definition.category` (aidream
  migration `0201_consolidate_desktop_tool_categories.sql`).

## Delegated execution + streaming continuation

Tool calls suspend the turn server-side; the delegation engine executes
them; the OPEN Cloud Chat view claims the continuation over loopback so the
resumed stream renders live. See `app/services/delegation/FEATURE.md`
§ Local UI stream claims.

## Ordered stream blocks (2026-07-19)

Live cloud-chat messages render from `ChatMessage.blocks` — an ordered
block list (text / thinking / tool_call / error) built event-by-event by
`StreamBlockBuilder` (`desktop/src/lib/chat-blocks.ts`), the desktop port
of matrx-frontend's `lib/chat-protocol`. Rules (do not regress):

- Blocks stay in true stream-arrival order; never group by type. A tool
  event closes the current text run — later text starts a NEW block.
- ONE tool block per `call_id`, anchored at first appearance, patched in
  place through `pending → running/delegated → complete | error`. Results
  match by ID, never by array position; a result arriving before (or
  without) its start event materializes the block.
- On stream error/abort, `failPendingTools()` force-terminates every
  non-terminal tool block so no card spins forever.
- Legacy flat `content` + `tool_calls`/`tool_results` are still maintained
  for cache/TTS/copy and for hydrated (DB-loaded) messages, which render
  through the legacy path in `ChatMessages.tsx` when `blocks` is absent.

## The "+" menu (`desktop/src/components/chat/PlusMenu.tsx`)

Working now: model override, temperature / max-tokens overrides
(`config_overrides`), per-conversation local-tool exclusions
(`client.amendments.remove`), text-file attachments (content parts on
`user_input`).

User-level machine policy: Settings → Cloud & Account → Cloud Agent Tools
(`cloud_tools.disabled_tools`, synced, web-controllable, enforced in the
delegation engine).

## Remaining work (parity with matrx-frontend SmartAgentInput)

Marked "coming soon" in the + menu; each item names its frontend reference:

1. **Skills picker** → `skill_config.included` (ref: RunSkillPicker + shape
   chips). Needs an agents/skills catalog fetch from aidream.
2. **Images & media attachments** → resource ContentBlocks + upload path
   (ref: ResourcePickerMenu). Desktop should shine here (local files,
   screenshots).
3. **Audio recording / transcription input** (engine already has Whisper).
4. **Notes / Documents / Scratchpad** (ref: DocumentsWorkspace,
   working-document thunks).
5. **Active context / scopes** → `scope_ids` (ref: features/scopes).
6. **Memory controls** → `memory` / `memory_model` / `memory_scope`.
7. **Sandbox / compute-target binding** (ref: SandboxPanel + token broker).
8. **Purpose bundles** — finer than the 2 categories (e.g. "email",
   "coding"): create `tool.bundle` rows + surface defaults per purpose.
   NOTE: bundle name `desktop` is TAKEN by the chrome-extension taxonomy.
9. **`loaded_categories` hint** in the envelope (skip re-discovery across
   turns) — payload field exists, UI doesn't track it yet.
10. **Instance targeting (Tier 2)** — two engines on one account still race
    (docs/TIER2_DESKTOP_INSTANCE_TARGETING.md); envelope already carries
    `instance_id`.

## Unification note (Arman's directive)

Local models, cloud models, and cloud agents should converge on ONE request
shape. The local `/ai` surface (ai_routes.py) already mirrors aidream's
API; as it grows, keep the `client` envelope + `config_overrides` semantics
identical so the UI needs no per-target branches beyond the base URL.
