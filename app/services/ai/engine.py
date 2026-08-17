"""matrx-ai engine lifecycle management.

Handles one-time configuration of the matrx_ai library (>= 0.3.0 client-host
seams) at startup, then registers all local OS tools into matrx-ai's
ToolRegistry so AI models can invoke them.

Initialization sequence
-----------------------
  1. ``initialize_matrx_ai()`` — sync phase: calls ``matrx_ai.configure()``
     with the four client-host seams:
       - ``api_key_resolver``      → SQLite-backed key resolver (key_manager)
       - ``conversation_store``    → SQLiteConversationStore (conversation_handler)
       - ``model_catalog``         → SqliteModelCatalog (model_catalog)
       - ``get_jwt`` + ``server_url`` + ``source_app`` → user identity +
         the server-backed tool registry fetch (matrx-ai >= 0.4.0 derives a
         ServerToolSource that GETs /ai-tools/app/matrx_local/all)

     Supplying ``get_jwt`` + ``server_url`` ALSO gives this host MANDATE
     resolution for free: matrx-ai derives a ``ServerMandateSource`` and
     installs it as the Mandate resolver, so a Mandate (the named job — DB
     decides which agent/orchestra/workflow fulfils it) resolves over the
     aidream API with the SAME precedence the server uses for itself, and a
     user's rebind reaches desktop with no deploy. Never hardcode an agent
     id here; a missing capability is a missing API call to add.
     Contract: common-docs/systems/mandates/RUNTIME.md.
     Seam validation is all-errors-at-once (ClientHostConfigError) and any
     wiring failure CRASHES startup — a client host without its seams would
     die with DBNotConfiguredError mid-request otherwise.
  2. ``load_tools_and_register()`` — async phase:
       a. Loads the matrx-ai tool registry from the SERVER (matrx-ai's
          derived ServerToolSource; requires AIDREAM_SERVER_URL_LIVE).
       b. FALLBACK: backfills any definitions the server didn't provide from
          app/tools/catalog.py, then registers all local OS tool executors
          via ``LocalToolBridge``. matrx-ai's host-executor precedence
          guarantees server rows never shadow the local executors.

matrx-local ALWAYS runs as a matrx-ai CLIENT HOST
-------------------------------------------------
Data flow follows docs/SYNC_CONTRACT.md: the cloud is the durable source of
truth and local SQLite (~/.matrx/matrx.db) is a first-access replica /
working store, never a competing server.

  - No direct PostgreSQL / asyncpg connection is ever opened, and no matrx-ai
    ORM base/model is ever configured — any DBNotConfiguredError after
    configure() succeeds is a matrx-ai packaging bug (report it upstream,
    never work around it here).
  - Public data (models, tools, agent catalog) is cached into SQLite by the
    local_db sync engine from the AIDream REST API; matrx-ai reads models
    back through the injected SqliteModelCatalog.
  - Conversation persistence is handled by SQLiteConversationStore, which
    writes to local SQLite first. ``app/services/chat_sync`` mirrors those
    canonical chat rows to Supabase immediately after a turn and retries from
    the outbox on reconnect.
  - The user JWT is read from an in-memory cache (warmed from the auth_tokens
    SQLite table, updated on every token push) at call time so it
    automatically picks up token refreshes without re-initializing.
  - AI provider calls (OpenAI, Anthropic, etc.) work in full, using
    locally-stored provider API keys (local-only, never synced) via the
    injected key resolver.
"""

from __future__ import annotations

import re
from typing import Any

from dotenv import load_dotenv

from app.common.system_logger import get_logger

# ── protobuf shadowing guard ──────────────────────────────────────────────
# The image-gen installer PREPENDS ~/.matrx/image-gen-packages to sys.path
# (main.py Phase 0a / frozen runtime_hook). That directory ships its own
# protobuf (7.x today), which would shadow the venv's protobuf 6 — and
# xai-sdk (pulled via matrx-ai) hard-rejects protobuf 7 at import time,
# killing AI engine init with "Unsupported protobuf version". Importing
# google.protobuf HERE (this module is imported by app/main.py before the
# lifespan runs) pins the venv's version in sys.modules so the later path
# injection can't swap it. See also [tool.uv] constraint-dependencies in
# pyproject.toml. NOTE: the frozen build's runtime_hook.py injects before
# any app import — the packaged binary bundles its own protobuf via
# PyInstaller, so this guard is for the dev/uv path.
import google.protobuf  # noqa: F401  (shadowing guard, see above)

