"""matrx-ai 0.3.0 client-host smoke tests — in-process, no engine fixture.

Proves the Phase-3 migration contract end to end against matrx-local's REAL
seam implementations (not upstream fakes):

  1. ``matrx_ai.configure()`` accepts the engine's seam wiring without
     raising (ClientHostConfigError / DBNotConfiguredError).
  2. A mock-provider conversation round-trips through the real
     ``SQLiteConversationStore`` + ``SqliteModelCatalog`` against a tmp
     SQLite database: gate → execute → persist, with rows verifiably on disk.

Mirrors matrx-ai's ``tests/client_host/test_execute_with_store.py`` but with
the desktop's production store/catalog instead of in-memory fakes. The global
matrx-ai seam registry is snapshot/restored per test so nothing leaks into
other tests in the session.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

# Break the matrx-ai 0.3.0 providers ↔ orchestrator import cycle before any
# provider import (same import-order fix the engine applies at startup).
import matrx_ai.orchestrator  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_MODEL_ID = str(uuid.uuid4())
_MOCK_MODEL = {
    "id": _MOCK_MODEL_ID,
    "name": "mock-model",
    "common_name": "Mock Model",
    "provider": "mock",
    # "mock_chat" is a real UnifiedAIClient dispatch attr, so the catalog's
    # endpoints-based wire_format enrichment kicks in — the same path live
    # models take via metadata.legacy.endpoints.
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


class _FakeEmitter:
    """Every async emitter method is a no-op; sync turn-text helpers exist."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self._turn_text = ""

    def reset_turn_text(self) -> None:
        self._turn_text = ""

    def get_turn_text(self) -> str:
        return self._turn_text

    def __getattr__(self, name: str):
        async def _noop(*args: Any, **kwargs: Any) -> None:
            self.events.append(name)

        return _noop


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
    from app.services.local_db import database as db_module

    from app.services.ai import conversation_handler as ch_module

    db = db_module.LocalDatabase(tmp_path / "matrx.db")
    asyncio.run(db.connect())
    monkeypatch.setattr(db_module, "_instance", db)
    # The store singleton binds its repos to get_db() at construction; a
    # singleton created by an earlier test would point at a closed tmp DB.
    monkeypatch.setattr(ch_module, "_STORE_INSTANCE", None)
    try:
        yield db
    finally:
        asyncio.run(db.close())


def _configure_seams() -> None:
    """Register matrx-local's REAL seam implementations (registry-level, so
    the engine module's one-shot init guard is not consumed by tests)."""
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_configure_accepts_engine_seam_wiring(seam_sandbox, local_db):
    """matrx_ai.configure() with the engine's exact seams must not raise."""
    import matrx_ai

    from app.services.ai.conversation_handler import get_conversation_store
    from app.services.ai.key_manager import get_key_resolver
    from app.services.ai.model_catalog import get_model_catalog

    matrx_ai.configure(
        api_key_resolver=get_key_resolver(),
        conversation_store=get_conversation_store(),
        model_catalog=get_model_catalog(),
        source_app="matrx_local",
    )

    from matrx_ai._ext import has_ext

    for seam in ("api_key_resolver", "conversation_store", "model_catalog", "source_app"):
        assert has_ext(seam), f"seam {seam!r} not registered after configure()"


def test_catalog_resolves_model_without_db(seam_sandbox, local_db):
    """The SQLite catalog serves the model dict with an explicit wire_format
    and matrx-ai builds a call profile from it with zero ORM access."""

    async def _run() -> None:
        from app.services.local_db.repositories import ModelsRepo

        await ModelsRepo().upsert(dict(_MOCK_MODEL))
        _configure_seams()

        from matrx_ai.catalog import get_model_catalog as get_registered_catalog
        from matrx_ai.catalog.host_catalog import (
            CatalogModel,
            build_catalog_call_profile,
        )

        catalog = get_registered_catalog()
        assert catalog is not None
        model = await catalog.get_model("mock-model")
        assert model is not None, "catalog lost the model"
        assert model["wire_format"] == "mock_chat", (
            "endpoints-based wire_format enrichment did not fire"
        )
        profile = build_catalog_call_profile(CatalogModel(model))
        assert profile.wire_format == "mock_chat"
        assert profile.model_name == "mock-model"

    asyncio.run(_run())


