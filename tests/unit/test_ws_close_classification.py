"""A normal WebSocket close must never be logged as an engine error.

Audit row SR-08 could not decide whether 28 close-code 1005 events were real
drops, because the ``/ws`` route logged every code it had not whitelisted —
1000 (normal close) included — as ``ERROR WebSocket error: ...``, with no
mention of the code's meaning. That is two defects in one line: a false alarm
on every page reload, and no countable record of the closes that matter.

These pin the rule for every WS route in the engine.
"""

from __future__ import annotations

import pytest

from app.api.ws_close import classify_ws_close


@pytest.mark.parametrize("code", [1000, 1001, 1012])
def test_a_deliberate_close_is_quiet_and_expected(code):
    close = classify_ws_close(code)
    assert close.expected is True
    assert close.level == "debug"
    assert str(code) in close.summary


@pytest.mark.parametrize("code", [1005, 1006])
def test_a_handshake_less_close_is_recorded_but_never_an_error(code):
    """THE GUARD. 1005/1006 are synthesised locally when the peer vanishes —
    an observation. Visible (countable), never ERROR."""
    close = classify_ws_close(code)
    assert close.level == "info", "a client that went away is not an engine failure"
    assert close.expected is True
    # The line has to explain itself: SR-08 existed because it did not.
    assert "close handshake" in close.summary
    assert str(code) in close.summary


def test_a_normal_close_is_never_reported_as_an_error():
    assert classify_ws_close(1000).level != "error"


def test_an_unknown_code_still_screams():
    close = classify_ws_close(4999, "custom app close")
    assert close.level == "error"
    assert close.expected is False
    assert "4999" in close.summary and "custom app close" in close.summary


def test_a_missing_code_is_never_silent_and_never_empty():
    close = classify_ws_close(None)
    assert close.level == "error"
    assert close.summary.strip()


def test_the_close_reason_reaches_the_line_when_there_is_one():
    close = classify_ws_close(1006, "  connection reset  ")
    assert "connection reset" in close.summary
    assert close.reason == "connection reset"
