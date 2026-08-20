"""Durable ordered outbox from local provider hooks to aidream."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import aiosqlite
from pydantic import ValidationError

from app.common.system_logger import get_logger
from app.services.aidream.client import (
    AIDreamClient,
    AIDreamError,
    AIDreamOfflineError,
    get_aidream_client,
)
from app.services.coding_sessions.models import (
    BridgeProvider,
    BridgeRequest,
    LocalBridgeReceipt,
)
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import TokenRepo

logger = get_logger()

_SERVER_PATH = "/coding-sessions/bridge"
# The metadata-plane hook name. It carries provider-authored session labels
# (title, workspace, branch, worktree, archived) rather than transcript
# content, so the server applies it to an EXISTING binding of either fidelity
# and settles an unbound session with accepted=0 instead of minting one.
SESSION_METADATA_EVENT = "SessionMetadata"
_PUBLISH_INTERVAL_SECONDS = 15.0
_MAX_BATCH = 100
_MAX_BACKOFF_SECONDS = 60.0

# THE POST-DELIVERY DURABILITY BOUNDARY. Once aidream has accepted an envelope,
# deleting its local row is no longer bookkeeping — it is the only thing that
# stops the publisher re-sending an already-delivered event. That write must
# therefore be as strong as the hook's own durable-ack boundary: a private
# short connection with BEGIN IMMEDIATE, not the application's shared
# connection. Found live 2026-08-19 under v1.4.35: a codex hook storm held the
# write lock continuously, the shared connection's delete lost with
# `database is locked`, and row 72184 was re-uploaded to the server every ~20s
# while the outbox grew. Ordered delivery plus a lost delete is a wedge exactly
# like a poison row, only louder on the server side.
_DURABLE_WRITE_BUSY_TIMEOUT_MS = 15000

# THE POISON-ROW RULE. Publication is strictly ordered — deliberately, so a
# provider event stream is never reordered — which means one permanently
# rejected row stops every row behind it, forever. Found live 2026-08-17: a
# single row had failed 2,520 times since 2026-08-13 with 409 `entry_mutated`
# and had blocked 3,709 rows for four days with nothing surfacing it.
#
# `entry_mutated` is definitionally terminal: the server already stores that
# stable event id with DIFFERENT bytes, so this envelope can never be accepted,
# and attempt 2,521 will fail exactly like attempt 1. Retrying a permanent
# rejection is not durability, it is a stall.
_TERMINAL_ERROR_CODES = frozenset({"entry_mutated"})

# Statuses that mean "the server understood and refused". They are quarantined
# only after a long retry run, because a proxy or a mid-deploy server can emit
# them transiently and dropping a row early would be real data loss.
_TERMINAL_STATUSES = frozenset({400, 409, 422})
_QUARANTINE_AFTER_ATTEMPTS = 25

_PROVIDER_CAPABILITIES: dict[BridgeProvider, dict[str, Any]] = {
    BridgeProvider.CLAUDE_CODE: {
        "event_mirror": True,
        "historical_import": True,
        "title_sync": True,
        "local_runtime": True,
        "native_resume": True,
        "participant_conversations": False,
        "limitations": [],
    },
    BridgeProvider.CODEX: {
        "event_mirror": True,
        "historical_import": False,
        "title_sync": False,
        "local_runtime": False,
        "native_resume": False,
        "participant_conversations": False,
        "limitations": [
            "Historical import, title sync, and local runtime are not implemented yet."
        ],
    },
    BridgeProvider.CURSOR: {
        "event_mirror": True,
        "historical_import": False,
        "title_sync": False,
        "local_runtime": False,
        "native_resume": False,
        "participant_conversations": False,
        "limitations": [
            "Capture is limited to events the Cursor host exposes; full historical host fidelity is not available."
        ],
    },
    BridgeProvider.VSCODE: {
        "event_mirror": False,
        "historical_import": False,
        "title_sync": False,
        "local_runtime": False,
        "native_resume": False,
        "participant_conversations": True,
        "limitations": [
            "Only AI Matrx @matrx participant conversations are available; unrelated VS Code chat history is outside the extension API boundary."
        ],
    },
}

_SOURCE_SQL = """CASE
    WHEN json_valid(envelope_json) THEN COALESCE(
        json_extract(envelope_json, '$.source_metadata.source_kind'),
        json_extract(envelope_json, '$.origin'),
        'unspecified'
    )
    ELSE 'unknown'
END"""


def _delivery_dimensions(request: BridgeRequest) -> tuple[str, str, str]:
    """Return bounded, non-identifying aggregation dimensions for status."""
    source = (
        request.source_metadata.source_kind
        if request.source_metadata is not None
        else request.origin.value
        if request.origin is not None
        else "unspecified"
    )
    return request.provider.value, request.action.value, source


def _delivery_lane_key(request: BridgeRequest) -> str:
    """Canonical local ordering lane for one logical provider session.

    Every action and subordinate stream sharing a real session deliberately
    shares a lane, so metadata cannot pass any transcript batch that may create
    its binding. Sessionless requests use action plus source as their identity,
    allowing unrelated operations to progress independently.
    """
    if request.provider_session_id is not None:
        identity = request.provider_session_id
        discriminator = "$session"
    else:
        identity = f"$action:{request.action.value}"
        discriminator = _delivery_dimensions(request)[2]
    return json.dumps(
        [
            request.provider.value,
            request.provider_project_key or "",
            identity,
            discriminator,
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _session_metadata_payload_digest(payload: dict[str, Any]) -> str:
    """Canonical digest shared with the Claude title reconciler's ledger."""
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _safe_delivery_error(raw_error: Any) -> dict[str, str] | None:
    """Reduce a persisted failure to a display-safe operational explanation.

    Server bodies can echo arbitrary request context. The status route must
    never turn the durable raw-event store into an accidental transcript or
    path disclosure, so no unrecognized error text leaves the engine.
    """
    if not isinstance(raw_error, str) or not raw_error:
        return None
    known = {
        "cloud_participation_disabled": (
            "cloud_participation_disabled",
            "Cloud delivery is disabled for this local development engine.",
        ),
        "no_active_user_jwt": (
            "sign_in_required",
            "Sign in to AI Matrx to deliver queued coding-session events.",
        ),
        "aidream_server_unconfigured": (
            "cloud_server_unavailable",
            "The AI Matrx cloud service is not configured for this app.",
        ),
    }
    if raw_error in known:
        code, message = known[raw_error]
        return {"code": code, "message": message}
    if "entry_mutated" in raw_error:
        return {
            "code": "entry_mutated",
            "message": "The cloud already has this event identity with different content.",
        }
    if "SHA-256 integrity check" in raw_error:
        return {
            "code": "local_integrity_failure",
            "message": "A queued event failed its local integrity check.",
        }
    if "not valid JSON" in raw_error or "no longer satisfies schema" in raw_error:
        return {
            "code": "invalid_local_envelope",
            "message": "A queued event no longer satisfies the bridge contract.",
        }
    if "bridge acknowledgement" in raw_error:
        return {
            "code": "invalid_cloud_acknowledgement",
            "message": "The cloud response did not prove that the queued event was stored.",
        }
    status_match = re.search(r"HTTP\s+(\d{3})", raw_error)
    if status_match:
        status_code = status_match.group(1)
        return {
            "code": f"cloud_http_{status_code}",
            "message": f"Cloud delivery returned HTTP {status_code}; the event remains local.",
        }
    return {
        "code": "cloud_delivery_failed",
        "message": "Cloud delivery failed; the event remains local and will be retried.",
    }


