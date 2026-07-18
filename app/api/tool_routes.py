"""HTTP routes for the tool dispatcher.

Provides a REST interface alongside the WebSocket interface.
Each request creates a fresh session (stateless). For persistent sessions
with working-directory tracking and background processes, use WebSocket.
"""

from __future__ import annotations

import time
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.tools.dispatcher import TOOL_NAMES, dispatch
from app.tools.session import ToolSession
from app.services.action_needed import ActionNeeded

logger = logging.getLogger(__name__)
router = APIRouter()


class ToolRequest(BaseModel):
    tool: str = Field(description="Tool name to invoke")
    input: dict[str, Any] = Field(default_factory=dict, description="Tool parameters")


class ToolResponse(BaseModel):
    type: str
    output: str
    image: dict[str, Any] | None = None
    artifact: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    action_needed: ActionNeeded | None = None


@router.get("/list")
async def list_tools() -> dict[str, list[str]]:
    return {"tools": TOOL_NAMES}


@router.post("/invoke", response_model=ToolResponse)
async def invoke_tool(req: ToolRequest, request: Request) -> ToolResponse:
    t0 = time.monotonic()
    client_ip = request.client.host if request.client else "unknown"
    has_auth = bool(request.headers.get("authorization"))

    logger.info(
        "[tool_routes/invoke] → %s | client=%s | auth=%s | input_keys=%s",
        req.tool, client_ip, has_auth, list(req.input.keys()),
    )

    session = ToolSession()
    try:
        result = await dispatch(req.tool, req.input, session)
    except Exception as exc:
        elapsed = round((time.monotonic() - t0) * 1000)
        logger.error(
            "[tool_routes/invoke] EXCEPTION — %s after %dms | %s: %s",
            req.tool, elapsed, type(exc).__name__, exc,
            exc_info=True,
            extra={"tool": req.tool, "input": req.input, "elapsed_ms": elapsed},
        )
        raise
    finally:
        await session.cleanup()

    elapsed = round((time.monotonic() - t0) * 1000)
    logger.info(
        "[tool_routes/invoke] ✓ %s | %dms | type=%s | output_len=%d",
        req.tool, elapsed, result.type.value, len(result.output or ""),
    )

    return ToolResponse(
        type=result.type.value,
        output=result.output,
        image=result.image.model_dump() if result.image else None,
        artifact=(
            result.artifact.model_dump(mode="json", exclude_none=True)
            if result.artifact
            else None
        ),
        metadata=result.metadata,
        action_needed=result.action_needed,
    )
