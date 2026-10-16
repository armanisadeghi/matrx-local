"""Turn 1 freezes only the system text the completed request REPORTS it sent.

Before: any non-string frozen text (a failed ``to_storage_dict()``, a completed dict with no
``conversation`` row) wrote ``""`` — "frozen, empty" — so the conversation lost its system
prompt for good. Now only a positive report freezes; an unknown one stays NULL (never froze).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.local_db import database as database_module
from app.services.local_db.database import LocalDatabase

CONV = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb01"
REQ = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb02"


def _frozen_after(tmp_path: Path, completed: Any) -> Any:
    result: dict[str, Any] = {}

    async def _main() -> None:
        from app.services.ai.conversation_handler import SQLiteConversationStore

        db = LocalDatabase(tmp_path / "matrx.db")
        await db.connect()
        old = database_module._instance
        database_module._instance = db
        try:
            store = SQLiteConversationStore()
            await store.ensure_conversation_exists(CONV, "u1")
            await store.create_pending_user_request(REQ, CONV, "u1")
            await store.persist_completed_request(completed)
            row = await db.fetchone(
                "SELECT system_instruction FROM chat.conversation WHERE id = ?", (CONV,)
            )
            result["value"] = row["system_instruction"]
        finally:
            database_module._instance = old
            await db.close()

    asyncio.run(_main())
    return result["value"]


def _completed_dict(conversation: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"conversation_id": CONV, "request_id": REQ, "messages": []}
    if conversation is not None:
        payload["conversation"] = conversation
    return payload


class _Completed:
    def __init__(self, storage: Any) -> None:
        self.request = SimpleNamespace(request_id=REQ)
        self.conversation_id = CONV
        self.messages: list[Any] = []
        self.metadata: dict[str, Any] = {}
        self._storage = storage

    def to_storage_dict(self) -> dict[str, Any]:
        if isinstance(self._storage, Exception):
            raise self._storage
        return self._storage


def test_reported_text_freezes(tmp_path: Path) -> None:
    assert _frozen_after(tmp_path, _completed_dict({"system_instruction": "You are X."})) == "You are X."


@pytest.mark.parametrize("reported", [None, ""])
def test_a_reported_empty_system_freezes_empty(tmp_path: Path, reported: Any) -> None:
    assert _frozen_after(tmp_path, _completed_dict({"system_instruction": reported})) == ""


def test_no_conversation_row_leaves_it_unfrozen(tmp_path: Path) -> None:
    assert _frozen_after(tmp_path, _completed_dict(None)) is None


def test_a_conversation_row_without_the_key_leaves_it_unfrozen(tmp_path: Path) -> None:
    assert _frozen_after(tmp_path, _completed_dict({"ai_model": "m"})) is None


def test_a_failed_serialization_leaves_it_unfrozen(tmp_path: Path) -> None:
    assert _frozen_after(tmp_path, _Completed(RuntimeError("boom"))) is None


def test_an_object_reporting_its_text_freezes(tmp_path: Path) -> None:
    storage = {"conversation": {"system_instruction": "Be brief."}, "user_request": {}, "requests": []}
    assert _frozen_after(tmp_path, _Completed(storage)) == "Be brief."
