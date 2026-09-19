"""Direct-loopback selected-organization fence for local-browser transport."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.api.remote_auth import headers_indicate_tunnel, verify_supabase_token
from app.services.aidream.organization import _active_memberships
from app.services.sync_client import get_sync_client

router = APIRouter(prefix="/local-browser", tags=["local-browser"])
_REFUSED = "local_browser_context_refused"
_CONFLICT = "local_browser_context_conflict"


class ContextBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    engine_boot_id: str
    expected_revision: int
    organization_id: str | None


@dataclass
class ContextState:
    boot_id: str
    revision: int = 0
    organization_id: str | None = None
    owner: tuple[str, str] | None = None
    lock: asyncio.Lock | None = None

    def __post_init__(self) -> None:
        self.lock = asyncio.Lock()


def _claim(token: str) -> tuple[str, str]:
    """Read required claims after the bearer has been issuer-verified."""
    try:
        part = token.split(".")[1]
        padded = part + "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        sub, session_id = data["sub"], data["session_id"]
        canonical_session_id = str(uuid.UUID(session_id))
        if not isinstance(sub, str) or canonical_session_id != session_id:
            raise ValueError
        return sub, session_id
    except Exception as exc:
        raise HTTPException(401, _REFUSED) from exc


def _state(request: Request) -> ContextState:
    state = getattr(request.app.state, "local_browser_context", None)
    if state is None:
        state = ContextState(str(uuid.uuid4()))
        request.app.state.local_browser_context = state
    return state


async def _owner(request: Request) -> tuple[str, str, str]:
    """Return the exact current daemon owner session and its ephemeral JWT."""
    try:
        direct_loopback = (
            not headers_indicate_tunnel(request.headers)
            and request.client is not None
            and ipaddress.ip_address(request.client.host).is_loopback
        )
    except ValueError:
        direct_loopback = False
    if not direct_loopback:
        raise HTTPException(403, _REFUSED)

    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, _REFUSED)
    desktop_jwt = authorization[7:].strip()
    desktop = await verify_supabase_token(desktop_jwt)
    grant = await get_sync_client().access_grant()
    if desktop is None or grant is None:
        raise HTTPException(401, _REFUSED)

    daemon_jwt, daemon_user_id = grant
    user_id, session_id = _claim(daemon_jwt)
    inbound_user_id, inbound_session_id = _claim(desktop_jwt)
    if (
        desktop.user_id != user_id
        or daemon_user_id != user_id
        or inbound_user_id != user_id
        or inbound_session_id != session_id
    ):
        raise HTTPException(401, _REFUSED)
    return user_id, session_id, daemon_jwt


def _fence_for_owner(state: ContextState, owner: tuple[str, str]) -> None:
    """Clear the context whenever either daemon actor or daemon session changes."""
    if state.owner != owner:
        state.owner = owner
        state.organization_id = None
        state.revision += 1


@router.get("/context")
async def get_context(request: Request) -> dict[str, str | int | None]:
    user_id, session_id, _jwt = await _owner(request)
    state = _state(request)
    assert state.lock is not None
    async with state.lock:
        _fence_for_owner(state, (user_id, session_id))
        return {
            "engine_boot_id": state.boot_id,
            "revision": state.revision,
            "organization_id": state.organization_id,
        }


@router.post("/context")
async def set_context(body: ContextBody, request: Request) -> dict[str, str | int | None]:
    user_id, session_id, daemon_jwt = await _owner(request)
    state = _state(request)
    if body.organization_id is not None:
        try:
            uuid.UUID(body.organization_id)
        except ValueError as exc:
            raise HTTPException(400, _REFUSED) from exc
        try:
            memberships = await _active_memberships(daemon_jwt)
            member_ids = {str(row.get("container_id")) for row in memberships}
        except Exception as exc:
            raise HTTPException(403, _REFUSED) from exc
        if body.organization_id not in member_ids:
            raise HTTPException(403, _REFUSED)

    # Membership is an awaited remote read. Re-read the atomic daemon grant
    # under the CAS lock so a same-user re-login cannot install an old result.
    assert state.lock is not None
    async with state.lock:
        current_user_id, current_session_id, current_daemon_jwt = await _owner(request)
        _fence_for_owner(state, (current_user_id, current_session_id))
        if (current_user_id, current_session_id, current_daemon_jwt) != (
            user_id,
            session_id,
            daemon_jwt,
        ):
            raise HTTPException(409, _CONFLICT)
        if (
            body.engine_boot_id != state.boot_id
            or body.expected_revision != state.revision
        ):
            raise HTTPException(409, _CONFLICT)
        state.organization_id = body.organization_id
        state.revision += 1
        return {
            "engine_boot_id": state.boot_id,
            "revision": state.revision,
            "organization_id": state.organization_id,
        }
