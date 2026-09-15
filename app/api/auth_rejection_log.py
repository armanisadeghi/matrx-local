"""One rate-limited log for every auth-rejected request, on every surface.

WHY THIS IS SHARED (SR-05, measured 2026-09-11 → 2026-09-14): the throttle
below existed, but only ``/extension/*`` used it. ``app/api/auth.py`` — the
middleware in front of everything else — emitted one unthrottled WARNING per
rejected request, so a signed-out desktop poller produced

    /prompt-matrix/paths      12,377
    /prompt-matrix/templates  12,225
    /prompt-matrix/library    12,225
    /cloud/debug               1,187
    /downloads/stream          1,009
    /access/health             1,057
    /filesystem/status           868
    /scrapes/sync-status         307

WARNING lines in 72 hours — ~37,000 from the three /prompt-matrix routes
alone, which is the largest single class in the engine log and buries
everything real in it. The throttle was written for exactly this and was one
import away from the code that needed it.

Strategy (unchanged from its ``/extension/*`` original): the FIRST rejection
of a ``(surface, path, reason)`` tuple is a WARNING so an operator sees it at
once; repeats inside the window are DEBUG; a continuing stream emits one INFO
summary per minute carrying the rate, so "still rejecting" is visible without
the flood. A stream that goes quiet for a full window and resumes is news
again.

This throttles the LOG, never the request: rejecting is the auth middleware's
job and is unaffected. And it is not a fix for a client that never attaches a
token — it only makes that client's bug findable instead of deafening.
"""

from __future__ import annotations

import time

from app.common.system_logger import get_logger

logger = get_logger()

# Quiet gap after which the next rejection re-surfaces as a WARNING.
REJECT_LOG_WINDOW_SECONDS = 60.0
REJECT_SUMMARY_INTERVAL_SECONDS = 60.0
# Hard bound on tracked keys so arbitrary rejected paths (port scanners,
# typo'd clients) cannot grow the dict forever.
REJECT_STATE_MAX_KEYS = 256

_RejectStateKey = tuple[str, str, str, str]  # (surface, kind, path, reason)
_reject_log_state: dict[_RejectStateKey, dict[str, float]] = {}


def reset_rejection_log_state() -> None:
    """Forget every tracked stream — for tests only."""
    _reject_log_state.clear()


def rejection_log_state() -> dict[_RejectStateKey, dict[str, float]]:
    """Live suppression state, so a test or a diagnostic can read the counts."""
    return _reject_log_state


def log_rejection(
    surface: str,
    kind: str,
    path: str,
    reason: str,
    *,
    method: str = "",
    detail: str = "",
) -> None:
    """Log an auth-rejected request with rate-limit suppression.

    Args:
        surface: log prefix naming the middleware (``"auth"``,
            ``"extension_auth"``) — part of the suppression key, so the two
            surfaces never mask each other.
        kind: ``"http"`` or ``"ws"`` — cosmetic, distinguishes the lanes.
        path: the rejected path. Part of the suppression key, so a different
            path still surfaces immediately.
        reason: short stable identifier for WHY (``"missing_bearer"``,
            ``"unverified_token_over_tunnel"``, …). Part of the key: a
            different reason on the same path is a different situation.
        method: HTTP method (HTTP rejections only); blank for WS.
        detail: optional extra words for the first WARNING only.
    """
    now = time.monotonic()
    key: _RejectStateKey = (surface, kind, path, reason)
    state = _reject_log_state.get(key)

    # A rejection stream that went quiet for the full window and then resumed
    # is news — drop the stale entry so it re-WARNs below.
    if state is not None and now - state["last_seen"] >= REJECT_LOG_WINDOW_SECONDS:
        del _reject_log_state[key]
        state = None

    method_str = f"{method} " if method else ""
    label = "rejected" if kind == "http" else "WS rejected"
    words = reason.replace("_", " ")
    tail = f" ({detail})" if detail else ""

    if state is None:
        # Bound the state dict: evict the longest-idle key when full.
        if len(_reject_log_state) >= REJECT_STATE_MAX_KEYS:
            oldest = min(
                _reject_log_state, key=lambda k: _reject_log_state[k]["last_seen"]
            )
            del _reject_log_state[oldest]
        _reject_log_state[key] = {
            "first_seen": now,
            "last_seen": now,
            "last_summary": now,
            "count": 1.0,
        }
        logger.warning(
            "[%s] %s %s%s — %s%s", surface, label, method_str, path, words, tail
        )
        return

    state["count"] += 1
    state["last_seen"] = now

    if now - state["last_summary"] >= REJECT_SUMMARY_INTERVAL_SECONDS:
        elapsed = now - state["first_seen"]
        rate = state["count"] / elapsed if elapsed > 0 else 0.0
        logger.info(
            "[%s] still %s %s%s (%s) — %.0f rejections in last %.0fs (%.1f/s)",
            surface, label, method_str, path, words, state["count"], elapsed, rate,
        )
        state["last_summary"] = now
        # Reset so the next summary covers the next window only.
        state["first_seen"] = now
        state["count"] = 0.0
        return

    logger.debug(
        "[%s] %s %s%s — %s (suppressed; count=%.0f)",
        surface, label, method_str, path, words, state["count"],
    )
