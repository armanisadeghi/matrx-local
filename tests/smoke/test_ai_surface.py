"""Host-owned /ai surface smoke tests (Phase 5 — the aidream-compatible layer).

Two layers of coverage:

1. IN-PROCESS (no engine fixture): the real ``build_ai_app()`` ASGI app is
   driven through httpx's ASGITransport against the REAL seams — the
   SQLiteConversationStore + SqliteModelCatalog on a tmp SQLite DB, the real
   ToolRegistry with the real LocalToolBridge (108 OS tools) and the mock
   provider. Asserts the full wire contract matrx-frontend consumes:
     * NDJSON stream: phase → data(conversation_id) → init → chunk →
       completion → end, plus heartbeats at the configured cadence.
     * X-Conversation-ID / X-Request-ID response headers.
     * Conversation + messages persisted through the SQLite store.
     * A LOCAL TOOL (local_system_info) round-trips through the mock
       provider's tool_calls path (tool_event on the stream + a
       tool_call_logs row on disk).
     * aidream error semantics: 404 conversation_not_found,
       409 conversation_already_exists, resume 404/409, envelope shape.

2. ENGINE FIXTURE (port 22199): a mounted-path probe proving the surface is
   live at BOTH /ai and /chat/ai on the real engine (the old 503 stub is
   gone) and the outer AuthMiddleware still gates it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

# Break the matrx-ai 0.3.0 providers ↔ orchestrator import cycle before any
# provider import (same import-order fix the engine applies at startup).
import matrx_ai.orchestrator  # noqa: F401

# app.config ↔ app.common are mutually importing; app.common must win.
import app.common  # noqa: F401


_MOCK_MODEL_ID = str(uuid.uuid4())
_MOCK_MODEL = {
    "id": _MOCK_MODEL_ID,
    "name": "mock-model",
    "common_name": "Mock Model",
    "provider": "mock",
    "endpoints": ["mock_chat"],
    "api_class": "mock_standard",
    "capabilities": {
        "input": ["text"],
        "output": ["text"],
        "features": ["function_calling"],
    },
    "is_primary": False,
    "is_premium": False,
    "is_deprecated": False,
}

_AGENT_ID = str(uuid.uuid4())
_MOCK_AGENT = {
    "id": _AGENT_ID,
    "name": "Smoke Agent",
    "description": "In-process smoke agent",
    "source": "builtin",
    "user_id": "",
    "category": "test",
    "tags": [],
    "is_favorite": False,
    "variable_defaults": [],
    "settings": {
        "model_id": "mock-model",
        "temperature": 0.2,
        "max_tokens": 512,
        "stream": True,
        "tools": [],
    },
    "is_active": True,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def seam_sandbox():
    """Snapshot + restore matrx-ai's global seam/runtime registries."""
    from matrx_ai import _ext
    from matrx_ai.catalog import host_catalog

    saved_registry = dict(_ext._registry)
    saved_configured = _ext._configured
    saved_runtime = dict(host_catalog._runtime_models)
    try:
        yield
    finally:
        _ext._registry.clear()
        _ext._registry.update(saved_registry)
        _ext._configured = saved_configured
        host_catalog._runtime_models.clear()
        host_catalog._runtime_models.update(saved_runtime)


