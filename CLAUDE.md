# CLAUDE.md — Matrx Local

**Why you're reading this: you are doing DESKTOP-APP work in this repo.** This file
carries what a desktop agent must know to avoid this repo's known failure modes —
Tauri + Python sidecar specifics, how local tools are exposed to the platform, and
pointers to the shared systems this client consumes. It is NOT a rulebook: every rule
body lives in exactly one canonical doc (linked below); this file holds one-liners and
links only, per the charter at
`/Users/armanisadeghi/code/common-docs/policies/claude-md-charter.md` (≤200 lines; no
stories, quotes, or incident narratives). Content outside that purpose is a defect —
relocate it, don't grow this file.

**Shared checkout, many concurrent writers — NORMAL, never a finding.** Commit and
push small batches to `origin/main` continuously; never run tree-wide destructive git;
never request your own branch/worktree/PR. Canonical:
`/Users/armanisadeghi/code/common-docs/policies/shared-checkout.md`.

## What this is

A **Tauri v2 desktop app** (Rust + React 19 + TS 7 + Vite 6 + Tailwind 3.4/shadcn,
`darkMode: "class"`) with a **Python 3.13 / FastAPI sidecar engine** exposing ~80
local tools (filesystem, shell, scraping, documents, media…) via REST and WebSocket.
It is a **client** of the aidream server (`server.app.matrxserver.com`) for all AI and
heavy work, and — like every client — talks to the ONE Supabase Postgres directly
(`https://db.matrxserver.com`, publishable key + user JWT + RLS; never a project ref,
never a service-role key). End-user app, not a developer tool. Not Next.js/Vercel.
Package managers: pnpm (desktop), uv (Python). Technical depth:
[docs/official/ARCHITECTURE.md](docs/official/ARCHITECTURE.md).

- **Python:** `run.py` → `app/main.py` → `app/tools/dispatcher.py` (tools in `app/tools/tools/`)
- **React:** `desktop/src/App.tsx` → `desktop/src/pages/`, hooks in `desktop/src/hooks/`
- **Rust:** `desktop/src-tauri/src/lib.rs` (sidecar lifecycle, tray, transcription, LLM)
- **Build:** `scripts/build-sidecar.sh` + `specs/*.spec` (PyInstaller, 4 platforms)

```bash
# Engine (T1): uv sync --all-extras && ./scripts/dev.sh   (plain `uv sync` STRIPS extras)
#   dev.sh --fresh = throwaway private home for agents needing their own matrx.db
# Frontend (T2): cd desktop && pnpm install && pnpm dev   # http://localhost:1420
# Full app:      cd desktop && pnpm tauri:dev
```

## Platform laws (canonical bodies in common-docs; one-liners only here)

- **Clients consume, never reimplement.** No agent UUID, prompt, resolution ladder, or
  rebuilt server-owned record in this repo; ask the platform by name. Sole exception:
  the offline local-model path ("does it run with the platform unreachable?"). →
  `/Users/armanisadeghi/code/common-docs/policies/clients-consume-never-reimplement.md`
- **Mandates: which agent runs a step is a DATABASE answer, never a code answer.**
  Python half: `matrx_ai.configure()` installs `ServerMandateSource`
  (`app/services/ai/engine.py`). TS half: `desktop/src/lib/agents/mandates.ts` +
  `desktop/src/lib/api/routes/ai.ts` — Cloud Chat defaults to the `local.cloud_chat`
  Mandate (`mandate:<key>` UI ref → `POST /api/ai/mandates/{key}`; local target resolves
  via `GET /api/mandates/{key}/resolution`). Never add an agent UUID here; only L3 (local
  personas, unruled) remains open. →
  `/Users/armanisadeghi/code/common-docs/systems/agents/mandates/RUNTIME.md` (+ `FEATURE.md`, `ROLLOUT.md`)
- **No unapproved schedules.** Every scheduled task exists only with Arman's approval
  by name and interval, registered and claimed via the master registry. →
  `/Users/armanisadeghi/code/common-docs/operations/scheduled-tasks.md`
- **THE USER-INPUT LAW.** Structured information is never passed as user text — it
  becomes named variables or context. →
  `/Users/armanisadeghi/code/common-docs/systems/agents/agent-variable-binding/FEATURE.md`
- **Limits are knobs, and agents set them.** Never a hardcoded constant or an absent
  control. → `/Users/armanisadeghi/code/common-docs/policies/limits-are-knobs-agents-set-them.md`
