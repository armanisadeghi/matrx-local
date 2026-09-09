"""The platform agent catalog, served offline in its IDENTICAL shape.

`GET /agents/catalog` and `POST /agents/catalog/rpc` hand back exactly what
`supabase.rpc("agx_get_list_full")` hands a browser: a JSON array of 19-key
rows, in the database's own order, with the database's own JSON types. The
sidecar mirrors those rows into SQLite (`SyncEngine.sync_agents`) so the
desktop can read the SAME catalog with the network down.

🚨 Ruling D4 (Arman, 2026-09-08) —
`/Users/armanisadeghi/code/common-docs/projects/npm-package-extraction/AGENT-PICKER-DESIGN.md`:
"the SQL light mirror is simply designed to give a user offline access, and so
nothing should ever change ... matrx local has an option to work locally. And
when it works locally, the data is saved locally. But the structure, the
format, and everything else must be absolutely identical." Adding a field,
dropping a field, re-sorting, or filtering these rows here is a defect.

THE CONTRACT the desktop TS lane codes against
----------------------------------------------
  GET  /agents/catalog
       auth: the engine's normal loopback/bearer posture (NOT public — the row
             set is this user's membership)
       200 : `AgentCatalogRow[]` — bare array, no wrapper
       503 : `{"detail": {...}}` when the mirror has never synced (an empty
             array would be a lie: every signed-in user sees builtins)
       response headers on every 200:
             X-Matrx-Catalog-Sync-Status  success | offline | error | skipped | never
             X-Matrx-Catalog-Synced-At    ISO-8601 of the last successful mirror
             X-Matrx-Catalog-Sync-Error   present ONLY when the last refresh failed
             X-Matrx-Catalog-Rows         row count

  POST /agents/catalog/rpc
       body: {"fn": "agx_get_list_full", "args": {}}  (args optional/empty)
       Same 200/503 responses. This is the door a structural Supabase-like
       `client.rpc(fn)` proxies to, so the shared picker package can read the
       offline mirror through the interface it already uses online. Any other
       `fn` is a 400 naming what is served — never a silent empty array.

  GET  /agents/catalog/status
       200 : {source, rows, mirror_user_id, sync: {status, last_synced_at,
              error_message}, stale, never_synced}

  GET  /agents/catalog/{agent_id}/execution
       200 : the row `public.agx_get_execution_full(p_agent_id)` returns,
             VERBATIM (id, variable_definitions, model_id, settings, tools,
             custom_tools, context_policies, auto_context_disabled, ui_gates)
             — the identical object Cloud Chat reads from Supabase.
             Headers: X-Matrx-Agent-Detail-Stale true|false
                      X-Matrx-Agent-Detail-Stale-Reason (only when stale)
       404 : nothing cached and nothing fetchable, or the database answered
             zero rows (this account cannot see that agent)
       503 : the RPC is unreachable and nothing is cached for this agent

AgentCatalogRow — the columns `public.agx_get_list_full()` returns. The list
below is the REQUIRED MINIMUM (verified against the live definition
2026-09-08), NOT an exact set: the RPC grows columns (the nullable
`orchestra jsonb` conductor badge is next), and every extra column is stored
and served here VERBATIM so one additive platform migration can never take an
installed desktop's catalog offline. A MISSING column is still a loud refusal.
  id uuid-string | agent_type string | name string | description string|null |
  model_id uuid-string|null | category string|null | tags string[] |
  is_active bool | is_archived bool | is_favorite bool | created_by uuid-string |
  organization_id uuid-string|null | task_id uuid-string|null |
  source_agent_id uuid-string|null | created_at ISO-8601 | updated_at ISO-8601 |
  is_owner bool | access_level "owner"|"system"|<permission level> |
  shared_by_email string|null

WRITES — deliberately absent. The catalog's one write is `is_favorite`, and it
goes to Supabase directly when online. There is NO offline write queue here:
a favourite that vanishes on reconnect is worse than a refusal, so offline the
caller must refuse loudly. See FEATURE.md § Favourites.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.common.background_tasks import fire_and_forget
from app.common.system_logger import get_logger
from app.services.agent_catalog.client import (
    CATALOG_RPC,
    EXECUTION_RPC,
    AgentCatalogError,
    fetch_agent_execution,
)

logger = get_logger()

router = APIRouter(prefix="/agents", tags=["agents"])


#: How long a cached `agx_get_execution_full` row is served without a refetch.
#: Short enough that editing an agent's variables in the browser shows up on
#: the desktop within a coffee break; long enough that opening the same agent
#: repeatedly is one RPC call, not one per open.
EXECUTION_CACHE_TTL_SECONDS = 600


def _is_stale(fetched_at: str) -> bool:
    """True when a cached execution row is older than the TTL (or unreadable)."""
    if not fetched_at:
        return True
    try:
        stamp = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    return age >= EXECUTION_CACHE_TTL_SECONDS


def _execution_response(
    row: dict[str, Any], *, stale: bool, reason: str | None
) -> JSONResponse:
    """The RPC row verbatim; staleness lives in headers, never in the payload."""
    headers = {"X-Matrx-Agent-Detail-Stale": "true" if stale else "false"}
    if stale and reason:
        headers["X-Matrx-Agent-Detail-Stale-Reason"] = str(reason)[:400]
    return JSONResponse(content=row, headers=headers)


class CatalogRpcRequest(BaseModel):
    fn: str = Field(..., description=f"Must be {CATALOG_RPC!r}")
    args: dict[str, Any] | None = None


async def _mirror_state() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from app.services.local_db.repositories import AgentsRepo, SyncMetaRepo

    repo = AgentsRepo()
    rows = await repo.list_catalog()
    meta = await SyncMetaRepo().get_last_sync("agents") or {}
    return rows, meta


def _kick_sync() -> None:
    from app.services.local_db.sync_engine import get_sync_engine

    fire_and_forget(get_sync_engine().sync_agents(), name="agent-catalog-sync")


async def _catalog_rows(response: Response) -> list[dict[str, Any]]:
    """The mirrored rows, or a loud 503 when there is no mirror to serve."""
    rows, meta = await _mirror_state()
    status = str(meta.get("status") or "never")
    last_synced_at = meta.get("last_synced_at")

    if not rows:
        _kick_sync()
        raise HTTPException(
            status_code=503,
            detail={
                "error": "agent_catalog_unavailable",
                "message": (
                    f"The local mirror of {CATALOG_RPC}() is empty. Every signed-in "
                    "user sees at least the active builtins, so an empty list would "
                    "be a lie — refusing instead."
                ),
                "sync_status": status,
                "last_synced_at": last_synced_at,
                "sync_error": meta.get("error_message"),
                "remedy": (
                    "Sign in to the desktop app (the catalog RPC is called as the "
                    "signed-in user) and retry; a refresh has been started."
                ),
                "rpc": CATALOG_RPC,
            },
        )

    response.headers["X-Matrx-Catalog-Sync-Status"] = status
    response.headers["X-Matrx-Catalog-Rows"] = str(len(rows))
    if last_synced_at:
        response.headers["X-Matrx-Catalog-Synced-At"] = str(last_synced_at)
    error_message = meta.get("error_message")
    if status != "success" and error_message:
        # The rows are real but the last refresh did not land: say so on every
        # response so a stale catalog can never look current.
        response.headers["X-Matrx-Catalog-Sync-Error"] = str(error_message)[:400]
    return rows


@router.get("/catalog")
async def get_agent_catalog(response: Response) -> list[dict[str, Any]]:
    """The RPC's rows, verbatim, from the offline mirror."""
    return await _catalog_rows(response)


