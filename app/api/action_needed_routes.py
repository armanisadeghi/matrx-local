from fastapi import APIRouter

from app.services.action_needed.registry import get_action_needed_registry

router = APIRouter(prefix="/actions/needed", tags=["action-needed"])


@router.get("")
async def action_needed_snapshots() -> dict[str, object]:
    """Reconnect snapshot, grouped by the source that owns resolution."""
    return {"snapshots": await get_action_needed_registry().snapshots()}
