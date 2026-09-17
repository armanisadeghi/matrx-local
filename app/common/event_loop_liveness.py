"""Process-local event-loop liveness shared by the server and its parent watchdog.

The async server thread publishes a monotonic pulse only after lifespan startup
is complete.  The existing parent-watchdog thread reads it without doing I/O;
callers log any transition after this module has released its lock.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class LivenessTransition:
    """One state transition for the watchdog to report outside the state lock."""

    kind: str
    age_seconds: float


_lock = threading.Lock()
_armed = False
_last_real_pulse: float | None = None
_last_watchdog_check: float | None = None
_watchdog_grace_deadline: float | None = None
_stall_after_seconds = 10.0
_reported_stall = False
_reported_stall_age: float | None = None
_reported_pulse: float | None = None


def arm_or_pulse(*, now: float, stall_after_seconds: float) -> None:
    """Publish server-loop progress and arm capture after startup is ready."""
    global _armed, _last_real_pulse, _stall_after_seconds
    with _lock:
        _armed = True
        _last_real_pulse = now
        _stall_after_seconds = stall_after_seconds


def disarm() -> None:
    """Suppress capture during startup gaps, shutdown, and disabled config."""
    global _armed, _last_real_pulse, _last_watchdog_check, _watchdog_grace_deadline
    global _reported_stall, _reported_stall_age, _reported_pulse
    with _lock:
        _armed = False
        _last_real_pulse = None
        _last_watchdog_check = None
        _watchdog_grace_deadline = None
        _reported_stall = False
        _reported_stall_age = None
        _reported_pulse = None


def check(*, now: float) -> LivenessTransition | None:
    """Return a stale/recovered transition, or ``None`` without logging.

    A parent watchdog that resumes after a long machine sleep cannot know
    whether the async loop has had a chance to run. A delayed check adds a
    grace period but never invents a pulse or ends an already-reported stall.
    """
    global _last_watchdog_check, _watchdog_grace_deadline
    global _reported_stall, _reported_stall_age, _reported_pulse
    with _lock:
        if not _armed or _last_real_pulse is None:
            return None
        previous_check = _last_watchdog_check
        _last_watchdog_check = now
        if previous_check is not None and now - previous_check > _stall_after_seconds:
            _watchdog_grace_deadline = now + _stall_after_seconds

        if _reported_stall:
            if (
                _reported_pulse is not None
                and _last_real_pulse > _reported_pulse
            ):
                _reported_stall = False
                reported_age = _reported_stall_age
                _reported_stall_age = None
                _reported_pulse = None
                return LivenessTransition("recovered", reported_age or 0.0)
            return None

        if _watchdog_grace_deadline is not None:
            if now < _watchdog_grace_deadline:
                return None
            _watchdog_grace_deadline = None

        age_seconds = max(0.0, now - _last_real_pulse)
        if age_seconds >= _stall_after_seconds:
            _reported_stall = True
            _reported_stall_age = age_seconds
            _reported_pulse = _last_real_pulse
            return LivenessTransition("stalled", age_seconds)
        return None
