"""One identity authority: no tool may name a macOS app by the person's word.

On 2026-09-21 the owner's first real desktop-agent run failed twice on the
same app. ``local_window focus app_name="Kindle"`` and
``local_process focus_app "Kindle"`` both raised AppleScript **-1728, no such
application**: the person's word and the process name are "Kindle" but the
bundle name AppleScript answers to is "Amazon Kindle". A fix landed for the
book capture alone (5f681d6b11); every other desktop tool kept the bug.

The class is: *a caller-supplied app name interpolated into an AppleScript
`tell application` / `application process` specifier, or matched against a
CoreGraphics window owner name.* These guards close it for the whole repo.

Two kinds of guard:

* :func:`test_no_tool_interpolates_an_app_name_into_an_applescript_target`
  is static — it fails the moment anyone writes the pattern again anywhere
  under ``app/``.
* the per-tool tests drive the real tool functions with the real macOS table
  captured from the owner's Mac on 2026-09-22 and assert on the argv handed
  to ``osascript``.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from app.services.app_identity import RunningApp, not_running_message

REPO = pathlib.Path(__file__).resolve().parents[2]
APP_DIR = REPO / "app"
IDENTITY_DIR = APP_DIR / "services" / "app_identity"

# The real Launch Services table from Arman's Mac, `lsappinfo list`, 2026-09-22.
KINDLE = RunningApp("Kindle", "com.amazon.Lassen", 56683, "Kindle", "Amazon Kindle.app")
CURSOR = RunningApp("Cursor", "com.todesktop.230313mzl4w4u92", 710, "Cursor", "Cursor.app")
PREVIEW = RunningApp("Preview", "com.apple.Preview", 30175, "Preview", "Preview.app")
TABLE = [CURSOR, PREVIEW, KINDLE]

# `tell application "…{` / `application process "…{` — an f-string hole in an
# AppleScript application specifier is the -1728 bug, always.
_INTERPOLATED_TARGET = re.compile(
    r'(?:tell\s+)?application(?:\s+process)?\s+"[^"\n]*\{'
)


def _python_sources() -> list[pathlib.Path]:
    return [
        p
        for p in APP_DIR.rglob("*.py")
        if "__pycache__" not in p.parts and IDENTITY_DIR not in p.parents
    ]


def test_no_tool_interpolates_an_app_name_into_an_applescript_target():
    offenders = []
    for path in _python_sources():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if _INTERPOLATED_TARGET.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "An app name is being interpolated into an AppleScript application "
        "specifier. Resolve it through app.services.app_identity and use "
        "RunningApp.applescript_target / .process_target instead:\n"
        + "\n".join(offenders)
    )


def test_no_code_matches_a_wanted_app_name_against_a_window_owner_name():
    """CoreGraphics owner names are for DISPLAY; windows are found by pid."""
    offenders = []
    for path in _python_sources():
        lines = path.read_text().splitlines()
        for lineno, line in enumerate(lines, 1):
            if "kCGWindowOwnerName" not in line:
                continue
            window = "\n".join(lines[max(0, lineno - 3): lineno + 3])
            if re.search(r"(wanted|app_name|app_filter|application)\b.*\.lower\(\)", window) or (
                "kCGWindowOwnerName" in line and " in owner" in window
            ):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "A window is being selected by matching a caller-supplied name against "
        "kCGWindowOwnerName. Resolve the app first and filter on "
        "kCGWindowOwnerPID:\n" + "\n".join(offenders)
    )


# ── per-tool behaviour ───────────────────────────────────────────────────


class _FakeProc:
    returncode = 0

    async def communicate(self):
        return b"", b""


@pytest.fixture
def osascript_calls(monkeypatch):
    """Capture every osascript argv a tool would run, and run none of them."""
    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        calls.append(list(args))
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


@pytest.fixture
def running_table(monkeypatch):
    async def fake_list(timeout: float = 10.0):
        return list(TABLE)

    monkeypatch.setattr(
        "app.services.app_identity.identity.list_running_apps", fake_list
    )
    return TABLE


def _script(calls: list[list[str]]) -> str:
    assert calls, "the tool ran no osascript at all"
    return "\n".join(a for call in calls for a in call)


def _mac_only():
    from app.common.platform_ctx import PLATFORM

    if not PLATFORM["is_mac"]:
        pytest.skip("macOS app identity")


TOOLS_THAT_TAKE_AN_APP_NAME = "window focus, window move, window minimize, process focus_app, type text, hotkey, mouse click"


def test_window_focus_addresses_kindle_by_bundle_id(osascript_calls, running_table):
    _mac_only()
    from app.tools.tools.window_manager import tool_focus_window

    result = asyncio.run(tool_focus_window(None, app_name="Kindle"))
    assert result.type.value != "error", result.output
    script = _script(osascript_calls)
    assert 'application id "com.amazon.Lassen"' in script
    assert 'tell application "Kindle"' not in script


def test_process_focus_app_addresses_kindle_by_bundle_id(osascript_calls, running_table):
    _mac_only()
    from app.tools.tools.process_manager import tool_focus_app

    result = asyncio.run(tool_focus_app(None, application="Kindle"))
    assert result.type.value != "error", result.output
    script = _script(osascript_calls)
    assert 'application id "com.amazon.Lassen"' in script
    assert 'tell application "Kindle"' not in script


def test_window_move_addresses_the_process_by_pid(osascript_calls, running_table):
    _mac_only()
    from app.tools.tools.window_manager import tool_move_window

    result = asyncio.run(tool_move_window(None, app_name="Kindle", x=10, y=10))
    assert result.type.value != "error", result.output
    script = _script(osascript_calls)
    assert "unix id is 56683" in script
    assert 'application process "Kindle"' not in script


def test_window_minimize_addresses_the_process_by_pid(osascript_calls, running_table):
    _mac_only()
    from app.tools.tools.window_manager import tool_minimize_window

    result = asyncio.run(tool_minimize_window(None, app_name="Kindle"))
    assert result.type.value != "error", result.output
    assert "unix id is 56683" in _script(osascript_calls)


def test_type_text_activates_by_bundle_id(osascript_calls, running_table, monkeypatch):
    _mac_only()
    import app.tools.tools.input_automation as ia

    async def no_preflight(_feature):
        return None

    monkeypatch.setattr(ia, "_accessibility_preflight", no_preflight)
    result = asyncio.run(ia.tool_type_text(None, text="hi", app_name="Kindle"))
    assert result.type.value != "error", result.output
    script = _script(osascript_calls)
    assert 'application id "com.amazon.Lassen"' in script
    assert 'tell application "Kindle"' not in script


def test_hotkey_activates_by_bundle_id(osascript_calls, running_table, monkeypatch):
    _mac_only()
    import app.tools.tools.input_automation as ia

    async def no_preflight(_feature):
        return None

    monkeypatch.setattr(ia, "_accessibility_preflight", no_preflight)
    result = asyncio.run(ia.tool_hotkey(None, keys="cmd+right", app_name="Kindle"))
    assert result.type.value != "error", result.output
    script = _script(osascript_calls)
    assert 'application id "com.amazon.Lassen"' in script
    assert 'tell application "Kindle"' not in script


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(("window_focus",), id="window focus"),
        pytest.param(("process_focus",), id="process focus_app"),
        pytest.param(("window_list",), id="window list"),
    ],
)
def test_an_app_that_is_not_running_is_refused_naming_what_is(
    call, osascript_calls, running_table
):
    _mac_only()
    from app.tools.tools.process_manager import tool_focus_app
    from app.tools.tools.window_manager import tool_focus_window, tool_list_windows

    which = call[0]
    if which == "window_focus":
        result = asyncio.run(tool_focus_window(None, app_name="Calibre"))
    elif which == "process_focus":
        result = asyncio.run(tool_focus_app(None, application="Calibre"))
    else:
        result = asyncio.run(tool_list_windows(None, app_filter="Calibre"))

    assert result.type.value == "error"
    assert "is not running" in result.output
    # The refusal must name something that IS running — a dead end is a defect.
    assert any(app.spoken_name in result.output for app in TABLE), result.output
    assert not osascript_calls, "a refusal must not touch the user's screen"


def test_the_refusal_sentence_names_a_near_miss_first():
    msg = not_running_message("Kindl", TABLE)
    assert "Kindle" in msg and "is not running" in msg


def test_an_app_with_no_accessible_windows_says_so_instead_of_minus_1719():
    """Kindle publishes no windows to accessibility; -1719 tells nobody that."""
    from app.tools.tools.window_manager import _no_accessible_windows

    msg = _no_accessible_windows(
        b"99:103: execution error: System Events got an error: cannot get "
        b"window 1 of application process 1 whose unix id = 56683. Invalid index. (-1719)",
        KINDLE,
    )
    assert msg is not None
    assert "Kindle is running" in msg
    assert "brought to the front" in msg and "captured" in msg
    assert "-1719" not in msg
    assert _no_accessible_windows(b"some unrelated failure", KINDLE) is None
