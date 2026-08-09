"""Cloud-sync classification for locally stored scrapes.

The regression these pin: on a real machine 6 of 8 scrapes sat in terminal
'failed'. Three causes, one shared defect —

  * 422 Unprocessable Entity — `save_content` never sent the `page_name` the
    server marks required, so EVERY push since the 2026-04-29 /api/v1 →
    /api/scraper migration was rejected and the dual write was dead.
  * 502 Bad Gateway — a transient gateway blip that spent all five retries.
  * 401 Unauthorized — pushes made with no signed-in user.

The payload bug is fixed; the other two must never reach 'failed' again,
because both resolve themselves and neither says anything about the row.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.local_db import database as database_module
from app.services.local_db.database import LocalDatabase
from app.services.scraper import scrape_store
from app.services.scraper.scrape_store import (
    BLOCKED_AUTH,
    BLOCKED_OFFLINE,
    classify_push_error,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://scraper.app.matrxserver.com/api/scraper/content/save")
    return httpx.HTTPStatusError(
        f"Client error '{status}'", request=request, response=httpx.Response(status, request=request)
    )


async def _fresh_db(tmp_path: Path) -> LocalDatabase:
    db = LocalDatabase(path=tmp_path / "matrx.db")
    database_module._instance = db
    await db.connect()
    return db


async def _insert(db: LocalDatabase, row_id: str, **overrides: Any) -> None:
    values: dict[str, Any] = {
        "id": row_id,
        "url": f"https://example.com/{row_id}",
        "page_name": f"example_com_{row_id}",
        "domain": "example.com",
        "content": json.dumps({"text_data": "hello", "ai_research_content": "hello"}),
        "char_count": 10,
        "content_type": "html",
        "scraped_at": "2026-08-09T00:00:00+00:00",
        "cloud_sync_status": "pending",
        "cloud_sync_attempts": 0,
        "cloud_sync_error": None,
        "cloud_sync_blocked_reason": None,
        "is_deleted": 0,
        "user_id": "",
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    await db.execute(
        f"INSERT INTO scrape_pages ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )
    await db.commit()


async def _row(db: LocalDatabase, row_id: str) -> dict[str, Any]:
    found = await db.fetchone("SELECT * FROM scrape_pages WHERE id = ?", (row_id,))
    assert found is not None
    return dict(found)


class _FakeRemote:
    """Stands in for RemoteScraperClient, recording the exact payload sent."""

    def __init__(self, raises: BaseException | None = None) -> None:
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    async def save_content(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return {"status": "saved"}


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str | None,
    remote: _FakeRemote,
    token_calls: list[int] | None = None,
) -> None:
    async def _token() -> str | None:
        if token_calls is not None:
            token_calls.append(1)
        return token

    monkeypatch.setattr("app.services.scraper.auth_helper.get_active_user_token", _token)
    monkeypatch.setattr(
        "app.services.scraper.remote_client.get_remote_scraper", lambda: remote
    )


# ── classification ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_status_error(401), BLOCKED_AUTH),
        (_status_error(403), BLOCKED_AUTH),
        (_status_error(502), BLOCKED_OFFLINE),   # the observed stuck row
        (_status_error(500), BLOCKED_OFFLINE),
        (_status_error(503), BLOCKED_OFFLINE),
        (_status_error(429), BLOCKED_OFFLINE),
        (_status_error(408), BLOCKED_OFFLINE),
        (httpx.ConnectError("no route to host"), BLOCKED_OFFLINE),
        (httpx.ReadTimeout("timed out"), BLOCKED_OFFLINE),
        (_status_error(422), None),              # genuine rejection
        (_status_error(400), None),
        (_status_error(404), None),
        (ValueError("something we cannot name"), None),
    ],
)
def test_classification(exc: BaseException, expected: str | None) -> None:
    assert classify_push_error(exc) == expected


# ── push outcomes ──────────────────────────────────────────────────────────


def test_no_token_defers_and_spends_no_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        db = await _fresh_db(tmp_path)
        remote = _FakeRemote()
        _wire(monkeypatch, token=None, remote=remote)
        await _insert(db, "r1")

        for _ in range(scrape_store._MAX_AUTO_RETRIES + 3):
            assert await scrape_store.push_pending_to_cloud() == {
                "pushed": 0, "deferred": 1, "failed": 0
            }

        row = await _row(db, "r1")
        assert row["cloud_sync_status"] == "pending"     # never terminal
        assert row["cloud_sync_attempts"] == 0           # budget untouched
        assert row["cloud_sync_blocked_reason"] == BLOCKED_AUTH
        assert remote.calls == []                        # never even attempted
        await db.close()

    asyncio.run(run())


def test_expired_token_401_defers_and_spends_no_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token that looks valid locally but the server rejects.

    `get_active_user_token` filters tokens it knows are expired, so the server
    is the one that discovers a stale JWT — a 401 must land in the same state
    as having no token at all, not in 'failed'.
    """

    async def run() -> None:
        db = await _fresh_db(tmp_path)
        remote = _FakeRemote(raises=_status_error(401))
        _wire(monkeypatch, token="stale.jwt.value", remote=remote)
        await _insert(db, "r1")

        for _ in range(scrape_store._MAX_AUTO_RETRIES + 3):
            await scrape_store.push_pending_to_cloud()

        row = await _row(db, "r1")
        assert row["cloud_sync_status"] == "pending"
        assert row["cloud_sync_attempts"] == 0
        assert row["cloud_sync_blocked_reason"] == BLOCKED_AUTH
        await db.close()

    asyncio.run(run())


