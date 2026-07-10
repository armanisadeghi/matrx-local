"""matrx-ai engine lifecycle management.

Handles one-time initialization of the matrx_ai library at startup, then
registers all local OS tools into matrx-ai's ToolRegistry so AI models can
invoke them.

Initialization sequence
-----------------------
  1. ``initialize_matrx_ai()`` — sync phase: sets up client mode with a
     fully-configured ClientModeConfig (server_url, supabase_url, anon_key,
     get_jwt callable, and a SQLite-backed ConversationHandler).
  2. ``load_tools_and_register()`` — async phase:
       a. Loads the matrx-ai tool registry from the AIDream server API.
       b. Registers all local OS tools via ``LocalToolBridge``.

matrx-local ALWAYS runs in client mode
---------------------------------------
  - No direct PostgreSQL / asyncpg connection is ever opened.
  - Public data (models, tools, prompt builtins) is fetched from the AIDream
    REST API by the matrx-ai library itself.
  - Conversation persistence is handled by LocalConversationHandler, which
    writes to local SQLite (~/.matrx/matrx.db).
  - The user JWT is read from the auth_tokens SQLite table at call time so
    it automatically picks up token refreshes without re-initializing.
  - AI provider calls (OpenAI, Anthropic, etc.) work in full.
"""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

from app.common.system_logger import get_logger

load_dotenv()

logger = get_logger()

_ai_initialized = False
_tools_loaded = False
_registered_tool_count = 0
_client_mode_active = False


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
    """Synchronous getter passed to ClientModeConfig.get_jwt."""
    return _jwt_cache


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
    """Initialize the matrx_ai library once at startup (synchronous phase).

    Builds a ClientModeConfig with:
      - server_url from AIDREAM_SERVER_URL_LIVE env var
      - supabase_url / supabase_anon_key from SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY
      - get_jwt: callable that reads the stored JWT from SQLite at request time
      - conversation_handler: LocalConversationHandler (SQLite-backed)

    Call from the FastAPI lifespan handler BEFORE the async phase.
    """
    global _ai_initialized, _client_mode_active
    if _ai_initialized:
        logger.debug("[engine] initialize_matrx_ai() called again — already initialized, skipping")
        return

    import matrx_ai
    from matrx_ai.client_mode.config import ClientModeConfig

    server_url = os.getenv("AIDREAM_SERVER_URL_LIVE", "").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_anon_key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()

    from importlib.metadata import version as _pkg_version

    def _safe_version(pkg: str) -> str:
        try:
            return _pkg_version(pkg)
        except Exception:
            return "NOT INSTALLED"

    logger.info("=" * 60)
    logger.info("[engine] matrx-ai STARTUP — client mode")
    logger.info("[engine]   matrx-ai   = %s", _safe_version("matrx-ai"))
    logger.info("[engine]   matrx-orm  = %s", _safe_version("matrx-orm"))
    logger.info("[engine]   matrx-utils= %s", _safe_version("matrx-utils"))
    logger.info("[engine]   AIDREAM_SERVER_URL_LIVE  = %s", server_url or "(NOT SET ✗)")
    logger.info("[engine]   SUPABASE_URL             = %s", supabase_url or "(NOT SET ✗)")
    logger.info("[engine]   SUPABASE_PUBLISHABLE_KEY = %s", "SET ✓" if supabase_anon_key else "(NOT SET ✗)")
    logger.info("=" * 60)

    missing = []
    if not server_url:
        missing.append("AIDREAM_SERVER_URL_LIVE")
    if not supabase_url:
        missing.append("SUPABASE_URL")
    if not supabase_anon_key:
        missing.append("SUPABASE_PUBLISHABLE_KEY")
    if missing:
        logger.error(
            "[engine] Missing env vars: %s — AI models/agents may not load. "
            "Add them to .env",
            ", ".join(missing),
        )

    from app.services.ai.conversation_handler import get_conversation_handler

    try:
        config = ClientModeConfig(
            server_url=server_url,
            supabase_url=supabase_url,
            supabase_anon_key=supabase_anon_key,
            get_jwt=_get_jwt,
            conversation_handler=get_conversation_handler(),
            source_app="matrx_local",
        )
        matrx_ai.initialize(client_mode=True, client_config=config)
        _client_mode_active = True
        logger.info(
            "[engine] matrx-ai: initialized in client mode ✓  "
            "(conversations → SQLite, data → AIDream API)"
        )
    except Exception:
        logger.error(
            "[engine] matrx-ai: client mode initialization FAILED",
            exc_info=True,
        )
        # Mark initialized anyway so tool registration proceeds on the async phase.
        # AI provider calls still work; DB-backed features unavailable.
        matrx_ai._initialized = True

    _ai_initialized = True


