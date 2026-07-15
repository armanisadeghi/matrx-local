from __future__ import annotations

import asyncio

import pytest

from app.launcher import ServiceController, ServiceRegistry


@pytest.mark.anyio
async def test_recovery_registry_runs_restart_in_order() -> None:
    registry = ServiceRegistry()
    calls: list[str] = []
    registry.register_controller(
        "demo",
        ServiceController(
            stop=lambda: calls.append("stop"),
            start=lambda: calls.append("start"),
        ),
    )

    result = await registry.recover("demo", "restart", timeout_s=1)

    assert calls == ["stop", "start"]
    assert result["status"] == "succeeded"
    assert registry.snapshot()["services"]["demo"]["capabilities"] == [
        "stop", "start", "restart"
    ]


@pytest.mark.anyio
async def test_recovery_registry_times_out_without_unsafe_fallback() -> None:
    async def stuck() -> None:
        await asyncio.Event().wait()

    registry = ServiceRegistry()
    registry.register_controller("demo", ServiceController(repair=stuck))

    with pytest.raises(TimeoutError):
        await registry.recover("demo", "repair", timeout_s=0.01)

    operation = registry.snapshot()["recovery_operations"][-1]
    assert operation["status"] == "timed_out"
    assert "timed out" in operation["error"]


@pytest.mark.anyio
async def test_recovery_registry_rejects_unsupported_action() -> None:
    registry = ServiceRegistry()
    registry.register_controller("demo", ServiceController(probe=lambda: {}))

    with pytest.raises(NotImplementedError):
        await registry.recover("demo", "stop")


@pytest.mark.anyio
async def test_restart_does_not_start_when_stop_reports_incomplete() -> None:
    started = False

    def start() -> None:
        nonlocal started
        started = True

    registry = ServiceRegistry()
    registry.register_controller(
        "demo",
        ServiceController(
            stop=lambda: {"stopped": False, "reason": "requires engine restart"},
            start=start,
        ),
    )

    with pytest.raises(RuntimeError, match="requires engine restart"):
        await registry.recover("demo", "restart")
    assert started is False