def test_mock_conversation_round_trips_through_sqlite_store(seam_sandbox, local_db):
    """Classic execution path (execute_until_complete) with the mock provider:
    gate + persistence land in the REAL SQLite store, and the completed
    request carries the mock answer."""

    async def _run() -> None:
        from app.services.local_db.repositories import ModelsRepo

        await ModelsRepo().upsert(dict(_MOCK_MODEL))
        _configure_seams()

        from matrx_connect.context.app_context import AppContext, set_app_context

        conversation_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        emitter = _FakeEmitter()
        set_app_context(
            AppContext(
                emitter=emitter,
                user_id=user_id,
                request_id=request_id,
                conversation_id=conversation_id,
                # Internal-agent marker keeps the fire-and-forget conversation
                # labeler (a second LLM call) out of this test.
                is_internal_agent=True,
                store=True,
                source_app="matrx_local",
                source_feature="smoke_test",
            )
        )

        from matrx_ai.config import MessageList, TextContent, UnifiedConfig, UnifiedMessage
        from matrx_ai.orchestrator.executor import execute_until_complete
        from matrx_ai.orchestrator.requests import AIMatrixRequest
        from matrx_ai.providers.unified_client import UnifiedAIClient

        config = UnifiedConfig(
            model="mock-model",
            messages=MessageList(
                _messages=[
                    UnifiedMessage(role="user", content=[TextContent(text="hi there")])
                ]
            ),
            metadata={
                "mock": {
                    "latency_ms": 1,
                    "ttft_ms": 0,
                    "chunks": 1,
                    "mode": "text",
                    "text": "hello from the mock model",
                }
            },
        )
        request = AIMatrixRequest(
            conversation_id=conversation_id,
            config=config,
            request_id=request_id,
        )

        completed = await execute_until_complete(request, UnifiedAIClient())

        # The mock's answer came back through the real path.
        final_text = "".join(
            c.text
            for m in completed.final_response.messages
            for c in (m.content or [])
            if getattr(c, "text", None)
        )
        assert "hello from the mock model" in final_text

        # ── Rows actually landed in SQLite ────────────────────────────
        db = local_db
        conv_row = await db.fetchone(
            "SELECT * FROM chat.conversation WHERE id = ?", (conversation_id,)
        )
        assert conv_row is not None, "ensure_conversation_exists never wrote the row"

        req_row = await db.fetchone(
            "SELECT * FROM chat.user_request WHERE id = ?", (request_id,)
        )
        assert req_row is not None, "create_pending_user_request never wrote the row"
        assert dict(req_row)["status"] == "completed", (
            "persist_completed_request did not flip the request to completed"
        )

        msg_rows = await db.fetchall(
            "SELECT * FROM chat.message WHERE conversation_id = ? ORDER BY position",
            (conversation_id,),
        )
        contents = [dict(r).get("content", "") for r in msg_rows]
        assert any("hi there" in c for c in contents), "user message not persisted"
        assert any("hello from the mock model" in c for c in contents), (
            "assistant message not persisted"
        )

        # ── Store idempotency: repeat gate calls must be no-ops ───────
        from app.services.ai.conversation_handler import get_conversation_store

        store = get_conversation_store()
        await store.ensure_conversation_exists(conversation_id, user_id)
        await store.create_pending_user_request(request_id, conversation_id, user_id)
        again = await db.fetchall(
            "SELECT id FROM chat.user_request WHERE id = ?", (request_id,)
        )
        assert len(again) == 1

        # ── History read comes back through the store ─────────────────
        cfg = await store.get_conversation_config(conversation_id)
        assert cfg["messages"], "get_conversation_config returned no history"
        with pytest.raises(KeyError):
            await store.get_conversation_config(str(uuid.uuid4()))

        data = await store.get_conversation_data(conversation_id)
        assert data["conversation"]["id"] == conversation_id
        assert data["messages"], "get_conversation_data returned no messages"

    asyncio.run(_run())


