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

## Google Workspace on this surface (2026-08-18)

`tool.surface_defaults` for `matrx-local/desktop` now includes the `google`
bundle, so cloud agents reach both first-party Google tools from desktop:

- `google_workspace` (executor `aidream`) — Doc/Sheet read, append, create,
  bounded write, and `prepare_email`. Runs on the server; nothing to build here.
**Attaching a Google file (2026-08-24).** The "+" menu has a **Google files**
section listing the Docs/Sheets this user ALREADY registered — read straight
from `users.integration_connection_resources` on healthy `drive.file`
connections by `desktop/src/lib/google-workspace.ts::listRegisteredGoogleFiles`.
Checked files ride the next turn as the reserved context key
`__google_files` — a PLAIN ARRAY of Drive file ids on a top-level `context`
object (max 20; the server truncates):

```jsonc
"context": { "__google_files": ["1AbC…", "1XyZ…"] }
```

aidream (`services/google_workspace/attachments.py`, reached through
`conversation_context/context_utils.py`) resolves the ids, names the files for
the agent, and injects `google_workspace` for that turn — which is why this is
a context directive and not a content block. Mirrors to keep byte-identical:
`GOOGLE_FILES_CONTEXT_KEY` here, matrx-frontend
`features/google-workspace/attach/googleFileContext.ts`, and the server
constant. Wire shape copied from matrx-frontend
`execute-instance.thunk.ts` — `context` is sent ONLY when non-empty, on all
three cloud body shapes.

There is **no Drive browsing and no Google Picker in this webview**, and there
must never be one: registering a new file stays on the web app, and both empty
states link out to `/user-settings/integrations/google-workspace` in the system
browser. Attachments are per-conversation state in `use-cloud-chat.ts` and are
dropped when the active conversation changes.

- `google_email_send` (client-only) — **parked, never executed.** The engine
  holds the proposal and `<GmailReviewCard>` above the composer IS the
  authorization: the user sees and edits the exact message and only their click
  sends it. See `app/services/delegation/FEATURE.md` § User-review calls. The
  matching `tool.binding` is to executor `matrx-local` (a CLIENT binding,
  parallel to the chrome-extension one); there is still no server executor and
  there must never be one.

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
`user_input`), Google file attach (`context.__google_files`, see above).

User-level machine policy: Settings → Cloud & Account → Cloud Agent Tools
(`cloud_tools.disabled_tools`, synced, web-controllable, enforced in the
delegation engine).

## Remaining work (parity with matrx-frontend SmartAgentInput)

Marked "coming soon" in the + menu; each item names its frontend reference:

1. **Skills picker** → `skill_config.included` (ref: RunSkillPicker + shape
   chips). Needs an agents/skills catalog fetch from aidream.
2. **Images & media attachments** → resource ContentBlocks + upload path
   (ref: ResourcePickerMenu). Desktop should shine here (local files,
   screenshots). STILL OPEN — the resource picker's **Google** lane shipped
   2026-08-24 (see § Google Workspace above), the media lane did not, so the
   "Images & media" coming-soon row stays true and stays in the menu.
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
10. **Instance targeting (Tier 2)** — **DONE, not a gap.** The "two engines on one account
    race" claim was false and is retired (verified live 2026-08-22): `chat.tool_call` carries
    `target_instance_id` / `claimed_by_instance_id` / `claimed_at` / `claim_expires_at` in the
    live DB; aidream's `_claim_pending_calls_for_instance`
    (`aidream/services/ai_execution/tool_results.py`) claims rows in one atomic
    `UPDATE … RETURNING` under a 6-hour lease, scoped to
    `target_instance_id IS NULL OR = :caller`; this engine sends `instance_id`
    (`app/services/delegation/client.py:173`); the picker route `/desktop-instances` is mounted.
    The contract is `common-docs/systems/clients/client-tool-delegation/FEATURE.md` §2.7.

## Unification note (Arman's directive)

Local models, cloud models, and cloud agents should converge on ONE request
shape. The local `/ai` surface (ai_routes.py) already mirrors aidream's
API; as it grows, keep the `client` envelope + `config_overrides` semantics
identical so the UI needs no per-target branches beyond the base URL.
