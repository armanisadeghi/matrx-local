"""PostgREST client for the ONE platform agent catalog RPC.

`public.agx_get_list_full()` is the catalog every Matrx client reads —
matrx-frontend, matrx-extend, workflow-studio, and (through this module) the
matrx-local sidecar. It is SECURITY DEFINER and callable by the authenticated
role; membership (owned + directly-shared + org-shared user agents + active
builtins) and ordering are decided INSIDE the database, never here.

🚨 THE LAW (Arman, 2026-09-08, ruling D4 of
`/Users/armanisadeghi/code/common-docs/projects/npm-package-extraction/AGENT-PICKER-DESIGN.md`):
matrx-local is NEVER an exception. The SQLite mirror exists so the catalog is
readable offline — "the data is saved locally, but the structure, the format,
and everything else must be absolutely identical." This module therefore
returns the RPC's rows VERBATIM: the same 19 keys, the same JSON types, in the
same order the database returned them. It never filters, renames, defaults,
reorders, or enriches. A row that reaches SQLite differently from how the
browser saw it is a defect, not a local convenience.

Wire posture is the app-wide one (docs/official/authentication.md): the
publishable key plus the user's JWT, RLS is the entire authz story, and no
service-role key exists on this machine. `POST /rest/v1/rpc/<fn>` with
`Content-Profile: public` (SUPABASE_PROFILE_HEADERS — the default exposed
schema on db.matrxserver.com is NOT `public`).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.common.system_logger import get_logger
from app.config import (
    SUPABASE_PROFILE_HEADERS,
    SUPABASE_PUBLISHABLE_KEY,
    SUPABASE_URL,
)

logger = get_logger()

CATALOG_RPC = "agx_get_list_full"

# The exact row `agx_get_list_full()` returns, in the function's declared
# column order. Verified against the live definition (project
# brsgrqvjdzwihsvnfqkf) on 2026-09-08 with
#   select pg_get_functiondef('public.agx_get_list_full'::regproc)
# Change this list ONLY after the DB function changes; it is the contract the
# mirror, the served rows, and the shape test all read.
CATALOG_COLUMNS: tuple[str, ...] = (
    "id",
    "agent_type",
    "name",
    "description",
    "model_id",
    "category",
    "tags",
    "is_active",
    "is_archived",
    "is_favorite",
    "created_by",
    "organization_id",
    "task_id",
    "source_agent_id",
    "created_at",
    "updated_at",
    "is_owner",
    "access_level",
    "shared_by_email",
)

# Supabase's server-side max-rows silently truncates an unranged response
# (default 1000). The live catalog was 1022 rows on 2026-09-08 — already past
# it — so paging is mandatory, not defensive. Same Range-header pattern as
# app/services/catalogs/client.py.
_PAGE_SIZE = 1000
_MAX_TOTAL_ROWS = 50_000

# The catalog is a background refresh of an offline mirror: generous enough for
# a multi-page read, never long enough to hold a sync tick hostage.
_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class AgentCatalogError(RuntimeError):
    """The catalog RPC could not be read. Always carries a stated reason."""

    def __init__(self, reason: str, *, status_code: int | None = None) -> None:
        self.reason = reason
        self.status_code = status_code
        super().__init__(reason)

    @property
    def is_auth(self) -> bool:
        return self.status_code in (401, 403)


class AgentCatalogAuthError(AgentCatalogError):
    """The stored JWT is missing/expired/rejected.

    Never downgraded to "use the stale mirror silently": membership is
    per-user, so an unauthenticated refresh would serve someone else's
    (or nobody's) list while claiming to be current.
    """


def _rpc_url(fn: str) -> str:
    if not SUPABASE_URL:
        raise AgentCatalogError("SUPABASE_URL is not configured")
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1/rpc/{fn}"


def _content_range_total(header: str | None) -> int | None:
    """Parse PostgREST's `Content-Range: 0-999/1022` total, if present."""
    if not header or "/" not in header:
        return None
    total = header.rsplit("/", 1)[1].strip()
    if not total.isdigit():
        return None
    return int(total)


async def fetch_agent_catalog(jwt: str) -> list[dict[str, Any]]:
    """Every row `agx_get_list_full()` returns for this user, in DB order.

    Raises `AgentCatalogAuthError` on 401/403 and `AgentCatalogError` on any
    other failure — the caller keeps the last good mirror and records the
    failure where the UI can see it. Never returns a partial list quietly: a
    short read against an exact count is an error, not a smaller catalog.
    """
    if not jwt:
        raise AgentCatalogAuthError("no JWT — the agent catalog RPC has no anonymous variant")

    url = _rpc_url(CATALOG_RPC)
    rows: list[dict[str, Any]] = []
    declared_total: int | None = None

    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        while True:
            start = len(rows)
            headers = {
                "apikey": SUPABASE_PUBLISHABLE_KEY,
                **SUPABASE_PROFILE_HEADERS,
                "Authorization": f"Bearer {jwt}",
                "Content-Type": "application/json",
                "Range-Unit": "items",
                "Range": f"{start}-{start + _PAGE_SIZE - 1}",
                # count=exact turns a silent truncation into an arithmetic
                # check we can fail on.
                "Prefer": "count=exact",
            }
            try:
                resp = await http.post(url, json={}, headers=headers)
            except httpx.HTTPError as exc:
                raise AgentCatalogError(f"{CATALOG_RPC} unreachable: {exc}") from exc

            if resp.status_code in (401, 403):
                raise AgentCatalogAuthError(
                    f"{CATALOG_RPC} rejected the stored JWT (HTTP {resp.status_code}): "
                    f"{resp.text[:300]}",
                    status_code=resp.status_code,
                )
            if resp.status_code >= 400:
                raise AgentCatalogError(
                    f"{CATALOG_RPC} -> HTTP {resp.status_code}: {resp.text[:300]}",
                    status_code=resp.status_code,
                )

            body = resp.json() if resp.text else []
            if not isinstance(body, list):
                raise AgentCatalogError(
                    f"{CATALOG_RPC} returned {type(body).__name__}, not a JSON array"
                )
            page = [r for r in body if isinstance(r, dict)]
            if len(page) != len(body):
                raise AgentCatalogError(
                    f"{CATALOG_RPC} returned {len(body) - len(page)} non-object row(s)"
                )

            if declared_total is None:
                declared_total = _content_range_total(resp.headers.get("content-range"))

            rows.extend(page)
            if len(rows) > _MAX_TOTAL_ROWS:
                raise AgentCatalogError(
                    f"{CATALOG_RPC} returned more than {_MAX_TOTAL_ROWS} rows — "
                    "refusing the payload as a runaway read"
                )
            if len(page) < _PAGE_SIZE:
                break

    if declared_total is not None and len(rows) != declared_total:
        raise AgentCatalogError(
            f"{CATALOG_RPC} paging is incomplete: read {len(rows)} rows but the "
            f"database reported {declared_total}. Refusing a partial catalog — "
            "a short list is indistinguishable from lost access."
        )

    missing = _first_row_missing_columns(rows)
    if missing:
        raise AgentCatalogError(
            f"{CATALOG_RPC} rows are missing expected column(s): {', '.join(missing)}. "
            "The database function changed shape — regenerate CATALOG_COLUMNS and the "
            "SQLite mirror migration together."
        )

    logger.info(
        "[agent_catalog] %s returned %d row(s)%s",
        CATALOG_RPC,
        len(rows),
        f" (db count {declared_total})" if declared_total is not None else "",
    )
    return rows


def _first_row_missing_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Columns the RPC promised but the first row does not carry.

    PostgREST omits nothing from a `RETURNS TABLE` row, so one row is a
    sufficient probe; an empty catalog proves nothing and is accepted.
    """
    if not rows:
        return []
    return [c for c in CATALOG_COLUMNS if c not in rows[0]]
