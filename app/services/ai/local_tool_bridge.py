"""Local tool bridge — registers matrx-local's OS tools into matrx-ai via ExternalToolAdapter.

Architecture
------------
matrx-ai's ToolExecutor dispatches tool calls for ``source_kind="matrx_local"``
to the registered ``ExternalToolAdapter``.  This module provides that adapter.

Each local tool handler has the signature::

    async def tool_xxx(session: ToolSession, param1, param2, ...) -> LocalToolResult

The bridge:
  1. Maintains a per-conversation ``ToolSession`` (tracks cwd, background shells, etc.)
  2. For each tool call, gets or creates the session for the conversation
  3. Validates args through the Pydantic arg model (from the catalog) when one exists
  4. Calls the real handler with unpacked validated args
  5. Converts matrx-local's ``ToolResult`` → matrx-ai's ``ToolResult``

The adapter auto-discovers ALL local tools from the canonical catalog
(``app.tools.catalog.get_catalog()`` — built from the dispatcher, one entry
per dispatched tool) and registers them as individual per-tool handlers under
their canonical cloud names (highest priority in the resolution chain). Any
tool from ``source_kind="matrx_local"`` not covered by the catalog falls
through to ``dispatch()``, which surfaces a clear "not implemented" error to
the model.

Conversation lifecycle
----------------------
``ToolSession`` objects are keyed by ``conversation_id`` and cleaned up automatically
when matrx-ai's ``ToolLifecycleManager`` detects a conversation has ended or idled out
(30-minute default timeout).  The adapter's ``on_conversation_end()`` hook handles this.

Schema generation
-----------------
Schemas come from the catalog entry's ``input_schema`` — the Pydantic arg
model's JSON Schema when one exists, signature introspection otherwise —
keeping the advertised schema in sync with the code automatically.

Registration
------------
Call ``LocalToolBridge().register()`` once at startup (from engine.py) **before** the
first AI request.  This replaces the old ``register_local_tools(registry)`` call.
"""

from __future__ import annotations

import base64
import inspect
import logging
import time
from pathlib import Path
from typing import Any

from matrx_ai.tools import ExternalToolAdapter, ToolContext, ToolResult
from matrx_ai.tools.models import ToolError
from pydantic import Field

logger = logging.getLogger(__name__)


class _LocalProviderToolResult(ToolResult):
    """Compatibility seam until every installed matrx-ai has this field.

    The canonical implementation now lives in matrx-ai itself. Keeping the
    tiny subclass here makes the offline desktop behavior correct immediately
    even when it is running an older packaged matrx-ai wheel.
    """

    provider_content: Any = Field(default=None, exclude=True, repr=False)

    def to_tool_result_content(self) -> dict[str, Any]:
        payload = super().to_tool_result_content()
        if self.success and self.provider_content is not None:
            payload["content"] = self.provider_content
        return payload


# ---------------------------------------------------------------------------
# ToolSession pool — keyed by conversation_id
# ---------------------------------------------------------------------------