def test_server_outage_defers_and_spends_no_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observed 502 row: five retries burned on a gateway blip."""

    async def run() -> None:
        db = await _fresh_db(tmp_path)
        remote = _FakeRemote(raises=_status_error(502))
        _wire(monkeypatch, token="good.jwt", remote=remote)
        await _insert(db, "r1")

        for _ in range(scrape_store._MAX_AUTO_RETRIES + 3):
            await scrape_store.push_pending_to_cloud()

        row = await _row(db, "r1")
        assert row["cloud_sync_status"] == "pending"
        assert row["cloud_sync_attempts"] == 0
        assert row["cloud_sync_blocked_reason"] == BLOCKED_OFFLINE
        await db.close()

    asyncio.run(run())


def test_genuine_rejection_is_the_only_thing_that_spends_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        db = await _fresh_db(tmp_path)
        remote = _FakeRemote(raises=_status_error(422))
        _wire(monkeypatch, token="good.jwt", remote=remote)
        await _insert(db, "r1")

        assert await scrape_store.push_pending_to_cloud() == {
            "pushed": 0, "deferred": 0, "failed": 1
        }
        row = await _row(db, "r1")
        assert row["cloud_sync_status"] == "failed"
        assert row["cloud_sync_attempts"] == 1
        assert row["cloud_sync_blocked_reason"] is None
        assert "422" in row["cloud_sync_error"]

        # Automatic retries run out, and then stop.
        for _ in range(scrape_store._MAX_AUTO_RETRIES + 3):
            await scrape_store.reset_pending_failed()
            await scrape_store.push_pending_to_cloud()

        row = await _row(db, "r1")
        assert row["cloud_sync_status"] == "failed"
        assert row["cloud_sync_attempts"] == scrape_store._MAX_AUTO_RETRIES
        await db.close()

    asyncio.run(run())


def test_successful_push_sends_page_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`page_name` is required by the server; omitting it was the 422."""

    async def run() -> None:
        db = await _fresh_db(tmp_path)
        remote = _FakeRemote()
        _wire(monkeypatch, token="good.jwt", remote=remote)
        await _insert(db, "r1")

        assert await scrape_store.push_pending_to_cloud() == {
            "pushed": 1, "deferred": 0, "failed": 0
        }
        assert remote.calls[0]["page_name"] == "example_com_r1"
        assert "ttl_days" not in remote.calls[0]

        row = await _row(db, "r1")
        assert row["cloud_sync_status"] == "synced"
        assert row["cloud_sync_error"] is None
        assert row["cloud_sync_blocked_reason"] is None
        await db.close()

    asyncio.run(run())


def test_batch_resolves_the_token_once_and_reruns_write_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token decrypts through the OS keychain, and a deferred backlog is
    re-derived every two minutes — neither may scale with the row count."""

    async def run() -> None:
        db = await _fresh_db(tmp_path)
        calls: list[int] = []
        _wire(monkeypatch, token=None, remote=_FakeRemote(), token_calls=calls)
        for i in range(20):
            await _insert(db, f"r{i}")

        await scrape_store.push_pending_to_cloud(limit=50)
        assert len(calls) == 1  # not 20

        # Second pass over an unchanged backlog updates no rows at all.
        before = await db.fetchone(
            "SELECT COUNT(*) AS n FROM scrape_pages WHERE cloud_sync_blocked_reason = ?",
            (BLOCKED_AUTH,),
        )
        assert dict(before)["n"] == 20
        cursor = await db.execute("SELECT total_changes()")
        changes_before = (await cursor.fetchone())[0]
        await scrape_store.push_pending_to_cloud(limit=50)
        cursor = await db.execute("SELECT total_changes()")
        assert (await cursor.fetchone())[0] == changes_before
        await db.close()

    asyncio.run(run())


# ── recovery ───────────────────────────────────────────────────────────────


def test_sign_in_syncs_previously_stuck_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observed machine, replayed: rows stranded at the retry cap by
    failures that predate the fix all sync once a user signs in."""

    async def run() -> None:
        db = await _fresh_db(tmp_path)
        # Six rows exactly as found: three 422s, one 502, two 401s — all terminal.
        for i, error in enumerate(
            ["422 Unprocessable Entity"] * 3
            + ["502 Bad Gateway"]
            + ["401 Unauthorized"] * 2
        ):
            await _insert(
                db, f"stuck{i}",
                cloud_sync_status="failed",
                cloud_sync_attempts=scrape_store._MAX_AUTO_RETRIES,
                cloud_sync_error=error,
            )

        # Signed out: the background loop leaves every one of them alone.
        remote = _FakeRemote()
        _wire(monkeypatch, token=None, remote=remote)
        await scrape_store.reset_pending_failed()
        assert await scrape_store.push_pending_to_cloud() == {
            "pushed": 0, "deferred": 0, "failed": 0
        }
        summary = await scrape_store.get_sync_summary()
        assert summary["failed"] == 6 and summary["state"] == "rejected"

        # Sign in.
        _wire(monkeypatch, token="good.jwt", remote=remote)
        result = await scrape_store.sync_after_sign_in()

        assert result["revived"] == 6
        assert result["pushed"] == 6
        summary = await scrape_store.get_sync_summary()
        assert summary == {**summary, "synced": 6, "failed": 0, "pending": 0}
        assert summary["healthy"] is True and summary["state"] == "synced"
        await db.close()

    asyncio.run(run())


