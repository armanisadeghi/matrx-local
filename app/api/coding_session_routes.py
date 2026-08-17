"""Direct-loopback ingress for provider command-hook observations."""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.remote_auth import headers_indicate_tunnel
from app.services.coding_sessions import (
    BridgeMutationConflict,
    CaptureReconcileBlocked,
    get_claude_capture_reconciler,
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
from app.services.coding_sessions.local_runtime import (
    LocalRuntimeFolderRequest,
    LocalRuntimeRefused,
    LocalRuntimeStartRequest,
    LocalRuntimeWorkspaceRootsResponse,
    get_local_claude_runtime,
)
from app.services.coding_sessions.workspace_discovery import WorkspaceDiscoveryResponse

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


@router.get("/status")
async def coding_session_delivery_status() -> dict[str, object]:
    """Provider-neutral local capture and cloud-delivery truth.

    Counts and acknowledgement summaries are safe aggregates. No command,
    transcript, provider session id, project path, or raw server error is
    returned from this route.
    """
    return await get_coding_session_bridge_outbox().delivery_status()


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


@router.get("/claude/capture/status")
async def claude_capture_status() -> dict[str, object]:
    """Report the capture reconciler: enabled, running, and recent backfills."""
    return await get_claude_capture_reconciler().status()


@router.post("/claude/capture/reconcile", status_code=status.HTTP_202_ACCEPTED)
async def reconcile_claude_capture(
    dry_run: bool = Query(default=False),
) -> dict[str, object]:
    """Run one capture-reconcile pass now instead of waiting for the timer.

    Backfills only sessions from AFTER the owner's first binding — history from
    before they ever mirrored anything stays behind the explicit
    `Review local history` door. `dry_run` reports the diff and enqueues nothing.
    """
    try:
        return await get_claude_capture_reconciler().reconcile(dry_run=dry_run)
    except CaptureReconcileBlocked as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.reason,
        ) from exc


# ---------------------------------------------------------------------------
# LOCAL Claude Code runtime — start/stream/cancel/resume on this machine.
# The desktop UI reaches these over loopback; the browser reaches the same
# operations through the Supabase Broadcast bridge channel (see
# app/api/coding_runtime_handlers.py). Mutating routes are loopback-only:
# launching an agent with shell access on this machine over the tunnel
# without an auth story would be wrong, and the Broadcast channel is the
# sanctioned remote door.
# ---------------------------------------------------------------------------


def _require_loopback(request: Request) -> None:
    peer_host = request.client.host if request.client else None
    if headers_indicate_tunnel(request.headers) or not _is_loopback_host(peer_host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This runtime route is available on direct loopback only",
        )


def _refused(exc: LocalRuntimeRefused) -> HTTPException:
    status_code = (
        status.HTTP_404_NOT_FOUND
        if exc.code == "unknown_runtime"
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(
        status_code=status_code, detail={"code": exc.code, "detail": exc.detail}
    )


@router.get("/runtime/capabilities")
async def runtime_capabilities() -> dict[str, object]:
    """Truthful local-runtime availability, allowlist, and active runs."""
    return await get_local_claude_runtime().capabilities()


@router.get("/runtime/approvals")
async def runtime_approvals() -> dict[str, object]:
    return get_local_claude_runtime().list_approved()


@router.post("/runtime/approvals")
async def approve_runtime_folder(
    request: Request, body: LocalRuntimeFolderRequest
) -> dict[str, object]:
    """One-click persisted approval of a workspace folder — loopback only."""
    _require_loopback(request)
    try:
        return get_local_claude_runtime().approve_folder(body.folder)
    except LocalRuntimeRefused as exc:
        raise _refused(exc) from exc


@router.delete("/runtime/approvals")
async def revoke_runtime_folder(request: Request, folder: str = Query()) -> dict[str, object]:
    _require_loopback(request)
    return get_local_claude_runtime().revoke_folder(folder)


@router.post(
    "/runtime/workspaces/roots",
    response_model=LocalRuntimeWorkspaceRootsResponse,
)
async def add_runtime_workspace_root(
    request: Request, body: LocalRuntimeFolderRequest
) -> LocalRuntimeWorkspaceRootsResponse:
    """Persist a parent explicitly selected in the native folder picker."""
    _require_loopback(request)
    try:
        return get_local_claude_runtime().add_workspace_root(body.folder)
    except LocalRuntimeRefused as exc:
        raise _refused(exc) from exc


@router.delete(
    "/runtime/workspaces/roots",
    response_model=LocalRuntimeWorkspaceRootsResponse,
)
async def remove_runtime_workspace_root(
    request: Request,
    folder: str = Query(min_length=1, max_length=4096),
) -> LocalRuntimeWorkspaceRootsResponse:
    """Remove one exact root and report approvals that lose root authority."""
    _require_loopback(request)
    return get_local_claude_runtime().remove_workspace_root(folder)


@router.get(
    "/runtime/workspaces/discovery",
    response_model=WorkspaceDiscoveryResponse,
)
async def discover_runtime_workspaces(
    request: Request,
    parent: str | None = Query(default=None, min_length=1, max_length=4096),
) -> WorkspaceDiscoveryResponse:
    """Build a bounded local-only directory/project tree for folder approval."""
    _require_loopback(request)
    try:
        return await get_local_claude_runtime().discover_workspaces(parent)
    except LocalRuntimeRefused as exc:
        raise _refused(exc) from exc


@router.post("/runtime/start", status_code=status.HTTP_202_ACCEPTED)
async def start_runtime_session(
    request: Request, body: LocalRuntimeStartRequest
) -> dict[str, object]:
    """Start (or natively resume) a Claude Code session on this machine."""
    _require_loopback(request)
    try:
        return await get_local_claude_runtime().start(body)
    except LocalRuntimeRefused as exc:
        raise _refused(exc) from exc


@router.get("/runtime/status")
async def runtime_status(runtime_id: str | None = Query(default=None)) -> dict[str, object]:
    try:
        return get_local_claude_runtime().status(runtime_id)
    except LocalRuntimeRefused as exc:
        raise _refused(exc) from exc


@router.post("/runtime/{runtime_id}/cancel")
async def cancel_runtime_session(request: Request, runtime_id: str) -> dict[str, object]:
    _require_loopback(request)
    try:
        return await get_local_claude_runtime().cancel(runtime_id)
    except LocalRuntimeRefused as exc:
        raise _refused(exc) from exc


@router.get("/runtime/resumable")
async def runtime_resumable(provider_session_id: str = Query()) -> dict[str, object]:
    """Native-resume verdict from Claude's OWN local store — never a guess."""
    return await get_local_claude_runtime().resumable(provider_session_id)


@router.get("/runtime/{runtime_id}/events")
async def runtime_events(runtime_id: str):
    """SSE stream: buffered replay, then live SDK events until the run ends."""
    from fastapi.responses import StreamingResponse
    import json as _json

    runtime = get_local_claude_runtime()
    try:
        runtime.status(runtime_id)
    except LocalRuntimeRefused as exc:
        raise _refused(exc) from exc

    async def _stream():
        async for event in runtime.subscribe(runtime_id):
            yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
