"""HTTP client for the aidream delegation surface.

One thin, injectable-transport wrapper around the three cloud endpoints the
suspend/resume protocol uses (canonical doc:
matrx-frontend/features/agents/docs/CLIENT_TOOL_SUSPEND_RESUME.md):

  GET  /ai/user/pending_calls                       — all delegated calls for the user
  POST /ai/conversations/{id}/tool_results          — deliver executed results
  POST /ai/conversations/{id}/resume                — continue the suspended loop (stream)

All calls carry the user's Supabase JWT as ``Authorization: Bearer``. The
base URL is always the app-config accessor value
(``app.services.app_config.get_aidream_server_url``) — never hardcoded. These
paths are served at the server root (NO ``/api`` prefix — matrx-extend's
``tool-results.ts`` pins the same shape).

The ``transport`` constructor arg exists so tests can pin the full protocol
round-trip against ``httpx.MockTransport`` without a network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import httpx

from app.common.system_logger import get_logger

logger = get_logger()

_REQUEST_TIMEOUT = 30.0
# The resume response streams the whole continuation turn — model iterations
# included — so it needs a long read window. Connect stays snappy.
_RESUME_TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=30.0, pool=30.0)


class DelegationApiError(Exception):
    """Non-2xx from the delegation surface (except structured resume 409s)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ResumeConflict:
    """Structured 409 from POST /resume. Never a user-facing error.

    ``code`` per the protocol table (§2.5):
      - resume_conflict              → retryable (bounded backoff)
      - outstanding_delegated_calls  → do not retry; next tool_results re-triggers
      - not_resumable                → terminal; never retry
    """

    code: str
    retryable: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResumeOutcome:
    """What happened when we opened /resume."""

    status: str  # "streamed" | "conflict"
    conflict: Optional[ResumeConflict] = None
    events_seen: int = 0
    tool_delegated_seen: int = 0