def test_manual_retry_revives_terminal_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        db = await _fresh_db(tmp_path)
        await _insert(
            db, "r1",
            cloud_sync_status="failed",
            cloud_sync_attempts=scrape_store._MAX_AUTO_RETRIES,
            cloud_sync_error="422 Unprocessable Entity",
        )
        # The automatic path refuses — that is what the budget is for.
        assert await scrape_store.reset_pending_failed() == 0
        # The explicit one revives it and clears the counter.
        assert await scrape_store.reset_pending_failed(include_terminal=True) == 1

        row = await _row(db, "r1")
        assert row["cloud_sync_status"] == "pending"
        assert row["cloud_sync_attempts"] == 0
        await db.close()

    asyncio.run(run())


# ── user-facing state ──────────────────────────────────────────────────────


def test_signed_out_state_offers_sign_in_not_an_error_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        db = await _fresh_db(tmp_path)
        _wire(monkeypatch, token=None, remote=_FakeRemote())
        await _insert(db, "r1")
        await scrape_store.push_pending_to_cloud()

        summary = await scrape_store.get_sync_summary()
        assert summary["state"] == "signed_out"
        assert summary["action"] == "sign_in"
        assert summary["unsynced"] == 1
        assert summary["healthy"] is False
        assert "Sign in" in summary["message"]
        # Never leak the raw diagnostic into what the user reads.
        assert "HTTPStatusError" not in summary["message"]
        await db.close()

    asyncio.run(run())


def test_offline_state_asks_for_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        db = await _fresh_db(tmp_path)
        _wire(monkeypatch, token="good.jwt", remote=_FakeRemote(raises=httpx.ConnectError("down")))
        await _insert(db, "r1")
        await scrape_store.push_pending_to_cloud()

        summary = await scrape_store.get_sync_summary()
        assert summary["state"] == "offline"
        assert summary["action"] == "none"
        assert summary["failed"] == 0 and summary["pending"] == 1
        await db.close()

    asyncio.run(run())


# ── the contract that actually broke ───────────────────────────────────────


def test_save_content_body_satisfies_the_servers_own_request_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate the real outgoing body against the SERVER's pydantic model.

    A hand-rolled fake cannot catch a missing required field — that is why a
    missing `page_name` shipped and every push 422'd for three months. The
    server and this client share the `matrx_scraper` package, so the request
    model is importable and can be the judge: if a field is added, renamed, or
    made required upstream, this fails here instead of in the user's log.
    """
    from matrx_scraper.api.ext_router import ContentSaveRequest

    from app.services.scraper.remote_client import RemoteScraperClient

    sent: dict[str, Any] = {}

    async def _post(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        sent.update(kwargs["json"])
        return httpx.Response(
            200,
            json={"status": "saved"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)

    asyncio.run(
        RemoteScraperClient(server_url="https://scraper.test").save_content(
            url="https://example.com/a",
            page_name="example_com_a",
            content={"text_data": "hi", "ai_research_content": "hi"},
            char_count=2,
            auth_token="jwt",
        )
    )

    parsed = ContentSaveRequest(**sent)
    assert parsed.url == "https://example.com/a"
    assert parsed.page_name == "example_com_a"
    assert parsed.char_count == 2

    for required in ("url", "page_name", "content"):
        with pytest.raises(Exception):
            ContentSaveRequest(**{k: v for k, v in sent.items() if k != required})


def test_empty_store_is_healthy(tmp_path: Path) -> None:
    async def run() -> None:
        db = await _fresh_db(tmp_path)
        summary = await scrape_store.get_sync_summary()
        assert summary["healthy"] is True
        assert summary["state"] == "synced"
        assert summary["unsynced"] == 0
        await db.close()

    asyncio.run(run())
