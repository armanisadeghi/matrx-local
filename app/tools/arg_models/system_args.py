from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SystemInfoArgs(BaseModel):
    pass  # no parameters


class ScreenshotArgs(BaseModel):
    monitor: str = Field(
        default="all",
        description=(
            "Display to capture: 'all' for the full virtual desktop, 'primary' "
            "for the primary display, or a 1-based monitor index string from list screens."
        ),
    )
    region: list[int] | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        description=(
            "Optional crop rectangle [x, y, width, height]. Coordinates are "
            "absolute when monitor='all', otherwise relative to the selected monitor."
        ),
    )


class OpenUrlArgs(BaseModel):
    url: str = Field(description="URL to open in the default web browser.")


class OpenPathArgs(BaseModel):
    path: str = Field(
        description=(
            "Absolute path to a file or directory to open with the OS default "
            "application (Finder, Explorer, etc.)."
        )
    )


# ── System Monitor ────────────────────────────────────────────────────────────

class SystemResourcesArgs(BaseModel):
    pass  # no parameters — returns live CPU, RAM, disk, network stats


class BatteryStatusArgs(BaseModel):
    pass  # no parameters


class DiskUsageArgs(BaseModel):
    path: str | None = Field(
        default=None,
        description=(
            "Specific path to analyze. If omitted, returns stats for all mounted volumes."
        ),
    )


class TopProcessesArgs(BaseModel):
    sort_by: Literal["cpu", "memory"] = Field(
        default="cpu",
        description="Sort processes by 'cpu' or 'memory' usage.",
    )
    limit: int = Field(
        default=15,
        ge=1,
        le=100,
        description="Number of top processes to return.",
    )


# ── Clipboard ─────────────────────────────────────────────────────────────────

class ClipboardReadArgs(BaseModel):
    pass  # no parameters


class ClipboardWriteArgs(BaseModel):
    content: str = Field(description="Text to write to the clipboard.")


# ── Notifications ─────────────────────────────────────────────────────────────

class NotifyArgs(BaseModel):
    title: str = Field(description="Notification title.")
    message: str = Field(description="Notification body text.")
    timeout: int = Field(
        default=10,
        ge=1,
        le=60,
        description="Seconds to display the notification (default 10).",
    )
