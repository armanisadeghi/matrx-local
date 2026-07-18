"""Headless suspend/resume delegation engine.

THE FLOW (canonical protocol:
matrx-frontend/features/agents/docs/CLIENT_TOOL_SUSPEND_RESUME.md):

    cloud agent turn delegates a matrx-local-bound tool
        → aidream hard-suspends (chat.tool_call row status='delegated')
        → THIS ENGINE learns of the call        (poll + broadcast wake)
        → executes it via the canonical dispatcher (catalog cloud name → mega)
        → POST /ai/conversations/{id}/tool_results (user JWT)
        → continuation_needed=true → POST /ai/conversations/{id}/resume
        → drains the continuation stream; a re-delegated call is picked up
          by the immediate post-resume sweep (re-entrancy, §2.4).

HOW A HEADLESS DESKTOP LEARNS OF A CALL — two sources, one sweep path:

  1. POLL (primary, correctness): ``GET /ai/user/pending_calls`` on an
     interval. The ledger row is durable (30-day expiry) so nothing is ever
     lost; this works against the live server today with zero new surface.
  2. BROADCAST WAKE (latency): aidream publishes a ``kind:"wake"``
     ``action:"tool_call.delegated"`` envelope on ``matrx-local-bridge:<uid>``
     when a turn suspends (server-side publish; deploy-gated). The wake is
     ONLY a "sweep now" hint — its payload is never trusted for execution.

The `chat.tool_call` ledger has NO executor column — the desktop recognizes
its own work by tool name: a pending call whose ``tool_name`` resolves in
``app.tools.catalog.get_by_cloud_name`` belongs to this engine. Anything
else (browser tools, ui-first tools) is silently left for its owner.

Failure posture: states, not errors. No JWT / expired JWT → idle (the
frontend refreshes tokens; we just read TokenRepo each tick). Server
unreachable → warn once per transition, keep polling. A tool execution
failure is a RESULT (is_error=true) — the agent must hear about it, never a
dropped call. An undeliverable result is retried on subsequent sweeps
(execute-once, deliver-until-acknowledged).
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
import io
import os
import sys
import time
from collections.abc import Iterable
from typing import Any, Dict, Literal, Optional

from app.common.system_logger import get_logger
from app.services.app_config import get_aidream_server_url
from app.services.delegation.client import (
    DelegationApiClient,
    DelegationApiError,
    ResumeOutcome,
)
from app.services.delegation.outbox import DelegationOutbox, SqliteDelegationOutbox

logger = get_logger()

# Poll cadence. Deliberately modest — the broadcast wake is the low-latency
# path; the poll is the correctness backstop (and the primary until the
# aidream-side publish deploys). Overridable for tests / impatient dev.
DEFAULT_POLL_INTERVAL = float(os.environ.get("MATRX_DELEGATION_POLL_INTERVAL", "15"))

# Client-side execution guard per mega-tool (dispatcher name → seconds).
# The server-side ledger expiry is 30 days (abandonment TTL, not a deadline)
# so slow desktop ops are safe; this bound exists so a hung handler can't
# pin an executor slot forever. Anything not listed gets the default.
EXECUTION_TIMEOUTS: dict[str, float] = {
    "Shell": 900.0,  # long builds / installs
    "Web": 600.0,  # downloads, research
    "Browser": 300.0,  # playwright flows
    "Media": 600.0,  # OCR / PDF / archives
    "Audio": 600.0,  # record / transcribe
}
DEFAULT_EXECUTION_TIMEOUT = 120.0

# Keep delegated result bodies below common proxy/server body limits. Full
# multi-monitor screenshots can be several MB once base64 encoded; text/file
# tools can succeed while screenshot delivery fails and strands the turn.
MAX_INLINE_IMAGE_BASE64_CHARS = int(
    os.environ.get("MATRX_DELEGATION_MAX_INLINE_IMAGE_BASE64_CHARS", "900000")
)
DELEGATED_IMAGE_LONG_EDGE = int(
    os.environ.get("MATRX_DELEGATION_IMAGE_LONG_EDGE", "1600")
)
DELEGATED_IMAGE_JPEG_QUALITY = int(
    os.environ.get("MATRX_DELEGATION_IMAGE_JPEG_QUALITY", "75")
)

# At most this many delegated calls execute concurrently.
_MAX_CONCURRENT_CALLS = 4

# Resume single-flight: suppress duplicate resume attempts per
# user_request_id for this window (§2.6 of the protocol doc).
_RESUME_SUPPRESS_TTL = 10.0
# Bounded retry for 409 resume_conflict: 700ms × attempt, max 4 attempts.
_RESUME_CONFLICT_BASE_DELAY = 0.7
_RESUME_CONFLICT_MAX_ATTEMPTS = 4

# Give an open web tab's durable-ledger watcher first claim on /resume so the
# continuation flows through its normal Redux stream processor. The desktop
# remains the correctness fallback when no browser is watching.
_BROWSER_RESUME_GRACE_SECONDS = float(
    os.environ.get("MATRX_DELEGATION_FRONTEND_RESUME_GRACE_SECONDS", "2.5")
)

# Bounded memory for the executed-call guard.
_HANDLED_CALLS_MAX = 500
_DIAGNOSTIC_EVENTS_MAX = 80


class DelegationEngine:
    """Background loop: sweep pending delegated calls, execute, deliver, resume."""

    def __init__(
        self,
        client: DelegationApiClient | None = None,
        poll_interval: float | None = None,
        outbox: DelegationOutbox | None = None,
    ) -> None:
        # URL captured at engine construction from the app-config accessor
        # (env override > remote > cache > compiled default). A mid-session
        # config change applies on the next engine start.
        self._client = client or DelegationApiClient(get_aidream_server_url())
        self._interval = (
            poll_interval if poll_interval is not None else DEFAULT_POLL_INTERVAL
        )
        self._outbox = outbox or SqliteDelegationOutbox()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT_CALLS)
        # call_id → started monotonic ts. In-flight executions.
        self._inflight: Dict[str, float] = {}
        # call_id → ts. Executed (delivered or pending delivery) — never re-execute.
        self._handled: Dict[str, float] = {}
        # call_id → (conversation_id, result payload). Executed but the
        # tool_results POST failed; retried each sweep until acknowledged.
        self._undelivered: Dict[str, tuple[str, dict[str, Any]]] = {}
        # user_request_id → ts of last resume attempt (single-flight).
        self._recent_resumes: Dict[str, float] = {}
        self._call_tasks: set[asyncio.Task[None]] = set()
        # State-transition logging (states, not errors).
        self._last_idle_reason: str | None = None
        self._server_unreachable = False
        self._last_sweep_at: float | None = None
        self._last_dispatched_at: float | None = None
        self._last_delivered_at: float | None = None
        self._last_resume_at: float | None = None
        self._last_error: str | None = None
        self._last_claimed_pending_count = 0
        self._last_visible_pending_count = 0
        self._last_claimed_pending_tools: list[str] = []
        self._last_visible_pending_tools: list[str] = []
        self._last_unresolved_tools: list[str] = []
        self._last_visible_pending: list[dict[str, Any]] = []
        self._events: deque[dict[str, Any]] = deque(maxlen=_DIAGNOSTIC_EVENTS_MAX)

    # ------------------------------------------------------------------
    # Lifecycle (launcher-managed; see app/main.py Phase 2f)
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start_background(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="delegation-sweep")
        logger.info(
            "[delegation] background sweep started (interval=%.0fs, server=%s)",
            self._interval,
            get_aidream_server_url(),
        )

    async def stop_background(self) -> None:
        self._stop.set()
        self._wake.set()
        tasks = [t for t in (self._task, *self._call_tasks) if t is not None]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._call_tasks.clear()
        logger.info("[delegation] background sweep stopped")

    def request_sweep(self, reason: str) -> None:
        """Ask for an immediate sweep (broadcast wake, post-resume, tests)."""
        logger.info("[delegation] sweep requested: %s", reason)
        self._wake.set()

    def status_payload(self) -> dict[str, Any]:
        """Cheap, read-only snapshot for diagnostics UIs.

        This intentionally exposes state, not secrets: it never returns the
        user's JWT, pending call arguments, or tool result bodies.
        """
        now = time.time()

        def age(ts: float | None) -> float | None:
            return round(now - ts, 3) if ts is not None else None

        return {
            "active": self.active,
            "server_url": self._client.base_url,
            "instance_id": _local_instance_id(),
            "poll_interval_seconds": self._interval,
            "idle_reason": self._last_idle_reason,
            "server_unreachable": self._server_unreachable,
            "last_error": self._last_error,
            "pending": {
                "claimed_count": self._last_claimed_pending_count,
                "claimed_tools": self._last_claimed_pending_tools,
                "visible_count": self._last_visible_pending_count,
                "visible_tools": self._last_visible_pending_tools,
                "unresolved_tools": self._last_unresolved_tools,
                "visible_sample": self._last_visible_pending[:20],
            },
            "counts": {
                "inflight": len(self._inflight),
                "handled": len(self._handled),
                "undelivered": len(self._undelivered),
                "recent_resumes": len(self._recent_resumes),
                "call_tasks": len(self._call_tasks),
            },
            "recent_events": list(self._events),
            "timestamps": {
                "last_sweep_at": self._last_sweep_at,
                "last_dispatched_at": self._last_dispatched_at,
                "last_delivered_at": self._last_delivered_at,
                "last_resume_at": self._last_resume_at,
            },
            "ages_seconds": {
                "last_sweep": age(self._last_sweep_at),
                "last_dispatched": age(self._last_dispatched_at),
                "last_delivered": age(self._last_delivered_at),
                "last_resume": age(self._last_resume_at),
            },
        }

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # The loop must survive anything; individual failures are
                # already logged with attribution inside sweep_once.
                logger.error(
                    "[delegation] sweep crashed (loop continues)", exc_info=True
                )
            # Wait for the interval OR an explicit wake, whichever first.
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def sweep_once(self) -> int:
        """One sweep: retry undelivered results, then discover + execute new
        calls. Returns the number of NEW calls dispatched (for tests)."""
        self._last_sweep_at = time.time()
        creds = await self._get_credentials()
        if creds is None:
            return 0
        jwt = creds

        if not await self._restore_outbox():
            return 0
        await self._retry_undelivered(jwt)

        try:
            pending = await self._client.list_pending_calls(jwt)
            self._record_pending_snapshot("claimed", pending)
            if self._server_unreachable:
                self._server_unreachable = False
                logger.info("[delegation] server reachable again")
        except (DelegationApiError, Exception) as exc:
            if not self._server_unreachable:
                self._server_unreachable = True
                logger.warning("[delegation] pending_calls unreachable: %s", exc)
            self._last_error = str(exc)
            return 0

        dispatched = 0
        unresolved: set[str] = set()
        for call in pending:
            call_id = str(call.get("call_id") or "")
            tool_name = str(call.get("tool_name") or "")
            conversation_id = str(call.get("conversation_id") or "")
            if not call_id or not tool_name or not conversation_id:
                logger.warning("[delegation] malformed pending call skipped: %r", call)
                continue
            if call_id in self._inflight or call_id in self._handled:
                continue
            entry = self._resolve_tool(tool_name)
            if entry is None:
                # Not ours — a browser/ui-first tool awaiting its own client.
                unresolved.add(tool_name)
                self._event(
                    "skipped_unresolved_tool",
                    call_id=call_id,
                    tool_name=tool_name,
                    conversation_id=conversation_id,
                )
                continue
            try:
                queued = await self._outbox.enqueue(call)
            except Exception as exc:
                self._last_error = f"delegation outbox enqueue failed: {exc}"
                logger.error(
                    "[delegation] refusing execution because outbox enqueue failed "
                    "(call_id=%s)",
                    call_id,
                    exc_info=True,
                )
                continue
            if not queued:
                logger.info(
                    "[delegation] call_id=%s already owned by an outbox execution",
                    call_id,
                )
                continue
            self._inflight[call_id] = time.monotonic()
            self._event(
                "dispatching",
                call_id=call_id,
                tool_name=tool_name,
                dispatcher=entry.dispatcher_name,
                conversation_id=conversation_id,
            )
            task = asyncio.create_task(
                self._handle_call(call, entry), name=f"delegation-call-{call_id[:12]}"
            )
            self._call_tasks.add(task)
            task.add_done_callback(self._call_tasks.discard)
            dispatched += 1

        if dispatched:
            self._last_dispatched_at = time.time()
            logger.info("[delegation] dispatched %d delegated call(s)", dispatched)
        self._last_unresolved_tools = sorted(unresolved)
        await self._refresh_visible_pending_diagnostics(jwt, claimed=pending)
        return dispatched

    # ------------------------------------------------------------------
    # Credentials (state, not error)
    # ------------------------------------------------------------------

    async def _get_credentials(self) -> str | None:
        from app.services.local_db.repositories import TokenRepo

        repo = TokenRepo()
        row = await repo.get()
        if not row or not row.get("access_token"):
            self._log_idle("no signed-in user")
            return None
        if repo.is_expired(row):
            self._log_idle("access token expired — waiting for the app to refresh it")
            return None
        if self._last_idle_reason is not None:
            logger.info("[delegation] credentials available — sweeping resumes")
            self._last_idle_reason = None
        return str(row["access_token"])

    def _log_idle(self, reason: str) -> None:
        if self._last_idle_reason != reason:
            logger.info("[delegation] idle: %s", reason)
            self._last_idle_reason = reason

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _resolve_tool(self, tool_name: str) -> Any | None:
        from app.tools.catalog import get_by_cloud_name

        return get_by_cloud_name(tool_name)

    async def _handle_call(self, call: dict[str, Any], entry: Any) -> None:
        """Execute ONE delegated call and deliver its result. Never raises."""
        call_id = str(call["call_id"])
        tool_name = str(call["tool_name"])
        conversation_id = str(call["conversation_id"])
        args = call.get("arguments")
        if not isinstance(args, dict):
            args = {}

        try:
            async with self._sem:
                # Queued work is safe to retry after a restart. Cross the
                # at-most-once boundary only after an executor slot is held;
                # the queued->executing CAS also prevents a second process
                # sharing this SQLite home from running the same mutating call.
                if not await self._outbox.mark_executing(call_id):
                    logger.info(
                        "[delegation] execution claim lost for call_id=%s", call_id
                    )
                    return
                result_payload = await self._execute(entry, tool_name, call_id, args)
            await self._outbox.store_result(call_id, result_payload)
            self._mark_handled(call_id)
            await self._deliver(conversation_id, call_id, result_payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "[delegation] call handling crashed (call_id=%s tool=%s) — the "
                "conversation may be stuck until the next sweep retries delivery",
                call_id,
                tool_name,
                exc_info=True,
            )
        finally:
            self._inflight.pop(call_id, None)

    async def _execute(
        self, entry: Any, tool_name: str, call_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Run the tool through the canonical dispatcher; build the
        ClientToolResult wire payload. Failures become is_error results."""
        from app.tools.dispatcher import dispatch
        from app.tools.session import ToolSession
        from app.tools.types import ToolResultType

        timeout = EXECUTION_TIMEOUTS.get(
            entry.dispatcher_name, DEFAULT_EXECUTION_TIMEOUT
        )
        logger.info(
            "[delegation] executing %s (dispatcher=%s call_id=%s timeout=%.0fs)",
            tool_name,
            entry.dispatcher_name,
            call_id,
            timeout,
        )
        self._event(
            "executing",
            call_id=call_id,
            tool_name=tool_name,
            dispatcher=entry.dispatcher_name,
            args_keys=sorted(args.keys()),
        )
        started = time.monotonic()
        session = ToolSession()
        try:
            result = await asyncio.wait_for(
                dispatch(entry.dispatcher_name, args, session), timeout=timeout
            )
        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "[delegation] %s timed out after %.0fs (call_id=%s)",
                tool_name,
                timeout,
                call_id,
            )
            return {
                "call_id": call_id,
                "tool_name": tool_name,
                "output": None,
                "is_error": True,
                "error_message": (
                    f"Tool '{tool_name}' timed out after {int(timeout)}s on the desktop."
                ),
                "duration_ms": duration_ms,
            }
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.error(
                "[delegation] dispatch raised for %s (call_id=%s)",
                tool_name,
                call_id,
                exc_info=True,
            )
            return {
                "call_id": call_id,
                "tool_name": tool_name,
                "output": None,
                "is_error": True,
                "error_message": f"Tool dispatch crashed: {type(exc).__name__}: {exc}",
                "duration_ms": duration_ms,
            }
        finally:
            try:
                await session.cleanup()
            except Exception:
                logger.warning(
                    "[delegation] session cleanup failed (call_id=%s)",
                    call_id,
                    exc_info=True,
                )

        duration_ms = int((time.monotonic() - started) * 1000)
        is_error = result.type == ToolResultType.ERROR
        if is_error:
            return {
                "call_id": call_id,
                "tool_name": tool_name,
                "output": None,
                "is_error": True,
                "error_message": result.output
                or "Tool returned an error with no message.",
                "duration_ms": duration_ms,
            }
        output: dict[str, Any] = {"output": result.output}
        if result.metadata:
            output["metadata"] = result.metadata
        if result.image is not None:
            image_payload, image_note = _prepare_delegated_image(result.image)
            if image_note:
                metadata = dict(output.get("metadata") or {})
                metadata["delegation_image_note"] = image_note
                output["metadata"] = metadata
            if image_payload is not None:
                output["image"] = image_payload
        return {
            "call_id": call_id,
            "tool_name": tool_name,
            "output": output,
            "is_error": False,
            "error_message": None,
            "duration_ms": duration_ms,
        }

    def _mark_handled(self, call_id: str) -> None:
        self._handled[call_id] = time.monotonic()
        while len(self._handled) > _HANDLED_CALLS_MAX:
            self._handled.pop(next(iter(self._handled)))

    # ------------------------------------------------------------------
    # Delivery + continuation
    # ------------------------------------------------------------------

    async def _deliver(
        self, conversation_id: str, call_id: str, result_payload: dict[str, Any]
    ) -> None:
        jwt = await self._get_credentials()
        if jwt is None:
            logger.warning(
                "[delegation] result for call_id=%s cannot be delivered (no credentials) — "
                "queued for the next sweep",
                call_id,
            )
            self._undelivered[call_id] = (conversation_id, result_payload)
            return
        try:
            response = await self._client.post_tool_results(
                jwt, conversation_id, [result_payload]
            )
        except DelegationApiError as exc:
            logger.warning(
                "[delegation] tool_results delivery failed for call_id=%s (%s) — "
                "queued for retry",
                call_id,
                exc,
            )
            self._undelivered[call_id] = (conversation_id, result_payload)
            self._last_error = str(exc)
            self._event("delivery_retry_queued", call_id=call_id, error=str(exc))
            return
        except Exception as exc:
            logger.warning(
                "[delegation] tool_results delivery failed for call_id=%s (%s) — "
                "queued for retry",
                call_id,
                exc,
            )
            self._undelivered[call_id] = (conversation_id, result_payload)
            self._last_error = str(exc)
            self._event("delivery_retry_queued", call_id=call_id, error=str(exc))
            return

        self._last_delivered_at = time.time()
        not_found = response.get("not_found")
        if isinstance(not_found, list) and call_id in not_found:
            logger.error(
                "[delegation] server did not recognize call_id=%s (not_found) — "
                "conversation may be out of sync",
                call_id,
            )
        logger.info(
            "[delegation] result delivered call_id=%s continuation_needed=%s",
            call_id,
            response.get("continuation_needed"),
        )
        self._event(
            "delivered",
            call_id=call_id,
            continuation_needed=bool(response.get("continuation_needed")),
            user_request_id=response.get("user_request_id"),
        )
        if response.get("continuation_needed") and response.get("user_request_id"):
            user_request_id = str(response["user_request_id"])
            # Keep the executed result until continuation is either streamed or
            # proven terminal. Re-posting it is idempotent and lets a later
            # sweep recover from a lost acknowledgement or failed /resume.
            self._undelivered[call_id] = (conversation_id, result_payload)
            if (
                user_request_id not in self._recent_resumes
                and _BROWSER_RESUME_GRACE_SECONDS > 0
            ):
                self._event(
                    "browser_resume_grace",
                    conversation_id=conversation_id,
                    user_request_id=user_request_id,
                    seconds=_BROWSER_RESUME_GRACE_SECONDS,
                )
                await asyncio.sleep(_BROWSER_RESUME_GRACE_SECONDS)

            disposition = await self._maybe_resume(
                conversation_id, user_request_id, jwt
            )
            if disposition in ("streamed", "terminal"):
                self._undelivered.pop(call_id, None)
                await self._clear_outbox(call_id)
            else:
                self._event(
                    "resume_retry_queued",
                    conversation_id=conversation_id,
                    user_request_id=user_request_id,
                    call_id=call_id,
                    disposition=disposition,
                )
            return

        self._undelivered.pop(call_id, None)
        await self._clear_outbox(call_id)

    async def _restore_outbox(self) -> bool:
        """Restore executed results before reading the cloud pending ledger."""
        try:
            entries = await self._outbox.list_entries()
        except Exception as exc:
            self._last_error = f"delegation outbox unavailable: {exc}"
            logger.error(
                "[delegation] local outbox unavailable — refusing new execution",
                exc_info=True,
            )
            return False

        for entry in entries:
            call_id = str(entry.get("call_id") or "")
            conversation_id = str(entry.get("conversation_id") or "")
            tool_name = str(entry.get("tool_name") or "")
            if not call_id or not conversation_id:
                logger.error("[delegation] malformed outbox entry skipped: %r", entry)
                continue
            if (
                call_id in self._undelivered
                or call_id in self._inflight
                or call_id in self._handled
            ):
                continue
            if entry.get("state") == "queued":
                # It never crossed the execution boundary. Leave it queued;
                # the durable cloud row will schedule it below.
                continue
            payload = entry.get("result")
            if not isinstance(payload, dict):
                payload = {
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "output": None,
                    "is_error": True,
                    "error_message": (
                        "The desktop restarted while this tool was executing. Its outcome "
                        "is unknown, so it was not executed again to avoid duplicate side effects."
                    ),
                    "duration_ms": 0,
                }
                try:
                    await self._outbox.store_result(call_id, payload)
                except Exception:
                    logger.error(
                        "[delegation] failed to persist ambiguous restart result call_id=%s",
                        call_id,
                        exc_info=True,
                    )
                    return False
            self._mark_handled(call_id)
            self._undelivered[call_id] = (conversation_id, payload)
            self._event(
                "outbox_restored",
                call_id=call_id,
                conversation_id=conversation_id,
                state=entry.get("state"),
            )
        return True

    async def _clear_outbox(self, call_id: str) -> None:
        try:
            await self._outbox.delete(call_id)
        except Exception as exc:
            # A stale completed row is safe: the next boot re-posts the same
            # idempotent result and aidream answers not_resumable.
            self._last_error = f"delegation outbox cleanup failed: {exc}"
            logger.warning(
                "[delegation] outbox cleanup failed call_id=%s: %s", call_id, exc
            )

    async def _retry_undelivered(self, jwt: str) -> None:
        if not self._undelivered:
            return
        pending = list(self._undelivered.items())
        logger.info("[delegation] retrying %d undelivered result(s)", len(pending))
        for call_id, (conversation_id, payload) in pending:
            # _deliver re-queues on failure and pops on success.
            self._undelivered.pop(call_id, None)
            await self._deliver(conversation_id, call_id, payload)

    async def _maybe_resume(
        self, conversation_id: str, user_request_id: str, jwt: str
    ) -> Literal["streamed", "retry", "terminal"]:
        """Single-flight resume per user_request_id, with bounded 409 retry."""
        now = time.monotonic()
        last = self._recent_resumes.get(user_request_id)
        if last is not None and (now - last) < _RESUME_SUPPRESS_TTL:
            logger.info(
                "[delegation] duplicate resume suppressed for request=%s",
                user_request_id,
            )
            return "retry"
        self._recent_resumes[user_request_id] = now
        # Bound the map.
        while len(self._recent_resumes) > _HANDLED_CALLS_MAX:
            self._recent_resumes.pop(next(iter(self._recent_resumes)))

        body = {
            "user_request_id": user_request_id,
            "client": self._build_client_context(),
        }
        for attempt in range(1, _RESUME_CONFLICT_MAX_ATTEMPTS + 1):
            try:
                outcome: ResumeOutcome = await self._client.resume(
                    jwt, conversation_id, body
                )
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning(
                    "[delegation] resume failed for conversation=%s request=%s: %s",
                    conversation_id,
                    user_request_id,
                    exc,
                )
                return "retry"
            if outcome.status == "streamed":
                self._last_resume_at = time.time()
                logger.info(
                    "[delegation] resume streamed to completion "
                    "(conversation=%s events=%d re-delegated=%d)",
                    conversation_id,
                    outcome.events_seen,
                    outcome.tool_delegated_seen,
                )
                self._event(
                    "resume_streamed",
                    conversation_id=conversation_id,
                    user_request_id=user_request_id,
                    events_seen=outcome.events_seen,
                    tool_delegated_seen=outcome.tool_delegated_seen,
                )
                # Re-entrancy (§2.4): a re-delegated call is a durable row by
                # now — sweep immediately instead of waiting an interval.
                self.request_sweep("post-resume re-entrancy check")
                return "streamed"
            conflict = outcome.conflict
            assert conflict is not None
            if conflict.code == "resume_conflict" and conflict.retryable:
                if attempt >= _RESUME_CONFLICT_MAX_ATTEMPTS:
                    logger.info(
                        "[delegation] resume_conflict retries exhausted for request=%s "
                        "(benign — another runner owns it)",
                        user_request_id,
                    )
                    return "retry"
                delay = _RESUME_CONFLICT_BASE_DELAY * attempt
                logger.info(
                    "[delegation] resume_conflict (attempt %d/%d) — retrying in %.1fs",
                    attempt,
                    _RESUME_CONFLICT_MAX_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            if conflict.code == "not_resumable":
                logger.info(
                    "[delegation] request=%s is terminal — resume obligation cleared",
                    user_request_id,
                )
                return "terminal"
            # Outstanding siblings and unknown transient conflict shapes retain
            # the idempotent result so a later sweep re-evaluates readiness.
            logger.info(
                "[delegation] resume deferred (%s) for request=%s — retry queued",
                conflict.code,
                user_request_id,
            )
            self._event(
                "resume_declined",
                conversation_id=conversation_id,
                user_request_id=user_request_id,
                code=conflict.code,
                retryable=conflict.retryable,
            )
            return "retry"

        return "retry"

    # ------------------------------------------------------------------
    # Client context for /resume
    # ------------------------------------------------------------------

    def _build_client_context(self) -> dict[str, Any]:
        """The desktop's `client` envelope for the resumed segment.

        Declaring surface `matrx-local/desktop` + the `desktop-native`
        capability keeps this engine an ACTIVE executor for the continuation —
        without it, a re-delegated desktop tool would be dropped pre-flight
        by the merge (no viable executor)."""
        try:
            from app.api.routes import _APP_VERSION

            engine_version = str(_APP_VERSION)
        except Exception:
            engine_version = ""
        try:
            from app.services.cloud_sync.instance_manager import get_instance_manager

            instance_id = get_instance_manager().instance_id
        except Exception:
            instance_id = ""
        return {
            "surface": "matrx-local/desktop",
            "capabilities": ["desktop-native"],
            "state": {
                "desktop-native": {
                    "platform": sys.platform,
                    "engine_version": engine_version,
                    "instance_id": instance_id,
                }
            },
        }

    async def _refresh_visible_pending_diagnostics(
        self, jwt: str, *, claimed: list[dict[str, Any]]
    ) -> None:
        """Read-only pending-call sweep for diagnosis.

        The normal poll includes ``?instance_id=...`` and therefore only sees
        rows this desktop may claim. This pass omits the instance_id so support
        can distinguish "no pending work exists" from "pending work exists but
        is targeted/claimed elsewhere." Rows from this method are NEVER
        executed.
        """
        should_probe = not claimed or bool(self._last_unresolved_tools)
        if not should_probe:
            self._record_pending_snapshot("visible", [])
            return
        try:
            visible = await self._client.list_pending_calls(
                jwt, claim_for_instance=False
            )
        except Exception as exc:
            self._last_error = str(exc)
            self._event("visible_pending_probe_failed", error=str(exc))
            return
        self._record_pending_snapshot("visible", visible)
        if not claimed and visible:
            logger.warning(
                "[delegation] no claimable pending calls for this instance, but "
                "%d delegated call(s) are visible for the user. Check "
                "target_instance_id / claimed_by_instance_id in /chat/delegation/status.",
                len(visible),
            )
            self._event(
                "visible_but_not_claimed",
                visible_count=len(visible),
                tools=_tool_counts(visible),
            )

    def _record_pending_snapshot(self, kind: str, calls: list[dict[str, Any]]) -> None:
        tools = _tool_counts(calls)
        sample = [_summarize_pending_call(c) for c in calls[:20]]
        if kind == "claimed":
            self._last_claimed_pending_count = len(calls)
            self._last_claimed_pending_tools = tools
        elif kind == "visible":
            self._last_visible_pending_count = len(calls)
            self._last_visible_pending_tools = tools
            self._last_visible_pending = sample
        self._event(f"pending_{kind}", count=len(calls), tools=tools)

    def _event(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "ts": time.time(),
            "event": event,
        }
        for key, value in fields.items():
            payload[key] = _safe_diag_value(value)
        self._events.append(payload)


# ---------------------------------------------------------------------------
# Singleton accessor (mirrors chat_sync's get_chat_sync_engine)
# ---------------------------------------------------------------------------

_engine: Optional[DelegationEngine] = None


def get_delegation_engine() -> DelegationEngine:
    global _engine
    if _engine is None:
        _engine = DelegationEngine()
    return _engine


def _tool_counts(calls: Iterable[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    for call in calls:
        name = str(call.get("tool_name") or "")
        if not name:
            name = "<missing>"
        counts[name] = counts.get(name, 0) + 1
    return [f"{name}:{count}" for name, count in sorted(counts.items())]


def _summarize_pending_call(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "call_id": _short(str(call.get("call_id") or "")),
        "conversation_id": _short(str(call.get("conversation_id") or "")),
        "user_request_id": _short(str(call.get("user_request_id") or "")),
        "tool_name": str(call.get("tool_name") or ""),
        "target_instance_id": str(call.get("target_instance_id") or "") or None,
        "claimed_by_instance_id": str(call.get("claimed_by_instance_id") or "") or None,
        "claim_expires_at": str(call.get("claim_expires_at") or "") or None,
        "args_keys": sorted((call.get("arguments") or {}).keys())
        if isinstance(call.get("arguments"), dict)
        else [],
    }


def _short(value: str, *, keep: int = 10) -> str:
    if not value:
        return ""
    return value if len(value) <= keep else f"{value[:keep]}…"


def _local_instance_id() -> str:
    try:
        from app.services.cloud_sync.instance_manager import get_instance_manager

        return get_instance_manager().instance_id
    except Exception:
        return ""


def _safe_diag_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 500 else f"{value[:500]}…"
    if isinstance(value, list):
        return [_safe_diag_value(v) for v in value[:20]]
    if isinstance(value, tuple):
        return [_safe_diag_value(v) for v in list(value)[:20]]
    if isinstance(value, dict):
        return {str(k): _safe_diag_value(v) for k, v in list(value.items())[:30]}
    return str(value)[:500]


def _prepare_delegated_image(image: Any) -> tuple[dict[str, str] | None, str | None]:
    """Return a compact image payload for cloud tool-results delivery.

    The agent should still be able to see screenshots, but the delegation
    engine must never strand a turn because a multi-monitor PNG is too large
    for the cloud handoff. The tool metadata keeps the saved full-fidelity
    local path for desktop inspection.
    """
    payload = image.model_dump()
    b64 = str(payload.get("base64_data") or "")
    media_type = str(payload.get("media_type") or "application/octet-stream")
    if len(b64) <= MAX_INLINE_IMAGE_BASE64_CHARS:
        return payload, None

    try:
        from PIL import Image

        raw = base64.b64decode(b64)
        with Image.open(io.BytesIO(raw)) as img:
            img = img.convert("RGB")
            width, height = img.size
            long_edge = max(width, height)
            if long_edge > DELEGATED_IMAGE_LONG_EDGE:
                scale = DELEGATED_IMAGE_LONG_EDGE / float(long_edge)
                img = img.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    Image.Resampling.LANCZOS,
                )
            out = io.BytesIO()
            img.save(
                out, format="JPEG", quality=DELEGATED_IMAGE_JPEG_QUALITY, optimize=True
            )
        compact = base64.b64encode(out.getvalue()).decode()
        if len(compact) <= MAX_INLINE_IMAGE_BASE64_CHARS:
            return {
                "media_type": "image/jpeg",
                "base64_data": compact,
            }, (
                f"original {media_type} image was {len(b64)} base64 chars; "
                f"sent re-encoded JPEG ({len(compact)} chars)"
            )
        return None, (
            f"original {media_type} image was {len(b64)} base64 chars and compact "
            f"JPEG was still {len(compact)} chars; omitted inline image, metadata/path retained"
        )
    except Exception as exc:
        return None, (
            f"image payload was {len(b64)} base64 chars and could not be compacted "
            f"({type(exc).__name__}: {exc}); omitted inline image, metadata/path retained"
        )
