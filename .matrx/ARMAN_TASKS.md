# Arman Tasks — Matrx Local

_Last updated: 2026-07-12_

> **Ask-Arman list for agents** — not Arman’s personal inbox. When blocked on
> secrets, accounts, CDN, OS permissions, or a decision only Arman can make,
> agents file it here and **ask him in chat** when it blocks current work.
>
> Code work → `.matrx/AGENT_TASKS.md`. Discoveries → `FOUND_DEFECTS.md`.
> **Never enter `.arman/`** — that directory is Arman-private.

---

## Active

- [X] **CRITICAL: Secure the Media Vault escrow PRIVATE key** — The Private
  media vault's recovery backdoor is an RSA-4096 keypair generated
  2026-07-09. The PUBLIC key is embedded in the app
  (`app/services/media_vault/escrow.py`); the PRIVATE key lives ONLY at
  `~/.matrx-escrow/matrx-media-escrow-private.pem` on this machine (mode
  600, outside every repo). Do now: (1) copy it into your password manager
  AND one offline backup — if this file is lost before you back it up, the
  backdoor is gone for every vault created meanwhile; (2) NEVER commit it
  anywhere. Recovery usage:
  `uv run python scripts/vault-recover.py --private-key <pem> --vault-dir ~/.matrx/media/vault --new-password <pw>`
  (rewraps the user slot without exposing contents) or `--out <dir>` to
  decrypt everything. Optional upgrade later: create an asymmetric KMS key
  (RSA-4096, `alias/matrx-media-escrow` — do NOT reuse the redaction
  escrow key), and we swap the embedded public key + import this private
  key or re-wrap vaults on next unlock.
- [ ] **URGENT: Review local GLiNER NER plan before agents build** —
  Cloud NER API volume is a major cost driver; local NER is required.
  Read [`docs/GLINER_NER_INTEGRATION_PLAN.md`](../docs/GLINER_NER_INTEGRATION_PLAN.md)
  and answer the decision checklist (default model, PII/relations in P1 vs P3,
  desktop UI for P1, whether to offer XXL in-app). Tracked as TASK-001 in
  `.matrx/AGENT_TASKS.md` — agents must not start coding until you approve.
- [ ] **Reconcile AIDream server ↔ matrx-ai tool registry (aidream repo)** — The
  deployed server 404s `GET /api/ai-tools/app/matrx_local` (the endpoint
  matrx-ai 0.1.26 from PyPI calls); the DB has migrated to the surface-based
  system (`tool_def` + `tool_surface_defaults`) and has no `matrx_local`
  surface. The desktop now backfills its 62 local tool definitions from
  `LOCAL_TOOL_MANIFEST` so chat tools work without the server, but
  *cloud-registered* tools won't reach the desktop until either: (a) matrx-ai
  0.2.x (new protocol) is published to PyPI and matrx-local bumps it, or
  (b) the server re-adds a compat route for `/api/ai-tools/app/{source_app}`.
  Option (a) is the real fix — same playbook as matrx-scheduler 0.3.0.
- [ ] **Grant Screen Recording permission** — Setup wizard reports it denied;
  click "Review & Grant" (System Settings → Privacy & Security → Screen
  Recording) so screenshot tools work.
- [ ] **Train “Hey Matrix” OWW model** — [`docs/wake-word-training.md`](../docs/wake-word-training.md)
- [ ] **Windows EV code-signing cert** — Before broad public launch (SmartScreen).
- [ ] **`MAIN_SERVER` URL** — For a future “real” proxy proof test (callback from server). Pick canonical production base URL.
- [ ] **CDN: GGUF mirrors** — `assets.aimatrx.com/llm-models/` (per prior plan).
- [ ] **CDN: llama-server binaries** — `assets.aimatrx.com/llama-server/`.
- [ ] **CDN: Whisper `.bin` models** — `assets.aimatrx.com/whisper-models/`.
- [ ] **Image gen: FLUX.1 Dev HF gate** — Accept license at `https://huggingface.co/black-forest-labs/FLUX.1-dev` with your HF account so downloads work for gated users.

**GitHub Actions secrets** (if anything missing on new fork): `AIDREAM_SERVER_URL_LIVE`, `VITE_SUPABASE_*`.

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

## From the 2026-07-11 startup-error sweep (MXL-D-029 … D-032)

- [ ] **Add your Hugging Face token** — Settings → API Keys → Hugging Face.
      There is currently NO HF token anywhere on this machine: the app's key
      store holds only anthropic/cerebras/google/xai, and there is no
      `HF_TOKEN` in `.env`, in the environment, or in the huggingface-cli
      cache. That is why FLUX.1-schnell 401'd — the download went out
      unauthenticated. (The app now asks you for it properly instead of
      showing a 401.)
- [ ] **Accept the FLUX.1-schnell license** (only if you want that model) —
      https://huggingface.co/black-forest-labs/FLUX.1-schnell → "Agree and
      access repository". It is `gated: auto` (the only gated repo in the
      catalog); Apache-2.0 licensed but still gated. The app now links you
      straight there when the download hits the gate.
- [ ] **Grant Screen Recording** — Setup Wizard → Review & Grant → Grant Access.
      This now actually calls `CGRequestScreenCaptureAccess` in the engine (the
      process that runs `screencapture`), which is what finally lists it in
      System Settings → Privacy & Security → Screen Recording. macOS may need
      an app restart before it reads back as granted.
- [ ] **Publish matrx-ai 0.3.6** (fix is committed in `aidream/packages/matrx-ai`:
      `configure()` no longer loads `db/_registry.py` by file path, which was
      impossible in a PyInstaller bundle and killed the AI stack in every
      packaged build). Then raise the floor in matrx-local `pyproject.toml`
      (`matrx-ai>=0.3.6`) and drop the pre-import workaround in
      `app/services/ai/engine.py`. matrx-local works with 0.3.3 in the meantime.

## Done

- [x] Apple Developer / notarization path live.
- [x] Supabase OAuth redirect `aimatrx://auth/callback`.
- [x] `app_settings` / `note_folders` verified in Supabase + RLS (historical session).
- [x] Migrations 003 `forbidden_urls`, 005 hardware columns, 006–008 hardware/tunnel (`per prior sessions`).
- [x] llama-server binaries downloaded via `scripts/download-llama-server.sh` (per ARMAN note 2026).
- [x] GitHub secrets: `AIDREAM_SERVER_URL_LIVE`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY`.
- [x] Windows installer NSIS + hooks.
- [x] First-run / setup wizard shipped in app (`FirstRunScreen` + wizard).
- [x] Moved ask-Arman list from `.arman/ARMAN_TASKS.md` → `.matrx/ARMAN_TASKS.md` (2026-07-12); agents must not enter `.arman/`.
