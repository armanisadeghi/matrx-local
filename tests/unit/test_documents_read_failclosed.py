"""Cloud note reads distinguish a successful miss from a failed request.

The transport is an ``httpx.MockTransport``.  It never contacts Supabase and
the conditional-push case asserts that no POST fallback is attempted.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.services.documents import supabase_client as documents_client
from app.services.documents.supabase_client import SupabaseDocClient
from app.services.documents.sync_engine import SyncEngine


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(documents_client.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        documents_client, "_REST_BASE", "https://postgrest.test/rest/v1"
    )


def _client() -> SupabaseDocClient:
    client = SupabaseDocClient()
    client.set_jwt("isolated-test-token")
    return client


def test_get_note_returns_none_only_for_a_successful_empty_query(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=[], request=request)

    _install_transport(monkeypatch, handler)

    assert asyncio.run(_client().get_note("note-1")) is None


def test_get_note_propagates_postgrest_failure(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"code": "PGRST205", "message": "missing relation"},
            request=request,
        )

    _install_transport(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        asyncio.run(_client().get_note("note-1"))

    assert caught.value.response.status_code == 404


class _FileManager:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"note_hashes": {"General/note.md": "old-hash"}}

    def write_note(
        self, _folder: str, _label: str, _content: str, file_path: str | None
    ) -> str:
        assert file_path == "General/note.md"
        return file_path

    def load_sync_state(self) -> dict[str, Any]:
        return self.state

    def save_sync_state(self, state: dict[str, Any]) -> None:
        self.state = state


class _NotesRepo:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []

    async def set_sync_status(self, note_id: str, status: str, **_kwargs: Any) -> None:
        self.statuses.append((note_id, status))


def test_conditional_push_does_not_upsert_when_the_followup_read_fails(monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.url.path.endswith("/note_folders"):
            return httpx.Response(200, json=[], request=request)
        if request.method == "PATCH":
            # Conditional update missed; the engine must inspect the remote row.
            return httpx.Response(200, json=[], request=request)
        if request.method == "GET":
            return httpx.Response(503, json={"message": "unavailable"}, request=request)
        raise AssertionError("unconditional POST must not follow a failed cloud read")

    _install_transport(monkeypatch, handler)
    repo = _NotesRepo()
    engine = SyncEngine(fm=_FileManager(), sb=_client())
    engine._user_id = "user-1"
    engine._device_id = "device-1"
    engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]

    result = asyncio.run(
        engine._push_note(
            note_id="note-1",
            label="note",
            content="local edit",
            file_path="General/note.md",
        )
    )

    assert result["_synced_to_cloud"] is False
    assert calls == ["GET", "PATCH", "GET"]
    assert repo.statuses == [("note-1", "failed")]
