"""ONE rule for what a WebSocket close means, for every WS route in the engine.

Audit row SR-08 counted 28 close-code 1005 and several 1006 events in 72 hours
and could not say whether any of them were real, because the ``/ws`` route
logged EVERY disconnect it did not explicitly whitelist — 1000 (normal close)
included — as ``ERROR WebSocket error: ...``. A log line that screams on a
normal page reload is not a signal; it hides the one drop that matters and it
tells the reader nothing about the code it just swallowed.

1005 and 1006 deserve their own sentence. RFC 6455 reserves both: they are
never sent on the wire, they are synthesised locally by the server's WebSocket
library when the peer's connection disappeared WITHOUT a close handshake. So
they mean exactly one thing — "the client went away without saying goodbye" —
which is the normal shape of a webview reload, a window teardown, a machine
sleeping, and (correlated 4 times in the audit window) a sign-out that revokes
the token and drops the socket. It is an observation, not an error, and the log
must say which it is.

Every WS route in this engine classifies through here so the same close code
can never mean two different severities in two different log files.
"""

from __future__ import annotations

from dataclasses import dataclass

# What each close code we understand actually means, in one place.
_CLOSE_MEANINGS: dict[int, str] = {
    1000: "normal close",
    1001: "going away (page unload or app close)",
    1005: "no close code — the client's connection vanished without a close "
    "handshake (reload, window teardown, sleep, or a sign-out dropping the socket)",
    1006: "abnormal close — the connection dropped without a close handshake "
    "(network loss, renderer teardown, or a killed peer)",
    1012: "service restart (engine restart or update)",
}

# Closes that are part of normal life. Quiet (debug) for the ones a client
# announces deliberately; visible (info) for the ones we only infer, because
# those are the ones an investigation needs to be able to count.
_QUIET_CODES = frozenset({1000, 1001, 1012})
_OBSERVED_CODES = frozenset({1005, 1006})


@dataclass(frozen=True)
class WsCloseClassification:
    """How one WebSocket close should be recorded."""

    code: int | None
    reason: str | None
    #: A logging level name: ``debug``, ``info`` or ``error``.
    level: str
    #: True when this close is a normal part of a client's lifecycle.
    expected: bool
    #: One sentence naming the code and what it means.
    summary: str


def classify_ws_close(code: int | None, reason: str | None = None) -> WsCloseClassification:
    """Classify a WebSocket close. Never raises, never returns an empty summary."""
    detail = (reason or "").strip()
    meaning = _CLOSE_MEANINGS.get(code) if code is not None else None
    if meaning is None:
        return WsCloseClassification(
            code=code,
            reason=detail or None,
            level="error",
            expected=False,
            summary=(
                f"closed with unexpected code {code}"
                + (f": {detail}" if detail else "")
            ),
        )
    summary = f"closed code={code} ({meaning})" + (f": {detail}" if detail else "")
    if code in _QUIET_CODES:
        return WsCloseClassification(code, detail or None, "debug", True, summary)
    if code in _OBSERVED_CODES:
        return WsCloseClassification(code, detail or None, "info", True, summary)
    return WsCloseClassification(code, detail or None, "error", False, summary)
