# CLAUDE.md -- Matrx Local

## Shared checkout, many concurrent writers — NORMAL, never a finding

Arman plus dozens of concurrent agents (across two machines) edit these repos simultaneously; **`origin/main` is the ONLY sync point.** As soon as your code won't crash the app, commit it and get it to remote main — batches of a few files, exactly like a human IDE session. Code held back in a private worktree or branch goes stale; and because the task it belonged to is already checked off as done, held-back code is not merely delayed — it is LOST, and resurfaces days later as an unexplained broken feature with no trail back to the conversation that wrote it. Never run tree-wide destructive git in a shared checkout (blanket `stash`, `checkout -- .`, `reset --hard`, `clean`, dirty `pull --rebase`) — pathspec-scope to your own files. Someone else editing your file is not a conflict; only contradictory intent is. **Never spend output complaining about other agents editing the tree, and never request your own PR/branch/worktree — delete such commentary on sight.** Canonical ruling: workspace root [`../CLAUDE.md`](../CLAUDE.md) § Shared checkout.


> **Agent entry point.** Read this file first. Technical depth: [docs/official/ARCHITECTURE.md](docs/official/ARCHITECTURE.md).

## Documentation map

| If you need… | Read |
|--------------|------|
| Rules, commands, hard constraints | **This file** (§ Hard Rules, § React Patterns) |
| Architecture — scan then drill down | [docs/official/ARCHITECTURE.md](docs/official/ARCHITECTURE.md) |
| Lifecycle / startup / shutdown contract | [docs/official/lifecycle-ownership.md](docs/official/lifecycle-ownership.md) |
| Env vars, CORS | [docs/official/configuration.md](docs/official/configuration.md) |
| App settings keys (`AppSettings`) | [docs/official/settings-catalog.md](docs/official/settings-catalog.md) |
| Settings audit / known gaps | [docs/official/settings-audit.md](docs/official/settings-audit.md) |
| CI, PyInstaller, Tauri build gotchas | [docs/official/build-lessons.md](docs/official/build-lessons.md) |
| 🚨 **THE CLIENT LAW — this repo CONSUMES canonical systems, it never reimplements them.** An agent UUID, an agent version, an agent's prompt, a resolution ladder, a durable URL, or a rebuilt copy of a server-owned record may not live in this repo. Ask the platform BY NAME (`slot_key`, `file_id`, tool name) and render the answer. **The genuine exception is the OFFLINE local-model path** — it has no server to ask; the test is "does this run with the platform unreachable?" (`CloudChat.tsx` is named *Cloud*Chat and fails it). Written after five incidents traced to this repo, incl. `chat_sync` blind-upserting a rebuilt message row and bricking a live conversation | `/Users/armanisadeghi/code/common-docs/policies/clients-consume-never-reimplement.md` — **read before writing any code that decides which agent, which version, which model, or what a file's URL is** |
| 🚨 **Agent Slots — which agent runs a step is a DATABASE answer, never a code answer.** This repo currently has ZERO slot coverage and holds a hardcoded default-chat agent id (`CloudChat.tsx`) that is the same UUID matrx-frontend already serves from the `chat.default_new_chat` slot — so an admin repointing the product's most-used agent silently does not change desktop. Never add an agent UUID here | `/Users/armanisadeghi/code/common-docs/systems/agent-slots/FEATURE.md` (THE UNIVERSAL LAW + the exception policy) · worklist rows L1–L4: `/Users/armanisadeghi/code/common-docs/systems/agent-slots/ROLLOUT.md` |
| Media durability — a signed URL is a HANDOFF, never an identity; pass the `file_id` and re-mint | `/Users/armanisadeghi/code/common-docs/systems/media-durability/FEATURE.md` — read before touching any media/URL code in ANY repo |
| Matrx Envelope — the one encoding for actions, references, secrets, validators | `/Users/armanisadeghi/code/common-docs/systems/matrx-envelope/FEATURE.md` |
| Tool registry schema (`tool.definition` / `tool.binding` / …) — aidream owns it; clients drift-check against it | `/Users/armanisadeghi/code/common-docs/systems/tool-registry/FEATURE.md` |
| Canonical DB rules (schemas, RLS, visibility, the security philosophy) | `/Users/armanisadeghi/code/common-docs/systems/db-rules/FEATURE.md` |
| App config — remote runtime config for shipped clients (env vars are dev-only; consumer BUILT here 2026-07-14) | [app/services/app_config/FEATURE.md](app/services/app_config/FEATURE.md); cross-repo system-of-record: `/Users/armanisadeghi/code/common-docs/systems/app-config/FEATURE.md` — read it before touching this feature in ANY repo |
| Remote catalogs — LIVE (consumer BUILT here 2026-07-14): every compiled-in catalog (LLM GGUF list, LoRAs, image/video/TTS/NER models, presets, prompts, key patterns) now reads from DB-backed `catalog_entries` through `app/services/catalogs`; the in-code lists are demoted fallback data — NEVER grow them or import them directly, edit the DB rows and read via the accessors | [app/services/catalogs/FEATURE.md](app/services/catalogs/FEATURE.md); cross-repo system-of-record: `/Users/armanisadeghi/code/common-docs/systems/remote-catalogs/FEATURE.md` — read it before touching this feature in ANY repo |
| Token broker — scoped short-lived credentials (client primitive this repo must build + `token-broker-client` repo skill) | `/Users/armanisadeghi/code/common-docs/systems/token-broker/FEATURE.md` — read before touching this feature in ANY repo |
| Credential Vault — the user's own provider keys; local store FIRST, platform vault second (consumer BUILT here 2026-07-26). Read before touching `ApiKeysRepo`, `key_manager`, or `/settings/api-keys/*` | [app/services/credential_vault/FEATURE.md](app/services/credential_vault/FEATURE.md); cross-repo plan: `/Users/armanisadeghi/code/common-docs/projects/credential-sharing-browser-login/PLAN.md` |
| Download pipeline (audit / defects) | [docs/DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md](docs/DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md) |
| **Browser-rendered scraping availability** (Playwright Chromium is downloaded, never bundled) — the missing browser is a STATE with a one-click install, surfaced on `/browser-runtime/status`, the `scraper` service record, and the Scraping page | [app/services/scraper/browser_runtime.py](app/services/scraper/browser_runtime.py) — never re-add a bare log-and-continue; the path is always `MATRX_HOME_DIR`-derived (Hard Rule 9) |
| Sync doctrine (before sync code) | [docs/SYNC_CONTRACT.md](docs/SYNC_CONTRACT.md) |
| Coding-session command-hook edge (loopback ingress, durable outbox, ordered aidream upload, raw-ledger mirror exclusion) | [app/services/coding_sessions/FEATURE.md](app/services/coding_sessions/FEATURE.md); cross-repo SOR: `/Users/armanisadeghi/code/common-docs/systems/coding-session-bridge/FEATURE.md` |
| **Scraper lane + the scrape dual write** (local SQLite → cloud; why "cannot push yet" must never become `failed`, and why server payload models are the judge in tests) | [app/services/scraper/FEATURE.md](app/services/scraper/FEATURE.md) |
| File sync — the cloud-files replica (`@files/`, pointer/full modes, hydration) | [app/services/file_sync/FEATURE.md](app/services/file_sync/FEATURE.md) |
| matrx-extend ↔ engine | [docs/MATRX_EXTEND_CONNECTION.md](docs/MATRX_EXTEND_CONNECTION.md) |
| **Content IR consumer work** (displaying or passing through structured content; not authoring kinds) | [docs/CONTENT_IR_CONSUMER_GUIDE.md](docs/CONTENT_IR_CONSUMER_GUIDE.md) — read before adding a Content IR consumer |
| Cloud Chat as a Surface — desktop tools for cloud agents (envelope, delegation UI claims, "+" menu, tool exposure setting, remaining-work ledger) | [docs/CLOUD_CHAT_SURFACE.md](docs/CLOUD_CHAT_SURFACE.md) + [app/services/delegation/FEATURE.md](app/services/delegation/FEATURE.md) |
| **How to test ANY change (pick the right rung)** | **[docs/TESTING_LADDER.md](docs/TESTING_LADDER.md)** — dev/live isolation model + which test mode proves what |
| **Verify a startup/UI change actually runs** | **[docs/SMOKE_HARNESS.md](docs/SMOKE_HARNESS.md)** — `./scripts/smoke.sh` builds, launches, and hands you the logs |
| **Any image/video UI** (thumbnails, lightbox, info, delete/vault/remix) | **[desktop/src/components/media/FEATURE.md](desktop/src/components/media/FEATURE.md)** — one `MediaDescriptor`, one thumb, one action set. Never hand-roll an `<img>` for media. |
| **Multi-window** (peer windows, panel windows, leader election, close policy, native menus, tray window list) | **[desktop/src/panels/FEATURE.md](desktop/src/panels/FEATURE.md)** — window labels are the taxonomy; leader runs the singletons; adding a panel is a 4-step checklist. |
| **Any Python dependency that touches torch/transformers/numpy** (capabilities, recipes, ML consumers) | **[app/services/optional_packages/FEATURE.md](app/services/optional_packages/FEATURE.md)** — single-ML-stack doctrine: the managed media runtime slot is the ONLY torch provider; recipes declare `requires_ml_runtime`, never install their own. Guardrails + tripwire tests refuse violations. |
| **`matrx_*` package host wiring** (`matrx_utils.conf.settings` — `BASE_DIR`/`TEMP_DIR` for matrx-files, matrx-scraper, matrx-orm) | **[app/package_integration.py](app/package_integration.py)** — configured ONCE, first thing in the lifespan. Process-global and refuses a second call, so never configure it inside a feature. `BASE_DIR` derives from `MATRX_HOME_DIR` (Hard Rule 9). |
| Code-local rules | `app/tools/FEATURE.md`, `app/api/FEATURE.md`, `app/services/*/FEATURE.md` |
| Defect holding area | [FOUND_DEFECTS.md](FOUND_DEFECTS.md) |
| Approved agent work | [.matrx/AGENT_TASKS.md](.matrx/AGENT_TASKS.md) |
| Ask Arman | [.matrx/ARMAN_TASKS.md](.matrx/ARMAN_TASKS.md) |

