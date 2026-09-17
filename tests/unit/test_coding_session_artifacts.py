"""Coding-session artifacts: capture what a session built, keep it durable,
publish it — and skip the working copies that live beside it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.coding_sessions import artifacts as mod
from app.services.coding_sessions.artifacts import (
    MANIFEST_NAME,
    CodingSessionArtifactsLane,
)
from app.services.file_sync.client import FileSyncHTTPError
from app.services.local_db.database import LocalDatabase

pytestmark = pytest.mark.anyio


class _Tokens:
    def __init__(self, row: dict[str, Any] | None = None, expired: bool = False) -> None:
        self.row, self.expired = row, expired

    async def get(self) -> dict[str, Any] | None:
        return self.row

    def is_expired(self, _row: dict[str, Any]) -> bool:
        return self.expired


class _FakeFilesClient:
    """Models the real matrx-files door as MEASURED against production on
    2026-09-17, not as its response model advertises:

    * identical bytes are aliased onto the CANONICAL row (`matrx_files/dedup.py`
      implicit ``alias_existing``) — one row, one id, many logical paths;
    * the response says nothing useful about that: ``is_new`` comes back null
      and ``file_path`` merely echoes the request, so only ``GET /files/{id}``
      reveals the row's real path;
    * a repeated ``X-Idempotency-Key`` REPLAYS the stored response without
      doing any work — even after the row it named went to the trash.
    """

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.reads: list[str] = []
        self.rows: dict[str, dict[str, Any]] = {}
        self._by_content: dict[bytes, str] = {}
        self._replay: dict[str, dict[str, Any]] = {}
        self._next_id = 0
        self.jwt: str | None = None

    def set_jwt(self, token: str | None) -> None:
        self.jwt = token

    async def upload(self, **kwargs: Any) -> dict[str, Any]:
        self.uploads.append(kwargs)
        key = kwargs.get("idempotency_key")
        if key and key in self._replay:
            return dict(self._replay[key])
        content = kwargs["content"]
        canonical = self._by_content.get(content)
        if canonical is None:
            self._next_id += 1
            canonical = f"file-{self._next_id}"
            self.rows[canonical] = {
                "id": canonical,
                "file_path": kwargs["file_path"],
                "deleted_at": None,
            }
            self._by_content[content] = canonical
        response = {
            "file_id": canonical,
            "file_path": kwargs["file_path"],  # echo, not the row's path
            "is_new": None,
            "size_bytes": len(content),
        }
        if key:
            self._replay[key] = dict(response)
        return response

    async def get_record(self, file_id: str) -> dict[str, Any]:
        self.reads.append(file_id)
        row = self.rows.get(file_id)
        if row is None:
            raise FileSyncHTTPError("GET", f"/files/{file_id}", 404, "not found")
        return dict(row)

    def drop_row(self, file_id: str) -> None:
        """The cloud stops serving a row the ledger still believes in."""
        self.rows.pop(file_id, None)
        self._by_content = {c: fid for c, fid in self._by_content.items() if fid != file_id}


def _scratchpad(tmp_path: Path, session: str = "11111111-2222-3333-4444-555555555555") -> Path:
    pad = tmp_path / "roots" / "claude-501" / "-Users-me-code" / session / "scratchpad"
    (pad / "qd" / "page").mkdir(parents=True)
    (pad / "qd" / "page" / "desk.html").write_text("<h1>desk</h1>")
    (pad / "report.md").write_text("# report")
    # Working copies beside the deliverables — never artifacts.
    (pad / "clone" / ".git").mkdir(parents=True)
    (pad / "clone" / "main.py").write_text("print('clone')")
    (pad / "node_modules" / "x").mkdir(parents=True)
    (pad / "node_modules" / "x" / "index.js").write_text("module.exports=1")
    (pad / ".venv" / "lib").mkdir(parents=True)
    (pad / ".venv" / "lib" / "site.py").write_text("x=1")
    (pad / "cache.pyc").write_bytes(b"\x00")
    (pad / "huge.bin").write_bytes(b"x" * (mod.MAX_FILE_BYTES + 1))
    return pad


async def _lane(tmp_path: Path, *, client: _FakeFilesClient, tokens: _Tokens, cloud: bool = True):
    db = LocalDatabase(tmp_path / "t.db")
    await db.connect()
    lane = CodingSessionArtifactsLane(
        db=db,
        roots=[tmp_path / "roots" / "claude-501"],
        durable_root=tmp_path / "durable",
        files_client=client,  # type: ignore[arg-type]
        cloud_enabled=cloud,
    )
    return db, lane


async def test_capture_keeps_deliverables_and_skips_working_copies(tmp_path: Path, monkeypatch) -> None:
    from app.services import sync_client, session_freshness
    from app.services.sync_client.client import SessionSnapshot
    class Daemon:
        last_state = SessionSnapshot(state="signed_out", state_reason="", signed_in=False)
        async def access_grant(self):
            return None
    monkeypatch.setattr(sync_client, "get_sync_client", Daemon)
    session_freshness.session_restored()
    pad = _scratchpad(tmp_path)
    client = _FakeFilesClient()
    db, lane = await _lane(tmp_path, client=client, tokens=_Tokens(None))
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens(None))
    try:
        tick = await lane.run_once()
        session = lane.session_detail("11111111-2222-3333-4444-555555555555")
        assert session is not None
        assert sorted(session["entries"]) == ["qd/page/desk.html", "report.md"]
        assert tick["captured"] == 2
        assert session["skipped_over_size"] == 1
        durable = tmp_path / "durable" / "11111111-2222-3333-4444-555555555555"
        assert (durable / "qd" / "page" / "desk.html").read_text() == "<h1>desk</h1>"
        manifest = json.loads((durable / MANIFEST_NAME).read_text())
        assert manifest["files"]["report.md"]["sha256"]
        # No session → visible blocker, nothing uploaded, nothing lost.
        assert lane.status()["blocker"]["code"] == "no_active_user_jwt"
        assert client.uploads == []

        # Unchanged files are not re-copied; a changed file is.
        (pad / "report.md").write_text("# report v2")
        import os
        import time
        os.utime(pad / "report.md", (time.time() + 5, time.time() + 5))
        tick = await lane.run_once()
        assert tick["captured"] == 1
        assert (durable / "report.md").read_text() == "# report v2"
    finally:
        await db.close()


async def test_publish_uploads_each_captured_file_once_with_session_tags(tmp_path: Path, monkeypatch) -> None:
    pad = _scratchpad(tmp_path)
    for filename in (
        "useAgentApp.fixed.ts",
        "component.tsx",
        "module.mts",
        "config.cts",
    ):
        (pad / filename).write_text("export const fixed = true;\n")
    client = _FakeFilesClient()
    db, lane = await _lane(tmp_path, client=client, tokens=_Tokens({"access_token": "jwt"}))
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))

    async def _no_refresh(**_: Any) -> bool:
        return False

    monkeypatch.setattr(mod, "request_session_grant", _no_refresh)
    try:
        tick = await lane.run_once()
        assert tick["uploaded"] == 6 and tick["failed"] == 0
        paths = sorted(u["file_path"] for u in client.uploads)
        assert paths == [
            "coding-sessions/claude_code/11111111-2222-3333-4444-555555555555/component.tsx",
            "coding-sessions/claude_code/11111111-2222-3333-4444-555555555555/config.cts",
            "coding-sessions/claude_code/11111111-2222-3333-4444-555555555555/module.mts",
            "coding-sessions/claude_code/11111111-2222-3333-4444-555555555555/qd/page/desk.html",
            "coding-sessions/claude_code/11111111-2222-3333-4444-555555555555/report.md",
            "coding-sessions/claude_code/11111111-2222-3333-4444-555555555555/useAgentApp.fixed.ts",
        ]
        meta = client.uploads[0]["metadata"]
        assert meta["kind"] == "coding_session_artifact"
        assert meta["cli_session_id"] == "11111111-2222-3333-4444-555555555555"
        assert client.uploads[0]["idempotency_key"].startswith("csa:")
        typescript_uploads = [
            upload
            for upload in client.uploads
            if Path(upload["file_path"]).suffix in {".ts", ".tsx", ".mts", ".cts"}
        ]
        assert len(typescript_uploads) == 4
        assert {upload["mime_type"] for upload in typescript_uploads} == {
            "text/typescript"
        }
        status = lane.status()
        assert status["uploaded"] == 6 and status["pending_upload"] == 0 and status["blocker"] is None
        # 4 of the 6 paths hold identical bytes, so matrx-files aliased 3 of
        # them onto ONE row: the screen may never call 6 paths 6 cloud files.
        assert status["deduplicated"] == 3
        assert status["cloud_rows"] == 3 == len(client.rows)
        assert status["distinct_content"] == 3
        assert status["awaiting_confirmation"] == 0 and status["missing_in_cloud"] == 0

        # Second tick: nothing new, nothing re-uploaded; manifest remembers file ids.
        tick = await lane.run_once()
        assert tick["uploaded"] == 0 and len(client.uploads) == 6
        manifest = json.loads(
            (tmp_path / "durable" / "11111111-2222-3333-4444-555555555555" / MANIFEST_NAME).read_text()
        )
        assert manifest["files"]["report.md"]["file_id"].startswith("file-")
    finally:
        await db.close()

async def test_cloud_off_keeps_files_locally_and_says_so(tmp_path: Path, monkeypatch) -> None:
    _scratchpad(tmp_path)
    client = _FakeFilesClient()
    db, lane = await _lane(tmp_path, client=client, tokens=_Tokens({"access_token": "jwt"}), cloud=False)
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    try:
        await lane.run_once()
        status = lane.status()
        assert status["files"] == 2 and status["uploaded"] == 0
        assert status["blocker"]["code"] == "cloud_disabled" and status["blocker"]["remedy"]
        assert client.uploads == []
    finally:
        await db.close()


async def test_persistent_upload_failure_is_abandoned_visibly_not_retried_forever(tmp_path: Path, monkeypatch) -> None:
    _scratchpad(tmp_path)

    class _Dropping(_FakeFilesClient):
        async def upload(self, **kwargs: Any) -> dict[str, Any]:
            self.uploads.append(kwargs)
            raise RuntimeError("")  # httpx ReadError-style: connection dropped mid-body

    client = _Dropping()
    db, lane = await _lane(tmp_path, client=client, tokens=_Tokens({"access_token": "jwt"}))
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    try:
        for _ in range(mod.MAX_UPLOAD_ATTEMPTS + 3):
            await lane.run_once()
        status = lane.status()
        # Each of the 2 files was tried exactly MAX times, then left alone — and said so.
        assert len(client.uploads) == 2 * mod.MAX_UPLOAD_ATTEMPTS
        assert status["abandoned_upload"] == 2 and status["pending_upload"] == 2
        entry = lane.session_detail("11111111-2222-3333-4444-555555555555")["entries"]["report.md"]
        assert entry["upload_attempts"] == mod.MAX_UPLOAD_ATTEMPTS
        assert "connection dropped" in entry["upload_error"]
        # The durable copy is untouched by upload failure.
        assert (tmp_path / "durable" / "11111111-2222-3333-4444-555555555555" / "report.md").exists()
    finally:
        await db.close()


async def test_a_returned_file_id_counts_only_once_it_reads_back(tmp_path: Path, monkeypatch) -> None:
    """The lie this closes: the lane called a file "uploaded" on the strength of
    the upload response alone, so a session could report 10,631 files in AI
    Matrx while the cloud held far fewer rows."""
    _scratchpad(tmp_path)

    class _Amnesiac(_FakeFilesClient):
        async def upload(self, **kwargs: Any) -> dict[str, Any]:
            self.uploads.append(kwargs)
            # Accepts the bytes, reports an id, keeps no row.
            return {"file_id": "file-ghost", "file_path": kwargs["file_path"], "is_new": True}

    client = _Amnesiac()
    db, lane = await _lane(tmp_path, client=client, tokens=_Tokens({"access_token": "jwt"}))
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    try:
        await lane.run_once()
        status = lane.status()
        assert status["uploaded"] == 0, "an unreadable file id is not storage"
        assert status["missing_in_cloud"] == 2 and status["pending_upload"] == 2
        entry = lane.session_detail("11111111-2222-3333-4444-555555555555")["entries"]["report.md"]
        assert entry["file_id"] is None and "no longer serves" in entry["verify_error"]
        # Nothing is lost: the durable copy stands and the entry is re-queued.
        assert (tmp_path / "durable" / "11111111-2222-3333-4444-555555555555" / "report.md").exists()
    finally:
        await db.close()


async def test_verify_now_repairs_a_row_the_cloud_stopped_serving(tmp_path: Path, monkeypatch) -> None:
    _scratchpad(tmp_path)
    client = _FakeFilesClient()
    db, lane = await _lane(tmp_path, client=client, tokens=_Tokens({"access_token": "jwt"}))
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    try:
        await lane.run_once()
        entries = lane.session_detail("11111111-2222-3333-4444-555555555555")["entries"]
        lost = entries["report.md"]["file_id"]
        assert lane.status()["uploaded"] == 2
        client.drop_row(lost)

        result = await lane.verify_now()

        assert result["missing_in_cloud"] == 1 and result["re_uploaded"] == 1
        assert result["still_missing_in_cloud"] == 0
        status = lane.status()
        assert status["uploaded"] == 2 and status["pending_upload"] == 0
        entry = lane.session_detail("11111111-2222-3333-4444-555555555555")["entries"]["report.md"]
        assert entry["file_id"] != lost and entry["verified_at"] and entry["verify_error"] is None
        assert entry["file_id"] in client.rows
    finally:
        await db.close()


async def test_a_changed_file_keeps_the_version_it_superseded(tmp_path: Path, monkeypatch) -> None:
    import os
    import time

    pad = _scratchpad(tmp_path)
    client = _FakeFilesClient()
    db, lane = await _lane(tmp_path, client=client, tokens=_Tokens({"access_token": "jwt"}))
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    try:
        await lane.run_once()
        first = lane.session_detail("11111111-2222-3333-4444-555555555555")["entries"]["report.md"][
            "file_id"
        ]
        (pad / "report.md").write_text("# report v2")
        os.utime(pad / "report.md", (time.time() + 5, time.time() + 5))
        await lane.run_once()
        entry = lane.session_detail("11111111-2222-3333-4444-555555555555")["entries"]["report.md"]
        assert entry["previous_file_ids"] == [first]
        assert lane.status()["superseded_versions"] == 1
    finally:
        await db.close()
