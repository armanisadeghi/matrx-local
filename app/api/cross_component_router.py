"""
Cross-component broadcast inbound router.

Receives v2 envelopes from `_on_broadcast` (extension_broadcast.py) and
routes by `kind`:

- "rpc"      → dispatched into the SAME command registry that serves
               `POST /extension/rpc` (`extension_handlers.HANDLERS`, via
               the transport-agnostic `invoke_command`). The result is
               published back on the same per-user bridge channel as a
               reply envelope: `action: "<action>.result"`, same
               `requestId`, `direction: "local->extension"`.
- "wake"     → fetch the sch_task row named in payload.taskId, attempt
               to claim it, run it; not yet implemented (the
               matrx-scheduler[host] integration is Phase 3c).
- "presence" → log debug-level; presence handling is future work.

One command surface, two transports: HTTP `/extension/rpc` and Supabase
Broadcast rpc envelopes both resolve against `HANDLERS` — commands can
never drift apart between transports. The Broadcast path exists for the
cross-machine fallback where neither loopback nor the Cloudflare tunnel
is reachable.

Failure posture: broadcast subscribers must never crash on malformed
inbound traffic, but every drop is LOUD — malformed envelopes, missing
user context, and unpublishable replies all log at warning/error level.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from app.api.cross_component_envelope import (
    CrossComponentEnvelope,
    parse_envelope,
)
from app.common.system_logger import get_logger

logger = get_logger()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _local_instance_id() -> str:
    """This engine's stable instance id (same identity the cloud row uses)."""
    try:
        from app.services.cloud_sync.instance_manager import get_instance_manager

        return get_instance_manager().instance_id
    except Exception:  # pragma: no cover — identity must never break routing
        logger.warning("[cross-component] could not resolve local instance_id", exc_info=True)
        return "unknown"


def route_envelope(raw: Dict[str, Any], user_id: Optional[str] = None) -> None:
    """Entry point called from `_on_broadcast`. Parses the raw payload
    into a v2 envelope and dispatches by `kind`.

    `user_id` identifies the per-user bridge channel the envelope arrived
    on — it is required to publish an rpc reply. `_on_broadcast` always
    passes it; the None default exists only so wake/presence handling
    (which never replies) keeps working if a future caller lacks it.

    Never raises — broadcast subscribers must not crash on malformed
    inbound traffic. Parse failures are logged at warning level with
    the offending payload for debugging.
    """
    try:
        envelope = parse_envelope(raw)
    except Exception as exc:
        logger.warning(
            "[cross-component] dropped malformed envelope (%s): %r",
            exc,
            raw,
        )
        return

    # Filter: ignore envelopes that originate from this component.
    # The bus is per-user, so an "extension->extension" or
    # "local->local" envelope is either an echo loop or a misdirected
    # message — drop it. This also drops our OWN rpc replies echoed back
    # by Supabase Broadcast (self-receive), preventing dispatch loops.
    if envelope.fromInstance.component == "local":
        return

    # Filter: ignore envelopes addressed to a different component.
    # Absent toInstance = broadcast (we DO take those if our kind handler
    # wants them).
    if envelope.toInstance is not None and envelope.toInstance.component != "local":
        return

    logger.info(
        "[cross-component] received envelope v=%s kind=%s direction=%s action=%s from=%s/%s",
        envelope.v,
        envelope.kind,
        envelope.direction,
        envelope.action,
        envelope.fromInstance.component,
        envelope.fromInstance.instanceId,
    )

    if envelope.kind == "rpc":
        _handle_rpc(envelope, user_id)
    elif envelope.kind == "wake":
        _handle_wake(envelope)
    elif envelope.kind == "presence":
        _handle_presence(envelope)
    else:
        logger.warning(
            "[cross-component] unknown envelope kind=%r (action=%s)",
            envelope.kind,
            envelope.action,
        )