def _parse_conflict_code(body: dict[str, Any]) -> str:
    """Extract the structured 409 code from aidream's exception envelope.

    aidream's global envelope rewrites HTTPException.detail to a top-level
    ``{error, message, details}`` — the code arrives in ``error``, as a
    ``message`` prefix, and in ``details.code``. A raw FastAPI deployment
    nests it under ``detail.code``. Parse all of them (protocol doc §2.5).
    """
    for candidate in (
        body.get("error"),
        (body.get("details") or {}).get("code")
        if isinstance(body.get("details"), dict)
        else None,
        (body.get("detail") or {}).get("code")
        if isinstance(body.get("detail"), dict)
        else None,
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    message = body.get("message")
    if isinstance(message, str) and ":" in message:
        return message.split(":", 1)[0].strip()
    return "unknown"


class DelegationApiClient:
    """Async client for the three suspend/resume endpoints."""

    def __init__(
        self,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError(
                "[delegation] No AIDream server URL was resolved from app config "
                "— cannot build the delegation client."
            )
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base_url

    def _client(
        self, timeout: httpx.Timeout | float = _REQUEST_TIMEOUT
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    def _headers(self, jwt: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def list_pending_calls(
        self,
        jwt: str,
        *,
        claim_for_instance: bool = True,
    ) -> list[dict[str, Any]]:
        """GET /ai/user/pending_calls — every delegated call awaiting this user,
        across all conversations. Caller filters to the tools this desktop owns.

        Sends this engine's ``instance_id`` (Tier 2, see
        docs/TIER2_DESKTOP_INSTANCE_TARGETING.md): once the server filters by
        the conversation's bound ``app_instance_id``, a delegated call bound to
        ANOTHER desktop (e.g. the installed app vs a dev build) is not returned
        here — killing the double-claim race. Forward-compatible: a server that
        does not yet filter simply ignores the param. Best-effort — a resolve
        failure omits the param and falls back to today's user-scoped behavior.
        """
        url = f"{self._base_url}/ai/user/pending_calls"
        params: dict[str, str] = {}
        if claim_for_instance:
            try:
                from app.services.cloud_sync.instance_manager import (
                    get_instance_manager,
                )

                iid = get_instance_manager().instance_id
                if iid:
                    params["instance_id"] = iid
            except Exception:
                logger.debug(
                    "[delegation] could not resolve instance_id for pending_calls",
                    exc_info=True,
                )
        async with self._client() as http:
            resp = await http.get(
                url, headers=self._headers(jwt), params=params or None
            )
        if resp.status_code != 200:
            raise DelegationApiError(
                resp.status_code,
                f"[delegation] pending_calls → HTTP {resp.status_code}: {resp.text[:300]}",
            )
        data = resp.json()
        if not isinstance(data, list):
            raise DelegationApiError(
                200,
                f"[delegation] pending_calls returned non-list payload: {type(data).__name__}",
            )
        return data

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    async def post_tool_results(
        self,
        jwt: str,
        conversation_id: str,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """POST /ai/conversations/{id}/tool_results.

        Returns the ToolResultsResponse dict — the caller MUST read
        ``continuation_needed`` (anti-pattern #2 in the protocol doc).
        """
        url = f"{self._base_url}/ai/conversations/{conversation_id}/tool_results"
        async with self._client() as http:
            resp = await http.post(
                url, headers=self._headers(jwt), json={"results": results}
            )
        if resp.status_code != 200:
            raise DelegationApiError(
                resp.status_code,
                f"[delegation] tool_results → HTTP {resp.status_code}: {resp.text[:300]}",
            )
        body = resp.json()
        if not isinstance(body, dict):
            raise DelegationApiError(
                200, "[delegation] tool_results returned 200 but body is not an object"
            )
        return body

    # ------------------------------------------------------------------
    # Resume
    # ------------------------------------------------------------------

    async def resume(
        self,
        jwt: str,
        conversation_id: str,
        body: dict[str, Any],
    ) -> ResumeOutcome:
        """POST /ai/conversations/{id}/resume and DRAIN the continuation stream.

        The stream must be consumed to completion — the server runs the model
        loop while we hold the response open. We do not act on individual
        events here (a re-delegated call lands as a durable ledger row and is
        picked up by the engine's post-resume sweep) but we count
        ``tool_delegated`` sub-events so the caller can log re-entrancy.
        """
        url = f"{self._base_url}/ai/conversations/{conversation_id}/resume"
        async with self._client(timeout=_RESUME_TIMEOUT) as http:
            async with http.stream(
                "POST", url, headers=self._headers(jwt), json=body
            ) as resp:
                if resp.status_code == 409:
                    raw = await _read_json_body(resp)
                    code = _parse_conflict_code(raw)
                    retryable = bool(raw.get("retryable")) or code == "resume_conflict"
                    if (
                        isinstance(raw.get("details"), dict)
                        and "retryable" in raw["details"]
                    ):
                        retryable = bool(raw["details"]["retryable"])
                    return ResumeOutcome(
                        status="conflict",
                        conflict=ResumeConflict(
                            code=code, retryable=retryable, raw=raw
                        ),
                    )
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", errors="replace")
                    raise DelegationApiError(
                        resp.status_code,
                        f"[delegation] resume → HTTP {resp.status_code}: {text[:300]}",
                    )
                events = 0
                delegated = 0
                async for evt in _iter_stream_events(resp):
                    events += 1
                    error_message = _stream_error_message(evt)
                    if error_message is not None:
                        raise DelegationApiError(
                            200,
                            f"[delegation] resume stream reported a fatal error: {error_message}",
                        )
                    if _is_tool_delegated(evt):
                        delegated += 1
                return ResumeOutcome(
                    status="streamed", events_seen=events, tool_delegated_seen=delegated
                )


async def _read_json_body(resp: httpx.Response) -> dict[str, Any]:
    try:
        raw = await resp.aread()
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def _iter_stream_events(resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Yield parsed JSON events from an NDJSON or SSE-style stream.

    Tolerant by design: lines that are not JSON (SSE ``event:`` markers,
    keep-alives, blanks) are skipped silently — the drain is what matters.
    """
    async for line in resp.aiter_lines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[len("data:") :].strip()
            if not line:
                continue
        if not (line.startswith("{") or line.startswith("[")):
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            yield parsed


def _is_tool_delegated(evt: dict[str, Any]) -> bool:
    """True when a stream event is (or wraps) a ``tool_delegated`` signal."""
    if evt.get("event") == "tool_delegated":
        return True
    data = evt.get("data")
    if isinstance(data, dict) and data.get("event") == "tool_delegated":
        return True
    return False


def _stream_error_message(evt: dict[str, Any]) -> str | None:
    """Return a diagnostic when the continuation emitted a fatal error."""
    if evt.get("event") != "error":
        return None
    data = evt.get("data")
    if isinstance(data, dict):
        for key in ("message", "user_message", "error_type"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return "unknown stream error"
