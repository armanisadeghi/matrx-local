"""The pin/title reconciler must run on a timer, not only when a human clicks.

Until 2026-09-12 ``ClaudeSessionMetadataReconciler`` ran ONLY from
``POST /coding-session/claude/labels/sync``. Arman's pins sat four days stale
and nothing looked broken, because nothing was scheduled to notice. These
tests pin the loop that closes that hole and the two knobs that govern it.
"""

from __future__ import annotations

import asyncio
import logging
from types import MethodType
from typing import Any

import pytest

from app.services.coding_sessions import title_sync as title_sync_module
from app.services.coding_sessions.title_sync import (
    AUTO_LABEL_SYNC_DEFAULT_INTERVAL_MINUTES,
    AUTO_LABEL_SYNC_ENABLED_SETTING,
    AUTO_LABEL_SYNC_INTERVAL_SETTING,
    AUTO_LABEL_SYNC_MAX_INTERVAL_MINUTES,
    AUTO_LABEL_SYNC_MIN_INTERVAL_MINUTES,
    ClaudeSessionMetadataReconciler,
    ClaudeTitleSyncBlocked,
    auto_label_sync_interval_seconds,
    is_auto_label_sync_enabled,
)


class _LogSink(logging.Handler):
    """The engine logger does not propagate to root, so caplog sees nothing.

    Attaching to `system_logger` itself is the only honest way to assert what
    the loop actually said — and it bypasses the console handler's dedup
    filter, so a repeated line is still visible here.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)

    def at_least(self, level: int) -> list[logging.LogRecord]:
        return [record for record in self.records if record.levelno >= level]


@pytest.fixture
def logs() -> Any:
    logger = logging.getLogger("system_logger")
    sink = _LogSink()
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(sink)
    try:
        yield sink
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous_level)


class _FakeSettings:
    """Stands in for the real ~/.matrx/settings.json store, same contract."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


def _use_settings(monkeypatch: pytest.MonkeyPatch, values: dict[str, Any]) -> None:
    settings = _FakeSettings(values)
    monkeypatch.setattr(title_sync_module, "get_settings_sync", lambda: settings)


def _loop_reconciler() -> ClaudeSessionMetadataReconciler:
    """A reconciler with only the loop's own state — no DB, no network."""
    reconciler = object.__new__(ClaudeSessionMetadataReconciler)
    reconciler._sync_lock = asyncio.Lock()
    reconciler._task = None
    reconciler._stopping = False
    return reconciler


async def _settle(iterations: int = 40) -> None:
    """Yield to the loop task enough times for several ticks to complete."""
    for _ in range(iterations):
        await asyncio.sleep(0)


# --------------------------------------------------------------------- knobs


def test_interval_knob_defaults_to_fifteen_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_settings(monkeypatch, {})
    assert is_auto_label_sync_enabled() is True
    assert auto_label_sync_interval_seconds() == (
        AUTO_LABEL_SYNC_DEFAULT_INTERVAL_MINUTES * 60.0
    )


def test_interval_knob_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_settings(monkeypatch, {AUTO_LABEL_SYNC_INTERVAL_SETTING: 3})
    assert auto_label_sync_interval_seconds() == 180.0


def test_out_of_band_interval_is_clamped_loudly(
    monkeypatch: pytest.MonkeyPatch, logs: Any
) -> None:
    _use_settings(monkeypatch, {AUTO_LABEL_SYNC_INTERVAL_SETTING: 0})
    assert auto_label_sync_interval_seconds() == (
        AUTO_LABEL_SYNC_MIN_INTERVAL_MINUTES * 60.0
    )
    _use_settings(monkeypatch, {AUTO_LABEL_SYNC_INTERVAL_SETTING: 99_999})
    assert auto_label_sync_interval_seconds() == (
        AUTO_LABEL_SYNC_MAX_INTERVAL_MINUTES * 60.0
    )
    warnings = logs.at_least(logging.WARNING)
    assert len(warnings) == 2, "a clamped interval must announce itself"
    assert all("Settings" in record.getMessage() for record in warnings)


def test_garbage_interval_falls_back_and_says_so(
    monkeypatch: pytest.MonkeyPatch, logs: Any
) -> None:
    _use_settings(monkeypatch, {AUTO_LABEL_SYNC_INTERVAL_SETTING: "soon"})
    assert auto_label_sync_interval_seconds() == (
        AUTO_LABEL_SYNC_DEFAULT_INTERVAL_MINUTES * 60.0
    )
    assert "not a number" in logs.text
    assert logs.at_least(logging.WARNING)


