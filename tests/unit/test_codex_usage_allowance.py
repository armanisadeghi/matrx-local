from __future__ import annotations

import asyncio
import json

from app.services.codex_usage import allowance


class _Stream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.writes: list[str] = []

    def readline(self) -> str:
        return self._lines.pop(0) if self._lines else ""

    def write(self, value: str) -> int:
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _Process:
    def __init__(self, lines: list[str]) -> None:
        self.stdin, self.stdout = _Stream([]), _Stream(lines)
        self.returncode: int | None = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated, self.returncode = True, 0

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


class _Selector:
    def __init__(self) -> None:
        self.stream: _Stream | None = None

    def register(self, stream: _Stream, _events: int) -> None:
        self.stream = stream

    def select(self, _timeout: float):
        return [(object(), object())] if self.stream and self.stream._lines else []

    def close(self) -> None:
        return None


def test_allowance_reports_missing_cli_without_starting_process(monkeypatch) -> None:
    monkeypatch.setattr(allowance.shutil, "which", lambda _name: None)
    assert allowance._read_allowance_sync()["status"] == "unavailable"


def test_allowance_sanitizes_response_and_cleans_owned_process(monkeypatch) -> None:
    process = _Process(
        [
            json.dumps({"id": 1, "result": {}}) + "\n",
            json.dumps(
                {
                    "id": 2,
                    "result": {
                        "accountId": "private-account",
                        "rateLimitsByLimitId": {
                            "five-hour": {
                                "usedPercent": 25,
                                "windowDurationMins": 300,
                                "resetsAt": 2_000_000_000,
                            }
                        },
                    },
                }
            )
            + "\n",
        ]
    )
    monkeypatch.setattr(allowance.shutil, "which", lambda _name: "/safe/codex")
    monkeypatch.setattr(
        allowance.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(allowance.selectors, "DefaultSelector", _Selector)

    result = allowance._read_allowance_sync()

    assert result["status"] == "available"
    assert result["account_hash"] != "private-account"
    assert result["limits"] == [
        {
            "used_percent": 25,
            "remaining_percent": 75,
            "window_minutes": 300,
            "resets_at": 2_000_000_000,
        }
    ]
    assert process.terminated
    sent = "".join(process.stdin.writes)
    assert "account/rateLimits/read" in sent
    assert "excludeResetCreditDetails" in sent


def test_allowance_timeout_cleans_owned_process(monkeypatch) -> None:
    process = _Process([])
    monkeypatch.setattr(allowance.shutil, "which", lambda _name: "/safe/codex")
    monkeypatch.setattr(
        allowance.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(allowance.selectors, "DefaultSelector", _Selector)

    result = allowance._read_allowance_sync()

    assert result["status"] == "unavailable"
    assert "timed out" in result["reason"]
    assert process.terminated


def test_allowance_never_hashes_an_unknown_account_marker() -> None:
    result = allowance._sanitize_result(
        {
            "accountId": "unknown",
            "rateLimits": {"usedPercent": 10},
        }
    )

    assert result["status"] == "available"
    assert "account_hash" not in result


def test_allowance_service_single_flight_and_cache(monkeypatch) -> None:
    service = allowance.AllowanceService()
    calls = 0

    def read() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"status": "available", "observed_at": "now", "limits": []}

    monkeypatch.setattr(allowance, "_read_allowance_sync", read)

    async def exercise() -> None:
        first, second = await asyncio.gather(service.read(), service.read())
        third = await service.read()
        assert first == second == third

    asyncio.run(exercise())
    assert calls == 1
