"""Characterization: the custom-record mirror — switch gate, pull, offline
capture, idempotent replay, and durable conflicts.

Everything runs against a REAL SQLite database in tmp_path (real migrations).
The store is a faithful in-memory stand-in for the doors: it enforces the two
behaviours the contract turns on — `anon_capture` is idempotent per
`(organization_id, client_key)` and counts replays, and `record_update`
refuses a stale `expected_version` with PT409.

The live twin of these tests (same engine, the REAL store) is
tests/live/test_records_mirror_live.py.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest

from app.services.local_db import database as database_module
from app.services.local_db.database import LocalDatabase
from app.services.records_sync.client import CustomStoreClient, RecordsStoreError
from app.services.records_sync.engine import RecordsMirrorUnavailable, RecordsSyncEngine

ORG = "884d1ce8-7b49-4fba-a2f3-0f7dd7c83d4f"
TABLE = "7774dec4-8dcc-4c16-956a-f80bbe6908e0"
USER = "87a6e699-3622-4869-8843-d0867456c0dd"


class FakeStore:
    """The doors, in memory, with the two behaviours that matter."""

    def __init__(self, *, switch_on: bool = True) -> None:
        self.switch_on = switch_on
        self.records: dict[str, dict[str, Any]] = {}
        self.replays: dict[str, dict[str, Any]] = {}
        self.offline = False
        self.calls: list[str] = []

    async def call(self, schema: str, function: str, args: dict[str, Any]) -> Any:
        self.calls.append(f"{schema}.{function}")
        if self.offline:
            raise RecordsStoreError(function, 0, "transport failure (no HTTP response)")
        handler = getattr(self, f"_{function}")
        return handler(args)

    # -- doors ---------------------------------------------------------

    def _knob_resolve(self, args: dict[str, Any]) -> Any:
        return self.switch_on

    def _read_records(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [
            {"id": rid, "document": dict(rec["document"]), "level": "admin"}
            for rid, rec in sorted(self.records.items())
            if rec["table_id"] == args["p_table_id"]
        ]
        offset = args.get("p_offset", 0)
        limit = args.get("p_limit", 200)
        return rows[offset : offset + limit]

    def _read_record(self, args: dict[str, Any]) -> dict[str, Any] | None:
        rec = self.records.get(args["p_record_id"])
        return dict(rec["document"]) if rec else None

    def _record_write(self, args: dict[str, Any]) -> str:
        rid = f"srv-{len(self.records) + 1:04d}"
        self.records[rid] = {
            "table_id": args["p_table_id"],
            "document": dict(args["p_data"]),
            "version": 1,
        }
        return rid

    def _anon_capture(self, args: dict[str, Any]) -> str:
        key = (args["p_organization_id"], args["p_client_key"])
        seen = self.replays.get(str(key))
        if seen is not None:
            seen["replays"] += 1
            return seen["record_id"]
        rid = self._record_write(
            {"p_table_id": args["p_table_id"], "p_data": args["p_payload"]}
        )
        self.replays[str(key)] = {"record_id": rid, "replays": 0}
        return rid

    def _record_update(self, args: dict[str, Any]) -> int:
        rec = self.records[args["p_record_id"]]
        expected = args.get("p_expected_version")
        if expected is not None and expected != rec["version"]:
            raise RecordsStoreError(
                "record_update",
                409,
                "Someone else changed this record while you were working on it.",
                code="PT409",
                details={"current_version": rec["version"], "expected_version": expected},
            )
        rec["document"].update(args["p_patch"])
        rec["version"] += 1
        return rec["version"]


def _run(tmp_path: Path, scenario: Callable[[LocalDatabase, FakeStore, RecordsSyncEngine], Awaitable[None]],
         *, switch_on: bool = True) -> None:
    async def _main() -> None:
        db = LocalDatabase(tmp_path / "matrx.db")
        await db.connect()
        old = database_module._instance
        database_module._instance = db
        store = FakeStore(switch_on=switch_on)
        engine = RecordsSyncEngine(CustomStoreClient(transport=store))
        engine.configure(user_id=USER, jwt=None, organization_id=ORG, table_ids=[TABLE], transport=store)
        try:
            await scenario(db, store, engine)
        finally:
            database_module._instance = old
            await db.close()

    asyncio.run(_main())


def test_switch_off_makes_the_feature_absent_and_loud(tmp_path: Path) -> None:
    """Law 4: closed is announced with a remedy — never a quiet empty sync."""

    async def scenario(db: LocalDatabase, store: FakeStore, engine: RecordsSyncEngine) -> None:
        with pytest.raises(RecordsMirrorUnavailable) as caught:
            await engine.sync_cycle()
        assert "switched off" in caught.value.reason
        assert "system_enabled" in caught.value.remedy
        # Nothing was read or written behind the closed switch.
        assert store.calls == ["platform.knob_resolve"]
        status = await engine.get_status()
        assert status["switch"]["open"] is False

    _run(tmp_path, scenario, switch_on=False)


def test_pull_writes_the_store_rows_into_the_desktop_sqlite(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase, store: FakeStore, engine: RecordsSyncEngine) -> None:
        store._record_write({"p_table_id": TABLE, "p_data": {"title": "one", "note": "from the store"}})
        await engine.sync_cycle()
        rows = await db.fetchall(
            "SELECT record_id, document, state, origin FROM custom_record_mirror"
        )
        assert len(rows) == 1
        assert json.loads(rows[0]["document"]) == {"title": "one", "note": "from the store"}
        assert rows[0]["state"] == "synced"
        assert rows[0]["origin"] == "cloud"

    _run(tmp_path, scenario)


def test_offline_edit_queues_without_touching_the_network(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase, store: FakeStore, engine: RecordsSyncEngine) -> None:
        store.offline = True
        local_id = await engine.create_record(TABLE, {"title": "written on a plane"})
        await engine.edit_record(local_id, {"note": "edited on the same plane"})
        assert store.calls == []  # authoring never reaches for the wire
        queued = await db.fetchall("SELECT action FROM sync_queue WHERE entity_type = 'custom.record'")
        assert [q["action"] for q in queued] == ["capture"]
        row = await db.fetchone("SELECT document, state FROM custom_record_mirror")
        assert json.loads(row["document"]) == {
            "title": "written on a plane",
            "note": "edited on the same plane",
        }
        assert row["state"] == "pending"

    _run(tmp_path, scenario)


def test_reconnect_drains_once_and_a_replay_never_duplicates(tmp_path: Path) -> None:
    """The heart of DOOR-21: same client key, same record, replays counted."""

    async def scenario(db: LocalDatabase, store: FakeStore, engine: RecordsSyncEngine) -> None:
        store.offline = True
        local_id = await engine.create_record(TABLE, {"title": "captured offline"})
        with pytest.raises(RecordsMirrorUnavailable) as offline:
            # Offline the switch itself cannot be read, so the cycle refuses —
            # loudly, with the remedy, and with the queue untouched.
            await engine.sync_cycle()
        assert "offline" in offline.value.reason
        assert "queued" in offline.value.remedy

        store.offline = False
        await engine.sync_cycle()
        assert len(store.records) == 1
        row = await db.fetchone("SELECT record_id, client_key, state FROM custom_record_mirror")
        assert row["state"] == "synced"
        record_id = row["record_id"]

        # Replay the SAME deferred write (a crash between write and ack).
        await db.execute(
            "INSERT INTO sync_queue (entity_type, entity_id, action, payload) "
            "VALUES ('custom.record', ?, 'capture', '{}')",
            (local_id,),
        )
        await db.commit()
        await engine.sync_cycle()
        assert len(store.records) == 1, "a replay wrote a second record"
        key = str((ORG, row["client_key"]))
        assert store.replays[key]["record_id"] == record_id
        assert store.replays[key]["replays"] == 1

    _run(tmp_path, scenario)


def test_edit_round_trips_through_record_update(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase, store: FakeStore, engine: RecordsSyncEngine) -> None:
        local_id = await engine.create_record(TABLE, {"title": "author here"})
        await engine.sync_cycle()
        await engine.edit_record(local_id, {"note": "edited after the capture"})
        await engine.sync_cycle()
        record_id = (await db.fetchone("SELECT record_id FROM custom_record_mirror"))["record_id"]
        assert store.records[record_id]["document"] == {
            "title": "author here",
            "note": "edited after the capture",
        }
        row = await db.fetchone("SELECT state, version FROM custom_record_mirror")
        assert row["state"] == "synced"
        assert row["version"] == 2

    _run(tmp_path, scenario)


def test_a_losing_edit_keeps_both_copies(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase, store: FakeStore, engine: RecordsSyncEngine) -> None:
        local_id = await engine.create_record(TABLE, {"title": "mine"})
        await engine.sync_cycle()
        await engine.edit_record(local_id, {"note": "mine"})
        await engine.sync_cycle()  # version now 2, known locally
        record_id = (await db.fetchone("SELECT record_id FROM custom_record_mirror"))["record_id"]
        store.records[record_id]["document"]["note"] = "theirs"
        store.records[record_id]["version"] = 5  # someone else wrote meanwhile

        await engine.edit_record(local_id, {"note": "mine again"})
        await engine.sync_cycle()
        row = await db.fetchone(
            "SELECT state, document, store_document, version FROM custom_record_mirror"
        )
        assert row["state"] == "conflict"
        assert json.loads(row["document"])["note"] == "mine again"
        assert json.loads(row["store_document"])["note"] == "theirs"
        assert row["version"] == 5
        status = await engine.get_status()
        assert status["conflicts"] == 1

    _run(tmp_path, scenario)
