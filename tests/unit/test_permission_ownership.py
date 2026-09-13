"""Who owns a macOS permission — and what the engine is allowed to claim.

Three shipped lies, one test file:

1. Wi-Fi was listed as a "permission". It is a network inventory scan
   (airport / system_profiler, 10-20s) with no TCC service and nothing to
   grant, and it single-handedly made GET /devices/permissions and
   GET /setup/status take 10-20 seconds.
2. The engine offered to REQUEST Contacts/Calendar/Reminders/Photos/Location/
   Speech Recognition. The helper bundle has no run loop, so those requests are
   silently ignored — the app never even appears in System Settings → Location.
   The desktop app (Tauri) owns them; the engine only reports status.
3. Mail came back UNKNOWN, which the Devices page renders as "Not Granted" —
   telling the user something was refused when nobody was ever asked.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import permissions_routes
from app.services.permissions import checker


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_wifi_is_not_a_permission() -> None:
    assert "wifi" not in checker.PERMISSION_CHECKERS
    # The scan lives with the WifiNetworks tool (/devices/wifi); the checker
    # module no longer carries a copy of it (no-legacy).
    assert not hasattr(checker, "check_wifi")


@pytest.mark.anyio
async def test_request_route_refuses_location_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(permissions_routes.PLATFORM, "is_mac", True)

    with pytest.raises(HTTPException) as exc_info:
        await permissions_routes.request_permission("location")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "permission_owned_by_desktop_app"
    assert exc_info.value.detail["permission"] == "location"
    assert "desktop app" in exc_info.value.detail["message"]


@pytest.mark.anyio
async def test_engine_request_helper_refuses_location_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(checker.PLATFORM, "is_mac", True)

    with pytest.raises(ValueError) as exc_info:
        await checker.request_engine_permission("location")

    assert "desktop app" in str(exc_info.value)


@pytest.mark.anyio
async def test_mail_is_not_determined_not_a_denial() -> None:
    result = await checker.check_mail()

    if checker.PLATFORM["is_mac"]:
        assert result.status is checker.PermissionStatus.NOT_DETERMINED
        assert result.to_dict()["status"] == "not_determined"
        assert "first time a Mail tool runs" in result.details
        assert result.deep_link
    else:
        assert result.status is checker.PermissionStatus.UNAVAILABLE
