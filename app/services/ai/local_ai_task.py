"""Host-owned AI execution task + request prep for the /ai surface.

matrx-ai 0.3.0 ships no HTTP layer — the desktop owns its AI surface
(app/api/ai_routes.py is the HTTP boundary; this module is the service
layer). It mirrors aidream's split:

  aidream/services/ai_execution/ai_task.py   → run_local_ai_task
  aidream/services/ai_execution/agent_run.py → prepare_agent_start
  .../conversation_context/continue_conversation.py
                                             → prepare_conversation_continue
  aidream/services/ai_execution/chat_run.py  → prepare_chat

Deliberately smaller than aidream's pipeline: no observational memory, no
scope bindings, no picklists, no sandbox arming, no block mode — those are
server concerns. What IS byte-compatible is the wire: the same
matrx-connect StreamEmitter event vocabulary (phase / init / chunk /
reasoning_chunk / tool_event / data / completion / error / heartbeat / end),
the same conversation-gate semantics (409 conversation_already_exists /
404 conversation_not_found), and the same completion payload shapes
(matrx_connect.context.operations.UserRequestResult) — so matrx-frontend's
process-stream.ts consumes a local turn identically to an aidream turn.

Everything here runs against the client-host seams wired at startup
(SQLiteConversationStore, SqliteModelCatalog, SqliteKeyResolver, the local
ToolRegistry with 108 OS tools) — see app/services/ai/engine.py.

Saved agents are loaded only through matrx-ai's ``ExecutionAgentSource`` seam.
The local ``agents`` table remains picker metadata and is never interpreted as
an executable definition. Complete definitions are fetched through the
authenticated AIDream endpoint and cached opaquely in SQLite; matrx-ai owns the
single definition -> AgentConfig -> UnifiedConfig conversion.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

# Import-order fix (matrx-ai 0.3.0 providers ↔ orchestrator circular import):
# the orchestrator must be imported before anything that pulls providers.
# Tracked in .matrx/AGENT_TASKS.md; remove when upstream makes it lazy.
import matrx_ai.orchestrator  # noqa: F401  (import-order fix, see above)

from fastapi import HTTPException, status
from matrx_ai.config import LLMParams, UnifiedConfig
from matrx_ai.orchestrator.requests import CompletedRequest
from matrx_connect.context.app_context import AppContext, set_app_context
from matrx_connect.context.events import WarningPayload
from matrx_connect.context.operations import (
    AggregatedUsageResult,
    CompletionPayload,
    InitPayload,
    TimingStatsResult,
    ToolCallStatsResult,
    UserRequestResult,
)
from matrx_connect.emitters.stream_emitter import StreamEmitter

from app.common.system_logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Local model ownership
# ---------------------------------------------------------------------------


def bind_active_local_model(config: UnifiedConfig) -> str:
    """Make the desktop-owned llama-server the only model for a local run.

    Saved agent definitions intentionally retain their cloud model and offering
    metadata so they remain portable. Once the web client routes the run to
    Matrx Local, however, execution ownership has changed: the active local
    llama-server is authoritative. Letting the agent model or a frontend model
    override survive here can call a paid cloud provider from the desktop or
    fail on a cloud-only offering UUID.
    """
    from app.services.ai.local_llm_registry import resolve_local_llm_model

    target = resolve_local_llm_model()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "local_model_not_available",
                "message": (
                    "Matrx Local received this agent run, but its Python engine "
                    "is not connected to the llama-server currently used by the "
                    "desktop app. Start a local model or wait for engine "
                    "registration to complete. No cloud provider was called."
                ),
            },
        )

    canonical = str(target["canonical_model_name"])
    previous_model = str(getattr(config, "model", "") or "")
    config.model = canonical
    # Offering pins are meaningful only inside the server's ai.offering graph.
    # A synthetic local runtime model has its own catalog profile and must never
    # inherit a cloud offering or a prior overload reroute.
    config.offering_id = None
    config.runtime_offering_id = None
    config.matrx_model_name = None
    logger.info(
        "[local_ai_task] bound local execution model %s (agent/request model was %s)",
        canonical,
        previous_model or "unset",
    )
    return canonical


# ---------------------------------------------------------------------------
# Conversation gate — aidream resolve_conversation semantics over local SQLite
# ---------------------------------------------------------------------------


def _validate_uuid(value: str, field: str = "conversation_id") -> str:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_uuid",
                "message": f"{field} must be a valid UUID, got {value!r}.",
            },
        ) from None
    return value


async def conversation_exists(conversation_id: str) -> bool:
    from app.services.local_db.repositories import ConversationsRepo

    return await ConversationsRepo().get(conversation_id) is not None


async def resolve_conversation_gate(
    conversation_id: str,
    is_new: bool,
    store: bool,
) -> tuple[str, bool]:
    """Local mirror of aidream's ``resolve_conversation`` behavior matrix.

    All three inputs are REQUIRED, exactly as on the server: the CLIENT mints
    ``conversation_id`` (its correlation handle), ``is_new`` asserts what to do
    with it, and ``store`` is the ONE ephemeral signal. The old
    ``conversation_id=None`` + ``is_new=False`` shape — "this is not a new
    conversation, and I won't tell you which one" — is rejected at the request
    model and cannot reach here.

    Returns ``(effective_conversation_id, skip_persistence)``. Raises the
    same 409/404/422 HTTPExceptions (same ``code`` strings) aidream raises,
    so the frontend's error handling is transport-identical.
    """
    effective_id = _validate_uuid(conversation_id)

    # store=False → ephemeral. Nothing is read, nothing is written; the
    # caller's id is echoed back for correlation only.
    if not store:
        return effective_id, True

    exists = await conversation_exists(effective_id)

    if is_new:
        if exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "conversation_already_exists",
                    "message": (
                        f"A conversation with id={effective_id!r} already exists. "
                        "Pass is_new=false to continue it, or mint a new "
                        "conversation_id."
                    ),
                },
            )
        # Creation itself is owned by the executor's conversation gate
        # (store.ensure_conversation_exists) — single-writer, idempotent.
        return effective_id, False

    # is_new=False → the conversation must exist locally.
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "conversation_not_found",
                "message": (
                    f"No conversation found with id={effective_id!r}. "
                    "Pass is_new=true to create a new conversation with this id."
                ),
            },
        )
    return effective_id, False


# ---------------------------------------------------------------------------
# Tool merge — request tools/tools_replace against the LOCAL registry
# ---------------------------------------------------------------------------


def _registry():
    from matrx_ai.tools.registry import ToolRegistry

    return ToolRegistry.get_instance()


def _local_desktop_capability_state() -> dict[str, Any]:
    """Return authoritative runtime state for this desktop engine process."""
    try:
        from app.api.routes import _APP_VERSION

        engine_version = str(_APP_VERSION)
    except Exception:
        engine_version = ""

    try:
        from app.services.cloud_sync.instance_manager import get_instance_manager

        instance_id = get_instance_manager().instance_id
    except Exception:
        instance_id = ""

    try:
        from app.api.tunnel_state import get_tunnel_snapshot

        tunnel_state = "active" if get_tunnel_snapshot().get("active") else "none"
    except Exception:
        tunnel_state = "none"

    return {
        "platform": sys.platform,
        "engine_version": engine_version,
        "instance_id": instance_id,
        "tunnel_state": tunnel_state,
    }


def _with_local_desktop_capability(client: Any, *, advertise: bool = True) -> Any:
    """Add this execution host to the request's client capability envelope.

    A browser normally derives ``desktop-native`` from cloud presence. That
    signal can briefly disappear after a desktop restart, and it is not needed
    once a request is already executing inside Matrx Local: this process is the
    authoritative proof that the desktop runtime is available.
    """
    from matrx_ai.capabilities import ClientContext

    context = ClientContext.model_validate(client or {})
    capabilities = list(context.capabilities)
    if advertise:
        capabilities = list(dict.fromkeys([*capabilities, "desktop-native"]))
    state = {name: dict(payload) for name, payload in context.state.items()}
    if advertise or "desktop-native" in capabilities:
        # This process is the execution host. Browser routing hints and
        # previously loaded categories describe a different runtime and must
        # never leak into this host's in-process tool resolution.
        state["desktop-native"] = _local_desktop_capability_state()
    return context.model_copy(
        update={
            "capabilities": capabilities,
            "state": state,
        }
    )


def _apply_request_scope(ctx: AppContext, request: Any) -> AppContext:
    overrides = {
        name: value
        for name in (
            "organization_id",
            "project_id",
            "task_id",
            "source_app",
            "source_feature",
        )
        if (value := getattr(request, name, None)) is not None
    }
    scope_ids = getattr(request, "scope_ids", None)
    if scope_ids is not None:
        overrides["metadata"] = {**ctx.metadata, "scope_ids": list(scope_ids)}
    return ctx.with_overrides(**overrides) if overrides else ctx


async def apply_request_tools(
    config: UnifiedConfig,
    ctx: AppContext,
    tools: list[Any],
    tools_replace: list[Any] | None,
    *,
    excluded: list[str] | None = None,
    client: Any = None,
) -> AppContext:
    """Apply the frontend's unified tool injection to ``config``.

    ``tools_replace`` (when present) becomes the ENTIRE tool set for the
    turn; ``tools`` merges additively. Uses matrx-ai's own
    ``merge_request_tools`` primitive so spec handling (registered / inline
    / delegate) matches aidream. A registered spec whose name is unknown to
    the LOCAL registry is a 422 — loud, never a silently-absent tool.
    """
    from matrx_ai.tools.merge import merge_request_tools

    specs = list(tools_replace if tools_replace is not None else tools)
    client = _with_local_desktop_capability(
        client,
        advertise=tools_replace is None,
    )
    amendments = getattr(client, "amendments", None)
    if tools_replace is None and amendments is not None:
        specs = [*getattr(amendments, "add", []), *specs]
        excluded = [*(excluded or []), *getattr(amendments, "remove", [])]
    if tools_replace is None and getattr(client, "capabilities", None):
        from matrx_ai.capabilities import (
            CapabilityResolutionError,
            resolve_client_capabilities,
        )

        try:
            default_specs, optional_specs, capability_payloads = (
                resolve_client_capabilities(
                    client,
                    is_authenticated=ctx.is_authenticated,
                )
            )
        except CapabilityResolutionError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "client_capability_resolution_failed",
                    "message": f"Client capability resolution failed: {exc}",
                },
            ) from exc

        requested = {
            str(getattr(spec, "tool_id", None) or getattr(spec, "name", ""))
            for spec in specs
        } | {str(name) for name in (config.tools or [])}
        selected_optional = [
            spec
            for spec in optional_specs
            if str(getattr(spec, "tool_id", None) or getattr(spec, "name", ""))
            in requested
        ]
        specs = [*default_specs, *selected_optional, *specs]

        if capability_payloads:
            metadata = dict(ctx.metadata)
            canonical = dict(metadata.get("client_capabilities_payloads") or {})
            for name, payload in capability_payloads.items():
                canonical[name] = payload.model_dump(exclude_none=True)
            metadata["client_capabilities_payloads"] = canonical
            ctx = ctx.with_overrides(metadata=metadata)
            set_app_context(ctx)

    agent_specs = [spec for spec in specs if getattr(spec, "kind", None) == "agent"]
    if agent_specs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "remote_agent_tool_projection_unsupported",
                "message": (
                    "Agent-as-tool request projections are not yet executable "
                    "inside the local model host. Use registered local/server "
                    "tools for this turn."
                ),
            },
        )
    if tools_replace is not None:
        config.tools = []
        config.custom_tools = []
    registry = _registry()
    requested_names = {
        str(name)
        for name in [
            *(config.tools or []),
            *(
                (getattr(spec, "tool_id", None) or getattr(spec, "name", None))
                for spec in specs
                if getattr(spec, "kind", "registered") == "registered"
            ),
        ]
        if name
    }
    bundle_names = sorted(
        name for name in requested_names if name.startswith("bundle:list_")
    )
    if bundle_names:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "remote_dynamic_bundle_unsupported",
                "message": (
                    "Dynamic server tool bundles cannot mutate a local model's "
                    f"active toolset: {', '.join(bundle_names)}. Attach the "
                    "bundle member tools directly to this agent."
                ),
            },
        )
    missing_before_refresh = {
        name for name in requested_names if registry.get(name) is None
    }
    if missing_before_refresh:
        from app.services.aidream.client import AIDreamError, AIDreamOfflineError
        from app.services.ai.remote_tool_bridge import get_remote_tool_bridge

        try:
            await get_remote_tool_bridge().ensure(missing_before_refresh)
        except (AIDreamError, AIDreamOfflineError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "remote_tool_registry_unavailable",
                    "message": (
                        "The agent requested server-owned tools, but AIDream is "
                        "currently unreachable. The desktop will retry when the "
                        "server connection is available."
                    ),
                },
            ) from exc

    def _dump(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json", exclude_none=True)
        return value

    from app.services.ai.remote_tool_bridge import REMOTE_TOOL_CONTEXT_KEY

    ctx = ctx.with_overrides(
        metadata={
            **ctx.metadata,
            REMOTE_TOOL_CONTEXT_KEY: {
                "tools": [_dump(spec) for spec in tools],
                "tools_replace": (
                    [_dump(spec) for spec in tools_replace]
                    if tools_replace is not None
                    else None
                ),
                "client": _dump(client),
                "scope_ids": ctx.metadata.get("scope_ids"),
            },
        }
    )
    set_app_context(ctx)

    unknown = [
        getattr(s, "name", None)
        for s in specs
        if getattr(s, "kind", "registered") == "registered"
        and registry.get(getattr(s, "tool_id", None) or getattr(s, "name", "")) is None
    ]
    unknown = [n for n in unknown if n]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "tool_not_found",
                "message": (
                    "Tool(s) are not registered locally or in AIDream: "
                    f"{', '.join(sorted(set(unknown)))}."
                ),
            },
        )

    from matrx_ai.tools.models import ToolType

    remote_names = sorted(
        name
        for name in requested_names
        if (
            (definition := registry.get(name)) is not None
            and definition.tool_type == ToolType.EXTERNAL_HANDLER
        )
    )
    if remote_names and not (ctx.agent_id or ctx.agent_version_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "remote_agent_identity_required",
                "message": (
                    "Server-owned tools require a saved agent identity for "
                    f"authorization: {', '.join(remote_names)}. Send agent_id "
                    "or agent_version_id, or use the agent execution route."
                ),
            },
        )

    # Local engine: every registered tool executes in-process (no client
    # delegation surface), so active_executors stays empty.
    new_ctx = merge_request_tools(config, ctx, specs, excluded=excluded or [])
    set_app_context(new_ctx)
    return new_ctx


# ---------------------------------------------------------------------------
# Prep — agent start (turn 1) / conversation continue (turn 2+) / chat
# ---------------------------------------------------------------------------


async def prepare_agent_start(
    agent_id: str,
    request: Any,  # LocalAgentStartRequest (app/api/ai_routes.py)
    ctx: AppContext,
) -> tuple[AppContext, UnifiedConfig, int, int]:
    """Pre-stream prep for POST /ai/agents/{agent_id}.

    Continue-mode short-circuit mirrors aidream: when the conversation
    already exists locally and the caller isn't asserting is_new=True, the
    persisted conversation state is the source of truth and the URL
    agent_id is informational.
    """
    _validate_uuid(agent_id, "agent_version_id" if request.is_version else "agent_id")
    ctx = _apply_request_scope(ctx, request)

    from app.services.ai.engine import supports_agent_execution

    if not supports_agent_execution():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "agent_execution_unavailable",
                "message": (
                    "This Matrx Local build does not support canonical saved-agent "
                    "definitions. Route the turn through AIDream."
                ),
            },
        )

    # Continue only a REAL, persisted conversation. An ephemeral run
    # (store=false) has no row by design and must never be routed here.
    if (
        not request.is_new
        and request.store
        and await conversation_exists(request.conversation_id)
    ):
        return await prepare_conversation_continue(
            request.conversation_id, request, ctx, agent_id_hint=agent_id
        )

    conversation_id, skip_persistence = await resolve_conversation_gate(
        request.conversation_id, request.is_new, request.store
    )

    from matrx_ai.db.agx_manager import agx

    agent_config = await agx.load_for_execution(
        agent_id,
        is_version=request.is_version,
    )
    overrides = request.config_overrides
    config = agent_config.config
    if request.variables:
        config.replace_variables(request.variables)
    if overrides:
        config.apply_overrides(overrides)
    bind_active_local_model(config)

    metadata = {
        **ctx.metadata,
        "agent_name": agent_config.name,
        "initial_model": config.model,
    }
    if agent_config.matrx_actions:
        metadata["matrx_actions"] = agent_config.matrx_actions

    ctx = ctx.with_overrides(
        conversation_id=conversation_id,
        debug=request.debug,
        initial_variables=dict(request.variables or {}),
        initial_overrides=(
            overrides.model_dump(exclude_none=True)
            if isinstance(overrides, LLMParams)
            else (overrides or {})
        ),
        store=bool(ctx.store and request.store and not skip_persistence),
        source_feature=ctx.source_feature or "agent",
        metadata=metadata,
        **(
            {"agent_version_id": agent_id}
            if request.is_version
            else {"agent_id": agent_id}
        ),
    )
    set_app_context(ctx)

    if not ctx.store:
        config.store = False
    config.stream = request.stream

    ctx = await apply_request_tools(
        config,
        ctx,
        request.tools,
        request.tools_replace,
        excluded=agent_config.excluded_tools,
        client=request.client,
    )
    if request.user_input is not None:
        config.append_or_extend_user_input(request.user_input)

    return ctx, config, request.max_iterations, request.max_retries_per_iteration


async def prepare_conversation_continue(
    conversation_id: str,
    request: Any,  # LocalConversationContinueRequest | LocalAgentStartRequest
    ctx: AppContext,
    agent_id_hint: str | None = None,
) -> tuple[AppContext, UnifiedConfig, int, int]:
    """Pre-stream prep for POST /ai/conversations/{conversation_id}.

    The persisted local conversation state (SQLiteConversationStore via
    ConversationResolver) is the source of truth. 404s with aidream's
    ``conversation_not_found`` code when the row is missing.
    """
    _validate_uuid(conversation_id)
    ctx = _apply_request_scope(ctx, request)

    if not await conversation_exists(conversation_id):
        try:
            from app.services.chat_sync import get_chat_sync_engine

            await get_chat_sync_engine().hydrate_conversation(conversation_id)
        except Exception:
            logger.warning(
                "[local_ai_task] targeted conversation hydration failed for %s",
                conversation_id,
                exc_info=True,
            )

    if not await conversation_exists(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "conversation_not_found",
                "message": f"No conversation found with id={conversation_id!r}.",
            },
        )

    ctx = ctx.with_overrides(
        conversation_id=conversation_id,
        debug=request.debug,
        source_feature=ctx.source_feature or "agent",
    )
    set_app_context(ctx)

    user_input = getattr(request, "user_input", None)
    retry = bool(getattr(request, "retry", False))
    if user_input is None and not retry:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "user_input_required",
                "message": (
                    "Send user_input, or retry=true to re-run the conversation's "
                    "persisted state."
                ),
            },
        )

    from app.services.local_db.repositories import ConversationsRepo

    conv_row = await ConversationsRepo().get(conversation_id)
    version_id = (conv_row or {}).get("agent_version_id") or None
    persisted_agent_id = (conv_row or {}).get("agent_id") or agent_id_hint
    source_id = version_id or persisted_agent_id
    excluded_tools: list[str] = []

    if source_id:
        ctx = ctx.with_overrides(
            **(
                {"agent_version_id": source_id}
                if version_id
                else {"agent_id": source_id}
            )
        )
        set_app_context(ctx)

    if source_id:
        # Rebuild from the exact canonical definition on every continuation,
        # then append durable history. A failed first turn and a process restart
        # therefore cannot erase the agent's prompt, model, or tools.
        from matrx_ai.db.agx_manager import agx

        from app.services.ai.conversation_handler import get_conversation_store

        agent_config = await agx.load_for_execution(
            source_id,
            is_version=bool(version_id),
        )
        config = agent_config.config
        excluded_tools = agent_config.excluded_tools
        persisted = UnifiedConfig.from_dict(
            await get_conversation_store().get_conversation_config(conversation_id)
        )
        if persisted.model:
            config.model = persisted.model
        config.messages.extend(persisted.messages)
        if user_input is not None:
            config.append_or_extend_user_input(user_input)
        if request.config_overrides:
            config.apply_overrides(request.config_overrides)
    else:
        from matrx_ai.agents.resolver import ConversationResolver

        config = await ConversationResolver.from_conversation_id(
            conversation_id,
            user_input=user_input,
            config_overrides=request.config_overrides,
        )

    # A continuation may carry a cloud model persisted by an older desktop
    # build. Local ownership is re-applied every turn so stale conversation
    # state cannot escape to a provider.
    bind_active_local_model(config)

    config.stream = getattr(request, "stream", True)
    if not ctx.store:
        config.store = False

    ctx = await apply_request_tools(
        config,
        ctx,
        getattr(request, "tools", []),
        getattr(request, "tools_replace", None),
        excluded=excluded_tools,
        client=getattr(request, "client", None),
    )

    return ctx, config, request.max_iterations, request.max_retries_per_iteration


async def prepare_chat(
    request: Any,  # LocalChatRequest (app/api/ai_routes.py)
    ctx: AppContext,
) -> tuple[AppContext, UnifiedConfig, int, int]:
    """Pre-stream prep for POST /ai/chat — direct model chat (no saved agent).

    Same conversation-gate matrix as the agent path; the config comes from
    the request body (ai_model_id + messages [+ system_instruction]).
    """
    ctx = _apply_request_scope(ctx, request)
    agent_version_id = getattr(request, "agent_version_id", None)
    agent_id = getattr(request, "agent_id", None)
    if agent_version_id:
        _validate_uuid(agent_version_id, "agent_version_id")
        ctx = ctx.with_overrides(agent_version_id=agent_version_id)
    elif agent_id:
        _validate_uuid(agent_id, "agent_id")
        ctx = ctx.with_overrides(agent_id=agent_id)

    conversation_id, skip_persistence = await resolve_conversation_gate(
        request.conversation_id, request.is_new, request.store
    )

    config_dict: dict[str, Any] = {
        "model": request.ai_model_id,
        "messages": [
            m if isinstance(m, dict) else m.model_dump() for m in request.messages
        ],
        "stream": request.stream,
    }
    if request.system_instruction is not None:
        config_dict["system_instruction"] = request.system_instruction
    if request.metadata:
        config_dict["metadata"] = dict(request.metadata)
    config = UnifiedConfig.from_dict(config_dict)

    ctx = ctx.with_overrides(
        conversation_id=conversation_id,
        debug=request.debug,
        store=bool(ctx.store and request.store and not skip_persistence),
        source_feature=ctx.source_feature or "chat",
    )
    set_app_context(ctx)

    if not ctx.store:
        config.store = False

    ctx = await apply_request_tools(
        config,
        ctx,
        request.tools,
        request.tools_replace,
        client=request.client,
    )

    if request.variables:
        config.replace_variables(request.variables)
    if request.config_overrides:
        config.apply_overrides(request.config_overrides)
    bind_active_local_model(config)

    return ctx, config, request.max_iterations, request.max_retries_per_iteration


# ---------------------------------------------------------------------------
# The streaming task — local analog of aidream run_ai_task
# ---------------------------------------------------------------------------


async def run_local_ai_task(
    emitter: StreamEmitter,
    config: UnifiedConfig,
    max_iterations: int = 100,
    max_retries_per_iteration: int = 2,
) -> CompletedRequest:
    """Execute an AI request, streaming every event to ``emitter``.

    Runs the SAME classic execution path aidream drives
    (``matrx_ai.orchestrator.execute_ai_request``) — tool calls dispatch
    through the local ToolRegistry/LocalToolBridge, persistence through the
    SQLiteConversationStore. Emits the identical init → … → completion →
    end envelope the frontend's process-stream.ts consumes.
    """
    from matrx_ai.orchestrator.executor import execute_ai_request
    from matrx_connect.context.app_context import get_app_context

    from app.services.ai.runtime_spine import (
        meters_from_completed,
        open_runtime_execution,
        settle_runtime_execution,
        start_heartbeat,
    )

    ctx = get_app_context()

    operation_id = str(uuid.uuid4())
    ctx = ctx.with_overrides(operation_id=operation_id)
    set_app_context(ctx)

    # Cloud runtime spine: open BEFORE the loop (best-effort, offline-safe —
    # a failed open means this run simply isn't cloud-tracked). The execution
    # id is stamped in-place on the INSTALLED ctx's metadata dict under BOTH
    # keys: the vendored matrx-ai reads "runtime_root_execution_id"
    # (resolve_runtime_root_execution_id) and chat_sync mirrors it into
    # chat.request.execution_id.
    lease = await open_runtime_execution(
        jwt=ctx.token,
        organization_id=ctx.organization_id,
        conversation_id=ctx.conversation_id,
        idempotency_key=operation_id,
    )
    if lease is not None:
        ctx.metadata["runtime_execution_id"] = lease.execution_id
        ctx.metadata["runtime_root_execution_id"] = lease.root_execution_id
        start_heartbeat(lease)

    settle_status = "failed"
    settle_error: str | None = None
    completed: CompletedRequest | None = None
    try:
        await emitter.send_phase("processing")
        await emitter.send_init(
            InitPayload(operation="user_request", operation_id=operation_id)
        )

        if ctx.store and ctx.conversation_id:
            try:
                from app.services.ai.conversation_handler import (
                    get_conversation_store,
                )

                await get_conversation_store().reserve_stream_messages(
                    config,
                    ctx.conversation_id,
                    ctx.user_id or "local-user",
                    emitter,
                )
            except Exception:
                logger.error(
                    "[local_ai_task] durable stream message reservation failed",
                    exc_info=True,
                )

        unrecognized = getattr(config, "_unrecognized_keys", [])
        if unrecognized:
            await emitter.send_warning(
                WarningPayload(
                    code="unrecognized_config",
                    system_message=f"Unrecognized config keys ignored: {unrecognized}",
                    user_message=(
                        "Some configuration options sent by the client are not "
                        "recognized by the local engine. They have been ignored."
                    ),
                    level="low",
                    recoverable=True,
                    metadata={"unrecognized_keys": list(unrecognized)},
                )
            )

        completed = await execute_ai_request(
            config,
            max_iterations=max_iterations,
            max_retries_per_iteration=max_retries_per_iteration,
        )

        await _enforce_visible_terminal_output(completed, ctx)
        _update_agent_cache(completed)

        if ctx.store:
            try:
                from app.services.chat_sync import get_chat_sync_engine

                pushed = await get_chat_sync_engine().flush_pending()
                if pushed.get("failed"):
                    raise RuntimeError(
                        f"{pushed['failed']} chat mirror row(s) failed to push"
                    )
                logger.info(
                    "[local_ai_task] flushed persisted turn to cloud (sent=%s)",
                    pushed.get("sent", 0),
                )
            except Exception as exc:
                logger.error(
                    "[local_ai_task] immediate chat mirror flush failed; the local "
                    "turn completed but web route promotion may be delayed: %s",
                    exc,
                    exc_info=True,
                )
                await emitter.send_warning(
                    WarningPayload(
                        code="chat_mirror_flush_failed",
                        system_message=str(exc),
                        user_message=(
                            "The response completed locally, but conversation sync "
                            "to the web is delayed. Matrx Local will retry automatically."
                        ),
                        level="high",
                        recoverable=True,
                    )
                )

        await _emit_completion(emitter, completed, operation_id)
        await emitter.send_end()
        settle_status = "completed"
        return completed
    except asyncio.CancelledError:
        settle_status = "cancelled"
        raise
    except BaseException as exc:
        settle_error = str(exc) or type(exc).__name__
        raise
    finally:
        # Settle EXACTLY ONCE on every exit path (completed / failed /
        # cancelled). Best-effort — a failed settle only warns; the server
        # reaper cleans up an un-settled run.
        if lease is not None:
            await settle_runtime_execution(
                lease,
                status=settle_status,
                error=settle_error,
                meters=(
                    meters_from_completed(completed)
                    if completed is not None
                    else None
                ),
            )


async def _enforce_visible_terminal_output(
    completed: CompletedRequest,
    ctx: Any,
) -> None:
    """Client-host backstop for older matrx-ai builds.

    A provider may return ``finish_reason=stop`` with reasoning only. The
    frontend intentionally hides reasoning, so a nominally successful
    completion with an empty public output is a user-visible silent stop.
    Current matrx-ai rejects this in the orchestrator; this host-side check
    keeps packaged desktops safe while their embedded package rolls forward.
    """
    status = completed.metadata.get("status")
    nonterminal_success = {
        "failed",
        "cancelled",
        *CompletedRequest.RESUMABLE_SUSPEND_STATUSES,
    }
    if status in nonterminal_success:
        return

    output = completed.request.config.get_last_output() if completed.request else ""
    if isinstance(output, str) and output.strip():
        return

    saw_pseudo_tool_syntax = False
    final_response = getattr(completed, "final_response", None)
    for message in getattr(final_response, "messages", None) or []:
        for content in getattr(message, "content", None) or []:
            content_type = getattr(content, "type", None)
            if content_type == "text":
                if str(getattr(content, "text", "") or "").strip():
                    return
                continue
            if content_type == "thinking":
                thought = str(getattr(content, "text", "") or "")
                if "<tool_call" in thought or "<function=" in thought:
                    saw_pseudo_tool_syntax = True
                continue
            if content_type not in {"tool_call", "function_call", "tool_result"}:
                # Media and other typed result blocks are user-visible.
                return

    error_type = (
        "unparsed_tool_call"
        if saw_pseudo_tool_syntax
        else "empty_assistant_response"
    )
    user_message = (
        "The model attempted a tool call in an unsupported format and did not "
        "produce an answer. Please retry or use another model."
        if saw_pseudo_tool_syntax
        else "The model ended without producing a visible answer. Please retry "
        "or use another model."
    )
    completed.metadata.update(
        {
            "status": "failed",
            "error": user_message,
            "error_type": error_type,
        }
    )
    last_assistant = completed.request.config.messages.get_last_by_role("assistant")
    if last_assistant is not None:
        last_assistant.status = "failed"

    logger.error(
        "[local_ai_task] converted terminal reasoning-only success to %s",
        error_type,
    )
    if ctx.store:
        from app.services.ai.conversation_handler import get_conversation_store

        await get_conversation_store().persist_completed_request(
            completed,
            ctx.conversation_id,
        )


def _update_agent_cache(completed: CompletedRequest) -> None:
    """Cache the post-turn UnifiedConfig so continue turns are instant.

    Mirrors aidream's ``_update_cache``. This matters MORE locally than on
    the server: the SQLite conversation row does not persist the model, so
    within a process lifetime the AgentCache is the continue-turn source of
    truth (a restart falls back to the store + agent-hint recovery).
    """
    import traceback

    try:
        from matrx_ai.agents.cache import AgentCache
        from matrx_ai.agents.definition import Agent
        from matrx_connect.context.app_context import get_app_context

        conversation_id = get_app_context().conversation_id
        if not conversation_id:
            return
        AgentCache.set(conversation_id, Agent(config=completed.request.config))
    except Exception as exc:
        logger.warning(
            "[local_ai_task] AgentCache update failed (non-fatal): %s\n%s",
            exc,
            traceback.format_exc(),
        )


def _build_typed_usage(completed: CompletedRequest) -> AggregatedUsageResult | None:
    if not completed.total_usage:
        return None
    return AggregatedUsageResult.model_validate(completed.total_usage.to_dict())


def _build_typed_timing(completed: CompletedRequest) -> TimingStatsResult:
    ts = completed.timing_stats
    return TimingStatsResult(
        total_duration=ts.get("total_duration"),
        sum_duration=ts.get("sum_duration"),
        api_duration=ts.get("api_duration"),
        tool_duration=ts.get("tool_duration"),
        processing_duration=ts.get("processing_duration"),
        iterations=ts.get("iterations"),
        avg_iteration_duration=ts.get("avg_iteration_duration"),
    )


def _build_typed_tool_stats(completed: CompletedRequest) -> ToolCallStatsResult:
    raw = completed.tool_call_stats
    if not raw:
        return ToolCallStatsResult()
    return ToolCallStatsResult.model_validate(raw)


async def _emit_completion(
    emitter: StreamEmitter, completed: CompletedRequest, operation_id: str
) -> None:
    """Terminal completion/error events — mirrors aidream's _emit_completion."""
    exec_status = completed.metadata.get("status", "complete")

    typed_usage = _build_typed_usage(completed)
    typed_timing = _build_typed_timing(completed)
    typed_tool_stats = _build_typed_tool_stats(completed)
    finish_reason_raw = completed.metadata.get("finish_reason")
    finish_reason_str = str(finish_reason_raw) if finish_reason_raw else None

    if exec_status == "failed":
        error_msg = completed.metadata.get("error", "Unknown error")
        logger.error("[local_ai_task] execution failed: %s", error_msg)
        result = UserRequestResult(
            status="failed",
            iterations=completed.iterations,
            total_usage=typed_usage,
            timing_stats=typed_timing,
            tool_call_stats=typed_tool_stats,
            finish_reason=finish_reason_str,
            metadata=completed.metadata,
        )
        await emitter.send_completion(
            CompletionPayload(
                operation="user_request",
                operation_id=operation_id,
                status="failed",
                result=result.model_dump(),
            )
        )
        await emitter.send_error(
            error_type=completed.metadata.get("error_type", "execution_error"),
            message=str(error_msg),
            user_message="Execution completed with errors. Please try again.",
            details={
                "error": str(error_msg),
                "error_type": completed.metadata.get("error_type", "execution_error"),
                "error_iteration": completed.metadata.get("error_iteration"),
                "status": exec_status,
            },
        )
        return

    last_output = (
        completed.request.config.get_last_output() if completed.request else None
    )
    result = UserRequestResult(
        status="complete",
        output=last_output,
        iterations=completed.iterations,
        total_usage=typed_usage,
        timing_stats=typed_timing,
        tool_call_stats=typed_tool_stats,
        finish_reason=finish_reason_str,
        metadata=completed.metadata,
    )
    await emitter.send_completion(
        CompletionPayload(
            operation="user_request",
            operation_id=operation_id,
            status="success",
            result=result.model_dump(),
        )
    )
