"""The generic desktop mirror must never copy raw provider ledgers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chat_sync import engine as chat_sync_module
from app.services.chat_sync.engine import ChatSyncEngine
from app.services.local_db.mirror_schema import MIRROR_TABLES
from scripts.generate_mirror_schema import generate


def test_owner_only_raw_bridge_tables_are_excluded_from_generation_and_runtime() -> (
    None
):
    assert "coding_session" not in MIRROR_TABLES["chat"]
    assert "coding_session_entry" not in MIRROR_TABLES["chat"]

    snapshot_path = Path(__file__).parents[2] / "schema_mirror" / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    table_spec = {
        "kind": "table",
        "pk": ["id"],
        "columns": [
            {
                "name": "id",
                "udt": "uuid",
                "data_type": "uuid",
                "nullable": False,
                "default": None,
            }
        ],
    }
    snapshot["schemas"]["chat"]["coding_session"] = table_spec
    snapshot["schemas"]["chat"]["coding_session_entry"] = table_spec
    rendered = generate(snapshot)
    assert '"coding_session"' not in rendered
    assert '"coding_session_entry"' not in rendered


@pytest.mark.anyio
async def test_runtime_pull_refuses_raw_tables_from_a_malformed_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = {
        "coding_session": {"cursor_col": "updated_at"},
        "coding_session_entry": {"cursor_col": "created_at"},
    }
    monkeypatch.setitem(chat_sync_module.MIRROR_TABLES, "chat", malformed)
    engine = ChatSyncEngine()

    class ExplodingClient:
        async def get_rows_since(self, *_args: Any, **_kwargs: Any) -> list[Any]:
            raise AssertionError("raw owner-only table reached the cloud client")

    engine._client = ExplodingClient()  # type: ignore[assignment]
    assert await engine._pull_changes() == {
        "coding_session": {"skipped": "owner_only_raw_ledger"},
        "coding_session_entry": {"skipped": "owner_only_raw_ledger"},
    }


@pytest.mark.anyio
async def test_runtime_push_refuses_raw_tables_from_a_malformed_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = {
        "coding_session": {"pk": ["id"]},
        "coding_session_entry": {"pk": ["id"]},
    }
    monkeypatch.setitem(chat_sync_module.MIRROR_TABLES, "chat", malformed)

    class FakeDb:
        async def fetchall(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": 1,
                    "entity_type": "chat.coding_session_entry",
                    "entity_id": "11111111-1111-4111-8111-111111111111",
                    "attempts": 0,
                }
            ]

    monkeypatch.setattr(chat_sync_module, "get_db", lambda: FakeDb())
    engine = ChatSyncEngine()
    assert await engine._push_pending() == {"sent": 0, "failed": 1}