@pytest.fixture()
def local_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real LocalDatabase on a tmp file, installed as the get_db() singleton."""
    from app.services.ai import conversation_handler as ch_module
    from app.services.local_db import database as db_module

    db = db_module.LocalDatabase(tmp_path / "matrx.db")
    asyncio.run(db.connect())
    monkeypatch.setattr(db_module, "_instance", db)
    monkeypatch.setattr(ch_module, "_STORE_INSTANCE", None)
    try:
        yield db
    finally:
        asyncio.run(db.close())


@pytest.fixture()
def ai_app(seam_sandbox, local_db, monkeypatch: pytest.MonkeyPatch):
    """The real /ai ASGI app wired to the real seams + local tool registry."""
    from matrx_ai._ext import configure_ext

    from app.services.ai.conversation_handler import SQLiteConversationStore
    from app.services.ai.key_manager import get_key_resolver
    from app.services.ai.model_catalog import SqliteModelCatalog

    configure_ext(
        conversation_store=SQLiteConversationStore(),
        model_catalog=SqliteModelCatalog(),
        api_key_resolver=get_key_resolver(),
        source_app="matrx_local",
    )

    # Same guard the engine installs at startup (WriteCoordinator forced off
    # in a client host — see engine.install_client_host_coordinator_guard).
    from app.services.ai.engine import install_client_host_coordinator_guard

    install_client_host_coordinator_guard()

    # Local tool registry: definitions + executors (the engine's Phase A½+B).
    from matrx_ai.tools.registry import ToolRegistry

    from app.services.ai.local_tool_bridge import (
        build_local_tool_definitions,
        register_local_tools,
    )

    registry = ToolRegistry.get_instance()
    missing = [d for d in build_local_tool_definitions() if registry.get(d.name) is None]
    if missing:
        registry.load_from_definitions(missing)
    register_local_tools()

    # Seed the mock model + agent into the tmp SQLite cache.
    async def _seed() -> None:
        from app.services.local_db.repositories import AgentsRepo, ModelsRepo

        await ModelsRepo().upsert(dict(_MOCK_MODEL))
        await AgentsRepo().upsert(dict(_MOCK_AGENT))

    asyncio.run(_seed())

    import app.api.ai_routes as ai_routes

    # Fast heartbeat so a sub-second mock stream still carries beats.
    monkeypatch.setattr(ai_routes, "_HEARTBEAT_INTERVAL", 0.02)
    return ai_routes.build_ai_app()


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://ai.test",
        headers={"Authorization": "Bearer test-token"},
        timeout=30.0,
    )


_COMPACT_EVENTS = {"c": "chunk", "rc": "reasoning_chunk"}


async def _read_stream(resp: httpx.Response) -> list[dict[str, Any]]:
    """Parse NDJSON lines. Chunks ride the compact aidream wire form
    ``{"e": "c", "t": "..."}`` — normalize to {"event", "data"} shape."""
    events: list[dict[str, Any]] = []
    async for line in resp.aiter_lines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if "event" not in d and d.get("e") in _COMPACT_EVENTS:
            d = {"event": _COMPACT_EVENTS[d["e"]], "data": {"text": d.get("t", "")}}
        events.append(d)
    return events


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [e.get("event", "") for e in events]


# ---------------------------------------------------------------------------
# In-process wire-contract tests
# ---------------------------------------------------------------------------


def test_chat_stream_with_local_tool_round_trip(ai_app, local_db):
    """POST /chat: mock provider emits a REAL local tool call; the stream
    carries the full aidream vocabulary; SQLite holds the turn + tool log."""

    conversation_id = str(uuid.uuid4())

    async def _run() -> None:
        async with _client(ai_app) as client:
            async with client.stream(
                "POST",
                "/chat",
                json={
                    "ai_model_id": "mock-model",
                    "messages": [{"role": "user", "content": "use the tool please"}],
                    "conversation_id": conversation_id,
                    "is_new": True,
                    "stream": True,
                    "metadata": {
                        "mock": {
                            "latency_ms": 60,
                            "ttft_ms": 0,
                            "chunks": 2,
                            "mode": "text",
                            "text": "tool round trip complete",
                            "tool_calls": [
                                {"name": "local_system_info", "arguments": {}}
                            ],
                        }
                    },
                },
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers.get("x-conversation-id") == conversation_id
                assert resp.headers.get("x-request-id")
                events = await _read_stream(resp)

        types = _event_types(events)
        # Opening envelope
        assert types[0] == "phase", f"first event must be phase, got {types[:3]}"
        assert "data" in types, "conversation_id data event missing"
        conv_events = [
            e
            for e in events
            if e["event"] == "data" and e["data"].get("type") == "conversation_id"
        ]
        assert conv_events and conv_events[0]["data"]["conversation_id"] == conversation_id
        assert "init" in types
        # The local tool actually ran (tool_event lifecycle on the stream)
        assert "tool_event" in types, f"no tool_event in stream: {sorted(set(types))}"
        tool_events = [e["data"] for e in events if e["event"] == "tool_event"]
        assert any(
            td.get("tool_name") == "local_system_info" for td in tool_events
        ), f"local_system_info missing from tool events: {tool_events}"
        # Model answer + terminal envelope
        assert "chunk" in types
        assert "completion" in types
        completion = next(e["data"] for e in events if e["event"] == "completion")
        assert completion["status"] == "success"
        assert completion["operation"] == "user_request"
        assert types[-1] == "end", f"stream must terminate with end, got {types[-5:]}"
        # Heartbeats at the configured (fast) cadence
        assert "heartbeat" in types, "no heartbeat event on the stream"

        # Persistence through the SQLite store
        conv_row = await local_db.fetchone(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        )
        assert conv_row is not None, "conversation row missing"
        msg_rows = await local_db.fetchall(
            "SELECT * FROM messages WHERE conversation_id = ?", (conversation_id,)
        )
        contents = [dict(r).get("content", "") for r in msg_rows]
        assert any("use the tool please" in c for c in contents)
        assert any("tool round trip complete" in c for c in contents)
        tool_rows = await local_db.fetchall(
            "SELECT * FROM tool_call_logs WHERE conversation_id = ?",
            (conversation_id,),
        )
        assert tool_rows, "tool_call_logs row missing for the local tool call"

    asyncio.run(_run())


def test_conversation_continue_and_agent_start(ai_app, local_db):
    """Turn 1 via /agents/{id}, turn 2 via /conversations/{id} — the
    persisted local state is the source of truth on the continue turn."""

    conversation_id = str(uuid.uuid4())

    async def _run() -> None:
        async with _client(ai_app) as client:
            # Turn 1 — agent start (echo mode: no mock spec on the config).
            async with client.stream(
                "POST",
                f"/agents/{_AGENT_ID}",
                json={
                    "user_input": "hello from turn one",
                    "conversation_id": conversation_id,
                    "is_new": True,
                },
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers.get("x-conversation-id") == conversation_id
                events = await _read_stream(resp)
            types = _event_types(events)
            assert "completion" in types and types[-1] == "end"
            # The authored-prompt contract gap surfaces LOUDLY as a warning.
            warn_codes = [
                e["data"].get("code") for e in events if e["event"] == "warning"
            ]
            assert "agent_prompt_unavailable_locally" in warn_codes

            # Turn 2 — continue.
            async with client.stream(
                "POST",
                f"/conversations/{conversation_id}",
                json={"user_input": "and turn two"},
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers.get("x-conversation-id") == conversation_id
                events2 = await _read_stream(resp)
            types2 = _event_types(events2)
            assert "completion" in types2 and types2[-1] == "end"

        msg_rows = await local_db.fetchall(
            "SELECT * FROM messages WHERE conversation_id = ?", (conversation_id,)
        )
        contents = [dict(r).get("content", "") for r in msg_rows]
        assert any("hello from turn one" in c for c in contents)
        assert any("and turn two" in c for c in contents)

    asyncio.run(_run())


def test_error_semantics_match_aidream(ai_app):
    """404 conversation_not_found / 409 conversation_already_exists /
    resume 404+409 / 422 tool_not_found — all in the {error,message,details}
    envelope the frontend parses."""

    async def _run() -> None:
        async with _client(ai_app) as client:
            # Continue a conversation that does not exist → 404 envelope.
            missing = str(uuid.uuid4())
            r = await client.post(
                f"/conversations/{missing}", json={"user_input": "hi"}
            )
            assert r.status_code == 404
            body = r.json()
            assert body["error"] == "conversation_not_found"
            assert missing in body["message"]
            assert isinstance(body["details"], dict)

            # Agent start asserting is_new on an EXISTING conversation → run
            # one turn first, then re-assert is_new → 409.
            cid = str(uuid.uuid4())
            async with client.stream(
                "POST",
                f"/agents/{_AGENT_ID}",
                json={"user_input": "seed", "conversation_id": cid, "is_new": True},
            ) as resp:
                assert resp.status_code == 200
                await _read_stream(resp)
            r = await client.post(
                f"/agents/{_AGENT_ID}",
                json={"user_input": "again", "conversation_id": cid, "is_new": True},
            )
            assert r.status_code == 409
            assert r.json()["error"] == "conversation_already_exists"

            # Unknown agent → 404 (aidream resolver shape).
            r = await client.post(
                f"/agents/{uuid.uuid4()}",
                json={"user_input": "x", "conversation_id": str(uuid.uuid4()), "is_new": True},
            )
            assert r.status_code == 404
            assert "Agent not found" in r.json()["message"]

            # Resume: unknown user_request → 404 user_request_not_found.
            r = await client.post(
                f"/conversations/{cid}/resume",
                json={"user_request_id": str(uuid.uuid4())},
            )
            assert r.status_code == 404
            assert r.json()["error"] == "user_request_not_found"

            # Resume: a real (terminal) user_request → 409 not_resumable.
            from app.services.local_db.database import get_db

            row = await get_db().fetchone(
                "SELECT id FROM user_requests WHERE conversation_id = ?", (cid,)
            )
            assert row is not None
            r = await client.post(
                f"/conversations/{cid}/resume",
                json={"user_request_id": dict(row)["id"]},
            )
            assert r.status_code == 409
            assert r.json()["error"] == "not_resumable"

            # Tool injection with an unknown tool name → 422 tool_not_found.
            r = await client.post(
                f"/conversations/{cid}",
                json={
                    "user_input": "x",
                    "tools": [{"kind": "registered", "name": "no_such_tool_xyz"}],
                },
            )
            assert r.status_code == 422
            body = r.json()
            assert body["error"] == "tool_not_found"
            assert "no_such_tool_xyz" in body["message"]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Real-engine mount probe (22199 fixture)
# ---------------------------------------------------------------------------


def test_ai_surface_mounted_on_engine(http: httpx.Client):
    """The /ai surface is live on the real engine at BOTH mounts — the 503
    ai_surface_migration_pending stub is gone — and errors are enveloped."""
    for base in ("/ai", "/chat/ai"):
        r = http.get(f"{base}/status")
        assert r.status_code == 200, f"{base}/status → {r.status_code}: {r.text[:200]}"
        body = r.json()
        assert body["surface"] == "local-ai"
        assert body["client_mode"] is True

        # A continue on a nonexistent conversation must be a structured 404 —
        # proving routing, context middleware, prep, and the envelope handler
        # all run on the real engine (not the old blanket 503).
        missing = str(uuid.uuid4())
        r = http.post(f"{base}/conversations/{missing}", json={"user_input": "hi"})
        assert r.status_code == 404, f"{base} continue → {r.status_code}: {r.text[:200]}"
        assert r.json()["error"] == "conversation_not_found"


def test_ai_surface_requires_auth_on_engine(http_public: httpx.Client):
    """The outer AuthMiddleware still gates /ai/* (bearer required)."""
    r = http_public.post(
        f"/ai/conversations/{uuid.uuid4()}", json={"user_input": "hi"}
    )
    assert r.status_code == 401
