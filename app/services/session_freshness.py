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

NO_SESSION_CODE = "no_active_user_jwt"
REFRESHING_CODE = "session_refreshing"
# How long a gap the desktop is visibly in the middle of filling may stay a
# quiet status instead of an error. An account switch (revoke, then the new
# account's sign-in) measured 27 s and 34 s end to end on 2026-09-14; the
# desktop normally answers a socket ask in well under a second. Anything still
# missing after this window is a real signed-out Mac and says so, with the
# sign-out remedy.
_REFRESH_GRACE_SECONDS = 90.0
# When the CURRENT gap started being filled — the first delivered ask since the
# last restored session, never the latest one. Re-arming it on every retry
# would let a genuinely signed-out Mac claim "refreshing" forever.
_refresh_pending_since: float | None = None


def _now() -> float:
    """The one clock these windows read (a test seam; never called for I/O)."""
    return time.time()


def session_refresh_pending(*, now: float | None = None) -> bool:
    """Is the desktop inside the window where it owes us a fresh session?"""
    if _refresh_pending_since is None:
        return False
    return (now if now is not None else _now()) - _refresh_pending_since < _REFRESH_GRACE_SECONDS


def session_restored() -> None:
    """A fresh session landed: close the window and let every lane ask again."""
    global _refresh_pending_since
    _refresh_pending_since = None
    _last_request_at.clear()


def session_blocker(*, lane: str, since: str | None = None) -> dict[str, Any]:
    """THE one description of "this Mac has no usable AI Matrx session".

    Two states, never one: while the desktop is answering an ask we made, this
    is a quiet status with nothing for the person to do; once that window
    closes unanswered it is the honest blocker with its sign-out remedy. Every
    producer in the engine builds its payload here — a lane that writes its own
    text would drift out of one of the two states (census: `no_active_user_jwt`
    across app/).
    """
    if session_refresh_pending():
        return {
            "code": REFRESHING_CODE,
            "message": "Refreshing your AI Matrx session\u2026",
            "remedy": None,
            "lane": lane,
            "since": since,
        }
    return {
        "code": NO_SESSION_CODE,
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
    session_restored()


async def request_ui_session_refresh(*, lane: str, reason: str) -> bool:
    """Ask every connected UI client to re-push a fresh session.

    Returns True when a request was sent, False when rate-limited or when no
    UI socket is connected (both are normal; the caller keeps its blocker).
    """
    global _refresh_pending_since
    now = _now()
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
        if _refresh_pending_since is None:
            _refresh_pending_since = now
        logger.info(
            "[session_freshness] asked the desktop for a fresh session (lane=%s, reason=%s)",
            lane,
            reason,
        )
    return delivered


__all__ = [
    "EVENT_TYPE",
    "NO_SESSION_CODE",
    "REFRESHING_CODE",
    "request_ui_session_refresh",
    "reset_rate_limit",
    "session_blocker",
    "session_refresh_pending",
    "session_restored",
]
