"""Every mandate run, and its cost, is recorded against its mandate on this device too.

Local-model path: ``POST /local-mandates/{key}/runs`` writes a conversation + user_request
stamped ``mandate:<key>`` with llama-server's tokens and total_cost 0, queued for chat_sync.
Engine path: a request carrying ``mandate_key`` stamps the same attribution the server stamps.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.services.local_db import database as database_module
from app.services.local_db.database import LocalDatabase

KEY = "local.summarize_text"
CONV = "cccccccc-cccc-4ccc-8ccc-cccccccccc01"
REQ = "cccccccc-cccc-4ccc-8ccc-cccccccccc02"


def _with_db(tmp_path: Path, body: Any) -> Any:
    out: dict[str, Any] = {}

    async def _main() -> None:
        db = LocalDatabase(tmp_path / "matrx.db")
        await db.connect()
        old = database_module._instance
        database_module._instance = db
        try:
            out["value"] = await body(db)
        finally:
            database_module._instance = old
            await db.close()

    asyncio.run(_main())
    return out["value"]


def test_local_model_run_is_recorded_against_its_mandate(tmp_path: Path) -> None:
    async def body(db: LocalDatabase) -> Any:
        from app.services.ai.mandate_attribution import record_local_model_run

        ids = await record_local_model_run(
            mandate_key=KEY,
            user_id="u1",
            organization_id="00000000-0000-4000-8000-000000000001",
            usage={"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            duration_ms=900,
        )
        conv = await db.fetchone(
            "SELECT source_feature, metadata FROM chat.conversation WHERE id = ?",
            (ids["conversation_id"],),
        )
        req = await db.fetchone(
            "SELECT source_feature, metadata, total_input_tokens, total_output_tokens,"
            " total_tokens, total_cost FROM chat.user_request WHERE id = ?",
            (ids["user_request_id"],),
        )
        queued = await db.fetchall("SELECT entity_type, entity_id FROM sync_queue")
        return conv, req, queued

    conv, req, queued = _with_db(tmp_path, body)
    assert {q["entity_type"] for q in queued} == {"chat.conversation", "chat.user_request"}
    assert conv["source_feature"] == f"mandate:{KEY}"
    assert json.loads(conv["metadata"])["mandate_run"]["mandate_key"] == KEY
    assert req["source_feature"] == f"mandate:{KEY}"
    assert json.loads(req["metadata"])["mandate_key"] == KEY
    assert (req["total_input_tokens"], req["total_output_tokens"], req["total_tokens"]) == (120, 30, 150)
    assert req["total_cost"] == 0


def test_a_run_with_no_organization_is_refused_not_queued(tmp_path: Path) -> None:
    """The cloud tables require an organization; an org-less row would fail every sync push."""
    import pytest

    async def body(db: LocalDatabase) -> Any:
        from app.services.ai.mandate_attribution import record_local_model_run

        with pytest.raises(ValueError, match="without an organization"):
            await record_local_model_run(
                mandate_key=KEY, user_id="u1", organization_id=None, usage={"total_tokens": 5}
            )
        return await db.fetchall("SELECT entity_id FROM sync_queue")

    assert _with_db(tmp_path, body) == []


def test_engine_run_with_a_mandate_key_stamps_it(tmp_path: Path) -> None:
    from matrx_connect.context.app_context import AppContext, set_app_context

    from app.services.ai.local_ai_task import _apply_request_scope

    base = AppContext(user_id="u1", emitter=None, metadata={})
    request = SimpleNamespace(mandate_key=KEY, scope_ids=None, context=None)
    ctx = _apply_request_scope(base, request)
    assert ctx.source_feature == f"mandate:{KEY}"

    async def body(db: LocalDatabase) -> Any:
        from app.services.ai.conversation_handler import SQLiteConversationStore

        set_app_context(ctx)
        store = SQLiteConversationStore()
        await store.ensure_conversation_exists(CONV, "u1")
        await store.create_pending_user_request(REQ, CONV, "u1")
        conv = await db.fetchone(
            "SELECT source_feature, metadata FROM chat.conversation WHERE id = ?", (CONV,)
        )
        req = await db.fetchone(
            "SELECT source_feature, metadata FROM chat.user_request WHERE id = ?", (REQ,)
        )
        return conv, req

    conv, req = _with_db(tmp_path, body)
    assert conv["source_feature"] == f"mandate:{KEY}"
    assert json.loads(conv["metadata"])["mandate_run"]["mandate_key"] == KEY
    assert req["source_feature"] == f"mandate:{KEY}"
    assert json.loads(req["metadata"])["mandate_key"] == KEY
