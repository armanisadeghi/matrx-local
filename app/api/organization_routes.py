"""This Mac's SET organization — the value every background job acts under.

The desktop owns the picker; the engine owns the durable answer. When the
user sets an organization in Matrx Local, the desktop PUTs it here, and every
sidecar caller (file sync, the scraper, the vault, delegation, coding-session
artifacts) reads it back through
``app.services.aidream.organization.resolve_active_organization_id``.

Why this route has to exist at all: the desktop's own selection lives in the
browser's ``localStorage``, which a background Python service cannot read. The
alternative — the sidecar reading the user's saved default-organization
preference out of their account — is exactly what Arman abolished on
2026-09-19. There is no "default organization"; there is only what the user
SET, and this is how the set value crosses the process boundary.

Membership is verified here before anything is stored: this Mac never records
an organization the signed-in user cannot actually act in.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.services.aidream.organization import (
    _active_memberships,
    clear_device_organization,
    get_device_organization,
    jwt_user_id,
    set_device_organization,
)

router = APIRouter(prefix="/organization", tags=["organization"])
logger = logging.getLogger(__name__)


class ActiveOrganization(BaseModel):
    organization_id: str | None = None


class SetActiveOrganization(BaseModel):
    organization_id: str = Field(min_length=1)


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Sign in to Matrx Local before choosing an organization.",
        )
    return authorization.split(" ", 1)[1].strip()


@router.get("/active", response_model=ActiveOrganization)
async def read_active_organization(
    authorization: str | None = Header(default=None),
) -> ActiveOrganization:
    """What this Mac is set to, for the signed-in user. Never a guess."""
    user_id = jwt_user_id(_bearer(authorization))
    return ActiveOrganization(organization_id=await get_device_organization(user_id))


@router.put("/active", response_model=ActiveOrganization)
async def write_active_organization(
    req: SetActiveOrganization,
    authorization: str | None = Header(default=None),
) -> ActiveOrganization:
    """Record what the user just SET in the desktop picker."""
    jwt_value = _bearer(authorization)
    user_id = jwt_user_id(jwt_value)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="That sign-in could not be read. Sign in to Matrx Local again.",
        )
    try:
        memberships = await _active_memberships(jwt_value)
    except Exception as exc:  # noqa: BLE001 — a renderable state, not a stack
        raise HTTPException(
            status_code=503,
            detail=(
                f"Could not check your organization membership ({exc}). "
                "Check your connection and try again."
            ),
        ) from exc
    if req.organization_id not in {str(row["container_id"]) for row in memberships}:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of that organization.",
        )
    await set_device_organization(req.organization_id, user_id=user_id)
    await _mirror_to_coding_session_filing(req.organization_id)
    return ActiveOrganization(organization_id=req.organization_id)


async def _mirror_to_coding_session_filing(organization_id: str) -> None:
    """THE ONE CHOICE answers the server's coding-session question too.

    AI Matrx files this account's Claude Code / Codex sessions in the
    organization set on the coding-session connection (aidream
    ``/coding-sessions/connection/organization``), because that transport
    carries no organization on the wire. Until 2026-09-21 that was a SECOND
    question with its own card — a person who had just chosen an organization
    in the top bar was asked again, in different words, by the coding-session
    lane. There is one organization on this Mac: the one the user set. So the
    set value is written through the server's door here (membership-verified
    there), and the coding-session hold, if any, lifts by itself.

    Best-effort on purpose: the desktop's choice is already recorded and is
    correct whether or not the server could be reached right now. A failure is
    logged, and the publisher answers the server's hold from the device value
    the next time it meets one (``_answer_server_hold_from_device``).
    """
    try:
        from app.services.coding_sessions.service import (
            get_coding_session_bridge_outbox,
        )

        await get_coding_session_bridge_outbox().set_connection_organization(
            organization_id
        )
    except Exception as exc:  # noqa: BLE001 — logged, never the caller's failure
        logger.warning(
            "[organization] the coding-session filing organization could not be "
            "set to this Mac's choice yet (%s); the publisher will answer the "
            "server's hold from it when delivery next runs",
            exc,
        )


@router.delete("/active", response_model=ActiveOrganization)
async def forget_active_organization(
    authorization: str | None = Header(default=None),
) -> ActiveOrganization:
    """Forget this Mac's pick — sign-out, or an account switch.

    Bearer-scoped exactly like GET/PUT above: this route used to take no
    bearer at all, so any local caller could clear whichever user's choice
    happened to be stored, on a machine where more than one account can be
    signed in. ``clear_device_organization`` only clears the stored pick when
    it belongs to the authenticated caller.
    """
    jwt_value = _bearer(authorization)
    user_id = jwt_user_id(jwt_value)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="That sign-in could not be read. Sign in to Matrx Local again.",
        )
    await clear_device_organization(user_id=user_id)
    return ActiveOrganization(organization_id=None)
