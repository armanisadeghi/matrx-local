"""Keep the engine's AI Matrx session fresh — by asking its owner, never by
refreshing behind its back.

The desktop UI's Supabase client OWNS the session: it persists the refresh
token, rotates it on its own schedule, and pushes every new pair to the engine
(``POST /auth/token``). The engine must never call the refresh grant itself —
Supabase rotates refresh tokens on use and detects reuse, so an engine-side
refresh would consume the token the UI still holds and, on the UI's next
refresh, revoke the whole session (the person is silently signed out).

What the engine CAN do when it finds its stored access token expired is say
so, loudly and to the right listener: every connected UI client receives a
``session_refresh_requested`` event and re-pushes a fresh session. Measured
2026-09-11: the UI posted a stale session at startup, the engine rightly
rejected it, nothing ever asked for another one, and every cloud lane sat on a
three-day-old token — 143,982 deliveries queued with no error anywhere.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

EVENT_TYPE = "session_refresh_requested"
# One request per lane per minute: the UI answers in well under a second, and
# a lane that keeps finding the token expired must not flood the socket.
_MIN_INTERVAL_SECONDS = 60.0
_last_request_at: dict[str, float] = {}


def session_blocker(*, lane: str, since: str | None = None) -> dict[str, Any]:
    """The one honest description of "no valid AI Matrx session on this Mac"."""
    return {
        "code": "no_active_user_jwt",
        "message": (
            "AI Matrx cannot be reached: this Mac has no valid signed-in session. "
            "Nothing was lost — every event stays queued here."
        ),
        "remedy": (
            "Matrx Local asks the desktop for a fresh session automatically. If this "
            "does not clear within a minute, sign out and back in to AI Matrx in "
            "Matrx Local; delivery resumes on its own."
        ),
        "lane": lane,
        "since": since,
    }


def reset_rate_limit() -> None:
    """Test hook."""
    _last_request_at.clear()


async def request_ui_session_refresh(*, lane: str, reason: str) -> bool:
    """Ask every connected UI client to re-push a fresh session.

    Returns True when a request was sent, False when rate-limited or when no
    UI socket is connected (both are normal; the caller keeps its blocker).
    """
    now = time.time()
    last = _last_request_at.get(lane)
    if last is not None and now - last < _MIN_INTERVAL_SECONDS:
        return False
    _last_request_at[lane] = now
    try:
        from app.main import websocket_manager  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — tests import this module without the app
        return False
    connections = list(getattr(websocket_manager, "connections", {}).values())
    if not connections:
        logger.info(
            "[session_freshness] %s needs a fresh session (%s) but no UI client is connected",
            lane,
            reason,
        )
        return False
    payload = {
        "type": EVENT_TYPE,
        "lane": lane,
        "reason": reason,
        "timestamp": int(now * 1000),
    }
    delivered = False
    for conn in connections:
        try:
            await websocket_manager._send(conn, payload)
            delivered = True
        except Exception:  # noqa: BLE001 — one dead socket must not stop the others
            logger.warning(
                "[session_freshness] could not ask one UI client for a session refresh",
                exc_info=True,
            )
    if delivered:
        logger.info(
            "[session_freshness] asked the desktop for a fresh session (lane=%s, reason=%s)",
            lane,
            reason,
        )
    return delivered


__all__ = [
    "EVENT_TYPE",
    "request_ui_session_refresh",
    "reset_rate_limit",
    "session_blocker",
]