load_dotenv()

logger = get_logger()

_ai_initialized = False
_tools_loaded = False
_registered_tool_count = 0
_client_mode_active = False
_queue_guard_installed = False


# ------------------------------------------------------------------
# In-memory JWT cache — the single synchronous read point for matrx-ai
# ------------------------------------------------------------------
# matrx-ai calls get_jwt() synchronously (no await), so we maintain a
# module-level string cache that is:
#   1. Pre-loaded from SQLite during the async startup phase (warm_jwt_cache)
#   2. Updated instantly whenever React pushes a new token via POST /auth/token
#      (call set_jwt_cache from token_routes.py)
#   3. Cleared on logout (call clear_jwt_cache)
#
# This means matrx-ai always gets the latest known token with a simple
# dict lookup, no event-loop juggling required.

_jwt_cache: str | None = None


def set_jwt_cache(token: str | None) -> None:
    """Update the in-memory JWT so matrx-ai picks it up on next call."""
    global _jwt_cache
    _jwt_cache = token


def clear_jwt_cache() -> None:
    global _jwt_cache
    _jwt_cache = None


def _get_jwt() -> str | None:
    """Synchronous getter passed to matrx_ai.configure(get_jwt=...)."""
    return _jwt_cache


def install_client_host_queue_guard() -> None:
    """Skip ORM-backed write coordinators when matrx-ai runs as a client host.

    matrx-local intentionally configures matrx-ai without Postgres ORM bases.
    Conversation persistence is delegated to SQLiteConversationStore, but
    matrx-ai 0.4.0 still calls ``queue_helpers.get_coordinator()`` from a few
    reservation paths. Without this guard that helper lazily imports cx_ ORM
    managers and raises DBNotConfiguredError mid-stream.

    The guard is narrow: if the host has registered ORM bases, the original
    coordinator path is left intact.
    """
    global _queue_guard_installed
    if _queue_guard_installed:
        return

    try:
        from matrx_ai._ext import has_ext
        from matrx_ai.db._registry import get_base, get_model
        from matrx_ai.db import persistence as db_persistence
        from matrx_ai.orchestrator import executor as orchestrator_executor
        from matrx_ai.persistence import queue_helpers
        from matrx_ai.tools import dynamic_drain
        from matrx_ai.tools.logger import ToolExecutionLogger
    except Exception:
        logger.warning("[engine] could not install matrx-ai queue guard", exc_info=True)
        return

    original = queue_helpers.get_coordinator

    required_bases = (
        "AgentMemoryBase",
        "ObservationalMemoryBase",
        "ObservationalMemoryEventBase",
        "RequestBase",
        "RequestSnapshotBase",
        "ToolCallBase",
        "ToolTraceBase",
        "UserRequestBase",
        "MessageBase",
        "MediaBase",
        "ConversationBase",
        "PendingInjectionBase",
    )
    required_models = (
        "Message",
        "ToolCall",
        "ToolTrace",
        "Media",
        "UserRequest",
        "Request",
        "RequestSnapshot",
        "AgentMemory",
        "ObservationalMemory",
        "ObservationalMemoryEvent",
        "Conversation",
        "PendingInjection",
    )

    def _full_cx_orm_available() -> bool:
        try:
            for base_name in required_bases:
                get_base(base_name)
            for model_name in required_models:
                get_model(model_name)
            return True
        except Exception:
            return False

    def _client_host_without_orm() -> bool:
        return has_ext("conversation_store") and not _full_cx_orm_available()

    def _guarded_get_coordinator():
        if _client_host_without_orm():
            return None
        return original()

    async def _guarded_drain_pending_injections(config, ctx):
        if _client_host_without_orm():
            return ctx
        return await original_drain(config, ctx)

    async def _guarded_apply_authoritative_user_request_rollup(user_request_id: str) -> None:
        if _client_host_without_orm():
            return None
        return await original_rollup(user_request_id)

    def _guarded_schedule_labeling_if_new(exec_ctx, config) -> None:
        # Older embedded matrx-ai builds schedule the ORM-backed labeler even
        # when a ConversationStore client host is active. The desktop has no
        # AgentMemoryBase by design, so that path can only emit a noisy
        # DBNotConfiguredError. Current matrx-ai has the same guard upstream;
        # keep it here until the packaged floor includes that release.
        if _client_host_without_orm():
            return
        original_schedule_labeling(exec_ctx, config)

    # The tool-lifecycle background sweep (started unconditionally by matrx-ai's
    # handle_tool_calls, every 5 min) reaps stale/expired cx_tool_call rows via
    # abandon_stale_running_rows() / expire_delegated_calls(). Both go straight
    # to the cx ORM (_cxm()), which matrx-local NEVER configures in client-host
    # mode → DBNotConfiguredError every sweep, logged as a red traceback in the
    # user's app forever. There is nothing for a desktop client to reap: the
    # authoritative cx_tool_call table lives on the server, not here. Neutralize
    # both reapers to no-ops (return 0, their normal "nothing swept" result)
    # while ORM-less; a host that DOES register the cx bases keeps the real path.
    original_abandon = ToolExecutionLogger.abandon_stale_running_rows
    original_expire = ToolExecutionLogger.expire_delegated_calls

    async def _guarded_abandon_stale_running_rows(self, *args: Any, **kwargs: Any) -> int:
        if _client_host_without_orm():
            return 0
        return await original_abandon(self, *args, **kwargs)

    async def _guarded_expire_delegated_calls(self, *args: Any, **kwargs: Any) -> int:
        if _client_host_without_orm():
            return 0
        return await original_expire(self, *args, **kwargs)

    original_drain = dynamic_drain.drain_pending_injections
    original_rollup = db_persistence.apply_authoritative_user_request_rollup
    original_schedule_labeling = orchestrator_executor._schedule_labeling_if_new
    queue_helpers.get_coordinator = _guarded_get_coordinator
    dynamic_drain.drain_pending_injections = _guarded_drain_pending_injections
    db_persistence.apply_authoritative_user_request_rollup = (
        _guarded_apply_authoritative_user_request_rollup
    )
    orchestrator_executor._schedule_labeling_if_new = (
        _guarded_schedule_labeling_if_new
    )
    ToolExecutionLogger.abandon_stale_running_rows = _guarded_abandon_stale_running_rows
    ToolExecutionLogger.expire_delegated_calls = _guarded_expire_delegated_calls
    _queue_guard_installed = True
    logger.info("[engine] matrx-ai client-host queue guard installed ✓")


