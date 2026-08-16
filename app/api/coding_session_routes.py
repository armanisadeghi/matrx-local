"""Direct-loopback ingress for provider command-hook observations."""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, HTTPException, Query, Request, status

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
from app.services.coding_sessions.claude_history import (
    ClaudeHistoryConflict,
    ClaudeHistoryImporter,
    ClaudeHistoryImportRequest,
)
from app.services.coding_sessions.title_sync import (
    ClaudeSessionMetadataReconciler,
    ClaudeTitleSyncBlocked,
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


@router.get("/claude/history/preview")
async def preview_claude_history(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    """Inspect local Claude history only after an explicit user request."""
    try:
        return await ClaudeHistoryImporter().preview(limit=limit)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post("/claude/history/import", status_code=status.HTTP_202_ACCEPTED)
async def import_claude_history(
    body: ClaudeHistoryImportRequest,
) -> dict[str, object]:
    """Reconcile selected transcript revisions into the durable bridge outbox."""
    try:
        return await ClaudeHistoryImporter().import_selected(body)
    except ClaudeHistoryConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/claude/history/status")
async def claude_history_status() -> dict[str, object]:
    return await ClaudeHistoryImporter().status()


@router.delete("/claude/history/pending")
async def discard_pending_claude_history() -> dict[str, object]:
    """Discard queued history copies; source files and hook observations are untouched."""
    return await ClaudeHistoryImporter().discard_pending()


@router.get("/claude/labels/status")
async def claude_label_status() -> dict[str, object]:
    """Report whether Claude's own session index is readable and last synced."""
    return await ClaudeSessionMetadataReconciler().status()


@router.post("/claude/labels/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_claude_labels(
    dry_run: bool = Query(default=False),
) -> dict[str, object]:
    """Match already-mirrored Claude sessions to Claude's own labels and sync.

    Only sessions AI Matrx already knows are touched; labels of local sessions
    the user never mirrored never leave this machine.
    """
    try:
        return await ClaudeSessionMetadataReconciler().sync(dry_run=dry_run)
    except ClaudeTitleSyncBlocked as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.reason,
        ) from exc


@router.post("/claude/history/pending/retry")
async def retry_pending_claude_history() -> dict[str, object]:
    """Retry queued history copies explicitly after the user repairs the cause."""
    return await ClaudeHistoryImporter().retry_pending()