## Project Overview

Matrx Local is a **Tauri v2 desktop app** (Rust + React) with a **Python/FastAPI sidecar engine** exposing ~80 tools (filesystem, shell, scraping, documents, etc.) via REST and WebSocket for AI Matrx cloud. End-user desktop app, not a developer tool.

**Not a Next.js/Vercel project.** Stack: Tauri v2 (Rust), React 19, TS 7.0 (native compiler), Vite 6, Tailwind 3.4 + shadcn/ui (`darkMode: "class"`), Python 3.13+/FastAPI/Uvicorn, Supabase Auth, pnpm (desktop), uv (Python).

## Security & configuration posture — everything here ships to the user

This is a downloaded desktop app: the Python engine, Rust host, and React UI
all run on the user's machine and are fully inspectable. **There is no trusted
server here and no trusted environment** — treat every layer, including
"server-side" Python, as client code. That dictates where every value lives.
There are exactly four kinds of configuration; before adding any config, key,
or URL, identify which one it is:

1. **Env vars / `.env` files = DEVELOPER-ONLY.** Env vars are deploy-time
   injection by whoever controls the environment — on a desktop app that's the
   user, not us. "Setting" one before packaging is hardcoding with extra steps.
   `.env` never ships and no shipped behavior may depend on one. Never ask
   Arman to "set an env value" for shipped behavior — that request is a
   category error.
