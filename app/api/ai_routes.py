"""The unified /ai surface — host-owned AI orchestration HTTP layer.

matrx-ai 0.3.0 removed its packaged HTTP routers (they moved into aidream's
server app). This module is the desktop's own /ai surface, byte-compatible
with aidream's so matrx-frontend can transparently point a conversation at
``http://127.0.0.1:22140`` instead of ``https://server.app.matrxserver.com``:

    POST /ai/agents/{agent_id}                 (alias /ai/agent/{agent_id})
    POST /ai/conversations/{conversation_id}   (alias /ai/conversation/{id})
    POST /ai/conversations/{conversation_id}/resume
    POST /ai/chat

Wire contract (matches aidream / matrx-frontend run-ai-stream.ts +
process-stream.ts):
  * NDJSON stream of matrx-connect events — phase, init, chunk,
    reasoning_chunk, reasoning, tool_event, data (conversation_id, ...),
    warning, completion, error, heartbeat (~every HEARTBEAT_INTERVAL s),
    end. Emitted by matrx_connect.emitters.StreamEmitter — the same class
    aidream uses, so the vocabulary cannot drift.
  * ``X-Conversation-ID`` / ``X-Request-ID`` response headers.
  * Error envelope ``{error, message, details}`` (app/api/error_envelope.py)
    with aidream's status semantics: 409 conversation_already_exists,
    404 conversation_not_found / agent not found, 422 validation & tool
    injection failures, resume 404 user_request_not_found / 409
    not_resumable.

Mounting: ``build_ai_app()`` is mounted at ``/ai`` (the canonical
aidream-compatible surface), ``/v2/ai`` (the current frontend spine), and
``/chat/ai`` (the desktop UI's historical path) — one app, three mounts, zero
drift. See app/main.py Phase 1b.

Auth model:
  * The engine's outer AuthMiddleware (app/api/auth.py) already gates
    /ai/*: bearer presence on direct loopback (the socket is the trust
    boundary), cryptographically-verified Supabase identity + instance
    ownership over the Cloudflare tunnel.
  * The per-request ``AIContextMiddleware`` here (pure ASGI, mirrors
    matrx-connect's AuthMiddleware shape) builds the matrx-connect
    AppContext: a fresh StreamEmitter, request_id (honoring a valid
    ``X-Request-Id``), and the user identity — the JWT ``sub`` claim
    decoded WITHOUT signature verification (the outer middleware already
    established trust; the engine deliberately holds no JWT secret — see
    the SUPABASE_JWT_SECRET note in CLAUDE.md), falling back to the stored
    auth_tokens user_id (single-user desktop), then to ``local-user`` for
    tokenless TEST_MODE callers.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse

from app.common.system_logger import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger()

router = APIRouter()

# Heartbeat cadence for the stream emitter. The frontend's liveness deadline
# is 30s (~3 missed beats at 10s); aidream ships 5s. Env-overridable so the
# smoke tests can shrink it and assert a heartbeat without waiting seconds.
_HEARTBEAT_INTERVAL = float(os.getenv("MATRX_AI_HEARTBEAT_INTERVAL", "5.0"))


# ---------------------------------------------------------------------------
# Request models — mirror aidream's (subset the local runtime honors).
# Unknown fields are IGNORED (pydantic default) exactly like aidream's
# ScopedRequest-based models, so a full frontend body never 422s here.
# ---------------------------------------------------------------------------


# NOTE: the pydantic models below need the real types at class-definition
# time; the import-order fix must run before this module's import completes.
import matrx_ai.orchestrator  # noqa: E402, F401  (import-order fix, see engine.py)
from matrx_ai.capabilities import ClientContext  # noqa: E402
from matrx_ai.config.llm_params import LLMParams  # noqa: E402
from matrx_ai.tools.specs import ToolSpec  # noqa: E402


class LocalScopedRequest(BaseModel):
    organization_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    scope_ids: list[str] | None = None
    source_app: str | None = None
    source_feature: str | None = None


class LocalAgentStartRequest(LocalScopedRequest):
    """Mirror of aidream AgentStartRequest (local-honored subset)."""

    user_input: str | list[dict[str, Any]] | None = None
    variables: dict[str, Any] | None = None
    config_overrides: LLMParams | None = None
    stream: bool = True
    debug: bool = False

    tools: list[ToolSpec] = []
    tools_replace: list[ToolSpec] | None = None
    client: ClientContext | None = None

    # REQUIRED, mirroring aidream's ConversationStartRequest: the client mints
    # conversation_id (its correlation handle), is_new asserts what to do with
    # it, and store is the ONE ephemeral signal.
    conversation_id: str
    is_new: bool
    store: bool
    is_version: bool = False

    max_iterations: int = 100
    max_retries_per_iteration: int = 2

    # Accepted-but-unused aidream fields ride through pydantic's default
    # ignore-extras behavior (client, user, sandbox, context, memory, ...).


class LocalConversationContinueRequest(LocalScopedRequest):
    """Mirror of aidream ConversationContinueRequest (local-honored subset)."""

    user_input: str | list[dict[str, Any]] | None = None
    retry: bool = False
    config_overrides: LLMParams | None = None
    stream: bool = True
    debug: bool = False

    tools: list[ToolSpec] = []
    tools_replace: list[ToolSpec] | None = None
    client: ClientContext | None = None

    store: bool = True
    max_iterations: int = 100
    max_retries_per_iteration: int = 2


class LocalResumeRequest(LocalScopedRequest):
    """Mirror of aidream ResumeRequest."""

    user_request_id: str
    config_overrides: LLMParams | None = None
    debug: bool = False
    tools: list[ToolSpec] = []
    tools_replace: list[ToolSpec] | None = None
    client: ClientContext | None = None


class LocalChatRequest(LocalScopedRequest):
    """Mirror of aidream ChatRequest (local-honored subset)."""

    model_config = ConfigDict(extra="allow")

    ai_model_id: str
    messages: list[dict[str, Any]]
    agent_id: str | None = None
    agent_version_id: str | None = None

    # REQUIRED — see LocalAgentStartRequest above.
    conversation_id: str
    is_new: bool
    store: bool

    system_instruction: str | dict[str, Any] | None = None
    variables: dict[str, Any] | None = None
    config_overrides: LLMParams | None = None

    tools: list[ToolSpec] = []
    tools_replace: list[ToolSpec] | None = None
    client: ClientContext | None = None

    metadata: dict[str, Any] | None = None

    max_iterations: int = 100
    max_retries_per_iteration: int = 2
    stream: bool = True
    debug: bool = False


# ---------------------------------------------------------------------------
# Per-request AppContext middleware (pure ASGI)
# ---------------------------------------------------------------------------


def _decode_jwt_sub(token: str) -> str | None:
    """Extract ``sub`` from a JWT WITHOUT verifying the signature.

    Trust was already established by the outer AuthMiddleware (loopback
    boundary, or verified-owner over the tunnel). The engine deliberately
    holds no JWT secret — see CLAUDE.md.
    """
    try:
        import jwt as pyjwt

        claims = pyjwt.decode(token, options={"verify_signature": False})
        sub = claims.get("sub")
        return str(sub) if sub else None
    except Exception:
        return None


async def _resolve_user_id(bearer: str | None) -> str:
    """Resolve the request's user identity (single-user desktop posture)."""
    bearer_sub = _decode_jwt_sub(bearer) if bearer else None
    try:
        from app.services.local_db.repositories import TokenRepo

        row = await TokenRepo().get()
        if row and row.get("user_id"):
            persisted_user_id = str(row["user_id"])
            if bearer_sub and bearer_sub != persisted_user_id:
                raise DesktopOwnerMismatchError
            return persisted_user_id
    except DesktopOwnerMismatchError:
        raise
    except Exception:
        logger.debug("[ai_routes] stored-token user_id lookup failed", exc_info=True)
    if bearer_sub:
        return bearer_sub
    # Tokenless/opaque-token caller on loopback (TEST_MODE, local API key).
    # Deterministic single-user identity — the local store is per-machine.
    return "local-user"


