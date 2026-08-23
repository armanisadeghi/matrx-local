from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.coding_sessions.identity_client import (
    IdentityInventoryBlocked,
    fetch_complete_identity_inventory,
)


class _PagedClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    async def get(self, path: str, jwt: str) -> dict[str, Any]:
        assert jwt == "jwt"
        self.calls.append(path)
        query = parse_qs(urlparse(path).query)
        offset = int(query.get("cursor", ["0"])[0])
        limit = int(query["limit"][0])
        page = self.rows[offset : offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < len(self.rows)
        return {
            "schema_version": 2,
            "provider": "claude_code",
            "sessions": page,
            "total_count": len(self.rows),
            "page_count": len(page),
            "has_more": has_more,
            "complete": not has_more,
            "next_cursor": str(next_offset) if has_more else None,
        }


@pytest.mark.anyio
async def test_fetches_more_than_one_thousand_identities_without_truncation() -> None:
    expected = [{"provider_session_id": f"session-{index}"} for index in range(1_205)]
    client = _PagedClient(expected)

    actual = await fetch_complete_identity_inventory(
        client=client, jwt="jwt", provider="claude_code"
    )

    assert actual == expected
    assert len(client.calls) == 2
    assert "cursor=1000" in client.calls[1]


@pytest.mark.anyio
async def test_fails_closed_when_final_page_cannot_prove_completeness() -> None:
    class _IncompleteClient:
        async def get(self, path: str, jwt: str) -> dict[str, Any]:  # noqa: ARG002
            return {
                "schema_version": 2,
                "provider": "claude_code",
                "sessions": [{"provider_session_id": "only-row"}],
                "total_count": 2,
                "page_count": 1,
                "has_more": False,
                "complete": False,
                "next_cursor": None,
            }

    with pytest.raises(IdentityInventoryBlocked) as excinfo:
        await fetch_complete_identity_inventory(
            client=_IncompleteClient(), jwt="jwt", provider="claude_code"
        )

    assert excinfo.value.reason == "identity_list_incomplete"


@pytest.mark.anyio
async def test_fails_closed_against_legacy_response_without_completeness() -> None:
    class _LegacyClient:
        async def get(self, path: str, jwt: str) -> dict[str, Any]:  # noqa: ARG002
            return {"provider": "claude_code", "sessions": []}

    with pytest.raises(IdentityInventoryBlocked) as excinfo:
        await fetch_complete_identity_inventory(
            client=_LegacyClient(), jwt="jwt", provider="claude_code"
        )

    assert excinfo.value.reason == "identity_list_completeness_unavailable"