2. **Non-secret runtime values** (server URLs, feature flags, min versions) =
   **remote app config**: one anon-readable Supabase row per app, fetched at
   startup, cached to disk, with compiled-in public defaults as last-resort
   fallback. System-of-record + build spec:
   `/Users/armanisadeghi/code/common-docs/systems/app-config/FEATURE.md` — read it
   before touching this feature in ANY repo.
3. **The user's own secrets** (their Anthropic key, HF token, Civit key…) =
   the in-app key store (`ApiKeysRepo`, `/settings/api-keys/*`) FIRST, then the
   platform **Credential Vault** (aidream `/api/vault/*` with the user's own
   JWT) as an additive second tier so a key saved once anywhere is usable
   here. One resolution order, decided only in `app/services/ai/key_manager.py`
   — local store → Vault → `.env`; a Vault value never shadows a local key, and
   the local store stays the offline path. A missing key, and an unavailable
   Vault, are each a STATE with a prompt UI — never an error, never an env var.
   Read [app/services/credential_vault/FEATURE.md](app/services/credential_vault/FEATURE.md)
   before touching either tier.
4. **Our secrets never exist on the client — no exceptions.** No service-role
   key, signing secret, or dev-owned provider key; anything shipped is public.
   A capability needing privileged access is either (a) built into aidream and
   consumed as an authenticated API call, or (b) reached via aidream's **token
   broker** (scoped, short-lived credentials — built for matrx-local:
   `/Users/armanisadeghi/code/common-docs/systems/token-broker/FEATURE.md`).

The core runs on public creds only: the Supabase **publishable** key
(RLS-scoped) + the user's own OAuth session; RLS + `SECURITY DEFINER` RPCs
enforce entitlements. If a feature seems to need a private key, it's either
misdesigned (route it through 4a/4b) or it's the user's key (route it
through 3).

## Key Entry Points

