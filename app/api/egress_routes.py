"""Home Connection (residential egress) routes.

Three routes and nothing else: read the honest status, turn it on, turn it off.
The switch writes the ``residential_egress_enabled`` setting and then runs the
ONE reconciler — the routes never start or stop the child directly, so the
engine has exactly one decision point for "signed in AND enabled".

Contract: ``common-docs/systems/platform/residential-egress/FEATURE.md``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.residential_egress.supervisor import (
    egress_enabled,
    get_egress_supervisor,
    reconcile_residential_egress,
)

router = APIRouter(prefix="/egress", tags=["egress"])


class EgressStatus(BaseModel):
    """The helper's own status JSON plus what only the engine knows.

    ``state`` is one of the helper's states (``connected``, ``connecting``,
    ``paused``, ``signed_out``, ``error``) or one of the engine's own
    (``not_installed``, ``disabled``, ``stopped``). It is never absent and
    never a placeholder.
    """

    state: str
    installed: bool
    enabled: bool
    running: bool
    remedy: str | None = None
    last_error: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    server: str | None = None
    since: str | None = None
    helper_version: str | None = None
    streams_active: int = 0
    streams_total: int = 0
    bytes_relayed: int = 0
    uptime_seconds: float = 0.0


def _status() -> dict[str, Any]:
    return get_egress_supervisor().get_status(enabled=egress_enabled())


@router.get("/status", response_model=EgressStatus)
async def egress_status() -> dict[str, Any]:
    """What the home connection is doing right now, in plain terms."""
    return _status()


async def _set_enabled(value: bool) -> dict[str, Any]:
    from app.services.cloud_sync.settings_sync import get_settings_sync

    get_settings_sync().set("residential_egress_enabled", value)
    await reconcile_residential_egress()
    return _status()


@router.post("/enable", response_model=EgressStatus)
async def egress_enable() -> dict[str, Any]:
    """Lend this computer's connection when AI Matrx gets blocked."""
    return await _set_enabled(True)


@router.post("/disable", response_model=EgressStatus)
async def egress_disable() -> dict[str, Any]:
    """Stop lending this computer's connection, and stop the helper now."""
    return await _set_enabled(False)
