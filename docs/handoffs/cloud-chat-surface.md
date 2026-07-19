---
status: active
updated: 2026-07-18
repos: [matrx-local, aidream, matrx-frontend]
vision: [docs/CLOUD_CHAT_SURFACE.md]
---

# Cloud Chat as a platform Surface — handoff

Supersedes `actions-delegation-bundles.md` (W7 — deleted; its remaining
items are absorbed below).

## Vision — Arman's words

- "A surface automatically extends a set of tools and things that make the
  agent a better fit for that setting… the system needs to be set up such
  that we can override that behavior for one or all of the tools… if we
  enforce it for all and there's no way to turn it off, that becomes a
  problem because some of the really small models should never get that
  many tools."
- "We need to give the agent the tools it cannot live without, and the rest
  are offered in a bundle."
- "Our local agent is designed to primarily be a coding agent and one who
  works like 'Cowork' to totally control the user's computer and get work
  done. It's the way you get things done on your computer without having to
  be there yourself!"
- "NOTHING should be set up to just be local on the user's machine. All
  settings and configs must go to the web and be controllable from the web
  as well."
- The composer "+" menu: "there are some core settings that are in that +
  icon that the system cannot live without, including… Model selection,
  settings overrides, adding tools, removing tools, adding skills, etc. And
  of course, files… we need it all there, with 'coming soon' for what we
  don't have yet." (Reference: matrx-frontend SmartAgentInput — "a MASSIVE
  and incredible component" — full inventory mapped in
  `docs/CLOUD_CHAT_SURFACE.md`.)
- Tool taxonomy: "The tools should be in 1 or 2 categories MAX!… Instead of
  'Desktop Mac, Desktop media, etc' we need either just Desktop or a simple
  split for two Desktop categories." And: "the bundles need to be based on
  purpose and usage. The agent loads a bundle based on the things it's
  going to do."
- "The ultimate goal is to fully unify this system so that your local
  models, cloud models, and everything else works together as one."
- Standing frame (W7): the desktop is "our portal to the user's computer
  for everything — private, confidential data… private files, private
  data, disk access, desktop access, local models, local codebase."

## Resources

- Architecture map + coming-soon ledger: `docs/CLOUD_CHAT_SURFACE.md`.
- Delegation protocol + UI stream claims: `app/services/delegation/FEATURE.md`;
  canonical wire doc `matrx-frontend/features/agents/docs/CLIENT_TOOL_SUSPEND_RESUME.md`.
- Key code (matrx-local): `desktop/src/lib/desktop-client-context.ts`
  (envelope), `desktop/src/hooks/use-cloud-chat.ts` (multi-segment stream
  loop, run controls), `desktop/src/components/chat/PlusMenu.tsx`,
  `desktop/src/components/settings/CloudAgentToolsCard.tsx`,
  `app/tools/actions.py` (19 mega-tools, 2 categories),
  `app/services/delegation/engine.py`, `app/api/chat_routes.py`
  (`/chat/delegation/ui-*`, `/chat/local-tools*`).
- Key code (aidream): `packages/matrx-ai/matrx_ai/capabilities/desktop_native.py`,
  `.../tools/implementations/desktop_discovery.py`,
  `aidream/services/tooling/tool_merge.py` (the merge funnel),
  `db/migrations/0201_consolidate_desktop_tool_categories.sql` (APPLIED live
  + ledgered 2026-07-18).
- Reference client: `matrx-extend/src/lib/tools/dispatch.ts` +
  `matrx-extend/docs/REQUEST_PAYLOAD_CONTRACT.md`.
- Tests: `tests/unit/test_delegation_client.py` (ui-claim test),
  `tests/unit/test_delegation_disabled_tools.py`,
  `tests/parity/test_settings_parity.py` (brace-aware parser +
  `ENGINE_OWNED_PY_KEYS`).
- Frontend + menu reference: `matrx-frontend/features/agents/components/inputs/smart-input/`
  (PlusAttachMenu, RunControlsTabPanel) and
  `.../redux/execution-system/utils/build-tool-injection.ts`.

## Remaining work (priority order)

