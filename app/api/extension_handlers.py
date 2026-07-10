"""Handler registry for the matrx-extend Chrome extension command surface.

This registry is the SINGLE command surface for every transport the
extension can arrive on:

  * `POST /extension/rpc` (HTTP, loopback or tunnel) — `extension_routes.py`
  * Supabase Broadcast rpc envelopes (cross-machine fallback) —
    `cross_component_router.py` dispatches `kind:"rpc"` envelopes here via
    `invoke_command`.

Handlers are async, accept the request args dict + the underlying FastAPI
`Request` (or `None` when the call arrived over a non-HTTP transport), and
return a JSON-serializable dict that becomes the `data` field of the outer
`DesktopRpcResponse` (HTTP) or the reply envelope payload (Broadcast).

Adding a new command is a one-line decorator:

    @register("my_command")
    async def my_command(args: Dict[str, Any], req: Request) -> Dict[str, Any]:
        ...

Exceptions raised inside a handler are caught by the route in
`extension_routes.py` and converted into `ok=false` responses — handlers
should NOT swallow errors silently. The one exception is the `tool`
handler below, which intentionally encodes tool-call failures as
`{ok: False, error: ...}` payloads inside a successful HTTP response so
the extension's UX can distinguish RPC-layer failures from tool-layer
failures.
"""

from __future__ import annotations

import sys
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import Request

from app.api.routes import _APP_VERSION
from app.common.system_logger import get_logger
from app.tools.dispatcher import dispatch, list_tool_specs
from app.tools.session import ToolSession

logger = get_logger()

# Second parameter is the FastAPI Request for HTTP-transported commands and
# None for transport-agnostic paths (Supabase Broadcast). None of the current
# handlers read it; any future handler that does MUST tolerate None or reject
# loudly with a clear error.
HandlerFunc = Callable[[Dict[str, Any], Optional[Request]], Awaitable[Dict[str, Any]]]

HANDLERS: Dict[str, HandlerFunc] = {}


async def invoke_command(
    command: str,
    args: Optional[Dict[str, Any]],
    request: Optional[Request] = None,
) -> Dict[str, Any]:
    """Transport-agnostic command dispatch into the HANDLERS registry.

    Both the Broadcast rpc router and any future non-HTTP transport call
    this instead of duplicating lookup/error handling. The HTTP route in
    `extension_routes.py` keeps its own dispatch loop because it layers
    per-branch metrics + bridge-event publishing on top; the command
    REGISTRY is shared, so the surface cannot drift between transports.

    Returns an envelope-ready dict:
      success:  {"ok": True,  "data": <handler dict>}
      failure:  {"ok": False, "error": str, "error_type": str}
    Never raises — transport code must always get a reply to send.
    """
    handler = HANDLERS.get(command)
    if handler is None:
        logger.warning("[extension_handlers] unknown command %r via invoke_command", command)
        return {
            "ok": False,
            "error": f"Unknown command: {command}",
            "error_type": "UnknownCommand",
        }
    try:
        data = await handler(args or {}, request)
    except Exception as e:
        logger.error(
            "[extension_handlers] command %r failed via invoke_command: %s",
            command,
            e,
            exc_info=True,
        )
        return {"ok": False, "error": str(e), "error_type": type(e).__name__}
    return {"ok": True, "data": data}


def register(name: str) -> Callable[[HandlerFunc], HandlerFunc]:
    """Decorator to register a handler under `name` in HANDLERS."""

    def decorator(fn: HandlerFunc) -> HandlerFunc:
        if name in HANDLERS:
            raise ValueError(f"Handler {name!r} already registered")
        HANDLERS[name] = fn
        return fn

    return decorator


def _get_build_identifier() -> Optional[str]:
    """Return a build identifier when running as a packaged binary, else None.

    PyInstaller / Tauri sidecar builds set `sys.frozen = True` and expose the
    bundle extraction directory via `sys._MEIPASS`. In dev mode neither is
    set, so we return None and the extension can render "dev" in its UI.
    """
    is_frozen = bool(getattr(sys, "frozen", False))
    if not is_frozen:
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return f"frozen:{meipass}"
    return "frozen"


