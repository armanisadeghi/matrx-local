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
       chat.tool_call row on disk).
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
from decimal import Decimal
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
        # Listing metadata is deliberately non-executable. If Local ever
        # regresses to interpreting this projection, the test fails loudly.
        "model_id": "must-never-be-used",
        "temperature": 0.2,
        "max_tokens": 512,
        "stream": True,
        "tools": [],
    },
    "is_active": True,
}

_MOCK_EXECUTION_DEFINITION = {
    "definition_id": _AGENT_ID,
    "agent_id": _AGENT_ID,
    "is_version": False,
    "version_number": 1,
    "revision": "test-revision",
    "name": "Smoke Agent",
    "model_id": "mock-model",
    "messages": [{"role": "system", "content": "Canonical smoke system prompt."}],
    "settings": {"temperature": 0.2, "max_output_tokens": 512, "stream": True},
    "tools": [],
    "custom_tools": [],
    "mcp_servers": [],
    "variable_definitions": [],
    "context_slots": [],
    "tool_config": {},
    "output_schema": None,
    "matrx_actions": None,
    "skill_config": {},
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

    from matrx_ai.client_host.agent_source import ExecutionAgentDefinition

    definition = ExecutionAgentDefinition.model_validate(
        _MOCK_EXECUTION_DEFINITION
    ).with_content_hash()

    class StaticExecutionAgentSource:
        async def load_for_execution(
            self, agent_id: str, *, is_version: bool = False
        ) -> dict[str, Any]:
            if agent_id != _AGENT_ID:
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "agent_not_found",
                        "message": f"Agent not found: {agent_id}",
                    },
                )
            assert is_version is False
            return definition.model_dump(mode="json")

    configure_ext(
        conversation_store=SQLiteConversationStore(),
        model_catalog=SqliteModelCatalog(),
        api_key_resolver=get_key_resolver(),
        execution_agent_source=StaticExecutionAgentSource(),
        source_app="matrx_local",
    )
    from app.services.ai.engine import install_client_host_queue_guard

    install_client_host_queue_guard()

    # The real engine installs this guard during initialize_matrx_ai(); this
    # direct configure_ext() fixture mirrors that client-host posture.

    # Local tool registry: definitions + executors (the engine's Phase A½+B).
    from matrx_ai.tools.registry import ToolRegistry

    from app.services.ai.local_tool_bridge import (
        build_local_tool_definitions,
        register_local_tools,
    )

    registry = ToolRegistry.get_instance()
    missing = [
        d for d in build_local_tool_definitions() if registry.get(d.name) is None
    ]
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
    import app.services.ai.local_ai_task as local_ai_task

    # Fast heartbeat so a sub-second mock stream still carries beats.
    monkeypatch.setattr(ai_routes, "_HEARTBEAT_INTERVAL", 0.02)
    # These wire-contract tests intentionally use matrx-ai's mock provider.
    # Local-model ownership has dedicated tests below and a real llama-server
    # acceptance run; keep the mock stream fixture focused on event/persistence.
    monkeypatch.setattr(
        local_ai_task,
        "bind_active_local_model",
        lambda config: str(config.model),
    )
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


