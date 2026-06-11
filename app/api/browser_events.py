"""Browser event dispatch — currently unimplemented.

The previous placeholder FABRICATED success responses (a hardcoded file list
and a fake "screenshot taken") for the live POST /trigger endpoint. Until a
real implementation lands, report not-implemented honestly so callers don't
act on invented data.
"""

from __future__ import annotations


async def handle_browser_event(data: dict):
    event_type = data.get("event_type")
    return {
        "error": "not_implemented",
        "detail": f"Browser event '{event_type}' has no implementation yet",
    }