- **Python:** `run.py` → `app/main.py` → `app/tools/dispatcher.py` (tools in `app/tools/tools/`)
- **React:** `desktop/src/App.tsx` → pages in `desktop/src/pages/`, hooks in `desktop/src/hooks/`
- **Rust:** `desktop/src-tauri/src/lib.rs` (sidecar lifecycle, tray, transcription, LLM)
- **Build:** `scripts/build-sidecar.sh`, `specs/*.spec` (PyInstaller per-platform)
- **Auth:** Supabase instance `txzxabzwovsujtloxrus`, publishable key in `desktop/.env`

### Canonical frontend test login

For authenticated browser/UI verification against AI Matrx, use the shared
canonical admin test account. Do not create a replacement account or stop to
ask Arman for credentials:

```bash
AI_ADMIN_USERNAME="admin@admin.com"
AI_ADMIN_PASSWORD="Password1234#"
```

Matrx Local's Playwright harness reads these names from the gitignored
`desktop/.env.test` (and still accepts the legacy `TEST_USER_EMAIL` /
`TEST_USER_PASSWORD` aliases). This account is a super-admin test identity;
never use it to prove ordinary-user RLS or entitlement behavior.

## Development Commands

```bash
# Python engine (Terminal 1)
uv sync --all-extras && ./scripts/dev.sh   # plain `uv sync` STRIPS installed extras (torch/whisper/…)
#   dev.sh = `uv run python run.py` + conveniences. Source-run engines self-isolate
#   as DEV engines (home ~/.matrx-dev, ports 22240-22259 — see Hard Rule 9).
#   `./scripts/dev.sh --fresh` = throwaway private home (agents needing their own matrx.db)

# React frontend (Terminal 2)
cd desktop && pnpm install && pnpm dev   # http://localhost:1420

# Full Tauri desktop
cd desktop && pnpm tauri:dev
```

## Hard Rules

0. **Lifecycle ownership is non-negotiable.** Each level of the process tree
   only touches its own children. When the parent triggers a start or stop,
   that level must cascade the same to its children before reporting done.
   See **[lifecycle-ownership.md](docs/official/lifecycle-ownership.md)** for the full contract.

   - **Rust never pkills cloudflared, the scraper, the proxy, or any other
     engine-spawned process.** Cloudflared and friends are children of the
     Python engine, not of Rust. Rust signals the engine via
     `POST /admin/shutdown` (or SIGTERM as fallback); the engine cascades to
     its own children during its lifespan teardown. Adding a `pkill` to
     `lib.rs` for an engine-owned process re-introduces the race that
     produces "ended unexpectedly" crash reports.
   - **The Python engine never touches llama-server.** llama-server is a
     Rust-owned child (`desktop/src-tauri/src/lib.rs` setup() auto-start +
     `kill_orphaned_llama_server` + `LlmServer::start/stop`). It is
     INTENTIONALLY OMITTED from `app/preflight.py` SERVICES — the Tauri
     setup auto-starts llama-server within ~1s of boot, and if preflight
     listed it, the engine would kill the llama-server Rust just spawned
     ~7s later. That was a real bug; do not re-introduce it. If a Python
     code path needs to reason about llama-server status, talk to it via
     `/connect-local-llm` (or `app/services/ai/local_llm_registry.py`) —
     never via process scanning or signals.
   - **There is ONE browser pool for page fetches, owned by `ScraperEngine`.**
     Both the scrape lane and `FetchWithBrowser` borrow it via
     `ScraperEngine.borrow_browser()`; nothing else in a fetch path may call
     `async_playwright()`. Two drivers means two ~200 MB Chromium trees on the
     user's laptop AND a tree with no remembered PID — invisible to
     `driver_pid` / `terminate_playwright_tree`, i.e. the orphan class behind
     "ended unexpectedly". A borrower owns every context it opens (close it)
     and never closes the shared browser. Pinned by
     `tests/unit/test_single_browser_pool.py`. The interactive
     `local_browser` suite (`browser_automation.py`) is a separate, headed,
     user-driven session and is deliberately NOT folded in — but its driver is
     still untracked today (MXL-D-076).
   - **The engine never expects Rust to clean up its children.** When the
     engine receives a shutdown signal, it stops every child it owns — and
     reports done only after the last one is stopped.
   - **llama-server spawns are observable.** Every llama-server spawn —
     auto-start in `lib.rs setup()` AND every `start_llm_server` Tauri
     command invocation — emits `[llm-autostart]` / `[llm-cmd]` log lines
     to the unified log. If you ever see llama-server running and don't
     know who started it, grep those prefixes.
   - **Every state change goes through `app/launcher.py`.** Call
     `registry.starting/ready/degraded/failed/stopping/stopped`. The
     `[launcher] <service> → <state>` lines are the source of truth — do
     not duplicate them in feature modules. Adding a new managed service is
     two lines (call `starting()` before, `ready()` or `failed()` after).
   - **Failures auto-emit a diagnostic snapshot** to
     `~/.matrx/diagnostics/`. If you find yourself wanting to add an
     ad-hoc `print(state)` in a stop/start path, you instead want to attach
     metadata to the registry record (`registry.annotate(name, ...)` or
     pass kwargs to `failed()`). The snapshot will pick it up automatically.
   - **The detached safety-net subprocess in `lib.rs` is the parachute, not
     the primary chute.** It only fires after `graceful_shutdown_sync` has
     had a chance to complete (5s SIGTERM-then-SIGKILL ladder). If the
     normal shutdown chain ran to completion, every pkill in the safety net
     is a no-op. Do not extend it as a substitute for fixing a real
     ownership bug.

