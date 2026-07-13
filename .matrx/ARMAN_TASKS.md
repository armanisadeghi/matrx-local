# Arman Tasks — Matrx Local

_Last updated: 2026-07-12 (hygiene pass: SMTP P0 + Full Disk Access added; FLUX.1-dev gate deleted — verified out of catalog)_

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

- [ ] **P0: Fix Supabase SMTP — public email signup is broken platform-wide**
      (~5 min) — `auth.signUp` fails with "Error sending confirmation email"
      and the user row rolls back, so ANY self-serve email signup (and likely
      password-reset / magic-link emails) is dead on every Matrx surface
      (MXL-D-028, verified 2026-07-10 with two addresses). Do: Supabase
      dashboard → project `txzxabzwovsujtloxrus` → Authentication → Emails →
      SMTP Settings — check whether custom SMTP is enabled and its credentials
      are still valid (rotate/re-enter the provider key, e.g. Resend/SendGrid),
      then test with a throwaway signup. If no custom SMTP is set, the built-in
      sender is rate-limited/failing — configure a real provider.
- [ ] **Grant Full Disk Access to Matrx** (~1 min) — System Settings →
      Privacy & Security → Full Disk Access → enable Matrx (or the engine
      binary). The engine currently can't list `~/Documents/Matrx/Notes`
      (CURRENT_ERRORS E002); notes sync sits in a degraded state (with the
      hint surfaced in-app) until granted.
- [ ] **Add your Hugging Face token** (~1 min) — Settings → API Keys → Hugging
      Face. There is currently NO HF token anywhere on this machine: the app's
      key store holds only anthropic/cerebras/google/xai, and there is no
      `HF_TOKEN` in `.env`, in the environment, or in the huggingface-cli
      cache. That is why FLUX.1-schnell 401'd — the download went out
      unauthenticated. (The app now asks you for it properly instead of
      showing a 401.)
- [ ] **Grant Screen Recording** (~1 min) — Setup Wizard → Review & Grant →
      Grant Access. This now actually calls `CGRequestScreenCaptureAccess` in
      the engine (the process that runs `screencapture`), which is what finally
      lists it in System Settings → Privacy & Security → Screen Recording.
      macOS may need an app restart before it reads back as granted.
- [ ] **Accept the FLUX.1-schnell license** (~1 min, only if you want that
      model) — https://huggingface.co/black-forest-labs/FLUX.1-schnell →
      "Agree and access repository". It is `gated: auto` (the only gated repo
      in the catalog); Apache-2.0 licensed but still gated. The app links you
      straight there when a download hits the gate.
- [ ] **URGENT: Review local GLiNER NER plan before agents build** —
      Cloud NER API volume is a major cost driver; local NER is required.
      Read [`docs/GLINER_NER_INTEGRATION_PLAN.md`](../docs/GLINER_NER_INTEGRATION_PLAN.md)
      and answer the decision checklist (default model, PII/relations in P1 vs P3,
      desktop UI for P1, whether to offer XXL in-app). Tracked as TASK-001 in
      `.matrx/AGENT_TASKS.md` — agents must not start coding until you approve.
- [ ] **Publish matrx-ai 0.3.6** — fix is committed in `aidream/packages/matrx-ai`
      (`configure()` no longer loads `db/_registry.py` by file path, which was
      impossible in a PyInstaller bundle and killed the AI stack in every
      packaged build — MXL-D-029). Then agents raise the floor in matrx-local
      `pyproject.toml` (`matrx-ai>=0.3.6`) and drop the pre-import workaround
      in `app/services/ai/engine.py`. matrx-local works with 0.3.3 meanwhile.
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