@router.post("/catalog/rpc")
async def post_agent_catalog_rpc(
    body: CatalogRpcRequest, response: Response
) -> list[dict[str, Any]]:
    """RPC-shaped door for a structural Supabase-like client."""
    if body.fn != CATALOG_RPC:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_rpc",
                "message": (
                    f"This engine mirrors exactly one RPC. Asked for {body.fn!r}; "
                    f"only {CATALOG_RPC!r} is served."
                ),
                "supported": [CATALOG_RPC],
            },
        )
    if body.args:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_rpc_args",
                "message": (
                    f"{CATALOG_RPC}() takes no arguments; the mirror cannot honour "
                    f"{sorted(body.args)}. Filter and sort the returned rows."
                ),
            },
        )
    return await _catalog_rows(response)


@router.get("/catalog/{agent_id}/execution")
async def get_agent_execution(agent_id: str) -> JSONResponse:
    """ONE agent's execution detail, VERBATIM from the platform RPC.

    `variable_definitions` and `settings` are NOT catalog columns — no Matrx
    client's LIST rows carry them, online or off — so a variables form asks
    for ONE agent by id, exactly as Cloud Chat asks Supabase
    (`agx_get_execution_full(p_agent_id)`). This is that same read, through
    the same PostgREST lane as the catalog itself
    (`app/services/agent_catalog/client.py` — ONE Supabase REST path in this
    repo, never a second), with a fetch-through SQLite cache in front of it so
    it works offline and does not re-read on every keystroke.

    The row is served with the RPC's own keys and JSON types (id,
    variable_definitions, model_id, settings, tools, custom_tools,
    context_policies, auto_context_disabled, ui_gates) — ruling D4: offline is
    a data LOCATION, never a different structure.

    Refresh policy (docs/SYNC_CONTRACT.md, the fetch-through cache pattern
    `agent_execution_definitions` already uses): read the cache; refetch when
    it is missing or older than `EXECUTION_CACHE_TTL_SECONDS`; on a network or
    auth failure serve the cached row and SAY it is stale in a header. 468
    agents are never 468 RPC calls at startup — the catalog list is one call,
    and detail is one call for the one agent a person opened.

    404 when there is nothing cached and nothing fetchable: an empty payload
    is indistinguishable from "this agent takes no variables", and a form that
    silently drops a required question is the failure this refuses.
    """
    from app.services.local_db.repositories import (
        AgentExecutionDetailsRepo,
        TokenRepo,
    )

    repo = AgentExecutionDetailsRepo()
    cached = await repo.get(agent_id)
    if cached is not None and not _is_stale(cached[1]):
        return _execution_response(cached[0], stale=False, reason=None)

    token_repo = TokenRepo()
    token_row = await token_repo.get()
    jwt = ""
    if token_row and not token_repo.is_expired(token_row):
        jwt = token_row.get("access_token") or ""

    if not jwt:
        reason = "expired" if token_row else "missing"
        if cached is not None:
            return _execution_response(
                cached[0],
                stale=True,
                reason=f"the stored sign-in is {reason}, so this detail was not refreshed",
            )
        raise HTTPException(
            status_code=404,
            detail={
                "error": "agent_detail_unavailable",
                "message": (
                    f"No cached execution detail for agent {agent_id!r}, and "
                    f"{EXECUTION_RPC}() cannot be called: the stored sign-in is "
                    f"{reason}. Answering an empty variables form would read as "
                    "'this agent takes no variables'."
                ),
                "remedy": "Sign in to the desktop app and reopen the agent.",
                "rpc": EXECUTION_RPC,
            },
        )

    try:
        row = await fetch_agent_execution(jwt, agent_id)
    except AgentCatalogError as exc:
        if cached is not None:
            logger.warning(
                "[agent_catalog] %s refresh for %s failed (%s) — serving the "
                "cached row and flagging it stale.",
                EXECUTION_RPC,
                agent_id,
                exc,
            )
            return _execution_response(cached[0], stale=True, reason=str(exc))
        raise HTTPException(
            status_code=503,
            detail={
                "error": "agent_detail_unreachable",
                "message": (
                    f"{EXECUTION_RPC}({agent_id}) could not be read and nothing is "
                    f"cached for this agent: {exc}"
                ),
                "remedy": "Reconnect (or sign in again) and reopen the agent.",
                "rpc": EXECUTION_RPC,
            },
        ) from exc

    if row is None:
        # The database answered zero rows: this user cannot see this agent.
        # That is a real answer — drop any stale cache rather than keep
        # serving detail for an agent access to which has been removed.
        await repo.delete(agent_id)
        raise HTTPException(
            status_code=404,
            detail={
                "error": "agent_not_visible",
                "message": (
                    f"{EXECUTION_RPC}({agent_id}) returned no row — this account "
                    "cannot see that agent, or it has been deleted. Membership is "
                    "decided in the database, exactly as it is in the browser."
                ),
                "remedy": "Pick an agent from the catalog list, or ask its owner to share it.",
                "rpc": EXECUTION_RPC,
            },
        )

    await repo.upsert(agent_id, row)
    return _execution_response(row, stale=False, reason=None)


@router.get("/catalog/status")
async def get_agent_catalog_status() -> dict[str, Any]:
    """Freshness of the mirror — never mixed into the row array itself."""
    from app.services.local_db.repositories import AgentsRepo

    rows, meta = await _mirror_state()
    status = str(meta.get("status") or "never")
    never_synced = not meta or not meta.get("last_synced_at")
    if not rows and never_synced:
        _kick_sync()
    return {
        "source": "sqlite",
        "rpc": CATALOG_RPC,
        "rows": len(rows),
        "mirror_user_id": await AgentsRepo().mirror_user_id(),
        "never_synced": never_synced,
        "stale": status != "success",
        "sync": {
            "status": status,
            "last_synced_at": meta.get("last_synced_at"),
            "error_message": meta.get("error_message"),
        },
    }
