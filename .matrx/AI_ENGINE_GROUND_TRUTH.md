# MATRX-LOCAL × MATRX-AI: THE GROUND TRUTH

*Synthesized from six independent investigations plus adversarial verification. Every claim below carries file:line or a live query. Where a skeptic could not confirm a claim, it is labelled UNPROVEN and stated as such rather than dropped.*

---

## 1. THE ONE-PARAGRAPH TRUTH

**matrx-ai was split in half and only one half was built.** The "client host" seam set was designed as six seams; three of them (`api_key_resolver`, `conversation_store`, `model_catalog`) are real, consumed on the hot path, and tested — the chat/execution loop genuinely runs with zero database. The other three (`get_jwt`, `server_url`, `source_app`) are **write-only**: `matrx_ai/__init__.py:164-166` stashes them in the `_ext` registry, `client_host/validate.py:81-102` validates them, and **nothing in the package ever reads them** — there is no `get_ext("get_jwt")`, no `get_ext("server_url")`, no `get_ext("source_app")` anywhere in matrx_ai, and no HTTP client in the package ever builds a request to an aidream base URL. The "server-backed features (tool registry fetch, authenticated reads)" that the docstrings, the validator's *error message*, and matrx-local's own startup ERROR log all promise **do not exist as code**. Everything downstream follows from that single fact: the tool registry has only an ORM loader, so on desktop it loads 0 rows (`registry.py:592` → `DBNotConfiguredError` swallowed at `:594-603`); matrx-local compensates by synthesizing 108 tool definitions in-process with a `source_kind` (`matrx_local`) that exists in neither the DB nor matrx-ai's recognized set; matrx-local monkeypatches `queue_helpers.get_coordinator` (`engine.py:229-258`) because matrx-ai's own WriteCoordinator ignores the store seam it documents; and conversations persist to SQLite forever because no push pipeline was ever written — even though **RLS already permits the desktop to write `chat.conversation`/`chat.message` directly with the user's JWT**. The root cause is not drift in the sense of "it used to work and rotted." It is **a half-built abstraction that was documented as if complete**, and then trusted — by agents, by the validator, by matrx-local's own error messages — as a guarantee. The docs are the load-bearing failure.

---

## 2. HIS FIVE CLAIMS

### (1) "matrx-ai needs server DB access it can't have locally; fix = API calls at startup + a local replica for offline."
**PARTLY TRUE — right diagnosis, half-wrong prescription, and the prescription is already half-built.**

*Wrong part:* matrx-ai does **not** need DB access for the core execution path. `db/persistence.py:441-455`, `conversation_gate.py:620-632/830-836/862-864/1194-1196`, `tools/logger.py:230/404/788-790/904`, and `agents/resolver.py:41-49` all check `get_conversation_store()` **first** and return before touching the ORM. `catalog/host_catalog.py:82` + `db/ai_models/ai_model_manager.py:66-85` + `catalog/resolve.py:150-159` fully replace the ORM model manager. `config/usage_config.py:343-352` even warms pricing off the host catalog. The adversarial pass **refuted** the sweeping "no REST alternative exists for ANY of them" claim: the catalog, the persistence coordinator/replay, and the tool logger *all* have implemented client-host alternatives that matrx-local uses today.

*Right part:* there IS a genuine ORM-only residue with **no** client path — `db/_agx_manager_impl.py:9-16` (module-scope `get_base` → raises at import), `db/_cx_managers_impl.py`, `db/_arm_managers_impl.py`, `db/voice_catalog.py:32`, `db/content_types/*`, `instructions/matrx_fetcher.py:25,153`, `ops/issue_capture.py:93`, `skills/providers.py:209,223`, `tools/tool_binding_db.py:45`, `tools/tool_executor_db.py:47`, `memory/observational_memory.py:33`, and the notes/tasks/picklists/datasets/dictionary tool implementations. Those are agx agent definitions, skills, tool bindings/executors, observational memory — all dead on desktop.

*And two unguarded holes in the "zero-DB" contract that make the docstring a lie:* `persistence/queue_helpers.py:99-164` `get_coordinator()` has **no store short-circuit** — with a `RequestLane` open (and matrx-connect always opens one: `matrx_connect/streaming/response.py:148,205`), it constructs a Coordinator and calls `_ensure_cx_registered()` (`:162` → `:74` import → `_cx_managers_impl.py:12` → `db/_registry.py:88 raise DBNotConfiguredError`). The exact unguarded reach sites are **`tools/executor.py:1076-1078` (every tool completion), `:863-865`, `:1379-1381`**. And `conversation_gate.py:462` `create_new_conversation` / `:794` `verify_existing_conversation` have no store guard either (latent — no matrx-local caller today).

*On "API calls at startup + local replica":* that is **exactly what matrx-local already does** — `app/services/local_db/sync_engine.py:156/268/379` pulls models/agents/tools from the aidream REST API into SQLite at boot and every 600s, and it works (live: `GET /api/ai-models` → 200). So the prescription is correct and already partially shipped. What's missing is (a) the residue above, (b) agent *messages* (see claim 4/5).

### (2) "matrx-ai has drifted and mishandles client mode; matrx-local devs must fix it directly in the package."
**CONFIRMED on the diagnosis. The "fix it in the package" instinct is right, and the proof is that matrx-local is *already* patching the package from outside.**

`app/services/ai/engine.py:229-297` `install_client_host_coordinator_guard()` reassigns three matrx-ai internals at startup: `matrx_ai.persistence.queue_helpers.get_coordinator` (`:258`), `matrx_ai.tools.dynamic_drain.drain_pending_injections` (`:277`), `.drain_pending` (`:290`). A desktop app is monkeypatching a library's private module layout to make the library's own documented contract (`matrx_ai/__init__.py:88-93`: *"any DBNotConfiguredError raised after a configure() like the above is a matrx-ai packaging bug"*) true. That is the definition of a mishandled client mode. One upstream rename of `queue_helpers.get_coordinator` silently un-patches it and every `/ai` request throws mid-stream.

