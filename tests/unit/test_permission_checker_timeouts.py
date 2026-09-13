from __future__ import annotations

import asyncio

from app.services.permissions import checker


def test_permission_scan_never_logs_a_blank_exception(monkeypatch, caplog) -> None:
    async def blank_timeout() -> checker.PermissionResult:
        raise TimeoutError

    monkeypatch.setattr(
        checker,
        "PERMISSION_CHECKERS",
        {"camera": blank_timeout},
    )

    result = asyncio.run(checker.check_all_permissions())

    assert result[0]["details"] == "Check failed: TimeoutError"
    assert "ERROR: TimeoutError" in caplog.text
