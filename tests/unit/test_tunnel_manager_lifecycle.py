from __future__ import annotations

import asyncio
import logging
import sys
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


@pytest.mark.anyio
async def test_spontaneous_exit_withdraws_cloud_tunnel_before_next_heartbeat(
    tunnel_fakes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned, _updates, _set_emit_url = tunnel_fakes
    withdrawn: list[tuple[str | None, bool]] = []

    class _InstanceManager:
        async def update_tunnel_url(self, url: str | None, active: bool) -> bool:
            withdrawn.append((url, active))
            return True

    from app.services.cloud_sync import instance_manager

    monkeypatch.setattr(instance_manager, "get_instance_manager", lambda: _InstanceManager())
    manager = tunnel_manager.TunnelManager()

    assert await manager.start(22140) == "https://owned.trycloudflare.com"
    spawned[0].returncode = 1
    spawned[0].stdout.close()
    spawned[0]._exited.set()
    assert manager._reader_task is not None
    await manager._reader_task

    assert withdrawn == [(None, False)]


@pytest.mark.anyio
async def test_normal_stop_leaves_cloud_withdrawal_to_lifespan_teardown(
    tunnel_fakes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    withdrawn: list[tuple[str | None, bool]] = []

    class _InstanceManager:
        async def update_tunnel_url(self, url: str | None, active: bool) -> bool:
            withdrawn.append((url, active))
            return True

    from app.services.cloud_sync import instance_manager

    monkeypatch.setattr(instance_manager, "get_instance_manager", lambda: _InstanceManager())
    manager = tunnel_manager.TunnelManager()

    assert await manager.start(22140) == "https://owned.trycloudflare.com"
    await manager.stop()

    assert withdrawn == []


@pytest.mark.anyio
async def test_reader_waits_for_real_child_exit_after_output_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child can close its combined output before it actually exits."""
    withdrawn: list[tuple[str | None, bool]] = []

    class _InstanceManager:
        async def update_tunnel_url(self, url: str | None, active: bool) -> bool:
            withdrawn.append((url, active))
            return True

    from app.services.cloud_sync import instance_manager

    monkeypatch.setattr(instance_manager, "get_instance_manager", lambda: _InstanceManager())
    monkeypatch.setattr(tunnel_manager, "_ensure_binary", lambda: Path(sys.executable))
    manager = tunnel_manager.TunnelManager()
    script = (
        "import os, sys, time; "
        "print('INF | https://owned.trycloudflare.com |', flush=True); "
        "os.close(sys.stdout.fileno()); os.close(sys.stderr.fileno()); "
        "time.sleep(0.05); os._exit(1)"
    )
    monkeypatch.setattr(manager, "_build_command", lambda _bin, _port: [sys.executable, "-c", script])

    assert await manager.start(22140) == "https://owned.trycloudflare.com"
    assert manager._reader_task is not None
    await manager._reader_task

    assert manager.last_exit_code is None  # no stop() has overwritten the child result
    assert withdrawn == [(None, False)]


@pytest.mark.anyio
async def test_late_active_publication_cannot_restore_exited_child(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[tuple[str | None, bool]] = []

    class _InstanceManager:
        async def update_tunnel_url(self, url: str | None, active: bool) -> bool:
            published.append((url, active))
            return True

    from app.services.cloud_sync import instance_manager

    monkeypatch.setattr(instance_manager, "get_instance_manager", lambda: _InstanceManager())
    monkeypatch.setattr(tunnel_manager, "_ensure_binary", lambda: Path(sys.executable))
    manager = tunnel_manager.TunnelManager()
    script = (
        "import os, sys; "
        "print('INF | https://owned.trycloudflare.com |', flush=True); "
        "os.close(sys.stdout.fileno()); os.close(sys.stderr.fileno()); os._exit(1)"
    )
    monkeypatch.setattr(manager, "_build_command", lambda _bin, _port: [sys.executable, "-c", script])

    url = await manager.start(22140)
    assert url == "https://owned.trycloudflare.com"
    assert manager._reader_task is not None
    await manager._reader_task

    assert await manager.publish_active_registration(url) is False
    assert published == [(None, False)]