@register("health")
async def handle_health(args: Dict[str, Any], req: Optional[Request]) -> Dict[str, Any]:
    """Return engine health — matches the extension's `DesktopHealthSchema`.

    user_id is None for now; will be populated once auth handshake lands
    (see master plan section B3). The extension renders "signed in as ..."
    from this field, so wire it up rather than dropping the key.
    """
    return {
        "status": "ok",
        "version": _APP_VERSION,
        "user_id": None,
    }


@register("version")
async def handle_version(args: Dict[str, Any], req: Optional[Request]) -> Dict[str, Any]:
    """Return the engine version + a build identifier when packaged."""
    return {
        "version": _APP_VERSION,
        "build": _get_build_identifier(),
    }


@register("capabilities")
async def handle_capabilities(args: Dict[str, Any], req: Optional[Request]) -> Dict[str, Any]:
    """Return the engine's tool catalog so the extension can advertise tools.

    Schema: `{"tools": [{name, description, category, input_schema}, ...]}`.
    Source of truth: `app.tools.dispatcher.list_tool_specs`, which itself
    delegates to `app.tools.tool_schemas.generate_all_tool_schemas` — never
    duplicate the catalog here.
    """
    return {"tools": list_tool_specs()}


@register("tool")
async def handle_tool(args: Dict[str, Any], req: Optional[Request]) -> Dict[str, Any]:
    """Generic tool invocation — dispatch a single tool call and return the result.

    Args payload (validated here, NOT via Pydantic so the registry stays
    framework-thin):
      - tool_name: str (required)
      - tool_input: dict (default {})
      - session_id: str | None (currently informational; a request-scoped
        ToolSession is created per call until B3 adds a session cache)

    Tool-layer failures are returned as `{ok: False, error, error_type}`
    payloads inside a 200 HTTP response. RPC-layer failures (bad args
    shape, dispatcher import errors, etc.) raise and are caught by the
    outer route, becoming `DesktopRpcResponse.ok=False` envelopes — so the
    extension can always distinguish "the call never ran" from "the call
    ran and the tool returned an error".
    """
    tool_name = args.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return {
            "ok": False,
            "error": "tool_name is required and must be a non-empty string",
            "error_type": "ValidationError",
        }

    tool_input = args.get("tool_input", {})
    if tool_input is None:
        tool_input = {}
    if not isinstance(tool_input, dict):
        return {
            "ok": False,
            "error": "tool_input must be an object",
            "error_type": "ValidationError",
        }

    session_id = args.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        return {
            "ok": False,
            "error": "session_id must be a string or null",
            "error_type": "ValidationError",
        }

    # B2 ships request-scoped sessions only. Cross-call session reuse
    # (keyed by session_id) lands in B3 once the engine has a session
    # manager. Until then, every tool call gets a fresh session — fine
    # for stateless tools, surfaces a clear upgrade path for stateful
    # ones.
    session = ToolSession()

    try:
        result = await dispatch(tool_name, tool_input, session)
    except Exception as e:
        logger.error(
            "[extension_handlers] tool dispatch raised: tool=%s err=%s",
            tool_name,
            e,
            exc_info=True,
        )
        return {
            "ok": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }
    finally:
        try:
            await session.cleanup()
        except Exception:
            logger.warning(
                "[extension_handlers] session cleanup failed for tool=%s",
                tool_name,
                exc_info=True,
            )

    # ToolResult is a Pydantic model — model_dump() is the v2 idiom and
    # also exists on v1 models via the compatibility shim.
    if hasattr(result, "model_dump"):
        result_payload: Any = result.model_dump()
    elif hasattr(result, "dict"):
        result_payload = result.dict()
    else:
        result_payload = result

    return {
        "ok": True,
        "result": result_payload,
    }
