from __future__ import annotations

import asyncio
import errno
from types import SimpleNamespace
from unittest.mock import patch

from app.services.action_needed import filesystem_access_needed, os_permission_needed
from app.tools.session import ToolSession
from app.tools import dispatcher
from app.tools.types import ToolResult, ToolResultType
from app.tools.tools import calendar_tools, system
from app.tools.tools.file_ops import _io_error_result


def test_os_permission_builder_is_stable_and_explicit():
    first = os_permission_needed(
        feature="Desktop screenshot",
        permission_key="screen_recording",
        source="tool.screenshot",
    )
    second = os_permission_needed(
        feature="Desktop screenshot",
        permission_key="screen_recording",
        source="tool.screenshot",
    )
    assert first.fingerprint == second.fingerprint
    assert first.action.kind == "request_os_permission"
    assert first.action.permission_key == "screen_recording"


def test_filesystem_builder_never_claims_fda_from_bare_eacces():
    item = filesystem_access_needed(
        feature="Files",
        path="/protected/example.txt",
        operation="read",
        source="tool.file_ops",
    )
    assert item.code == "filesystem_access_denied"
    assert "Full Disk Access is only suggested if diagnostics confirm it" in item.message


def test_generic_file_error_only_emits_action_for_permission_errno():
    denied = _io_error_result(
        path="/protected/file",
        operation="read",
        prefix="Cannot read file",
        exc=PermissionError(errno.EACCES, "denied"),
    )
    missing = _io_error_result(
        path="/missing/file",
        operation="read",
        prefix="Cannot read file",
        exc=OSError(errno.ENOENT, "missing"),
    )
    assert denied.action_needed is not None
    assert missing.action_needed is None


def test_calendar_permission_failure_returns_structured_action():
    async def denied(*_args, **_kwargs):
        raise PermissionError("denied")

    with (
        patch.dict(calendar_tools.PLATFORM, {"is_mac": True}),
        patch.object(asyncio.BaseEventLoop, "run_in_executor", denied),
    ):
        result = asyncio.run(calendar_tools.tool_list_events(ToolSession()))

    assert result.action_needed is not None
    assert result.action_needed.action.permission_key == "calendar"


def test_screenshot_permission_failure_returns_structured_action(monkeypatch):
    from app.services.permissions import checker

    async def not_determined():
        return SimpleNamespace(
            status=checker.PermissionStatus.NOT_DETERMINED,
            user_details="Screen capture hasn't been set up",
            details="not requested",
        )

    monkeypatch.setitem(system.PLATFORM, "is_mac", True)
    monkeypatch.setattr(checker, "check_screen_recording", not_determined)
    monkeypatch.setattr(
        system,
        "_grab_screenshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("capture must not run before explicit grant")
        ),
    )
    result = asyncio.run(system.tool_screenshot(ToolSession()))
    assert result.action_needed is not None
    assert result.action_needed.action.permission_key == "screen_recording"


def test_legacy_capability_metadata_is_canonicalized_and_clears_on_retry(
    monkeypatch,
):
    async def missing(*, session):
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Install the optional runtime.",
            metadata={"fix_capability_id": "browser_automation"},
        )

    monkeypatch.setitem(dispatcher.TOOL_HANDLERS, "CapabilityProbe", missing)
    first = asyncio.run(
        dispatcher.dispatch("CapabilityProbe", {}, ToolSession())
    )
    assert first.action_needed is not None
    assert first.action_needed.kind.value == "capability_install"
    assert first.action_needed.details == {"capability_id": "browser_automation"}
    assert first.metadata == {}

    async def ready(*, session):
        return ToolResult(output="ready")

    monkeypatch.setitem(dispatcher.TOOL_HANDLERS, "CapabilityProbe", ready)
    recovered = asyncio.run(
        dispatcher.dispatch("CapabilityProbe", {}, ToolSession())
    )
    assert recovered.action_needed is None