def test_runtime_model_registration(seam_sandbox, monkeypatch):
    """local_llm_registry drives matrx-ai's public runtime-model registry
    (the 0.3.0 replacement for AiModelManager._api_cache pokes)."""
    from matrx_ai.catalog import get_runtime_model

    from app.services.ai import local_llm_registry as reg

    assert reg.set_local_llm(port=65533, model_name="qwen-test") is True
    entry = get_runtime_model("local/qwen-test")
    assert entry is not None
    assert entry.api_class == "generic_openai_standard"
    assert reg.is_local_llm_available()

    # get_local_llm_status() health-probes the registered port without taking
    # lifecycle ownership away from the Rust desktop. No real llama-server
    # listens on the test port, so simulate a reachable server first.
    monkeypatch.setattr(reg, "_probe_llama_server", lambda port: (True, None))
    status = reg.get_local_llm_status()
    assert status["canonical_model_name"] == "local/qwen-test"
    assert status["registered"] is True
    assert status["reachable"] is True
    assert status["available"] is True
    assert status["matrx_ai_support"] is True

    # A transient failed probe must report the truth without destroying the
    # registration. Cold model load and active inference can both delay the
    # single-threaded llama-server beyond the health timeout.
    monkeypatch.setattr(
        reg, "_probe_llama_server", lambda port: (False, "connection refused")
    )
    unreachable = reg.get_local_llm_status()
    assert unreachable["reachable"] is False
    assert unreachable["registered"] is True
    assert unreachable["available"] is False
    assert unreachable["port"] == 65533
    assert unreachable["model_name"] == "qwen-test"
    assert get_runtime_model("local/qwen-test") is not None
    assert reg.is_local_llm_available()

    # Only the explicit desktop lifecycle signal tears the registration down.
    reg.clear_local_llm()
    assert get_runtime_model("local/qwen-test") is None
    assert not reg.is_local_llm_available()


def test_local_llm_async_status_runs_probe_off_event_loop(monkeypatch):
    """Async chat routes must not block streams/heartbeats on urlopen()."""
    from app.services.ai import local_llm_registry as reg

    main_thread = threading.get_ident()
    monkeypatch.setattr(
        reg,
        "get_local_llm_status",
        lambda: {"probe_thread": threading.get_ident()},
    )

    result = asyncio.run(reg.get_local_llm_status_async())

    assert result["probe_thread"] != main_thread


def test_local_llm_status_discards_probe_when_registration_changes(monkeypatch):
    """A worker probe must not combine an old port with new registry state."""
    from app.services.ai import local_llm_registry as reg

    monkeypatch.setattr(reg, "_local_llm_port", 22399)
    monkeypatch.setattr(reg, "_local_llm_model", "old-model")
    monkeypatch.setattr(reg, "_registry_generation", 10)
    monkeypatch.setattr(reg, "_unregister_runtime_model", lambda _model: None)
    monkeypatch.setattr(
        reg, "_unregister_instance_from_unified_client", lambda _model: None
    )
    probe_started = threading.Event()
    release_probe = threading.Event()

    def blocked_probe(_port: int) -> tuple[bool, None]:
        probe_started.set()
        assert release_probe.wait(timeout=2)
        return True, None

    monkeypatch.setattr(reg, "_probe_llama_server", blocked_probe)

    async def run() -> dict[str, Any]:
        status_task = asyncio.create_task(reg.get_local_llm_status_async())
        assert await asyncio.to_thread(probe_started.wait, 1)
        reg.clear_local_llm()
        release_probe.set()
        return await status_task

    status = asyncio.run(run())

    assert status["registered"] is False
    assert status["port"] is None
    assert status["model_name"] is None
    assert status["reachable"] is False
    assert "changed during" in status["error"]
