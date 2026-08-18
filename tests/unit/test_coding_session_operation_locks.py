"""Manual clicks and background passes must never overlap one reconciliation."""

from __future__ import annotations

import asyncio
from types import MethodType
from typing import Any

from app.services.coding_sessions.capture_reconciler import ClaudeCaptureReconciler
from app.services.coding_sessions.title_sync import ClaudeSessionMetadataReconciler


async def _assert_serialized(instance: Any, method_name: str) -> None:
    active = 0
    peak = 0

    async def fake_once(self: Any, *, dry_run: bool = False) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {"dry_run": dry_run}

    instance._sync_lock = asyncio.Lock()
    if method_name == "reconcile":
        instance._lock = asyncio.Lock()
        instance._reconcile_once = MethodType(fake_once, instance)
    else:
        instance._sync_once = MethodType(fake_once, instance)

    reports = await asyncio.gather(
        getattr(instance, method_name)(dry_run=False),
        getattr(instance, method_name)(dry_run=True),
    )
    assert peak == 1
    assert {report["dry_run"] for report in reports} == {False, True}


def test_capture_manual_and_background_passes_are_serialized() -> None:
    async def exercise() -> None:
        reconciler = object.__new__(ClaudeCaptureReconciler)
        await _assert_serialized(reconciler, "reconcile")

    asyncio.run(exercise())


def test_title_sync_clicks_are_serialized() -> None:
    async def exercise() -> None:
        reconciler = object.__new__(ClaudeSessionMetadataReconciler)
        await _assert_serialized(reconciler, "sync")

    asyncio.run(exercise())
