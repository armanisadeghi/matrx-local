"""``local_window list`` said "Failed to list windows: " and nothing else.

That blank was asyncio's TimeoutError (15 s walking every window of every
process).  A refusal must carry its reason.

The walk itself is gone (2026-09-22): macOS listing now asks CoreGraphics,
which answers in ~0.1 s, needs no Accessibility grant, and carries the window
id.  Naming the app no longer narrows an AppleScript walk — it resolves the app
through ``app.services.app_identity`` and filters on that process's pid.  The
timeout paths above still stand: the Windows and Linux branches walk, and a
hung CoreGraphics call would surface the same way.
"""

from __future__ import annotations

import asyncio
import pathlib

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


def test_the_macos_listing_no_longer_walks_applescript_at_all():
    """The 15-second blank error had one cause: the walk. It is gone."""
    source = pathlib.Path(wm.__file__).read_text()
    listing = source[source.index("def _mac_windows_via_quartz"): source.index("async def _list_windows_windows")]
    assert "osascript" not in listing
    assert "tell application" not in listing
    assert "kCGWindowOwnerPID" in listing


def test_naming_the_app_scopes_the_listing_to_that_process(monkeypatch):
    """The filter is a pid, not a name: the window owner name is the
    executable ("Kindle"), which is not the AppleScript name ("Amazon
    Kindle") and not always the Dock name."""
    from app.services.app_identity import RunningApp

    kindle = RunningApp("Kindle", "com.amazon.Lassen", 56683, "Kindle", "Amazon Kindle.app")

    async def one_app(_wanted):
        return kindle

    seen: list[int | None] = []

    def fake_quartz(pid):
        seen.append(pid)
        return ([], True)

    monkeypatch.setattr(wm, "require_running_app", one_app)
    monkeypatch.setattr(wm, "_mac_windows_via_quartz", fake_quartz)
    monkeypatch.setitem(wm.PLATFORM, "is_mac", True)
    asyncio.run(wm.tool_list_windows(None, app_filter="Amazon Kindle"))
    assert seen == [56683]
