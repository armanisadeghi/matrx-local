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
from app.services.coding_sessions.provider_readiness import get_provider_readiness
from app.services.coding_sessions.claude_history import (
    ClaudeHistoryConflict,
    ClaudeHistoryImporter,
    ClaudeHistoryImportRequest,
    ClaudeHistoryPrepareRequest,
)
from app.services.coding_sessions.title_sync import (
    ClaudeTitleSyncBlocked,
    get_claude_session_metadata_reconciler,
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


@router.get("/providers/readiness")
async def coding_session_provider_readiness() -> dict[str, object]:
    """Separate product, adapter, spool, local, and cloud readiness evidence."""
    outbox = get_coding_session_bridge_outbox()
    return await get_provider_readiness().status(await outbox.delivery_status())


@router.get("/delivery/envelopes")
async def coding_session_delivery_envelopes(
    request: Request,
    state: str = Query(pattern="^(pending|quarantine)$"),
    limit: int = Query(default=50, ge=1, le=200),
    after_receipt_id: int | None = Query(default=None, ge=0),
    provider: str | None = Query(default=None, min_length=1, max_length=64),
    action: str | None = Query(default=None, min_length=1, max_length=64),
    source: str | None = Query(default=None, min_length=1, max_length=128),
    enqueue_origin: str | None = Query(default=None, min_length=1, max_length=64),
) -> dict[str, object]:
    """Payload-free, paginated evidence behind every delivery count."""
    _require_loopback(request)
    return await get_coding_session_bridge_outbox().delivery_envelopes(
        queue_state=state,
        limit=limit,
        after_receipt_id=after_receipt_id,
        provider=provider,
        action=action,
        source=source,
        enqueue_origin=enqueue_origin,
    )


@router.post("/delivery/envelopes/{receipt_id}/retry")
async def retry_coding_session_delivery_envelope(
    request: Request, receipt_id: int
) -> dict[str, object]:
    """Make one exact waiting or preserved envelope eligible for delivery."""
    _require_loopback(request)
    try:
        return await get_coding_session_bridge_outbox().retry_delivery_envelope(
            receipt_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.delete("/delivery/envelopes/{receipt_id}")
async def discard_coding_session_delivery_envelope(
    request: Request,
    receipt_id: int,
    confirm: bool = Query(default=False),
) -> dict[str, object]:
    """Preview impact first; discard one exact local copy only when confirmed."""
    _require_loopback(request)
    try:
        return await get_coding_session_bridge_outbox().discard_delivery_envelope(
            receipt_id, confirmed=confirm
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


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


@router.post("/claude/history/review")
async def review_claude_history(
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, object]:
    """Create a durable inventory snapshot and return its exact delta."""
    try:
        return await ClaudeHistoryImporter().review(limit=limit)
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


@router.get("/claude/history/scans/{scan_id}")
async def list_claude_history_scan(
    scan_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    search: str | None = Query(default=None, max_length=200),
    change_type: list[str] = Query(default=[]),
    project: str | None = Query(default=None, max_length=200),
    branch: str | None = Query(default=None, max_length=200),
    archived: bool | None = Query(default=None),
    importable: bool | None = Query(default=None),
    include_missing: bool = Query(default=False),
    sort: str = Query(
        default="modified", pattern="^(modified|title|project|bytes|change)$"
    ),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> dict[str, object]:
    """Search, filter, sort, and page one immutable review snapshot."""
    allowed_changes = {
        "new",
        "content_changed",
        "metadata_changed",
        "missing",
        "unchanged",
    }
    if any(value not in allowed_changes for value in change_type):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported history change type",
        )
    try:
        return await ClaudeHistoryImporter().inventory_page(
            scan_id,
            cursor=cursor,
            limit=limit,
            search=search,
            change_types=tuple(change_type),
            project=project,
            branch=branch,
            archived=archived,
            importable=importable,
            include_missing=include_missing,
            sort=sort,
            direction=direction,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post("/claude/history/prepare")
async def prepare_claude_history_selection(
    body: ClaudeHistoryPrepareRequest,
) -> dict[str, object]:
    """Hash only selected rows and return content-bound import revisions."""
    try:
        return await ClaudeHistoryImporter().prepare_selected(body)
    except ClaudeHistoryConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
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
    return await get_claude_session_metadata_reconciler().status()


@router.post("/claude/labels/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_claude_labels(
    dry_run: bool = Query(default=False),
) -> dict[str, object]:
    """Match already-mirrored Claude sessions to Claude's own labels and sync.

    Only sessions AI Matrx already knows are touched; labels of local sessions
    the user never mirrored never leave this machine.
    """
    try:
        return await get_claude_session_metadata_reconciler().sync(dry_run=dry_run)
    except ClaudeTitleSyncBlocked as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.reason,
        ) from exc


@router.get("/claude/labels/operations/{operation_id}")
async def claude_label_operation(
    operation_id: str,
    limit: int = Query(default=200, ge=1, le=200),
    after_session_ref: str | None = Query(default=None, min_length=1, max_length=512),
) -> dict[str, object]:
    """Page the exact per-session evidence for one preview/apply/verify pass."""
    try:
        return await get_claude_session_metadata_reconciler().operation(
            operation_id, limit=limit, after_session_ref=after_session_ref
        )
    except ClaudeTitleSyncBlocked as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=exc.reason
        ) from exc


@router.post("/claude/labels/operations/{operation_id}/verify")
async def verify_claude_label_operation(operation_id: str) -> dict[str, object]:
    """Refetch both sides and record acknowledgement/convergence proof."""
    try:
        return await get_claude_session_metadata_reconciler().verify(operation_id)
    except ClaudeTitleSyncBlocked as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.reason
        ) from exc


@router.post("/claude/labels/push-intents/{intent_id}/retry")
async def retry_claude_label_push_intent(intent_id: str) -> dict[str, object]:
    """Retry one durable Claude-file write and its convergence observation."""
    try:
        return await get_claude_session_metadata_reconciler().retry_push_intent(
            intent_id
        )
    except ClaudeTitleSyncBlocked as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.reason
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
async def revoke_runtime_folder(
    request: Request, folder: str = Query()
) -> dict[str, object]:
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
async def runtime_status(
    runtime_id: str | None = Query(default=None),
) -> dict[str, object]:
    try:
        return get_local_claude_runtime().status(runtime_id)
    except LocalRuntimeRefused as exc:
        raise _refused(exc) from exc


@router.post("/runtime/{runtime_id}/cancel")
async def cancel_runtime_session(
    request: Request, runtime_id: str
) -> dict[str, object]:
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
async def runtime_events(
    runtime_id: str,
    after_sequence: int | None = Query(default=None, ge=0),
):
    """Cursor-aware SSE replay with an explicit gap event if history expired."""
    from fastapi.responses import StreamingResponse
    import json as _json

    runtime = get_local_claude_runtime()
    try:
        runtime.status(runtime_id)
    except LocalRuntimeRefused as exc:
        raise _refused(exc) from exc

    async def _stream():
        async for event in runtime.subscribe(runtime_id, after_sequence=after_sequence):
            event_id = event.get("sequence")
            prefix = f"id: {event_id}\n" if isinstance(event_id, int) else ""
            yield f"{prefix}data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        final = runtime.status(runtime_id)
        yield f"event: done\ndata: {_json.dumps(final, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