- **We don't do legacy.** Replaced systems are migrated, repointed, DELETED — never frozen or
  kept "just in case". → `/Users/armanisadeghi/code/common-docs/policies/no-legacy.md`
- **Human steps are guided sessions.** Anything only Arman can do: one link, one task, what to
  look for, what to report — never a list or menu. →
  `/Users/armanisadeghi/code/common-docs/policies/human-steps-are-guided-sessions.md`
- **Every org-scoped write carries an explicit `organization_id`.** Database defaults,
  personal/system fallbacks, and parent-inheritance triggers are defects. Emergency work order:
  `/Users/armanisadeghi/code/common-docs/projects/no-db-assigned-org/PLAN.md`

## Configuration posture — everything here ships to the user

No trusted server, no trusted environment: every layer runs on the user's machine.
Exactly four kinds of configuration — identify which before adding any value:

| Kind | Where it lives |
|---|---|
| Env vars / `.env` | **DEVELOPER-ONLY.** Never shipped; no shipped behavior may depend on one; never ask Arman to "set an env value". Comment out, don't delete. [docs/official/configuration.md](docs/official/configuration.md) |
| Non-secret runtime values (URLs, flags, min versions) | **Remote app config** — anon-readable Supabase row, disk cache, compiled-in fallback. [app/services/app_config/FEATURE.md](app/services/app_config/FEATURE.md); SOR: `/Users/armanisadeghi/code/common-docs/systems/platform/app-config/FEATURE.md` |
| The user's own secrets (their Anthropic key, HF token…) | In-app key store FIRST, then platform Credential Vault; resolution order decided ONLY in `app/services/ai/key_manager.py` (local → Vault → `.env`). Missing key / unavailable Vault = a STATE with a prompt UI, never an error. [app/services/credential_vault/FEATURE.md](app/services/credential_vault/FEATURE.md) |
| Our secrets | **Never exist on the client.** Privileged capability = aidream API call or the token broker. `/Users/armanisadeghi/code/common-docs/systems/platform/token-broker/FEATURE.md` |

Compiled-in catalogs (GGUF lists, LoRAs, presets, prompts…) are demoted fallback data
— edit DB `catalog_entries` rows and read via `app/services/catalogs` accessors, never
grow or import the in-code lists. [app/services/catalogs/FEATURE.md](app/services/catalogs/FEATURE.md)

## Hard Rules (bodies in the linked docs; numbering 0–9 is stable — code cites it)

0. **Lifecycle ownership:** each level of the process tree touches only its own
   children and cascades start/stop before reporting done. Rust never pkills
   engine-owned processes; Python never touches llama-server (Rust-owned, deliberately
   absent from `app/preflight.py`); all state changes go through `app/launcher.py`'s
   registry. There is ONE browser pool for page fetches, owned by `ScraperEngine`
   (`borrow_browser()`) — nothing else in a fetch path calls `async_playwright()`. →
   [docs/official/lifecycle-ownership.md](docs/official/lifecycle-ownership.md) +
   [app/services/scraper/FEATURE.md](app/services/scraper/FEATURE.md)
1. **ONE scraper: the `matrx-scraper` package — never fork it.** This repo holds only
   the local execution lane (`app/services/scraper/`, `use_proxy=False` always).
   Missing capability → add to the package with tests, then consume. →
   [app/services/scraper/FEATURE.md](app/services/scraper/FEATURE.md); package/implementation
   split: `/Users/armanisadeghi/code/common-docs/policies/package-vs-implementation.md`
2. **One scrape result shape:** consumers read `ScrapeResult.success` /
   `failure_reason`; persistence via `scrape_store.content_from_result`; the ONE
   client payload converter is `app/services/scraper/result_contract.py`, read only by
   `desktop/src/lib/scrape-result.ts`; the server streams NDJSON that the proxy
   converts to SSE. → [app/services/scraper/FEATURE.md](app/services/scraper/FEATURE.md)
3. **Graceful degradation:** the engine works without a Brave key (search disabled) —
   never a hard dependency. Playwright Chromium is downloaded, never bundled: the
   missing browser is a STATE with one-click install
   ([app/services/scraper/browser_runtime.py](app/services/scraper/browser_runtime.py)).
