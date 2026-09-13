"""Read Codex account allowance without a model turn, reset, or purchase."""

from __future__ import annotations

import asyncio
import json
import queue
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime

_ALLOWANCE_TIMEOUT_SECONDS = 12.0
_CACHE_SECONDS = 60.0


def _unavailable(reason: str) -> dict[str, object]:
    return {
        "status": "unavailable",
        "observed_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "limits": [],
    }


def _number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return (
        value if value == value and value not in (float("inf"), float("-inf")) else None
    )


def _sanitize_limit(bucket: str, value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    used = _number(value.get("usedPercent"))
    remaining = _number(value.get("remainingPercent"))
    if remaining is None and used is not None:
        remaining = max(0, min(100, 100 - used))
    if used is None and remaining is not None:
        used = max(0, min(100, 100 - remaining))
    window, reset = (
        _number(value.get("windowDurationMins")),
        _number(value.get("resetsAt")),
    )
    if used is None and remaining is None and window is None and reset is None:
        return None
    return {
        "bucket": bucket[:80] or "Current allowance",
        "used_percent": used,
        "remaining_percent": remaining,
        "window_minutes": window,
        "resets_at": reset,
    }


def _sanitize_result(result: object) -> dict[str, object]:
    if not isinstance(result, dict):
        return _unavailable("Codex returned an invalid allowance response.")
    raw_limits = result.get("rateLimitsByLimitId")
    if isinstance(raw_limits, dict):
        candidates = [(str(key), value) for key, value in raw_limits.items()]
    else:
        candidates = [("Current allowance", result.get("rateLimits"))]
    limits = [
        limit
        for bucket, item in candidates
        if (limit := _sanitize_limit(bucket, item)) is not None
    ]
    if not limits:
        return _unavailable("Codex did not expose a readable account allowance.")
    return {
        "status": "available",
        "observed_at": datetime.now(UTC).isoformat(),
        "limits": limits,
    }


def _reader(source, output: queue.Queue[str]) -> None:
    try:
        for line in iter(source.readline, ""):
            output.put(line)
    finally:
        output.put("")


def _read_allowance_sync() -> dict[str, object]:
    executable = shutil.which("codex")
    if not executable:
        return _unavailable("The Codex CLI is not installed on this device.")
    process: subprocess.Popen[str] | None = None
    reader: threading.Thread | None = None
    output: queue.Queue[str] = queue.Queue()
    try:
        process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            return _unavailable("The Codex CLI did not open an allowance channel.")
        reader = threading.Thread(target=_reader, args=(process.stdout, output))
        reader.start()
        deadline = time.monotonic() + _ALLOWANCE_TIMEOUT_SECONDS

        def send(message: dict[str, object]) -> None:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "matrx-local", "version": "1"}},
            }
        )
        initialized = False
        while time.monotonic() < deadline:
            try:
                line = output.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                break
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                return _unavailable("Codex returned a non-object allowance message.")
            if message.get("id") == 1:
                if "error" in message:
                    return _unavailable("Codex declined allowance initialization.")
                if not initialized:
                    initialized = True
                    send({"method": "initialized", "params": {}})
                    send(
                        {
                            "id": 2,
                            "method": "account/rateLimits/read",
                            "params": {"excludeResetCreditDetails": True},
                        }
                    )
            elif message.get("id") == 2:
                if "error" in message:
                    return _unavailable("Codex could not read the account allowance.")
                return _sanitize_result(message.get("result"))
        return _unavailable("Codex allowance read timed out.")
    except (OSError, ValueError):
        return _unavailable("The Codex CLI could not be started.")
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        if reader is not None:
            reader.join(timeout=2)


class AllowanceService:
    """Short-lived shared account-only read; cancelled callers cannot cancel it."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[dict[str, object]] | None = None
        self._cached: dict[str, object] | None = None
        self._expires_at = 0.0

    async def _finish(self, task: asyncio.Task[dict[str, object]]) -> None:
        try:
            response = task.result()
        except BaseException:
            return
        async with self._lock:
            if self._inflight is task:
                self._inflight = None
                self._cached = response
                self._expires_at = time.monotonic() + _CACHE_SECONDS

    async def read(self, refresh: bool = False) -> dict[str, object]:
        async with self._lock:
            if not refresh and self._cached is not None and time.monotonic() < self._expires_at:
                return self._cached
            if self._inflight is None:
                self._inflight = asyncio.create_task(
                    asyncio.to_thread(_read_allowance_sync)
                )
                self._inflight.add_done_callback(
                    lambda task: asyncio.create_task(self._finish(task))
                )
            task = self._inflight
        return await asyncio.shield(task)


allowance_service = AllowanceService()
