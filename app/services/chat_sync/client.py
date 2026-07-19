"""PostgREST client for the canonical ``chat`` schema.

Same wire pattern as the notes client (documents/supabase_client.py):
publishable key + user JWT, non-public schema selected via the
Accept-Profile / Content-Profile headers. This app NEVER holds a service
role key — RLS with the user's JWT is the entire authz story.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.common.system_logger import get_logger
from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL

logger = get_logger()

_REST_BASE = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else ""
_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


class ChatSyncHTTPError(RuntimeError):
    """A PostgREST call failed; carries enough context for loud reporting."""

    def __init__(self, method: str, table: str, status_code: int, body: str) -> None:
        self.method = method
        self.table = table
        self.status_code = status_code
        self.body = body[:500]
        super().__init__(f"{method} chat.{table} -> HTTP {status_code}: {self.body}")

    @property
    def is_auth(self) -> bool:
        return self.status_code in (401,)

    @property
    def is_permanent(self) -> bool:
        """4xx other than auth/rate-limit — retrying the same payload won't help."""
        return 400 <= self.status_code < 500 and self.status_code not in (401, 429)


class SupabaseChatClient:
    """Thin PostgREST wrapper scoped to the ``chat`` schema profile."""

    def __init__(self, schema: str = "chat") -> None:
        self._schema = schema
        self._jwt: str | None = None

    def set_jwt(self, token: str | None) -> None:
        self._jwt = token

    @property
    def available(self) -> bool:
        return bool(_REST_BASE and self._jwt)

    async def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if not _REST_BASE:
            raise RuntimeError("SUPABASE_URL not configured")
        if not self._jwt:
            raise RuntimeError("No JWT set — user must be authenticated")

        headers: dict[str, str] = {
            "apikey": SUPABASE_PUBLISHABLE_KEY,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._jwt}",
        }
        if method.upper() in _WRITE_METHODS:
            headers["Content-Profile"] = self._schema
        else:
            headers["Accept-Profile"] = self._schema
        if extra_headers:
            headers.update(extra_headers)

        url = f"{_REST_BASE}/{table}"
        # Cloud ops must never block local-first UX: 5s connect / 20s total
        # (pull pages can be larger than a single note write).
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            resp = await client.request(
                method, url, params=params, json=json_body, headers=headers
            )
        if resp.status_code == 204:
            return []
        if resp.status_code >= 400:
            raise ChatSyncHTTPError(method.upper(), table, resp.status_code, resp.text)
        return resp.json() if resp.text else []

    # ------------------------------------------------------------------
    # Pull — incremental keyset pagination on (cursor_col, pk)
    # ------------------------------------------------------------------

    async def get_rows_since(
        self,
        table: str,
        *,
        cursor_col: str,
        pk_col: str,
        cursor_ts: str | None,
        cursor_id: str | None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Fetch the next page of rows after the (cursor_ts, cursor_id) keyset.

        Ordering is (cursor_col asc, pk asc) so ties on the timestamp cannot
        skip rows — the classic `gt.updated_at` cursor loses same-timestamp
        rows past the page boundary.
        """
        params: dict[str, str] = {
            "select": "*",
            "order": f"{cursor_col}.asc,{pk_col}.asc",
            "limit": str(limit),
        }
        if cursor_ts:
            if cursor_id:
                params["or"] = (
                    f"({cursor_col}.gt.{cursor_ts},"
                    f"and({cursor_col}.eq.{cursor_ts},{pk_col}.gt.{cursor_id}))"
                )
            else:
                params[cursor_col] = f"gt.{cursor_ts}"
        return await self._request("GET", table, params=params)

    async def get_rows(
        self,
        table: str,
        *,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch rows with explicit PostgREST filters.

        Used for targeted hydration when the web asks this desktop to continue
        a conversation before the periodic mirror cursor has reached it.
        """
        query = {"select": "*", **(params or {})}
        return await self._request("GET", table, params=query)

    # ------------------------------------------------------------------
    # Push — insert-if-absent + optimistic conditional updates
    # ------------------------------------------------------------------

    async def insert_rows_if_absent(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        pk_col: str = "id",
    ) -> list[dict[str, Any]]:
        """Insert new rows without modifying rows that already exist.

        Blind ``resolution=merge-duplicates`` upserts are forbidden here: a
        stale desktop mirror must never replace newer cloud state.  Existing
        rows are updated separately through :meth:`update_row_if_version`.
        """
        if not rows:
            return []
        return await self._request(
            "POST",
            table,
            params={"on_conflict": pk_col},
            json_body=rows,
            extra_headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
        )

    async def update_row_if_version(
        self,
        table: str,
        row: dict[str, Any],
        *,
        pk_col: str,
        pk_value: str,
        expected_version: int,
    ) -> list[dict[str, Any]]:
        """PATCH one row only when its cloud version is still expected.

        PostgREST returns an empty representation when the predicate matches
        no row.  The caller treats that as an optimistic-concurrency conflict,
        refreshes the current cloud version, and may retry once.
        """
        return await self._request(
            "PATCH",
            table,
            params={pk_col: f"eq.{pk_value}", "version": f"eq.{expected_version}"},
            json_body=row,
            extra_headers={"Prefer": "return=representation"},
        )
