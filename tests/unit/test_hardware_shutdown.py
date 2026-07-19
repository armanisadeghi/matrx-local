from __future__ import annotations

import concurrent.futures
import asyncio
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from app.common.process_shutdown import (
    process_shutdown_event,
    request_process_shutdown,
)
from app.services.hardware import detector


@pytest.fixture(autouse=True)
def _reset_process_shutdown_event():
    process_shutdown_event.clear()
    yield
    process_shutdown_event.clear()


def test_probe_command_returns_successful_stdout() -> None:
    assert detector._run([sys.executable, "-c", "print('ready')"]) == "ready"


def test_probe_command_terminates_promptly_for_process_shutdown(tmp_path: Path) -> None:
    pid_path = tmp_path / "probe.pid"
    script = (
        "import os, pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(pid_path)!r}).write_text(f'{{os.getpid()}} {{child.pid}}'); "
        "time.sleep(30)"
    )

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        detector._run,
        [sys.executable, "-c", script],
        30,
    )
    probe_pids: list[int] = []
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                probe_pids = [int(pid) for pid in pid_path.read_text().split()]
            except (FileNotFoundError, ValueError):
                time.sleep(0.01)
                continue
            if len(probe_pids) == 2:
                break
        assert len(probe_pids) == 2, "probe subprocess tree did not start"

        started_shutdown_at = time.monotonic()
        request_process_shutdown()

        assert future.result(timeout=2) == ""
        assert time.monotonic() - started_shutdown_at < 1
    finally:
        request_process_shutdown()
        try:
            future.result(timeout=2)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    deadline = time.monotonic() + 2
    while any(psutil.pid_exists(pid) for pid in probe_pids) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not any(psutil.pid_exists(pid) for pid in probe_pids)


def test_probe_cleanup_retains_direct_fallback_when_tree_discovery_fails(
    monkeypatch,
) -> None:
    process = detector.subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=detector.subprocess.PIPE,
        stderr=detector.subprocess.PIPE,
        text=True,
    )

    def deny_tree_discovery(_process: object) -> None:
        raise psutil.AccessDenied(process.pid)

    monkeypatch.setattr(detector, "_IS_WIN", True)
    monkeypatch.setattr(detector, "_stop_windows_probe_tree", deny_tree_discovery)

    detector._stop_probe_tree(process)

    assert process.poll() is not None


def test_full_detection_stops_between_sections_on_shutdown(monkeypatch) -> None:
    def detect_cpus_and_stop() -> list[dict[str, object]]:
        request_process_shutdown()
        return [{"model": "test"}]

    def unexpected_gpu_probe() -> list[dict[str, object]]:
        raise AssertionError("GPU detection ran after process shutdown")

    monkeypatch.setattr(detector, "_detect_cpus", detect_cpus_and_stop)
    monkeypatch.setattr(detector, "_detect_gpus", unexpected_gpu_probe)

    profile = detector.detect_all_sync()

    assert profile["cpus"] == [{"model": "test"}]
    assert profile["gpus"] == []


def test_async_detection_uses_daemon_worker(monkeypatch) -> None:
    worker_is_daemon: list[bool] = []

    def detect() -> dict[str, object]:
        worker_is_daemon.append(threading.current_thread().daemon)
        return {"detected_at": "now"}

    monkeypatch.setattr(detector, "detect_all_sync", detect)

    assert asyncio.run(detector.detect_all()) == {"detected_at": "now"}
    assert worker_is_daemon == [True]


def test_cancelled_async_detection_does_not_delay_event_loop_shutdown(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def blocked_native_probe() -> dict[str, object]:
        started.set()
        release.wait(timeout=5)
        completed.set()
        return {}

    async def cancel_detection() -> None:
        task = asyncio.create_task(detector.detect_all())
        deadline = asyncio.get_running_loop().time() + 1
        while not started.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(detector, "detect_all_sync", blocked_native_probe)
    started_at = time.monotonic()
    try:
        asyncio.run(cancel_detection())
        assert time.monotonic() - started_at < 1
    finally:
        release.set()
        assert completed.wait(timeout=1)
