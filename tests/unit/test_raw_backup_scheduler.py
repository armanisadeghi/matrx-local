"""Automatic raw backup: opted in → uploaded after turns, debounced; off → zero bytes leave."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.coding_sessions import raw_backup_scheduler as module
from app.services.coding_sessions.raw_backup_scheduler import BackupPolicy, RawBackupScheduler

SESSION = "0f0e0d0c-aaaa-4bbb-8ccc-123456789abc"


class _World:
    def __init__(self, tmp_path: Path, *, enabled: bool, debounce: int = 180) -> None:
        self.enabled = enabled
        self.debounce = debounce
        self.now = 1000.0
        self.uploads: list[bytes] = []
        self.reads = 0
        self.file = tmp_path / f"{SESSION}.jsonl"
        self.file.write_text('{"type":"user"}\n')
        self.sleeps: list[float] = []

    async def policy(self, provider: str, psid: str, project: str | None) -> BackupPolicy:
        return BackupPolicy(enabled=self.enabled, debounce_seconds=self.debounce)

    async def upload(self, provider: str, psid: str, project: str | None, content: bytes) -> None:
        self.uploads.append(content)

    def locate(self, provider: str, native: str):
        world = self

        class _Main:
            def read_bytes(self) -> bytes:
                world.reads += 1
                return world.file.read_bytes()

        return SimpleNamespace(main=_Main())

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def scheduler(self) -> RawBackupScheduler:
        return RawBackupScheduler(
            policy=self.policy, upload=self.upload, locate=self.locate,
            clock=lambda: self.now, sleep=self.sleep,
        )


@pytest.mark.anyio
async def test_with_the_knob_off_not_one_byte_leaves_the_machine(tmp_path: Path) -> None:
    """Turns red if: any upload — or even a read for upload — happens while off."""
    world = _World(tmp_path, enabled=False)
    scheduler = world.scheduler()
    for _ in range(5):
        await scheduler.notify("claude_code", SESSION, SESSION)
        world.now += 1000
    await scheduler.wait_idle()
    assert world.uploads == []
    assert world.reads == 0
    assert scheduler.skipped_off == 5


@pytest.mark.anyio
async def test_opted_in_uploads_now_then_one_trailing_upload_per_window(tmp_path: Path) -> None:
    world = _World(tmp_path, enabled=True, debounce=180)
    scheduler = world.scheduler()

    await scheduler.notify("claude_code", SESSION, SESSION)  # first turn end: now
    assert len(world.uploads) == 1
    world.file.write_text('{"type":"user"}\n{"type":"assistant"}\n')
    world.now += 20
    await scheduler.notify("claude_code", SESSION, SESSION)  # inside window: trailing
    world.now += 5
    await scheduler.notify("claude_code", SESSION, SESSION)  # coalesced into trailing
    await scheduler.wait_idle()

    assert len(world.uploads) == 2
    assert world.uploads[-1] == world.file.read_bytes()
    assert world.sleeps == [160.0]


@pytest.mark.anyio
async def test_an_unchanged_file_is_never_sent_twice(tmp_path: Path) -> None:
    world = _World(tmp_path, enabled=True, debounce=30)
    scheduler = world.scheduler()
    await scheduler.notify("codex", SESSION, SESSION)
    world.now += 100
    await scheduler.notify("codex", SESSION, SESSION)
    assert len(world.uploads) == 1


@pytest.mark.anyio
async def test_turning_the_knob_off_before_the_trailing_upload_cancels_it(tmp_path: Path) -> None:
    world = _World(tmp_path, enabled=True, debounce=60)
    scheduler = world.scheduler()
    await scheduler.notify("claude_code", SESSION, SESSION)
    world.file.write_text("changed\n")
    world.now += 10
    await scheduler.notify("claude_code", SESSION, SESSION)
    world.enabled = False
    await scheduler.wait_idle()
    assert len(world.uploads) == 1


def test_only_turn_ends_and_imports_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(module, "schedule_notify", lambda *a: calls.append(a))

    def request(action: str, **extra):
        return SimpleNamespace(
            action=SimpleNamespace(value=action),
            provider=SimpleNamespace(value="codex"),
            provider_session_id="thread-1",
            **extra,
        )

    module.notify_from_request(request("observe_hook", hook_event=SimpleNamespace(name="PostToolUse")))
    module.notify_from_request(request("observe_hook", hook_event=SimpleNamespace(name="Stop")))
    module.notify_from_request(
        request(
            "append_native",
            source_metadata=SimpleNamespace(provider_native_session_id=SESSION),
            provider_project_key="p",
        )
    )
    assert calls == [("codex", "thread-1", "thread-1"), ("codex", "thread-1", SESSION, "p")]


def test_asyncio_is_used_for_fire_and_forget() -> None:
    assert asyncio.iscoroutinefunction(RawBackupScheduler.notify)
