"""Read the daemon's current grant; Python and React never rotate it."""

from __future__ import annotations

import logging
import time
from typing import Any, Final

logger = logging.getLogger(__name__)

NO_SESSION_CODE = "no_active_user_jwt"
REFRESHING_CODE = "session_refreshing"
_recovery_since: float | None = None


def session_refresh_pending(*, now: float | None = None) -> bool:
    """Only a daemon-reported temporary recovery gets a bounded quiet window."""
    return (
        _recovery_since is not None
        and (now if now is not None else time.time()) - _recovery_since < 90
    )


def session_restored() -> None:
    global _recovery_since
    _recovery_since = None


DEFAULT_REMEDY: Final[str] = (
    "Matrx Local checks the session daemon automatically. If this "
    "does not clear within a minute, sign out and back in to AI Matrx in "
    "Matrx Local; delivery resumes on its own."
)


def daemon_remedy() -> str | None:
    """The daemon's OWN sentence for why there is no session, when it has one.

    The daemon names the condition; the engine must not paraphrase it. That matters
    most right after the custody cutover: a Mac whose pre-cutover session could not
    be carried over is told "sign in again to AI Matrx — this update changed how this
    computer keeps you signed in", and the generic sentence below would tell that same
    person to "sign out and back in" — impossible advice, because there is nothing
    signed in to sign out of.
    """
    from app.services.sync_client import get_sync_client

    snapshot = get_sync_client().last_state
    if snapshot is None:
        return None
    return (snapshot.state_reason or "").strip() or None


def session_blocker(*, lane: str, since: str | None = None) -> dict[str, Any]:
    """THE one description of "this Mac has no usable AI Matrx session".

    Two states, never one: while the daemon is recovering a temporary outage, this
    is a quiet status with nothing for the person to do; once that window
    closes unanswered it is the honest blocker with its remedy. Every
    producer in the engine builds its payload here — a lane that writes its own
    text would drift out of one of the two states (census: `no_active_user_jwt`
    across app/).

    The remedy is the DAEMON's sentence whenever it has one, so every lane's blocker
    and the app's sign-in screen say the same thing about the same condition.
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
        "remedy": daemon_remedy() or DEFAULT_REMEDY,
        "lane": lane,
        "since": since,
    }


async def request_session_grant(*, lane: str, reason: str) -> bool:
    """Ask the one renewal owner for a current grant, without a UI dependency."""
    from app.services.sync_client import get_sync_client

    client = get_sync_client()
    if await client.access_grant() is not None:
        session_restored()
        return True
    snapshot = client.last_state
    global _recovery_since
    if snapshot and snapshot.state in (
        "offline",
        "credential_store_unavailable",
        "daemon_not_running",
    ):
        if _recovery_since is None:
            _recovery_since = time.time()
    else:
        _recovery_since = None
    logger.info("[session_freshness] %s has no daemon grant (%s)", lane, reason)
    return False
