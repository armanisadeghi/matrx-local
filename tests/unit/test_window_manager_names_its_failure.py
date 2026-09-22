"""``local_window list`` said "Failed to list windows: " and nothing else.

That blank was asyncio's TimeoutError (15 s walking every window of every
process).  A refusal must carry its reason, and naming the app must narrow
the walk.
"""

from __future__ import annotations

import asyncio

from app.tools.tools import window_manager as wm


def test_a_timeout_is_named_and_says_how_to_avoid_it(monkeypatch):
    async def slow(_filter):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(wm, "_list_windows_macos", slow)
    monkeypatch.setitem(wm.PLATFORM, "is_mac", True)
    result = asyncio.run(wm.tool_list_windows(None, None))
    assert "15-second" in result.output
    assert "Failed to list windows: " not in result.output


def test_an_empty_exception_still_names_its_class(monkeypatch):
    async def blank(_filter):
        raise RuntimeError("")
    monkeypatch.setattr(wm, "_list_windows_macos", blank)
    monkeypatch.setitem(wm.PLATFORM, "is_mac", True)
    result = asyncio.run(wm.tool_list_windows(None, None))
    assert result.output.endswith("RuntimeError")


def test_naming_the_app_narrows_the_applescript_walk():
    assert "__FILTER__" in wm._MAC_WINDOW_LIST_SCRIPT
    quoted = wm._applescript_string('Kin"dle')
    assert quoted == '"Kin\\"dle"'
