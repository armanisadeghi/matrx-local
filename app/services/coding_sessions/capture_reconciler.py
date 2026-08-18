"""Backfill Claude Code sessions the hook path failed to deliver.

**The hole this closes.** Every Claude Code hook rides the plugin's
already-connected MCP session, an MCP hook can never initiate OAuth, and Claude
treats a hook failure as **non-blocking**. So when that connection drops or its
authorization lapses, the coding session keeps running and mirrors *nothing* —
permanently, silently, recoverable only by a human noticing and running
``/mcp``. On 2026-08-16 that cost 23.5 hours of capture and was noticed only
because timestamps looked wrong.

**Why this is not a second transport.** Nothing here is new. Claude's own local
JSONL is already the source of the explicit history import; that import already
enqueues ``append_native`` batches into the durable
``coding_session_bridge_outbox``; that outbox already delivers with server-side
idempotency; and ``GET /coding-sessions/sessions`` already answers with exactly
which sessions the platform holds. This module only runs the diff between those
two on a timer instead of waiting for a human to press a button. The delivery
path, the byte-hashing, the account identity rules, and the conflict rules are
the import's, unchanged.

**THE ERA RULE — the consent boundary, and it is load-bearing.** Explicit
history import exists because a user's local transcripts may predate AI Matrx
entirely, and uploading those is their decision. But once a user has installed
the plugin and mirrored anything, continuous automatic capture of Claude
sessions is precisely the behaviour they opted into — that is what the hooks do
on every prompt, with no per-session consent. So this reconciler backfills ONLY
sessions whose local activity falls at or after the owner's earliest existing
binding: the era in which mirroring was already running and simply failed.

Two consequences that are features, not gaps:

- **An owner with no bindings at all gets nothing.** There is no era, so a
  first-time user's entire history is never swept up automatically. The
  explicit ``Review local history`` door remains the only way in.
- **Pre-install sessions stay local forever**, however long the reconciler
  runs.

**Bounded and loud.** One pass enqueues at most ``MAX_SELECTED_SESSIONS``
sessions, oldest first so progress is deterministic and a large backlog drains
predictably. Every enqueue is recorded in ``claude_capture_backfill``; a
session that keeps failing is retried ``MAX_ATTEMPTS`` times and then left
alone with its error preserved. A pass that backfills anything logs a WARNING,
not an INFO — a backfill firing means the hook path lost data, which is a real
defect upstream, not routine housekeeping.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import UTC, datetime
from typing import Any

from app.common.system_logger import get_logger
from app.services.aidream.client import (
    AIDreamClient,
    AIDreamError,
    AIDreamOfflineError,
    get_aidream_client,
)
from app.services.coding_sessions.claude_history import (
    MAX_IMPORT_BYTES,
    MAX_SELECTED_SESSIONS,
    ClaudeHistoryConflict,
    ClaudeHistoryImportRequest,
    ClaudeHistoryImporter,
    ClaudeHistorySelection,
)
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import TokenRepo

logger = get_logger()

IDENTITY_PATH = "/coding-sessions/sessions"
MAX_IDENTITY_ROWS = 1000

# How many local sessions one pass inspects. The importer's own preview cap.
PREVIEW_LIMIT = 200

# A session that cannot be imported is retried this many times, then left with
# its error recorded. Without this an unreadable transcript re-enters the
# candidate set on every pass forever.
MAX_ATTEMPTS = 3

# Not an env var: a toggle read from the environment is exactly the switch that
# gets flipped once in an emergency and then silently disables the feature for
# months. Flipping this takes a code change, which is the point.
AUTO_BACKFILL_ENABLED = True

# Half the importer's hard cap. A backfill runs unattended in the background,
# so it deliberately reads and uploads less per pass than a user who just
# pressed a button and is watching it.
BATCH_BYTE_BUDGET = MAX_IMPORT_BYTES // 2

# Idle interval between passes. Capture loss is measured in hours, so a tight
# loop buys nothing and re-reads the user's disk for no reason.
PASS_INTERVAL_SECONDS = 15 * 60


class CaptureReconcileBlocked(RuntimeError):
    """The pass cannot run yet — sign-in, configuration, or connectivity."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _sdk_identity(project_key: str, raw_session_id: str) -> str:
    """The composite identity an imported native session is stored under.

    Mirrors ``claude_history._bridge_provider_session_id``. A hook binding
    stores Claude's RAW session UUID while an import stores this composite, so
    a diff that checks only one form re-imports everything on every pass.
    """
    project_digest = hashlib.sha256(project_key.encode("utf-8")).hexdigest()
    encoded = base64.urlsafe_b64encode(raw_session_id.encode("utf-8")).decode().rstrip("=")
    return f"claude-sdk:{project_digest}:{encoded}"


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class ClaudeCaptureReconciler:
    """Diffs local Claude sessions against the platform and backfills the gap."""

    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        importer: ClaudeHistoryImporter | None = None,
        client: AIDreamClient | None = None,
    ) -> None:
        self._db = db or get_db()
        self._importer = importer or ClaudeHistoryImporter(db=self._db)
        self._client = client
        self._tokens = TokenRepo(self._db)
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ state

    async def _attempts(self) -> dict[str, dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT session_key, source_revision, attempts, last_error "
            "FROM claude_capture_backfill"
        )
        return {str(row["session_key"]): dict(row) for row in rows}

    async def _record_attempt(
        self, session_key: str, source_revision: str | None, error: str | None
    ) -> None:
        """Counts one enqueue attempt. A CHANGED revision resets the count.

        A transcript the user has since written more into is a genuinely new
        input, not the same failure repeating, so it deserves its full retry
        budget again.
        """
        await self._db.execute(
            """INSERT INTO claude_capture_backfill
                   (session_key, source_revision, attempts, last_error,
                    enqueued_at, updated_at)
               VALUES (?, ?, 1, ?, datetime('now'), datetime('now'))
               ON CONFLICT(session_key) DO UPDATE SET
                   attempts = CASE
                       WHEN claude_capture_backfill.source_revision IS excluded.source_revision
                       THEN claude_capture_backfill.attempts + 1
                       ELSE 1
                   END,
                   source_revision = excluded.source_revision,
                   last_error = excluded.last_error,
                   enqueued_at = excluded.enqueued_at,
                   updated_at = excluded.updated_at""",
            (session_key, source_revision, error),
        )
        await self._db.commit()

    # ------------------------------------------------------------------ cloud

    async def _identities(self) -> list[dict[str, Any]]:
        token_row = await self._tokens.get()
        if (
            not token_row
            or not token_row.get("access_token")
            or not token_row.get("user_id")
            or self._tokens.is_expired(token_row)
        ):
            raise CaptureReconcileBlocked("no_active_user_jwt")
        client = self._client or get_aidream_client()
        if client is None:
            raise CaptureReconcileBlocked("aidream_server_unconfigured")
        try:
            response = await client.get(
                f"{IDENTITY_PATH}?provider=claude_code&limit={MAX_IDENTITY_ROWS}",
                jwt=str(token_row["access_token"]),
            )
        except AIDreamOfflineError as exc:
            raise CaptureReconcileBlocked("aidream_unreachable") from exc
        except AIDreamError as exc:
            raise CaptureReconcileBlocked(f"aidream_error:{exc}") from exc
        sessions = response.get("sessions") if isinstance(response, dict) else None
        if not isinstance(sessions, list):
            raise CaptureReconcileBlocked("identity_list_malformed")
        return [row for row in sessions if isinstance(row, dict)]

    # ------------------------------------------------------------------- pass

    async def reconcile(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Serialize manual and background passes, then return one report."""
        async with self._lock:
            return await self._reconcile_once(dry_run=dry_run)

    async def _reconcile_once(self, *, dry_run: bool = False) -> dict[str, Any]:
        """One bounded pass. Returns exactly what it saw and what it enqueued."""
        if not AUTO_BACKFILL_ENABLED:
            return {"status": "disabled", "enqueued": 0}

        identities = await self._identities()
        known: set[str] = set()
        era_start: datetime | None = None
        for row in identities:
            identity = row.get("provider_session_id")
            if isinstance(identity, str):
                known.add(identity)
            seen = _parse_iso(row.get("last_seen_at"))
            if seen is not None and (era_start is None or seen < era_start):
                era_start = seen

        if era_start is None:
            # THE ERA RULE: no binding has ever existed, so the user has not
            # opted into continuous mirroring. Automatic backfill would be an
            # unrequested upload of their entire local history.
            return {
                "status": "no_mirroring_era",
                "detail": (
                    "No Claude session has ever been mirrored, so there is no "
                    "era to backfill. Use Review local history to choose "
                    "explicitly."
                ),
                "cloud_sessions": 0,
                "enqueued": 0,
            }

        preview = await self._importer.preview(limit=PREVIEW_LIMIT)
        if not preview.get("import_ready"):
            raise CaptureReconcileBlocked(
                str(preview.get("account_blocked_reason") or "import_not_ready")
            )
        account_key = preview.get("provider_account_key")
        if not isinstance(account_key, str):
            raise CaptureReconcileBlocked("account_identity_unavailable")

        era_start_ns = int(era_start.timestamp() * 1_000_000_000)
        attempts = await self._attempts()

        candidates: list[dict[str, Any]] = []
        skipped_pre_era = 0
        for session in preview.get("sessions", []):
            if not isinstance(session, dict) or not session.get("import_available"):
                continue
            raw_id = session.get("session_id")
            project_key = session.get("project_key")
            revision = session.get("source_revision")
            if not (
                isinstance(raw_id, str)
                and isinstance(project_key, str)
                and isinstance(revision, str)
            ):
                continue
            # Both identity forms: a hook binding stores the raw UUID, an
            # import stores the composite. Either means it is already there.
            if raw_id in known or _sdk_identity(project_key, raw_id) in known:
                continue
            modified_ns = session.get("last_modified_ns")
            if not isinstance(modified_ns, int) or modified_ns < era_start_ns:
                skipped_pre_era += 1
                continue
            session_key = f"{project_key}:{raw_id}"
            prior = attempts.get(session_key)
            if (
                prior is not None
                and prior.get("source_revision") == revision
                and int(prior.get("attempts") or 0) >= MAX_ATTEMPTS
            ):
                continue
            candidates.append(
                {
                    "session_key": session_key,
                    "session_id": raw_id,
                    "project_key": project_key,
                    "source_revision": revision,
                    "last_modified_ns": modified_ns,
                    "bytes": session.get("bytes") if isinstance(session.get("bytes"), int) else 0,
                }
            )

        # Oldest first: a backlog drains in a deterministic order, and the
        # sessions closest to being lost from the user's attention go first.
        candidates.sort(key=lambda item: item["last_modified_ns"])

        # Fill the batch under a BYTE budget as well as a count. The importer
        # rejects an oversized selection wholesale, and that rejection would
        # otherwise burn a retry attempt on nine innocent sessions because one
        # transcript happened to be huge.
        batch: list[dict[str, Any]] = []
        oversized: list[dict[str, Any]] = []
        budget = 0
        for item in candidates:
            size = int(item["bytes"] or 0)
            if size > BATCH_BYTE_BUDGET:
                # Can never share a batch, and alone it would still exceed the
                # importer's cap. Record it so it is visible instead of being
                # silently skipped on every pass forever.
                oversized.append(item)
                continue
            if len(batch) >= MAX_SELECTED_SESSIONS or budget + size > BATCH_BYTE_BUDGET:
                break
            batch.append(item)
            budget += size

        for item in oversized[:MAX_SELECTED_SESSIONS]:
            await self._record_attempt(
                item["session_key"],
                item["source_revision"],
                f"transcript exceeds the {BATCH_BYTE_BUDGET}-byte import budget",
            )

        report = {
            "status": "ok",
            "cloud_sessions": len(known),
            "local_sessions": len(preview.get("sessions", [])),
            "missing": len(candidates),
            "skipped_pre_era": skipped_pre_era,
            "era_start": era_start.isoformat(),
            "enqueued": 0,
            "batch": [item["session_key"] for item in batch],
        }
        if not batch or dry_run:
            return report

        request = ClaudeHistoryImportRequest(
            provider_account_key=account_key,
            sessions=[
                ClaudeHistorySelection(
                    session_id=item["session_id"],
                    provider_project_key=item["project_key"],
                    source_revision=item["source_revision"],
                )
                for item in batch
            ],
        )
        try:
            result = await self._importer.import_selected(request)
        except (ClaudeHistoryConflict, ValueError) as exc:
            # A conflict is normal (the user wrote more mid-pass); record it
            # against every session in the batch so a permanently broken one
            # exhausts its budget instead of blocking the queue forever.
            for item in batch:
                await self._record_attempt(
                    item["session_key"], item["source_revision"], str(exc)
                )
            report["status"] = "conflict"
            report["detail"] = str(exc)
            return report

        for item in batch:
            await self._record_attempt(item["session_key"], item["source_revision"], None)
        report["enqueued"] = len(batch)
        report["import"] = result
        # LOUD ON PURPOSE: a backfill firing means the hook path lost data.
        logger.warning(
            "[capture_reconciler] Backfilled %d Claude session(s) the hook path "
            "never delivered (%d still missing). Capture through the plugin's "
            "MCP connection is failing or was interrupted — check /mcp in "
            "Claude Code.",
            len(batch),
            max(0, len(candidates) - len(batch)),
        )
        return report

    async def status(self) -> dict[str, Any]:
        rows = await self._db.fetchall(
            "SELECT session_key, attempts, last_error, enqueued_at "
            "FROM claude_capture_backfill ORDER BY updated_at DESC LIMIT 50"
        )
        exhausted = [
            dict(row) for row in rows if int(row["attempts"] or 0) >= MAX_ATTEMPTS
        ]
        return {
            "enabled": AUTO_BACKFILL_ENABLED,
            "running": self.active,
            "interval_seconds": PASS_INTERVAL_SECONDS,
            "max_per_pass": MAX_SELECTED_SESSIONS,
            "recent": [dict(row) for row in rows],
            "exhausted": exhausted,
        }

    # -------------------------------------------------------------- lifecycle

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start_background(self) -> None:
        if self.active or not AUTO_BACKFILL_ENABLED:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="claude_capture_reconciler")

    async def stop(self) -> None:
        self._stopping = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.reconcile()
            except CaptureReconcileBlocked as exc:
                # Signed out, offline, or unconfigured are ordinary states for a
                # desktop app; they are not defects and must not spam ERROR.
                logger.debug("[capture_reconciler] pass skipped: %s", exc.reason)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.error(
                    "[capture_reconciler] pass FAILED — Claude sessions the hook "
                    "path missed are not being backfilled",
                    exc_info=True,
                )
            try:
                await asyncio.sleep(PASS_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise


_reconciler: ClaudeCaptureReconciler | None = None


def get_claude_capture_reconciler() -> ClaudeCaptureReconciler:
    global _reconciler
    if _reconciler is None:
        _reconciler = ClaudeCaptureReconciler()
    return _reconciler


__all__ = [
    "AUTO_BACKFILL_ENABLED",
    "CaptureReconcileBlocked",
    "ClaudeCaptureReconciler",
    "MAX_ATTEMPTS",
    "PASS_INTERVAL_SECONDS",
    "get_claude_capture_reconciler",
]