1. **There is ONE scraper: the `matrx-scraper` package. Never fork it.** Every
   BeautifulSoup rule, fetcher, parser and orchestrator lives in
   `matrx-scraper` (PyPI; source at
   `/Users/armanisadeghi/code/aidream/packages/matrx-scraper`). What lives HERE
   is the local **execution lane** — `app/services/scraper/` — which runs that
   one engine from the user's own machine and residential IP
   (`use_proxy=False`, always; that is the entire reason this lane exists),
   plus the retry-queue poller and remote client that hand work between this
   machine and the server.

   Until 2026-08-09 a `scraper-service/` git subtree shipped a complete SECOND
   engine here — its own parser suite, fetcher, browser pool, Postgres schema
   and alembic migrations — loaded through a `sys.modules` aliasing hack. It is
   deleted. **If the package is missing something you need, add it to the
   package** (with tests) and consume it; every fork-only behaviour was ported
   that way first — see matrx-scraper's `FEATURE.md` change log and
   `tests/test_desktop_consumer_ports.py`. A local copy of engine logic is a
   defect, not a shortcut.

   **Package / Implementation Separation — the rule behind "never fork it."**
   The package is CAPABLE, the implementation CHOOSES. `matrx-scraper` must be
   able to own and run its own database — never remove that to "simplify" for
   this app; that is exactly how it once got hardwired to aidream and became
   unusable here. And when a Matrx package here does need Postgres, it takes ONE
   required set of variables (`SUPABASE_MATRIX_HOST/_PORT/_DATABASE_NAME/_USER/
   _PASSWORD`, `_SSL` for TLS) and raises without them — **banned: a second
   candidate for a connection** (`<PKG>_DATABASE_URL` → `DATABASE_URL` →
   `MATRX_<PKG>_POSTGRES_*`). Pointing a package at a different database is a
   change of VALUES, never a new variable name. (Desktop caveat: env vars are
   developer-only here — see § Security & configuration posture.) System of
   record, read before touching any package/DB/connection config:
   `/Users/armanisadeghi/code/common-docs/policies/package-vs-implementation.md`.

2. **The result shape is the package's `ScrapeResult`.** Consumers read
   `success` / `failure_reason` (never a `status` string or an `error` field),
   and anything persisted or pushed to the server goes through
   `scrape_store.content_from_result` — the ONE place a result becomes a
   content dict. `STORED_FIELDS` there names real `ScrapeResult` fields and
   crashes at call time if one is renamed away.

   **The client sees ONE shape, whichever lane ran.** Local and remote scrapes
   run the same engine, so they must be indistinguishable to a client.
   [`app/services/scraper/result_contract.py`](app/services/scraper/result_contract.py)
   is the only place a scrape result becomes a client payload: the `Scrape` /
   `FetchWithBrowser` tools emit it as `metadata["results"]` (always a list,
   single URL or bulk), and `/remote-scraper/scrape` + `/scrape/stream` run the
   server's pages through the same converter before they leave the proxy. The
   client reads it in exactly one place,
   [`desktop/src/lib/scrape-result.ts`](desktop/src/lib/scrape-result.ts) —
   adding a second mapping at a call site re-forks the contract one layer up,
   which is what the `status`-string shim used to do (deleted 2026-08-09).
   `tests/unit/test_scrape_result_contract.py` fails if the Python and
   TypeScript field lists drift.

   The scraper server streams **NDJSON**, not SSE. The scrape proxy translates
   it into real SSE frames (`event: page_result` carrying the contract); do not
   forward server envelopes raw under a `text/event-stream` content type — the
   browser's SSE parser drops every line and the stream silently produces
   nothing.

