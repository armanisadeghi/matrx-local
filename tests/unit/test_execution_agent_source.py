from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from matrx_ai.client_host.agent_source import ExecutionAgentDefinition

from app.services.ai.agent_source import LocalExecutionAgentSource
from app.services.aidream.client import AIDreamError, AIDreamOfflineError


def _definition(agent_id: str) -> dict[str, Any]:
    definition = ExecutionAgentDefinition(
        definition_id=agent_id,
        agent_id=agent_id,
        name="Canonical",
        model_id="model-top-level",
        messages=[{"role": "system", "content": "Keep me."}],
        tools=["tool-1"],
    ).with_content_hash()
    return definition.model_dump(mode="json")


class FakeRepo:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.upserts: list[dict[str, Any]] = []
        self.deletes = 0

    async def get(self, agent_id: str, *, is_version: bool) -> dict[str, Any] | None:
        return self.row

    async def upsert(self, definition: dict[str, Any]) -> None:
        self.upserts.append(definition)
        self.row = {
            "definition_json": definition,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    async def delete(self, agent_id: str, *, is_version: bool) -> None:
        self.deletes += 1
        self.row = None


class FakeTokenRepo:
    async def get(self) -> dict[str, Any]:
        return {"access_token": "jwt"}

    def is_expired(self, row: dict[str, Any]) -> bool:
        return False


class FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    async def fetch_agent_execution_definition(
        self, definition_id: str, *, is_version: bool, jwt: str
    ) -> dict[str, Any]:
        self.calls += 1
        return self.payload


def test_cache_miss_fetches_full_definition_and_cache_hit_is_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        agent_id = "agent-1"
        repo = FakeRepo()
        client = FakeClient(_definition(agent_id))
        monkeypatch.setattr(
            "app.services.ai.agent_source.get_aidream_client", lambda: client
        )
        source = LocalExecutionAgentSource(repo=repo, token_repo=FakeTokenRepo())

        first = await source.load_for_execution(agent_id)
        second = await source.load_for_execution(agent_id)

        assert client.calls == 1
        assert len(repo.upserts) == 1
        assert first == second
        assert first.model_id == "model-top-level"
        assert first.messages[0]["content"] == "Keep me."
        assert first.tools == ["tool-1"]

    asyncio.run(_run())


def test_incomplete_cached_definition_is_invalidated_and_refetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        agent_id = "agent-1"
        repo = FakeRepo(
            {
                "definition_json": {**_definition(agent_id), "messages": []},
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        client = FakeClient(_definition(agent_id))
        monkeypatch.setattr(
            "app.services.ai.agent_source.get_aidream_client", lambda: client
        )
        source = LocalExecutionAgentSource(repo=repo, token_repo=FakeTokenRepo())

        definition = await source.load_for_execution(agent_id)

        assert repo.deletes == 1
        assert client.calls == 1
        assert definition.messages

    asyncio.run(_run())


def test_authoritative_incomplete_definition_never_reaches_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        agent_id = "agent-1"
        client = FakeClient({**_definition(agent_id), "messages": []})
        monkeypatch.setattr(
            "app.services.ai.agent_source.get_aidream_client", lambda: client
        )
        source = LocalExecutionAgentSource(repo=FakeRepo(), token_repo=FakeTokenRepo())

        with pytest.raises(Exception, match="messages"):
            await source.load_for_execution(agent_id)

    asyncio.run(_run())


def test_authoritative_not_found_remains_a_structured_http_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingClient:
        async def fetch_agent_execution_definition(
            self, definition_id: str, *, is_version: bool, jwt: str
        ) -> dict[str, Any]:
            raise AIDreamError(404, "missing")

    async def _run() -> None:
        monkeypatch.setattr(
            "app.services.ai.agent_source.get_aidream_client",
            lambda: MissingClient(),
        )
        source = LocalExecutionAgentSource(
            repo=FakeRepo(),
            token_repo=FakeTokenRepo(),
        )

        with pytest.raises(HTTPException) as caught:
            await source.load_for_execution("agent-404")

        assert caught.value.status_code == 404
        assert caught.value.detail["code"] == "agent_not_found"

    asyncio.run(_run())


def test_valid_stale_definition_remains_offline_capable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OfflineClient:
        async def fetch_agent_execution_definition(
            self, definition_id: str, *, is_version: bool, jwt: str
        ) -> dict[str, Any]:
            raise AIDreamOfflineError("offline")

    async def _run() -> None:
        agent_id = "agent-offline"
        repo = FakeRepo(
            {
                "definition_json": _definition(agent_id),
                "fetched_at": "2000-01-01T00:00:00+00:00",
            }
        )
        monkeypatch.setattr(
            "app.services.ai.agent_source.get_aidream_client",
            lambda: OfflineClient(),
        )
        source = LocalExecutionAgentSource(repo=repo, token_repo=FakeTokenRepo())

        definition = await source.load_for_execution(agent_id)

        assert definition.definition_id == agent_id
        assert repo.deletes == 0

    asyncio.run(_run())
