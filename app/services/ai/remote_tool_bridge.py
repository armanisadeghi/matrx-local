"""Authenticated AIDream tool execution for the local AI client host.

The local model loop is hybrid: Matrx Local owns OS tools, while tools owned by
AIDream execute through the server's canonical ``/ai/tools/execute`` pipeline.
Both kinds are registered in the same matrx-ai ToolRegistry, so a model can use
one unified agent config without knowing where a tool runs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from matrx_ai.tools.models import (
    ToolContext,
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolType,
)

from app.services.aidream.client import (
    AIDreamError,
    AIDreamOfflineError,
    AIDreamTimeoutError,
    get_aidream_client,
)

REMOTE_TOOL_CONTEXT_KEY = "remote_tool_request"
_LOCAL_CONTEXT_TOOLS = frozenset({"load_desktop_tools"})


class RemoteToolBridge:
    """Load server definitions and proxy their execution with user identity."""

    def __init__(self) -> None:
        self._loaded_names: set[str] = set()
        self._server_timeouts: dict[str, float] = {}
        self._refresh_lock = asyncio.Lock()

    async def refresh(self) -> int:
        """Fetch all canonical server tools and install exact-name handlers."""
        async with self._refresh_lock:
            client = get_aidream_client()
            if client is None:
                raise AIDreamOfflineError("AIDream server URL is not configured")

            rows = await client.fetch_tools()
            from app.tools.catalog import get_catalog
            from matrx_ai.tools.external_handlers import ExternalHandlerRegistry
            from matrx_ai.tools.registry import ToolRegistry

            local_names = {entry.cloud_name for entry in get_catalog()}
            registry = ToolRegistry.get_instance()
            handlers = ExternalHandlerRegistry.get_instance()
            definitions: list[ToolDefinition] = []
            local_context_definitions: list[ToolDefinition] = []
            server_timeouts: dict[str, float] = {}

            # Validate the full incoming set before mutating either global
            # registry. A malformed row cannot leave a half-installed catalog.
            for row in rows:
                name = row.get("name") if isinstance(row, dict) else None
                if isinstance(name, str) and name in _LOCAL_CONTEXT_TOOLS:
                    existing = registry.get(name)
                    local_context_definitions.append(
                        existing
                        if _has_in_process_executor(existing)
                        else _build_local_context_definition(row)
                    )
                    continue
                if (
                    not isinstance(name, str)
                    or not name
                    or name in local_names
                    or _has_in_process_executor(registry.get(name))
                    or name.startswith("bundle:list_")
                    or row.get("is_active") is False
                ):
                    continue
                try:
                    server_timeout = max(float(row.get("timeout_seconds") or 120.0), 1.0)
                except (TypeError, ValueError):
                    server_timeout = 120.0
                definition = ToolDefinition.model_validate(
                    {
                        **row,
                        "tool_id": row.get("id"),
                        "tool_type": ToolType.EXTERNAL_HANDLER,
                        "function_path": "",
                        # AIDream owns the real role check. The desktop cannot
                        # infer server admin status from a loopback credential.
                        "admin_only": False,
                        # The outer executor must outlive the inner server
                        # timeout plus HTTP response propagation.
                        "timeout_seconds": server_timeout + 30.0,
                    }
                )
                definitions.append(definition)
                server_timeouts[name] = server_timeout

            local_context_names = {
                definition.name for definition in local_context_definitions
            }
            if local_context_definitions:
                registry.load_from_definitions(local_context_definitions)
                for name in local_context_names:
                    # A prior refresh from an older build may have installed a
                    # server proxy under this exact name.
                    handlers._tool_handlers.pop(name, None)  # noqa: SLF001

            new_names = {definition.name for definition in definitions}
            count = registry.load_from_definitions(definitions)
            for stale_name in self._loaded_names - new_names:
                if stale_name in local_context_names:
                    continue
                registry.unregister(stale_name)
                handlers._tool_handlers.pop(stale_name, None)  # noqa: SLF001
            for name in new_names:
                # Exact-name handlers outrank LocalToolBridge's native fallback.
                handlers._tool_handlers[name] = self.execute  # noqa: SLF001
            self._loaded_names = new_names
            self._server_timeouts = server_timeouts
            return count

    async def ensure(self, names: set[str]) -> set[str]:
        """Refresh on demand and return names still absent from the registry."""
        from matrx_ai.tools.registry import ToolRegistry

        registry = ToolRegistry.get_instance()
        missing = {name for name in names if name and registry.get(name) is None}
        if missing:
            await self.refresh()
            missing = {name for name in missing if registry.get(name) is None}
        return missing

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        started_at = time.time()
        try:
            from matrx_connect.context.app_context import get_app_context

            app_ctx = get_app_context()
            agent_id = app_ctx.agent_version_id or app_ctx.agent_id
            if not agent_id:
                return self._error(
                    ctx,
                    started_at,
                    "remote_context_missing",
                    "A server-owned tool requires a saved-agent identity.",
                    retryable=False,
                )

            jwt = (
                app_ctx.token if _is_user_jwt(app_ctx.token) else None
            ) or await _stored_jwt()
            if not jwt:
                return self._error(
                    ctx,
                    started_at,
                    "authentication_required",
                    "A signed-in session is required to execute server-owned tools.",
                    retryable=False,
                )

            request_context = app_ctx.metadata.get(REMOTE_TOOL_CONTEXT_KEY, {})
            if not isinstance(request_context, dict):
                request_context = {}
            client_context = request_context.get("client")
            surface = (
                client_context.get("surface")
                if isinstance(client_context, dict)
                else None
            ) or "matrx-user/chat"
            source_app = app_ctx.source_app
            if source_app == "matrx_local":
                # matrx-ai uses the underscore spelling for its internal tool
                # source. AIDream's request provenance registry deliberately
                # uses the product name ``matrx-local``.
                source_app = "matrx-local"

            payload = {
                "agent_id": agent_id,
                "conversation_id": app_ctx.conversation_id,
                "tool_name": ctx.tool_name,
                "arguments": args,
                "call_id": ctx.call_id,
                "surface": surface,
                "is_version": bool(app_ctx.agent_version_id),
                "tools": request_context.get("tools", []),
                "tools_replace": request_context.get("tools_replace"),
                "client": client_context,
                "organization_id": app_ctx.organization_id,
                "project_id": app_ctx.project_id,
                "task_id": app_ctx.task_id,
                "scope_ids": request_context.get("scope_ids"),
                "source_app": source_app,
                "source_feature": app_ctx.source_feature,
                "store": app_ctx.store,
            }
            client = get_aidream_client()
            if client is None:
                raise AIDreamOfflineError("AIDream server URL is not configured")
            response = await client.post(
                "/ai/tools/execute",
                payload,
                jwt=jwt,
                timeout=self._server_timeouts.get(ctx.tool_name, 120.0) + 15.0,
            )
            if not isinstance(response, dict):
                raise AIDreamError(502, "AIDream returned a non-object tool result")
            if response.get("call_id") != ctx.call_id:
                raise AIDreamError(502, "AIDream returned a mismatched tool call ID")
            ok = response.get("ok")
            output = response.get("output")
            if not isinstance(ok, bool) or not isinstance(output, str):
                raise AIDreamError(502, "AIDream returned an invalid tool result shape")
            if not ok:
                return self._error(
                    ctx,
                    started_at,
                    "server_tool_error",
                    output,
                    retryable=False,
                )
            return ToolResult(
                success=True,
                output=output,
                started_at=started_at,
                completed_at=time.time(),
                tool_name=ctx.tool_name,
                call_id=ctx.call_id,
            )
        except AIDreamTimeoutError as exc:
            return self._error(
                ctx,
                started_at,
                "server_result_unknown",
                (
                    f"{exc}. The server may have completed this tool; do not "
                    "automatically repeat a mutating call."
                ),
                retryable=False,
            )
        except AIDreamOfflineError as exc:
            return self._error(
                ctx,
                started_at,
                "server_unavailable",
                str(exc),
                retryable=True,
            )
        except AIDreamError as exc:
            return self._error(
                ctx,
                started_at,
                "server_tool_request_failed",
                str(exc),
                retryable=exc.status >= 500,
            )
        except Exception as exc:  # noqa: BLE001
            return self._error(
                ctx,
                started_at,
                "server_tool_request_failed",
                f"Remote tool execution failed: {exc}",
                retryable=False,
            )

    @staticmethod
    def _error(
        ctx: ToolContext,
        started_at: float,
        error_type: str,
        message: str,
        *,
        retryable: bool,
    ) -> ToolResult:
        return ToolResult(
            success=False,
            error=ToolError(
                error_type=error_type,
                message=message,
                is_retryable=retryable,
                suggested_action=(
                    "Retry when the server connection is available."
                    if retryable
                    else "Review the tool error and adjust the request."
                ),
            ),
            started_at=started_at,
            completed_at=time.time(),
            tool_name=ctx.tool_name,
            call_id=ctx.call_id,
        )


async def _stored_jwt() -> str | None:
    from app.services.local_db.repositories import TokenRepo

    repo = TokenRepo()
    token = await repo.get()
    if not token or repo.is_expired(token):
        return None
    value = token.get("access_token")
    return value if isinstance(value, str) and value else None


def _has_in_process_executor(definition: ToolDefinition | None) -> bool:
    """Preserve matrx-ai code tools already registered in this process.

    Discovery tools such as ``load_desktop_tools`` queue mutations on the
    active local request context. Replacing them with an AIDream proxy makes
    the mutation happen in a different process, so the newly loaded tools can
    never reach the local model loop.
    """
    if definition is None:
        return False
    return bool(
        getattr(definition, "_callable", None)
        or str(getattr(definition, "function_path", "") or "").strip()
    )


def _build_local_context_definition(row: dict[str, Any]) -> ToolDefinition:
    """Bind a context-mutating matrx-ai tool to this process."""
    # Importing the declarations registers the hand-owned callable contracts.
    from matrx_ai.tools import _generated_declarations as _declarations  # noqa: F401
    from matrx_ai.tools.declared import get_effective_declared

    name = str(row.get("name") or "")
    declared = get_effective_declared(name)
    if declared is None:
        raise RuntimeError(f"No in-process declaration is registered for {name!r}")
    definition = ToolDefinition.model_validate(
        {
            **row,
            "tool_id": row.get("id"),
            "tool_type": ToolType.LOCAL,
            "function_path": declared.function_path,
            "admin_only": False,
        }
    )
    definition._callable = declared.func
    return definition


def _is_user_jwt(value: str | None) -> bool:
    if not isinstance(value, str) or value.count(".") != 2:
        return False
    try:
        import jwt as pyjwt

        claims = pyjwt.decode(value, options={"verify_signature": False})
        return bool(claims.get("sub"))
    except Exception:
        return False


_bridge: RemoteToolBridge | None = None


def get_remote_tool_bridge() -> RemoteToolBridge:
    global _bridge
    if _bridge is None:
        _bridge = RemoteToolBridge()
    return _bridge


__all__ = ["REMOTE_TOOL_CONTEXT_KEY", "RemoteToolBridge", "get_remote_tool_bridge"]