The three dead seams (§1) are the other half. And four provider call sites bypass `resolve_api_key()` entirely: `agent_runners/conversation_labeler.py:199`, `processing/audio/groq_transcription.py:146`, `evaluators/ai_judge.py:129`, `graph_nodes/image_pipeline_actions.py:64-67`. **Correction to the raw finding:** the skeptic refuted "these are broken today" — matrx-local mirrors keys into `os.environ` (`key_manager.py:102-107`), so they resolve fine *right now*. They become broken the day the shim is deleted, which `key_manager.py:18-26` schedules for **after 2026-09-01**. Of the four, only `conversation_labeler` is on a live matrx-local path (`local_ai_task.py:554` → `executor.py:1785` → `agents/services/conversation_labeler.py:190` → the bypass); the other three are latent.

### (3) "aidream packages hard-couple to aidream and break standalone."
**CONFIRMED — and the CI that supposedly proves otherwise is structurally blind.**

The core import surface is clean: `import matrx_ai` and all 16 client-host modules import fine in matrx-local's real venv (matrx-ai 0.3.3 from PyPI, no aidream present). But:

- **`matrx_ai/tools/implementations/browser.py:30`** — module-scope, unguarded: `from matrx_scraper.ai_browser import BrowserClientError, RemoteBrowserClient`. matrx-scraper is **not** a declared dependency (`packages/matrx-ai/pyproject.toml:67-73` lists only matrx-utils/connect/graph/runtime). `implementations/__init__.py:2` eagerly imports it, so **the entire built-in tool-implementations package is unimportable on a client host** — verified live: `ModuleNotFoundError: No module named 'matrx_scraper'`. Every leaf (`.web`, `.filesystem`) fails too, because Python runs the parent `__init__` first. It surfaces at *dispatch* time via `registry.py:980 importlib.import_module`, not at boot. (Skeptic's correction: it does **not** poison `import matrx_ai` itself.)
- **`matrx_ai/providers/eleven_labs/tts_streaming.py:38-44`** — `from initialize_systems import initialize` at module scope, and it **calls `initialize()` at import time**. Dead on arrival outside aidream.
- **`matrx_ai/skills/providers.py:214,225`** — the skeptic **upgraded** this: those `from db.managers...` lines are the *body* of the `except` handler that catches `get_instance()` failure. On a client host `get_instance("skl_definitions_manager")` raises `DBNotConfiguredError` → enters the except → `ModuleNotFoundError` **propagates uncaught** through `providers.py:247,306,330,373,397` into `skill.py:191`. The skill tool **hard-crashes** on a client host; it does not degrade.
- **matrx-orm is imported in 4 files at module top-level** (`instructions/content_blocks_manager.py:5` — pulled in eagerly by `instructions/__init__.py:1`; `persistence/replay.py:37-40`; `db/persistence.py:29`; `db/ai_models/_ai_model_dto_impl.py:5`) yet **is not declared in matrx-ai's pyproject**. `pip install matrx-ai` works today only because matrx-runtime (`packages/matrx-runtime/pyproject.toml:31`) transitively pulls `matrx-orm>=3.1`.
- **CI cannot see any of this.** `.github/workflows/test.yml:189` runs the suite via `uv run pytest` from the aidream workspace root, where `.venv/.../aidream.pth` puts the repo root on `sys.path` and matrx_scraper is editable-installed. `tests/client_host/test_import_surface.py:60-67` spawns `sys.executable` with a scrubbed **env** — not a scrubbed **sys.path** — so even adding the three broken modules to `_CLIENT_HOST_IMPORTS` (`:22-44`) would still pass green.
- 13 grandfathered violations sit in `scripts/package_boundaries_baseline.json` while `packages/matrx-ai/CLAUDE.md:95,154` claims *"Zero known violations"*.

### (4) "Local tool names/structure/DB schema are archaic (months behind)."
**REFUTED as stated — plainly wrong. But there is a real, dangerous problem underneath it.**

The catalog is **108 tools**; the cloud has **exactly 108 `local_*` rows** in `tool.definition`, **all 108 bound-active** to executor `matrx-local`, and the repo's own live drift check (`uv run python -m app.tools.tool_sync status` against `https://server.app.matrxserver.com/ai-tools/app/matrx-local/all`) reports *"108 in sync … no drift detected"* — including parameter JSON Schemas byte-for-byte. Names and schemas are **not** months behind.

What is actually true, and worse than "archaic":

- **The tables `tool_def` / `tool_binding` do not exist.** Live `pg_class` scan: the only `%tool%` relations outside schema `tool` are `chat.tool_call` and `chat.tool_trace`. The real tables are `tool.definition` / `tool.binding` / `tool.executor`. Yet `/Users/armanisadeghi/code/CLAUDE.md:77` and matrx-ai's own `tools/registry.py:70,83,622,668` and `merge.py:659` all name the nonexistent ones. **Prior agents queried a table that doesn't exist.**
- **A latent bomb in `source_kind`.** All 108 cloud rows carry `source_kind='native'` (live: `select distinct source_kind from tool.definition` → *only* `'native'`). `registry.py:839-840` maps that → `ToolType.LOCAL`, and `function_path` is `''` for every row (`registry.py:826`; its own docstring at `:820-824` admits the DB no longer carries it). matrx-local instead synthesizes the *same 108 names* as `ToolType.EXTERNAL_HANDLER` with `source_kind="matrx_local"` (`local_tool_bridge.py:481-482`). This works **only** because the DB load returns 0 rows and the backfill wins (`engine.py:399`). **The moment matrx-ai gains a REST-backed registry load — the very thing the `server_url`/`get_jwt` seams were for — the DB definitions win, become `ToolType.LOCAL` with empty `function_path`, and `executor.py:765-790` rejects every call with `no_viable_executor`. All 108 local tools break at once.** `client_tools` (the only escape, `executor.py:533`) has zero references in matrx-local.
- **`_bindings_by_tool` is never populated on desktop**, so `resolve_executor_binding()` returns `"server"` for every tool (`registry.py:569-571`) — and this runs on **every request** (`local_ai_task.py:206` → `merge.py:535`).
- **`FALLBACK_CLOUD_BASELINE` is stale**: 49 names vs 108 live (`tool_sync.py:84-104`). If the aidream route is down, `emit-changeset` would generate **59 duplicate INSERTs** into `tool.definition` (`tool_sync.py:216-231` can't detect collisions because `all_definition_names` is None in fallback; `:334-357` renders raw INSERTs). Live foot-gun.
- Category drift: 49 cloud rows are flat `'local'` while 51 are fine-grained — caused by `_new_tool_sql:316-324` writing the fine-grained category while `_changed_tool_sql:344-351` only ever repairs `parameters`.

**Tell Arman plainly: the tool catalog is in sync. The thing to fear is the `source_kind` collision, not staleness.**

### (5) "Cloud↔local DB sync has never actually worked; AI call structures don't match."
**SPLIT VERDICT. Sync: PARTLY CONFIRMED (three subsystems, one is genuinely dead). AI call structures: REFUTED.**

*"Sync" is three unrelated subsystems, not one:*
1. **Catalog pull (local_db/sync_engine.py)** — **WORKS.** Wired at `app/main.py:516-527`, tested (`tests/characterization/test_local_db_sync_characterization.py`), pulls models/agents/tools from aidream REST. Pull-only.
2. **Settings/instance sync (cloud_sync/settings_sync.py)** — **WORKS**, including *push*. `settings_sync.py:398-417` upserts `public.app_settings`; every column verified against live Supabase.
3. **Notes/documents sync (app/services/documents/)** — **HALF-BROKEN, exactly as he suspects.** `notes` and `note_folders` were repointed to the `workbench` schema and match live. But **`note_versions`, `note_shares`, `note_devices`, `note_directory_mappings`, `note_sync_log` are addressed as `public.*` with no `schema=` arg** (`supabase_client.py:366,392,453,512,548`) — and live `information_schema` shows all five exist **only in the `graveyard` schema**. Every call 404s. GETs are swallowed into `[]` (`supabase_client.py:133-134`); POSTs raise and are caught (`sync_engine.py:245-247` `except Exception: logger.debug("Version snapshot failed (non-critical)")`, `:289 except Exception: pass`, `:734-736 return {}`). **Cloud version history, sharing, device registration, directory mappings and the sync audit log have silently never worked against the current cloud.** The module docstring `supabase_client.py:10-12` *asserts these tables live in `public`* — that docstring is why the missing `schema=` looks intentional.

*Conversations:* **never-worked-stub, confirmed.** `sync_queue` + `SyncMetaRepo.enqueue()` (`repositories.py:496-522`) have **zero callers repo-wide**. `conversations.server_conversation_id` (`schema.py:55`) is inserted as literal `None` (`conversation_handler.py:92`) — a hook with no writer. Desktop conversations are invisible on web, forever. Worse, the skeptic found the UI persists chat to **localStorage** (`desktop/src/hooks/use-chat.ts:122,132`), not to the SQLite store the engine writes — desktop conversation data is fragmented across two disjoint local stores, and neither reaches the cloud.

*"AI call structures don't match" — REFUTED.* `app/api/ai_routes.py:81-82` imports **matrx-ai's own** `LLMParams` and `ToolSpec`. `LocalChatRequest` (`ai_routes.py:136-160`) is a field-for-field subset of aidream's `ChatRequest` (`aidream/api/routers/chat.py:34-90`); both stream through the identical `matrx_connect` `StreamEmitter`. 9/9 AI smoke tests pass including two against a live engine on :22199 asserting `client_mode is True`. **One real divergence:** aidream's `ChatRequest(ScopedRequest, LLMParams)` applies top-level `temperature`/`max_tokens` via a `model_dump()` sweep (`aidream/services/ai_execution/chat_run.py:120-126`); `LocalChatRequest` doesn't inherit `LLMParams` and sets `extra="allow"` (`ai_routes.py:139`), so those fields are **silently swallowed** — a straight violation of "no silent defaults." Only `config_overrides` is read (`local_ai_task.py:529-530`).

---

## 3. THE PRIVILEGE ANSWER

For each capability matrx-ai gets from server privileges, here is the decided client-side answer. Live RLS was measured as role `authenticated`.

| Capability | Verdict | Why (evidence) |
|---|---|---|
| **Tool definitions / bindings** | **Direct-Supabase-under-RLS** | Live as `authenticated`: `tool.definition` **347 rows visible**, `tool.binding` **336**. Policy `tool.definition/cfg_select` allows anon+authenticated on `visibility=public OR created_by=uid OR org`. PostgREST exposes schema `tool` (`pgrst.db_schemas`). No broker needed. Today matrx-local gets these via aidream REST — an extra hop, not a privilege problem; keep it or go direct, but **do not** build a JWT-backed ORM path. |
| **Model catalog (`ai.model_definition`, `ai.provider`)** | **Direct-Supabase-under-RLS + local replica** | 221 models, 29 providers visible to `authenticated`. Already replicated to SQLite by `sync_engine.py:156`. Keep the replica for offline; it is the right shape. |
| **`ai.endpoint` (13 rows)** | **MUST BROKER through aidream** | The one genuine exception. Live: 13/13 rows have `created_by IS NULL` and `visibility != 'public'`. `pub_read` (anon) requires `visibility='public'` → 0. `std_select` (authenticated) requires `created_by = auth.uid()` OR `is_super_admin()` → 0. Grants are present, so clients get **200 + empty array, not an error** — silent-empty. Worse: `ai.resolve_model_config()` is SECURITY **INVOKER** and still *returns a plausible-looking degraded config* when the endpoint join yields nothing. Read endpoints via `GET /api/ai-models` — or fix the data (`visibility='public'` on those 13 rows), which is a one-migration fix. |
| **Provider API keys / LLM inference** | **Two valid answers; pick per-user, don't hardcode one.** (a) *BYO-key local*: what ships today (`key_manager.py`, `resolve_api_key` seam) — works, keeps latency low, but requires every key on the user's disk. (b) **Broker through aidream — and this already exists and is unused.** `POST /api/ai/chat` (+ `/api/v2/ai/chat`, `/ai/agents/*`, `/ai/conversations/*`) is mounted with `Depends(require_guest_or_above)` (`aidream/api/app.py:954-986`; `routers/chat.py:250-257`), executes with **server-held keys**, and emits the **same matrx_connect NDJSON vocabulary** matrx-local's local `/ai` already emits. Live probe: 401 without a JWT. **A desktop user could run AI with zero local keys today.** |
| **Conversation persistence** | **Direct-Supabase-under-RLS** (not a broker, not local-only) | Live `pg_policies`: `chat.conversation` `std_insert WITH CHECK (created_by = auth.uid() …)`, plus std_select/update/delete; `chat.message` `std_insert/update/delete WITH CHECK iam.has_access('conversation', conversation_id, 'editor')`. **The DB already permits the desktop to write its own conversations with the user's JWT.** The "local-only" gap is unbuilt work, not a blocked capability. |
| **Notes / folders** | **Direct-Supabase-under-RLS** — already working (`workbench` schema via Accept-Profile, `supabase_client.py:216,256`). |
| **Note versions/shares/devices/mappings/sync-log** | **Product decision required — the cloud tables are in `graveyard`.** Either repoint to a canonical table (none exists in `public`/`workbench` today) or delete the cloud code path and declare version history local-only (SQLite `note_versions`, migration V7 — which **does** work: `document_routes.py:331-340`). |
| **Private file bytes / URL signing** | **Broker through aidream** — `/api/assets/*` exists and is authed (`matrx-utils/.../router_assets.py:94,130,223`; live 401). The desktop must never hold S3 creds. |
| **agx agent definitions, skills, tool bindings/executors, observational memory, ops issue ledger, notes/tasks/picklists/datasets tools** | **MUST-STAY-SERVER-SIDE for now** (`db/_agx_manager_impl.py:9-16`, `skills/providers.py:209`, `tools/tool_binding_db.py:45`, `tools/tool_executor_db.py:47`, `memory/observational_memory.py:33`, `db/content_types/*`). No client path exists for any of them. Either broker each, or **fail loudly at `configure()`** instead of mid-request. |
| **Agent system prompts (messages)** | **Broker — requires an aidream change.** Not a privilege problem, an *API gap*: `agents_listing.py:35-44` `AgentListItem` = `{id,name,description,category,tags,type,variables}` — no `messages`, **no `settings`, no `model_id`**, and `variables` is hardcoded `[]` (`:63`). The admin-gated `AgentDetail` (`services/agent_service/models.py:205-226`) has no `messages` field either. So cloud agents on desktop run with **no system prompt** (`local_ai_task.py:328-352` emits `agent_prompt_unavailable_locally`) — *and*, worse than documented, with **no model and no tools**, so `local_ai_task.py:302-314` raises 422 `agent_model_missing` unless the caller supplies `config_overrides.model`. |
| **Short-lived scoped credentials** | **The primitive EXISTS — do not build from scratch.** The original finding said "does not exist"; the skeptic **refuted** it. `POST /compute-targets/resolve` (`aidream/api/app.py:1011-1016`, `routers/compute_targets.py:204-225`) is production, user-JWT-authed, ownership-checked (`services/sandboxes/compute_targets.py:43-49`), and mints a short-lived scoped bearer token from the sandbox orchestrator (`:67-87`). It even has a 401-driven re-mint seam already wired into matrx-ai (`package_integration.py:734-740 sandbox_token_minter`). It is scoped to *sandboxes*, not to LLM providers — but extending it is an extension, not a green field. |
| **Per-user secrets vault** | **Exists server-side and is ignored.** `/api/user-secrets/*` (`aidream/api/app.py:1063-1081`, `routers/user_secrets.py:45-195`). There is a sanctioned home for the user's provider keys that is not the user's disk. |

**The spine:** *data* goes direct to Supabase under the user's JWT (tools, models, conversations, notes). *Work* goes to aidream (inference-without-keys, file bytes, agent definitions, endpoint config). *Offline* is served by the SQLite replica, which already exists and works. **Nothing needs service-role on the desktop, and nothing should.**

---

## 4. THE WORK

Ordered. Dependencies are real — the order is not preference.

> **Server-risk rule for every matrx-ai change below:** 319 files outside `packages/matrx-ai` import from it (heaviest: `tools.models` ×37, `db` ×26, `capabilities` ×21, `context.app_context` ×18, `tools.specs` ×17). aidream injects ~40 named ext values through **two** `matrx_ai.configure()` calls (`aidream/package_integration.py:497-770`), and the two-phase ordering exists because DB models must be registered **before** importing submodules that resolve models at module scope. **Every fix must be additive** — new `_ext` keys, lazy imports, new optional code paths. `_ext._registry` is a plain dict with `.get()` defaults, so *adding* keys cannot break the server. **Never rename or move a module path. Never move a `get_model()` call to import time.**

---

**W0 — Kill the doc lies. (PREREQUISITE for everything; 1 day)**
Repos: aidream, matrx-local, workspace root. Fix `/Users/armanisadeghi/code/CLAUDE.md:77` (`tool_def`→`tool.definition`), `matrx_ai/tools/registry.py:70,83,622,668`, `merge.py:659`, `packages/matrx-ai/CLAUDE.md:95,154` ("zero violations" → the 13 grandfathered), `matrx_ai/__init__.py:88-93,131-139`, `client_host/validate.py:89-92`, `app/services/documents/supabase_client.py:10-12`, `app/tools/tool_sync.py:27,81`, `matrx-local/.env:87-101`. **Why first:** every subsequent workstream will be executed by someone (human or agent) who reads these files. Server risk: **zero**.

**W1 — Fix the matrx-ai client-host contract at the source. (PREREQUISITE for release; ~1 week)**
Repo: aidream/packages/matrx-ai.
- `persistence/queue_helpers.py:99-164` — `get_coordinator()` returns `None` immediately when `client_host.get_conversation_store()` is set. This kills the unguarded reaches at `tools/executor.py:1076-1078, 863-865, 1379-1381`.
- `db/conversation_gate.py:462, :794` — add the same store-first guard the four siblings already have.
- `tools/dynamic_drain` — store-first dispatch.
- Then **delete `matrx-local/app/services/ai/engine.py:229-297`.** A desktop app must not monkeypatch a library.
- Replace the 4 `os.environ` bypasses (`agent_runners/conversation_labeler.py:199`, `processing/audio/groq_transcription.py:146`, `evaluators/ai_judge.py:129`, `graph_nodes/image_pipeline_actions.py:64-67`) with `resolve_api_key()`.
- Reconcile the `source_app` key-name mismatch: `configure(source_app=…)` writes `"source_app"`; `agents/source_tracking.py:20-21` reads `"default_child_source_app"` (which aidream sets at `package_integration.py:658` and matrx-local never does — so **every matrx-local child agent is currently stamped `"matrx-ai"`**).
**Server risk: LOW-MEDIUM.** The coordinator short-circuit only fires when a `conversation_store` is configured — aidream configures none, so the server path is untouched by construction. Verify with the existing 2385-test suite.

**W2 — Make matrx-ai importable standalone. (PREREQUISITE for release; 2-3 days)**
Repo: aidream/packages/matrx-ai.
- `tools/implementations/browser.py:30` → lazy the `matrx_scraper` import inside the tool functions (`web.py:51` already does this), and drop the eager `from .browser import …` in `implementations/__init__.py:2`. **This alone restores every built-in matrx-ai tool on desktop.**
- `providers/eleven_labs/tts_streaming.py:38-44` → lazy or delete.
- `skills/providers.py:214,225` → the `except` handler must not raise `ModuleNotFoundError` uncaught; today the skill tool hard-crashes on any client host.
- Declare `matrx-orm` in `packages/matrx-ai/pyproject.toml` **or** make the 4 module-scope imports lazy (`instructions/content_blocks_manager.py:5`, `persistence/replay.py:37-40`, `db/persistence.py:29`, `db/ai_models/_ai_model_dto_impl.py:5`).
- **Add the CI job that would have caught all of this:** `uv venv /tmp/clean && uv pip install ./packages/matrx-ai && python -c "import matrx_ai, matrx_ai.tools.implementations, matrx_ai.providers.eleven_labs.tts_streaming"`. The existing `tests/client_host/test_import_surface.py:60-67` **cannot** catch it — it spawns `sys.executable` inside the aidream venv where `aidream.pth` and matrx_scraper are installed. (Also: `tests/vfs/test_package_isolation.py:9` hardcodes `/home/user/matrx-ai/...`, a path that exists nowhere, and passes vacuously.)
**Server risk: LOW.** Lazy imports are additive. Declaring a dep you already transitively have is a no-op.

**W3 — Decide the `server_url`/`get_jwt` seams: build or delete. (PREREQUISITE for release — the current state is a lie either way; 1 day to delete, ~1-2 weeks to build)**
This is the fork in the road and Arman must choose.
- **Delete path:** remove `get_jwt`/`server_url` from `configure()` (`matrx_ai/__init__.py:164-165`), from `client_host/validate.py:81-98`, and from `matrx-local/app/services/ai/engine.py:194-215` — including the **ERROR log at `engine.py:195-200`** that screams about a missing env var for a capability that does not exist. Cheapest honest move.
- **Build path:** `matrx_ai/client_host/remote.py` — httpx + `get_ext("server_url")` + `Bearer get_ext("get_jwt")()`. **⚠ If you build this and wire it to the tool registry, you MUST fix W4 first or all 108 local tools break the day it lands.**

**W4 — Defuse the `source_kind` bomb. (PREREQUISITE if and only if W3-build happens; otherwise HIGH priority anyway; 2-3 days)**
The correct fix is the platform primitive, not the patch: **matrx-ai's registry should treat a tool bound to a client executor (`tool.binding.executor_name='matrx-local'`) as `EXTERNAL_HANDLER` on the client side**, instead of keying purely on `source_kind`. Routing already lives in `resolve_executor_binding` (`registry.py:550-585`); `source_kind` is doing double duty and shouldn't. The cheap alternative is to have matrx-local's backfill *rewrite* server-provided `local_*` defs rather than skip them (`engine.py:399`). Also populate `_bindings_by_tool` on desktop (cache `tool.binding` rows into SQLite via `sync_engine`), because `resolve_executor_binding()` currently answers `"server"` for every tool on **every request** (`local_ai_task.py:206` → `merge.py:535`).

**W5 — Conversation push to the cloud. (Release-blocking for "cross-surface continuity"; ~1 week)**
Repo: matrx-local. RLS **already permits it** (`chat.conversation std_insert`, `chat.message std_insert`). Extend the proven PostgREST pattern from `app/services/documents/supabase_client.py:107-122` (Accept-Profile) into `app/services/ai/conversation_handler.py`, keyed on the existing-but-never-written `conversations.server_conversation_id` (`schema.py:55`, inserted as `None` at `conversation_handler.py:92`). **Fix the split-brain first:** `desktop/src/hooks/use-chat.ts:122,132` persists to localStorage, not to the SQLite store the engine writes. Two local stores, neither syncing. Either build the outbox on `sync_queue` (`repositories.py:496-522`, zero callers today) or delete that table — it currently makes the system *look* bidirectional when it is strictly one-way.

**W6 — Notes/documents: repoint or retire the five graveyard tables. (Release-blocking; 3-5 days)**
Repo: matrx-local + a Supabase product decision. `supabase_client.py:366,392,453,512,548` — five tables addressed as `public.*` that live only in `graveyard`. And **delete the swallows** that hid this for months: `sync_engine.py:245-247`, `:289`, `:734-736`, `document_routes.py:1128-1130`, `:1153` (fire-and-forget). Also fix `desktop/src/hooks/use-realtime-sync.ts:72-75`, which subscribes to `schema:"public", table:"notes"` while the data lives in `workbench.notes`. And the folder-id bug the skeptic surfaced: `document_routes.py:193-194` computes a `uuid5`-of-name local id, but `supabase_client.py:160-166` inserts a fresh `uuid4()` and discards it — so every subsequent cloud `PATCH`/`DELETE ?id=eq.<uuid5>` silently no-ops.

**W7 — The `/ai` surface honesty pass. (Release-blocking; 2-3 days)**
Repo: matrx-local.
- `ai_routes.py:136-141` — make `LocalChatRequest` inherit `LLMParams` (as aidream does) or drop `extra="allow"`. Today top-level `temperature` is silently dropped.
- Pre-flight the model against the catalog in `local_ai_task.py:499-512` and raise a 422 `model_not_in_local_cache` pointing at `/chat/sync/trigger`, mirroring the existing `agent_model_missing` 422 at `:306-316`. Today an unsynced user gets a raw `ValueError: resolve_call_profile: unknown model '<id>'` (`matrx_ai/catalog/resolve.py:143`) as a `fatal_error` stream event.
- Fix `/chat/ai-status` (`chat_routes.py:402-421`): it hard-codes its own 7-provider `PROVIDER_ENV_VARS` and reads `os.environ` **only**, while `key_manager.py:47-62` `PROVIDER_ENV_MAP` has 11. It works today *solely* because of the `_inject` shim it is scheduled to outlive. Delete the shim per `key_manager.py:18-26` and `ChatPanel.tsx:59-69` will tell every user "no AI providers configured" while AI keeps working.
- Delete `MATRX_AI_CLIENT_MODE` from `.env:101` and `setup_routes.py:1620` — it is read by nothing; the real switch is `engine.py:219 _client_mode_active = True`, unconditional.

**W8 — Secret storage: stop the silent downgrade. (Release-blocking; 1-2 days)**
`app/services/local_db/secret_store.py:70-83` — **any** keychain exception → `_fernet_unavailable` → `protect()` returns plaintext (`:91-92`) → `repositories.py:1276-1279` base64-encodes it. On a headless/locked-keychain machine every provider key is trivially reversible base64 in `~/.matrx/matrx.db`, and the encrypt-failure path logs at **DEBUG only** (`:97-99`). Auth tokens are worse: `repositories.py:562-576` stores them as **literal plaintext** on the fallback. `secret_store.py:15-19` codifies this as an intentional "Fail-safe contract" — which directly contradicts the workspace doctrine "never silently fall back." Surface `_fernet_unavailable` through `/health` and the settings UI; refuse to persist a **new** provider key when the keychain is unavailable. *(Note the skeptic's correction: shell tools do **not** inherit the keys — `app/tools/tools/execution.py:70-89` scrubs `API_KEY`/`TOKEN`/`SECRET` from spawned shell envs. The engine's own process env does hold them while the shim lives.)*

**W9 — The provider boundary is untested. (Release-blocking; 1 day)**
9/9 AI smoke tests pass — and **every one routes through `"endpoints": ["mock_chat"]`** (`test_ai_client_host.py:49`, `test_ai_surface.py:50`). **No test ever makes a real provider network call.** The one layer a desktop user actually depends on is the only unproven link. Add one opt-in test gated on `skipif(not os.getenv("OPENAI_API_KEY"))`. Also: the **strongest "it works" story in the whole stack — the fully-local, key-free llama-server path** (`local_llm_registry.py:56-72,98-107,137-145`; runtime models are checked *before* the catalog at `ai_model_manager.py:78-80`, so it works with a totally empty `ai_models` cache and zero keys) — **has no smoke test at all.**

**W10 — Cloud-brain execution mode. (NOT release-blocking; high strategic value; ~1 week)**
Add a mode in `ai_routes.py` that proxies to `{AIDREAM_SERVER_URL}/api/ai/chat` with the cached JWT (`engine.py:96-130`) when no local key exists for the target provider. The stream vocabulary is **already identical**, so the desktop UI needs no change. This is the "BYO-key-less desktop" and it is closer than anyone thinks.

**W11 — Agent messages. (NOT release-blocking; needs an aidream change; ~1 week)**
Add a non-admin `GET` returning an agent's full definition **including `messages`, `settings`, and real `variables`** (`agents_listing.py:35-44,63` returns none of them), then extend `app/services/aidream/client.py:107-118` and `sync_engine.py:316-354`. Until then, cloud agents on desktop have no prompt, no model, and no tools.

**W12 — Housekeeping.** Regenerate `FALLBACK_CLOUD_BASELINE` (`tool_sync.py:84-104`, 49→108) or make an unreachable cloud a hard "cannot check" with no changeset emission. Fix `ai.endpoint` visibility (one migration, unblocks direct catalog reads). Start diffing `category` in `tool_sync`.

---

## 5. DOC LIES

These are what poisoned prior agents. Claim → reality, both cited.

**In matrx-ai (the worst offenders — they are *in the code*, so agents trust them most):**

| Doc claim | Reality |
|---|---|
| `matrx_ai/__init__.py:88-93` — *"the classic execution path runs with **ZERO database access**: any DBNotConfiguredError raised after a configure() like the above is a matrx-ai packaging bug."* | `persistence/queue_helpers.py:99-164` has no store short-circuit; with a lane open it calls `_ensure_cx_registered()` (`:162`→`:74`) → `_cx_managers_impl.py:12` → `db/_registry.py:88 raise DBNotConfiguredError`. Fired from `tools/executor.py:1076-1078` on **every tool completion**. matrx-local monkeypatches it (`engine.py:229-258`) *because this claim is false*. |
| `matrx_ai/__init__.py:134-139` — *"server_url: … for server-backed features (tool registry fetch, authenticated reads)"* and *"source_app: … filters server tool fetches and stamps persisted rows."* | Zero `get_ext("server_url")` / `get_ext("source_app")` in the package. `tools/registry.py:592,608` is the only loader and it goes through `get_base("ToolDefBase")` (`tool_def_db.py:60`). `registry.py` imports **no HTTP client**. Verified in the *installed* 0.3.3 wheel too. |
| `matrx_ai/__init__.py:131-133` — *"get_jwt: … Called at request time, never cached."* | It is never called **at all**. Written at `:172-174`, read by nothing. Only other reference is the validator at `client_host/validate.py:81-93`. |
| `client_host/validate.py:89-92` — the *error message* asserts the features exist: *"Server-backed features (tool registry fetch, authenticated reads) need the AIDream base URL."* | The validator is the sole reader of both values. **The validator lies to the host about why it's failing.** |
| `packages/matrx-ai/CLAUDE.md:95,154` — *"**Zero known violations**… No open package-boundary violations."* | `scripts/package_boundaries_baseline.json` lists **13 grandfathered matrx-ai violations**, incl. `tools/implementations/browser.py::matrx_scraper` (fatal at import) and `providers/eleven_labs/tts_streaming.py::initialize_systems`. The gate passes because they're grandfathered, not absent. |
| `packages/matrx-ai/CLAUDE.md:94` — *"No `from matrx_orm import …` at module top-level."* | Four such imports: `instructions/content_blocks_manager.py:5`, `persistence/replay.py:37-40`, `db/persistence.py:29`, `db/ai_models/_ai_model_dto_impl.py:5`. None guarded. matrx-orm is also **undeclared** in the pyproject. |
| `packages/matrx-ai/CLAUDE.md:141` — *"Tests must pass in an environment with just matrx-utils, matrx-connect, matrx-graph, and the provider SDKs."* | **No such environment is ever built.** CI runs from the aidream root (`test.yml:189,317`) where `aidream.pth` puts the repo root on `sys.path` and matrx_scraper is editable-installed. |
| `tests/client_host/test_import_surface.py:4-8` — *"imported in a SUBPROCESS with a scrubbed environment."* | It scrubs **env vars**, not `sys.path` (`:47-55`), and spawns `sys.executable` — the aidream venv (`:61`). Structurally cannot detect an aidream-root or matrx_scraper dependency. |
| `matrx_ai/tools/registry.py:70,83,622,668` + `merge.py:659` — *"the `tool_def` table"*, *"`public.tool_binding`"*, *"`public.tool_executor`"* | **No such tables exist in any schema.** Live `pg_class`: only `chat.tool_call`, `chat.tool_trace`. The real ones are `tool.definition` / `tool.binding` / `tool.executor` — as `tool_def_db.py:1`'s own docstring admits. |
| `aidream/package_integration.py:744` — *"matrx-ai must not import [matrx-orm] — even lazily; the boundary gate rejects it… matrx-ai never imports aidream."* | matrx-ai imports `matrx_orm` in ~23 files and aidream-root packages in 6 (`tools/implementations/user_secrets_tool.py:67`, `skills/providers.py:214`). The gate **grandfathers** them. |

**Workspace root:**

| `/Users/armanisadeghi/code/CLAUDE.md:77` — *"reads tool definitions live from the shared DB (`tool_def` / `tool_binding`)"* | Those relations do not exist. matrx-extend's own code already records the rename: `matrx-extend/src/lib/supabase/queries.ts:14` — *"tool.definition (was public.tool_def)"*. |
|---|---|

**In matrx-local:**

| Doc claim | Reality |
|---|---|
| `app/services/documents/supabase_client.py:10-12` — *"`note_versions`/`note_shares` in `public`, the sync-log tables — keeps the default public profile."* | Live `information_schema`: `note_versions`, `note_shares`, `note_devices`, `note_directory_mappings`, `note_sync_log` exist **only in `graveyard`**. This docstring is *why* the missing `schema=` looks intentional. |
| `docs/SYNC_CONTRACT.md:35` — *"cloud `public.note_versions` … push-only snapshot before each cloud overwrite"* | 404s every time; swallowed at `sync_engine.py:245-247`. Cloud version history has never worked. |
| `docs/SYNC_CONTRACT.md:97` — *"API keys and auth tokens never leave the machine."* | True for storage — but `key_manager.py:104-108` broadcasts every key into `os.environ` at startup (`main.py:311`). (Shell tools are scrubbed — `execution.py:70-89` — but the engine process env holds them.) |
| `docs/SYNC_CONTRACT.md:40` — API keys *"keychain-encrypted (base64 fallback)"* presented as an at-rest guarantee | `secret_store.py:70-83` catches **any** keychain failure → `repositories.py:1276-1279` base64. The "fallback" *is* the guarantee's escape hatch, and the encrypt-failure path logs at DEBUG (`:97-99`). |
| `pyproject.toml:59-64` and `.matrx/AGENT_TASKS.md:319-326` — *"matrx-ai 0.3.0 … are NOT on PyPI yet … `uv lock`/`uv sync`/CI CANNOT resolve these pins"* | `uv.lock:1886-1888`: `name = "matrx-ai"`, `version = "0.3.3"`, `source = { registry = "https://pypi.org/simple" }`. Verified live. **This false claim is embedded in the build manifest**, where it is most likely to be trusted. |
| `.matrx/AGENT_TASKS.md:312-318` — *"pricing/cost lookup ignores the host catalog"* | Fixed upstream: installed `matrx_ai/config/usage_config.py:342-354` consults the host catalog first. Stale task. |
| `app/tools/tool_sync.py:27,81` + `catalog.py:157` — *"the 49 cloud names … verified live 2026-07-10"* | 108 live, all bound. Baseline is 59 short. |
| `app/tools/tool_sync.py:39-42` — *"the cloud intentionally flattens category to 'local'"* | Live: 49 flat, 51 fine-grained. Contradicted by its own `_new_tool_sql:316-324`, which writes the fine-grained category. |
| `.env:96-99` — *"matrx-local always uses client mode: Supabase PostgREST + RLS"* | matrx-ai in matrx-local **never touches PostgREST**. `engine.py:305-313 has_db()` returns `False` unconditionally. |
| `.env:87-91` — *"SUPABASE_JWT_SECRET … matrx_ai reads SUPABASE_JWT_SECRET"* | Zero hits for that var across matrx-ai. `main.py:263` and `extension_auth.py:20` explicitly say there is no HS256 path. A live secret sitting in dev `.env` validating nothing. |
| `local_tool_bridge.py:463-467` and `engine.py:385-392` — *"Server-provided definitions win … the same path a server-provided definition would take."* | The opposite: `registry.py:839` types a DB row `ToolType.LOCAL`, and `executor.py:765-790` then **rejects it** for lack of a `function_path`. A DB definition "winning" is not benign; it is fatal. |

**The one docstring that is honest:** `app/services/ai/engine.py:38-41` — *"these conversations/messages are currently LOCAL-ONLY — no reconnect push pipeline exists yet."* Correct.

---

## 6. WHAT I STILL DON'T KNOW

**UNVERIFIED — real provider inference from the desktop.** Every AI test routes through `mock_chat`. Nobody has proven a real OpenAI/Anthropic call succeeds end-to-end from a shipped matrx-local build with only a user-entered key. *To settle:* W9 — one opt-in smoke test with a real key against a cheap model.

**UNVERIFIED — the llama-server keyless path.** It reads correct (`local_llm_registry.py:56-72,98-107,137-145`; runtime-first lookup at `ai_model_manager.py:78-80`), and it's the strongest "works" story in the stack, but zero tests cover it. *To settle:* drive `POST /ai/chat` with a `local/<model>` ref against a stub OpenAI-compatible server.

**UNVERIFIED — the exact cloud conversation table names.** The RLS policies for `chat.conversation` / `chat.message` were read from live `pg_policies` and permit owner CRUD. But no one has confirmed that matrx-ai's ORM `cxm` writes to *those* tables, nor tried an actual PostgREST insert with a user JWT. *To settle:* one `curl` insert with a real user token before building W5 on the assumption.

**UNVERIFIED — whether `matrx_ai.tools.implementations` is reachable at all on desktop today.** It's unimportable, but matrx-local registers its own 108 tools and never resolves a `func_path` under that package — so the break may be latent rather than live. *To settle:* grep every `tool.definition.function_path` value in the live DB for `matrx_ai.tools.implementations.*` and check whether any is bound to `matrx-local`.

**UNVERIFIED — the full blast radius of `source_kind`.** The prediction (all 108 tools break when a REST registry load lands) is a code-read inference, not an observation. *To settle:* stub a REST loader returning the 108 DB rows and run the tool smoke tests. **Do this before W3-build, not after.**

**UNVERIFIED — whether the graveyard tables have a canonical successor.** They exist only in `graveyard`; nothing equivalent exists in `public` or `workbench`. Whether that was intentional retirement or an incomplete migration is a **question only Arman can answer.** It gates W6's shape entirely.

**UNVERIFIED — the 4 `os.environ` bypasses' real reachability.** Only `conversation_labeler` is confirmed live in matrx-local (`local_ai_task.py:554` → `executor.py:1785` → the bypass). `ai_judge` and `image_pipeline_actions` have **zero imports** anywhere in matrx-local's `app/`. *To settle:* trivial grep already done — treat the other three as latent, not live.

**UNVERIFIED — matrx-extend's exposure.** The PascalCase dispatcher namespace (`Bash`, `Read`, `MDNSDiscover` — `dispatcher.py:167-304`) is what `/extension/rpc` capabilities advertises, and it is a **second, parallel namespace** to the 108 cloud `local_*` names. Whether matrx-extend or the cloud tool system expects one or the other was outside every investigation's scope. *To settle:* trace one live browser-agent tool call end-to-end from matrx-extend through `/extension/rpc` to `dispatcher.py`.