def test_local_execution_rebinds_cloud_model_and_offering(monkeypatch):
    from matrx_ai.config import UnifiedConfig

    from app.services.ai import local_llm_registry
    from app.services.ai.local_ai_task import bind_active_local_model

    monkeypatch.setattr(
        local_llm_registry,
        "resolve_local_llm_model",
        lambda requested_model=None: {
            "port": 11434,
            "base_url": "http://127.0.0.1:11434/v1",
            "model_name": "custom-model.gguf",
            "canonical_model_name": "local/custom-model.gguf",
        },
    )
    config = UnifiedConfig.from_dict(
        {
            "model": "claude-sonnet-5",
            "offering_id": str(uuid.uuid4()),
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    config.runtime_offering_id = str(uuid.uuid4())
    config.matrx_model_name = "claude-sonnet-5"

    assert bind_active_local_model(config) == "local/custom-model.gguf"
    assert config.model == "local/custom-model.gguf"
    assert config.offering_id is None
    assert config.runtime_offering_id is None
    assert config.matrx_model_name is None


def test_local_execution_refuses_cloud_fallback_without_registered_model(
    monkeypatch,
):
    from fastapi import HTTPException
    from matrx_ai.config import UnifiedConfig

    from app.services.ai import local_llm_registry
    from app.services.ai.local_ai_task import bind_active_local_model

    monkeypatch.setattr(
        local_llm_registry,
        "resolve_local_llm_model",
        lambda requested_model=None: None,
    )
    config = UnifiedConfig.from_dict(
        {
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        bind_active_local_model(config)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "local_model_not_available"
    assert "No cloud provider was called" in exc_info.value.detail["message"]


def test_desktop_native_capability_injects_discovery_tool_and_typed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from matrx_ai.capabilities import ClientContext
    from matrx_ai.config import UnifiedConfig
    from matrx_ai.tools.models import ToolType
    from matrx_connect.context.app_context import AppContext
    from matrx_connect.emitters.stream_emitter import StreamEmitter

    from app.services.ai import local_ai_task

    captured: dict[str, object] = {}

    def fake_merge(config, ctx, specs, excluded=None, **kwargs):
        captured["names"] = [getattr(spec, "name", None) for spec in specs]
        return ctx

    class PermissiveRegistry:
        def get(self, name: str):
            return SimpleNamespace(tool_type=ToolType.LOCAL)

    monkeypatch.setattr("matrx_ai.tools.merge.merge_request_tools", fake_merge)
    monkeypatch.setattr(local_ai_task, "_registry", lambda: PermissiveRegistry())
    monkeypatch.setattr(
        local_ai_task,
        "_local_desktop_capability_state",
        lambda: {
            "platform": "darwin",
            "engine_version": "1.2.3",
            "instance_id": "inst-host",
            "tunnel_state": "active",
        },
    )

    ctx = AppContext(
        emitter=StreamEmitter(),
        user_id="user-1",
        is_authenticated=True,
        metadata={},
    )
    client = ClientContext(
        capabilities=["desktop-native"],
        state={
            "desktop-native": {
                "platform": "darwin",
                "instance_id": "inst-test",
                "target_instance_id": "inst-other",
                "tunnel_state": "active",
                "permissions_granted": ["screen-recording"],
                "loaded_categories": ["desktop"],
            }
        },
    )

    async def scenario() -> None:
        updated = await local_ai_task.apply_request_tools(
            UnifiedConfig(model="local/test", messages=[]),
            ctx,
            [],
            None,
            client=client,
        )
        assert "load_desktop_tools" in captured["names"]
        payload = updated.metadata["client_capabilities_payloads"]["desktop-native"]
        assert payload["instance_id"] == "inst-host"
        assert payload["engine_version"] == "1.2.3"

    asyncio.run(scenario())


def test_local_host_injects_desktop_capability_when_browser_reports_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from matrx_ai.capabilities import ClientContext
    from matrx_ai.config import UnifiedConfig
    from matrx_ai.tools.models import ToolType
    from matrx_connect.context.app_context import AppContext
    from matrx_connect.emitters.stream_emitter import StreamEmitter

    from app.services.ai import local_ai_task

    captured: dict[str, object] = {}

    def fake_merge(config, ctx, specs, excluded=None, **kwargs):
        captured["names"] = [getattr(spec, "name", None) for spec in specs]
        captured["client"] = ctx.metadata["remote_tool_request"]["client"]
        return ctx

    class PermissiveRegistry:
        def get(self, name: str):
            return SimpleNamespace(tool_type=ToolType.LOCAL)

    monkeypatch.setattr("matrx_ai.tools.merge.merge_request_tools", fake_merge)
    monkeypatch.setattr(local_ai_task, "_registry", lambda: PermissiveRegistry())
    monkeypatch.setattr(
        local_ai_task,
        "_local_desktop_capability_state",
        lambda: {
            "platform": "darwin",
            "engine_version": "1.2.3",
            "instance_id": "inst-host",
            "tunnel_state": "none",
        },
    )

    ctx = AppContext(
        emitter=StreamEmitter(),
        user_id="user-1",
        is_authenticated=True,
        metadata={},
    )

    async def scenario() -> None:
        updated = await local_ai_task.apply_request_tools(
            UnifiedConfig(model="local/test", messages=[]),
            ctx,
            [],
            None,
            client=ClientContext(
                surface="matrx-user/chat",
                capabilities=[],
                state={},
            ),
        )
        assert captured["names"] == ["load_desktop_tools"]
        client = captured["client"]
        assert client["surface"] == "matrx-user/chat"
        assert client["capabilities"] == ["desktop-native"]
        assert client["state"]["desktop-native"]["instance_id"] == "inst-host"
        assert "target_instance_id" not in client["state"]["desktop-native"]
        assert "permissions_granted" not in client["state"]["desktop-native"]
        assert "loaded_categories" not in client["state"]["desktop-native"]
        assert (
            updated.metadata["client_capabilities_payloads"]["desktop-native"][
                "platform"
            ]
            == "darwin"
        )

    asyncio.run(scenario())


def test_explicit_empty_tool_replacement_stays_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from matrx_ai.capabilities import ClientContext
    from matrx_ai.config import UnifiedConfig
    from matrx_ai.tools.models import ToolType
    from matrx_connect.context.app_context import AppContext
    from matrx_connect.emitters.stream_emitter import StreamEmitter

    from app.services.ai import local_ai_task

    captured: dict[str, object] = {}

    def fake_merge(config, ctx, specs, excluded=None, **kwargs):
        captured["names"] = [getattr(spec, "name", None) for spec in specs]
        captured["excluded"] = list(excluded or [])
        return ctx

    class PermissiveRegistry:
        def get(self, name: str):
            return SimpleNamespace(tool_type=ToolType.LOCAL)

    monkeypatch.setattr("matrx_ai.tools.merge.merge_request_tools", fake_merge)
    monkeypatch.setattr(local_ai_task, "_registry", lambda: PermissiveRegistry())
    monkeypatch.setattr(
        local_ai_task,
        "_local_desktop_capability_state",
        lambda: {
            "platform": "darwin",
            "engine_version": "1.2.3",
            "instance_id": "inst-host",
            "tunnel_state": "none",
        },
    )

    ctx = AppContext(
        emitter=StreamEmitter(),
        user_id="user-1",
        is_authenticated=True,
        metadata={},
    )

    async def scenario() -> None:
        config = UnifiedConfig(
            model="local/test", messages=[], tools=["existing-tool"]
        )
        await local_ai_task.apply_request_tools(
            config,
            ctx,
            [],
            [],
            client=ClientContext(
                surface="matrx-user/chat",
                capabilities=["desktop-native"],
                state={"desktop-native": {"loaded_categories": ["desktop"]}},
                amendments={
                    "add": [{"kind": "registered", "name": "amended-tool"}],
                    "remove": [],
                },
            ),
        )
        assert captured["names"] == []
        assert config.tools == []

    asyncio.run(scenario())


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
                    "agent_id": _AGENT_ID,
                    "messages": [{"role": "user", "content": "use the tool please"}],
                    "conversation_id": conversation_id,
                    "is_new": True,
                    "stream": True,
                    "tools": [{"kind": "registered", "name": "local_system"}],
                    "metadata": {
                        "mock": {
                            "latency_ms": 60,
                            "ttft_ms": 0,
                            "chunks": 2,
                            "mode": "text",
                            "text": "tool round trip complete",
                            "tool_calls": [
                                {
                                    "name": "local_system",
                                    "arguments": {"action": "info"},
                                }
                            ],
                        }
                    },
                },
            ) as resp:
                assert resp.status_code == 200, await resp.aread()
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
        assert (
            conv_events and conv_events[0]["data"]["conversation_id"] == conversation_id
        )
        assert "init" in types
        # The local tool actually ran (tool_event lifecycle on the stream)
        assert "tool_event" in types, f"no tool_event in stream: {sorted(set(types))}"
        tool_events = [e["data"] for e in events if e["event"] == "tool_event"]
        system_events = [
            td for td in tool_events if td.get("tool_name") == "local_system"
        ]
        assert system_events, f"local_system missing from tool events: {tool_events}"
        assert any(td.get("event") == "tool_completed" for td in system_events), (
            f"local_system did not complete: {system_events}"
        )
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
            "SELECT * FROM chat.conversation WHERE id = ?", (conversation_id,)
        )
        assert conv_row is not None, "conversation row missing"
        msg_rows = await local_db.fetchall(
            "SELECT * FROM chat.message WHERE conversation_id = ?", (conversation_id,)
        )
        contents = [dict(r).get("content", "") for r in msg_rows]
        assert any("use the tool please" in c for c in contents)
        assert any("tool round trip complete" in c for c in contents)
        tool_rows = await local_db.fetchall(
            "SELECT tc.*, m.role AS message_role "
            "FROM chat.tool_call tc "
            "LEFT JOIN chat.message m ON m.id = tc.message_id "
            "WHERE tc.conversation_id = ?",
            (conversation_id,),
        )
        assert tool_rows, "chat.tool_call row missing for the local tool call"
        assert all(dict(row).get("message_role") == "assistant" for row in tool_rows), (
            "persisted tool calls must link to their assistant message"
        )

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
            # The canonical authored prompt is present; the old warning that
            # admitted prompt-less execution must never return.
            warn_codes = [
                e["data"].get("code") for e in events if e["event"] == "warning"
            ]
            assert "agent_prompt_unavailable_locally" not in warn_codes

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
            "SELECT * FROM chat.message WHERE conversation_id = ?", (conversation_id,)
        )
        contents = [dict(r).get("content", "") for r in msg_rows]
        assert any("hello from turn one" in c for c in contents)
        assert any("and turn two" in c for c in contents)

    asyncio.run(_run())


