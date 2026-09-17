"""The mirror never loses the path the person chose, and never calls a
foreign row "synced".

SUT: ``FileSyncEngine._push_upload`` over a REAL SQLite database (real
schema migrations, real ``file_sync_state``) and a double of the matrx-files
door that behaves as MEASURED against production on 2026-09-17.

WHY (feedback 75e2ae34-5ec6-45fb-ad1b-e909c86eb3f8). The push uploaded with
``file_path=rel_path`` and no dedup declaration. An undeclared matrx-files
write is implicitly ``alias_existing``: when the account already holds those
bytes the server keeps the CANONICAL row and writes nothing at the requested
path — while the response echoes the request back as if it had landed. The
mirror then rekeyed its state onto that foreign row and marked the path
``synced``, so the index claimed a path that exists nowhere in the cloud.
Two copies of one document in two folders is an ordinary thing to have.

Measured the same day against the live door as admin@admin.com: three uploads
of identical bytes at three different paths returned ONE file id, and the row
that id names is filed under the FIRST path.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest

from app.services.file_sync import engine as engine_module
from app.services.file_sync.client import FileSyncHTTPError
from app.services.file_sync.engine import FileSyncEngine
from app.services.local_db import database as database_module
from app.services.local_db.database import LocalDatabase

BYTES = b"the same bytes in two folders\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DoorDouble:
    """matrx-files' buffered upload door, as production actually answers it."""

    def __init__(self, *, honour_placement: bool = True) -> None:
        self.honour_placement = honour_placement
        self.uploads: list[dict[str, Any]] = []
        self.records: dict[str, dict[str, Any]] = {}
        self._canonical: dict[str, str] = {}
        self._jwt: str | None = None

    def set_jwt(self, token: str | None) -> None:
        self._jwt = token

    @property
    def available(self) -> bool:
        return True

    async def upload(
        self,
        *,
        file_path: str,
        content: bytes,
        filename: str,
        mime_type: str | None = None,
        visibility: str = "private",
        intent: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.uploads.append({"file_path": file_path, "intent": intent, "reason": reason})
        checksum = _sha(content)
        canonical = self._canonical.get(checksum)
        placement = intent == "force_new_copy" and self.honour_placement
        if canonical is not None and not placement:
            # No new row: the requested path is thrown away, and only the
            # response pretends otherwise.
            return {"file_id": canonical, "file_path": file_path, "checksum": checksum}
        file_id = f"cloud-{len(self.uploads)}"
        if canonical is None:
            self._canonical[checksum] = file_id
        self.records[file_id] = {
            "id": file_id,
            "file_path": file_path,
            "file_name": filename,
            "mime_type": mime_type or "text/plain",
            "size_bytes": len(content),
            "checksum": checksum,
            "visibility": visibility,
            "current_version": 1,
            "parent_folder_id": None,
            "duplicate_of_file_id": canonical,
            "created_at": "2026-09-17T00:00:00Z",
            "updated_at": "2026-09-17T00:00:00Z",
            "deleted_at": None,
        }
        return {"file_id": file_id, "file_path": file_path, "checksum": checksum}

    async def get_record(self, file_id: str) -> dict[str, Any]:
        if file_id not in self.records:
            raise FileSyncHTTPError("GET", f"/files/{file_id}", 404, "not found")
        return dict(self.records[file_id])


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario, *, door: DoorDouble):
    monkeypatch.setattr(FileSyncEngine, "mode", property(lambda self: "pointers"))

    async def main() -> None:
        db = LocalDatabase(path=tmp_path / "placement.db")
        await db.connect()
        monkeypatch.setattr(database_module, "_instance", db)
        try:
            engine = FileSyncEngine(root=tmp_path / "FilesRoot")
            engine.root.mkdir(parents=True, exist_ok=True)
            engine._client = door  # type: ignore[assignment]
            engine.configure("user-placement", "jwt-placement")
            await scenario(engine, db)
        finally:
            await db.close()

    asyncio.run(main())


async def _queue(engine: FileSyncEngine, rel: str, content: bytes) -> dict[str, Any]:
    abs_path = engine.root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)
    digest = _sha(content)
    file_id = f"local:{rel}"
    await engine._index.upsert_state(
        file_id,
        rel_path=rel,
        local_state="dirty",
        pending_op="upload",
        local_hash=digest,
        local_size=len(content),
        local_mtime=abs_path.stat().st_mtime,
    )
    state = await engine._index.get_state(file_id)
    assert state is not None
    return state


