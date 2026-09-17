"""Cloud-reachable commands for the LOCAL Claude Code runtime.

These register in the SAME transport-agnostic command registry that serves
`POST /extension/rpc` and the per-user Supabase Broadcast bridge channel
(`matrx-local-bridge:<user_id>` → `cross_component_router` → `invoke_command`).
That Broadcast channel is the EXISTING cloud→local relay: the browser cannot
reach localhost, but it can publish an rpc envelope on the user's own bridge
channel with supabase-js, and this engine answers on the same channel. No new
service, no new database, no inbound port — the same path that already carries
the ~80-tool dispatcher carries these five commands.

Command surface (args → reply data):

- `coding_runtime.capabilities` {} → truthful availability + allowlist
- `coding_runtime.start` {workspace, prompt, resume_session_id?, model?,
  max_turns?, permission_mode?} → run status incl. conversation_id
- `coding_runtime.status` {runtime_id?} → one run / all runs
- `coding_runtime.cancel` {runtime_id} → {cancelled}
- `coding_runtime.resumable` {provider_session_id} → native-resume verdict

Folder APPROVAL is deliberately NOT exposed here: approving a workspace for
agent execution is a physical-presence decision made in the desktop app on
loopback, never remotely.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request

from app.api.extension_handlers import register
from app.services.coding_sessions.local_runtime import (
    LocalRuntimeStartRequest,
    get_local_claude_runtime,
)


@register("coding_runtime.capabilities")
async def coding_runtime_capabilities(
    args: Dict[str, Any], req: Optional[Request]
) -> Dict[str, Any]:
    return await get_local_claude_runtime().capabilities()


@register("coding_runtime.start")
async def coding_runtime_start(
    args: Dict[str, Any], req: Optional[Request]
) -> Dict[str, Any]:
    request = LocalRuntimeStartRequest.model_validate(args)
    return await get_local_claude_runtime().start(request)


@register("coding_runtime.status")
async def coding_runtime_status(
    args: Dict[str, Any], req: Optional[Request]
) -> Dict[str, Any]:
    runtime_id = args.get("runtime_id")
    return await get_local_claude_runtime().status(
        runtime_id if isinstance(runtime_id, str) and runtime_id else None
    )


@register("coding_runtime.cancel")
async def coding_runtime_cancel(
    args: Dict[str, Any], req: Optional[Request]
) -> Dict[str, Any]:
    runtime_id = args.get("runtime_id")
    if not isinstance(runtime_id, str) or not runtime_id:
        raise ValueError("runtime_id is required")
    return await get_local_claude_runtime().cancel(runtime_id)


@register("coding_runtime.resumable")
async def coding_runtime_resumable(
    args: Dict[str, Any], req: Optional[Request]
) -> Dict[str, Any]:
    provider_session_id = args.get("provider_session_id")
    if not isinstance(provider_session_id, str) or not provider_session_id:
        raise ValueError("provider_session_id is required")
    return await get_local_claude_runtime().resumable(provider_session_id)


# ---------------------------------------------------------------------------
# Per-conversation sync truth, reachable from the web.
#
# The browser cannot see this Mac's transcript, delivery ledger or local copy,
# so the web half of the sync panel can only ever show the cloud's side. These
# two commands let the /work conversation page ask the owning Mac for the whole
# truth and to repair it -- over the SAME bridge channel that already carries
# the runtime commands above. If the Mac is asleep, the caller's timeout is the
# honest answer ("not reachable"), never a claim that everything is in sync.


@register("coding_session.sync_truth")
async def coding_session_sync_truth(
    args: Dict[str, Any], req: Optional[Request]
) -> Dict[str, Any]:
    from app.services.coding_sessions.sync_truth_reader import sync_truth

    provider_session_id = args.get("provider_session_id")
    if not isinstance(provider_session_id, str) or not provider_session_id:
        raise ValueError("provider_session_id is required")
    result = await sync_truth(provider_session_id)
    if result is None:
        return {
            "found": False,
            "detail": (
                "That conversation is not on this Mac, and AI Matrx could not "
                "be asked about it from here."
            ),
        }
    return {"found": True, **result}


@register("coding_session.reconcile")
async def coding_session_reconcile(
    args: Dict[str, Any], req: Optional[Request]
) -> Dict[str, Any]:
    from app.services.coding_sessions.sync_truth_reader import reconcile

    provider_session_id = args.get("provider_session_id")
    if not isinstance(provider_session_id, str) or not provider_session_id:
        raise ValueError("provider_session_id is required")
    result = await reconcile(provider_session_id)
    if result is None:
        return {
            "found": False,
            "detail": (
                "That conversation is not on this Mac, so there is nothing "
                "here to reconcile."
            ),
        }
    return {"found": True, **result}
