"""Guards for the BookCapture tool surface.

The subject here is the refusal path: what the tool does when the operating
system has not granted it the screen or the keyboard. It must say which grant
is missing, in a sentence a person can act on, and it must not have touched
the screen to find out.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.permissions.checker import PermissionResult, PermissionStatus
from app.tools.catalog import get_catalog
from app.tools.dispatcher import TOOL_HANDLERS
from app.tools.session import ToolSession
from app.tools.tools import book_capture as tool_mod
from app.tools.types import ToolResultType


def _granted(name: str) -> PermissionResult:
    return PermissionResult(permission=name, status=PermissionStatus.GRANTED)


def _denied(name: str) -> PermissionResult:
    return PermissionResult(
        permission=name,
        status=PermissionStatus.DENIED,
        details=f"{name} permission denied",
        user_details=f"{name.replace('_', ' ').title()} is switched off",
    )


@pytest.fixture
def on_mac(monkeypatch):
    monkeypatch.setitem(tool_mod.PLATFORM, "is_mac", True)


@pytest.fixture
def screen_untouched(monkeypatch):
    """Fail loudly if the tool reaches for the screen on a refusal path."""

    def _forbidden(*_args, **_kwargs):
        raise AssertionError(
            "the tool touched the screen before its permissions were settled"
        )

    monkeypatch.setattr(tool_mod, "build_driver", _forbidden)
    monkeypatch.setattr(tool_mod, "capture_book", _forbidden)


def _permissions(monkeypatch, *, screen: PermissionResult, accessibility: PermissionResult):
    import app.services.permissions.checker as checker

    async def _screen() -> PermissionResult:
        return screen

    async def _access() -> PermissionResult:
        return accessibility

    monkeypatch.setattr(checker, "check_screen_recording", _screen)
    monkeypatch.setattr(checker, "check_accessibility", _access)


def _call(**kwargs):
    session = ToolSession()
    return asyncio.run(tool_mod.tool_book_capture(session, **kwargs))


# ── the honest refusals ──────────────────────────────────────────────────


def test_without_screen_recording_it_names_that_grant_and_stops(
    monkeypatch, on_mac, screen_untouched
):
    _permissions(
        monkeypatch,
        screen=_denied("screen_recording"),
        accessibility=_granted("accessibility"),
    )
    result = _call(app_name="Books")

    assert result.type is ToolResultType.ERROR
    assert result.action_needed is not None
    assert result.action_needed.details["permission_key"] == "screen_recording"
    assert result.action_needed.code == "screen_recording_required"
    assert "Screen Recording" in result.output
    # The message is a sentence a person can act on, not a status code.
    assert result.action_needed.action.kind == "request_os_permission"
    assert result.action_needed.action.label


def test_without_accessibility_it_names_that_grant_and_stops(
    monkeypatch, on_mac, screen_untouched
):
    _permissions(
        monkeypatch,
        screen=_granted("screen_recording"),
        accessibility=_denied("accessibility"),
    )
    result = _call(app_name="Books")

    assert result.type is ToolResultType.ERROR
    assert result.action_needed is not None
    assert result.action_needed.details["permission_key"] == "accessibility"
    assert "Accessibility" in result.output


def test_a_not_yet_asked_permission_is_still_a_refusal_not_a_run(
    monkeypatch, on_mac, screen_untouched
):
    """NOT_DETERMINED is not granted, and the tool must not proceed on it."""
    _permissions(
        monkeypatch,
        screen=PermissionResult(
            permission="screen_recording", status=PermissionStatus.NOT_DETERMINED
        ),
        accessibility=_granted("accessibility"),
    )
    result = _call(app_name="Books")

    assert result.type is ToolResultType.ERROR
    assert result.action_needed.details["permission_key"] == "screen_recording"


def test_off_macos_it_refuses_by_name(monkeypatch, screen_untouched):
    monkeypatch.setitem(tool_mod.PLATFORM, "is_mac", False)
    result = _call(app_name="Books")

    assert result.type is ToolResultType.ERROR
    assert "only available on macOS" in result.output


def test_an_unknown_page_turn_key_lists_the_usable_ones(
    monkeypatch, on_mac, screen_untouched
):
    _permissions(
        monkeypatch,
        screen=_granted("screen_recording"),
        accessibility=_granted("accessibility"),
    )
    result = _call(app_name="Books", next_page_key="banana")

    assert result.type is ToolResultType.ERROR
    assert "page_down" in result.output and "space" in result.output


def test_a_bad_knob_is_refused_in_words(monkeypatch, on_mac, screen_untouched):
    _permissions(
        monkeypatch,
        screen=_granted("screen_recording"),
        accessibility=_granted("accessibility"),
    )
    result = _call(app_name="Books", crop_top=0.9)

    assert result.type is ToolResultType.ERROR
    assert "out of range" in result.output


# ── the registration ─────────────────────────────────────────────────────


def test_the_tool_is_registered_for_the_platform_to_call():
    assert TOOL_HANDLERS["BookCapture"] is tool_mod.tool_book_capture

    entry = next(e for e in get_catalog() if e.dispatcher_name == "BookCapture")
    assert entry.cloud_name == "local_book_capture"
    assert entry.platforms == ("darwin",)
    # A book is long: the default 120s tool timeout would kill a real capture.
    assert entry.timeout_seconds >= 600

    properties = entry.input_schema["properties"]
    assert entry.input_schema["required"] == ["app_name"]
    for knob in (
        "max_pages",
        "next_page_key",
        "page_delay_seconds",
        "page_change_threshold",
        "crop_top",
        "crop_bottom",
        "crop_left",
        "crop_right",
        "ocr",
    ):
        assert knob in properties, f"{knob} is not exposed as a knob"
        assert properties[knob].get("description"), f"{knob} has no description"
