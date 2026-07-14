"""Chat API routes — tool schemas, models, agents, + AI streaming completions.

Provides:
  GET  /chat/tools                   — all tool schemas (Anthropic-compatible)
  GET  /chat/tools/by-category       — tool schemas grouped by category
  GET  /chat/tools/anthropic         — Anthropic Messages API format
  GET  /chat/models                  — AI models from local SQLite cache
  GET  /chat/agents                  — agents/prompts from local SQLite cache
  GET  /chat/local-tools             — local OS tools registered in matrx-ai registry
  POST /chat/local-llm/connect       — register running llama-server with the agent pipeline
  POST /chat/local-llm/disconnect    — deregister local LLM (server stopped)
  GET  /chat/local-llm/status        — current local LLM registration status

Streaming AI endpoints (POST /chat/ai/* == POST /ai/*) live in
app/api/ai_routes.py — the host-owned AI surface mounted at both paths.

Data access strategy
--------------------
SQLite is the single source of truth.  All reads here go through SQLite
repositories.  The SyncEngine populates SQLite in the background by calling
the AIDream server API.

If SQLite is empty and a sync has never completed, we trigger a background
sync and return an empty list with syncing=True so the UI can show a spinner.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.common.background_tasks import fire_and_forget
from app.common.system_logger import get_logger
from app.tools.tool_schemas import (
    generate_all_tool_schemas,
    get_anthropic_tools,
    get_tool_schemas_by_category,
)

logger = get_logger()
_agents_sync_task: "asyncio.Task | None" = None

router = APIRouter(prefix="/chat", tags=["chat"])

# ---------------------------------------------------------------------------
# Endpoint → provider label mapping (from matrx_ai endpoints field)
# ---------------------------------------------------------------------------
_ENDPOINT_TO_PROVIDER: dict[str, str] = {
    "anthropic_chat": "anthropic",
    "anthropic_adaptive": "anthropic",
    "openai_chat": "openai",
    "google_chat": "google",
    "groq_chat": "groq",
    "together_chat": "together",
    "xai_chat": "xai",
    "cerebras_chat": "cerebras",
}

# Providers whose keys we'd realistically have installed locally
_SUPPORTED_PROVIDERS = {"anthropic", "openai", "google", "groq", "together", "xai", "cerebras"}


def _endpoint_to_provider(endpoints: list[str]) -> str | None:
    for ep in endpoints:
        p = _ENDPOINT_TO_PROVIDER.get(ep)
        if p in _SUPPORTED_PROVIDERS:
            return p
    return None


# ---------------------------------------------------------------------------
# Tool schema endpoints (no auth required)
# ---------------------------------------------------------------------------


@router.get("/tools")
async def list_tool_schemas() -> dict:
    """Return all tool schemas with category info for the chat UI."""
    schemas = generate_all_tool_schemas()
    return {"tools": schemas, "total": len(schemas)}


@router.get("/tools/by-category")
async def list_tool_schemas_by_category() -> dict:
    """Return tool schemas grouped by category."""
    grouped = get_tool_schemas_by_category()
    return {
        "categories": grouped,
        "total_categories": len(grouped),
        "total_tools": sum(len(v) for v in grouped.values()),
    }


@router.get("/tools/anthropic")
async def list_anthropic_tool_schemas() -> dict:
    """Return tool schemas in Anthropic Messages API format."""
    tools = get_anthropic_tools()
    return {"tools": tools, "total": len(tools)}


@router.get("/local-tools")
async def list_local_tools() -> dict[str, Any]:
    """Return all local OS tools registered in the matrx-ai ToolRegistry."""
    try:
        from app.tools.catalog import get_catalog
        from matrx_ai.tools.registry import ToolRegistry
        from app.services.ai.engine import tools_loaded

        registry = ToolRegistry.get_instance()
        tools_out = []

        for entry in get_catalog():
            tool_def = registry.get(entry.cloud_name)
            tools_out.append({
                "name": entry.cloud_name,
                "dispatcher_name": entry.dispatcher_name,
                "description": entry.description,
                "category": entry.category,
                "tags": list(entry.tags),
                "parameters": entry.input_schema,
                "version": entry.version,
                "platforms": list(entry.platforms) if entry.platforms else None,
                "registered": tool_def is not None,
                "timeout_seconds": entry.timeout_seconds,
            })

        registered_count = sum(1 for t in tools_out if t["registered"])
        return {
            "tools": tools_out,
            "total": len(tools_out),
            "registered": registered_count,
            "registry_loaded": tools_loaded(),
        }
    except Exception:
        logger.warning("Failed to list local tools", exc_info=True)
        return {"tools": [], "total": 0, "registered": 0, "registry_loaded": False}


# ---------------------------------------------------------------------------
# Models endpoint — reads from SQLite (populated by SyncEngine)
# ---------------------------------------------------------------------------


@router.get("/models")
async def list_models() -> dict[str, Any]:
    """Return all active AI models from local SQLite cache.

    SQLite is populated by SyncEngine on startup and every 10 minutes.
    If the cache is empty and has never synced, triggers a background sync
    and returns syncing=True so the UI can show a loading state.
    """
    from app.services.local_db.repositories import ModelsRepo, SyncMetaRepo
    from app.services.local_db.sync_engine import get_sync_engine

    logger.info("[chat_routes /models] Request received")

    repo = ModelsRepo()
    models = await repo.list_all(include_deprecated=False)

    if not models:
        sync_meta = SyncMetaRepo()
        meta = await sync_meta.get_last_sync("models")
        never_synced = meta is None or meta.get("last_synced_at") is None

        if never_synced:
            logger.info(
                "[chat_routes /models] SQLite empty and never synced — triggering background sync"
            )
            engine = get_sync_engine()
            fire_and_forget(engine.sync_models(), name="models-sync")
            return {"models": [], "total": 0, "source": "sqlite", "syncing": True}

        logger.info("[chat_routes /models] SQLite is empty (sync ran but found no models)")
        return {"models": [], "total": 0, "source": "sqlite", "syncing": False}

    logger.info("[chat_routes /models] Returning %d models from SQLite", len(models))
    return {"models": models, "total": len(models), "source": "sqlite", "syncing": False}


# ---------------------------------------------------------------------------
# Agents endpoint — reads from SQLite (populated by SyncEngine)
# ---------------------------------------------------------------------------


def _shape_agent_from_sqlite(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a SQLite agents row into the API response shape."""
    import json as _json
    settings: dict[str, Any] = row.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = _json.loads(settings)
        except Exception:
            settings = {}
    variable_defaults = row.get("variable_defaults") or []
    if isinstance(variable_defaults, str):
        try:
            variable_defaults = _json.loads(variable_defaults)
        except Exception:
            variable_defaults = []
    tags = row.get("tags") or []
    if isinstance(tags, str):
        try:
            tags = _json.loads(tags)
        except Exception:
            tags = []
    return {
        "id": row.get("id", ""),
        "name": row.get("name", ""),
        "description": row.get("description") or "",
        "source": row.get("source", "builtin"),
        "variable_defaults": variable_defaults,
        "category": row.get("category") or None,
        "tags": tags,
        "is_favorite": bool(row.get("is_favorite", False)),
        "settings": {
            "model_id": settings.get("model_id"),
            "temperature": settings.get("temperature"),
            "max_tokens": settings.get("max_tokens") or settings.get("max_output_tokens"),
            "stream": settings.get("stream", True),
            "tools": settings.get("tools") or [],
        },
    }