def is_client_mode() -> bool:
    """Return True if matrx-ai was successfully initialized in client (PostgREST + RLS) mode."""
    return _client_mode_active


def has_db() -> bool:
    """Always returns False for matrx-local.

    matrx-local never opens an asyncpg connection to the database. All
    data access goes through the Supabase PostgREST API (client mode).
    Code that guards on has_db() will skip gracefully without error.
    """
    return False


async def load_tools_and_register() -> int:
    """Async startup phase: load tool registry from DB, register local tools, start executor.

    Call this from the FastAPI lifespan handler AFTER ``initialize_matrx_ai()``.
    Safe to call multiple times (idempotent after first call).

    Returns the number of local OS tools registered into the matrx-ai registry.
    A return of 0 means tool calls will fail — the caller should mark the
    "tools" service DEGRADED rather than READY.
    """
    global _tools_loaded, _registered_tool_count
    if _tools_loaded:
        return _registered_tool_count

    import matrx_ai

    if not matrx_ai._initialized:
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

    # --- Phase A: load DB tools into matrx-ai registry ---
    # matrx_ai vcprints a red error + traceback to stdout when the server
    # registry fetch fails (the deployed server 404s this app's endpoint for
    # matrx-ai 0.1.x). That is an expected, mitigated condition — Phase A½
    # backfills the definitions — so capture the stdout noise and keep it at
    # DEBUG instead of tripping issue reports with 3 ERR lines on every boot.
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
            logger.info("[engine] matrx-ai: loaded %d tools from DB into registry ✓", count)
        else:
            logger.warning(
                "[engine] matrx-ai: server tool registry returned 0 tools — "
                "falling back to the local tool catalog"
            )
    except Exception:
        logger.warning(
            "[engine] matrx-ai: FAILED to load tool registry from DB — "
            "AI agents won't have access to cloud-registered tools",
            exc_info=True,
        )

    # --- Phase A½: backfill local tool definitions from the catalog ---
    # The server's tool registry endpoint may be unavailable or may not carry
    # this app's tools (the deployed server 404s /api/ai-tools/app/matrx_local
    # for matrx-ai 0.1.x clients). The desktop owns its OS tools end-to-end —
    # the catalog (app/tools/catalog.py) has the schemas and Phase B registers
    # the executors — so synthesize definitions for any catalog tool the
    # server didn't provide. Server-provided definitions win (descriptions are
    # DB-canonical); we only fill gaps.
    try:
        from matrx_ai.tools.registry import ToolRegistryV2

        from app.services.ai.local_tool_bridge import build_local_tool_definitions

        registry = ToolRegistryV2.get_instance()
        missing = [
            d for d in build_local_tool_definitions() if registry.get(d.name) is None
        ]
        if missing:
            n = registry.load_from_definitions(missing)
            logger.info(
                "[engine] matrx-ai: backfilled %d/%d local tool definitions from "
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

    # --- Phase B: register all local OS tools via the ExternalToolAdapter bridge ---
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

    # --- Phase C: probe for local LLM (GenericOpenAIChat) support ---
    # This check is non-blocking — it only logs status.  Actual local LLM
    # registration happens later when the frontend calls POST /chat/local-llm/connect.
    try:
        from app.services.ai.local_llm_registry import _check_matrx_ai_support
        _check_matrx_ai_support()
    except Exception:
        logger.warning("[engine] Could not probe local LLM registry", exc_info=True)

    # Only mark loaded when the critical local-tool registration succeeded, so
    # a transient failure doesn't permanently wedge the registry as "loaded".
    _tools_loaded = local_tools_ok
    _registered_tool_count = registered_count
    return registered_count


def is_initialized() -> bool:
    return _ai_initialized


def tools_loaded() -> bool:
    return _tools_loaded