class LocalToolBridge(ExternalToolAdapter):
    """ExternalToolAdapter that exposes every matrx-local OS tool to matrx-ai.

    At startup, the catalog is scanned and every tool is registered as a per-tool
    handler under its canonical cloud name (highest priority).  ``dispatch()``
    handles any tool that slips through (e.g. a new tool added to the DB but not
    yet implemented here).

    ``on_conversation_end()`` evicts the ``ToolSession`` when matrx-ai's lifecycle
    manager signals that a conversation has ended or timed out.
    """

    # matrx-ai >= 0.3.0: ExternalToolAdapter keys the app-level fallback on
    # source_kind (was source_app pre-0.3.0).
    source_kind = "matrx_local"

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}  # conversation_id → ToolSession

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_session(self, conversation_id: str) -> Any:
        """Get or create a ToolSession for a conversation."""
        from app.tools.session import ToolSession

        if conversation_id not in self._sessions:
            self._sessions[conversation_id] = ToolSession()
            logger.debug("Created new ToolSession for conversation %s", conversation_id)
        return self._sessions[conversation_id]

    async def on_conversation_end(self, conversation_id: str) -> None:
        """Clean up the ToolSession when matrx-ai signals a conversation has ended."""
        session = self._sessions.pop(conversation_id, None)
        if session is not None:
            try:
                await session.cleanup()
            except Exception:
                logger.debug(
                    "Error cleaning up ToolSession for conversation %s",
                    conversation_id,
                    exc_info=True,
                )
            logger.debug("Evicted ToolSession for conversation %s", conversation_id)

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    # ------------------------------------------------------------------
    # Dynamic registration from manifest (called by register())
    # ------------------------------------------------------------------

    def register(self, registry: Any = None) -> None:
        """Register all catalog tools + lifecycle cleanup.

        Overrides ``ExternalToolAdapter.register()`` to dynamically build per-tool
        handlers from the canonical catalog instead of relying on the
        ``@external_tool`` decorator (which would require 100+ explicit method
        definitions).
        """
        from app.tools.catalog import get_catalog
        from matrx_ai.tools.external_handlers import ExternalHandlerRegistry

        reg = registry or ExternalHandlerRegistry.get_instance()

        registered_names: list[str] = []
        failed: list[str] = []

        # Silence the per-tool vcprint spam from ExternalHandlerRegistry.register()
        # and emit a single consolidated summary instead.
        _orig_register = reg.__class__.register

        def _silent_register(self_reg: Any, tool_name: str, handler: Any) -> None:
            self_reg._tool_handlers[tool_name] = handler

        reg.__class__.register = _silent_register  # type: ignore[method-assign]
        try:
            for entry in get_catalog():
                try:
                    tool_handler = self._make_tool_handler(
                        entry.handler, entry.cloud_name, entry.arg_model
                    )
                    reg.register(entry.cloud_name, tool_handler)
                    registered_names.append(entry.cloud_name)
                except Exception as exc:
                    failed.append(entry.cloud_name)
                    logger.error("Failed to register local tool %s: %s", entry.cloud_name, exc)
        finally:
            reg.__class__.register = _orig_register  # type: ignore[method-assign]

        # Register the app-level fallback dispatcher for any tool not in the manifest.
        # Silence the vcprint from register_app_handler too.
        _orig_register_app = reg.__class__.register_app_handler

        def _silent_register_app(self_reg: Any, source_kind: str, handler: Any) -> None:
            self_reg._app_handlers[source_kind] = handler

        reg.__class__.register_app_handler = _silent_register_app  # type: ignore[method-assign]
        try:
            reg.register_app_handler(self.source_kind, self._app_dispatcher)
            # Server-fetched tool rows carry source_kind='native' (the cloud
            # rows for this app's tools), not 'matrx_local'. Registering the
            # same dispatcher under 'native' keeps the app-level fallback
            # alive for a cloud row whose name is NOT in this build's catalog
            # (cloud registry ahead of the installed app) — it then gets the
            # bridge's friendly not_implemented instead of the executor's
            # no_viable_executor red banner. Safe on desktop: every native
            # no-path def here IS one of this app's tools (the server fetch
            # is filtered to /ai-tools/app/matrx_local/all), and per-name
            # handlers always win over the app-level fallback.
            reg.register_app_handler("native", self._app_dispatcher)
        finally:
            reg.__class__.register_app_handler = _orig_register_app  # type: ignore[method-assign]

        # Wire on_conversation_end into matrx-ai's ToolLifecycleManager.
        try:
            from matrx_ai.tools.lifecycle import ToolLifecycleManager
            ToolLifecycleManager.get_instance().register_external_adapter_cleanup(
                self.on_conversation_end
            )
        except Exception:
            pass

        if failed:
            logger.warning("[LocalToolBridge] Failed to register: %s", failed)

        names_list = ", ".join(registered_names)
        logger.info(
            "[ExternalHandlerRegistry] Registered %d/%d local tool handlers "
            "(app: %s): %s",
            len(registered_names),
            len(get_catalog()),
            self.source_kind,
            names_list,
        )

    # ------------------------------------------------------------------
    # Adapter factory (replaces the old _make_adapter + _resolve_handler)
    # ------------------------------------------------------------------

    def _make_tool_handler(
        self,
        handler: Any,
        tool_name: str,
        arg_model: type | None,
    ) -> Any:
        """Build a matrx-ai compatible async callable for a local tool handler.

        The returned callable has the signature expected by ExternalHandlerRegistry:
            ``async (args: dict, ctx: ToolContext) -> ToolResult``
        """
        sig = inspect.signature(handler)
        accepts_var_kwargs = any(
            param.kind is inspect.Parameter.VAR_KEYWORD
            for param in sig.parameters.values()
        )
        named_params = {
            name
            for name, param in sig.parameters.items()
            if name != "session"
            and param.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }

        async def tool_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            started_at = time.time()
            session = self._get_session(ctx.conversation_id)

            # Validate + coerce args via the Pydantic model when available.
            validated_args = args
            if arg_model is not None:
                try:
                    parsed = arg_model.model_validate(args)
                    validated_args = parsed.model_dump(exclude_none=True)
                except Exception as exc:
                    return ToolResult(
                        success=False,
                        error=ToolError(
                            error_type="invalid_arguments",
                            message=f"Argument validation failed for '{tool_name}': {exc}",
                            is_retryable=True,
                            suggested_action=(
                                "Review the tool's parameter schema and correct the arguments."
                            ),
                        ),
                        started_at=started_at,
                        completed_at=time.time(),
                        tool_name=tool_name,
                        call_id=ctx.call_id,
                    )

            # Action-enum mega-tools intentionally accept ``**tool_input`` so
            # one handler can fan out to each action variant. Preserve the
            # whole validated payload for those handlers; filtering by the
            # literal VAR_KEYWORD parameter name (``tool_input``) drops every
            # argument and turns valid actions into ``action=None``.
            if accepts_var_kwargs:
                kwargs = dict(validated_args)
            else:
                kwargs = {
                    name: validated_args[name]
                    for name in named_params
                    if name in validated_args
                }

            try:
                local_result = await handler(session, **kwargs)
                return _convert_result(local_result, tool_name, ctx.call_id, started_at)
            except Exception as exc:
                logger.exception("Local tool %s raised an exception", tool_name)
                return ToolResult(
                    success=False,
                    error=ToolError(
                        error_type="execution",
                        message=f"Tool '{tool_name}' failed: {exc}",
                        is_retryable=False,
                        suggested_action="Check the error message and adjust parameters.",
                    ),
                    started_at=started_at,
                    completed_at=time.time(),
                    tool_name=tool_name,
                    call_id=ctx.call_id,
                )

        tool_handler.__name__ = f"local_{tool_name}_handler"
        return tool_handler

    # ------------------------------------------------------------------
    # Fallback dispatch for tools not in the manifest
    # ------------------------------------------------------------------

    async def dispatch(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Handle any ``matrx_local`` tool that has no registered handler.

        This fires only when the DB contains a tool with ``source_kind="matrx_local"``
        that isn't in the local tool catalog.  The model receives a clear error so it
        can inform the user rather than silently failing.
        """
        return ToolResult(
            success=False,
            error=ToolError(
                error_type="not_implemented",
                message=(
                    f"Local tool '{ctx.tool_name}' is not in this desktop's tool catalog. "
                    "Either the tool hasn't been implemented yet or the cloud registry "
                    "is ahead of this app version. "
                    "Run: uv run python -m app.tools.tool_sync status"
                ),
                is_retryable=False,
                suggested_action=(
                    "This local tool is not available. "
                    "Inform the user and suggest an alternative approach."
                ),
            ),
            started_at=time.time(),
            completed_at=time.time(),
            tool_name=ctx.tool_name,
            call_id=ctx.call_id,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _convert_result(
    local_result: Any,
    tool_name: str,
    call_id: str,
    started_at: float,
) -> ToolResult:
    """Convert matrx-local ``ToolResult`` → matrx-ai ``ToolResult``.

    matrx-local: ``ToolResult(type=ToolResultType.SUCCESS, output=str, image=ImageData|None)``
    matrx-ai:    ``ToolResult(success=bool, output=Any, error=ToolError|None, ...)``
    """
    from app.tools.types import ToolResultType

    is_error = local_result.type == ToolResultType.ERROR
    completed_at = time.time()

    output: Any = {"output": local_result.output or ""}
    if local_result.metadata:
        output["metadata"] = local_result.metadata
    provider_content: Any = None

    if local_result.artifact is not None:
        output = local_result.artifact.model_dump(mode="json", exclude_none=True)
        if local_result.provider_image_path:
            from matrx_ai.config import ImageContent, TextContent

            image_bytes = Path(local_result.provider_image_path).read_bytes()
            provider_content = [
                ImageContent(
                    base64_data=base64.b64encode(image_bytes).decode("ascii"),
                    mime_type=local_result.artifact.media_type,
                ),
                TextContent(text=local_result.output or "Screenshot captured."),
            ]

    # Legacy images are also provider-only. Their base64 must not become the
    # ToolResult output that the execution logger persists.
    if local_result.image is not None:
        from matrx_ai.config import ImageContent, TextContent

        provider_content = [
            ImageContent(
                base64_data=local_result.image.base64_data,
                mime_type=local_result.image.media_type,
            ),
            TextContent(text=local_result.output or "Image produced."),
        ]

    return _LocalProviderToolResult(
        success=not is_error,
        output=output if not is_error else None,
        provider_content=provider_content,
        error=(
            ToolError(
                error_type="tool_error",
                message=str(local_result.output),
                is_retryable=False,
                suggested_action="Check the error message and try with corrected parameters.",
            )
            if is_error
            else None
        ),
        started_at=started_at,
        completed_at=completed_at,
        tool_name=tool_name,
        call_id=call_id,
    )


# ---------------------------------------------------------------------------
# Backwards-compatible registration function (used by engine.py)
# ---------------------------------------------------------------------------

_bridge_instance: LocalToolBridge | None = None


def get_bridge() -> LocalToolBridge:
    """Return the process-level LocalToolBridge singleton."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = LocalToolBridge()
    return _bridge_instance


def register_local_tools(registry: Any | None = None) -> int:
    """Register all local tools. Returns the number of tools registered.

    Called from ``engine.py``.  Creates and registers the ``LocalToolBridge``
    singleton, then returns the count of successfully registered tools.
    """
    from app.tools.catalog import get_catalog

    bridge = get_bridge()
    bridge.register(registry)

    # Return count of tools that were registered (those in the catalog that
    # succeeded — the bridge logs failures internally).
    from matrx_ai.tools.external_handlers import ExternalHandlerRegistry
    reg = ExternalHandlerRegistry.get_instance()
    return sum(1 for e in get_catalog() if reg.has_handler(e.cloud_name, "matrx_local"))


def registered_local_tool_names() -> list[str]:
    """Return the canonical cloud names of all local tools in the catalog."""
    from app.tools.catalog import get_catalog
    return [e.cloud_name for e in get_catalog()]


def evict_session(conversation_id: str) -> None:
    """Manually evict the ToolSession for a conversation.

    You normally don't need to call this — matrx-ai's ToolLifecycleManager handles
    cleanup automatically via ``on_conversation_end``.  Use this for explicit cleanup
    (e.g. when a WebSocket disconnects).
    """
    import asyncio

    bridge = get_bridge()
    if conversation_id in bridge._sessions:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(bridge.on_conversation_end(conversation_id))
            else:
                loop.run_until_complete(bridge.on_conversation_end(conversation_id))
        except Exception:
            bridge._sessions.pop(conversation_id, None)


def session_count() -> int:
    """Return the number of active ToolSessions."""
    return get_bridge().session_count


# ---------------------------------------------------------------------------
# Local tool definitions — registry fallback when the server registry is
# unavailable. The catalog (arg models + introspection) is the single source
# of truth for the desktop's own tools, so the engine can synthesize full
# ToolDefinitions without any server round-trip.
# ---------------------------------------------------------------------------

def _schema_to_param_dict(input_schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a catalog entry's JSON Schema into matrx-ai's internal param dict.

    matrx-ai's own ``_pydantic_to_param_dict`` drops ``required`` flags and
    mishandles ``Optional[...]`` fields (pydantic emits ``anyOf`` with no top
    -level ``type``), so we do the conversion here: per-property dicts with
    ``required: True`` markers, picking the first non-null branch for
    optionals — matching what ``ToolDefinition._build_json_schema`` expects.
    """
    required = set(input_schema.get("required", []))
    params: dict[str, Any] = {}

    for fname, fs in (input_schema.get("properties") or {}).items():
        fs = dict(fs)
        if "type" not in fs and "anyOf" in fs:
            branches = [b for b in fs["anyOf"] if b.get("type") != "null"]
            if branches:
                merged = dict(branches[0])
                merged.update({k: v for k, v in fs.items() if k != "anyOf"})
                fs = merged
        param: dict[str, Any] = {
            "type": fs.get("type", "string"),
            "description": fs.get("description", ""),
        }
        for k in (
            "items", "enum", "default", "minimum", "maximum",
            "minItems", "maxItems", "properties", "pattern", "uniqueItems",
        ):
            if k in fs:
                param[k] = fs[k]
        if fname in required:
            param["required"] = True
        params[fname] = param
    return params


def build_local_tool_definitions() -> list[Any]:
    """Build a ToolDefinition for every ADVERTISED catalog tool.

    ``tool_type=EXTERNAL_HANDLER`` + ``source_kind="matrx_local"`` routes
    execution to the LocalToolBridge handlers registered in ``register()`` —
    the same path a server-provided definition would take. Descriptions here
    are the code-side fallback; when the server registry is reachable its
    (DB-canonical) definitions win and this backfill only fills gaps.

    W7 action collapse: only advertised entries (the action-enum mega-tools)
    get definitions — mirroring the cloud registry, where the flat legacy
    rows are retired. Legacy handlers stay registered for EXECUTION (fan-in
    from older cloud rows during the transition), but the local agent loop
    advertises the same collapsed surface the platform does. Mega-tools carry
    their flat cloud dialect (incl. ``$variants``) verbatim.
    """
    from matrx_ai.tools.models import ToolDefinition, ToolType

    from app.tools.catalog import get_advertised_catalog

    defs: list[Any] = []
    for entry in get_advertised_catalog():
        try:
            defs.append(
                ToolDefinition(
                    name=entry.cloud_name,
                    description=entry.description,
                    parameters=(
                        entry.cloud_parameters
                        or _schema_to_param_dict(entry.input_schema)
                    ),
                    tool_type=ToolType.EXTERNAL_HANDLER,
                    source_kind="matrx_local",
                    category=entry.category,
                    tags=list(entry.tags),
                    # ToolDefinition.version became an int row-version in
                    # matrx-ai 0.3.0; the catalog's semantic version string
                    # maps to `semver`.
                    semver=entry.version,
                    timeout_seconds=float(entry.timeout_seconds),
                )
            )
        except Exception:
            logger.warning(
                "Could not build ToolDefinition for local tool %s", entry.cloud_name,
                exc_info=True,
            )
    return defs