4. **Ports/discovery:** live engine 22140–22159, discovery `~/.matrx/local.json`.
5. **Every Python import is declared in pyproject.toml** (no `except ImportError` as
   dependency management); add + `uv sync --all-extras` in the same commit.
6. **PyInstaller:** hidden imports sync across all 4 `specs/*.spec` AND
   `build-sidecar.sh` (Python import names); a collection that can fail must FAIL THE
   BUILD (shared lists in `specs/_managed_runtime_bundle.py` / `specs/_office_bundle.py`);
   `hiddenimports` does not cover data files; prove it on the artifact with
   `scripts/verify-frozen-runtime.py`. → [docs/official/build-lessons.md](docs/official/build-lessons.md)
7. **macOS llama-server must be re-signed** (`codesign --force --timestamp --options
   runtime`) before `tauri-action`; ad-hoc upstream signatures fail Gatekeeper.
8. **Tauri JSON configs are strictly schema-validated** — no `"$comment"` or other
   non-schema keys in `tauri.conf.json` or platform overlays.
9. **Dev and live are separate worlds — never cross them.** Packaged app owns
   `~/.matrx` + 22140–22159; every source run owns `~/.matrx-dev` + 22240–22259.
   Enforced in `run.py`, Rust `debug_assertions`, `desktop/src/lib/engine-ports.ts`.
   The live position needs `MATRX_LIVE_ENGINE=1` / `dev.sh --live` with the installed
   app quit. → [docs/TESTING_LADDER.md](docs/TESTING_LADDER.md)
10. **Single ML stack:** the managed media runtime slot is the ONLY torch provider;
    recipes declare `requires_ml_runtime`, never install their own. →
    [app/services/optional_packages/FEATURE.md](app/services/optional_packages/FEATURE.md)
11. **`matrx_*` host wiring is configured ONCE**, first thing in the lifespan, in
    [app/package_integration.py](app/package_integration.py) — never inside a feature.
12. **Conversation-start contract mirrors aidream exactly** (`conversation_id` +
    `is_new` + `store` required; this repo's copy of the gate is
    `app/services/ai/local_ai_task.py::resolve_conversation_gate` — change one, change
    both). → `/Users/armanisadeghi/code/common-docs/systems/agents/conversation-start-contract/FEATURE.md`
13. **Migrations are applied the moment they're created** (`migrations/NNN_name.sql`,
    Supabase MCP `apply_migration`, project `brsgrqvjdzwihsvnfqkf`); an unapplied
    migration on disk = `PGRST204` at runtime — apply it before anything else.
14. **React hook/effect rules** (each maps to a shipped polling outage): `actions`
    wrapped in `useMemo`; never `actions` as an effect dep; init fetches inside the
    hook; persistent state in app-level Context providers; polling gated on the
    specific boolean with cleanup; focus handlers only for externally-changed data. →
    [docs/REACT_PATTERNS.md](docs/REACT_PATTERNS.md)

## Where the rest lives

| Topic | Doc |
|---|---|
| Auth (publishable key, user JWT, no `SUPABASE_JWT_SECRET` ever, `/extension/*` token posture) | [docs/official/authentication.md](docs/official/authentication.md) + `app/api/extension_auth.py` |
| Settings keys / env reference | [docs/official/settings-catalog.md](docs/official/settings-catalog.md) · [docs/official/configuration.md](docs/official/configuration.md) |
| matrx-extend ↔ engine (Channel B live; envelopes, auth, verification, tunnel runbook) | [/Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md](/Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md); SOR: `/Users/armanisadeghi/code/common-docs/systems/clients/extension/STATE.md` |
| Cloud Chat as a surface (desktop tools for cloud agents, ordered blocks, "+" menu) | [docs/CLOUD_CHAT_SURFACE.md](docs/CLOUD_CHAT_SURFACE.md) + [app/services/delegation/FEATURE.md](app/services/delegation/FEATURE.md) |
| Google Workspace on desktop — the `google_email_send` review card IS the authorization; the engine parks the call, never sends | [app/services/delegation/FEATURE.md](app/services/delegation/FEATURE.md) § User-review calls |
| Media durability (signed URL = handoff, never identity; pass `file_id`) | `/Users/armanisadeghi/code/common-docs/systems/media/media-durability/FEATURE.md` |
| Matrx Envelope · Tool registry · DB rules | `/Users/armanisadeghi/code/common-docs/systems/{matrx-envelope,tool-registry,db-rules}/FEATURE.md` |
| Sync doctrine · file sync · coding-session bridge | [docs/SYNC_CONTRACT.md](docs/SYNC_CONTRACT.md) · [app/services/file_sync/FEATURE.md](app/services/file_sync/FEATURE.md) · [app/services/coding_sessions/FEATURE.md](app/services/coding_sessions/FEATURE.md) |
| Content IR — NEVER parse a stream; server envelopes render via the SHARED packages (`desktop/src/features/content-ir/`); catalog is `GET /workflow/kinds`, never a `content_ir.*` table read; DB components stay OFF | [docs/CONTENT_IR_CONSUMER_GUIDE.md](docs/CONTENT_IR_CONSUMER_GUIDE.md) |
| Any image/video UI (one `MediaDescriptor`, one thumb, one action set — never a hand-rolled `<img>`) | [desktop/src/components/media/FEATURE.md](desktop/src/components/media/FEATURE.md) |
| Multi-window (labels, leader election, panels checklist) | [desktop/src/panels/FEATURE.md](desktop/src/panels/FEATURE.md) |
| Code-local rules | `app/tools/FEATURE.md`, `app/api/FEATURE.md`, `app/services/*/FEATURE.md` |