# ---------------------------------------------------------------------- loop


def test_loop_reconciles_on_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: passes happen with nobody pressing anything."""

    async def exercise() -> None:
        _use_settings(monkeypatch, {})
        # A real 15-minute wall clock would make this test useless; the loop's
        # own interval function is the single place the delay comes from.
        monkeypatch.setattr(
            title_sync_module, "auto_label_sync_interval_seconds", lambda: 0.0
        )
        reconciler = _loop_reconciler()
        calls = 0

        async def counting_sync(self: Any, *, dry_run: bool = False) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            async with self._sync_lock:
                await asyncio.sleep(0)
            return {"dry_run": dry_run}

        reconciler.sync = MethodType(counting_sync, reconciler)

        await reconciler.start_background()
        assert reconciler.active is True
        await _settle()
        await reconciler.stop()

        assert calls >= 2, f"loop only reconciled {calls} time(s)"
        assert reconciler.active is False

    asyncio.run(exercise())


def test_disabled_knob_means_the_loop_never_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        _use_settings(monkeypatch, {AUTO_LABEL_SYNC_ENABLED_SETTING: False})
        monkeypatch.setattr(
            title_sync_module, "auto_label_sync_interval_seconds", lambda: 0.0
        )
        reconciler = _loop_reconciler()
        calls = 0

        async def counting_sync(self: Any, *, dry_run: bool = False) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"dry_run": dry_run}

        reconciler.sync = MethodType(counting_sync, reconciler)

        await reconciler.start_background()
        assert reconciler.active is False
        await _settle()
        await reconciler.stop()

        assert calls == 0

    asyncio.run(exercise())


def test_loop_stands_down_while_a_ui_sync_holds_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page click and a tick must never overlap one reconciliation."""

    async def exercise() -> None:
        _use_settings(monkeypatch, {})
        monkeypatch.setattr(
            title_sync_module, "auto_label_sync_interval_seconds", lambda: 0.0
        )
        reconciler = _loop_reconciler()
        calls = 0

        async def counting_sync(self: Any, *, dry_run: bool = False) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"dry_run": dry_run}

        reconciler.sync = MethodType(counting_sync, reconciler)

        await reconciler._sync_lock.acquire()
        try:
            await reconciler.start_background()
            await _settle()
            assert calls == 0, "a tick ran while a UI sync held the lock"
        finally:
            reconciler._sync_lock.release()
        await _settle()
        await reconciler.stop()
        assert calls >= 1, "the loop never resumed after the UI sync finished"

    asyncio.run(exercise())


def test_blocked_pass_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, logs: Any
) -> None:
    """Signed out is an ordinary desktop state, and the loop keeps going."""

    async def exercise() -> None:
        _use_settings(monkeypatch, {})
        monkeypatch.setattr(
            title_sync_module, "auto_label_sync_interval_seconds", lambda: 0.0
        )
        reconciler = _loop_reconciler()
        calls = 0

        async def blocked_sync(self: Any, *, dry_run: bool = False) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            raise ClaudeTitleSyncBlocked("no_active_user_jwt")

        reconciler.sync = MethodType(blocked_sync, reconciler)

        await reconciler.start_background()
        await _settle()
        await reconciler.stop()

        assert calls >= 2, "a blocked pass stopped the loop"
        assert not logs.at_least(logging.WARNING), "signed out is not a defect"
        assert "no valid signed-in session" in logs.text

    asyncio.run(exercise())


def test_real_failure_is_loud_and_the_loop_survives(
    monkeypatch: pytest.MonkeyPatch, logs: Any
) -> None:
    async def exercise() -> None:
        _use_settings(monkeypatch, {})
        monkeypatch.setattr(
            title_sync_module, "auto_label_sync_interval_seconds", lambda: 0.0
        )
        reconciler = _loop_reconciler()
        calls = 0

        async def failing_sync(self: Any, *, dry_run: bool = False) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            raise RuntimeError("aidream returned 500")

        reconciler.sync = MethodType(failing_sync, reconciler)

        await reconciler.start_background()
        await _settle()
        await reconciler.stop()

        assert calls >= 2
        assert logs.at_least(logging.ERROR), "a real failure was swallowed"
        assert "press Sync" in logs.text
