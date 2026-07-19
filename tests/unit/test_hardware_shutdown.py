from __future__ import annotations

import concurrent.futures
import os
import sys
import time
from pathlib import Path

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
        "import os, pathlib, time; "
        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            detector._run,
            [sys.executable, "-c", script],
            30,
        )
        deadline = time.monotonic() + 2
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_path.exists(), "probe subprocess did not start"
        child_pid = int(pid_path.read_text())

        started_shutdown_at = time.monotonic()
        request_process_shutdown()

        assert future.result(timeout=2) == ""
        assert time.monotonic() - started_shutdown_at < 1

    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


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
