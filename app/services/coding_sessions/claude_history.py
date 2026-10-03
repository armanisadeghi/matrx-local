"""Matrx Local's Claude history importer: the shared importer, wired to this app.

The importer itself (discovery, hashing, parsing, account identity, envelope bytes) lives in
``matrx_coding_history.importer`` (packages/matrx-coding-history), shared with Matrx 2. This
module keeps everything that is Matrx Local's own: the SQLite review inventory and its flows
(review, scan pages, prepare, sync-all), the views of the durable hook delivery queue (status,
discard, retry), and the default wiring every caller relies on (``ClaudeHistoryImporter()``).
Every name callers imported from this path is still importable from it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from matrx_coding_history.account import (
    ACCOUNT_KEY_VERSION,
    AccountSnapshot as _AccountSnapshot,
    account_label,
    derive_account_key,
    read_account_snapshot as _read_account_snapshot,
)
from matrx_coding_history.importer import (
    IMPORTER_VERSION,
    MAX_BATCH_BYTES,
    MAX_BATCH_ENTRIES,
    MAX_DISCOVERED_SESSIONS,
    MAX_IMPORT_BYTES,
    MAX_LINE_BYTES,
    MAX_PREVIEW_SESSIONS,
    MAX_SELECTED_SESSIONS,
    ClaudeHistoryConflict,
    ClaudeHistoryImportRequest,
    ClaudeHistorySelection,
    TranscriptCensus,
    _aggregate_revision,
    _bridge_provider_session_id,
    _conversation_id,
    _discover_sources,
    _first_prompt_text,
    _hash_source,
    _open_regular_under,
    _read_summary,
    _safe_json,
    _SessionSource,
    _sha256_text,
    _source_state,
    _stream_key,
    _subagent_streams,
    transcript_census,
)
from matrx_coding_history.importer import ClaudeHistoryImporter as _SharedImporter
from pydantic import BaseModel, ConfigDict, Field

from app.services.coding_sessions.history_inventory import (
    HistoryChangeType,
    HistoryInventoryStore,
)
from app.services.coding_sessions.history_ports import TokenRepoAccess
from app.services.coding_sessions.service import CodingSessionBridgeOutbox
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import SyncMetaRepo, TokenRepo


class ClaudeHistoryPrepareSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    provider_project_key: Annotated[str, Field(min_length=1, max_length=1024)]
    source_state: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ClaudeHistoryPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_id: UUID
    provider_account_key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    sessions: Annotated[
        list[ClaudeHistoryPrepareSelection],
        Field(min_length=1, max_length=MAX_SELECTED_SESSIONS),
    ]

_review_lock = asyncio.Lock()


class ClaudeHistoryImporter(_SharedImporter):
    """The shared importer with Matrx Local's SQLite repos and durable outbox."""

    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        outbox: CodingSessionBridgeOutbox | None = None,
        config_dir: Path | None = None,
        sessions_dir: Path | None = None,
        account_reader: Any = _read_account_snapshot,
    ) -> None:
        self._db = db or get_db()
        self._outbox = outbox
        self._tokens = TokenRepo(self._db)
        self._inventory = HistoryInventoryStore(self._db)
        super().__init__(
            sink=self._bridge_outbox,
            user=TokenRepoAccess(self._tokens),
            sync_meta=SyncMetaRepo(self._db),
            config_dir=config_dir,
            sessions_dir=sessions_dir,
            account_reader=account_reader,
        )

    def _bridge_outbox(self) -> CodingSessionBridgeOutbox:
        outbox = self._outbox
        if outbox is None:
            from app.services.coding_sessions.service import (
                get_coding_session_bridge_outbox,
            )

            outbox = get_coding_session_bridge_outbox()
        return outbox

    @staticmethod
    def _inventory_row(
        source: _SessionSource,
        previous: dict[str, Any] | None,
    ) -> dict[str, Any]:
        entry = source.index_entry
        metadata = {
            "title": source.title,
            "title_source": entry.title_source if entry else None,
            "project_name": source.project_name,
            "git_branch": source.git_branch,
            "worktree_name": entry.worktree_name if entry else None,
            "is_archived": entry.is_archived if entry else None,
            "is_pinned": entry.is_pinned if entry else None,
            "pinned_rank": entry.pinned_rank if entry else None,
            "category": entry.category if entry else None,
            "import_blocked_reason": source.import_blocked_reason,
        }
        if previous is None:
            change_type: HistoryChangeType = "new"
            source_revision = None
        elif previous.get("source_state") != source.source_state:
            change_type = "content_changed"
            source_revision = None
        elif any(previous.get(key) != value for key, value in metadata.items()):
            change_type = "metadata_changed"
            source_revision = previous.get("source_revision")
        else:
            change_type = "unchanged"
            source_revision = previous.get("source_revision")
        return {
            "session_id": source.session_id,
            "project_key": source.project_key,
            "present": True,
            "change_type": change_type,
            "source_state": source.source_state,
            "source_revision": source_revision,
            **metadata,
            "bytes": source.total_bytes,
            "file_count": len(source.streams),
            "subagent_count": len(source.streams) - 1,
            "last_modified_ns": source.latest_mtime_ns,
            "import_available": source.import_blocked_reason is None,
        }

    async def review(
        self,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Create a durable stat-fenced inventory and report its exact delta."""
        if not 1 <= limit <= MAX_PREVIEW_SESSIONS:
            raise ValueError(f"limit must be between 1 and {MAX_PREVIEW_SESSIONS}")
        if _review_lock.locked():
            raise ClaudeHistoryConflict("A Claude history review is already running")
        async with _review_lock:
            account, index = await asyncio.gather(
                self._account_reader(), self._read_index()
            )
            previous_scan = await self._inventory.latest_completed_scan(
                "claude_code", account.account_key
            )
            previous_scan_id = previous_scan["scan_id"] if previous_scan else None
            previous_rows = await self._inventory.comparison_rows(previous_scan_id)
            scan_id = await self._inventory.begin_scan(
                provider="claude_code",
                provider_account_key=account.account_key,
                previous_scan_id=previous_scan_id,
            )
            try:
                sources, totals = await asyncio.to_thread(
                    _discover_sources,
                    self._config_dir,
                    hash_content=False,
                    index=index,
                )
                rows: list[dict[str, Any]] = []
                present_identities: set[tuple[str, str]] = set()
                for source in sources:
                    identity = (source.session_id, source.project_key)
                    present_identities.add(identity)
                    rows.append(
                        self._inventory_row(source, previous_rows.get(identity))
                    )
                for identity, previous in previous_rows.items():
                    if identity in present_identities:
                        continue
                    missing = dict(previous)
                    missing.update(
                        {
                            "present": False,
                            "change_type": "missing",
                            "source_revision": None,
                            "import_available": False,
                            "import_blocked_reason": "source_missing",
                            "bytes": previous.get("payload_bytes", 0),
                        }
                    )
                    rows.append(missing)
                summary = await self._inventory.complete_scan(
                    scan_id, rows=rows, totals=totals
                )
            except Exception as exc:
                await self._inventory.fail_scan(scan_id, str(exc))
                raise

            token_row = await self._tokens.get()
            page = await self._inventory.list_rows(scan_id, limit=limit)
            return {
                "schema_version": 2,
                "source": "claude_local_jsonl",
                "scan": summary,
                "account_identity_available": account.available,
                "provider_account_key": account.account_key,
                "provider_account_key_version": ACCOUNT_KEY_VERSION,
                "account_fingerprint": account.fingerprint,
                "provider_account_label": account.account_label,
                "account_identity_observed_at": summary["completed_at"],
                "account_blocked_reason": account.reason,
                "claude_client_version": account.client_version,
                "matrx_user_available": bool(token_row and token_row.get("user_id")),
                "import_ready": account.available
                and bool(token_row and token_row.get("user_id")),
                "limits": {
                    "page_size_max": MAX_PREVIEW_SESSIONS,
                    "selected_sessions": MAX_SELECTED_SESSIONS,
                    "import_bytes": MAX_IMPORT_BYTES,
                    "line_bytes": MAX_LINE_BYTES,
                },
                **page,
                "facets": await self._inventory.facets(scan_id),
            }

    async def inventory_page(
        self,
        scan_id: str,
        **query: Any,
    ) -> dict[str, Any]:
        if await self._inventory.get_scan(scan_id) is None:
            raise ValueError("Unknown history scan")
        result = await self._inventory.list_rows(scan_id, **query)
        result["facets"] = await self._inventory.facets(scan_id)
        return result

    async def prepare_selected(
        self, request: ClaudeHistoryPrepareRequest
    ) -> dict[str, Any]:
        """Hash only selected rows, preserving fast review for large histories."""
        account = await self._account_reader()
        if not account.available or account.account_key != request.provider_account_key:
            raise ClaudeHistoryConflict(
                "Claude account changed after review; review again before importing"
            )
        identities = {
            (str(selection.session_id), selection.provider_project_key)
            for selection in request.sessions
        }
        sources = await self.capture_revisions(identities)
        prepared: list[dict[str, str]] = []
        for selection in request.sessions:
            identity = (str(selection.session_id), selection.provider_project_key)
            source = sources.get(identity)
            if source is None or source.source_revision is None:
                raise ClaudeHistoryConflict(
                    f"Claude session {selection.session_id} is no longer importable"
                )
            if source.source_state != selection.source_state:
                raise ClaudeHistoryConflict(
                    f"Claude session {selection.session_id} changed after review"
                )
            prepared.append(
                {
                    "session_id": source.session_id,
                    "project_key": source.project_key,
                    "source_state": source.source_state,
                    "source_revision": source.source_revision,
                }
            )
        try:
            await self._inventory.set_source_revisions(str(request.scan_id), prepared)
        except ValueError as exc:
            raise ClaudeHistoryConflict(str(exc)) from exc
        return {
            "schema_version": 1,
            "scan_id": str(request.scan_id),
            "prepared": prepared,
        }

    async def sync_all(self) -> dict[str, Any]:
        """Sync every syncable conversation. No selection, no preview, no steps.

        The four-call shape this replaces (review, page, prepare, import) exists
        to let a caller choose a subset. Nothing ever wants a subset: the job is
        "put my conversations in the cloud". MAX_SELECTED_SESSIONS caps a single
        prepare/import pair at 10, so batching is this method's problem, not the
        user's — it is an internal transport detail, never a reason to make
        somebody click 181 times.
        """
        review = await self.review(limit=1)
        scan_id = str(review["scan"]["scan_id"])
        account_key = str(review["provider_account_key"])
        if not review.get("import_ready"):
            return {
                "started": False,
                "blocked_reason": (
                    review.get("account_blocked_reason")
                    or ("Sign in to AI Matrx to sync." if not review.get(
                        "matrx_user_available"
                    ) else "Claude account identity is unavailable.")
                ),
                "queued": 0,
                "entries": 0,
                "conversations": 0,
                "failed": [],
            }

        identities: list[dict[str, str]] = []
        cursor: str | None = None
        while True:
            # archived-items-law-exempt: "put ALL my conversations in the
            # cloud" — this walk is a machine enumeration with no screen and no
            # selection, and an archived conversation is still the user's. The
            # law governs what a LIST SHOWS, never what a backup carries; the
            # default "active" here would silently drop archived history from
            # the sync, which is the opposite of what the button promises.
            page = await self._inventory.list_rows(
                scan_id,
                cursor=cursor,
                limit=MAX_PREVIEW_SESSIONS,
                importable=True,
                archived="all",
            )
            for row in page["items"]:
                identities.append(
                    {
                        "session_id": str(row["session_id"]),
                        "provider_project_key": str(row["project_key"]),
                    }
                )
            cursor = page["page"]["next_cursor"]
            if not cursor:
                break

        queued = 0
        entries = 0
        synced = 0
        failed: list[dict[str, str]] = []
        for start in range(0, len(identities), MAX_SELECTED_SESSIONS):
            batch = identities[start : start + MAX_SELECTED_SESSIONS]
            try:
                prepared = await self.prepare_selected(
                    ClaudeHistoryPrepareRequest(
                        scan_id=UUID(scan_id),
                        provider_account_key=account_key,
                        sessions=[
                            ClaudeHistoryPrepareSelection(
                                session_id=UUID(item["session_id"]),
                                provider_project_key=item["provider_project_key"],
                                source_state=item["source_state"],
                            )
                            for item in await self._source_states(scan_id, batch)
                        ],
                    )
                )
                ready = [
                    ClaudeHistorySelection(
                        session_id=UUID(str(item["session_id"])),
                        provider_project_key=str(item["provider_project_key"]),
                        source_revision=str(item["source_revision"]),
                    )
                    for item in prepared.get("sessions", [])
                    if item.get("source_revision")
                ]
                if not ready:
                    continue
                result = await self.import_selected(
                    ClaudeHistoryImportRequest(
                        provider_account_key=account_key, sessions=ready
                    )
                )
                queued += int(result.get("queued_batches", 0))
                entries += int(result.get("entries", 0))
                synced += int(result.get("selected_sessions", 0))
            except Exception as exc:  # one bad batch must not stop the rest
                failed.append(
                    {
                        "sessions": ", ".join(item["session_id"] for item in batch),
                        "reason": str(exc),
                    }
                )

        return {
            "started": True,
            "blocked_reason": None,
            "queued": queued,
            "entries": entries,
            "conversations": synced,
            "failed": failed,
        }

    async def _source_states(
        self, scan_id: str, batch: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Attach each row's current source_state hash, which prepare requires."""
        out: list[dict[str, str]] = []
        for item in batch:
            row = await self._inventory._db.fetchone(
                """SELECT source_state FROM coding_session_history_scan_rows
                   WHERE scan_id = ? AND session_id = ? AND project_key = ?""",
                (scan_id, item["session_id"], item["provider_project_key"]),
            )
            if row and row["source_state"]:
                out.append({**item, "source_state": str(row["source_state"])})
        return out

    async def status(self) -> dict[str, Any]:
        outbox = self._outbox
        if outbox is None:
            from app.services.coding_sessions.service import (
                get_coding_session_bridge_outbox,
            )

            outbox = get_coding_session_bridge_outbox()
        outbox_status = await outbox.status()
        pending_history_imports = await outbox.pending_native_import_count()
        quarantined_history_imports = await outbox.quarantined_native_import_count()
        oldest_history_import = await outbox.oldest_native_import()
        sync = await self._sync_meta.get_last_sync("claude_history_import")
        delivery = outbox_status["delivery"]
        history_activity = delivery["providers"]["claude_code"]["by_source"].get(
            "claude_local_jsonl", {}
        )
        last_enqueue = history_activity.get("last_enqueue")
        last_acknowledgement = history_activity.get("last_acknowledgement")
        if quarantined_history_imports:
            delivery_state = "attention_required"
        elif pending_history_imports:
            delivery_state = "queued"
        elif sync and sync.get("status") == "discarded":
            delivery_state = "discarded"
        elif last_acknowledgement is not None and (
            last_enqueue is None
            or last_acknowledgement["receipt_id"] >= last_enqueue["receipt_id"]
        ):
            delivery_state = "acknowledged"
        else:
            delivery_state = "idle"
        return {
            "schema_version": 1,
            "source": "claude_local_jsonl",
            "pending_outbox": outbox_status["pending"],
            "pending_history_imports": pending_history_imports,
            "quarantined_outbox": outbox_status["quarantined"],
            "quarantined_history_imports": quarantined_history_imports,
            "oldest_history_import": oldest_history_import,
            "oldest_pending": outbox_status["oldest"],
            "last_sync": sync,
            "delivery": {
                "state": delivery_state,
                "queued_batches": pending_history_imports,
                "quarantined_batches": quarantined_history_imports,
                "last_enqueue": last_enqueue,
                "last_acknowledgement": last_acknowledgement,
            },
            "native_restore_available": False,
        }

    async def discard_pending(self) -> dict[str, Any]:
        outbox = self._outbox
        if outbox is None:
            from app.services.coding_sessions.service import (
                get_coding_session_bridge_outbox,
            )

            outbox = get_coding_session_bridge_outbox()
        result = await outbox.discard_pending_native_imports()
        await self._sync_meta.set_last_sync(
            "claude_history_import",
            status="discarded",
        )
        return {
            "schema_version": 1,
            "source": "claude_local_jsonl",
            **result,
        }

    async def retry_pending(self) -> dict[str, Any]:
        outbox = self._outbox
        if outbox is None:
            from app.services.coding_sessions.service import (
                get_coding_session_bridge_outbox,
            )

            outbox = get_coding_session_bridge_outbox()
        result = await outbox.retry_pending_native_imports()
        return {
            "schema_version": 1,
            "source": "claude_local_jsonl",
            **result,
        }


__all__ = [
    "ACCOUNT_KEY_VERSION",
    "IMPORTER_VERSION",
    "ClaudeHistoryConflict",
    "ClaudeHistoryImporter",
    "ClaudeHistoryImportRequest",
    "ClaudeHistoryPrepareRequest",
    "ClaudeHistoryPrepareSelection",
    "TranscriptCensus",
    "ClaudeHistorySelection",
    "account_label",
    "derive_account_key",
    "transcript_census",
]

# Re-exported for callers that import them from this path (local_runtime, claude_overview,
# sync_truth_reader, tests); they are the shared importer's own objects.
_REEXPORTED = (
    MAX_BATCH_BYTES,
    MAX_BATCH_ENTRIES,
    MAX_DISCOVERED_SESSIONS,
    MAX_LINE_BYTES,
    _AccountSnapshot,
    _aggregate_revision,
    _bridge_provider_session_id,
    _conversation_id,
    _first_prompt_text,
    _hash_source,
    _open_regular_under,
    _read_summary,
    _safe_json,
    _SessionSource,
    _sha256_text,
    _source_state,
    _stream_key,
    _subagent_streams,
)
