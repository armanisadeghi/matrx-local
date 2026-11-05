"""The agent-catalog read pages a POST RPC the way PostgREST honors it.

WHY (system_error, 31 rows 2026-10-06..10-23, "agx_get_list_full returned more
than 50000 rows — refusing the payload as a runaway read"): the catalog is a
POST to /rpc/agx_get_list_full and PostgREST IGNORES the Range header on a POST
(measured live 2026-11-05: `Range: 0-1` answered `Content-Range: 0-999/1404`,
1000 rows). Every "page" was rows 0-999 again, so a person who can see more than
1000 agents looped until the 50,000-row guard refused the sync, every tick, and
the offline mirror went stale. `limit`/`offset` query parameters ARE honored on a
POST (`?limit=2&offset=0` answered `0-1/1404`).

The fake server below behaves exactly like that: it ignores Range, honors
limit/offset, and caps every response at max-rows 1000.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.agent_catalog import client as catalog_client

_TOTAL = 1404
_MAX_ROWS = 1000


def _row(i: int) -> dict[str, object]:
    row: dict[str, object] = {col: None for col in catalog_client.CATALOG_COLUMNS}
    row["id"] = f"00000000-0000-4000-8000-{i:012d}"
    row["name"] = f"Agent {i}"
    row["orchestra"] = None
    return row


_ALL_ROWS = [_row(i) for i in range(_TOTAL)]


def _postgrest(request: httpx.Request) -> httpx.Response:
    assert request.method == "POST"
    offset = int(request.url.params.get("offset", "0"))
    limit = int(request.url.params.get("limit", str(_MAX_ROWS)))
    # The Range header is deliberately ignored: PostgREST does so on a POST.
    page = _ALL_ROWS[offset : offset + min(limit, _MAX_ROWS)]
    end = offset + len(page) - 1
    content_range = f"{offset}-{end}/{_TOTAL}" if page else f"*/{_TOTAL}"
    return httpx.Response(206, json=page, headers={"Content-Range": content_range})


@pytest.fixture
def fake_postgrest(monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = httpx.AsyncClient

    def _client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_postgrest)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(catalog_client.httpx, "AsyncClient", _client)
    monkeypatch.setattr(catalog_client, "SUPABASE_URL", "https://db.example.test")


def test_a_catalog_past_max_rows_reads_every_row_once(fake_postgrest: None) -> None:
    rows = asyncio.run(catalog_client.fetch_agent_catalog("jwt"))

    assert len(rows) == _TOTAL
    assert len({r["id"] for r in rows}) == _TOTAL
    assert [r["id"] for r in rows] == [r["id"] for r in _ALL_ROWS]
