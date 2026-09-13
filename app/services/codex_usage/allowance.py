"""Read the Codex account allowance without making a model request."""

from __future__ import annotations

import asyncio
import hashlib
import json
import selectors
import shutil
import subprocess
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
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _sanitize_limit(value: object) -> dict[str, float | int | None] | None:
    if not isinstance(value, dict):
        return None
    used = _number(value.get("usedPercent"))
    remaining = _number(value.get("remainingPercent"))
    if remaining is None and used is not None:
        remaining = max(0, min(100, 100 - used))
    if used is None and remaining is not None:
        used = max(0, min(100, 100 - remaining))
    window = _number(value.get("windowDurationMins"))
    reset = _number(value.get("resetsAt"))
    if used is None and remaining is None and window is None and reset is None:
        return None
    return {
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
        candidates = list(raw_limits.values())
    else:
        candidates = [result.get("rateLimits")]
    limits = [limit for item in candidates if (limit := _sanitize_limit(item))]
    if not limits:
        return _unavailable("Codex did not expose a readable account allowance.")
    response: dict[str, object] = {
        "status": "available",
        "observed_at": datetime.now(UTC).isoformat(),
        "limits": limits,
    }
    account_id = result.get("accountId")
    if isinstance(account_id, str) and account_id and account_id.lower() != "unknown":
        response["account_hash"] = hashlib.sha256(account_id.encode()).hexdigest()[:16]
    return response


def _read_allowance_sync() -> dict[str, object]:
    executable = shutil.which("codex")
    if not executable:
        return _unavailable("The Codex CLI is not installed on this device.")
    process: subprocess.Popen[str] | None = None
    selector: selectors.BaseSelector | None = None
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
        deadline = time.monotonic() + _ALLOWANCE_TIMEOUT_SECONDS
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)

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
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            line = process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
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
    except OSError:
        return _unavailable("The Codex CLI could not be started.")
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


class AllowanceService:
    """A short-lived, single-flight cache around the account-only CLI read."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[dict[str, object]] | None = None
        self._cached: dict[str, object] | None = None
        self._expires_at = 0.0

    async def read(self) -> dict[str, object]:
        async with self._lock:
            if self._cached is not None and time.monotonic() < self._expires_at:
                return self._cached
            if self._inflight is None:
                self._inflight = asyncio.create_task(
                    asyncio.to_thread(_read_allowance_sync)
                )
            task = self._inflight
        try:
            response = await task
        finally:
            async with self._lock:
                if self._inflight is task:
                    self._inflight = None
        async with self._lock:
            self._cached = response
            self._expires_at = time.monotonic() + _CACHE_SECONDS
        return response


allowance_service = AllowanceService()