def _is_terminal_rejection(exc: Exception, attempts: int) -> bool:
    """True when retrying this exact envelope can never succeed.

    Offline is never terminal — the server has said nothing. A known-terminal
    error code is terminal immediately; a generic understood-and-refused status
    is terminal only after a long retry run.
    """
    if isinstance(exc, AIDreamOfflineError) or not isinstance(exc, AIDreamError):
        return False
    message = str(exc)
    if any(f'"{code}"' in message for code in _TERMINAL_ERROR_CODES):
        return True
    return exc.status in _TERMINAL_STATUSES and attempts >= _QUARANTINE_AFTER_ATTEMPTS


class BridgeMutationConflict(ValueError):
    """A provider reused a stable event identity with different content."""


def _canonical_envelope(request: BridgeRequest) -> tuple[dict[str, Any], str, str]:
    payload = request.model_dump(mode="json", exclude_none=True)
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return payload, serialized, digest


def _stable_delivery_key(request: BridgeRequest, envelope_digest: str) -> str | None:
    if request.action.value == "append_native":
        return hashlib.sha256(f"append_native:{envelope_digest}".encode()).hexdigest()
    hook = request.hook_event
    if hook is None or hook.stable_event_id is None:
        return None
    identity = json.dumps(
        [
            request.provider.value,
            request.provider_session_id,
            request.stream_key,
            hook.stable_event_id,
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _validate_upstream_acknowledgement(
    response: Any,
    request: BridgeRequest,
) -> None:
    """Prove a 2xx body durably accepted exactly this one hook event.

    A reverse proxy, stale server, or accidentally remounted route can return
    JSON with HTTP 2xx without committing the bridge entry. Deleting the local
    outbox row on that weak signal would turn a deployment mistake into data
    loss, so the response must satisfy the frozen BridgeResponse v1 receipt.
    """

    def _count(name: str) -> int:
        value = response.get(name) if isinstance(response, dict) else None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AIDreamError(502, f"bridge acknowledgement has invalid {name}")
        return value

    if not isinstance(response, dict):
        raise AIDreamError(502, "bridge acknowledgement is not a JSON object")
    expected = {
        "schema_version": 1,
        "action": request.action.value,
        "provider": request.provider.value,
    }
    hook_event = request.hook_event
    is_session_metadata = (
        request.action.value == "observe_hook"
        and hook_event is not None
        and hook_event.name == SESSION_METADATA_EVENT
    )
    if is_session_metadata:
        # A label update lands on an existing binding of EITHER fidelity, and
        # an unmirrored local session settles with accepted=0 and no session
        # identity — that is a durable "nothing to update here", not a failure
        # to retry forever.
        for field, value in expected.items():
            if response.get(field) != value:
                raise AIDreamError(
                    502,
                    f"bridge acknowledgement {field} did not match request",
                )
        accepted = _count("accepted")
        duplicates = _count("duplicates")
        if _count("conflicts") != 0:
            raise AIDreamError(
                502,
                "bridge acknowledgement did not account for every submitted entry",
            )
        if accepted == 0 and duplicates == 0:
            return
        if accepted + duplicates != 1:
            raise AIDreamError(
                502,
                "bridge acknowledgement did not account for every submitted entry",
            )
        if response.get("fidelity") not in {"native", "event_mirror"}:
            raise AIDreamError(502, "bridge acknowledgement has invalid fidelity")
        for field in ("session_id", "conversation_id"):
            try:
                UUID(str(response.get(field)))
            except (TypeError, ValueError, AttributeError) as exc:
                raise AIDreamError(
                    502,
                    f"bridge acknowledgement has invalid {field}",
                ) from exc
        return
    if request.action.value == "observe_hook":
        expected["fidelity"] = "event_mirror"
    for field, value in expected.items():
        if response.get(field) != value:
            raise AIDreamError(
                502,
                f"bridge acknowledgement {field} did not match request",
            )
    for field in ("session_id", "conversation_id"):
        try:
            UUID(str(response.get(field)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AIDreamError(
                502,
                f"bridge acknowledgement has invalid {field}",
            ) from exc
    accepted = _count("accepted")
    duplicates = _count("duplicates")
    conflicts = _count("conflicts")
    expected_count = (
        1 if request.action.value == "observe_hook" else len(request.entries)
    )
    if request.action.value == "append_native" and response.get("fidelity") not in {
        "native",
        "event_mirror",
    }:
        raise AIDreamError(502, "bridge acknowledgement has invalid import fidelity")
    if conflicts != 0 or accepted + duplicates != expected_count:
        raise AIDreamError(
            502,
            "bridge acknowledgement did not account for every submitted entry",
        )


class CodingSessionBridgeOutbox:
    """Persists first, then uploads the oldest envelope until acknowledged.

    Successful server acknowledgement deletes the local row. If aidream
    accepted a request but the response was lost, the unchanged persisted
    envelope is replayed after restart; server-side bridge idempotency makes
    that retry a no-op.
    """

    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        client: AIDreamClient | None = None,
        client_factory: Callable[[], AIDreamClient | None] = get_aidream_client,
        token_repo: TokenRepo | None = None,
        cloud_enabled: bool | None = None,
    ) -> None:
        self._db = db or get_db()
        self._client = client
        self._client_factory = client_factory
        self._tokens = token_repo or TokenRepo(self._db)
        if cloud_enabled is None:
            from app.config import CLOUD_PARTICIPATION_ENABLED

            cloud_enabled = CLOUD_PARTICIPATION_ENABLED
        self._cloud_enabled = cloud_enabled
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._sync_lock = asyncio.Lock()
        # Rows aidream has ALREADY accepted whose local delete has not yet
        # won the SQLite write lock. They are never uploaded again — the
        # publisher only retries their delete.
        self._delivered_undeleted: set[int] = set()

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start_background(self) -> None:
        if self.active:
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._publisher_loop(), name="coding-session-bridge-outbox"
        )

    async def stop_background(self) -> None:
        self._stopping = True
        self._wake.set()
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    async def enqueue(self, request: BridgeRequest) -> LocalBridgeReceipt:
        """Commit an exact envelope before returning a receipt."""
        _payload, serialized, digest = _canonical_envelope(request)
        dedupe_key = _stable_delivery_key(request, digest)
        outbox_id, duplicate = await self._commit_enqueue(
            dedupe_key=dedupe_key,
            serialized=serialized,
            digest=digest,
            dimensions=_delivery_dimensions(request),
            lane_key=_delivery_lane_key(request),
        )

        pending = await self.pending_count()
        self.wake()
        return LocalBridgeReceipt(
            receipt_id=outbox_id,
            duplicate=duplicate,
            pending=pending,
        )

    async def enqueue_many(self, requests: list[BridgeRequest]) -> dict[str, Any]:
        """Atomically persist a bounded import plan before reporting success."""
        if not requests:
            raise ValueError("at least one bridge request is required")
        prepared: list[tuple[str | None, str, str, tuple[str, str, str], str]] = []
        for request in requests:
            _payload, serialized, digest = _canonical_envelope(request)
            prepared.append(
                (
                    _stable_delivery_key(request, digest),
                    serialized,
                    digest,
                    _delivery_dimensions(request),
                    _delivery_lane_key(request),
                )
            )
        ids, duplicates_by_index = await self._commit_enqueue_many(prepared)
        duplicates = sum(duplicates_by_index)
        pending = await self.pending_count()
        self.wake()
        return {
            "queued": len(ids) - duplicates,
            "duplicate_pending": duplicates,
            "duplicates_by_index": duplicates_by_index,
            "receipt_ids": ids,
            "pending": pending,
        }

    async def _commit_enqueue_many(
        self,
        prepared: list[tuple[str | None, str, str, tuple[str, str, str], str]],
    ) -> tuple[list[int], list[bool]]:
        ids: list[int] = []
        duplicates_by_index: list[bool] = []
        async with aiosqlite.connect(str(self._db.path)) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA busy_timeout=5000")
            await connection.execute("PRAGMA synchronous=FULL")
            await connection.execute("BEGIN IMMEDIATE")
            try:
                for dedupe_key, serialized, digest, dimensions, lane_key in prepared:
                    if dedupe_key is None:
                        cursor = await connection.execute(
                            """INSERT INTO coding_session_bridge_outbox (
                                 dedupe_key, envelope_json, envelope_sha256, lane_key
                               ) VALUES (?, ?, ?, ?)""",
                            (None, serialized, digest, lane_key),
                        )
                    else:
                        cursor = await connection.execute(
                            """INSERT INTO coding_session_bridge_outbox (
                                 dedupe_key, envelope_json, envelope_sha256, lane_key
                               ) VALUES (?, ?, ?, ?)
                               ON CONFLICT(dedupe_key) WHERE dedupe_key IS NOT NULL
                               DO NOTHING""",
                            (dedupe_key, serialized, digest, lane_key),
                        )
                    if cursor.rowcount == 1:
                        receipt_id = int(cursor.lastrowid)
                        ids.append(receipt_id)
                        await self._record_enqueue_activity(
                            connection,
                            dimensions=dimensions,
                            receipt_id=receipt_id,
                        )
                        duplicates_by_index.append(False)
                        continue
                    existing_cursor = await connection.execute(
                        """SELECT id, envelope_sha256
                           FROM coding_session_bridge_outbox
                           WHERE dedupe_key = ?""",
                        (dedupe_key,),
                    )
                    row = await existing_cursor.fetchone()
                    if row is None or str(row["envelope_sha256"]) != digest:
                        raise BridgeMutationConflict(
                            "stable import identity was reused with different bytes"
                        )
                    ids.append(int(row["id"]))
                    duplicates_by_index.append(True)
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return ids, duplicates_by_index

    async def _commit_enqueue(
        self,
        *,
        dedupe_key: str | None,
        serialized: str,
        digest: str,
        dimensions: tuple[str, str, str],
        lane_key: str,
    ) -> tuple[int, bool]:
        """Use a private, FULL-sync transaction for the durable-ack boundary.

        The application's general repositories intentionally share one
        aiosqlite connection. Their multi-step writes can therefore commit or
        roll back one another when coroutines interleave. A hook 202 has a
        stronger promise: its row must already be independently durable. This
        short connection targets the same SQLite database (not a second
        store), takes one immediate transaction, fsyncs its WAL commit, and
        closes before the HTTP response is assembled.
        """

        async with aiosqlite.connect(str(self._db.path)) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA busy_timeout=5000")
            await connection.execute("PRAGMA synchronous=FULL")
            await connection.execute("BEGIN IMMEDIATE")
            try:
                if dedupe_key is None:
                    cursor = await connection.execute(
                        """INSERT INTO coding_session_bridge_outbox (
                             dedupe_key, envelope_json, envelope_sha256, lane_key
                           ) VALUES (?, ?, ?, ?)""",
                        (None, serialized, digest, lane_key),
                    )
                else:
                    cursor = await connection.execute(
                        """INSERT INTO coding_session_bridge_outbox (
                             dedupe_key, envelope_json, envelope_sha256, lane_key
                           ) VALUES (?, ?, ?, ?)
                           ON CONFLICT(dedupe_key) WHERE dedupe_key IS NOT NULL
                           DO NOTHING""",
                        (dedupe_key, serialized, digest, lane_key),
                    )
                if cursor.rowcount == 1:
                    outbox_id = int(cursor.lastrowid)
                    duplicate = False
                    await self._record_enqueue_activity(
                        connection,
                        dimensions=dimensions,
                        receipt_id=outbox_id,
                    )
                else:
                    existing_cursor = await connection.execute(
                        """SELECT id, envelope_sha256
                           FROM coding_session_bridge_outbox
                           WHERE dedupe_key = ?""",
                        (dedupe_key,),
                    )
                    row = await existing_cursor.fetchone()
                    if row is None:
                        raise RuntimeError(
                            "stable bridge event disappeared inside its write transaction"
                        )
                    if str(row["envelope_sha256"]) != digest:
                        raise BridgeMutationConflict(
                            "stable hook event identity was reused with a different envelope"
                        )
                    outbox_id = int(row["id"])
                    duplicate = True
                await connection.commit()
                return outbox_id, duplicate
            except BaseException:
                await connection.rollback()
                raise

    @staticmethod
    async def _record_enqueue_activity(
        connection: aiosqlite.Connection,
        *,
        dimensions: tuple[str, str, str],
        receipt_id: int,
    ) -> None:
        """Update the bounded activity aggregate inside the enqueue commit."""
        provider, action, source = dimensions
        await connection.execute(
            """INSERT INTO coding_session_bridge_delivery_activity (
                   provider, action, source,
                   last_enqueued_at, last_enqueued_receipt_id, updated_at
               ) VALUES (?, ?, ?, datetime('now'), ?, datetime('now'))
               ON CONFLICT(provider, action, source) DO UPDATE SET
                   last_enqueued_at=excluded.last_enqueued_at,
                   last_enqueued_receipt_id=excluded.last_enqueued_receipt_id,
                   updated_at=excluded.updated_at""",
            (provider, action, source, receipt_id),
        )

    async def pending_count(self) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS count FROM coding_session_bridge_outbox"
        )
        return int(row["count"]) if row else 0

    async def pending_native_import_count(self) -> int:
        row = await self._db.fetchone(
            """SELECT COUNT(*) AS count
               FROM coding_session_bridge_outbox
               WHERE json_valid(envelope_json)
                 AND json_extract(envelope_json, '$.action') = 'append_native'
                 AND json_extract(
                       envelope_json, '$.source_metadata.source_kind'
                     ) = 'claude_local_jsonl'"""
        )
        return int(row["count"]) if row else 0

    async def oldest_native_import(self) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            """SELECT id, attempts, next_attempt_at, last_error, created_at
               FROM coding_session_bridge_outbox
               WHERE json_valid(envelope_json)
                 AND json_extract(envelope_json, '$.action') = 'append_native'
                 AND json_extract(
                       envelope_json, '$.source_metadata.source_kind'
                     ) = 'claude_local_jsonl'
               ORDER BY id LIMIT 1"""
        )
        if row is None:
            return None
        result = dict(row)
        safe_error = _safe_delivery_error(result.pop("last_error", None))
        result["error"] = safe_error
        # Compatibility for the existing history card; this is the same
        # redacted object, never the persisted raw server text.
        result["last_error"] = safe_error
        return result

    async def quarantined_native_import_count(self) -> int:
        row = await self._db.fetchone(
            """SELECT COUNT(*) AS count
               FROM coding_session_bridge_quarantine
               WHERE json_valid(envelope_json)
                 AND json_extract(envelope_json, '$.action') = 'append_native'
                 AND json_extract(
                       envelope_json, '$.source_metadata.source_kind'
                     ) = 'claude_local_jsonl'"""
        )
        return int(row["count"]) if row else 0

    async def retry_pending_native_imports(self) -> dict[str, int]:
        """Make queued Claude-history copies immediately eligible for retry."""
        async with aiosqlite.connect(str(self._db.path)) as connection:
            await connection.execute("PRAGMA busy_timeout=5000")
            await connection.execute("PRAGMA synchronous=FULL")
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute(
                    """UPDATE coding_session_bridge_outbox
                       SET next_attempt_at=0, last_error=NULL,
                           updated_at=datetime('now')
                       WHERE json_valid(envelope_json)
                         AND json_extract(envelope_json, '$.action') = 'append_native'
                         AND json_extract(
                               envelope_json, '$.source_metadata.source_kind'
                             ) = 'claude_local_jsonl'"""
                )
                retried = max(0, int(cursor.rowcount))
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        self.wake()
        return {"retried": retried, "pending": await self.pending_count()}

    async def discard_pending_native_imports(self) -> dict[str, int]:
        """Delete only explicitly queued Claude-history copies, never hook rows."""
        async with aiosqlite.connect(str(self._db.path)) as connection:
            await connection.execute("PRAGMA busy_timeout=5000")
            await connection.execute("PRAGMA synchronous=FULL")
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute(
                    """DELETE FROM coding_session_bridge_outbox
                       WHERE json_valid(envelope_json)
                         AND json_extract(envelope_json, '$.action') = 'append_native'
                         AND json_extract(
                               envelope_json, '$.source_metadata.source_kind'
                             ) = 'claude_local_jsonl'"""
                )
                discarded = max(0, int(cursor.rowcount))
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return {"discarded": discarded, "pending": await self.pending_count()}

    async def _queue_breakdown(self, table: str) -> dict[str, Any]:
        if table not in {
            "coding_session_bridge_outbox",
            "coding_session_bridge_quarantine",
        }:
            raise ValueError("unsupported coding-session delivery table")
        rows = await self._db.fetchall(
            f"""SELECT
                    CASE WHEN json_valid(envelope_json)
                         THEN COALESCE(json_extract(envelope_json, '$.provider'), 'unknown')
                         ELSE 'unknown' END AS provider,
                    CASE WHEN json_valid(envelope_json)
                         THEN COALESCE(json_extract(envelope_json, '$.action'), 'unknown')
                         ELSE 'unknown' END AS action,
                    {_SOURCE_SQL} AS source,
                    COUNT(*) AS count
                FROM {table}
                GROUP BY provider, action, source"""
        )
        by_provider = {provider.value: 0 for provider in BridgeProvider}
        by_action: dict[str, int] = {}
        by_source: dict[str, int] = {}
        dimensions: list[dict[str, Any]] = []
        total = 0
        for row in rows:
            provider = str(row["provider"])
            action = str(row["action"])
            source = str(row["source"])
            count = int(row["count"])
            total += count
            by_provider[provider] = by_provider.get(provider, 0) + count
            by_action[action] = by_action.get(action, 0) + count
            by_source[source] = by_source.get(source, 0) + count
            dimensions.append(
                {
                    "provider": provider,
                    "action": action,
                    "source": source,
                    "count": count,
                }
            )
        return {
            "total": total,
            "by_provider": by_provider,
            "by_action": by_action,
            "by_source": by_source,
            "dimensions": dimensions,
        }

    @staticmethod
    def _enqueue_summary(row: Any) -> dict[str, Any] | None:
        if row["last_enqueued_at"] is None:
            return None
        return {
            "receipt_id": int(row["last_enqueued_receipt_id"]),
            "at": str(row["last_enqueued_at"]),
            "provider": str(row["provider"]),
            "action": str(row["action"]),
            "source": str(row["source"]),
        }

    @staticmethod
    def _acknowledgement_summary(row: Any) -> dict[str, Any] | None:
        if row["last_acknowledged_at"] is None:
            return None
        return {
            "receipt_id": int(row["last_acknowledged_receipt_id"]),
            "at": str(row["last_acknowledged_at"]),
            "provider": str(row["provider"]),
            "action": str(row["action"]),
            "source": str(row["source"]),
            "accepted": int(row["last_acknowledged_accepted"] or 0),
            "duplicates": int(row["last_acknowledged_duplicates"] or 0),
            "fidelity": (
                str(row["last_acknowledged_fidelity"])
                if row["last_acknowledged_fidelity"] is not None
                else None
            ),
        }

    async def delivery_status(self) -> dict[str, Any]:
        """Return provider-neutral queue truth without any stored envelope data."""
        pending = await self._queue_breakdown("coding_session_bridge_outbox")
        quarantine = await self._queue_breakdown("coding_session_bridge_quarantine")
        activity_rows = await self._db.fetchall(
            """SELECT * FROM coding_session_bridge_delivery_activity
               ORDER BY provider, action, source"""
        )

        providers: dict[str, dict[str, Any]] = {
            provider.value: {
                "capabilities": dict(_PROVIDER_CAPABILITIES[provider]),
                "pending": 0,
                "quarantined": 0,
                "by_action": {},
                "by_source": {},
                "last_enqueue": None,
                "last_acknowledgement": None,
            }
            for provider in BridgeProvider
        }

        def _dimension_cell(container: dict[str, Any], key: str) -> dict[str, Any]:
            return container.setdefault(
                key,
                {
                    "pending": 0,
                    "quarantined": 0,
                    "last_enqueue": None,
                    "last_acknowledgement": None,
                },
            )

        def _set_latest(
            container: dict[str, Any], key: str, summary: dict[str, Any]
        ) -> None:
            current = container.get(key)
            if current is None or summary["receipt_id"] > current["receipt_id"]:
                container[key] = summary

        for state_name, breakdown in (
            ("pending", pending),
            ("quarantined", quarantine),
        ):
            for item in breakdown.pop("dimensions"):
                provider_state = providers.get(item["provider"])
                if provider_state is None:
                    continue
                count = int(item["count"])
                provider_state[state_name] += count
                _dimension_cell(provider_state["by_action"], item["action"])[
                    state_name
                ] += count
                _dimension_cell(provider_state["by_source"], item["source"])[
                    state_name
                ] += count

        last_enqueue: dict[str, Any] | None = None
        last_acknowledgement: dict[str, Any] | None = None
        for row in activity_rows:
            provider_state = providers.get(str(row["provider"]))
            if provider_state is None:
                continue
            enqueue = self._enqueue_summary(row)
            acknowledgement = self._acknowledgement_summary(row)
            action_state = _dimension_cell(
                provider_state["by_action"], str(row["action"])
            )
            source_state = _dimension_cell(
                provider_state["by_source"], str(row["source"])
            )
            if enqueue is not None:
                _set_latest(action_state, "last_enqueue", enqueue)
                _set_latest(source_state, "last_enqueue", enqueue)
                if (
                    provider_state["last_enqueue"] is None
                    or enqueue["receipt_id"]
                    > provider_state["last_enqueue"]["receipt_id"]
                ):
                    provider_state["last_enqueue"] = enqueue
                if (
                    last_enqueue is None
                    or enqueue["receipt_id"] > last_enqueue["receipt_id"]
                ):
                    last_enqueue = enqueue
            if acknowledgement is not None:
                _set_latest(action_state, "last_acknowledgement", acknowledgement)
                _set_latest(source_state, "last_acknowledgement", acknowledgement)
                if (
                    provider_state["last_acknowledgement"] is None
                    or acknowledgement["receipt_id"]
                    > provider_state["last_acknowledgement"]["receipt_id"]
                ):
                    provider_state["last_acknowledgement"] = acknowledgement
                if (
                    last_acknowledgement is None
                    or acknowledgement["receipt_id"]
                    > last_acknowledgement["receipt_id"]
                ):
                    last_acknowledgement = acknowledgement

        # The oldest lane head currently waiting for its retry window. Ready
        # rows are ordinary queued work, not blockers, and later rows in the
        # same lane are intentionally hidden behind this head.
        head = await self._db.fetchone(
            f"""SELECT id, attempts, next_attempt_at, last_error, created_at,
                       CASE WHEN json_valid(envelope_json)
                            THEN COALESCE(json_extract(envelope_json, '$.provider'), 'unknown')
                            ELSE 'unknown' END AS provider,
                       CASE WHEN json_valid(envelope_json)
                            THEN COALESCE(json_extract(envelope_json, '$.action'), 'unknown')
                            ELSE 'unknown' END AS action,
                       {_SOURCE_SQL} AS source
                FROM coding_session_bridge_outbox AS current
                WHERE current.next_attempt_at > ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM coding_session_bridge_outbox AS prior
                      WHERE prior.lane_key = current.lane_key
                        AND prior.id < current.id
                  )
                ORDER BY current.id LIMIT 1""",
            (time.time(),),
        )
        head_blocker = None
        if head is not None:
            next_attempt_at = float(head["next_attempt_at"] or 0)
            head_blocker = {
                "receipt_id": int(head["id"]),
                "provider": str(head["provider"]),
                "action": str(head["action"]),
                "source": str(head["source"]),
                "attempts": int(head["attempts"]),
                "next_attempt_at": next_attempt_at,
                "retry_in_seconds": max(0.0, next_attempt_at - time.time()),
                "created_at": str(head["created_at"]),
                "error": _safe_delivery_error(head["last_error"]),
            }

        return {
            "schema_version": 2,
            "publisher": {
                "active": self.active,
                "cloud_enabled": self._cloud_enabled,
                "server_path": f"/api{_SERVER_PATH}",
            },
            "pending": pending,
            "quarantine": quarantine,
            "providers": providers,
            "head_blocker": head_blocker,
            "last_enqueue": last_enqueue,
            "last_acknowledgement": last_acknowledgement,
        }

    async def status(self) -> dict[str, Any]:
        """Compatibility status for existing callers plus the v2 facade."""
        delivery = await self.delivery_status()
        return {
            "active": self.active,
            "cloud_enabled": self._cloud_enabled,
            "pending": delivery["pending"]["total"],
            "oldest": delivery["head_blocker"],
            # Non-zero means real events are permanently NOT in the platform.
            "quarantined": delivery["quarantine"]["total"],
            "server_path": f"/api{_SERVER_PATH}",
            "delivery": delivery,
        }

    async def sync_pending(self, *, limit: int = _MAX_BATCH) -> dict[str, Any]:
        """Publish eligible lane heads while preserving order inside each lane."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        async with self._sync_lock:
            if not self._cloud_enabled:
                await self._defer_head("cloud_participation_disabled", increment=False)
                return {
                    "sent": 0,
                    "failed": 0,
                    "blocked": "cloud_participation_disabled",
                }

            token_row = await self._tokens.get()
            if (
                not token_row
                or not token_row.get("access_token")
                or not token_row.get("user_id")
                or self._tokens.is_expired(token_row)
            ):
                await self._defer_head("no_active_user_jwt", increment=False)
                return {"sent": 0, "failed": 0, "blocked": "no_active_user_jwt"}

            client = self._client or self._client_factory()
            if client is None:
                await self._defer_head("aidream_server_unconfigured", increment=False)
                return {
                    "sent": 0,
                    "failed": 0,
                    "blocked": "aidream_server_unconfigured",
                }

            # A row aidream already accepted must never be uploaded twice.
            # Clear any that lost the write lock last tick before selecting
            # new work; while one is still stuck it also blocks its lane, so
            # skipping it here is order-preserving, not order-breaking.
            await self._drain_delivered_undeleted()

            sent = 0
            failed = 0
            processed = 0
            while processed < limit:
                row = await self._db.fetchone(
                    """SELECT o.id, o.envelope_json, o.envelope_sha256,
                              o.attempts, o.next_attempt_at, o.lane_key
                       FROM coding_session_bridge_outbox AS o
                       WHERE o.next_attempt_at <= ?
                         AND NOT EXISTS (
                             SELECT 1
                             FROM coding_session_bridge_outbox AS prior
                             WHERE prior.lane_key = o.lane_key
                               AND prior.id < o.id
                         )
                       ORDER BY o.id
                       LIMIT 1""",
                    (time.time(),),
                )
                if row is None:
                    break
                if int(row["id"]) in self._delivered_undeleted:
                    # Delivered, delete still losing the lock. Re-sending it
                    # would duplicate an accepted event on the server for no
                    # local benefit.
                    break
                processed += 1
                try:
                    serialized = str(row["envelope_json"])
                    actual_digest = hashlib.sha256(
                        serialized.encode("utf-8")
                    ).hexdigest()
                    if actual_digest != str(row["envelope_sha256"]):
                        raise AIDreamError(
                            500,
                            "persisted bridge envelope failed its SHA-256 integrity check",
                        )
                    try:
                        payload = json.loads(serialized)
                    except json.JSONDecodeError as exc:
                        raise AIDreamError(
                            500,
                            "persisted bridge envelope is not valid JSON",
                        ) from exc
                    try:
                        persisted_request = BridgeRequest.model_validate(payload)
                    except ValidationError as exc:
                        raise AIDreamError(
                            500,
                            "persisted bridge envelope no longer satisfies schema v1",
                        ) from exc
                    response = await client.post(
                        _SERVER_PATH,
                        payload,
                        jwt=str(token_row["access_token"]),
                        timeout=30.0,
                    )
                    _validate_upstream_acknowledgement(response, persisted_request)
                except (AIDreamOfflineError, AIDreamError) as exc:
                    if _is_terminal_rejection(exc, int(row["attempts"])):
                        try:
                            await self._quarantine_head(row, exc)
                        except Exception:
                            logger.exception(
                                "[coding_session_bridge] could not quarantine "
                                "id=%s — retrying next tick, envelope intact",
                                int(row["id"]),
                            )
                            failed += 1
                            break
                        # The next row in this lane, plus every unrelated lane,
                        # can now advance.
                        continue
                    try:
                        await self._record_failure(
                            int(row["id"]), int(row["attempts"]), exc
                        )
                    except Exception:
                        # A write failure INSIDE an exception handler is how
                        # this tick died on v1.4.37. Never let bookkeeping
                        # about a failure become a worse failure.
                        logger.exception(
                            "[coding_session_bridge] could not record the "
                            "failure for id=%s — backing off this tick",
                            int(row["id"]),
                        )
                        failed += 1
                        break
                    failed += 1
                    if isinstance(exc, AIDreamOfflineError):
                        # Network reachability is publisher-wide, not a poison
                        # row; burning every lane's retry counter adds no value.
                        break
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # An UNEXPECTED failure must degrade to a deferred row, not
                    # a dead publisher. Three separate incidents (v1.4.34's ack
                    # write, v1.4.35's delete, and a raw ssl.SSLError escaping
                    # the aidream client on 2026-08-19) each stopped the whole
                    # tick, and because the row never recorded an attempt it
                    # also got no backoff — every lane stalled at attempts=0
                    # with nothing in the outbox explaining why. Record it,
                    # scream, and let the next lane through.
                    logger.exception(
                        "[coding_session_bridge] unexpected failure on id=%s — "
                        "deferring the row instead of stopping the publisher",
                        int(row["id"]),
                    )
                    try:
                        await self._record_failure(
                            int(row["id"]), int(row["attempts"]), exc
                        )
                    except Exception:
                        logger.exception(
                            "[coding_session_bridge] could not even defer "
                            "id=%s", int(row["id"])
                        )
                        break
                    failed += 1
                    continue

                # The upstream POST succeeded — from here on the envelope is
                # DELIVERED. Retiring the row is the ONLY thing that stops the
                # publisher sending it again, so it runs on the same durable
                # boundary the hook ingress uses (see
                # _DURABLE_WRITE_BUSY_TIMEOUT_MS).
                if await self._retire_delivered_row(
                    outbox_id=int(row["id"]),
                    request=persisted_request,
                    response=response,
                ):
                    sent += 1
                    continue

                # The delete lost the write lock. The row is DELIVERED, so it
                # must never be uploaded again — remember it and stop this
                # tick. The next tick retries the delete before any upload.
                self._delivered_undeleted.add(int(row["id"]))
                failed += 1
                break
            return {"sent": sent, "failed": failed, "blocked": None}

    async def _drain_delivered_undeleted(self) -> None:
        """Retry deletes for rows aidream already accepted. Never re-uploads."""
        for outbox_id in sorted(self._delivered_undeleted):
            if await self._delete_delivered_row(outbox_id):
                self._delivered_undeleted.discard(outbox_id)
            else:
                # Still locked. Leave the rest for the next tick rather than
                # burning it against a write lock we are plainly not winning.
                return

    async def _retire_delivered_row(
        self,
        *,
        outbox_id: int,
        request: BridgeRequest,
        response: dict[str, Any],
    ) -> bool:
        """Record the acknowledgement and delete the row in one durable write.

        Returns True once the row is gone. The acknowledgement summary stays
        best-effort RELATIVE TO THE DELETE: if the combined transaction fails,
        a delete-only transaction is attempted, so an ack-write problem can
        never keep a delivered envelope in the queue.
        """
        try:
            await self._commit_delivery_retirement(
                outbox_id=outbox_id, request=request, response=response
            )
            return True
        except Exception:
            logger.warning(
                "[coding_session_bridge] delivered id=%s could not be retired "
                "with its acknowledgement summary — retrying delete alone",
                outbox_id,
                exc_info=True,
            )
        return await self._delete_delivered_row(outbox_id)

    async def _durable_writes(self, statements: list[tuple[str, tuple]]) -> None:
        """Run outbox state changes on the durable boundary, not the shared one.

        EVERY mutation of the outbox — enqueue, retire, defer, quarantine —
        goes through a private BEGIN IMMEDIATE transaction. The shared
        aiosqlite connection is contended by every other coroutine in the
        engine, and a queue-state write that loses that race does not merely
        fail: it fails INSIDE an exception handler and takes the publisher
        with it (live 2026-08-19 on v1.4.37, `_record_failure` raising
        `database is locked` out of `except AIDreamOfflineError`).
        """
        async with aiosqlite.connect(str(self._db.path)) as connection:
            await connection.execute(
                f"PRAGMA busy_timeout={_DURABLE_WRITE_BUSY_TIMEOUT_MS}"
            )
            await connection.execute("PRAGMA synchronous=FULL")
            await connection.execute("BEGIN IMMEDIATE")
            try:
                for sql, params in statements:
                    await connection.execute(sql, params)
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def _delete_delivered_row(self, outbox_id: int) -> bool:
        try:
            async with aiosqlite.connect(str(self._db.path)) as connection:
                await connection.execute(
                    f"PRAGMA busy_timeout={_DURABLE_WRITE_BUSY_TIMEOUT_MS}"
                )
                await connection.execute("PRAGMA synchronous=FULL")
                await connection.execute("BEGIN IMMEDIATE")
                try:
                    await connection.execute(
                        "DELETE FROM coding_session_bridge_outbox WHERE id = ?",
                        (outbox_id,),
                    )
                    await connection.commit()
                except BaseException:
                    await connection.rollback()
                    raise
            return True
        except Exception:
            logger.error(
                "[coding_session_bridge] could not delete DELIVERED outbox row "
                "id=%s — it stays queued but will NOT be re-uploaded; the "
                "delete is retried next tick",
                outbox_id,
                exc_info=True,
            )
            return False

    async def _commit_delivery_retirement(
        self,
        *,
        outbox_id: int,
        request: BridgeRequest,
        response: dict[str, Any],
    ) -> None:
        """One private BEGIN IMMEDIATE transaction: ack summary + delete."""
        async with aiosqlite.connect(str(self._db.path)) as connection:
            await connection.execute(
                f"PRAGMA busy_timeout={_DURABLE_WRITE_BUSY_TIMEOUT_MS}"
            )
            await connection.execute("PRAGMA synchronous=FULL")
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await self._write_delivery_acknowledgement(
                    connection,
                    receipt_id=outbox_id,
                    request=request,
                    response=response,
                )
                await connection.execute(
                    "DELETE FROM coding_session_bridge_outbox WHERE id = ?",
                    (outbox_id,),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    @staticmethod
    async def _write_delivery_acknowledgement(
        connection: aiosqlite.Connection,
        *,
        receipt_id: int,
        request: BridgeRequest,
        response: dict[str, Any],
    ) -> None:
        """Persist a bounded proof summary; never retain the server body.

        Runs inside the caller's transaction so the acknowledgement and the
        outbox delete land together or not at all.
        """
        provider, action, source = _delivery_dimensions(request)
        accepted = int(response.get("accepted", 0))
        duplicates = int(response.get("duplicates", 0))
        fidelity_value = response.get("fidelity")
        fidelity = str(fidelity_value) if fidelity_value is not None else None
        await connection.execute(
            """INSERT INTO coding_session_bridge_delivery_activity (
                   provider, action, source,
                   last_acknowledged_at, last_acknowledged_receipt_id,
                   last_acknowledged_accepted, last_acknowledged_duplicates,
                   last_acknowledged_fidelity, acknowledged_envelopes, updated_at
               ) VALUES (?, ?, ?, datetime('now'), ?, ?, ?, ?, 1, datetime('now'))
               ON CONFLICT(provider, action, source) DO UPDATE SET
                   last_acknowledged_at=excluded.last_acknowledged_at,
                   last_acknowledged_receipt_id=excluded.last_acknowledged_receipt_id,
                   last_acknowledged_accepted=excluded.last_acknowledged_accepted,
                   last_acknowledged_duplicates=excluded.last_acknowledged_duplicates,
                   last_acknowledged_fidelity=excluded.last_acknowledged_fidelity,
                   acknowledged_envelopes=
                       coding_session_bridge_delivery_activity.acknowledged_envelopes + 1,
                   updated_at=excluded.updated_at""",
            (
                provider,
                action,
                source,
                receipt_id,
                accepted,
                duplicates,
                fidelity,
            ),
        )
        hook = request.hook_event
        if (
            request.provider is BridgeProvider.CLAUDE_CODE
            and request.provider_session_id is not None
            and hook is not None
            and hook.name == SESSION_METADATA_EVENT
        ):
            # This ledger means cloud-acknowledged, not merely locally queued.
            # Keeping it in the same transaction as outbox deletion prevents a
            # failed label from being mistaken for a synchronized one.
            await connection.execute(
                """INSERT INTO claude_session_metadata_sent
                       (provider_session_id, payload_sha256, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(provider_session_id) DO UPDATE SET
                       payload_sha256=excluded.payload_sha256,
                       updated_at=excluded.updated_at""",
                (
                    request.provider_session_id,
                    _session_metadata_payload_digest(dict(hook.payload)),
                ),
            )

    async def _quarantine_head(self, row: Any, exc: Exception) -> None:
        """Move a permanently-rejected row aside so the queue can advance.

        The envelope is PRESERVED, never dropped — zero data loss is this
        outbox's contract, and a row the server refuses is exactly the row a
        human will want to look at. Screams, because a quarantine means real
        events are not in the platform and never will be without repair.
        """
        status_code = getattr(exc, "status", None)
        # Copy-then-delete must be ATOMIC, or a crash between them loses the
        # envelope outright — the one thing this outbox promises never happens.
        await self._durable_writes(
            [
                (
                    """INSERT OR REPLACE INTO coding_session_bridge_quarantine
                           (id, envelope_json, envelope_sha256, attempts,
                            http_status, last_error, original_created_at,
                            quarantined_at)
                       SELECT id, envelope_json, envelope_sha256, attempts, ?, ?,
                              created_at, datetime('now')
                       FROM coding_session_bridge_outbox WHERE id = ?""",
                    (status_code, str(exc)[:1000], int(row["id"])),
                ),
                (
                    "DELETE FROM coding_session_bridge_outbox WHERE id = ?",
                    (int(row["id"]),),
                ),
            ]
        )
        logger.error(
            "[coding_session_bridge] QUARANTINED outbox id=%s after %s attempts "
            "(HTTP %s) — the server permanently refuses this envelope, so these "
            "events will NEVER reach AI Matrx. The row is preserved in "
            "coding_session_bridge_quarantine. Later rows in its delivery lane "
            "were blocked until now: %s",
            int(row["id"]),
            int(row["attempts"]),
            status_code,
            exc,
        )

    async def quarantined_count(self) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS c FROM coding_session_bridge_quarantine"
        )
        return int(row["c"]) if row else 0

    async def _defer_head(self, reason: str, *, increment: bool) -> None:
        row = await self._db.fetchone(
            "SELECT id, attempts FROM coding_session_bridge_outbox ORDER BY id LIMIT 1"
        )
        if row is None:
            return
        attempts = int(row["attempts"]) + (1 if increment else 0)
        await self._durable_writes(
            [
                (
                    """UPDATE coding_session_bridge_outbox
                       SET attempts=?, last_error=?, next_attempt_at=?,
                           updated_at=datetime('now') WHERE id=?""",
                    (
                        attempts,
                        reason[:1000],
                        time.time() + _PUBLISH_INTERVAL_SECONDS,
                        int(row["id"]),
                    ),
                )
            ]
        )

    async def _record_failure(
        self, outbox_id: int, prior_attempts: int, exc: Exception
    ) -> None:
        attempts = prior_attempts + 1
        backoff = min(_MAX_BACKOFF_SECONDS, float(2 ** min(attempts, 6)))
        await self._durable_writes(
            [
                (
                    """UPDATE coding_session_bridge_outbox
                       SET attempts=?, last_error=?, next_attempt_at=?,
                           updated_at=datetime('now') WHERE id=?""",
                    (attempts, str(exc)[:1000], time.time() + backoff, outbox_id),
                )
            ]
        )
        logger.warning(
            "[coding_session_bridge] upload deferred id=%s attempt=%s: %s",
            outbox_id,
            attempts,
            exc,
        )

    async def _publisher_loop(self) -> None:
        while not self._stopping:
            # Consume the wake signal before work. An enqueue that lands while
            # sync_pending is running then remains set and triggers the next
            # pass immediately instead of being cleared and delayed 15s.
            self._wake.clear()
            try:
                await self.sync_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[coding_session_bridge] publisher tick failed")
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=_PUBLISH_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass


_instance: CodingSessionBridgeOutbox | None = None


def get_coding_session_bridge_outbox() -> CodingSessionBridgeOutbox:
    global _instance
    if _instance is None:
        _instance = CodingSessionBridgeOutbox()
    return _instance


__all__ = [
    "BridgeMutationConflict",
    "CodingSessionBridgeOutbox",
    "get_coding_session_bridge_outbox",
]
