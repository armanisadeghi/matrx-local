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

KNOWN CONTRACT GAP (documented, loud): the local agent cache (SQLite
``agents`` table, synced from aidream ``GET /api/agents``) carries an
agent's settings (model/temperature/max_tokens/tools) and variables but NOT
its authored system prompt — aidream exposes no non-admin REST endpoint
that returns agent messages. A NEW conversation started locally therefore
runs with the agent's model + tools but without the authored prompt (a
WARNING is logged and a stream `warning` event is emitted). Continue-mode
conversations are unaffected: the persisted conversation state is the
source of truth, exactly like aidream.
"""

from __future__ import annotations

import uuid
from typing import Any

# Import-order fix (matrx-ai 0.3.0 providers ↔ orchestrator circular import):
# the orchestrator must be imported before anything that pulls providers.
# Tracked in .matrx/AGENT_TASKS.md; remove when upstream makes it lazy.
import matrx_ai.orchestrator  # noqa: F401  (import-order fix, see above)

from fastapi import HTTPException, status
from matrx_ai.config import LLMParams, MessageList, UnifiedConfig
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
# Conversation gate — aidream resolve_conversation semantics over local SQLite
# ---------------------------------------------------------------------------


def _validate_uuid(value: str, field: str = "conversation_id") -> None:
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


async def conversation_exists(conversation_id: str) -> bool:
    from app.services.local_db.repositories import ConversationsRepo

    return await ConversationsRepo().get(conversation_id) is not None


async def resolve_conversation_gate(
    conversation_id: str | None,
    is_new: bool | None,
) -> tuple[str, bool]:
    """Local mirror of aidream's ``resolve_conversation`` behavior matrix.

    Returns ``(effective_conversation_id, skip_persistence)``. Raises the
    same 409/404/422 HTTPExceptions (same ``code`` strings) aidream raises,
    so the frontend's error handling is transport-identical.
    """
    has_id = conversation_id is not None
    if has_id:
        _validate_uuid(conversation_id)  # type: ignore[arg-type]

    # no ID + is_new=False → ephemeral run (skip persistence).
    if not has_id and is_new is False:
        return str(uuid.uuid4()), True

    effective_id: str = conversation_id if has_id else str(uuid.uuid4())  # type: ignore[assignment]
    must_create = is_new is True or (is_new is None and not has_id)

    exists = await conversation_exists(effective_id) if has_id else False

    if must_create:
        if exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "conversation_already_exists",
                    "message": (
                        f"A conversation with id={effective_id!r} already exists. "
                        "Pass is_new=False to continue it, or omit conversation_id "
                        "to let the server generate a new one."
                    ),
                },
            )
        # Creation itself is owned by the executor's conversation gate
        # (store.ensure_conversation_exists) — single-writer, idempotent.
        return effective_id, False

    # id + is_new in (False, None) → the conversation must exist locally.
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "conversation_not_found",
                "message": (
                    f"No conversation found with id={effective_id!r}. "
                    "Pass is_new=True to create a new conversation with this ID, "
                    "or omit conversation_id to let the server generate one."
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


def apply_request_tools(
    config: UnifiedConfig,
    ctx: AppContext,
    tools: list[Any],
    tools_replace: list[Any] | None,
) -> AppContext:
    """Apply the frontend's unified tool injection to ``config``.

    ``tools_replace`` (when present) becomes the ENTIRE tool set for the
    turn; ``tools`` merges additively. Uses matrx-ai's own
    ``merge_request_tools`` primitive so spec handling (registered / inline
    / delegate) matches aidream. A registered spec whose name is unknown to
    the LOCAL registry is a 422 — loud, never a silently-absent tool.
    """
    from matrx_ai.tools.merge import merge_request_tools

    specs = tools_replace if tools_replace is not None else tools
    if tools_replace is not None:
        config.tools = []
        config.custom_tools = []
    if not specs:
        return ctx

    registry = _registry()
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
                    "client capability/tool injection failed — tool(s) not in the "
                    f"local registry: {', '.join(sorted(set(unknown)))}. The desktop "
                    "engine only carries its local OS tool set; cloud-only tools "
                    "cannot run on the local runtime."
                ),
            },
        )

    # Local engine: every registered tool executes in-process (no client
    # delegation surface), so active_executors stays empty.
    new_ctx = merge_request_tools(config, ctx, specs)
    set_app_context(new_ctx)
    return new_ctx


def filter_agent_tools_to_local(agent_tools: list[Any], agent_id: str) -> list[str]:
    """Keep only agent-declared tools the LOCAL registry can execute.

    Agent settings carry cloud tool refs (UUIDs or names). Unknown refs are
    DROPPED with a loud warning (they'd otherwise hit matrx-ai's
    tool-id-at-provider-boundary guard and kill the turn) — a cloud-only
    tool simply isn't available on the local runtime.
    """
    registry = _registry()
    kept: list[str] = []
    dropped: list[str] = []
    for entry in agent_tools or []:
        if not isinstance(entry, str):
            dropped.append(repr(entry))
            continue
        tool = registry.get(entry)
        if tool is not None:
            kept.append(tool.name)
        else:
            dropped.append(entry)
    if dropped:
        logger.warning(
            "[local_ai_task] agent %s declares %d tool(s) not available in the "
            "local registry — dropped for this run: %s",
            agent_id,
            len(dropped),
            ", ".join(dropped),
        )
    return kept


# ---------------------------------------------------------------------------
# Prep — agent start (turn 1) / conversation continue (turn 2+) / chat
# ---------------------------------------------------------------------------


async def _load_local_agent(agent_id: str) -> dict[str, Any]:
    from app.services.local_db.repositories import AgentsRepo

    row = await AgentsRepo().get(agent_id)
    if row is None:
        # Same shape as aidream's AgentConfigResolver 404.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )
    return row


def _agent_settings(row: dict[str, Any]) -> dict[str, Any]:
    import json

    settings = row.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    return settings if isinstance(settings, dict) else {}


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

    if (
        request.conversation_id
        and request.is_new is not True
        and await conversation_exists(request.conversation_id)
    ):
        return await prepare_conversation_continue(
            request.conversation_id, request, ctx, agent_id_hint=agent_id
        )

    conversation_id, skip_persistence = await resolve_conversation_gate(
        request.conversation_id, request.is_new
    )

    agent_row = await _load_local_agent(agent_id)
    settings = _agent_settings(agent_row)

    overrides = request.config_overrides
    override_model = getattr(overrides, "model", None) if overrides else None
    model = override_model or settings.get("model_id")
    if not model:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "agent_model_missing",
                "message": (
                    f"Agent {agent_id!r} has no model in its local cached settings "
                    "and the request carried no config_overrides.model. Set a model "
                    "on the agent (web) and re-sync, or send config_overrides.model."
                ),
            },
        )

    config = UnifiedConfig(
        model=str(model),
        messages=MessageList(_messages=[]),
        stream=request.stream,
    )
    if settings.get("temperature") is not None:
        config.temperature = settings["temperature"]
    if settings.get("max_tokens") is not None:
        config.max_output_tokens = settings["max_tokens"]

    # Contract gap (see module docstring): the authored system prompt is not
    # in the local cache. Loud on both the log and the stream.
    logger.warning(
        "[local_ai_task] agent %s (%s): authored system prompt is NOT in the "
        "local cache (aidream exposes no non-admin full-definition endpoint) — "
        "running with model+tools only. Tracked in .matrx/AGENT_TASKS.md.",
        agent_id,
        agent_row.get("name", ""),
    )
    if ctx.emitter is not None:
        await ctx.emitter.send_warning(
            WarningPayload(
                code="agent_prompt_unavailable_locally",
                system_message=(
                    f"Agent {agent_id} authored system prompt is not synced to the "
                    "local engine; the turn runs with the agent's model/tools only."
                ),
                user_message=(
                    "This agent's full instructions aren't available on your local "
                    "runtime yet — responses may differ from the cloud version."
                ),
                level="medium",
                recoverable=True,
                metadata={"agent_id": agent_id},
            )
        )

    # Agent-declared tools (local-executable subset) + request injection.
    agent_tools = filter_agent_tools_to_local(settings.get("tools") or [], agent_id)
    if agent_tools:
        config.tools = list(agent_tools)

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

    ctx = apply_request_tools(config, ctx, request.tools, request.tools_replace)

    if request.variables:
        config.replace_variables(request.variables)
    if overrides:
        config.apply_overrides(overrides)
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
    from matrx_ai.agents.resolver import ConversationResolver

    _validate_uuid(conversation_id)

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

    config = await ConversationResolver.from_conversation_id(
        conversation_id,
        user_input=user_input,
        config_overrides=request.config_overrides,
    )

    # Stuck-conversation recovery (mirrors aidream _prepare_continue_run):
    # a conversation whose first turn never persisted a model is rescued
    # from the local agent cache when the URL/hint agent_id is known.
    if not getattr(config, "model", None):
        rescued = False
        if not agent_id_hint:
            # The conversation row may carry the agent it was created from.
            from app.services.local_db.repositories import ConversationsRepo

            conv_row = await ConversationsRepo().get(conversation_id)
            agent_id_hint = (conv_row or {}).get("agent_id") or None
        if agent_id_hint:
            try:
                row = await _load_local_agent(agent_id_hint)
                fallback_model = _agent_settings(row).get("model_id")
                if fallback_model:
                    config.model = str(fallback_model)
                    rescued = True
            except HTTPException:
                pass
        if not rescued:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "conversation_model_missing",
                    "message": (
                        f"Conversation {conversation_id!r} has no persisted model and "
                        "no agent fallback resolved. Send config_overrides.model."
                    ),
                },
            )

    config.stream = getattr(request, "stream", True)
    if not ctx.store:
        config.store = False

    ctx = apply_request_tools(
        config, ctx, getattr(request, "tools", []), getattr(request, "tools_replace", None)
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
    conversation_id, skip_persistence = await resolve_conversation_gate(
        request.conversation_id, request.is_new
    )

    config_dict: dict[str, Any] = {
        "model": request.ai_model_id,
        "messages": [m if isinstance(m, dict) else m.model_dump() for m in request.messages],
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

    ctx = apply_request_tools(config, ctx, request.tools, request.tools_replace)

    if request.variables:
        config.replace_variables(request.variables)
    if request.config_overrides:
        config.apply_overrides(request.config_overrides)

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

    ctx = get_app_context()

    operation_id = str(uuid.uuid4())
    ctx = ctx.with_overrides(operation_id=operation_id)
    set_app_context(ctx)

    await emitter.send_phase("processing")
    await emitter.send_init(
        InitPayload(operation="user_request", operation_id=operation_id)
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

    _update_agent_cache(completed)

    await _emit_completion(emitter, completed, operation_id)
    await emitter.send_end()
    return completed


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
