"""The desktop's local-model mandate door: fresh when online, last answer offline.

Guards the offline promise ("offline is a data LOCATION") without letting a
cached answer override the platform's own refusal.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from matrx_ai.client_host.agent_source import ExecutionAgentDefinition

from app.services.ai import local_mandates
from app.services.ai.local_mandates import LocalMandateResolver
from app.services.aidream.client import AIDreamError, AIDreamOfflineError

KEY = "local.polish_style_formal"
ORG = "org-1"
USER = "user-1"


def _resolution(agent_id: str = "agent-A") -> dict[str, Any]:
    return {"agent_id": agent_id, "is_version": False, "provenance": "system"}


class FakeRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def get(self, key: str, *, user_id: str, organization_id: str):
        return self.rows.get((key, user_id, organization_id))

    async def upsert(self, key: str, *, user_id: str, organization_id: str, resolution):
        self.rows[(key, user_id, organization_id)] = {
            "resolution_json": resolution,
            "fetched_at": "2026-09-25T10:00:00+00:00",
        }
        return "2026-09-25T10:00:00+00:00"


class FakeTokenRepo:
    def __init__(self, signed_in: bool = True) -> None:
        self.signed_in = signed_in

    async def get(self):
        return {"access_token": "jwt", "user_id": USER} if self.signed_in else None

    def is_expired(self, row) -> bool:
        return False

    async def get_owner_user_id(self):
        return USER


class FakeClient:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.paths: list[str] = []
        self.headers: list[dict[str, str] | None] = []

    async def get(self, path: str, jwt: str | None = None, *, headers=None):
        self.paths.append(path)
        self.headers.append(headers)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeAgentSource:
    def __init__(self) -> None:
        self.loaded: list[tuple[str, bool]] = []

    async def load_for_execution(self, agent_id: str, *, is_version: bool = False):
        self.loaded.append((agent_id, is_version))
        return ExecutionAgentDefinition(
            definition_id=agent_id,
            agent_id=agent_id,
            model_id="m",
            messages=[{"role": "system", "content": "Holder text."}],
        ).with_content_hash()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    source = FakeAgentSource()
    monkeypatch.setattr(local_mandates, "get_execution_agent_source", lambda: source)

    async def _org(jwt: str) -> str:
        return ORG

    async def _device_org(user_id: str | None = None) -> str:
        return ORG

    monkeypatch.setattr(local_mandates, "resolve_active_organization_id", _org)
    monkeypatch.setattr(local_mandates, "get_device_organization", _device_org)

    def use_client(client: FakeClient) -> None:
        monkeypatch.setattr(local_mandates, "get_aidream_client", lambda: client)

    return source, use_client


def test_online_resolution_is_fresh_cached_and_loads_the_holder(wired) -> None:
    source, use_client = wired
    client = FakeClient(_resolution("agent-A"))
    use_client(client)
    repo = FakeRepo()

    out = asyncio.run(LocalMandateResolver(repo, FakeTokenRepo()).resolve(KEY))

    assert out["stale"] is False and out["stale_reason"] is None
    assert out["resolution"]["agent_id"] == "agent-A"
    assert out["definition"]["messages"][0]["content"] == "Holder text."
    assert client.paths == ["/mandates/local.polish_style_formal/resolution"]
    assert client.headers == [{"X-Organization-Id": ORG}]
    assert repo.rows[(KEY, USER, ORG)]["resolution_json"]["agent_id"] == "agent-A"
    assert source.loaded == [("agent-A", False)]


def test_offline_serves_the_last_platform_answer_flagged_stale(wired) -> None:
    source, use_client = wired
    repo = FakeRepo()
    use_client(FakeClient(_resolution("agent-A")))
    asyncio.run(LocalMandateResolver(repo, FakeTokenRepo()).resolve(KEY))

    use_client(FakeClient(AIDreamOfflineError("no route to host")))
    out = asyncio.run(LocalMandateResolver(repo, FakeTokenRepo()).resolve(KEY))

    assert out["stale"] is True
    assert "unreachable" in out["stale_reason"]
    assert out["resolution"]["agent_id"] == "agent-A"
    assert source.loaded[-1] == ("agent-A", False)


def test_signed_out_serves_the_cached_answer_stale(wired) -> None:
    _, use_client = wired
    repo = FakeRepo()
    use_client(FakeClient(_resolution("agent-B")))
    asyncio.run(LocalMandateResolver(repo, FakeTokenRepo()).resolve(KEY))

    out = asyncio.run(LocalMandateResolver(repo, FakeTokenRepo(signed_in=False)).resolve(KEY))

    assert out["stale"] is True and "signed out" in out["stale_reason"]
    assert out["resolution"]["agent_id"] == "agent-B"


def test_never_resolved_and_offline_refuses_with_the_mandate_named(wired) -> None:
    _, use_client = wired
    use_client(FakeClient(AIDreamOfflineError("down")))

    with pytest.raises(HTTPException) as info:
        asyncio.run(LocalMandateResolver(FakeRepo(), FakeTokenRepo()).resolve(KEY))

    assert info.value.status_code == 503
    assert KEY in info.value.detail["message"]


def test_platform_refusal_is_never_overridden_by_the_cache(wired) -> None:
    _, use_client = wired
    repo = FakeRepo()
    use_client(FakeClient(_resolution("agent-A")))
    asyncio.run(LocalMandateResolver(repo, FakeTokenRepo()).resolve(KEY))

    use_client(FakeClient(AIDreamError(404, "no such mandate")))
    with pytest.raises(HTTPException) as info:
        asyncio.run(LocalMandateResolver(repo, FakeTokenRepo()).resolve(KEY))

    assert info.value.status_code == 404


def test_a_rebind_reaches_the_next_online_run(wired) -> None:
    _, use_client = wired
    repo = FakeRepo()
    use_client(FakeClient(_resolution("agent-A")))
    asyncio.run(LocalMandateResolver(repo, FakeTokenRepo()).resolve(KEY))

    use_client(FakeClient(_resolution("agent-REBOUND")))
    out = asyncio.run(LocalMandateResolver(repo, FakeTokenRepo()).resolve(KEY))

    assert out["resolution"]["agent_id"] == "agent-REBOUND"
    assert repo.rows[(KEY, USER, ORG)]["resolution_json"]["agent_id"] == "agent-REBOUND"


def test_sqlite_repo_round_trips_per_person_and_organization(tmp_path) -> None:
    """The real V38 table: one answer per (mandate, person, organization)."""
    from app.services.local_db.database import LocalDatabase
    from app.services.local_db.repositories import MandateResolutionsRepo

    async def main() -> None:
        db = LocalDatabase(tmp_path / "matrx.db")
        await db.connect()
        try:
            repo = MandateResolutionsRepo(db)
            await repo.upsert(KEY, user_id=USER, organization_id=ORG, resolution=_resolution("a"))
            await repo.upsert(KEY, user_id=USER, organization_id="org-2", resolution=_resolution("b"))
            await repo.upsert(KEY, user_id=USER, organization_id=ORG, resolution=_resolution("c"))
            got = await repo.get(KEY, user_id=USER, organization_id=ORG)
            other = await repo.get(KEY, user_id=USER, organization_id="org-2")
            none = await repo.get(KEY, user_id="someone-else", organization_id=ORG)
            assert got is not None and got["resolution_json"]["agent_id"] == "c"
            assert other is not None and other["resolution_json"]["agent_id"] == "b"
            assert none is None
        finally:
            await db.close()

    asyncio.run(main())
