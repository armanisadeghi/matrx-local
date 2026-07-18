"""Shared types for the tool system — result models and data structures."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field

from app.content_ir.media import ScreenshotArtifact
from app.services.action_needed import ActionNeeded


class ToolResultType(str, enum.Enum):
    SUCCESS = "success"
    ERROR = "error"


class ImageData(BaseModel):
    """Deprecated inline-image carrier kept only for older non-capture tools.

    Screenshot producers must return :class:`ScreenshotArtifact` instead. Raw
    base64 is never a durable tool-result representation.
    """

    media_type: str = Field(description="MIME type, e.g. 'image/png'")
    base64_data: str = Field(description="Raw base64-encoded image bytes")


class ToolResult(BaseModel):
    type: ToolResultType = ToolResultType.SUCCESS
    output: str = ""
    image: ImageData | None = None
    artifact: ScreenshotArtifact | None = None
    # Process-private provider seam. Local models receive bytes from this path;
    # REST/WebSocket serializers and conversation persistence never do.
    provider_image_path: str | None = Field(default=None, exclude=True, repr=False)
    metadata: dict[str, Any] | None = None
    # A user-fixable requirement is structured state, never only prose in
    # ``output``. REST, WebSocket, extension RPC, and agent consumers all carry
    # this exact model.
    action_needed: ActionNeeded | None = None
