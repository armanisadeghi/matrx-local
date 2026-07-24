"""Runtime-spine bracket for local AI runs — open → heartbeat → settle.

aidream's execution spine (POST /api/v2/runtime/{open,heartbeat,settle})
tracks every AI run — including client-hosted desktop loops — for execution
tracking and cost. This module brackets ``run_local_ai_task`` with that
lifecycle:

  * ``open_runtime_execution``  — before the loop; returns a ``RuntimeLease``
    whose ``execution_id`` is stamped into ``AppContext.metadata`` as BOTH
    ``runtime_execution_id`` and ``runtime_root_execution_id`` (the vendored
    matrx-ai reads the root key — see
    ``matrx_ai.db.request_tracking_log.resolve_runtime_root_execution_id`` —
    and chat_sync mirrors it into ``chat.request.execution_id``).
  * ``start_heartbeat``         — background lease renewal every
    ``lease_seconds / 3`` while the loop runs.
  * ``settle_runtime_execution``— exactly once on every exit path
    (completed / failed / cancelled), with usage meters when available.

EVERYTHING here is BEST-EFFORT and OFFLINE-SAFE: any open/heartbeat/settle
failure (offline, timeout, 4xx) loud-logs and never breaks or delays the
local run. If open failed, heartbeat/settle are skipped entirely; the server
reaper cleans up an un-settled run — that is by design. A "lost"/"terminal"
heartbeat outcome is loud-logged only — the local run is NEVER killed
(desktop-local UX wins).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any, Optional

from app.common.system_logger import get_logger

logger = get_logger()

# Short timeouts: the spine must never delay the local run.
_OPEN_TIMEOUT = 10.0  # seconds
_HEARTBEAT_TIMEOUT = 5.0  # seconds
_SETTLE_TIMEOUT = 5.0  # seconds
_MIN_HEARTBEAT_INTERVAL = 5.0  # seconds — floor against a tiny server lease
_DEFAULT_LEASE_SECONDS = 1800.0


@dataclass
class RuntimeLease:
    """One opened execution on the aidream runtime spine."""

    execution_id: str
    root_execution_id: str
    lease_holder: str
    lease_seconds: float
    jwt: str
    heartbeat_task: Optional[asyncio.Task[None]] = field(default=None, repr=False)
    settled: bool = False


def _get_client() -> Any | None:
    from app.services.aidream.client import get_aidream_client

    return get_aidream_client()


async def open_runtime_execution(
    *,
    jwt: str | None,
    conversation_id: str | None,
    idempotency_key: str | None = None,
) -> RuntimeLease | None:
    """Open an execution on the runtime spine. Returns None on any failure.

    Requires a real user JWT — an anonymous/local-only run has no cloud
    identity and is intentionally not tracked on the spine.
    """
    if not jwt:
        logger.info(
            "[runtime_spine] no user JWT on this run — skipping cloud runtime "
            "tracking (local-only run)"
        )
        return None

    client = _get_client()
    if client is None:
        logger.warning(
            "[runtime_spine] no aidream server URL resolved — skipping cloud "
            "runtime tracking for this run"
        )
        return None

    payload: dict[str, Any] = {
        "type": "conversation",
        "app": "matrx-local",
        "surface": "desktop_ai",
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
        payload["link_kind"] = "conversation"
        payload["link_id"] = conversation_id
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key

    try:
        data = await client.post(
            "/v2/runtime/open", payload, jwt=jwt, timeout=_OPEN_TIMEOUT
        )
        execution_id = str(data["execution_id"])
        lease = RuntimeLease(
            execution_id=execution_id,
            root_execution_id=str(data.get("root_execution_id") or execution_id),
            lease_holder=str(data.get("lease_holder") or ""),
            lease_seconds=float(data.get("lease_seconds") or _DEFAULT_LEASE_SECONDS),
            jwt=jwt,
        )
        logger.info(
            "[runtime_spine] opened execution %s (root=%s, lease=%ss) for "
            "conversation %s",
            lease.execution_id,
            lease.root_execution_id,
            lease.lease_seconds,
            conversation_id or "—",
        )
        return lease
    except Exception as exc:  # offline-safe: NEVER break the local run
        logger.warning(
            "[runtime_spine] runtime open failed — the local run proceeds "
            "WITHOUT cloud execution tracking (offline-safe): %s",
            exc,
        )
        return None


def start_heartbeat(lease: RuntimeLease) -> None:
    """Start the background lease-renewal task for an opened execution."""
    lease.heartbeat_task = asyncio.create_task(
        _heartbeat_loop(lease),
        name=f"runtime-spine-heartbeat-{lease.execution_id}",
    )


async def _heartbeat_loop(lease: RuntimeLease) -> None:
    interval = max(lease.lease_seconds / 3.0, _MIN_HEARTBEAT_INTERVAL)
    while True:
        await asyncio.sleep(interval)
        try:
            client = _get_client()
            if client is None:
                continue
            data = await client.post(
                "/v2/runtime/heartbeat",
                {
                    "execution_id": lease.execution_id,
                    "lease_holder": lease.lease_holder,
                },
                jwt=lease.jwt,
                timeout=_HEARTBEAT_TIMEOUT,
            )
            outcome = str((data or {}).get("outcome") or "")
            if outcome in ("lost", "terminal"):
                # Loud log ONLY — the local run is never killed over cloud
                # lease state (desktop-local UX wins). Tracking for the rest
                # of this run is best-effort; settle will still be attempted.
                logger.error(
                    "[runtime_spine] heartbeat outcome=%r for execution %s — "
                    "the cloud runtime no longer holds our lease. The LOCAL "
                    "run CONTINUES unaffected; cloud execution tracking for "
                    "it may be incomplete.",
                    outcome,
                    lease.execution_id,
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # offline-safe: retry next interval
            logger.warning(
                "[runtime_spine] heartbeat for execution %s failed (run "
                "continues; will retry): %s",
                lease.execution_id,
                exc,
            )


async def settle_runtime_execution(
    lease: RuntimeLease,
    *,
    status: str,
    error: str | None = None,
    meters: dict[str, Any] | None = None,
) -> None:
    """Settle the execution exactly once. Best-effort, offline-safe.

    ``status`` is one of "completed" | "failed" | "cancelled". Cancels the
    heartbeat task first. A failed settle only warns — the server reaper
    settles abandoned executions.
    """
    if lease.settled:
        return
    lease.settled = True

    if lease.heartbeat_task is not None and not lease.heartbeat_task.done():
        lease.heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await lease.heartbeat_task

    payload: dict[str, Any] = {
        "execution_id": lease.execution_id,
        "status": status,
    }
    if error:
        payload["error"] = error[:2000]
    if meters:
        payload["meters"] = meters

    try:
        client = _get_client()
        if client is None:
            raise RuntimeError("no aidream server URL resolved")
        await client.post(
            "/v2/runtime/settle", payload, jwt=lease.jwt, timeout=_SETTLE_TIMEOUT
        )
        logger.info(
            "[runtime_spine] settled execution %s status=%s meters=%s",
            lease.execution_id,
            status,
            meters or {},
        )
    except asyncio.CancelledError:
        logger.warning(
            "[runtime_spine] settle of execution %s interrupted by "
            "cancellation — the server reaper will settle it",
            lease.execution_id,
        )
    except Exception as exc:  # offline-safe: reaper cleans up
        logger.warning(
            "[runtime_spine] settle of execution %s (status=%s) failed — the "
            "server reaper will settle it: %s",
            lease.execution_id,
            status,
            exc,
        )


def meters_from_completed(completed: Any) -> dict[str, Any] | None:
    """Build spine meters from a CompletedRequest. Never raises.

    Uses ``build_request_summary()`` (the one aggregation both cx_user_request
    and the spine's request summary share) so meters match the request ledger.
    ``usd`` is included only when the run's cost is fully known.
    """
    try:
        summary = completed.build_request_summary()
        meters: dict[str, Any] = {
            "input_tokens": int(summary.get("total_input_tokens") or 0),
            "output_tokens": int(summary.get("total_output_tokens") or 0),
        }
        total_cost = summary.get("total_cost")
        if total_cost is not None:
            meters["usd"] = float(total_cost)
        return meters
    except Exception:
        logger.warning(
            "[runtime_spine] could not derive usage meters from the completed "
            "request — settling without meters",
            exc_info=True,
        )
        return None
