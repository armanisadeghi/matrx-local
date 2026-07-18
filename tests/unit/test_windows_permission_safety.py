"""Regression tests for passive Windows permission checks and scoped grants."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api import permissions_routes
from app.services.permissions import checker


def _select_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(checker.PLATFORM, "is_windows", True)
    monkeypatch.setitem(checker.PLATFORM, "is_mac", False)
    monkeypatch.setitem(checker.PLATFORM, "is_linux", False)
    monkeypatch.setitem(checker.PLATFORM, "system", "Windows")
    monkeypatch.setitem(permissions_routes.PLATFORM, "is_windows", True)
    monkeypatch.setitem(permissions_routes.PLATFORM, "system", "Windows")


async def _async_value(value: Any) -> Any:
    return value


@pytest.mark.anyio
async def test_windows_permission_checks_never_write_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_windows(monkeypatch)
    monkeypatch.setitem(checker.CAPABILITIES, "powershell_path", None)
    monkeypatch.setattr(
        checker,
        "_win_consent_status",
        lambda _key: checker.PermissionStatus.DENIED,
    )

    def _unexpected_write(key: str) -> bool:
        raise AssertionError(f"read-only permission check tried to write {key}")

    monkeypatch.setattr(checker, "_win_force_allow", _unexpected_write)
    monkeypatch.setattr(
        checker,
        "_win_enum_audio_endpoints",
        lambda: _async_value([{"FriendlyName": "Test Microphone", "Status": "OK"}]),
    )
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(query_devices=lambda: []),
    )

    await checker.check_microphone()
    await checker.check_camera()
    await checker.check_bluetooth()
    await checker.check_location()


@pytest.mark.anyio
async def test_windows_grant_mutates_only_requested_permission_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[str] = []
    monkeypatch.setattr(
        checker,
        "_win_force_allow",
        lambda key: written.append(key) is None,
    )

    result = await checker.grant_windows_permissions(
        ["camera", "microphone", "camera"]
    )

    assert written == ["webcam", "microphone"]
    assert result == {"webcam": True, "microphone": True}


@pytest.mark.anyio
async def test_windows_grant_rejects_unknown_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[str] = []
    monkeypatch.setattr(
        checker,
        "_win_force_allow",
        lambda key: written.append(key) is None,
    )

    with pytest.raises(ValueError, match="made_up_permission"):
        await checker.grant_windows_permissions(["microphone", "made_up_permission"])

    assert written == []


@pytest.mark.anyio
async def test_grant_route_passes_only_explicit_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_windows(monkeypatch)
    received: list[str] = []

    async def _grant(names: list[str]) -> dict[str, bool]:
        received.extend(names)
        return {"bluetooth": True, "bluetoothSync": True}

    monkeypatch.setattr(permissions_routes, "grant_windows_permissions", _grant)

    response = await permissions_routes.grant_permissions(
        permissions_routes.GrantPermissionsRequest(
            permissions=["bluetooth", "bluetooth"]
        )
    )

    assert received == ["bluetooth"]
    assert response["permissions"] == ["bluetooth"]
    assert response["details"] == {"bluetooth": True, "bluetoothSync": True}


@pytest.mark.anyio
async def test_grant_route_rejects_non_grantable_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_windows(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        await permissions_routes.grant_permissions(
            permissions_routes.GrantPermissionsRequest(
                permissions=["accessibility"]
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "unsupported_windows_permission"


@pytest.mark.anyio
async def test_single_permission_catalog_covers_full_scan_and_404s_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert list(checker.PERMISSION_CHECKERS) == [
        "microphone",
        "camera",
        "accessibility",
        "bluetooth",
        "network",
        "wifi",
        "screen_recording",
        "location",
        "contacts",
        "calendar",
        "reminders",
        "photos",
        "messages",
        "mail",
        "speech_recognition",
    ]

    async def _contacts() -> checker.PermissionResult:
        return checker.PermissionResult(
            permission="contacts",
            status=checker.PermissionStatus.GRANTED,
        )

    monkeypatch.setitem(checker.PERMISSION_CHECKERS, "contacts", _contacts)
    result = await permissions_routes.get_permission("contacts")
    assert result["status"] == "granted"

    with pytest.raises(HTTPException) as exc_info:
        await permissions_routes.get_permission("made-up")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "unknown_permission"
    assert "contacts" in exc_info.value.detail["available"]
