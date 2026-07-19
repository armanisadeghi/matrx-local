from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from app import preflight
from app.services.tunnel import manager as tunnel_manager


class _UrlOutput:
    def __init__(self, *, emit_url: bool) -> None:
        self._emit_url = emit_url
        self._sent = False
        self._closed = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._emit_url and not self._sent:
            self._sent = True
            return b"INF | https://owned.trycloudflare.com |\n"
        await self._closed.wait()
        raise StopAsyncIteration

    def close(self) -> None:
        self._closed.set()


class _AsyncProcess:
    def __init__(self, pid: int, *, emit_url: bool) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdout = _UrlOutput(emit_url=emit_url)
        self._exited = asyncio.Event()
        self.terminate_calls = 0

    async def wait(self) -> int:
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0
        self.stdout.close()
        self._exited.set()

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.close()
        self._exited.set()


class _PsutilIdentity:
    def __init__(self, _pid: int) -> None:
        pass

    def create_time(self) -> float:
        return 1_700_000_000.25

    def exe(self) -> str:
        return "/Users/test/.matrx/bin/cloudflared"


@pytest.fixture()
def tunnel_fakes(monkeypatch: pytest.MonkeyPatch):
    spawned: list[_AsyncProcess] = []
    updates: list[tuple[str, dict | None]] = []
    emit_url = True

    async def create_subprocess(*_args, **_kwargs) -> _AsyncProcess:
        process = _AsyncProcess(500 + len(spawned), emit_url=emit_url)
        spawned.append(process)
        return process

    monkeypatch.setattr(tunnel_manager, "_ensure_binary", lambda: Path("/bin/cloudflared"))
    monkeypatch.setattr(
        tunnel_manager.asyncio,
        "create_subprocess_exec",
        create_subprocess,
    )
    monkeypatch.setattr(tunnel_manager.psutil, "Process", _PsutilIdentity)
    monkeypatch.setattr(
        preflight,
        "update_discovery_service",
        lambda key, info: updates.append((key, info)),
    )

    def set_emit_url(value: bool) -> None:
        nonlocal emit_url
        emit_url = value

    return spawned, updates, set_emit_url


@pytest.mark.anyio
async def test_concurrent_named_starts_spawn_once_and_never_log_token(
    tunnel_fakes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spawned, updates, _set_emit_url = tunnel_fakes
    manager = tunnel_manager.TunnelManager()
    manager._token = "top-secret-tunnel-token"
    caplog.set_level(logging.INFO)

    first, second = await asyncio.gather(manager.start(22140), manager.start(22140))

    assert first == second == "https://owned.trycloudflare.com"
    assert len(spawned) == 1
    assert "top-secret-tunnel-token" not in caplog.text
    assert updates == [
        (
            "tunnel",
            {
                "pid": 500,
                "process_started_at": 1_700_000_000.25,
                "executable": "/Users/test/.matrx/bin/cloudflared",
            },
        )
    ]

    await manager.stop()


@pytest.mark.anyio
async def test_cancelled_start_terminates_and_reaps_spawned_child(
    tunnel_fakes,
) -> None:
    spawned, updates, set_emit_url = tunnel_fakes
    set_emit_url(False)
    manager = tunnel_manager.TunnelManager()

    start_task = asyncio.create_task(manager.start(22140))
    for _ in range(20):
        if spawned:
            break
        await asyncio.sleep(0)
    assert len(spawned) == 1

    start_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert spawned[0].terminate_calls == 1
    assert spawned[0].returncode == 0
    assert manager.process_identity is None
    assert manager.running is False
    assert updates[-1] == ("tunnel", None)