async def warm_jwt_cache() -> None:
    """Load the persisted JWT from SQLite into the in-memory cache.

    Call once during the async startup phase so matrx-ai has a token
    immediately if the user was previously logged in.
    """
    try:
        from app.services.local_db.repositories import TokenRepo
        row = await TokenRepo().get()
        if row and row.get("access_token"):
            set_jwt_cache(row["access_token"])
            logger.info("[engine] JWT cache warmed from SQLite (user_id=%s)", row.get("user_id"))
        else:
            logger.debug("[engine] No stored JWT — cache stays empty")
    except Exception as exc:
        logger.warning("[engine] Could not warm JWT cache: %s", exc)


def initialize_matrx_ai() -> None:
    """Configure the matrx_ai library once at startup (synchronous phase).

    Wires the matrx-ai 0.3.0 client-host seams (see module docstring). A
    seam-wiring failure (ClientHostConfigError) PROPAGATES — the engine must
    not boot with a half-configured AI stack, because the failure mode is a
    DBNotConfiguredError in the middle of a user's request instead of a
    clear crash at startup.

    The aidream server URL comes from the remote App Config system
    (``app.services.app_config.get_aidream_server_url`` — env override >
    remote > cache > compiled default), captured once here so remote changes
    apply on restart. An empty URL (contract breach) degrades only the
    server-backed features (tool registry fetch, authenticated reads) and is
    logged as an ERROR; the local seams are always wired.

    Call from the FastAPI lifespan handler BEFORE the async phase.
    """
    global _ai_initialized, _client_mode_active
    if _ai_initialized:
        logger.debug("[engine] initialize_matrx_ai() called again — already initialized, skipping")
        return

    # KNOWN UPSTREAM DEFECT (matrx-ai 0.3.0): importing matrx_ai.providers.*
    # cold still triggers the providers ↔ orchestrator circular import
    # (providers/__init__ → unified_client → orchestrator/__init__ →
    # executor → `from matrx_ai.providers import UnifiedAIClient` on the
    # partially-initialized package). Importing the orchestrator FIRST breaks
    # the cycle deterministically for the whole process. Tracked in
    # .matrx/AGENT_TASKS.md — remove when matrx-ai makes the executor import
    # lazy.
    import matrx_ai.orchestrator  # noqa: F401  (import-order fix, see above)

    # KNOWN UPSTREAM DEFECT (matrx-ai <= 0.3.5): configure() loads
    # matrx_ai/db/_registry.py by FILE PATH (spec_from_file_location on
    # Path(matrx_ai.__file__).parent / "db" / "_registry.py"). A PyInstaller
    # bundle ships modules inside the PYZ archive with no .py on disk, so that
    # load raised FileNotFoundError and killed the whole AI stack in every
    # packaged build (dev runs from source and never saw it). configure() skips
    # the file-path load when the module is ALREADY in sys.modules, so importing
    # it normally here is a complete, version-agnostic immunization. Fixed
    # upstream (matrx-ai __init__ now does a plain import); keep this until the
    # floor in pyproject.toml is raised past the fixed release.
    import matrx_ai.db._registry  # noqa: F401  (frozen-build fix, see above)

    import matrx_ai

    # Effective aidream URL from the remote App Config system (env override >
    # remote > cache > compiled default — the accessor owns tier-1 env
    # semantics). Captured ONCE here, so a remote change applies on restart —
    # same posture as delegation/engine.py. Imported inside the function to
    # avoid an import cycle (app_config → app.launcher / app.api.routes).
    from app.services.app_config import get_aidream_server_url

    server_url = get_aidream_server_url().strip()

    from importlib.metadata import version as _pkg_version

    def _safe_version(pkg: str) -> str:
        try:
            return _pkg_version(pkg)
        except Exception:
            return "NOT INSTALLED"

    logger.info("=" * 60)
    logger.info("[engine] matrx-ai STARTUP — client-host mode")
    logger.info("[engine]   matrx-ai   = %s", _safe_version("matrx-ai"))
    logger.info("[engine]   matrx-utils= %s", _safe_version("matrx-utils"))
    logger.info("[engine]   aidream server URL (app_config) = %s", server_url or "(NOT SET ✗)")
    logger.info("=" * 60)

    if not server_url:
        # Defensive: the app_config accessor always resolves at least the
        # compiled default, so this only fires if that contract breaks.
        logger.error(
            "[engine] aidream server URL resolved EMPTY from app_config — the "
            "server-backed tool registry fetch is DISABLED and tool "
            "definitions fall back to the bundled catalog. Local seams (keys, "
            "conversations, model catalog) are still active."
        )

    from app.services.ai.conversation_handler import get_conversation_store
    from app.services.ai.key_manager import get_key_resolver
    from app.services.ai.model_catalog import get_model_catalog
    execution_agent_source = None
    try:
        from matrx_ai.client_host.agent_source import ExecutionAgentSource  # noqa: F401

        from app.services.ai.agent_source import get_execution_agent_source

        execution_agent_source = get_execution_agent_source()
    except ImportError:
        # Safe containment for a rolling package upgrade: older matrx-ai
        # builds can still run direct model chat, but saved-agent execution is
        # not advertised and its route refuses explicitly.
        logger.warning(
            "[engine] matrx-ai has no ExecutionAgentSource seam — saved-agent "
            "execution is disabled; frontend routing will stay on AIDream"
        )

    # Seam wiring errors must CRASH here (ClientHostConfigError lists every
    # problem at once) — no try/except, no legacy fallback.
    configure_kwargs: dict[str, Any] = dict(
        api_key_resolver=get_key_resolver(),
        conversation_store=get_conversation_store(),
        model_catalog=get_model_catalog(),
        # get_jwt requires server_url (validated upstream); omit both when the
        # env var is missing so the degraded mode is explicit, not a crash.
        get_jwt=_get_jwt if server_url else None,
        server_url=server_url or None,
        source_app="matrx_local",
    )
    if execution_agent_source is not None:
        configure_kwargs["execution_agent_source"] = execution_agent_source
    matrx_ai.configure(**configure_kwargs)
    install_client_host_queue_guard()
    _client_mode_active = True
    _ai_initialized = True
    logger.info(
        "[engine] matrx-ai: configured as client host ✓  "
        "(keys → SQLite resolver, conversations → SQLite store, "
        "models → SQLite catalog%s)",
        ", identity → JWT cache" if server_url else "; NO server identity",
    )


