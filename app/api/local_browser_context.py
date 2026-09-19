"""Direct-loopback selected-organization transport for the local browser."""
from __future__ import annotations

import ipaddress
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from app.api.remote_auth import headers_indicate_tunnel, verify_supabase_token
from app.services.aidream.organization import _active_memberships
from app.services.local_browser_context import BrowserContext, get_local_browser_context, grant_claims
from app.services.sync_client import get_sync_client

router = APIRouter(prefix="/local-browser", tags=["local-browser"])
_REFUSED = "local_browser_context_refused"
_CONFLICT = "local_browser_context_conflict"
_claim = grant_claims
_NO_STORE = {"Cache-Control": "no-store"}


def _refuse(status_code: int, code: str = _REFUSED) -> HTTPException:
    return HTTPException(status_code, code, headers=_NO_STORE)


class ContextBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    engine_boot_id: str
    expected_revision: int
    organization_id: str | None


def _response(context: BrowserContext) -> dict[str, str | int | None]:
    return {"engine_boot_id": context.engine_boot_id, "revision": context.revision, "organization_id": context.organization_id}


async def _trusted_daemon_context() -> tuple[tuple[str, str, str], BrowserContext]:
    """Retire from a fresh daemon grant before inspecting any caller credential."""
    grant = await get_sync_client().access_grant()
    fresh = await get_local_browser_context().fresh_for_daemon_grant(grant)
    if fresh is None or grant is None:
        raise _refuse(401)
    context, owner = fresh
    return (owner[0], owner[1], grant[0]), context


async def _owner(request: Request) -> tuple[str, str, str, BrowserContext]:
    try:
        direct_loopback = not headers_indicate_tunnel(request.headers) and request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        direct_loopback = False
    if not direct_loopback:
        raise _refuse(403)

    # A forged caller cannot clear valid context: retirement derives only from
    # the daemon grant, before untrusted bearer validation.
    (user_id, session_id, daemon_jwt), context = await _trusted_daemon_context()
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise _refuse(401)
    desktop_jwt = authorization[7:].strip()
    desktop = await verify_supabase_token(desktop_jwt)
    try:
        inbound_user_id, inbound_session_id = grant_claims(desktop_jwt)
    except ValueError as exc:
        raise _refuse(401) from exc
    if desktop is None or desktop.user_id != user_id or inbound_user_id != user_id or inbound_session_id != session_id:
        raise _refuse(401)
    return user_id, session_id, daemon_jwt, context


@router.get("/context")
async def get_context(request: Request, response: Response) -> dict[str, str | int | None]:
    _user_id, _session_id, _jwt, context = await _owner(request)
    response.headers.update(_NO_STORE)
    return _response(context)


@router.post("/context")
async def set_context(body: ContextBody, request: Request, response: Response) -> dict[str, str | int | None]:
    user_id, session_id, daemon_jwt, _context = await _owner(request)
    if body.organization_id is not None:
        try:
            uuid.UUID(body.organization_id)
        except ValueError as exc:
            raise _refuse(400) from exc
        try:
            memberships = await _active_memberships(daemon_jwt)
            member_ids = {str(row.get("container_id")) for row in memberships}
        except Exception as exc:
            raise _refuse(403) from exc
        if body.organization_id not in member_ids:
            raise _refuse(403)

    # Membership awaits. Re-read and retire from the current daemon grant
    # before the engine-owned CAS so an old request cannot restore state.
    current_user_id, current_session_id, current_jwt, _current = await _owner(request)
    if (current_user_id, current_session_id, current_jwt) != (user_id, session_id, daemon_jwt):
        raise _refuse(409, _CONFLICT)
    result = await get_local_browser_context().compare_and_set(
        owner=(user_id, session_id), engine_boot_id=body.engine_boot_id,
        expected_revision=body.expected_revision, organization_id=body.organization_id,
    )
    if result is None:
        raise _refuse(409, _CONFLICT)
    response.headers.update(_NO_STORE)
    return _response(result)
