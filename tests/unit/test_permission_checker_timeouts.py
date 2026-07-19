from __future__ import annotations

import asyncio

from app.services.permissions import checker


def test_wifi_scan_allows_macos_fallback_deadline(monkeypatch) -> None:
    observed: dict[str, float | None] = {}

    async def wifi_check() -> checker.PermissionResult:
        return checker.PermissionResult(
            permission="wifi",
            status=checker.PermissionStatus.NOT_DETERMINED,
        )

    async def fake_wait_for(awaitable, timeout=None):
        observed["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    result = asyncio.run(checker._run_permission_check("wifi", wifi_check))

    assert result.permission == "wifi"
    assert observed["timeout"] == 25


def test_permission_scan_never_logs_a_blank_exception(monkeypatch, caplog) -> None:
    async def blank_timeout() -> checker.PermissionResult:
        raise TimeoutError

    monkeypatch.setattr(
        checker,
        "PERMISSION_CHECKERS",
        {"wifi": blank_timeout},
    )

    result = asyncio.run(checker.check_all_permissions())

    assert result[0]["details"] == "Check failed: TimeoutError"
    assert "ERROR: TimeoutError" in caplog.text