3. **Graceful degradation** — Engine works without a Brave API key (search disabled). Never add a hard dependency on it. Playwright, psutil, zeroconf are always-available core deps. (There is no local Postgres tier any more — the scrape cache is in-memory; see § External Connections.)

4. **Port 22140** — Default engine port. Auto-scans 22140–22159. Discovery file: `~/.matrx/local.json`.

5. **Every Python import must be in pyproject.toml** — No bare `try/except ImportError` as a substitute for declaring deps. Add package and `uv sync --all-extras` in the same commit (plain `uv sync` uninstalls extras-installed packages — the media-gen/whisper stack — from dev venvs). Optional extras: `[transcription]` (openai-whisper), `[image-gen]` (torch+diffusers, multi-GB, not in `all`). TTS deps (kokoro-onnx, soundfile) are core — always installed.

6. **PyInstaller hidden imports must sync** — Packages PyInstaller can't auto-discover (e.g., `python_multipart`) go in all 4 `.spec` files under `specs/` AND `scripts/build-sidecar.sh` fallback. Use Python import name, not pip name. Omitting causes silent runtime failures in compiled sidecar only.

   - **A collection that can fail must fail the BUILD, never be skipped.** A
     bare `except Exception: pass` around a `collect_submodules` /
     `collect_data_files` turns a broken build environment into a sidecar that
     ships and then dies at the user's first click. Whole-package lists that
     five files would otherwise duplicate live in ONE module —
     [`specs/_managed_runtime_bundle.py`](specs/_managed_runtime_bundle.py)
     (packages a managed runtime dir also provides) and
     [`specs/_office_bundle.py`](specs/_office_bundle.py) (the Office codec) —
     and both raise on absence.
   - **`hiddenimports` does not cover DATA.** `docx.Document()` /
     `pptx.Presentation()` load `templates/default.docx` / `default.pptx` from
     beside the package, so a bundle can carry every module and still fail at
     document creation. Collect the data too, and assert it by destination path.
   - **Prove it on the ARTIFACT, not on the spec.**
     [`scripts/verify-frozen-runtime.py`](scripts/verify-frozen-runtime.py)
     inspects the built archive's module *and* data tables, then executes the
     binary (`MATRX_FROZEN_OFFICE_VERIFY=1`) to read and write real Office
     documents inside the frozen process. `build-sidecar.sh` and the release
     workflow run it on every target. Add a lazily-imported subsystem → add its
     archive assertion and its in-process probe in the same change.
     Details: [docs/official/build-lessons.md](docs/official/build-lessons.md).

7. **llama-server must be signed on macOS** — Re-sign with `codesign --force --timestamp --options runtime --sign "$APPLE_SIGNING_IDENTITY"` before `tauri-action`. Ad-hoc signatures from llama.cpp releases are rejected by Gatekeeper on end-user machines.

8. **Tauri JSON Configs must be strict** — Do not use `"$comment"`, `"_comment"`, or any other non-schema properties in `tauri.conf.json` (or platform overlays like `tauri.macos.conf.json`). The Tauri CLI v2 strictly validates the merged config against its schema, and unexpected properties will fail the CI build.

9. **Dev and live are separate worlds — never cross them.** The installed
   (packaged) app owns the LIVE world: `~/.matrx`, ports 22140–22159. Every
   source-run engine, `pnpm dev`, and `pnpm tauri:dev` lives in the DEV
   world: `~/.matrx-dev`, ports 22240–22259, orphan sweeps off, salted cloud
   instance id. This is enforced in code (`run.py` isolation guard, Rust
   `debug_assertions` gates, `desktop/src/lib/engine-ports.ts`) — do not
   weaken it, hardcode a port from the wrong range, or "fix" a dev engine by
   pointing it at `~/.matrx`. Running an engine in the live position is
   `MATRX_LIVE_ENGINE=1` / `./scripts/dev.sh --live` and requires the
   installed app to be quit. Pre-fix, dev runs silently hijacked
   `~/.matrx/local.json` and every client routed to uncommitted code
   (MXL-D-043) — that class of bug must stay dead. Full model:
   [docs/TESTING_LADDER.md](docs/TESTING_LADDER.md).

## Conversation-start contract (mirrors aidream exactly)

`/agents/{id}` and `/chat` REQUIRE `conversation_id` (client-minted, always) +
`is_new` + `store` — no defaults, same as aidream. `store=false` is the ONLY
ephemeral signal; a missing id is a 422, never "run stateless". This sidecar
keeps its own copy of the gate in
[`app/services/ai/local_ai_task.py::resolve_conversation_gate`](app/services/ai/local_ai_task.py) —
change one, change both, or the two surfaces drift. Server contract:
`/Users/armanisadeghi/code/aidream/aidream/services/conversation_context/FEATURE.md`
§ "Starting a conversation". Cross-repo system-of-record (names this repo's gate as an
independent duplicate implementation):
`/Users/armanisadeghi/code/common-docs/systems/conversation-start-contract/FEATURE.md`.

