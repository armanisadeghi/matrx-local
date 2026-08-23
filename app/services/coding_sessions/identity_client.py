"""Complete, owner-scoped coding-session identity inventory from AI Dream.

Reconciliation is an allowlist operation: a partial cloud inventory is not a
smaller truth, it is unsafe input.  This client therefore walks the server's
opaque keyset cursor and returns rows only after the final page proves that the
snapshot is complete and its row count matches ``total_count``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from app.services.aidream.client import (
    AIDreamClient,
    AIDreamError,
    AIDreamOfflineError,
)

IDENTITY_PATH = "/coding-sessions/sessions"
IDENTITY_SCHEMA_VERSION = 2
IDENTITY_PAGE_SIZE = 1000


class IdentityInventoryBlocked(RuntimeError):
    """The complete owner-scoped inventory could not be proven."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def fetch_complete_identity_inventory(
    *,
    client: AIDreamClient,
    jwt: str,
    provider: str,
) -> list[dict[str, Any]]:
    """Fetch every identity in one server-fenced snapshot or fail closed."""
    rows: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    cursor: str | None = None
    seen_cursors: set[str] = set()
    expected_total: int | None = None

    while True:
        query: dict[str, str | int] = {
            "provider": provider,
            "limit": IDENTITY_PAGE_SIZE,
        }
        if cursor is not None:
            query["cursor"] = cursor
        try:
            response = await client.get(
                f"{IDENTITY_PATH}?{urlencode(query)}",
                jwt=jwt,
            )
        except AIDreamOfflineError as exc:
            raise IdentityInventoryBlocked("aidream_unreachable") from exc
        except AIDreamError as exc:
            raise IdentityInventoryBlocked(f"aidream_error:{exc}") from exc

        if not isinstance(response, dict):
            raise IdentityInventoryBlocked("identity_list_malformed")
        if response.get("schema_version") != IDENTITY_SCHEMA_VERSION:
            raise IdentityInventoryBlocked("identity_list_completeness_unavailable")
        if response.get("provider") != provider:
            raise IdentityInventoryBlocked("identity_list_provider_mismatch")
        page = response.get("sessions")
        total = _integer(response.get("total_count"))
        page_count = _integer(response.get("page_count"))
        has_more = response.get("has_more")
        complete = response.get("complete")
        next_cursor = response.get("next_cursor")
        if (
            not isinstance(page, list)
            or total is None
            or total < 0
            or page_count != len(page)
            or not isinstance(has_more, bool)
            or not isinstance(complete, bool)
        ):
            raise IdentityInventoryBlocked("identity_list_malformed")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise IdentityInventoryBlocked("identity_list_snapshot_changed")
        if any(not isinstance(row, dict) for row in page):
            raise IdentityInventoryBlocked("identity_list_malformed")
        for row in page:
            identity = row.get("provider_session_id")
            if not isinstance(identity, str) or not identity:
                raise IdentityInventoryBlocked("identity_list_malformed")
            if identity in seen_identities:
                raise IdentityInventoryBlocked("identity_list_duplicate")
            seen_identities.add(identity)
        rows.extend(page)
        if len(rows) > expected_total:
            raise IdentityInventoryBlocked("identity_list_count_mismatch")

        if has_more:
            if complete or not isinstance(next_cursor, str) or not next_cursor:
                raise IdentityInventoryBlocked("identity_list_continuation_missing")
            if next_cursor in seen_cursors:
                raise IdentityInventoryBlocked("identity_list_cursor_loop")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            continue

        if next_cursor is not None or not complete or len(rows) != expected_total:
            raise IdentityInventoryBlocked("identity_list_incomplete")
        return rows


__all__ = [
    "IDENTITY_PAGE_SIZE",
    "IdentityInventoryBlocked",
    "fetch_complete_identity_inventory",
]
