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
       - ``get_jwt`` + ``server_url`` + ``source_app`` → user identity for
         server-backed features (tool registry fetch, authenticated reads)
     Seam validation is all-errors-at-once (ClientHostConfigError) and any
     wiring failure CRASHES startup — a client host without its seams would
     die with DBNotConfiguredError mid-request otherwise.
  2. ``load_tools_and_register()`` — async phase:
       a. Loads the matrx-ai tool registry (server DB fetch fails loudly in a
          client host; that's expected and mitigated by b).
       b. Backfills local tool definitions from app/tools/catalog.py and
          registers all local OS tool executors via ``LocalToolBridge``.

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
    writes to local SQLite. NOTE: these conversations/messages are currently
    LOCAL-ONLY — no reconnect push pipeline exists yet (contract gap #1 in
    docs/SYNC_CONTRACT.md).
  - The user JWT is read from an in-memory cache (warmed from the auth_tokens
    SQLite table, updated on every token push) at call time so it
    automatically picks up token refreshes without re-initializing.
  - AI provider calls (OpenAI, Anthropic, etc.) work in full, using
    locally-stored provider API keys (local-only, never synced) via the
    injected key resolver.
"""

from __future__ import annotations

import os
import re

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

    Missing env vars (AIDREAM_SERVER_URL_LIVE) degrade only the
    server-backed features (tool registry fetch, authenticated reads) and
    are logged as ERRORs; the local seams are always wired.

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

    server_url = os.getenv("AIDREAM_SERVER_URL_LIVE", "").strip()

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
    logger.info("[engine]   AIDREAM_SERVER_URL_LIVE  = %s", server_url or "(NOT SET ✗)")
    logger.info("=" * 60)

    if not server_url:
        logger.error(
            "[engine] AIDREAM_SERVER_URL_LIVE is not set — server-backed matrx-ai "
            "features (tool registry fetch, authenticated reads) are DISABLED. "
            "Add it to .env. Local seams (keys, conversations, model catalog) "
            "are still active."
        )

    from app.services.ai.conversation_handler import get_conversation_store
    from app.services.ai.key_manager import get_key_resolver
    from app.services.ai.model_catalog import get_model_catalog

    # Seam wiring errors must CRASH here (ClientHostConfigError lists every
    # problem at once) — no try/except, no legacy fallback.
    matrx_ai.configure(
        api_key_resolver=get_key_resolver(),
        conversation_store=get_conversation_store(),
        model_catalog=get_model_catalog(),
        # get_jwt requires server_url (validated upstream); omit both when the
        # env var is missing so the degraded mode is explicit, not a crash.
        get_jwt=_get_jwt if server_url else None,
        server_url=server_url or None,
        source_app="matrx_local",
    )
    install_client_host_coordinator_guard()
    _client_mode_active = True
    _ai_initialized = True
    logger.info(
        "[engine] matrx-ai: configured as client host ✓  "
        "(keys → SQLite resolver, conversations → SQLite store, "
        "models → SQLite catalog%s)",
        ", identity → JWT cache" if server_url else "; NO server identity",
    )


def install_client_host_coordinator_guard() -> None:
    """Force matrx-ai's WriteCoordinator OFF in a client host.

    KNOWN UPSTREAM DEFECT (matrx-ai 0.3.0): the client-host contract says
    "no client-host write ever reaches persistence/queue_helpers.py — the
    coordinator is always None in a client host", but
    ``queue_helpers.get_coordinator()`` only returns None when there is NO
    RequestLane. matrx-connect's ``create_streaming_response`` (the /ai
    surface) ALWAYS opens a lane, so the coordinator lazily constructs and
    ``_ensure_cx_registered()`` imports the ORM cx managers →
    ``DBNotConfiguredError`` mid-request. Until upstream short-circuits on a
    configured conversation_store, this guard wraps ``get_coordinator`` to
    return None whenever the store seam is set — which routes every write
    through the store dispatch exactly as documented. Idempotent. Tracked
    in .matrx/AGENT_TASKS.md; delete when matrx-ai fixes it.
    """
    from matrx_ai.persistence import queue_helpers

    if getattr(queue_helpers, "_matrx_local_client_host_guard", False):
        return
    _orig_get_coordinator = queue_helpers.get_coordinator

    def _guarded_get_coordinator():  # type: ignore[no-untyped-def]
        from matrx_ai.client_host import get_conversation_store

        if get_conversation_store() is not None:
            return None
        return _orig_get_coordinator()

    queue_helpers.get_coordinator = _guarded_get_coordinator  # type: ignore[assignment]

    # Same defect class, second site: the Turn-Boundary Inbox drain
    # (matrx_ai.tools.dynamic_drain.drain_pending_injections) reads
    # cx_pending_injection through cxm with no store-first dispatch, so every
    # tool-loop iteration in a client host raised DBNotConfiguredError at the
    # turn boundary. There is no local inbox (no producer can queue into a
    # desktop conversation), so the drain is a documented no-op here.
    from matrx_ai.tools import dynamic_drain

    _orig_drain_injections = dynamic_drain.drain_pending_injections

    async def _guarded_drain_pending_injections(config, ctx):  # type: ignore[no-untyped-def]
        from matrx_ai.client_host import get_conversation_store

        if get_conversation_store() is not None:
            return ctx
        return await _orig_drain_injections(config, ctx)

    dynamic_drain.drain_pending_injections = _guarded_drain_pending_injections  # type: ignore[assignment]

    _orig_drain_pending = dynamic_drain.drain_pending

    async def _guarded_drain_pending(config, ctx):  # type: ignore[no-untyped-def]
        from matrx_ai.client_host import get_conversation_store

        if get_conversation_store() is not None:
            # Tool mutations are in-memory and still valid locally; only the
            # cx_pending_injection read is skipped.
            return await dynamic_drain.drain_tool_mutations(config, ctx)
        return await _orig_drain_pending(config, ctx)

    dynamic_drain.drain_pending = _guarded_drain_pending  # type: ignore[assignment]

    queue_helpers._matrx_local_client_host_guard = True  # type: ignore[attr-defined]
    logger.info(
        "[engine] client-host coordinator guard installed "
        "(WriteCoordinator forced off, inbox drain no-op — store dispatch "
        "owns all writes)"
    )


def is_client_mode() -> bool:
    """Return True if matrx-ai was successfully configured with the client-host seams."""
    return _client_mode_active


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

    # --- Phase A: load DB tools into matrx-ai registry ---
    # In a client host there is no ORM ToolDefBase, so matrx-ai's registry
    # fetch vcprints a red error + traceback to stdout and returns 0 rows.
    # That is an expected, mitigated condition — Phase A½ backfills the
    # definitions from the local catalog — so capture the stdout noise and
    # keep it at DEBUG instead of tripping issue reports with ERR lines on
    # every boot.
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
    # this app's tools. The desktop owns its OS tools end-to-end — the catalog
    # (app/tools/catalog.py) has the schemas and Phase B registers the
    # executors — so synthesize definitions for any catalog tool the server
    # didn't provide. Server-provided definitions win (descriptions are
    # DB-canonical); we only fill gaps.
    try:
        from matrx_ai.tools.registry import ToolRegistry

        from app.services.ai.local_tool_bridge import build_local_tool_definitions

        registry = ToolRegistry.get_instance()
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

    # Only mark loaded when the critical local-tool registration succeeded, so
    # a transient failure doesn't permanently wedge the registry as "loaded".
    _tools_loaded = local_tools_ok
    _registered_tool_count = registered_count
    return registered_count


def is_initialized() -> bool:
    return _ai_initialized


def tools_loaded() -> bool:
    return _tools_loaded