## Testing & verification

- **Pick the right rung before testing anything:** [docs/TESTING_LADDER.md](docs/TESTING_LADDER.md).
- **Changed anything that runs at startup?** A green `pnpm typecheck` is not evidence
  the app starts — run `./scripts/smoke.sh` (Windows: `scripts\smoke.ps1`). →
  [docs/SMOKE_HARNESS.md](docs/SMOKE_HARNESS.md)
- **Before finishing any code change, scan
  [.matrx/LANDING_CHECKLIST.md](.matrx/LANDING_CHECKLIST.md)** (trigger→verify pairs
  for shipped regression classes).
- **Authenticated UI verification** uses the shared canonical admin test account —
  never create a replacement or ask Arman for credentials:
  `AI_ADMIN_USERNAME="admin@admin.com"`, `AI_ADMIN_PASSWORD="<see AI_ADMIN_PASSWORD in .env>"`
  (Playwright reads them from gitignored `desktop/.env.test`; legacy
  `TEST_USER_EMAIL`/`TEST_USER_PASSWORD` aliases accepted). Super-admin identity —
  never use it to prove ordinary-user RLS or entitlements.

## Task tracking

- [.matrx/AGENT_TASKS.md](.matrx/AGENT_TASKS.md) — ONLY Arman-approved agent work
  (rules: `.matrx/AGENT_INSTRUCTIONS.md`; inbox: `.matrx/TASKS_FROM_USER.md`).
- [FOUND_DEFECTS.md](FOUND_DEFECTS.md) — evidence-backed discoveries awaiting
  approval; check `## Rejected` before filing; opportunistic fixes OK, then delete the
  entry. [CURRENT_ERRORS.md](CURRENT_ERRORS.md) — error inbox from live testing;
  every error gets a home, then the inbox is cleared.
- [.matrx/ARMAN_TASKS.md](.matrx/ARMAN_TASKS.md) — ask-Arman blockers only (verify
  still real first). User grants (OS permissions, their own keys) are never Arman
  tasks — the app guides the user. `.arman/` is Arman-private: never read, write, or
  list it. Maintenance: `/task-hygiene`.
- Never let a discovered issue go untracked; never invent approved tasks.

## Working style

Production-grade only (no stubs/TODOs); simplest version first; one task at a time;
keep going until done or stuck; update docs when code changes. Never edit
`docs/official/**` (or any `*/official/*`) without Arman's explicit approval — flag
staleness instead.

- **Logging into any Matrx UI**: sign in as `admin@admin.com` — the password is `AI_ADMIN_PASSWORD` in the `.env` of `aidream` or `matrx-frontend` (`AI_ADMIN_USERNAME` holds the email).

## 🚨 THE LATEST LAW — @ai-matrx packages are NEVER pinned

Every `@ai-matrx/*` dependency in this repo is declared `"latest"` — never a version, never a
range. Guard: `npm run check:matrx-latest` (fails on any pin). Version problems are fixed by
releasing forward, never by pinning — a pin licenses silent drift and workaround code (the
disaster that nearly killed AI Dream). Law + rationale:
`../common-docs/policies/typescript-package-standard.md` § THE LATEST LAW. The guard script lives in `desktop/package.json`.
