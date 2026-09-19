"""Private unit coverage for passive server event-loop stall capture."""

from __future__ import annotations

import ast
import asyncio
import copy
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from app.common import event_loop_liveness as liveness


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_liveness_state() -> None:
    liveness.disarm()
    yield
    liveness.disarm()


def _stack_formatter() -> object:
    """Extract the existing redactor without importing the sidecar entrypoint."""
    module = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_format_server_thread_stack"
    )
    extracted = ast.Module(body=[copy.deepcopy(function)], type_ignores=[])
    ast.fix_missing_locations(extracted)
    namespace = {"os": os, "sys": sys}
    exec(compile(extracted, "run.py:liveness-stack", "exec"), namespace)
    return namespace["_format_server_thread_stack"]


def _parent_watchdog() -> object:
    """Extract the watchdog seam without importing the sidecar entrypoint."""
    module = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_start_parent_watchdog"
    )
    extracted = ast.Module(body=[copy.deepcopy(function)], type_ignores=[])
    ast.fix_missing_locations(extracted)
    namespace: dict[str, object] = {}
    exec(compile(extracted, "run.py:liveness-watchdog", "exec"), namespace)
    return namespace["_start_parent_watchdog"]


def test_liveness_reports_one_stall_then_one_recovery() -> None:
    liveness.arm_or_pulse(now=10.0, stall_after_seconds=5.0)
    assert liveness.check(now=10.0) is None

    stalled = liveness.check(now=15.0)
    assert stalled == liveness.LivenessTransition(kind="stalled", age_seconds=5.0)
    assert liveness.check(now=16.0) is None

    liveness.arm_or_pulse(now=16.0, stall_after_seconds=5.0)
    recovered = liveness.check(now=16.1)
    assert recovered is not None
    assert recovered.kind == "recovered"
    assert recovered.age_seconds == pytest.approx(5.0)
    assert liveness.check(now=16.2) is None


def test_delayed_watchdog_wake_rebases_without_false_stall() -> None:
    liveness.arm_or_pulse(now=0.0, stall_after_seconds=10.0)
    assert liveness.check(now=0.0) is None

    # A laptop sleep or a descheduled watchdog cannot prove the loop was blocked.
    assert liveness.check(now=100.0) is None
    assert liveness.check(now=109.9) is None
    assert liveness.check(now=110.0) == liveness.LivenessTransition(
        kind="stalled", age_seconds=110.0
    )


def test_oversleep_preserves_reported_stall_until_a_real_pulse() -> None:
    liveness.arm_or_pulse(now=0.0, stall_after_seconds=10.0)
    assert liveness.check(now=0.0) is None
    assert liveness.check(now=10.0) == liveness.LivenessTransition(
        kind="stalled", age_seconds=10.0
    )

    # An overslept watchdog cannot manufacture a recovery or a second error.
    assert liveness.check(now=100.0) is None
    assert liveness.check(now=110.0) is None

    liveness.arm_or_pulse(now=110.1, stall_after_seconds=10.0)
    assert liveness.check(now=110.2) == liveness.LivenessTransition(
        kind="recovered", age_seconds=10.0
    )


def test_startup_and_shutdown_gating_prevent_stall_capture() -> None:
    assert liveness.check(now=100.0) is None


def test_cancelling_before_disarm_prevents_a_queued_pulse_during_teardown() -> None:
    async def scenario() -> None:
        queued = asyncio.Event()
        release = asyncio.Event()

        async def pulse_once() -> None:
            queued.set()
            await release.wait()
            liveness.arm_or_pulse(now=2.0, stall_after_seconds=1.0)

        pulse_task = asyncio.create_task(pulse_once())
        await queued.wait()

        # This is the exact no-await ordering used by lifespan's finally block.
        pulse_task.cancel()
        liveness.disarm()
        release.set()
        await asyncio.sleep(0)  # Model a long teardown yielding to the loop.
        with pytest.raises(asyncio.CancelledError):
            await pulse_task

        assert liveness.check(now=100.0) is None

    asyncio.run(scenario())
    liveness.arm_or_pulse(now=1.0, stall_after_seconds=2.0)
    liveness.disarm()
    assert liveness.check(now=100.0) is None


def test_existing_stack_redactor_captures_a_real_blocked_worker() -> None:
    release = threading.Event()
    entered = threading.Event()

    def blocked_server_worker() -> None:
        entered.set()
        release.wait(timeout=2)

    worker = threading.Thread(target=blocked_server_worker, daemon=True)
    worker.start()
    try:
        assert entered.wait(timeout=1)
        liveness.arm_or_pulse(now=0.0, stall_after_seconds=1.0)
        assert liveness.check(now=0.0) is None
        assert liveness.check(now=1.0) == liveness.LivenessTransition(
            kind="stalled", age_seconds=1.0
        )
        formatter = _stack_formatter()
        stack = formatter(worker.ident)
    finally:
        release.set()
        worker.join(timeout=1)

    assert stack is not None
    assert "blocked_server_worker" in stack
    assert Path(__file__).name in stack
    assert str(Path(__file__).parent) not in stack
    assert len(stack.split(" <- ")) <= 8
    assert len(stack) <= 1_024


