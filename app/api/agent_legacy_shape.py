"""LEGACY projection of the agent catalog — one place, retiring soon.

`GET /chat/agents` and `GET /data/agents` predate the shared agent-picker
package and hand the desktop a bucketed `{builtins, user, shared}` payload
with `variable_defaults`/`settings` attached. Both now derive that payload
from the SAME 19-column `agx_get_list_full()` rows the mirror holds, so:

  * `shared` is REAL. It used to be a hardcoded `[]` — the desktop showed a
    field that could never be non-empty while the user genuinely had shared
    agents. That was a screen telling a lie, and it is fixed here.
  * the bucket order is the DATABASE's order (favourites first, then
    `updated_at DESC`), not a local re-sort by name.

`variable_defaults` and `settings` are NOT catalog columns — no Matrx client's
list rows carry them — so they come from the `prompt_builtins` detail cache
(`SyncEngine._refresh_agent_detail_cache`). A row with no cached detail gets
empty values, exactly as a freshly-shared agent looks in the web app until its
detail is fetched.

🚨 Do not grow this module. It exists only until the desktop reads
`GET /agents/catalog` through the shared picker package; then it is deleted
along with both endpoints. New consumers use the catalog endpoint.
"""

from __future__ import annotations

from typing import Any

_SYSTEM = "system"
_OWNER = "owner"


def bucket_of(row: dict[str, Any]) -> str:
    """`builtin` | `user` | `shared` for one catalog row.

    `access_level` is the database's own answer: `'system'` for active
    builtins, `'owner'` for the caller's own agents, and the granted
    permission level (`view`, `edit`, ...) for anything shared directly or
    through an organization.
    """
    access_level = row.get("access_level")
    if access_level == _SYSTEM:
        return "builtin"
    if row.get("is_owner") or access_level == _OWNER:
        return "user"
    return "shared"


def project(row: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    """One catalog row in the legacy `AgentInfo` shape."""
    settings = (detail or {}).get("settings") or {}
    return {
        "id": row.get("id", ""),
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "source": bucket_of(row),
        "variable_defaults": (detail or {}).get("variable_defaults") or [],
        "category": row.get("category"),
        "tags": row.get("tags") or [],
        "is_favorite": bool(row.get("is_favorite")),
        "is_owner": bool(row.get("is_owner")),
        "access_level": row.get("access_level"),
        "shared_by_email": row.get("shared_by_email"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "settings": {
            "model_id": settings.get("model_id"),
            "temperature": settings.get("temperature"),
            "max_tokens": settings.get("max_tokens") or settings.get("max_output_tokens"),
            "stream": settings.get("stream", True),
            "tools": settings.get("tools") or [],
        },
    }


async def build_legacy_payload(source: str) -> dict[str, Any]:
    """The full `{builtins, user, shared, totals}` payload from the mirror."""
    from app.services.local_db.repositories import AgentsRepo, PromptBuiltinsRepo

    rows = await AgentsRepo().list_catalog()
    details = {d["id"]: d for d in await PromptBuiltinsRepo().list_all() if d.get("id")}

    buckets: dict[str, list[dict[str, Any]]] = {"builtin": [], "user": [], "shared": []}
    for row in rows:
        buckets[bucket_of(row)].append(project(row, details.get(row.get("id", ""))))

    builtins, user, shared = buckets["builtin"], buckets["user"], buckets["shared"]
    return {
        "builtins": builtins,
        "user": user,
        "shared": shared,
        "source": source,
        "totals": {
            "builtins": len(builtins),
            "user": len(user),
            "shared": len(shared),
            "total": len(builtins) + len(user) + len(shared),
        },
    }