@router.get("/agents")
async def list_agents() -> dict[str, Any]:
    """Return all agents from local SQLite cache.

    Sources:
      - builtins: prompt_builtins table (system agents, always available)
      - user: prompts table (user's own agents, populated when JWT is available)
      - shared: not yet supported

    SQLite is populated by SyncEngine. If empty and never synced, triggers
    a background sync and returns syncing=True.
    """
    from app.services.local_db.repositories import AgentsRepo, SyncMetaRepo, TokenRepo
    from app.services.local_db.sync_engine import get_sync_engine

    logger.info("[chat_routes /agents] Request received")

    # Resolve the authenticated user_id from the stored JWT so we only return
    # this user's own agents (builtins are always included).
    user_id: str | None = None
    jwt: str | None = None
    try:
        token_repo = TokenRepo()
        token_row = await token_repo.get()
        if token_row and not token_repo.is_expired(token_row):
            user_id = token_row.get("user_id") or None
            jwt = token_row.get("access_token") or None
    except Exception:
        pass

    logger.info("[chat_routes /agents] user_id from stored JWT: %s", user_id)

    repo = AgentsRepo()
    all_agents = await repo.list_all(user_id=user_id)

    builtins = [_shape_agent_from_sqlite(a) for a in all_agents if a.get("source") == "builtin"]
    user_agents = [_shape_agent_from_sqlite(a) for a in all_agents if a.get("source") == "user"]

    logger.info(
        "[chat_routes /agents] Found in SQLite: %d builtins, %d user agents",
        len(builtins), len(user_agents),
    )

    sync_meta = SyncMetaRepo()
    meta = await sync_meta.get_last_sync("agents")
    never_synced = meta is None or meta.get("last_synced_at") is None

    if not builtins:
        if never_synced:
            logger.info(
                "[chat_routes /agents] SQLite empty and never synced — triggering background sync"
            )
            engine = get_sync_engine()
            fire_and_forget(engine.sync_agents(), name="agents-sync")
            return {
                "builtins": [], "user": [], "shared": [],
                "source": "sqlite", "syncing": True,
                "totals": {"builtins": 0, "user": 0, "shared": 0, "total": 0},
            }
        logger.info("[chat_routes /agents] SQLite empty but sync ran — no builtins from server")

    # If we have a JWT but no user agents, kick a background sync to fetch them.
    # This covers the case where builtins synced before the JWT was available.
    if jwt and user_id and not user_agents:
        logger.info(
            "[chat_routes /agents] No user agents in SQLite but JWT is available — "
            "triggering background agent sync for user_id=%s",
            user_id,
        )
        global _agents_sync_task
        existing = _agents_sync_task
        if existing is None or existing.done():
            engine = get_sync_engine()
            # Retain the task (the loop holds only a weak ref) and reuse it
            # while in flight — /chat/agents is polled, and each poll used to
            # spawn ANOTHER full server sync when the user had zero agents.
            _agents_sync_task = asyncio.create_task(engine.sync_agents())

    total = len(builtins) + len(user_agents)
    return {
        "builtins": sorted(builtins, key=lambda x: x["name"]),
        "user": sorted(user_agents, key=lambda x: x["name"]),
        "shared": [],
        "source": "sqlite",
        "syncing": False,
        "totals": {
            "builtins": len(builtins),
            "user": len(user_agents),
            "shared": 0,
            "total": total,
        },
    }