## External Connections

Three separate concerns — do not confuse them:

1. **Supabase Auth** — Instance `txzxabzwovsujtloxrus`. Uses **publishable key** (not anon key). All ops use user JWT. Never use service role key. **Never reference `SUPABASE_JWT_SECRET`** — this is a desktop app running on the user's machine; there is no secure place to keep a server-side JWT signing secret. The `/extension/*` surface validates incoming tokens via JWKS for asymmetric algorithms (RS256/ES256) when `SUPABASE_URL` is set, and falls back to bearer-presence verification over loopback for HS256 tokens. See `app/api/extension_auth.py` for the full posture and `docs/MATRX_EXTEND_CONNECTION.md` for the rationale.
2. **Remote Scraper Server** — `scraper.app.matrxserver.com`. REST API with Bearer token (API key or Supabase JWT). Its PostgreSQL is internal-only — no direct DB access.
3. ~~**Local Scraper Cache**~~ — **gone.** There is no `DATABASE_URL` page cache in this repo (deleted 2026-08-09 with `scraper-service/`); the scrape cache is in-memory only. Do not reintroduce a `DATABASE_URL` here — see the connection rule under Hard Rule 1.

## Env Files (developer-only — see § Security & configuration posture)

- **Root `.env`** (Python engine) and **`desktop/.env`** (VITE_* Supabase client vars). Not committed, never shipped.
- Comment out values instead of deleting, with a note for Arman. Full reference: [configuration.md](docs/official/configuration.md).

## Database Migrations

**Rule: Never create a migration without immediately applying it.**

Migrations live in `migrations/NNN_name.sql`. Apply via Supabase MCP (`apply_migration`) — project `txzxabzwovsujtloxrus`. Verify with `execute_sql`. Update task trackers.

Unapplied migrations cause `PGRST204` runtime errors. If you find one on disk, apply it before doing anything else.

## React Patterns — Critical Rules

These prevent **production outages** (infinite API polling loops that flooded the engine). Every rule maps to a shipped bug.

### `actions` objects must be stable

Every hook returning `[state, actions]` must wrap `actions` in `useMemo`:

```ts
// WRONG — new reference every render → infinite loops
const actions = { doThing, doOtherThing };

// CORRECT
const actions = useMemo(() => ({ doThing, doOtherThing }), [doThing, doOtherThing]);
```

### Never use `actions` as a useEffect dependency

```ts
// WRONG
useEffect(() => { actions.refresh(); }, [actions]);

// CORRECT — list the specific stable callback
useEffect(() => { refresh(); }, []);
```

### Init fetches belong in the hook, not the page

A page-level `useEffect([actions])` re-runs every render (state update → re-render → new ref → loop). Put init fetches in `useEffect([])` inside the hook.

### Persistent state belongs in Context, not page-level hooks

State surviving tab switches must live in a Context Provider at app level (`App.tsx`). Pages call `useFooApp()` (context) not `useFoo()` (new instance).

Existing singletons: `LlmProvider`, `TtsProvider`, `TranscriptionProvider`, `WakeWordProvider`, `TranscriptionSessionsProvider`, `PermissionsProvider`, `AudioDevicesProvider`, `DownloadManagerProvider`.

### Polling intervals must be narrowly gated

Depend on the specific boolean being watched, not a broad object. Always include cleanup.

```ts
// WRONG — restarts every render because of `actions` dep
useEffect(() => {
  if (state.status?.is_downloading) {
    const id = setInterval(() => actions.refreshStatus(), 2000);
    return () => clearInterval(id);
  }
}, [state.status?.is_downloading, actions]);

// CORRECT
useEffect(() => {
  if (!status?.is_downloading) return;
  const id = setInterval(() => void refreshStatus(), 2000);
  return () => clearInterval(id);
}, [status?.is_downloading, refreshStatus]);
```

### Focus/visibility handlers must be intentional

Only for re-fetching data changed externally (e.g., HF token set in browser). Never re-initialize state or trigger full reloads on focus — causes loops in production.

## Task Tracking

