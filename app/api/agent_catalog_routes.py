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

AgentCatalogRow (the 19 columns of `public.agx_get_list_full()`, verified
against the live definition 2026-09-08):
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

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.common.background_tasks import fire_and_forget
from app.common.system_logger import get_logger
from app.services.agent_catalog.client import CATALOG_RPC

logger = get_logger()

router = APIRouter(prefix="/agents", tags=["agents"])


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
