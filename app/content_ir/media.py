"""Content IR media values used by local tool I/O contracts."""

from __future__ import annotations

import enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ArtifactAvailability(str, enum.Enum):
    SYNC_PENDING = "sync_pending"
    CLOUD_READY = "cloud_ready"
    SYNC_FAILED = "sync_failed"


class MediaRefValue(BaseModel):
    file_id: str
    vision_class: str | None = None


class ScreenshotArtifact(BaseModel):
    """Content-IR value emitted by screenshot-producing tools.

    ``artifact_id`` is generated locally and never changes. ``file_id`` and
    ``media_ref`` appear only after Matrx Files acknowledges publication.
    Absolute local paths deliberately live outside this value in the local
    artifact sidecar.
    """

    kind: Literal["image_ref"] = "image_ref"
    artifact_id: str
    availability: ArtifactAvailability
    media_type: str = "image/png"
    file_name: str
    size_bytes: int
    checksum: str
    source_width: int
    source_height: int
    capture_source: Literal["desktop", "browser"]
    file_id: str | None = None
    media_ref: MediaRefValue | None = None
    url: str | None = None
    cdn_url: str | None = None
    signed_url: str | None = None
    download_url: str | None = None
    visibility: Literal["private", "shared", "public"] = "private"
    capture: dict[str, Any] = Field(default_factory=dict)


def screenshot_artifact_json_schema() -> dict[str, Any]:
    return ScreenshotArtifact.model_json_schema()


def generic_tool_output_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "output": {"type": "string"},
            "metadata": {"type": "object", "additionalProperties": True},
        },
        "required": ["output"],
        "additionalProperties": False,
    }


def mixed_screenshot_tool_output_json_schema() -> dict[str, Any]:
    artifact = screenshot_artifact_json_schema()
    defs = artifact.pop("$defs", {})
    return {
        **({"$defs": defs} if defs else {}),
        "oneOf": [artifact, generic_tool_output_json_schema()],
    }
