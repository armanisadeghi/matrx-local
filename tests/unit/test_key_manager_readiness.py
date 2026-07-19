"""API-key startup readiness must never publish partial or false-missing state."""

from __future__ import annotations

import asyncio

import pytest

from app.services.ai import key_manager
from app.services.local_db import repositories


def _reset_key_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(key_manager, "_user_keys", {})
    monkeypatch.setattr(key_manager, "_user_keys_loaded", False)
    monkeypatch.setattr(key_manager, "_user_keys_load_lock", asyncio.Lock())
    monkeypatch.setattr(key_manager, "_inject", lambda _provider, _key: None)


def test_need_time_read_waits_for_the_complete_persisted_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_key_manager(monkeypatch)
    reads = 0

    class FakeRepo:
        async def get_all(self) -> dict[str, str]:
            nonlocal reads
            reads += 1
            await asyncio.sleep(0)
            return {
                "civitai": " civ-real ",
                "huggingface": "hf-real",
                "openai": "",
            }

    monkeypatch.setattr(repositories, "ApiKeysRepo", FakeRepo)

    async def read_concurrently() -> list[dict[str, str]]:
        return await asyncio.gather(
            key_manager.get_user_keys_when_ready(),
            key_manager.get_user_keys_when_ready(),
        )

    snapshots = asyncio.run(read_concurrently())
    assert reads == 1
    assert snapshots == [
        {"civitai": "civ-real", "huggingface": "hf-real"},
        {"civitai": "civ-real", "huggingface": "hf-real"},
    ]
    assert key_manager.user_keys_loaded() is True


def test_storage_failure_does_not_become_an_empty_ready_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_key_manager(monkeypatch)

    class FailingRepo:
        async def get_all(self) -> dict[str, str]:
            raise RuntimeError("database is still opening")

    monkeypatch.setattr(repositories, "ApiKeysRepo", FailingRepo)

    with pytest.raises(RuntimeError, match="still opening"):
        asyncio.run(key_manager.get_user_keys_when_ready())
    assert key_manager.user_keys_loaded() is False
    assert key_manager.get_cached_user_keys() == {}
