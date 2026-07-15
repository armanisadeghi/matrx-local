# Arman Tasks — Matrx Local

_Last updated: 2026-07-14 (step-7 pass: both official-docs asks APPROVED + applied — dev/live isolation env vars in configuration.md, api_key_validation + elevenlabs/fastino in settings-catalog.md; desktop security-posture doctrine added to CLAUDE.md; continuing asks one-by-one)_

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

> **Boundary rule (Arman, 2026-07-14, emphatic):** this file is for
> **lead-developer** actions ONLY — secrets you hold, accounts/dashboards you
> control, purchases, canonical decisions. A **user** action (grant an OS
> permission, add their own API key, accept a license) NEVER belongs here.
> Filing one is an agent hiding a missing feature behind the user: if the app
> needs it, the app must fire an urgent in-app notification and deep-link the
> user to the exact grant page — one click. Re-filing a user action here is the
> bug. See MXL-D-048 + the "Proactive in-app permission + API-key prompts" task.

## Active (ranked — quickest wins first within priority)

- [ ] **Publish the next `matrx-ai` package wave after the canonical agent-source changes are committed** — PyPI `0.4.7` was verified to omit `matrx_ai/client_host/agent_source.py`, so Matrx Local intentionally withholds `agent_execution_v1` and routes saved-agent turns through AIDream. Once the AIDream workspace changes are safely committed (the worktree currently also contains unrelated active work), run `./scripts/publish-all-packages.sh --only matrx-ai`; then an agent can raise matrx-local's floor/lock to that published version and re-run the live capability smoke. Do not tag the current dirty worktree wholesale.
- [ ] **Train “Hey Matrix” OWW model** — no custom model exists yet (app ships stock `hey_jarvis`/`alexa`). **Corrected 2026-07-14:** the old "4 commands + 2 GB" doc was fiction — real openWakeWord training is the full YAML-config pipeline (piper-sample-generator + multi-GB feature/background/RIR datasets). Runbook + honest options in [`docs/wake-word-training.md`](../docs/wake-word-training.md). **Recommended: openWakeWord's official Colab notebook** (`dscripka/openWakeWord` → `notebooks/automatic_model_training.ipynb`, `target_phrase="hey matrix"`) — free GPU, ~1 hr. Local training env is already set up on Arman's Mac (`~/wakeword-train`, imports clean). Decision for Arman: Colab now, full local harness, or defer and ship stock `hey_jarvis` interim. Deliver the resulting `.onnx` via CDN (not the installer) per [`docs/CDN_ASSETS_PLAN.md`](../docs/CDN_ASSETS_PLAN.md).
- [ ] **Windows EV code-signing cert — IN PROGRESS, check back 2026-07-16** — Before broad public launch (kills SmartScreen warnings). Arman purchased it 2026-03-02; it's mid-provisioning at Sectigo. **Next agent on/after 2026-07-16: check for updates and walk Arman through completion — offer to launch the browser to the Agreement Link.**
      - **Vendor:** Sectigo EV Code Signing (Token & US Shipping), 1 yr, bought via **SignMyCode**. Payment $469.99 on 2026-03-02, txn `SMC1014824S932733`.
      - **Action required (from Sectigo email 2026-03-03):** execute the Subscriber Agreement + confirm the Code Signing Request. **Verification code:** `tKGHm82ntIr0q089JvUVKNNVP47NlEqN`.
      - **Agreement Link:** the actual URL is in the Sectigo 2026-03-03 "operational" email (Arman must surface it — not captured here). Once Arman pastes it, the agent can `open <url>` (macOS) / shell-open plugin to launch it, then have him enter the verification code above.
      - **Hard-copy fallback:** download the Agreement, sign, email to `docs@sectigo.com`.
      - **Cloud-KMS option:** SignMyCode notes EV certs can be configured via Google Cloud KMS for signing Windows executables — worth evaluating vs the physical token for CI signing.
- [ ] **CDN: matrx-local assets (models + binaries)** — full plan written
      2026-07-14: [`docs/CDN_ASSETS_PLAN.md`](../docs/CDN_ASSETS_PLAN.md). Covers the
      complete bucket-upload list (LLM GGUF / image / video / whisper / NER /
      wake-word / TTS / LoRA — defaults flagged), the `matrx-local/` bucket
      layout, installer-slimming targets (~150–200 MB: cloudflared, llama-server
      +dylibs, ffmpeg), two stale-artifact purges to verify, and the exact code
      seams (`ASSETS_CDN_BASE` in config + Rust const, with a Rust token-leak
      guard to fix first). **Arman action:** create the `matrx-local/` prefix in
      our assets bucket and upload the bold defaults first; an agent wires the
      config seam + repoints catalogs. Assets are PUBLIC (never signed URLs);
      CDN base is remote-app-config, not an env var (CLAUDE.md config posture).

**GitHub Actions secrets** (if anything missing on new fork): `AIDREAM_SERVER_URL_LIVE`, `VITE_SUPABASE_*`.

---

## Pending Arman review

_(asks prepared by non-interactive `task-hygiene` runs land here — none pending; the API-key-validation ask was promoted into ranked Active 2026-07-14)_

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

- [x] REMOVED "`MAIN_SERVER` URL" — category error under the new config posture (§2 of CLAUDE.md): the aidream server URL is a non-secret runtime value that belongs in remote app config with a compiled-in public default, NOT an env var an end user's machine would need Arman to "set". Another agent owns the app-config migration; not an Arman ask (2026-07-14)
- [x] REMOVED "Grant Screen Recording" — not a lead-developer task, it's an END-USER OS-permission grant that was hiding a missing feature (the app should proactively notify + deep-link the user to the grant page, not ask Arman). Folded into the "Proactive in-app permission + API-key prompts" AGENT task (Screen Recording added explicitly) and MXL-D-048 broadened to OS permissions + the urgent-notification pattern (Arman, 2026-07-14)
- [x] Approved official-docs update for the API-key-validation surface — `settings-catalog.md` § Engine settings gained the `api_key_validation` blob key (verdicts only, user-supplied keys) + `elevenlabs`/`fastino` in `VALID_PROVIDERS`; also added the desktop security-posture doctrine to CLAUDE.md (Arman approved + requested, 2026-07-14)
- [x] Approved official-docs update for dev/live isolation (MXL-D-043) — `configuration.md` gained a Dev/live isolation section + 4 env-var rows (`MATRX_PORT_BASE`/`MATRX_LIVE_ENGINE`/`MATRX_INSTANCE_SALT`/`MATRX_HOME_DIR`), `settings-catalog.md` proxy-port + engine-port rows updated (Arman approved, agent applied, 2026-07-14)
- [x] matrx-files 0.1.5 deployed to files.matrxserver.com via deploy.sh (Arman, 2026-07-14) — verified: image version check passed, health green, /files/sync/* now 401 (live) — W3 cloud side ON
- [x] file_sync_mode added to official settings catalog (Arman approved 2026-07-14)

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
