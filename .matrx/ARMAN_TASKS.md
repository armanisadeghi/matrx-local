# Arman Tasks — Matrx Local

_Last updated: 2026-07-13 (hygiene pass: GLiNER NER review closed — Arman approved, work assigned to agent; item moved to Done)_

> **Ask-Arman list for agents — NOT Arman's personal inbox.** These are things
> only Arman can do (secrets, accounts, dashboards, decisions). When one blocks
> your current work, ASK HIM IN CHAT right then — concise background, then
> EXACTLY what to do, with copy-paste commands/links.
>
> - Before asking, VERIFY the task is still real (check the key store, env,
>   code, dashboard — never spend Arman's time on something already done).
> - Active is ranked: (urgency × importance) ÷ effort-for-Arman. Seconds-long
>   items float to the top so they get knocked out first.
> - Each entry should carry its prepped ask (what/where/why + exact steps) so
>   any agent can ask instantly. Hygiene passes: `task-hygiene` skill.
>
> Code work → `.matrx/AGENT_TASKS.md`. Discoveries → `FOUND_DEFECTS.md`.
> **Never enter `.arman/`** — that directory is Arman-private.

---

## Active (ranked — quickest wins first within priority)

- [ ] **Deploy matrx-files 0.1.4 — unblocks the entire file-sync feature (W3)**
      (~3 min) — The desktop file-sync engine is built, tested (35+14 tests),
      and E2E-drilled against a locally-run service, but the LIVE
      `files.matrxserver.com` still lacks the new `/files/sync/*` endpoints.
      Two steps, both gated actions an agent couldn't take alone:
      ① `cd ~/code/aidream && git push origin refs/tags/matrx-files/v0.1.4`
      (tag exists locally at `fd4053cc7`; the push triggers the PyPI OIDC
      publish workflow — a public PyPI release). ② Once on PyPI, bump the
      container: `ssh matrx-sandbox`, then per `packages/matrx-files/DEPLOY.md`
      rebuild `matrx-files` at `==0.1.4` (docker rm + run, NOT restart), run
      the verify triad + `GET /files/sync/changes?limit=1` with a user JWT.
      Or reply "proceed, deploy 0.1.4" and an agent with permissions does both.
- [ ] **Approve settings-catalog official-doc update for `file_sync_mode`**
      (~30 s) — the new setting (off|pointers|full, default pointers, W3) is
      live in both settings layers but `docs/official/settings-catalog.md` is
      Arman-only. Reply "yes, add file_sync_mode to the settings catalog" and
      any agent syncs it (source of truth: `app/services/file_sync/FEATURE.md`).
- [ ] **Approve official-docs update for dev/live isolation** (~2 min) — The
      MXL-D-043 fix added env vars (`MATRX_PORT_BASE`, `MATRX_LIVE_ENGINE`,
      `MATRX_INSTANCE_SALT`) and changed the `proxy_port` default to be
      port-base-derived (22180 live / 22280 dev). `docs/official/configuration.md`
      and `docs/official/settings-catalog.md` are now stale, and agents may not
      edit `docs/official/**` without your approval. Ask: reply "yes, update
      official docs for MXL-D-043" and any agent can sync them from
      `docs/TESTING_LADDER.md` (the non-official source of truth).
- [ ] **Grant Screen Recording** (~1 min) — Setup Wizard → Review & Grant →
      Grant Access. This now actually calls `CGRequestScreenCaptureAccess` in
      the engine (the process that runs `screencapture`), which is what finally
      lists it in System Settings → Privacy & Security → Screen Recording.
      macOS may need an app restart before it reads back as granted.
- [ ] **Reconcile AIDream server ↔ matrx-ai tool registry (aidream repo)** — The
      deployed server 404s `GET /api/ai-tools/app/matrx_local` (the endpoint
      matrx-ai 0.1.26 from PyPI calls); the DB has migrated to the surface-based
      system (`tool_def` + `tool_surface_defaults`) and has no `matrx_local`
      surface. The desktop backfills its local tool definitions so chat tools
      work without the server, but *cloud-registered* tools won't reach the
      desktop until either: (a) matrx-ai 0.2.x+ (new protocol) is published to
      PyPI and matrx-local bumps it, or (b) the server re-adds a compat route
      for `/api/ai-tools/app/{source_app}`. Option (a) is the real fix — same
      playbook as matrx-scheduler 0.3.0.
- [ ] **Train “Hey Matrix” OWW model** — [`docs/wake-word-training.md`](../docs/wake-word-training.md)
- [ ] **Windows EV code-signing cert** — Before broad public launch (SmartScreen).
- [ ] **`MAIN_SERVER` URL** — For a future “real” proxy proof test (callback from server). Pick canonical production base URL.
- [ ] **CDN: GGUF mirrors** — `assets.aimatrx.com/llm-models/` (per prior plan).
- [ ] **CDN: llama-server binaries** — `assets.aimatrx.com/llama-server/`.
- [ ] **CDN: Whisper `.bin` models** — `assets.aimatrx.com/whisper-models/`.

**GitHub Actions secrets** (if anything missing on new fork): `AIDREAM_SERVER_URL_LIVE`, `VITE_SUPABASE_*`.

---

## Pending Arman review

_(asks prepared by non-interactive `task-hygiene` runs land here)_

- **Approve an official-docs edit for the new API-key validation surface.**
  `docs/official/settings-catalog.md` is now stale in two ways and I can't edit
  `docs/official/**` without your say-so:
  1. New `AppSettings` blob key **`api_key_validation`** —
     `{provider: {verdict, account, checked_at}}`, written by
     `ApiKeysRepo.record_validation()`. Verdicts only; never a key value.
  2. `VALID_PROVIDERS` gained **`elevenlabs`** and **`fastino`** (they were in
     `PROVIDER_ENV_MAP` but not `VALID_PROVIDERS`, so PUT/bulk 422'd and they
     were `.env`-only).
  Say the word and I'll write both up.

---

## Future

- [ ] **AIDream AI relay** — JWT-authenticated endpoint so desktop can run cloud models without user API keys.
- [ ] **Scraper rate limits** — Per-user on remote server.
- [ ] **Wake-on-LAN / home APIs** — Backlog.
- [ ] **Reverse tunnel product** — Backlog.
- [ ] **Personal Cloudflare tunnel cleanup** — Optional; see old notes in repo if still applicable.

---

## Review queue (doc / backlog audit 2026-03-24)

_Skim and check off or delete. Agents: do not open `.arman/` — leave that to Arman._

| Item | Note |
|------|------|
| Doc-hygiene leftovers from deleted root AGENT_TASKS | Confirm whether private drafts under `.arman/` still matter; whether `PLATFORM_AUDIT.md` should stay gone |
| `PLATFORM_AUDIT.md` (repo root, if present) | Historically wrong about `initPlatformCtx`; delete or rewrite if it returns |
| Private drafts (Arman only): `.arman/pending/ui-overhaul/`, `.arman/in-progress/proxy/` | UI overhaul draft / proxy research — Arman decides keep vs delete |
| [`local-llm-inference-integration.md`](../local-llm-inference-integration.md) | Long operational doc — keep; update when LLM packaging changes |
| [`whisper-transcription-integration.md`](../whisper-transcription-integration.md) | Same for voice |
| [`docs/react-migration-notes-api.md`](../docs/react-migration-notes-api.md) | Still valid for external clients (`/documents` → `/notes`) |

**Suggested tickets to spawn (your call):**

1. **ORM / matrx-ai safety review** — Decide what “client-only” means for shipping; track in AGENT P0.
2. **App store assets** — Icon + screenshots when branding final.
3. **Production Supabase health** — If any user still sees `app_settings` 404, RLS/project mismatch; SQL verify on `txzxabzwovsujtloxrus`.

---

## Done

- [x] "Add your Hugging Face token" was stale and wrong to ask — verified directly against `~/.matrx/matrx.db` (2026-07-13, no secret values printed) that Arman's HF token IS already stored, encrypted, under `api_keys.huggingface`. My earlier check only looked at `.env`/environment/huggingface-cli cache, never the app's own key store — that was my mistake, not a missing key. Filed two real bugs instead: MXL-D-047 (possible resolver/startup-race causing a 401 despite a stored key — needs live repro) and MXL-D-048 (process bug: "add your X key" items don't belong in ARMAN_TASKS at all when the app can self-serve prompt) — both in `FOUND_DEFECTS.md`; MXL-D-048's fix folded into the "Proactive in-app permission + API-key prompts" task in `.matrx/AGENT_TASKS.md`
- [x] "Accept FLUX.1-schnell license" clarified — HF gate acceptance is per-account/per-token, not app-wide; Arman clicking accept fixes nothing for other users. Confirmed the app already handles this gracefully per-user (no blow-up): `app/services/downloads/failures.py:66` `hf_gate_not_accepted` + `manager.py:1063-1068` convert the raw 401 into a friendly "Accept the license on Hugging Face" prompt with a direct link, shown to whichever user hits the gate. Nothing to fix, nothing for Arman to do system-wide — removed as purely optional/personal — 2026-07-13
- [x] "Grant Full Disk Access" ask reclassified — not an Arman action, it's a missing in-app feature (proactive contextual permission prompts). Filed as real work in `.matrx/AGENT_TASKS.md` ("Proactive in-app permission prompts") — 2026-07-13
- [x] Supabase SMTP "broken" ask rejected — not in production yet, SMTP intentionally off (MXL-D-028 stays open in FOUND_DEFECTS as a pre-launch item, not an Arman ask) — 2026-07-13
- [x] matrx-ai published past 0.3.6 — `pyproject.toml` floor now `>=0.4.0`, `aidream/packages/matrx-ai` at `0.4.0` (verified 2026-07-13). Note: `app/services/ai/engine.py` still has the pre-import workaround for `db/_registry.py` — that's leftover agent code cleanup, not an Arman ask; flag for `.matrx/AGENT_TASKS.md` if not already tracked.
- [x] GLiNER NER plan reviewed/approved — assigned to agent, build underway (TASK-001, `.matrx/AGENT_TASKS.md`) — 2026-07-13
- [x] Media Vault escrow PRIVATE key secured (password manager + offline backup; key at `~/.matrx-escrow/matrx-media-escrow-private.pem`, recovery via `scripts/vault-recover.py`) — 2026-07-12
- [x] Apple Developer / notarization path live.
- [x] Supabase OAuth redirect `aimatrx://auth/callback`.
- [x] `app_settings` / `note_folders` verified in Supabase + RLS (historical session).
- [x] Migrations 003 `forbidden_urls`, 005 hardware columns, 006–008 hardware/tunnel (per prior sessions).
- [x] llama-server binaries downloaded via `scripts/download-llama-server.sh`.
- [x] GitHub secrets: `AIDREAM_SERVER_URL_LIVE`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY`.
- [x] Windows installer NSIS + hooks.
- [x] First-run / setup wizard shipped in app (`FirstRunScreen` + wizard).
- [x] Moved ask-Arman list from `.arman/ARMAN_TASKS.md` → `.matrx/ARMAN_TASKS.md` (2026-07-12); agents must not enter `.arman/`.