async def _state_by_path(db: LocalDatabase, rel: str) -> dict[str, Any] | None:
    row = await db.fetchone("SELECT * FROM file_sync_state WHERE rel_path = ?", (rel,))
    return dict(row) if row else None


def test_two_folders_holding_the_same_bytes_each_keep_their_own_cloud_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: the second folder's file is aliased onto the first one's row, so
    that path exists nowhere in the cloud while the index calls it synced."""

    async def scenario(engine: FileSyncEngine, db: LocalDatabase) -> None:
        first = await _queue(engine, "docs/report.md", BYTES)
        await engine._push_upload(first)
        second = await _queue(engine, "archive/report.md", BYTES)
        await engine._push_upload(second)

        door: DoorDouble = engine._client  # type: ignore[assignment]
        ids = {u["file_path"]: None for u in door.uploads}
        assert set(ids) == {"docs/report.md", "archive/report.md"}
        assert all(u["intent"] == "force_new_copy" for u in door.uploads), (
            "every push must DECLARE the placement — without it the door files "
            "the bytes under the other path and this one is recorded nowhere"
        )

        rows_by_path = {r["file_path"]: r for r in door.records.values()}
        assert set(rows_by_path) == {"docs/report.md", "archive/report.md"}
        # One content behind two rows: the second is the first's dedup alias.
        alias = rows_by_path["archive/report.md"]
        assert alias["duplicate_of_file_id"] == rows_by_path["docs/report.md"]["id"]

        for rel in ("docs/report.md", "archive/report.md"):
            state = await _state_by_path(db, rel)
            assert state is not None, rel
            assert state["local_state"] == "synced", rel
            assert state["pending_op"] is None, rel
            assert state["error"] is None, rel
            assert int(state["placement_attempts"] or 0) == 0, rel
            assert door.records[state["file_id"]]["file_path"] == rel, (
                f"{rel} is keyed to a row filed under "
                f"{door.records[state['file_id']]['file_path']}"
            )

    _run(tmp_path, monkeypatch, scenario, door=DoorDouble())


def test_a_path_the_door_files_elsewhere_is_never_called_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: the mirror believes the response instead of the row, rekeys onto
    a foreign file and reports the path as synced — a lie in the index."""

    async def scenario(engine: FileSyncEngine, db: LocalDatabase) -> None:
        first = await _queue(engine, "docs/report.md", BYTES)
        await engine._push_upload(first)
        second = await _queue(engine, "archive/report.md", BYTES)
        await engine._push_upload(second)

        state = await _state_by_path(db, "archive/report.md")
        assert state is not None
        assert state["local_state"] != "synced", (
            "a path with no file of its own in AI Matrx is not synced"
        )
        assert state["pending_op"] == "upload", "it must still be queued to re-send"
        assert state["error"] and "docs/report.md" in state["error"], (
            "the state must NAME the path the bytes were filed under: "
            f"{state['error']!r}"
        )
        assert int(state["placement_attempts"]) == 1

        counts = await engine._index.counts()
        assert counts["unplaced"] == 1

    # A door that ignores the declaration is exactly what an older server
    # build is: the client must degrade honestly against it.
    _run(tmp_path, monkeypatch, scenario, door=DoorDouble(honour_placement=False))


def test_the_mirror_stops_re_uploading_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounded, not forever: a door that will not honour the placement is
    announced once the rounds run out, with the local copy declared intact."""

    async def scenario(engine: FileSyncEngine, db: LocalDatabase) -> None:
        await engine._push_upload(await _queue(engine, "docs/report.md", BYTES))
        for _ in range(engine_module._MAX_PLACEMENT_ATTEMPTS):
            state = await _state_by_path(db, "archive/report.md")
            if state is None:
                state = await _queue(engine, "archive/report.md", BYTES)
            await engine._push_upload(state)

        state = await _state_by_path(db, "archive/report.md")
        assert state is not None
        assert int(state["placement_attempts"]) == engine_module._MAX_PLACEMENT_ATTEMPTS
        assert state["pending_op"] is None, "it must stop re-uploading the same bytes"
        assert "intact" in (state["error"] or ""), (
            f"the give-up message must say the local copy is safe: {state['error']!r}"
        )
        door: DoorDouble = engine._client  # type: ignore[assignment]
        assert len(door.uploads) == 1 + engine_module._MAX_PLACEMENT_ATTEMPTS

    _run(tmp_path, monkeypatch, scenario, door=DoorDouble(honour_placement=False))