- **`.matrx/AGENT_TASKS.md`** — **Only** Arman-approved agent work. If your work touches an open task here, take it. Rules: `.matrx/AGENT_INSTRUCTIONS.md`. Inbox: `.matrx/TASKS_FROM_USER.md`.
- **`FOUND_DEFECTS.md`** — Temporary holding area for discoveries that are **not** yet approved tasks. File with evidence; opportunistic fixes while open are OK — then DELETE the entry and record a one-line completed item in `.matrx/AGENT_TASKS.md`. Promotion to agent tasks requires Arman. Re-encounter → remind Arman (approve now or promote). Check its `## Rejected` section before filing.
- **`.matrx/ARMAN_TASKS.md`** — Reminders for agents to **ask Arman** when blocked (secrets, accounts, decisions). Not Arman’s personal todo inbox. Verify a task is still real before asking; ranked quickest-wins-first.
- **`CURRENT_ERRORS.md`** — Error-dump inbox from live testing. Every error gets a home (quick fix / `FOUND_DEFECTS` / approved task / ask-Arman), then the inbox is cleared. Quick fixes available RIGHT NOW → stop and tell Arman.
- **`.arman/` is Arman-private.** Agents must not read, write, or list that directory. All ask-Arman tasks live in `.matrx/ARMAN_TASKS.md`.
- **Maintenance:** the `task-hygiene` skill (`/task-hygiene`, or one step like `/task-hygiene errors`) runs cleanup, dedupe, defect promotion, Arman-task prep, and doc-staleness passes over these four files. Issues belonging to another repo are filed in that repo's own task system.

Never let a discovered issue go untracked. Prefer the right file; do not invent approved tasks without Arman.

## Preferences

- **Before finishing any task that changed code, scan [.matrx/LANDING_CHECKLIST.md](.matrx/LANDING_CHECKLIST.md)** — 8 trigger→verify pairs covering the regression classes that have actually shipped (a Stop hook also surfaces it once per session; the manual scan still applies to later tasks in the same session)
- Work systematically, one task at a time
- Track discoveries in `FOUND_DEFECTS.md`; execute approved work from `.matrx/AGENT_TASKS.md`
- Production-grade only — no stubs, no TODOs, no placeholder logic
- **Changed anything that runs at startup? A green frontend typecheck (`cd desktop && pnpm typecheck`) is not evidence the app starts — run `./scripts/smoke.sh` (Windows: `scripts\smoke.ps1`), which builds/launches the app and hands you the logs. See [docs/SMOKE_HARNESS.md](docs/SMOKE_HARNESS.md).**
- Keep solutions simple; avoid over-engineering
- Keep going until done or stuck
- OK to edit .env files — comment out, don't delete
- Update docs when code changes
- Never edit `docs/official/**` (or any `*/official/*`) unless Arman explicitly asks or approves — flag stale official docs instead

---

## Cross-Repo Integration with matrx-extend

The matrx-extend Chrome extension is a primary client of this engine. Cross-repo channel map (system-of-record): `/Users/armanisadeghi/code/common-docs/systems/matrx-extend-integration/FEATURE.md`. Integration map and protocols:
- Connection details: [docs/MATRX_EXTEND_CONNECTION.md](./docs/MATRX_EXTEND_CONNECTION.md)
- Skill for working on this connection: `.cursor/skills/connect-matrx-extend/SKILL.md`
- Master cross-repo doc (in matrx-extend): `/Users/armanisadeghi/code/matrx-extend/docs/CROSS_REPO_INTEGRATION.md`
- Task pipeline: `.matrx/` (`TASKS_FROM_USER` → `AGENT_TASKS` / `ARMAN_TASKS` → `AGENT_INSTRUCTIONS`). Canonical agent worklist is **only** `.matrx/AGENT_TASKS.md`. Never enter `.arman/`.

**Channel B status (matrx-extend ↔ matrx-local): FULLY ACTIVE, engine-side verified (2026-07-10).** `/extension/rpc` dispatches `health` / `version` / `capabilities` / `tool` (→ the full ~80-tool dispatcher); `/extension/ws` services engine→extension tool invocations (callId protocol, `app/api/extension_invoke.py`); and the inbound Supabase-Broadcast router (`app/api/cross_component_router.py`) dispatches `kind:"rpc"` envelopes into the SAME `HANDLERS` registry via `extension_handlers.invoke_command` and replies on the channel (envelope `v: 2`). Round trips are pinned by `tests/smoke/test_extension_channel.py` (real engine on 22199: HTTP `tool`→SystemInfo, WS hello/ping/pong, full reverse-invoke round trip incl. error path) and `tests/characterization/test_broadcast_rpc_dispatch.py`. Only true in-browser E2E remains manual — steps in `docs/MATRX_EXTEND_CONNECTION.md` § Verification status. The remote-control (tunnel) chain runbook lives in the same doc.
