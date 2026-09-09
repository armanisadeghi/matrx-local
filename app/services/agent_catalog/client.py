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
returns the RPC's rows VERBATIM: the same keys, the same JSON types, in the
same order the database returned them. It never filters, renames, defaults,
reorders, or enriches. A row that reaches SQLite differently from how the
browser saw it is a defect, not a local convenience.

SUPERSET TOLERANCE (2026-09-08). The platform column list is a REQUIRED
MINIMUM, never an exact set. `agx_get_list_full()` grows columns (the
conductor badge's nullable `orchestra jsonb` is the next one), and every
installed desktop reads the same function the browser does. If a NEW column
were treated as a shape violation, one platform migration would take every
shipped desktop's catalog offline — a self-inflicted outage for a change that
is, by construction, additive. So:

  * a row missing ANY of `CATALOG_COLUMNS` is still refused loudly — the
    function changed in a way the mirror cannot honour;
  * a row carrying EXTRA columns is accepted and carried through verbatim
    (SQLite `raw_json`, then the `/agents/catalog` doors), so the desktop's
    structural client sees byte-for-byte what a browser sees;
  * a column this build knows about by name (`MIRRORED_COLUMNS`) also gets a
    first-class SQLite column so the mirror can index/query it;
  * an unknown extra column is announced once per read, never absorbed
    silently.

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

EXECUTION_RPC = "agx_get_execution_full"

# The REQUIRED MINIMUM every `agx_get_list_full()` row must carry, in the
# function's declared column order. Verified against the live definition
# (project brsgrqvjdzwihsvnfqkf) on 2026-09-08 with
#   select pg_get_functiondef('public.agx_get_list_full'::regproc)
# A missing column here is a refusal; ADDITIONAL columns are welcome and are
# carried through verbatim (see SUPERSET TOLERANCE in the module docstring).
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

#: Optional platform columns this build knows by name. They are NOT required —
#: a mirror that predates them is correct — but when the RPC returns one it
#: gets a first-class, nullable SQLite column (schema migration `_V33_...`) so
#: the mirror can query it, in addition to riding along in `raw_json`.
#:
#: `orchestra` is the conductor badge: `{mode, tagline, depth_budget,
#: member_count, member_titles}` or null.
OPTIONAL_CATALOG_COLUMNS: tuple[str, ...] = ("orchestra",)

#: Every column this build gives a SQLite home. Anything outside it still
#: round-trips through `raw_json`.
MIRRORED_COLUMNS: tuple[str, ...] = CATALOG_COLUMNS + OPTIONAL_CATALOG_COLUMNS

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
            f"{CATALOG_RPC} rows are missing REQUIRED column(s): {', '.join(missing)}. "
            "Extra columns are fine (they ride through verbatim); a MISSING one means "
            "the database function dropped or renamed part of the catalog contract — "
            "regenerate CATALOG_COLUMNS and the SQLite mirror migration together."
        )

    extras = _unmirrored_columns(rows)
    if extras:
        # Nothing silent: the row still round-trips verbatim through raw_json
        # and the doors, but a column this build has no SQLite home for is
        # said out loud with the remedy.
        logger.warning(
            "[agent_catalog] %s returned column(s) this build does not mirror "
            "as first-class SQLite columns: %s. They are stored and served "
            "VERBATIM (raw_json → /agents/catalog), so no client loses data. "
            "To index or query one, add it to OPTIONAL_CATALOG_COLUMNS and a "
            "schema migration in the same change.",
            CATALOG_RPC,
            ", ".join(extras),
        )

    logger.info(
        "[agent_catalog] %s returned %d row(s)%s",
        CATALOG_RPC,
        len(rows),
        f" (db count {declared_total})" if declared_total is not None else "",
    )
    return rows


async def fetch_agent_execution(jwt: str, agent_id: str) -> dict[str, Any] | None:
    """ONE agent's execution detail from `agx_get_execution_full(p_agent_id)`.

    This is the SAME read Cloud Chat makes in the browser
    (`supabase.rpc("agx_get_execution_full", {p_agent_id})`) — a SECURITY
    DEFINER function gated on `iam.has_access('agent', id, 'viewer')` or an
    active builtin, so membership is decided in the database exactly as it is
    online. The row is returned VERBATIM:

      id, variable_definitions, model_id, settings, tools, custom_tools,
      context_policies, auto_context_disabled, ui_gates

    `None` means the database answered zero rows: this user cannot see that
    agent (or it is soft-deleted). That is a real answer, not an error — the
    caller turns it into a loud refusal rather than an empty variables form.

    Replaces the aidream `GET /agents` listing route, which could only be
    filtered down to agents the caller CREATED and which failed outright for a
    user belonging to several organizations with no default — leaving every
    variables form silently empty.
    """
    if not jwt:
        raise AgentCatalogAuthError(
            f"no JWT — {EXECUTION_RPC} has no anonymous variant"
        )
    if not agent_id:
        raise AgentCatalogError(f"{EXECUTION_RPC} needs an agent id")

    url = _rpc_url(EXECUTION_RPC)
    headers = {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        **SUPABASE_PROFILE_HEADERS,
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        try:
            resp = await http.post(url, json={"p_agent_id": agent_id}, headers=headers)
        except httpx.HTTPError as exc:
            raise AgentCatalogError(f"{EXECUTION_RPC} unreachable: {exc}") from exc

    if resp.status_code in (401, 403):
        raise AgentCatalogAuthError(
            f"{EXECUTION_RPC} rejected the stored JWT (HTTP {resp.status_code}): "
            f"{resp.text[:300]}",
            status_code=resp.status_code,
        )
    if resp.status_code >= 400:
        raise AgentCatalogError(
            f"{EXECUTION_RPC} -> HTTP {resp.status_code}: {resp.text[:300]}",
            status_code=resp.status_code,
        )

    body = resp.json() if resp.text else []
    if isinstance(body, dict):
        return body
    if not isinstance(body, list):
        raise AgentCatalogError(
            f"{EXECUTION_RPC} returned {type(body).__name__}, not a JSON array"
        )
    if not body:
        return None
    row = body[0]
    if not isinstance(row, dict):
        raise AgentCatalogError(f"{EXECUTION_RPC} returned a non-object row")
    return row


def _unmirrored_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Columns the RPC returned that this build has no SQLite column for."""
    if not rows:
        return []
    return [c for c in rows[0] if c not in MIRRORED_COLUMNS]


def _first_row_missing_columns(rows: list[dict[str, Any]]) -> list[str]:
    """REQUIRED columns the first row does not carry.

    PostgREST omits nothing from a `RETURNS TABLE` row, so one row is a
    sufficient probe; an empty catalog proves nothing and is accepted. Extra
    columns are NEVER reported here — the contract is a minimum, not an exact
    set (see SUPERSET TOLERANCE in the module docstring).
    """
    if not rows:
        return []
    return [c for c in CATALOG_COLUMNS if c not in rows[0]]