class DesktopOwnerMismatchError(Exception):
    """A request JWT belongs to someone other than this desktop owner."""


async def _adopt_request_jwt(bearer: str | None, user_id: str) -> None:
    """Refresh engine-owned auth state from a valid JWT already in use."""
    if not bearer or user_id == "local-user":
        return
    try:
        import jwt as pyjwt

        claims = pyjwt.decode(bearer, options={"verify_signature": False})
        if str(claims.get("sub") or "") != user_id:
            return
        expires_at = int(claims.get("exp") or 0)
        if expires_at <= int(time.time()):
            return

        from app.services.ai.engine import set_jwt_cache
        from app.services.local_db.repositories import TokenRepo

        repo = TokenRepo()
        existing = await repo.get()
        existing_user_id = str((existing or {}).get("user_id") or "")
        if existing_user_id and existing_user_id != user_id:
            logger.warning(
                "[ai_routes] refused request JWT adoption for a different owner "
                "(persisted_user_id=%s request_user_id=%s)",
                existing_user_id,
                user_id,
            )
            return
        if (
            existing
            and existing.get("access_token") == bearer
            and not repo.is_expired(existing)
        ):
            set_jwt_cache(bearer)
            return
        set_jwt_cache(bearer)
        await repo.save(
            access_token=bearer,
            user_id=user_id,
            refresh_token=(existing or {}).get("refresh_token")
            if existing_user_id == user_id
            else None,
            expires_at=expires_at,
        )
        logger.info(
            "[ai_routes] refreshed engine auth state from authenticated AI request "
            "(user_id=%s)",
            user_id,
        )
    except Exception:
        logger.warning(
            "[ai_routes] could not refresh engine auth state from request JWT",
            exc_info=True,
        )


