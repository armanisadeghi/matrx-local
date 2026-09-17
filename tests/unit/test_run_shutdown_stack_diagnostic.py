"""Regression coverage for the shutdown-timeout server-thread diagnostic."""

from __future__ import annotations

import ast
import copy
import os
import sys
import threading
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _shutdown_stack_capture() -> object:
    """Extract the capture seam without importing run.py or starting an engine."""
    module = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"), filename="run.py")
    names = {"_format_server_thread_stack", "_capture_server_thread_shutdown_timeout_stack"}
    functions = [
        copy.deepcopy(node)
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(functions) == len(names)
    extracted = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(extracted)
    namespace = {"os": os, "sys": sys, "threading": threading}
    exec(compile(extracted, "run.py:shutdown-stack", "exec"), namespace)
    return namespace["_capture_server_thread_shutdown_timeout_stack"]


def _wait_forever_without_importing_run(namespace: dict[str, object]) -> object:
    """Extract the shutdown loop so this test never imports application code."""
    module = ast.parse((REPO_ROOT / "run.py").read_text(encoding="utf-8"), filename="run.py")
    wait_forever = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_wait_forever"
    )
    extracted = ast.Module(body=[copy.deepcopy(wait_forever)], type_ignores=[])
    ast.fix_missing_locations(extracted)
    exec(compile(extracted, "run.py:wait-forever", "exec"), namespace)
    return namespace["_wait_forever"]


def test_shutdown_stack_snapshot_identifies_a_real_blocked_server_thread() -> None:
    """The timeout evidence names the blocked worker without leaking its path."""
    release = threading.Event()
    entered = threading.Event()

    def blocked_server_worker() -> None:
        entered.set()
        release.wait(timeout=2)

    worker = threading.Thread(target=blocked_server_worker, daemon=True)
    worker.start()
    try:
        assert entered.wait(timeout=1), "the worker must be blocked before capture"
        capture = _shutdown_stack_capture()
        capture.__globals__["_server_thread"] = worker
        stack = capture()
    finally:
        release.set()
        worker.join(timeout=1)

    assert stack is not None
    assert "blocked_server_worker" in stack
    assert Path(__file__).name in stack
    assert str(Path(__file__).parent) not in stack
    assert len(stack) <= 1_024


def test_clean_shutdown_does_not_capture_a_server_stack() -> None:
    """Only a failed completion barrier may emit the thread diagnostic."""
    captured: list[bool] = []
    errors: list[str] = []

    class CleanExit(Exception):
        pass

    class CleanServer:
        should_exit = False

    class Logger:
        def error(self, message: str, *args: object) -> None:
            errors.append(message % args if args else message)

    def exit_now(code: int) -> None:
        assert code == 0
        raise CleanExit

    shutdown_event = threading.Event()
    stopped_event = threading.Event()
    shutdown_event.set()
    stopped_event.set()
    wait_forever = _wait_forever_without_importing_run(
        {
            "_shutdown_event": shutdown_event,
            "_teardown_join_seconds": lambda: 1,
            "_uvicorn_server": CleanServer(),
            "_server_thread": object(),
            "_server_stopped_event": stopped_event,
            "_log_server_thread_shutdown_timeout": lambda: captured.append(True),
            "_kill_child_subprocesses": lambda: (_ for _ in ()).throw(AssertionError()),
            "remove_discovery_file": lambda: (_ for _ in ()).throw(AssertionError()),
            "logger": Logger(),
            "os": types.SimpleNamespace(_exit=exit_now),
        }
    )

    try:
        wait_forever()
    except CleanExit:
        pass
    else:
        raise AssertionError("the clean shutdown must exit")

    assert captured == []
    assert errors == []


def test_failed_shutdown_barrier_captures_then_cleans_up_before_logging() -> None:
    """An expired real Event wait performs all cleanup before timeout logging."""
    events: list[str] = []

    class ForcedExit(Exception):
        pass

    class Server:
        should_exit = False

    class NoDirectTimeoutLog:
        def error(self, message: str, *args: object) -> None:
            raise AssertionError("timeout logging must run through the post-cleanup helper")

    def exit_now(code: int) -> None:
        assert code == 0
        events.append("exit")
        raise ForcedExit

    shutdown_event = threading.Event()
    shutdown_event.set()
    server_stopped_event = threading.Event()
    wait_forever = _wait_forever_without_importing_run(
        {
            "_shutdown_event": shutdown_event,
            "_teardown_join_seconds": lambda: 0,
            "_uvicorn_server": Server(),
            "_server_thread": object(),
            "_server_stopped_event": server_stopped_event,
            "_capture_server_thread_shutdown_timeout_stack": lambda: events.append("capture"),
            "remove_discovery_file": lambda: events.append("remove-discovery"),
            "_kill_child_subprocesses": lambda: events.append("kill-children"),
            "_log_shutdown_timeout": lambda stack, join_s: events.append("timeout-log"),
            "logger": NoDirectTimeoutLog(),
            "os": types.SimpleNamespace(_exit=exit_now),
        }
    )

    try:
        wait_forever()
    except ForcedExit:
        pass
    else:
        raise AssertionError("the forced shutdown must exit")

    assert events == ["capture", "remove-discovery", "kill-children", "timeout-log", "exit"]