1. **Push + deploy aidream.** The repo is 1 commit ahead of origin
   (migration file + docstrings). The live DB is already migrated, but the
   running server lazily caches desktop category names — until it restarts,
   `load_desktop_tools` may still advertise the old 9 categories. Deploy,
   then confirm `load_desktop_tools` with no args lists exactly `desktop`
   and `desktop-web`.
2. **Live end-to-end drill on the installed app** (v1.3.131+ carries all of
   this). In Cloud Chat ask an agent: "Create ~/Desktop/delegation-test.txt
   containing 'hello from cloud'." Expect: `load_desktop_tools` call →
   "running on this computer…" status → stream resumes in place → file on
   disk. Repeat with the tool disabled in Settings → Cloud & Account →
   Cloud Agent Tools: expect a clean refusal, no execution. Watch engine
   log for `[delegation]` lines; `GET /chat/delegation/status` shows
   `ui_claims`.
3. **Skills picker in the + menu** → serialize as `skill_config.included`.
   Needs a skills catalog fetch (check aidream `/api/` routers for a skills
   listing; frontend ref: RunSkillPicker + shape chips). Replace the
   "coming soon" row in `PlusMenu.tsx`.
4. **Images & media attachments** — resource ContentBlocks on `user_input`
   (frontend ref: ResourcePickerMenu; private files route through aidream
   `/assets`). Desktop should also offer screenshot-attach (engine
   `local_screen`).
5. **Purpose bundles** — finer than the 2 categories ("email", "coding",
   …): `tool.bundle` rows + membership edges (platform.associations) +
   surface defaults. Trap: bundle name `desktop` is TAKEN by the
   chrome-extension taxonomy; pick prefixed names.
6. **Remaining + menu parity**, each currently an honest "Soon" row: audio
   recording (engine already has Whisper), notes/documents/scratchpad,
   active context (`scope_ids`), memory controls, sandbox binding.
7. **`loaded_categories` tracking** — the envelope payload field exists;
   the UI doesn't accumulate categories across turns yet, so re-discovery
   happens every turn.
8. **Verify matrx-frontend declares `desktop-native` on web turns** so a
   web chat can drive this desktop (code suggests it landed —
   `build-tool-injection.ts` capabilities + DesktopPresenceIndicator —
   but exercise it live; the old W7 handoff flagged it unfinished).
9. **Tier 2 instance targeting** — two engines on one account still race
   for delegated calls: `docs/TIER2_DESKTOP_INSTANCE_TARGETING.md`. The
   envelope already sends `instance_id`.
10. **Web UI for the `cloud_tools` setting** — it syncs via the per-instance
    `app_settings` row today (web-controllable in principle); a proper
    frontend editor needs the remote-edit conflict handling flagged as
    divergence #3 in `docs/SYNC_CONTRACT.md`.

Traps for the next agent:
- Never advertise tool schemas in the request — the surface pattern is ONE
  capability string + state envelope; the server injects from the DB.
- The delegation engine is the ONLY executor (durable outbox,
  execute-once). The UI only claims the resume stream; do not add a second
  execution path.
- `use-cloud-chat.ts` streaming is a multi-segment loop (`consumeSegment` +
  resume). Touch `buildRequest` inputs, not the loop structure.
- Any nested dict added to `DEFAULT_SETTINGS` must be either mirrored in TS
  `AppSettings` or listed in `ENGINE_OWNED_PY_KEYS` in the parity test.

## Done

- Surface envelope on every cloud request — `desktop/src/lib/desktop-client-context.ts`.
- In-view delegated continuation (UI stream claims, self-healing TTL) —
  `app/services/delegation/engine.py` + `use-cloud-chat.ts`.
- 9 → 2 tool categories, live DB migrated + 0171 bundles deactivated —
  aidream migration 0201; `app/tools/actions.py`.
- "+" menu core (model / temperature / max-tokens overrides, tool
  exclusions, text-file attachments, coming-soon rows) — `PlusMenu.tsx`.
- Web-synced `cloud_tools.disabled_tools` exposure setting, enforced every
  sweep + Settings card — `settings_sync.py`, `CloudAgentToolsCard.tsx`.
- W7 foundation (mega-tools, delegation round trip, capability + discovery
  loader) — see `app/services/delegation/FEATURE.md`.
- Released and CI-green as v1.3.131; intact through v1.3.138.
