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
import os
import sys
import time
from typing import Any, Dict, Optional

from app.common.system_logger import get_logger
from app.services.app_config import get_aidream_server_url
from app.services.delegation.client import (
    DelegationApiClient,
    DelegationApiError,
    ResumeOutcome,
)

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
    "Shell": 900.0,       # long builds / installs
    "Web": 600.0,         # downloads, research
    "Browser": 300.0,     # playwright flows
    "Media": 600.0,       # OCR / PDF / archives
    "Audio": 600.0,       # record / transcribe
}
DEFAULT_EXECUTION_TIMEOUT = 120.0

# At most this many delegated calls execute concurrently.
_MAX_CONCURRENT_CALLS = 4

# Resume single-flight: suppress duplicate resume attempts per
# user_request_id for this window (§2.6 of the protocol doc).
_RESUME_SUPPRESS_TTL = 10.0
# Bounded retry for 409 resume_conflict: 700ms × attempt, max 4 attempts.
_RESUME_CONFLICT_BASE_DELAY = 0.7
_RESUME_CONFLICT_MAX_ATTEMPTS = 4

# Bounded memory for the executed-call guard.
_HANDLED_CALLS_MAX = 500


class DelegationEngine:
    """Background loop: sweep pending delegated calls, execute, deliver, resume."""

    def __init__(
        self,
        client: DelegationApiClient | None = None,
        poll_interval: float | None = None,
    ) -> None:
        # URL captured at engine construction from the app-config accessor
        # (env override > remote > cache > compiled default). A mid-session
        # config change applies on the next engine start.
        self._client = client or DelegationApiClient(get_aidream_server_url())
        self._interval = poll_interval if poll_interval is not None else DEFAULT_POLL_INTERVAL
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
                logger.error("[delegation] sweep crashed (loop continues)", exc_info=True)
            # Wait for the interval OR an explicit wake, whichever first.
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def sweep_once(self) -> int:
        """One sweep: retry undelivered results, then discover + execute new
        calls. Returns the number of NEW calls dispatched (for tests)."""
        creds = await self._get_credentials()
        if creds is None:
            return 0
        jwt = creds

        await self._retry_undelivered(jwt)

        try:
            pending = await self._client.list_pending_calls(jwt)
            if self._server_unreachable:
                self._server_unreachable = False
                logger.info("[delegation] server reachable again")
        except (DelegationApiError, Exception) as exc:
            if not self._server_unreachable:
                self._server_unreachable = True
                logger.warning("[delegation] pending_calls unreachable: %s", exc)
            return 0

        dispatched = 0
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
                continue
            self._inflight[call_id] = time.monotonic()
            task = asyncio.create_task(
                self._handle_call(call, entry), name=f"delegation-call-{call_id[:12]}"
            )
            self._call_tasks.add(task)
            task.add_done_callback(self._call_tasks.discard)
            dispatched += 1

        if dispatched:
            logger.info("[delegation] dispatched %d delegated call(s)", dispatched)
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
                result_payload = await self._execute(entry, tool_name, call_id, args)
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

        timeout = EXECUTION_TIMEOUTS.get(entry.dispatcher_name, DEFAULT_EXECUTION_TIMEOUT)
        logger.info(
            "[delegation] executing %s (dispatcher=%s call_id=%s timeout=%.0fs)",
            tool_name,
            entry.dispatcher_name,
            call_id,
            timeout,
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
                "[delegation] dispatch raised for %s (call_id=%s)", tool_name, call_id,
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
                    "[delegation] session cleanup failed (call_id=%s)", call_id, exc_info=True
                )

        duration_ms = int((time.monotonic() - started) * 1000)
        is_error = result.type == ToolResultType.ERROR
        if is_error:
            return {
                "call_id": call_id,
                "tool_name": tool_name,
                "output": None,
                "is_error": True,
                "error_message": result.output or "Tool returned an error with no message.",
                "duration_ms": duration_ms,
            }
        output: dict[str, Any] = {"output": result.output}
        if result.metadata:
            output["metadata"] = result.metadata
        if result.image is not None:
            output["image"] = result.image.model_dump()
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
            if 400 <= exc.status < 500 and exc.status not in (408, 429):
                # Hard 4xx — the server doesn't know this call. Not replayable.
                logger.error(
                    "[delegation] tool_results rejected permanently for call_id=%s: %s",
                    call_id,
                    exc,
                )
                return
            logger.warning(
                "[delegation] tool_results delivery failed for call_id=%s (%s) — "
                "queued for retry",
                call_id,
                exc,
            )
            self._undelivered[call_id] = (conversation_id, result_payload)
            return
        except Exception as exc:
            logger.warning(
                "[delegation] tool_results delivery failed for call_id=%s (%s) — "
                "queued for retry",
                call_id,
                exc,
            )
            self._undelivered[call_id] = (conversation_id, result_payload)
            return

        self._undelivered.pop(call_id, None)
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
        if response.get("continuation_needed") and response.get("user_request_id"):
            await self._maybe_resume(
                conversation_id, str(response["user_request_id"]), jwt
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
    ) -> None:
        """Single-flight resume per user_request_id, with bounded 409 retry."""
        now = time.monotonic()
        last = self._recent_resumes.get(user_request_id)
        if last is not None and (now - last) < _RESUME_SUPPRESS_TTL:
            logger.info(
                "[delegation] duplicate resume suppressed for request=%s", user_request_id
            )
            return
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
                logger.warning(
                    "[delegation] resume failed for conversation=%s request=%s: %s",
                    conversation_id,
                    user_request_id,
                    exc,
                )
                return
            if outcome.status == "streamed":
                logger.info(
                    "[delegation] resume streamed to completion "
                    "(conversation=%s events=%d re-delegated=%d)",
                    conversation_id,
                    outcome.events_seen,
                    outcome.tool_delegated_seen,
                )
                # Re-entrancy (§2.4): a re-delegated call is a durable row by
                # now — sweep immediately instead of waiting an interval.
                self.request_sweep("post-resume re-entrancy check")
                return
            conflict = outcome.conflict
            assert conflict is not None
            if conflict.code == "resume_conflict" and conflict.retryable:
                if attempt >= _RESUME_CONFLICT_MAX_ATTEMPTS:
                    logger.info(
                        "[delegation] resume_conflict retries exhausted for request=%s "
                        "(benign — another runner owns it)",
                        user_request_id,
                    )
                    return
                delay = _RESUME_CONFLICT_BASE_DELAY * attempt
                logger.info(
                    "[delegation] resume_conflict (attempt %d/%d) — retrying in %.1fs",
                    attempt,
                    _RESUME_CONFLICT_MAX_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            # outstanding_delegated_calls / not_resumable / unknown → benign, no retry.
            logger.info(
                "[delegation] resume declined (%s) for request=%s — no retry",
                conflict.code,
                user_request_id,
            )
            return

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


# ---------------------------------------------------------------------------
# Singleton accessor (mirrors chat_sync's get_chat_sync_engine)
# ---------------------------------------------------------------------------

_engine: Optional[DelegationEngine] = None


def get_delegation_engine() -> DelegationEngine:
    global _engine
    if _engine is None:
        _engine = DelegationEngine()
    return _engine