# ---------------------------------------------------------------------------
# Sync status + force-sync endpoint for chat data (models, agents)
# ---------------------------------------------------------------------------


@router.get("/sync/status")
async def chat_sync_status() -> dict[str, Any]:
    """Return the sync status for all chat-related data from local SQLite."""
    from app.services.local_db.repositories import (
        SyncMetaRepo, ModelsRepo, AgentsRepo, TokenRepo, PromptBuiltinsRepo, PromptsRepo,
    )
    from app.services.local_db.sync_engine import get_sync_engine

    sync_meta = SyncMetaRepo()
    all_meta = await sync_meta.get_all_sync_status()

    models_count = await ModelsRepo().count()
    agents_count_total = len(await AgentsRepo().list_all())
    builtins_count = await PromptBuiltinsRepo().count()

    user_id: str | None = None
    jwt_present = False
    try:
        token_repo = TokenRepo()
        token_row = await token_repo.get()
        if token_row:
            user_id = token_row.get("user_id") or None
            jwt_present = not token_repo.is_expired(token_row)
    except Exception:
        pass

    prompts_count = 0
    if user_id:
        prompts_count = await PromptsRepo().count(user_id)

    engine = get_sync_engine()

    return {
        "sync_engine_running": engine.running,
        "jwt_present": jwt_present,
        "user_id": user_id,
        "counts": {
            "models": models_count,
            "agents_total": agents_count_total,
            "prompt_builtins": builtins_count,
            "user_prompts": prompts_count,
        },
        "last_sync": {m["entity_type"]: m for m in all_meta},
    }


@router.post("/sync/trigger")
async def trigger_chat_sync() -> dict[str, Any]:
    """Force an immediate full sync of chat data (models, agents, tools) from the server."""
    from app.services.local_db.sync_engine import get_sync_engine

    logger.info("[chat_routes /sync/trigger] Manual sync triggered")
    engine = get_sync_engine()
    results = await engine.sync_all()
    logger.info("[chat_routes /sync/trigger] Sync complete: %s", results)
    return {"status": "ok", "results": results}


# ---------------------------------------------------------------------------
# Chat-system mirror sync (chat.* <-> cloud) — see app/services/chat_sync/
# ---------------------------------------------------------------------------


@router.get("/mirror/status")
async def chat_mirror_status() -> dict[str, Any]:
    """Status of the bidirectional chat.* mirror sync (outbox + checkpoints)."""
    from app.services.chat_sync import get_chat_sync_engine

    return await get_chat_sync_engine().get_status()


