"""Characterization: canonical chat.* mirror — cutover migration, store
writes, outbox, and the chat sync engine's push/pull semantics.

Pins the behavior of:
  - app/services/local_db/mirror.py (attach + ensure, per-schema files)
  - schema.py V10 (bespoke chat tables -> canonical mirror + outbox seed)
  - app/services/ai/conversation_handler.py (canonical writes + enqueue)
  - app/services/local_db/repositories.py Conversations/Messages compat repos
  - app/services/chat_sync/engine.py (outbox drain, echo, keyset pull, LWW,
    pending-outbox protection, tombstones)

Everything runs against a REAL SQLite database in tmp_path (real migrations,
real ATTACHed mirror files); the PostgREST client is faked — nothing touches
the network. Each test runs inside ONE asyncio.run() so the aiosqlite
connection lives and dies on a single event loop.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

# ORDER MATTERS: app.common first — importing app.config as the process's
# first app module trips the app.config <-> app.common circular import.
import app.common  # noqa: F401

from app.services.local_db import database as database_module
from app.services.local_db.database import LocalDatabase
from app.services.local_db.mirror_schema import MIRROR_TABLES
from app.services.chat_sync.client import ChatSyncHTTPError
from app.services.chat_sync.engine import ChatSyncEngine


def _run(tmp_path: Path, scenario: Callable[[LocalDatabase], Awaitable[None]]) -> None:
    async def _main() -> None:
        db = LocalDatabase(tmp_path / "matrx.db")
        await db.connect()
        old = database_module._instance
        database_module._instance = db
        try:
            await scenario(db)
        finally:
            database_module._instance = old
            await db.close()

    asyncio.run(_main())


async def _seed_v9_bespoke(path: Path) -> None:
    """Build a pre-cutover (V9) database with bespoke chat data."""
    import aiosqlite

    from app.services.local_db import schema as sch

    raw = await aiosqlite.connect(str(path))
    await raw.execute(
        "CREATE TABLE _migrations (version INTEGER PRIMARY KEY, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    for v, sql in sch.MIGRATIONS:
        if v >= 10:
            continue
        for stmt in sql.split(";\n"):
            if stmt.strip():
                await raw.execute(stmt)
        await raw.execute("INSERT INTO _migrations (version) VALUES (?)", (v,))
    await raw.execute(
        "INSERT INTO conversations (id,title,mode,model,route_mode,agent_id,"
        "created_at,updated_at) VALUES "
        "('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01','Hi','chat','gpt','chat','a1','2026-01-01','2026-01-02')"
    )
    await raw.execute(
        "INSERT INTO messages (id,conversation_id,role,content,model,tool_calls,"
        "created_at) VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01','user','hello',NULL,NULL,'2026-01-01')"
    )
    await raw.execute(
        "INSERT INTO messages (id,conversation_id,role,content,model,tool_calls,"
        "created_at) VALUES "
        "('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01','assistant','world','gpt','[{\"n\":1}]','2026-01-02')"
    )
    await raw.execute(
        "INSERT INTO user_requests (id,conversation_id,user_id,status,created_at,"
        "updated_at) VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01','u1','completed','2026-01-01','2026-01-01')"
    )
    await raw.execute(
        "INSERT INTO tool_call_logs (id,conversation_id,user_request_id,status,data,"
        "created_at,updated_at) VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa31','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21','completed',"
        "'{\"tool_name\":\"Weather\",\"call_id\":\"cc1\",\"arguments\":{\"q\":1}}',"
        "'2026-01-01','2026-01-01')"
    )
    # Legacy localStorage-era conversation (non-UUID id) + one that already
    # exists server-side under a different id — both must migrate into the
    # mirror but must NOT be seeded into the push outbox.
    await raw.execute(
        "INSERT INTO conversations (id,title,mode,model,route_mode,created_at,updated_at) "
        "VALUES ('1751234567-abc','Legacy import','chat','','chat','2026-01-01','2026-01-01')"
    )
    await raw.execute(
        "INSERT INTO conversations (id,title,mode,model,route_mode,server_conversation_id,"
        "created_at,updated_at) VALUES "
        "('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa03','Server-linked','chat','','chat',"
        "'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb01','2026-01-01','2026-01-01')"
    )
    await raw.commit()
    await raw.close()


# ---------------------------------------------------------------------------
# Mirror attach + V10 cutover
# ---------------------------------------------------------------------------


def test_mirror_attach_creates_all_chat_tables(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase) -> None:
        rows = await db.fetchall(
            "SELECT name FROM chat.sqlite_master WHERE type='table' AND name NOT LIKE '\\_%' ESCAPE '\\'"
        )
        names = {r[0] for r in rows}
        assert names == set(MIRROR_TABLES["chat"].keys())
        # per-schema mirror file exists next to the main DB
        assert (tmp_path / "mirror" / "chat.db").exists()

    _run(tmp_path, scenario)


def test_v10_cutover_migrates_and_annihilates_bespoke(tmp_path: Path) -> None:
    async def _main() -> None:
        await _seed_v9_bespoke(tmp_path / "matrx.db")

    asyncio.run(_main())

    async def scenario(db: LocalDatabase) -> None:
        conv = dict(await db.fetchone("SELECT * FROM chat.conversation WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01'"))
        assert conv["title"] == "Hi"
        assert json.loads(conv["config"]) == {
            "mode": "chat", "route_mode": "chat", "model": "gpt",
        }
        assert conv["initial_agent_id"] == "a1"
        assert conv["message_count"] == 2

        msgs = [
            dict(r)
            for r in await db.fetchall(
                "SELECT * FROM chat.message ORDER BY position"
            )
        ]
        assert [m["position"] for m in msgs] == [0, 1]
        assert json.loads(msgs[0]["content"]) == [{"type": "text", "text": "hello"}]
        assert json.loads(msgs[1]["metadata"])["tool_calls"] == [{"n": 1}]

        ur = dict(await db.fetchone("SELECT * FROM chat.user_request WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21'"))
        assert json.loads(ur["metadata"])["conversation_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01"

        tc = dict(await db.fetchone("SELECT * FROM chat.tool_call WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa31'"))
        assert tc["tool_name"] == "Weather"
        assert tc["call_id"] == "cc1"

        # outbox seeded for every migrated row
        q = {
            (r["entity_type"], r["entity_id"])
            for r in await db.fetchall("SELECT entity_type, entity_id FROM sync_queue")
        }
        assert ("chat.conversation", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01") in q
        assert ("chat.message", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11") in q and ("chat.message", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12") in q
        assert ("chat.user_request", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21") in q
        assert ("chat.tool_call", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa31") in q

        # non-UUID legacy ids and server-linked conversations migrate into the
        # mirror but are NOT seeded for push (cloud pk is uuid / row already
        # exists server-side under another id)
        assert ("chat.conversation", "1751234567-abc") not in q
        assert ("chat.conversation", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa03") not in q
        legacy = dict(await db.fetchone(
            "SELECT * FROM chat.conversation WHERE id='1751234567-abc'"))
        assert legacy["title"] == "Legacy import"
        linked = dict(await db.fetchone(
            "SELECT * FROM chat.conversation WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa03'"))
        assert json.loads(linked["metadata"])["legacy_server_conversation_id"] == (
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb01"
        )

        # bespoke tables are GONE
        left = await db.fetchall(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name IN "
            "('conversations','messages','user_requests','tool_call_logs')"
        )
        assert not left

    _run(tmp_path, scenario)


# ---------------------------------------------------------------------------
# Store + compat repos write canonical rows and enqueue the outbox
# ---------------------------------------------------------------------------


def test_store_writes_canonical_rows_and_outbox(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase) -> None:
        from app.services.ai.conversation_handler import SQLiteConversationStore

        store = SQLiteConversationStore()
        await store.ensure_conversation_exists(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "u1", overrides={"route_mode": "agent", "agent_id": "ag1"}
        )
        await store.create_pending_user_request("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "u1")
        result = await store.persist_completed_request(
            {
                "conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01",
                "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21",
                "messages": [
                    {"role": "user", "content": "ping"},
                    {"role": "assistant", "content": "pong"},
                ],
            }
        )
        assert result["conversation_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01"
        assert len(result["message_ids"]) == 2

        # repeat persist is idempotent (deterministic ids by position)
        again = await store.persist_completed_request(
            {
                "conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01",
                "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21",
                "messages": [
                    {"role": "user", "content": "ping"},
                    {"role": "assistant", "content": "pong"},
                ],
            }
        )
        assert again["message_ids"] == []

        conv = dict(await db.fetchone("SELECT * FROM chat.conversation WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01'"))
        assert conv["created_by"] == "u1"
        assert conv["initial_agent_id"] == "ag1"
        assert conv["source_app"] == "matrx_local"
        assert conv["message_count"] == 2

        ur = dict(await db.fetchone("SELECT * FROM chat.user_request WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21'"))
        assert ur["status"] == "completed"
        assert ur["user_id"] == "u1"

        # tool logging: canonical columns + extras preserved in metadata
        await store.log_tool_call_start(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa32",
            {
                "conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "user_request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21",
                "tool_name": "X", "call_id": "k1", "status": "running",
                "arguments": {"a": 1}, "success": False, "metadata": {},
                "novel_key": "kept",
            },
        )
        await store.log_tool_call_update("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa32", {"status": "completed", "success": True})
        tc = dict(await db.fetchone("SELECT * FROM chat.tool_call WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa32'"))
        assert tc["status"] == "completed"
        assert tc["tool_name"] == "X"
        assert json.loads(tc["metadata"])["novel_key"] == "kept"

        # every write enqueued
        q = {
            (r["entity_type"], r["entity_id"])
            for r in await db.fetchall("SELECT entity_type, entity_id FROM sync_queue")
        }
        assert ("chat.conversation", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01") in q
        assert ("chat.user_request", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21") in q
        assert ("chat.tool_call", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa32") in q

        # reads round-trip through the store
        cfg = await store.get_conversation_config("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01")
        assert cfg["route_mode"] == "agent"
        assert cfg["agent_id"] == "ag1"
        assert [m["content"] for m in cfg["messages"]] == ["ping", "pong"]
        data = await store.get_conversation_data("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01")
        assert len(data["user_requests"]) == 1
        assert len(data["tool_calls"]) == 1

    _run(tmp_path, scenario)


def test_repo_compat_shapes_and_tombstone_delete(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase) -> None:
        from app.services.local_db.repositories import ConversationsRepo, MessagesRepo

        convs = ConversationsRepo()
        msgs = MessagesRepo()
        await convs.create(
            {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "title": "T", "mode": "chat", "model": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11",
             "route_mode": "chat", "agent_id": "a1", "user_id": "u1"}
        )
        got = await convs.get("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01")
        assert got["mode"] == "chat" and got["model"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11" and got["agent_id"] == "a1"
        assert got["server_conversation_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01"  # local id IS canonical

        await msgs.create(
            {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11", "conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "role": "user",
             "content": "hello", "tool_calls": [{"x": 1}], "error": "boom"}
        )
        listed = await msgs.list_by_conversation("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01")
        assert listed[0]["content"] == "hello"
        assert listed[0]["tool_calls"] == [{"x": 1}]
        assert listed[0]["error"] == "boom"
        assert listed[0]["position"] == 0

        await convs.update("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", {"title": "T2", "model": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12"})
        got = await convs.get("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01")
        assert got["title"] == "T2" and got["model"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12"
        # config merge must not clobber mode
        assert got["mode"] == "chat"

        # delete is a tombstone, not a hard delete — and it tombstones the
        # conversation's messages too (the bespoke schema cascade-deleted)
        await convs.delete("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01")
        assert await convs.get("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01") is None
        raw = dict(await db.fetchone("SELECT * FROM chat.conversation WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01'"))
        assert raw["deleted_at"] is not None
        raw_msg = dict(await db.fetchone("SELECT * FROM chat.message WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11'"))
        assert raw_msg["deleted_at"] is not None
        # idempotent: nothing left live to tombstone
        n = await msgs.delete_by_conversation("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01")
        assert n == 0

    _run(tmp_path, scenario)


# ---------------------------------------------------------------------------
# Chat sync engine — push/pull semantics against a fake PostgREST client
# ---------------------------------------------------------------------------


def _fake_cloud(engine: ChatSyncEngine):
    """Install capture fakes; returns the capture list."""
    pushed: list[tuple[str, list[dict[str, Any]]]] = []

    async def fake_upsert(table: str, rows: list[dict], pk_col: str = "id"):
        pushed.append((table, rows))
        out = []
        for r in rows:
            rr = dict(r)
            rr["organization_id"] = "org-1"
            rr["created_by"] = "u1"
            rr["updated_at"] = "2026-07-20T10:00:00.000000+00:00"
            out.append(rr)
        return out

    async def fake_get(table: str, **kw):
        return []

    engine._client.upsert_rows = fake_upsert  # type: ignore[method-assign]
    engine._client.get_rows_since = fake_get  # type: ignore[method-assign]
    return pushed


def test_push_drains_outbox_parent_first_and_applies_echo(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase) -> None:
        from app.services.ai.conversation_handler import SQLiteConversationStore

        store = SQLiteConversationStore()
        await store.ensure_conversation_exists("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "u1")
        await store.create_pending_user_request("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "u1")
        await store.persist_completed_request(
            {"conversation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa21",
             "messages": [{"role": "user", "content": "hi"}]}
        )

        engine = ChatSyncEngine()
        engine.configure("u1", "jwt")
        pushed = _fake_cloud(engine)
        summary = await engine.sync_cycle()

        assert summary["pushed"]["failed"] == 0
        order = [t for t, _ in pushed]
        assert order.index("conversation") < order.index("message")
        conv_payload = pushed[0][1][0]
        # cloud-owned columns stripped; jsonb sent as real JSON
        assert "organization_id" not in conv_payload
        assert "created_by" not in conv_payload
        assert isinstance(conv_payload["config"], dict)

        # echo applied: cloud stamps landed locally, queue drained
        row = dict(await db.fetchone("SELECT * FROM chat.conversation WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01'"))
        assert row["organization_id"] == "org-1"
        assert row["updated_at"].startswith("2026-07-20T10")
        left = await db.fetchone("SELECT COUNT(*) AS c FROM sync_queue")
        assert left["c"] == 0

    _run(tmp_path, scenario)


def test_pull_lww_tombstones_and_pending_protection(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase) -> None:
        from app.services.local_db.repositories import ConversationsRepo

        convs = ConversationsRepo()
        await convs.create({"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "title": "local", "user_id": "u1"})
        # drain the outbox so LWW comparisons run un-pended
        await db.execute("DELETE FROM sync_queue")
        await db.commit()
        await db.execute(
            "UPDATE chat.conversation SET updated_at='2026-07-10T00:00:00.000000Z' WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01'"
        )
        await db.commit()

        engine = ChatSyncEngine()
        engine.configure("u1", "jwt")

        async def fake_get(table, cursor_col=None, pk_col=None, cursor_ts=None,
                           cursor_id=None, limit=500):
            if table == "conversation" and not cursor_ts:
                return [
                    {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "title": "web-newer",
                     "updated_at": "2026-07-11T00:00:00+00:00"},
                    {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa02", "title": "web-only", "config": {"mode": "chat"},
                     "updated_at": "2026-07-11T00:00:00+00:00",
                     "deleted_at": "2026-07-11T00:00:00+00:00"},
                ]
            return []

        async def fake_upsert(table, rows, pk_col="id"):
            return [dict(r) for r in rows]

        engine._client.get_rows_since = fake_get  # type: ignore[method-assign]
        engine._client.upsert_rows = fake_upsert  # type: ignore[method-assign]
        await engine.sync_cycle()

        # newer remote wins
        assert (await db.fetchone(
            "SELECT title FROM chat.conversation WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01'"))[0] == "web-newer"
        # tombstoned web row lands as a tombstone (hidden from repo, kept in mirror)
        assert await convs.get("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa02") is None
        raw = dict(await db.fetchone("SELECT * FROM chat.conversation WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa02'"))
        assert raw["deleted_at"] is not None
        # pull never enqueues (no echo loop)
        assert (await db.fetchone("SELECT COUNT(*) AS c FROM sync_queue"))["c"] == 0
        # checkpoint advanced
        meta = dict(await db.fetchone(
            "SELECT * FROM sync_meta WHERE entity_type='chat.conversation'"))
        assert json.loads(meta["last_hash"])["id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa02"

        # stale remote must NOT clobber newer local
        async def fake_get_stale(table, cursor_col=None, pk_col=None, cursor_ts=None,
                                 cursor_id=None, limit=500):
            if table == "conversation":
                return [{"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "title": "STALE",
                         "updated_at": "2026-07-01T00:00:00+00:00"}]
            return []

        engine._client.get_rows_since = fake_get_stale  # type: ignore[method-assign]
        engine._cursors.clear()
        await db.execute("DELETE FROM sync_meta")
        await db.commit()
        await engine.sync_cycle()
        assert (await db.fetchone(
            "SELECT title FROM chat.conversation WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01'"))[0] == "web-newer"

        # pending-outbox protection: local unpushed change beats remote pull
        await convs.update("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", {"title": "local-edit"})  # enqueues + bumps updated_at
        async def fake_get_remote_newer_than_old(table, **kw):
            if table == "conversation":
                return [{"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01", "title": "web-mid",
                         "updated_at": "2026-07-11T00:00:01+00:00"}]
            return []
        engine._client.get_rows_since = fake_get_remote_newer_than_old  # type: ignore[method-assign]
        engine._cursors.clear()
        await db.execute("DELETE FROM sync_meta")
        await db.commit()
        # push must not run (would drain the queue) — pull directly
        await engine._pull_changes()
        assert (await db.fetchone(
            "SELECT title FROM chat.conversation WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01'"))[0] == "local-edit"

    _run(tmp_path, scenario)


def test_push_poison_row_isolation(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase) -> None:
        from app.services.local_db.repositories import ConversationsRepo

        convs = ConversationsRepo()
        await convs.create({"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa41", "title": "ok", "user_id": "u1"})
        await convs.create({"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa42", "title": "bad", "user_id": "u1"})

        engine = ChatSyncEngine()
        engine.configure("u1", "jwt")

        async def fake_upsert(table, rows, pk_col="id"):
            if any(r["id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa42" for r in rows):
                raise ChatSyncHTTPError("POST", table, 400, "constraint violated")
            return [dict(r) for r in rows]

        async def fake_get(table, **kw):
            return []

        engine._client.upsert_rows = fake_upsert  # type: ignore[method-assign]
        engine._client.get_rows_since = fake_get  # type: ignore[method-assign]
        summary = await engine.sync_cycle()

        assert summary["pushed"]["sent"] == 1
        assert summary["pushed"]["failed"] == 1
        left = [dict(r) for r in await db.fetchall("SELECT * FROM sync_queue")]
        assert len(left) == 1
        assert left[0]["entity_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa42"
        assert left[0]["attempts"] == 1

    _run(tmp_path, scenario)


def test_generator_is_current() -> None:
    """The generated mirror_schema.py must match the checked-in snapshot."""
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "generate_mirror_schema.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
