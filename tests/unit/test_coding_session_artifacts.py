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
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.jwt: str | None = None

    def set_jwt(self, token: str | None) -> None:
        self.jwt = token

    async def upload(self, **kwargs: Any) -> dict[str, Any]:
        self.uploads.append(kwargs)
        return {"file_id": f"file-{len(self.uploads)}", "size_bytes": len(kwargs["content"])}


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
    _scratchpad(tmp_path)
    client = _FakeFilesClient()
    db, lane = await _lane(tmp_path, client=client, tokens=_Tokens({"access_token": "jwt"}))
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))

    async def _no_refresh(**_: Any) -> bool:
        return False

    monkeypatch.setattr(mod, "request_ui_session_refresh", _no_refresh)
    try:
        tick = await lane.run_once()
        assert tick["uploaded"] == 2 and tick["failed"] == 0
        paths = sorted(u["file_path"] for u in client.uploads)
        assert paths == [
            "coding-sessions/claude_code/11111111-2222-3333-4444-555555555555/qd/page/desk.html",
            "coding-sessions/claude_code/11111111-2222-3333-4444-555555555555/report.md",
        ]
        meta = client.uploads[0]["metadata"]
        assert meta["kind"] == "coding_session_artifact"
        assert meta["cli_session_id"] == "11111111-2222-3333-4444-555555555555"
        assert client.uploads[0]["idempotency_key"].startswith("csa:")
        status = lane.status()
        assert status["uploaded"] == 2 and status["pending_upload"] == 0 and status["blocker"] is None

        # Second tick: nothing new, nothing re-uploaded; manifest remembers file ids.
        tick = await lane.run_once()
        assert tick["uploaded"] == 0 and len(client.uploads) == 2
        manifest = json.loads(
            (tmp_path / "durable" / "11111111-2222-3333-4444-555555555555" / MANIFEST_NAME).read_text()
        )
        assert manifest["files"]["report.md"]["file_id"] == "file-1" or manifest["files"]["report.md"]["file_id"] == "file-2"
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