def is_client_mode() -> bool:
    """Return True if matrx-ai was successfully configured with the client-host seams."""
    return _client_mode_active


def supports_agent_execution() -> bool:
    """True only when the canonical source seam is installed and configured."""

    try:
        from matrx_ai.client_host.agent_source import get_execution_agent_source

        return get_execution_agent_source() is not None
    except ImportError:
        return False


def has_db() -> bool:
    """Always returns False for matrx-local.

    matrx-local never opens an asyncpg connection to the database and never
    configures matrx-ai's ORM seams. All model/tool data comes from the
    local SQLite cache (synced from the AIDream REST API). Code that guards
    on has_db() will skip gracefully without error.
    """
    return False


async def load_tools_and_register() -> int:
    """Async startup phase: load tool registry, register local tools, start executor.

    Call this from the FastAPI lifespan handler AFTER ``initialize_matrx_ai()``.
    Safe to call multiple times (idempotent after first call).

    Returns the number of local OS tools registered into the matrx-ai registry.
    A return of 0 means tool calls will fail — the caller should mark the
    "tools" service DEGRADED rather than READY.
    """
    global _tools_loaded, _registered_tool_count
    if _tools_loaded:
        return _registered_tool_count

    if not _ai_initialized:
        logger.warning(
            "[engine] matrx-ai not initialized — skipping tool registry load. "
            "Call initialize_matrx_ai() first."
        )
        return 0

    # Track whether the load actually succeeded. We must NOT latch
    # _tools_loaded=True on failure — doing so makes this idempotent guard
    # short-circuit every retry, leaving the AI permanently tool-less while
    # /chat/tools cheerfully reports "loaded". Local OS-tool registration is
    # the load-bearing step; if it throws, the next call should try again.
    local_tools_ok = False

    # --- Phase B (runs FIRST): register all local OS tools via the
    # ExternalToolAdapter bridge. This MUST precede Phase A: the registry's
    # host-executor precedence (matrx-ai ``registry.py::_load_rows``) flips a
    # server row with no execution path to EXTERNAL_HANDLER only when an
    # ExternalHandlerRegistry handler ALREADY exists for it at load time.
    # When the server rows loaded first, all 115 ``local_*`` definitions
    # stayed typed LOCAL with no function_path and matrx-ai's
    # ``merge_request_tools`` pre-flight gate dropped every one of them from
    # the model's tool set (found live in the W1 boot drill, 2026-07-14).
    registered_count = 0
    try:
        from app.services.ai.local_tool_bridge import register_local_tools
        registered_count = register_local_tools()
        local_tools_ok = True
        logger.info("[engine] matrx-ai: registered %d local OS tools ✓", registered_count)
    except Exception:
        logger.error(
            "[engine] matrx-ai: local tool registration FAILED — "
            "AI won't have access to OS tools (will retry on next call)",
            exc_info=True,
        )

    # --- Phase A: load tool definitions into the matrx-ai registry ---
    # matrx-ai >= 0.4.0 derives a ServerToolSource from the configured
    # server_url + source_app seams and fetches this app's definitions from
    # /ai-tools/app/matrx_local/all — no ORM involved. When the server is
    # unreachable (offline boot) the fetch returns 0 rows and Phase A½
    # backfills from the bundled catalog. Capture the stdout noise and keep
    # it at DEBUG so an offline boot doesn't trip issue reports.
    import contextlib
    import io

    _tool_init_out = io.StringIO()
    try:
        from matrx_ai.tools.handle_tool_calls import initialize_tool_system
        try:
            with contextlib.redirect_stdout(_tool_init_out):
                count = await initialize_tool_system()
        finally:
            captured = _tool_init_out.getvalue().strip()
            if captured:
                # Single line, ANSI stripped: the desktop log viewer classifies
                # unprefixed continuation lines by keyword ("traceback", "error"
                # …) which would put this right back into issue reports as ERR.
                compact = re.sub(r"\x1b\[[0-9;]*m", "", captured)
                compact = " | ".join(
                    s for s in (p.strip() for p in compact.splitlines()) if s
                )
                logger.debug("[engine] matrx-ai tool init output: %s", compact)
        if count:
            logger.info(
                "[engine] matrx-ai: loaded %d tools from the server registry ✓", count
            )
        else:
            logger.warning(
                "[engine] matrx-ai: server tool registry returned 0 tools "
                "(offline or server unreachable) — falling back to the local "
                "tool catalog"
            )
    except Exception:
        logger.warning(
            "[engine] matrx-ai: FAILED to load the server tool registry — "
            "falling back to the local tool catalog",
            exc_info=True,
        )

    # --- Phase A½: backfill local tool definitions from the catalog ---
    # The server's tool registry endpoint may be unavailable or may not carry
    # this app's tools. The desktop owns its OS tools end-to-end — the catalog
    # (app/tools/catalog.py) has the schemas and Phase B registers the
    # executors — so synthesize definitions for any catalog tool the server
    # didn't provide. Server-provided definitions win (descriptions are
    # DB-canonical); we only fill gaps.
    try:
        from matrx_ai.tools.registry import ToolRegistry

        from app.services.ai.local_tool_bridge import build_local_tool_definitions
        from app.services.ai.remote_tool_bridge import build_local_context_definitions

        registry = ToolRegistry.get_instance()
        bundled_definitions = [
            *build_local_tool_definitions(),
            *build_local_context_definitions(),
        ]
        missing = [
            d for d in bundled_definitions if registry.get(d.name) is None
        ]
        if missing:
            n = registry.load_from_definitions(missing)
            logger.info(
                "[engine] matrx-ai: backfilled %d/%d local/context tool definitions from "
                "catalog (server registry %s) ✓",
                n,
                len(missing),
                "empty" if registry.count == n else "incomplete",
            )
    except Exception:
        logger.error(
            "[engine] matrx-ai: local tool definition backfill FAILED — "
            "AI won't see local tool schemas",
            exc_info=True,
        )

    # --- Phase C: register AIDream-owned tools as authenticated remote handlers ---
    # A local model runs a hybrid tool loop: OS tools stay in this process;
    # server-owned tools use AIDream's canonical /ai/tools/execute pipeline.
    # Offline startup is allowed because request preparation retries the catalog
    # fetch on demand before rejecting an injected tool.
    try:
        from app.services.ai.remote_tool_bridge import get_remote_tool_bridge

        remote_count = await get_remote_tool_bridge().refresh()
        logger.info(
            "[engine] matrx-ai: registered %d AIDream remote tool handlers ✓",
            remote_count,
        )
    except Exception:
        logger.warning(
            "[engine] matrx-ai: remote tool registry unavailable at startup — "
            "will retry on the first server-owned tool request",
            exc_info=True,
        )

    # Only mark loaded when the critical local-tool registration succeeded, so
    # a transient failure doesn't permanently wedge the registry as "loaded".
    _tools_loaded = local_tools_ok
    _registered_tool_count = registered_count
    return registered_count


def is_initialized() -> bool:
    return _ai_initialized


def tools_loaded() -> bool:
    return _tools_loaded