class AIContextMiddleware:
    """Sets the matrx-connect AppContext (emitter + identity) per request.

    MUST be ASGI-level (not a route dependency): the streaming generator and
    the work task it spawns inherit ContextVars from the task that iterates
    the response body — only middleware wraps that far.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from matrx_connect.context.app_context import (
            AppContext,
            clear_app_context,
            set_app_context,
        )
        from matrx_connect.emitters.stream_emitter import StreamEmitter

        request = Request(scope, receive)

        auth = request.headers.get("authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        if not bearer:
            bearer = (request.query_params.get("token") or "").strip() or None

        client_request_id = (request.headers.get("x-request-id") or "").strip()
        try:
            uuid.UUID(client_request_id)
            request_id = client_request_id
        except (ValueError, TypeError):
            request_id = str(uuid.uuid4())

        try:
            user_id = await _resolve_user_id(bearer)
        except DesktopOwnerMismatchError:
            response = JSONResponse(
                status_code=403,
                content={
                    "error": "desktop_owner_mismatch",
                    "message": "This desktop is signed in as a different user.",
                },
            )
            await response(scope, receive, send)
            return
        await _adopt_request_jwt(bearer, user_id)

        ctx = AppContext(
            emitter=StreamEmitter(debug=False, heartbeat_interval=_HEARTBEAT_INTERVAL),
            user_id=user_id,
            auth_type="token" if bearer else "anonymous",
            is_authenticated=bool(bearer),
            token=bearer,
            request_id=request_id,
            route=request.url.path,
            # Conversation provenance uses the canonical hyphenated producer
            # registered by AIDream. The underscore spelling belongs only to
            # matrx-ai's internal client-host/tool-source identity.
            source_app="matrx-local",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        request.scope.setdefault("state", {})
        # Starlette exposes scope["state"] via request.state on re-wraps.
        scope["state"]["context"] = ctx

        token = set_app_context(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            clear_app_context(token)


async def context_dep(request: Request) -> Any:
    """FastAPI dependency — the AppContext set by AIContextMiddleware."""
    from fastapi import HTTPException, status

    ctx = getattr(request.state, "context", None)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "context_missing",
                "message": "AIContextMiddleware did not set the request context.",
            },
        )
    return ctx


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}")  # stream
@router.post("/agent/{agent_id}")  # stream (aidream alias)
async def start_agent(
    agent_id: str,
    request: LocalAgentStartRequest,
    ctx: Any = Depends(context_dep),
    is_version: bool = Query(False),
):
    from matrx_connect.streaming import create_prepared_streaming_response

    from app.services.ai.local_ai_task import prepare_agent_start, run_local_ai_task

    request.is_version = request.is_version or is_version
    return await create_prepared_streaming_response(
        ctx,
        lambda: prepare_agent_start(agent_id, request, ctx),
        run_local_ai_task,
        initial_message="Initializing Matrx Agent...",
        debug_label="Agent",
    )


@router.post("/conversations/{conversation_id}")  # stream
@router.post("/conversation/{conversation_id}")  # stream (aidream alias)
async def continue_conversation(
    conversation_id: str,
    request: LocalConversationContinueRequest,
    ctx: Any = Depends(context_dep),
):
    from matrx_connect.streaming import create_prepared_streaming_response

    from app.services.ai.local_ai_task import (
        prepare_conversation_continue,
        run_local_ai_task,
    )

    return await create_prepared_streaming_response(
        ctx,
        lambda: prepare_conversation_continue(conversation_id, request, ctx),
        run_local_ai_task,
        initial_message="Initializing Matrx Conversation...",
        debug_label="Conversation",
    )


@router.post("/conversations/{conversation_id}/resume")
@router.post("/conversation/{conversation_id}/resume")
async def resume_conversation(
    conversation_id: str,
    request: LocalResumeRequest,
    ctx: Any = Depends(context_dep),
):
    """Resume after client-delegated tool calls — structurally supported,
    semantically terminal on the local runtime.

    Every local tool executes IN-PROCESS through the engine's dispatcher;
    the local runtime never delegates a tool call to the client, so a run
    never suspends into a resumable state. The aidream 409/404 vocabulary
    is preserved so the frontend's resume handling stays uniform:

      * unknown user_request_id           → 404 user_request_not_found
      * anything else (always terminal)   → 409 not_resumable
    """
    from fastapi import HTTPException, status

    from app.services.local_db.database import get_db

    db = get_db()
    row = await db.fetchone(
        "SELECT id, status FROM chat.user_request WHERE id = ?",
        (request.user_request_id,),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "user_request_not_found",
                "message": (
                    f"user_request_not_found: no user_request "
                    f"{request.user_request_id!r} for conversation "
                    f"{conversation_id!r} on this local engine."
                ),
            },
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "not_resumable",
            "message": (
                f"not_resumable: request {request.user_request_id!r} is "
                f"{dict(row).get('status', 'terminal')!r}. The local runtime "
                "executes every tool in-process and never suspends a run for "
                "client-delegated tools, so there is nothing to resume."
            ),
        },
    )


@router.post("/chat")  # stream
async def chat(
    request: LocalChatRequest,
    ctx: Any = Depends(context_dep),
):
    from matrx_connect.streaming import create_prepared_streaming_response

    from app.services.ai.local_ai_task import prepare_chat, run_local_ai_task

    return await create_prepared_streaming_response(
        ctx,
        lambda: prepare_chat(request, ctx),
        run_local_ai_task,
        initial_message="Initializing Matrx Chat...",
        debug_label="Chat",
    )


@router.get("/status")
async def ai_surface_status() -> dict[str, Any]:
    """Cheap non-streaming probe: is the host-owned AI surface live?"""
    from app.services.ai.engine import (
        is_client_mode,
        is_initialized,
        supports_agent_execution,
        tools_loaded,
    )

    capabilities = [
        "chat_execution_v1",
        "conversation_execution_v1",
        "chat_execution_v2",
        "conversation_execution_v2",
    ]
    if supports_agent_execution():
        capabilities.extend(["agent_execution_v1", "agent_execution_v2"])

    return {
        "surface": "local-ai",
        "initialized": is_initialized(),
        "client_mode": is_client_mode(),
        "tools_loaded": tools_loaded(),
        "heartbeat_interval": _HEARTBEAT_INTERVAL,
        "capabilities": capabilities,
        "endpoints": [
            "POST /agents/{agent_id}",
            "POST /conversations/{conversation_id}",
            "POST /conversations/{conversation_id}/resume",
            "POST /chat",
        ],
    }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_ai_app() -> "FastAPI":
    """Build the /ai sub-application (mounted at /ai AND /chat/ai).

    CORS is handled by the MAIN app's CORSMiddleware (app middleware wraps
    mounted sub-apps); adding a second CORSMiddleware here would emit
    duplicate Access-Control-* headers and break browsers. The main app's
    CORS exposes X-Conversation-ID / X-Request-ID (see main.py).
    """
    from fastapi import FastAPI

    from app.api.error_envelope import install_envelope_handlers

    ai_app = FastAPI(
        title="Matrx Local AI Surface",
        description="Host-owned AI orchestration surface (matrx-ai client host)",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
    )
    install_envelope_handlers(ai_app)
    ai_app.add_middleware(AIContextMiddleware)
    ai_app.include_router(router)
    logger.info(
        "[ai_routes] host-owned /ai surface built (heartbeat=%.1fs)",
        _HEARTBEAT_INTERVAL,
    )
    return ai_app
