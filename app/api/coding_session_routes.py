"""Direct-loopback ingress for provider command-hook observations."""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, HTTPException, Request, status

from app.api.remote_auth import headers_indicate_tunnel
from app.services.coding_sessions import (
    BridgeMutationConflict,
    get_coding_session_bridge_outbox,
)
from app.services.coding_sessions.models import (
    BridgeAction,
    BridgeRequest,
    LocalBridgeReceipt,
)

router = APIRouter(prefix="/coding-session", tags=["coding-session-bridge"])


def _is_loopback_host(host: str | None) -> bool:
    """Accept exact IPv4/IPv6 loopback peers, never hostnames or LAN IPs."""
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


@router.post(
    "/hooks",
    response_model=LocalBridgeReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def persist_coding_session_hook(
    request: Request,
    envelope: BridgeRequest,
) -> LocalBridgeReceipt:
    """Persist one hook envelope locally before acknowledging its caller.

    Command hooks cannot safely carry the desktop user's Supabase JWT. The
    loopback socket is therefore the boundary; Cloudflare tunnel traffic is
    always rejected here even when it has an otherwise-valid user token.
    """

    peer_host = request.client.host if request.client else None
    if headers_indicate_tunnel(request.headers) or not _is_loopback_host(peer_host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Coding-session hook ingress is available on direct loopback only",
        )
    if envelope.action is not BridgeAction.OBSERVE_HOOK:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Local hook ingress accepts action=observe_hook only",
        )
    try:
        return await get_coding_session_bridge_outbox().enqueue(envelope)
    except BridgeMutationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