@router.post("/mirror/sync")
async def trigger_chat_mirror_sync() -> dict[str, Any]:
    """Run one push+pull cycle of the chat.* mirror sync right now."""
    from app.services.chat_sync import get_chat_sync_engine
    from app.services.local_db.repositories import TokenRepo

    engine = get_chat_sync_engine()
    if not engine.is_configured:
        token_repo = TokenRepo()
        row = await token_repo.get()
        if not row or not row.get("access_token") or not row.get("user_id"):
            raise HTTPException(status_code=401, detail="No signed-in user — sign in first")
        if token_repo.is_expired(row):
            raise HTTPException(status_code=401, detail="Stored JWT expired — refresh via POST /auth/token")
        engine.configure(row["user_id"], row["access_token"])
    logger.info("[chat_routes /mirror/sync] Manual chat mirror sync triggered")
    summary = await engine.sync_cycle()
    return {"status": "ok", **summary}


# ---------------------------------------------------------------------------
# AI streaming endpoints live in app/api/ai_routes.py (the host-owned /ai
# surface, mounted at /ai AND /chat/ai — see main.py Phase 1b). This router
# keeps only the non-streaming chat metadata endpoints below.
# ---------------------------------------------------------------------------

@router.get("/ai-status")
async def ai_provider_status() -> dict[str, Any]:
    """Return which AI providers are configured (have API keys set).

    Used by the UI to show a warning when no providers are available instead
    of letting requests crash silently with 'No API key was provided' errors.

    This endpoint is public (listed in _PUBLIC_PATHS in auth.py) so it can be
    called before auth is established.
    """
    import os

    PROVIDER_ENV_VARS: dict[str, list[str]] = {
        "anthropic": ["ANTHROPIC_API_KEY"],
        "openai":    ["OPENAI_API_KEY"],
        "google":    ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "groq":      ["GROQ_API_KEY"],
        "together":  ["TOGETHER_API_KEY"],
        "xai":       ["XAI_API_KEY"],
        "cerebras":  ["CEREBRAS_API_KEY"],
    }

    available: list[str] = []
    missing: list[str] = []

    for provider, env_vars in PROVIDER_ENV_VARS.items():
        configured = any(os.getenv(var, "").strip() for var in env_vars)
        if configured:
            available.append(provider)
        else:
            missing.append(provider)

    ai_initialized = False
    client_mode = False
    try:
        from app.services.ai.engine import is_initialized, is_client_mode
        ai_initialized = is_initialized()
        client_mode = is_client_mode()
    except Exception:
        pass

    local_llm: dict[str, Any] = {
        "available": False,
        "port": None,
        "model_name": None,
        "matrx_ai_support": False,
    }
    try:
        from app.services.ai.local_llm_registry import get_local_llm_status
        local_llm = get_local_llm_status()
    except Exception:
        pass

    return {
        "providers": {
            "available": available,
            "missing": missing,
            "any_available": len(available) > 0,
        },
        # NOTE: no jwt_validation block. The engine runs on the user's
        # own machine and has no server-side JWT signing secret. Auth
        # posture for /extension/* is reported via /extension/boot-check.
        "engine": {
            "initialized": ai_initialized,
            "client_mode": client_mode,
        },
        "local_llm": local_llm,
    }


# ---------------------------------------------------------------------------
# Local LLM endpoints — bridge between llama-server sidecar and matrx-ai
# ---------------------------------------------------------------------------


class LocalLlmConnectRequest(BaseModel):
    port: int
    model_name: str


@router.post("/local-llm/connect")
async def local_llm_connect(req: LocalLlmConnectRequest) -> dict[str, Any]:
    """Register a running local llama-server with the matrx-ai agent pipeline.

    Called by the Tauri frontend when llama-server emits llm-server-ready.
    """
    from app.services.ai.local_llm_registry import set_local_llm, get_local_llm_status

    logger.info(
        "[chat_routes /local-llm/connect] Connecting local LLM: port=%d, model=%s",
        req.port,
        req.model_name,
    )

    success = set_local_llm(req.port, req.model_name)
    status = get_local_llm_status()

    return {"status": "ok" if success else "error", **status}


@router.post("/local-llm/disconnect")
async def local_llm_disconnect() -> dict[str, Any]:
    """Deregister the local LLM — called when llama-server stops."""
    from app.services.ai.local_llm_registry import clear_local_llm, get_local_llm_status

    logger.info("[chat_routes /local-llm/disconnect] Disconnecting local LLM")
    clear_local_llm()
    return {"status": "ok", **get_local_llm_status()}


@router.get("/local-llm/status")
async def local_llm_status() -> dict[str, Any]:
    """Return current local LLM registration status."""
    from app.services.ai.local_llm_registry import get_local_llm_status

    return get_local_llm_status()