def test_parent_watchdog_logs_stall_outside_liveness_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing parent watchdog owns the redacted diagnostic, not the server."""
    records: list[str] = []

    class ShutdownEvent:
        checks = 0

        def is_set(self) -> bool:
            self.checks += 1
            return self.checks > 1

    class InlineThread:
        def __init__(self, *, target: object, **_kwargs: object) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

    class Logger:
        def error(self, message: str, *args: object) -> None:
            records.append(message % args)

    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    watchdog = _parent_watchdog()
    globals_ = watchdog.__globals__
    globals_.update(
        {
            "time": time,
            "sys": types.SimpleNamespace(platform="darwin"),
            "os": types.SimpleNamespace(getppid=lambda: 42),
            "threading": types.SimpleNamespace(Thread=InlineThread),
            "_shutdown_event": ShutdownEvent(),
            "_server_thread": types.SimpleNamespace(ident=123),
            "check_event_loop_liveness": lambda **_kwargs: liveness.LivenessTransition(
                kind="stalled", age_seconds=10.0
            ),
            "_format_server_thread_stack": lambda ident: (
                "blocked_server_worker (private.py:7)" if ident == 123 else None
            ),
            "logger": Logger(),
        }
    )

    watchdog()

    assert records == [
        "[liveness] Server event loop made no progress for 10.0s; "
        "server-thread stack: blocked_server_worker (private.py:7)"
    ]


def test_parent_cleanup_precedes_even_a_blocking_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """A parent-death cleanup must not wait for any liveness/logging work."""
    events: list[str] = []

    class BlockingLog(RuntimeError):
        pass

    class ShutdownEvent:
        def is_set(self) -> bool:
            return False

    class InlineThread:
        def __init__(self, *, target: object, **_kwargs: object) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

    class Logger:
        def warning(self, *_args: object) -> None:
            events.append("warning")
            raise BlockingLog

        def error(self, *_args: object) -> None:
            raise AssertionError("liveness logging must not run after parent loss")

    parent_pids = iter((42, 1))
    server = types.SimpleNamespace(should_exit=False)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    watchdog = _parent_watchdog()
    watchdog.__globals__.update(
        {
            "time": time,
            "sys": types.SimpleNamespace(platform="darwin"),
            "os": types.SimpleNamespace(getppid=lambda: next(parent_pids)),
            "threading": types.SimpleNamespace(Thread=InlineThread),
            "_shutdown_event": ShutdownEvent(),
            "_uvicorn_server": server,
            "remove_discovery_file": lambda: events.append("remove-discovery"),
            "_request_process_shutdown": lambda: events.append("request-shutdown"),
            "_schedule_force_exit": lambda _seconds: events.append("force-exit"),
            "check_event_loop_liveness": lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("parent death must bypass liveness checks")
            ),
            "logger": Logger(),
        }
    )

    with pytest.raises(BlockingLog):
        watchdog()

    assert server.should_exit is True
    assert events == ["remove-discovery", "request-shutdown", "force-exit", "warning"]


def test_lifespan_stops_pulse_before_teardown_on_normal_or_exceptional_exit() -> None:
    """Keep the server lifecycle separate from run.py and teardown race-free."""
    module = ast.parse((REPO_ROOT / "app/main.py").read_text(encoding="utf-8"))
    lifespan = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    lifecycle_try = next(
        statement
        for statement in lifespan.body
        if isinstance(statement, ast.Try)
        and any(
            isinstance(node, ast.Yield)
            for node in ast.walk(statement)
        )
    )

    def _calls_named(statements: list[ast.stmt], name: str) -> bool:
        return any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
            for statement in statements
            for node in ast.walk(statement)
        )

    try_index = lifespan.body.index(lifecycle_try)
    assert _calls_named(lifespan.body[:try_index], "arm_or_pulse")
    assert len(lifecycle_try.finalbody) >= 3
    cancel_index = next(
        index
        for index, statement in enumerate(lifecycle_try.finalbody)
        if isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and statement.value.func.attr == "cancel"
    )
    disarm_index = next(
        index
        for index, statement in enumerate(lifecycle_try.finalbody)
        if isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == "disarm"
    )
    await_cancel_index = next(
        index
        for index, statement in enumerate(lifecycle_try.finalbody)
        if isinstance(statement, ast.Try)
        and any(isinstance(node, ast.Await) for node in ast.walk(statement))
    )
    assert cancel_index < disarm_index < await_cancel_index
    cancel = lifecycle_try.finalbody[cancel_index]
    disarm = lifecycle_try.finalbody[disarm_index]
    await_cancel = lifecycle_try.finalbody[await_cancel_index]
    assert isinstance(cancel, ast.Expr)
    assert isinstance(cancel.value, ast.Call)
    assert isinstance(cancel.value.func, ast.Attribute)
    assert cancel.value.func.attr == "cancel"
    assert isinstance(disarm, ast.Expr)
    assert isinstance(disarm.value, ast.Call)
    assert isinstance(disarm.value.func, ast.Name)
    assert disarm.value.func.id == "disarm"
    assert isinstance(await_cancel, ast.Try)
    assert any(isinstance(node, ast.Await) for node in ast.walk(await_cancel))
    assert not any(
        (isinstance(node, ast.Import) and any(alias.name == "run" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "run")
        for node in module.body
    )