def _handle_rpc(envelope: CrossComponentEnvelope, user_id: Optional[str]) -> None:
    """Schedule async dispatch of an inbound rpc envelope.

    Runs from the (sync) realtime broadcast callback, which executes
    inside the engine's event loop — so scheduling a task is safe. If no
    loop is running this is a wiring bug, not a runtime condition: scream
    and drop.
    """
    if not user_id:
        logger.error(
            "[cross-component] rpc envelope has no user channel context — cannot "
            "publish a reply; DROPPED (action=%s requestId=%s)",
            envelope.action,
            envelope.requestId,
        )
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error(
            "[cross-component] rpc envelope arrived outside a running event loop — "
            "DROPPED (action=%s requestId=%s). The broadcast subscription must "
            "live on the engine loop; see extension_broadcast.connect_broadcast.",
            envelope.action,
            envelope.requestId,
        )
        return
    task = loop.create_task(_dispatch_rpc(envelope, user_id))
    # Surface unexpected crashes (should be impossible — _dispatch_rpc is
    # fully try/excepted) rather than letting them vanish into the loop.
    task.add_done_callback(_log_task_crash)


def _log_task_crash(task: "asyncio.Task[None]") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("[cross-component] rpc dispatch task crashed: %s", exc, exc_info=exc)


async def _dispatch_rpc(envelope: CrossComponentEnvelope, user_id: str) -> None:
    """Dispatch one rpc envelope into the extension command registry and
    publish the reply envelope back on the user's bridge channel."""
    from app.api.extension_broadcast import publish_envelope
    from app.api.extension_handlers import invoke_command
    from app.api.extension_metrics import record as record_metric

    command = envelope.action
    args = envelope.payload if isinstance(envelope.payload, dict) else {}

    t0 = time.perf_counter()
    result = await invoke_command(command, args, request=None)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    await record_metric(
        f"broadcast:{command}",
        latency_ms,
        ok=bool(result.get("ok")),
        error=None if result.get("ok") else str(result.get("error")),
    )

    reply: Dict[str, Any] = {
        "v": 2,
        "kind": "rpc",
        "direction": "local->extension",
        "action": f"{envelope.action}.result",
        "requestId": envelope.requestId,
        "payload": result,
        "timestamp": _now_ms(),
        "fromInstance": {"component": "local", "instanceId": _local_instance_id()},
        "toInstance": envelope.fromInstance.model_dump(),
    }
    sent = await publish_envelope(user_id, reply)
    if sent:
        logger.info(
            "[cross-component] rpc dispatched action=%s requestId=%s ok=%s (%.1fms)",
            command,
            envelope.requestId,
            result.get("ok"),
            latency_ms,
        )
    else:
        logger.error(
            "[cross-component] rpc reply publish FAILED — the caller will time out "
            "(action=%s requestId=%s user=%s)",
            command,
            envelope.requestId,
            user_id,
        )


def _handle_wake(envelope: CrossComponentEnvelope) -> None:
    """Route a wake hint by action.

    - ``tool_call.delegated`` — aidream suspended an agent turn on one or more
      tool calls bound to this desktop. The payload is a HINT only (never
      trusted for execution): trigger an immediate delegation sweep, which
      re-reads the durable ledger via ``GET /ai/user/pending_calls``.
    - ``sch_task.due`` — scheduler wake; Phase 3c pending (claim deferred).
    """
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}

    if envelope.action == "tool_call.delegated":
        conversation_id = payload.get("conversationId")
        logger.info(
            "[cross-component] delegation wake received (conversation=%s) — sweeping",
            conversation_id,
        )
        try:
            from app.services.delegation import get_delegation_engine

            get_delegation_engine().request_sweep(
                f"broadcast wake (conversation={conversation_id})"
            )
        except Exception:
            logger.error(
                "[cross-component] delegation wake could not trigger a sweep — "
                "the poll interval remains the backstop",
                exc_info=True,
            )
        return

    task_id = payload.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        logger.warning(
            "[cross-component] wake envelope (action=%s) has no known routing and "
            "no taskId in payload: %r",
            envelope.action,
            envelope.payload,
        )
        return
    logger.info(
        "[cross-component] wake hint received for sch_task=%s — claim deferred to Phase 3c",
        task_id,
    )
    # TODO Phase 3c: call matrx_scheduler claim RPC for this task and run.


def _handle_presence(envelope: CrossComponentEnvelope) -> None:
    """Debug log only. Presence handling is future work."""
    logger.debug(
        "[cross-component] presence envelope ignored: action=%s from=%s/%s",
        envelope.action,
        envelope.fromInstance.component,
        envelope.fromInstance.instanceId,
    )