def test_retry_rehydrates_pinned_agent_after_failed_first_turn(ai_app, local_db):
    """Exact regression: no client model/config payload, no warm AgentCache.

    A failed first turn left only the conversation provenance + user message.
    Retry must reload the canonical agent model/prompt and execute successfully.
    """

    conversation_id = str(uuid.uuid4())

    async def _run() -> None:
        from app.services.local_db.repositories import ConversationsRepo, MessagesRepo

        await ConversationsRepo().create(
            {
                "id": conversation_id,
                "agent_id": _AGENT_ID,
                "model": "",
                "title": "Failed first turn",
            }
        )
        await MessagesRepo().create(
            {
                "id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "role": "user",
                "content": "retry this persisted user turn",
            }
        )

        async with _client(ai_app) as client:
            async with client.stream(
                "POST",
                f"/conversations/{conversation_id}",
                json={"retry": True},
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers.get("x-conversation-id") == conversation_id
                events = await _read_stream(resp)

        types = _event_types(events)
        assert "completion" in types
        assert types[-1] == "end"
        assert not any(
            e["event"] == "error" and "model" in str(e["data"]).lower() for e in events
        )

    asyncio.run(_run())


def test_error_semantics_match_aidream(ai_app, monkeypatch):
    """404 conversation_not_found / 409 conversation_already_exists /
    resume 404+409 / 422 tool_not_found — all in the {error,message,details}
    envelope the frontend parses."""
    from app.services.ai.remote_tool_bridge import get_remote_tool_bridge

    async def _still_missing(names):
        return set(names)

    monkeypatch.setattr(get_remote_tool_bridge(), "ensure", _still_missing)

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
                json={
                    "user_input": "x",
                    "conversation_id": str(uuid.uuid4()),
                    "is_new": True,
                },
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
                "SELECT id FROM chat.user_request WHERE json_extract(metadata, '$.conversation_id') = ?",
                (cid,),
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


def test_tool_call_decimal_values_persist(local_db):
    """Tool logging accepts Decimal values from matrx-ai cost accounting."""
    from app.services.ai.conversation_handler import SQLiteConversationStore

    row_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())

    async def _run() -> None:
        store = SQLiteConversationStore()
        await store._write_tool_call(
            row_id,
            {
                "conversation_id": conversation_id,
                "tool_name": "local_system_info",
                "status": "completed",
                "success": True,
                "duration_ms": Decimal("42"),
                "cost_usd": Decimal("0.000123"),
                "metadata": {"decimal_extra": Decimal("1.5")},
            },
            replace=True,
        )
        row = await local_db.fetchone(
            "SELECT duration_ms, cost_usd, metadata FROM chat.tool_call WHERE id = ?",
            (row_id,),
        )
        assert row is not None
        data = dict(row)
        assert data["duration_ms"] == 42
        assert data["cost_usd"] == 0.000123
        assert json.loads(data["metadata"])["decimal_extra"] == "1.5"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Real-engine mount probe (22199 fixture)
# ---------------------------------------------------------------------------


def test_ai_surface_mounted_on_engine(http: httpx.Client):
    """The /ai surface is live on the real engine at all compatibility mounts — the 503
    ai_surface_migration_pending stub is gone — and errors are enveloped."""
    for base in ("/ai", "/v2/ai", "/chat/ai"):
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
        assert r.status_code == 404, (
            f"{base} continue → {r.status_code}: {r.text[:200]}"
        )
        assert r.json()["error"] == "conversation_not_found"


def test_ai_surface_requires_auth_on_engine(http_public: httpx.Client):
    """The outer AuthMiddleware still gates /ai/* (bearer required)."""
    r = http_public.post(f"/ai/conversations/{uuid.uuid4()}", json={"user_input": "hi"})
    assert r.status_code == 401
