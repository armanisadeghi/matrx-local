"""Complete, owner-scoped coding-session identity inventory from AI Dream.

Reconciliation is an allowlist operation: a partial cloud inventory is not a
smaller truth, it is unsafe input. The walk itself (keyset cursor, total_count
proof, fail-closed reasons) lives in ``matrx_coding_history.identity``, shared
with Matrx 2; this module keeps Matrx Local's call shape over its AIDreamClient.
"""

from __future__ import annotations

from typing import Any

from matrx_coding_history.identity import (
    IDENTITY_PAGE_SIZE,
    IDENTITY_PATH,
    IDENTITY_SCHEMA_VERSION,
    IdentityInventoryBlocked,
)
from matrx_coding_history.identity import (
    fetch_complete_identity_inventory as _fetch_complete_identity_inventory,
)

from app.services.aidream.client import AIDreamClient
from app.services.coding_sessions.history_ports import AIDreamCloudHttp


async def fetch_complete_identity_inventory(
    *,
    client: AIDreamClient,
    jwt: str,
    provider: str,
) -> list[dict[str, Any]]:
    """Fetch every identity in one server-fenced snapshot or fail closed."""
    return await _fetch_complete_identity_inventory(
        http=AIDreamCloudHttp(client, jwt), provider=provider
    )


__all__ = [
    "IDENTITY_PAGE_SIZE",
    "IDENTITY_PATH",
    "IDENTITY_SCHEMA_VERSION",
    "IdentityInventoryBlocked",
    "fetch_complete_identity_inventory",
]
