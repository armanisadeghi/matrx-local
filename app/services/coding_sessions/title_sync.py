"""Two-way sync of one label: Claude Code's session title and AI Matrx's.

Arman's ruling (2026-08-16): *"Those labels cannot be different. They must be
exactly the same, and they must remain in sync."* and *"when our conversations
go to Claude Code, or if I update this, then the Claude Code value should be
updated to match."*

The join is exact. aidream answers ``GET /coding-sessions/sessions`` with the
owner's Claude bindings; Claude's desktop session index answers with the label
it shows for each ``cliSessionId``. One pass reconciles both directions:

1. **Inbound — Claude Code → AI Matrx.** For every bound session whose Claude
   labels changed, enqueue a ``SessionMetadata`` observation. The platform
   title tracks a rename made in Claude Code on the next sync pass.
2. **Outbound — AI Matrx → Claude Code.** For every bound session the server
   reports as ``title_source="user"`` whose AI Matrx title differs from
   Claude's, write that title into Claude Code's own index records (see
   :mod:`app.services.coding_sessions.claude_label_writer`) and then re-observe
   it with ``title_origin="ai_matrx_user"`` so the server records the user tier
   AND converges ``applied_title`` on the label both sides now show.

**The conflict rule is last-writer-wins by observed value, and it falls out of
that second half.** Both sides agree on a label only when the server's
``applied_title`` equals it. A rename on either side breaks that agreement in
exactly one place, and the side that moved is the side that wins:

- Renamed in AI Matrx → the conversation title no longer equals
  ``applied_title``, the server reports ``title_source="user"``, and the
  outbound leg pushes it down.
- Renamed in Claude Code → the index title no longer equals ``applied_title``,
  and the server's ladder accepts the provider title because the conversation
  still shows the last agreed one.
- Renamed on BOTH sides between two passes → Claude Code wins. The server's
  ``claude_title`` is the label it last heard from Claude, so an index title
  that no longer equals it proves Claude moved; the outbound leg stands down
  and lets the inbound value settle first.
- If Claude Code overwrites a title we wrote, nothing is lost: the next inbound
  pass simply pulls Claude's value back.

Deliberate boundaries:

- **Only already-mirrored sessions.** Labels of local sessions the user never
  mirrored never leave this machine, and a session AI Matrx does not own is
  never written to on disk. The server list is the allowlist for BOTH
  directions.
- **Metadata plane only.** ``SessionMetadata`` updates a binding's labels; it
  never mints a binding and never appends a transcript entry.
- **Idempotent both ways.** The exact payload last cloud-acknowledged and the exact title
  last written down are recorded locally, so an unchanged label costs zero
  network work and zero writes into another application's files.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.common.system_logger import get_logger
from app.services.aidream.client import AIDreamClient, get_aidream_client
from app.services.coding_sessions.claude_label_writer import (
    ClaudeSessionIndexWriter,
)
from app.services.coding_sessions.claude_session_index import (
    MAX_INDEX_FILES,
    ClaudeSessionIndexEntry,
    read_session_index,
)
from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.identity_client import (
    IdentityInventoryBlocked,
    fetch_complete_identity_inventory,
)
from app.services.coding_sessions.service import (
    SESSION_METADATA_EVENT,
    CodingSessionBridgeOutbox,
    get_coding_session_bridge_outbox,
)
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import SyncMetaRepo, TokenRepo

logger = get_logger()

_CLAUDE_SDK_PREFIX = "claude-sdk:"

# The ladder tier the server reports when the AI Matrx title was typed by the
# user. It is the ONLY tier that travels back down into Claude Code's index —
# a provider or first-prompt title came from Claude in the first place.
TITLE_SOURCE_USER = "user"
# Marks the observation sent right after a write-down, so the server records
# the user tier instead of demoting the label to `provider`.
TITLE_ORIGIN_AI_MATRX_USER = "ai_matrx_user"


class ClaudeTitleSyncBlocked(RuntimeError):
    """The sync cannot run yet — sign-in, configuration, or connectivity."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def raw_session_id(provider_session_id: str) -> str | None:
    """The Claude CLI session UUID behind either bound identity form.

    Event-mirror bindings store Claude's raw session UUID. Explicit local
    imports store the deterministic ``claude-sdk:<project digest>:<b64 id>``
    composite the Claude Agent SDK SessionStore uses; its last segment decodes
    back to the same UUID.
    """
    candidate = provider_session_id
    if candidate.startswith(_CLAUDE_SDK_PREFIX):
        parts = candidate.split(":")
        if len(parts) != 3:
            return None
        encoded = parts[2]
        padding = "=" * (-len(encoded) % 4)
        try:
            candidate = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    try:
        UUID(candidate)
    except ValueError:
        return None
    return candidate


def payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def title_sha256(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


_DETAIL_FIELDS = (
    "title",
    "project_name",
    "git_branch",
    "worktree_name",
    "is_archived",
    "is_pinned",
    "pinned_rank",
    "category",
)
_CLOUD_DETAIL_KEYS = {
    "title": "conversation_title",
    "project_name": "claude_project_name",
    "git_branch": "claude_git_branch",
    "worktree_name": "claude_worktree_name",
    "is_archived": "claude_is_archived",
    "is_pinned": "claude_is_pinned",
    "pinned_rank": "claude_pinned_rank",
    "category": "claude_category",
}


def _session_ref(provider_session_id: str) -> str:
    return hashlib.sha256(provider_session_id.encode("utf-8")).hexdigest()[:12]


def _cloud_detail_values(identity: dict[str, Any]) -> dict[str, Any]:
    """Provider metadata last proved by AI Matrx; missing keys stay unknown."""
    values: dict[str, Any] = {}
    for field, cloud_key in _CLOUD_DETAIL_KEYS.items():
        if cloud_key in identity:
            values[field] = identity.get(cloud_key)
    return values


def _field_comparisons(
    local: dict[str, Any], cloud: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        {
            "field": field,
            "local": local.get(field),
            "ai_matrx": cloud.get(field),
            "ai_matrx_observed": field in cloud,
            "equal": field in cloud and local.get(field) == cloud.get(field),
        }
        for field in _DETAIL_FIELDS
    ]


def _record_paths_writable(entries: dict[str, ClaudeSessionIndexEntry]) -> bool:
    """Prove each known copy can be opened for writing without changing bytes."""
    paths = {path for entry in entries.values() for path in entry.record_paths}
    if not paths:
        return False
    for path in paths:
        try:
            descriptor = os.open(path, os.O_WRONLY)
        except OSError:
            return False
        else:
            os.close(descriptor)
    return True


def session_metadata_request(
    *,
    provider_session_id: str,
    provider_project_key: str | None,
    payload: dict[str, Any],
) -> BridgeRequest:
    """One metadata-plane observation for an existing Claude binding."""
    envelope: dict[str, Any] = {
        "action": "observe_hook",
        "provider": "claude_code",
        "provider_session_id": provider_session_id,
        "origin": "independent_hook",
        "hook_event": {
            "name": SESSION_METADATA_EVENT,
            "stable_event_id": f"session-metadata:{payload_digest(payload)[:48]}",
            "payload": payload,
        },
    }
    if provider_project_key:
        envelope["provider_project_key"] = provider_project_key
    return BridgeRequest.model_validate(envelope)


class ClaudeSessionMetadataReconciler:
    """Keeps one label identical in Claude Code and AI Matrx, both directions."""

    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        outbox: CodingSessionBridgeOutbox | None = None,
        client: AIDreamClient | None = None,
        index_reader: Any = read_session_index,
        writer: ClaudeSessionIndexWriter | None = None,
    ) -> None:
        self._db = db or get_db()
        self._outbox = outbox
        self._client = client
        self._index_reader = index_reader
        self._writer = writer or ClaudeSessionIndexWriter()
        self._tokens = TokenRepo(self._db)
        self._sync_meta = SyncMetaRepo(self._db)
        self._sync_lock = asyncio.Lock()

    async def _sent_digests(self) -> dict[str, str]:
        rows = await self._db.fetchall(
            "SELECT provider_session_id, payload_sha256 FROM claude_session_metadata_sent"
        )
        return {
            str(row["provider_session_id"]): str(row["payload_sha256"]) for row in rows
        }

    async def _pushed_titles(self) -> dict[str, str]:
        rows = await self._db.fetchall(
            "SELECT provider_session_id, title_sha256 FROM claude_session_title_pushed"
        )
        return {
            str(row["provider_session_id"]): str(row["title_sha256"]) for row in rows
        }

    async def _record_pushed(self, provider_session_id: str, digest: str) -> None:
        await self._db.execute(
            """INSERT INTO claude_session_title_pushed
                   (provider_session_id, title_sha256, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(provider_session_id) DO UPDATE SET
                   title_sha256 = excluded.title_sha256,
                   updated_at = excluded.updated_at""",
            (provider_session_id, digest),
        )
        await self._db.commit()

    async def _identities(self) -> list[dict[str, Any]]:
        token_row = await self._tokens.get()
        if (
            not token_row
            or not token_row.get("access_token")
            or not token_row.get("user_id")
            or self._tokens.is_expired(token_row)
        ):
            raise ClaudeTitleSyncBlocked("no_active_user_jwt")
        client = self._client or get_aidream_client()
        if client is None:
            raise ClaudeTitleSyncBlocked("aidream_server_unconfigured")
        try:
            return await fetch_complete_identity_inventory(
                client=client,
                jwt=str(token_row["access_token"]),
                provider="claude_code",
            )
        except IdentityInventoryBlocked as exc:
            raise ClaudeTitleSyncBlocked(exc.reason) from exc

    async def _start_operation(
        self, *, mode: str, parent_operation_id: str | None = None
    ) -> str:
        operation_id = str(uuid4())
        await self._db.execute(
            """INSERT INTO coding_session_metadata_sync_operations (
                   operation_id, mode, status, started_at, parent_operation_id
               ) VALUES (?, ?, 'running', ?, ?)""",
            (
                operation_id,
                mode,
                datetime.now(tz=UTC).isoformat(),
                parent_operation_id,
            ),
        )
        await self._db.commit()
        return operation_id

    async def _record_operation_row(
        self,
        *,
        operation_id: str,
        provider_session_id: str,
        local_values: dict[str, Any],
        cloud_values: dict[str, Any],
        chosen_values: dict[str, Any],
        direction: str,
        action: str,
        reason: str,
        state: str,
        receipt_id: int | None = None,
        write_intent_id: str | None = None,
        outcome: dict[str, Any] | None = None,
    ) -> None:
        await self._db.execute(
            """INSERT OR REPLACE INTO coding_session_metadata_sync_rows (
                   operation_id, provider_session_id, session_ref,
                   local_values_json, cloud_values_json, chosen_values_json,
                   direction, action, reason, state, receipt_id,
                   write_intent_id, outcome_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                operation_id,
                provider_session_id,
                _session_ref(provider_session_id),
                json.dumps(local_values, sort_keys=True),
                json.dumps(cloud_values, sort_keys=True),
                json.dumps(chosen_values, sort_keys=True),
                direction,
                action,
                reason,
                state,
                receipt_id,
                write_intent_id,
                json.dumps(outcome, sort_keys=True) if outcome is not None else None,
            ),
        )

    async def _finish_operation(
        self,
        operation_id: str,
        *,
        status: str,
        bound_sessions: int,
        compared_sessions: int,
        detected_sessions: int,
        enqueued_sessions: int,
        acknowledged_sessions: int,
        verified_sessions: int,
        failed_sessions: int,
        index_totals: dict[str, int],
        index_writable: bool,
        error_message: str | None = None,
    ) -> None:
        await self._db.execute(
            """UPDATE coding_session_metadata_sync_operations SET
                   status=?, completed_at=?, bound_sessions=?,
                   compared_sessions=?, detected_sessions=?,
                   enqueued_sessions=?, acknowledged_sessions=?,
                   verified_sessions=?, failed_sessions=?, index_files=?,
                   index_records=?, index_unreadable=?, index_truncated=?,
                   index_writable=?, error_message=?
               WHERE operation_id=?""",
            (
                status,
                datetime.now(tz=UTC).isoformat(),
                bound_sessions,
                compared_sessions,
                detected_sessions,
                enqueued_sessions,
                acknowledged_sessions,
                verified_sessions,
                failed_sessions,
                int(index_totals.get("files", 0)),
                int(index_totals.get("records", 0)),
                int(index_totals.get("unreadable", 0)),
                int(index_totals.get("files", 0) >= MAX_INDEX_FILES),
                int(index_writable),
                error_message,
                operation_id,
            ),
        )
        await self._db.commit()

    async def operation(
        self,
        operation_id: str,
        *,
        limit: int = 200,
        after_session_ref: str | None = None,
    ) -> dict[str, Any]:
        operation = await self._db.fetchone(
            """SELECT * FROM coding_session_metadata_sync_operations
               WHERE operation_id=?""",
            (operation_id,),
        )
        if operation is None:
            raise ClaudeTitleSyncBlocked("unknown_operation")
        params: list[Any] = [operation_id]
        cursor = ""
        if after_session_ref is not None:
            cursor = " AND session_ref > ?"
            params.append(after_session_ref)
        rows = await self._db.fetchall(
            f"""SELECT * FROM coding_session_metadata_sync_rows
                WHERE operation_id=?{cursor}
                ORDER BY session_ref LIMIT ?""",
            tuple([*params, limit + 1]),
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        items = []
        for row in page:
            local_values = json.loads(str(row["local_values_json"]))
            cloud_values = json.loads(str(row["cloud_values_json"]))
            items.append(
                {
                    "session_ref": str(row["session_ref"]),
                    "local": local_values,
                    "ai_matrx": cloud_values,
                    "comparisons": _field_comparisons(local_values, cloud_values),
                    "chosen": json.loads(str(row["chosen_values_json"])),
                    "direction": str(row["direction"]),
                    "action": str(row["action"]),
                    "reason": str(row["reason"]),
                    "state": str(row["state"]),
                    "receipt_id": row["receipt_id"],
                    "write_intent_id": row["write_intent_id"],
                    "outcome": (
                        json.loads(str(row["outcome_json"]))
                        if row["outcome_json"] is not None
                        else None
                    ),
                }
            )
        summary = dict(operation)
        return {
            "schema_version": 1,
            "operation": summary,
            "items": items,
            "has_more": has_more,
            "next_cursor": items[-1]["session_ref"] if has_more and items else None,
        }

    async def sync(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Serialize one preview/apply operation through the shared service."""
        async with self._sync_lock:
            return await self._sync_once(dry_run=dry_run)

    async def _sync_once(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Plan every bound row, then optionally execute the exact plan."""
        mode = "preview" if dry_run else "apply"
        operation_id = await self._start_operation(mode=mode)
        identities: list[dict[str, Any]] = []
        index: dict[str, ClaudeSessionIndexEntry] = {}
        index_totals = {"files": 0, "records": 0, "unreadable": 0}
        matched = no_labels = unchanged = queued = already_queued = 0
        unreadable_identity = failed = acknowledged = 0
        unmatched: list[str] = []
        titles: list[dict[str, str]] = []
        detected = written = refused = deferred = 0
        push_candidates = push_identical = 0
        push_samples: list[dict[str, str]] = []
        refusal_reasons: dict[str, int] = {}
        try:
            identities = await self._identities()
            index, index_totals = self._index_reader()
            index_writable = _record_paths_writable(index)
            sent = await self._sent_digests()
            pushed_titles = await self._pushed_titles()
            outbox = self._outbox or get_coding_session_bridge_outbox()

            for identity in identities:
                provider_session_id = identity.get("provider_session_id")
                if not isinstance(provider_session_id, str) or not provider_session_id:
                    unreadable_identity += 1
                    continue
                native_id = raw_session_id(provider_session_id)
                entry = index.get(native_id) if native_id else None
                cloud_values = _cloud_detail_values(identity)
                if entry is None:
                    unmatched.append(provider_session_id)
                    failed += 1
                    await self._record_operation_row(
                        operation_id=operation_id,
                        provider_session_id=provider_session_id,
                        local_values={},
                        cloud_values=cloud_values,
                        chosen_values=cloud_values,
                        direction="blocked",
                        action="none",
                        reason="local_session_not_found",
                        state="blocked",
                    )
                    await self._db.commit()
                    continue

                matched += 1
                local_values = entry.metadata_payload()
                if not local_values:
                    no_labels += 1
                digest = payload_digest(local_values)
                project_key = identity.get("provider_project_key")
                project_key = project_key if isinstance(project_key, str) else None
                target_title_raw = identity.get("conversation_title")
                target_title = (
                    " ".join(target_title_raw.split()).strip()
                    if isinstance(target_title_raw, str)
                    else ""
                )
                user_title_candidate = bool(
                    identity.get("title_source") == TITLE_SOURCE_USER
                    and target_title
                    and entry.title != target_title
                )
                if user_title_candidate:
                    push_candidates += 1
                    if len(push_samples) < 25:
                        push_samples.append(
                            {
                                "session_ref": _session_ref(provider_session_id),
                                "title": target_title,
                            }
                        )
                elif (
                    identity.get("title_source") == TITLE_SOURCE_USER
                    and target_title
                    and entry.title == target_title
                ):
                    push_identical += 1
                outbound = bool(
                    user_title_candidate
                    and entry.title == identity.get("claude_title")
                    and pushed_titles.get(provider_session_id)
                    != title_sha256(target_title)
                )
                chosen_values = dict(local_values)
                direction = "none"
                action = "none"
                reason = "already_acknowledged"
                state = "acknowledged"
                receipt_id: int | None = None
                intent_id: str | None = None
                outcome: dict[str, Any] | None = None

                if outbound:
                    chosen_values["title"] = target_title
                    direction = "ai_matrx_to_claude"
                    action = "write_local_then_observe"
                    reason = "user_title_changed_in_ai_matrx"
                    state = "detected"
                    detected += 1
                    if dry_run:
                        deferred += 0
                    else:
                        intent_id = str(uuid4())
                        desired_payload = {
                            "title": target_title,
                            "title_origin": TITLE_ORIGIN_AI_MATRX_USER,
                        }
                        now = datetime.now(tz=UTC).isoformat()
                        await self._db.execute(
                            """INSERT INTO coding_session_title_push_intents (
                                   intent_id, operation_id, provider_session_id,
                                   cli_session_id, desired_title,
                                   desired_payload_json, status, created_at, updated_at
                               ) VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?)""",
                            (
                                intent_id,
                                operation_id,
                                provider_session_id,
                                entry.cli_session_id,
                                target_title,
                                json.dumps(desired_payload, sort_keys=True),
                                now,
                                now,
                            ),
                        )
                        await self._db.commit()  # intent exists before file access
                        result = self._writer.write_title(
                            cli_session_id=entry.cli_session_id,
                            title=target_title,
                            record_paths=entry.record_paths,
                        )
                        outcome = {
                            **result.summary(),
                            "copies": [
                                {"status": item.status, "detail": item.detail}
                                for item in result.outcomes
                            ],
                        }
                        if result.applied:
                            written += 1
                            await self._record_pushed(
                                provider_session_id, title_sha256(target_title)
                            )
                            request = session_metadata_request(
                                provider_session_id=provider_session_id,
                                provider_project_key=project_key,
                                payload=desired_payload,
                            )
                            receipt = await outbox.enqueue(request)
                            receipt_id = receipt.receipt_id
                            queued += int(not receipt.duplicate)
                            already_queued += int(receipt.duplicate)
                            state = "enqueued"
                            intent_status = "convergence_queued"
                        else:
                            refused += 1
                            failed += 1
                            state = "failed"
                            intent_status = "partial" if result.written else "refused"
                            for refusal in result.summary()["refusal_reasons"]:
                                refusal_reasons[refusal] = (
                                    refusal_reasons.get(refusal, 0) + 1
                                )
                        await self._db.execute(
                            """UPDATE coding_session_title_push_intents SET
                                   status=?, receipt_id=?, copy_outcomes_json=?,
                                   error_message=?, updated_at=? WHERE intent_id=?""",
                            (
                                intent_status,
                                receipt_id,
                                json.dumps(outcome, sort_keys=True),
                                None
                                if result.applied
                                else "one_or_more_copies_refused",
                                datetime.now(tz=UTC).isoformat(),
                                intent_id,
                            ),
                        )
                        await self._db.commit()
                elif user_title_candidate:
                    # Claude moved too, or moved after an earlier write-down.
                    # Its local value wins and travels upward this pass.
                    deferred += 1
                    direction = "claude_to_ai_matrx"
                    action = "observe_ai_matrx"
                    reason = "claude_changed_since_last_agreement"
                    state = "detected"
                    detected += 1
                    if not dry_run:
                        request = session_metadata_request(
                            provider_session_id=provider_session_id,
                            provider_project_key=project_key,
                            payload=local_values,
                        )
                        receipt = await outbox.enqueue(request)
                        receipt_id = receipt.receipt_id
                        queued += int(not receipt.duplicate)
                        already_queued += int(receipt.duplicate)
                        state = "enqueued"
                elif sent.get(provider_session_id) != digest:
                    direction = "claude_to_ai_matrx"
                    action = "observe_ai_matrx"
                    reason = "provider_details_changed"
                    state = "detected"
                    detected += 1
                    if not dry_run:
                        request = session_metadata_request(
                            provider_session_id=provider_session_id,
                            provider_project_key=project_key,
                            payload=local_values,
                        )
                        receipt = await outbox.enqueue(request)
                        receipt_id = receipt.receipt_id
                        queued += int(not receipt.duplicate)
                        already_queued += int(receipt.duplicate)
                        state = "enqueued"
                else:
                    unchanged += 1
                    acknowledged += 1

                if entry.title and len(titles) < 25:
                    titles.append(
                        {
                            "session_ref": _session_ref(provider_session_id),
                            "title": entry.title,
                        }
                    )
                await self._record_operation_row(
                    operation_id=operation_id,
                    provider_session_id=provider_session_id,
                    local_values=local_values,
                    cloud_values=cloud_values,
                    chosen_values=chosen_values,
                    direction=direction,
                    action=action,
                    reason=reason,
                    state=state,
                    receipt_id=receipt_id,
                    write_intent_id=intent_id,
                    outcome=outcome,
                )
                await self._db.commit()

            if not dry_run:
                await self._sync_meta.set_last_sync(
                    "claude_session_metadata",
                    status="queued" if queued or already_queued else "current",
                )
                if queued or already_queued:
                    outbox.wake()
            operation_status = "partial" if failed else "completed"
            await self._finish_operation(
                operation_id,
                status=operation_status,
                bound_sessions=len(identities),
                compared_sessions=matched,
                detected_sessions=detected,
                enqueued_sessions=queued + already_queued,
                acknowledged_sessions=acknowledged,
                verified_sessions=0,
                failed_sessions=failed,
                index_totals=index_totals,
                index_writable=index_writable,
            )
        except Exception as exc:
            await self._finish_operation(
                operation_id,
                status="failed",
                bound_sessions=len(identities),
                compared_sessions=matched,
                detected_sessions=detected,
                enqueued_sessions=queued + already_queued,
                acknowledged_sessions=acknowledged,
                verified_sessions=0,
                failed_sessions=max(1, failed),
                index_totals=index_totals,
                index_writable=_record_paths_writable(index),
                error_message=str(exc),
            )
            raise

        detail = await self.operation(operation_id)
        return {
            "schema_version": 3,
            "source": "claude_desktop_session_index",
            "operation_id": operation_id,
            "operation": detail["operation"],
            "comparisons": detail["items"],
            "comparisons_truncated": detail["has_more"],
            "dry_run": dry_run,
            "bound_sessions": len(identities),
            "index_files": index_totals["files"],
            "index_records": index_totals["records"],
            "index_unreadable": index_totals.get("unreadable", 0),
            "index_limit_reached": index_totals["files"] >= MAX_INDEX_FILES,
            "index_writable": _record_paths_writable(index),
            "matched": matched,
            "unmatched": len(unmatched),
            "unmatched_session_ids": unmatched[:50],
            "unmatched_session_refs": [_session_ref(value) for value in unmatched[:50]],
            "matched_without_labels": no_labels,
            "unchanged": unchanged,
            "detected": detected,
            "queued": queued,
            "already_queued": already_queued,
            "acknowledged": acknowledged,
            "verified": 0,
            "unreadable_identities": unreadable_identity,
            "sample_titles": titles,
            "push_down": {
                "user_titled_sessions": push_candidates,
                "written": written,
                "already_identical": push_identical,
                "deferred_to_claude": deferred,
                "refused": refused,
                "refusal_reasons": refusal_reasons,
                "sample_titles": push_samples,
            },
        }

    async def verify(self, operation_id: str) -> dict[str, Any]:
        """Refetch AI Matrx and reread Claude, then prove each prior outcome."""
        async with self._sync_lock:
            source = await self._db.fetchone(
                """SELECT operation_id FROM coding_session_metadata_sync_operations
                   WHERE operation_id=?""",
                (operation_id,),
            )
            if source is None:
                raise ClaudeTitleSyncBlocked("unknown_operation")
            verification_id = await self._start_operation(
                mode="verify", parent_operation_id=operation_id
            )
            identities = await self._identities()
            by_id = {
                str(item["provider_session_id"]): item
                for item in identities
                if isinstance(item.get("provider_session_id"), str)
            }
            index, index_totals = self._index_reader()
            sent = await self._sent_digests()
            previous = await self._db.fetchall(
                """SELECT * FROM coding_session_metadata_sync_rows
                   WHERE operation_id=? ORDER BY session_ref""",
                (operation_id,),
            )
            verified = acknowledged = pending = failed = 0
            for prior in previous:
                provider_session_id = str(prior["provider_session_id"])
                identity = by_id.get(provider_session_id)
                native_id = raw_session_id(provider_session_id)
                entry = index.get(native_id) if native_id else None
                current_local = entry.metadata_payload() if entry else {}
                current_cloud = _cloud_detail_values(identity) if identity else {}
                chosen = json.loads(str(prior["chosen_values_json"]))
                action = str(prior["action"])
                state = "failed"
                reason = "session_missing_during_verification"
                receipt_id = prior["receipt_id"]
                intent_id = prior["write_intent_id"]
                outcome: dict[str, Any] = {
                    "local_matches_chosen": all(
                        current_local.get(field) == value
                        for field, value in chosen.items()
                    ),
                    "ai_matrx_fields_observed": sorted(current_cloud),
                }
                expected_digest: str | None = None
                if action == "write_local_then_observe" and intent_id is not None:
                    intent = await self._db.fetchone(
                        """SELECT desired_payload_json FROM coding_session_title_push_intents
                           WHERE intent_id=?""",
                        (intent_id,),
                    )
                    if intent is not None:
                        desired_payload = json.loads(
                            str(intent["desired_payload_json"])
                        )
                        expected_digest = payload_digest(desired_payload)
                        desired_title = desired_payload.get("title")
                        cloud_converged = bool(
                            identity
                            and identity.get("claude_title") == desired_title
                            and identity.get("conversation_title") == desired_title
                        )
                        local_converged = current_local.get("title") == desired_title
                        acked = sent.get(provider_session_id) == expected_digest
                        outcome.update(
                            {
                                "local_converged": local_converged,
                                "ai_matrx_converged": cloud_converged,
                                "acknowledged": acked,
                            }
                        )
                        if local_converged and acked and cloud_converged:
                            state, reason = "verified", "both_sides_reread_equal"
                            verified += 1
                        elif local_converged and acked:
                            state, reason = "acknowledged", "awaiting_ai_matrx_reread"
                            acknowledged += 1
                        elif local_converged:
                            state, reason = "enqueued", "awaiting_acknowledgement"
                            pending += 1
                        else:
                            failed += 1
                elif entry is not None:
                    expected_digest = payload_digest(current_local)
                    acked = sent.get(provider_session_id) == expected_digest
                    observed_fields = set(current_cloud)
                    exposed_equal = bool(observed_fields) and all(
                        current_cloud.get(field) == current_local.get(field)
                        for field in observed_fields
                    )
                    outcome.update(
                        {
                            "acknowledged": acked,
                            "observed_fields_equal": exposed_equal,
                        }
                    )
                    if acked and exposed_equal:
                        state, reason = "verified", "reread_fields_equal"
                        verified += 1
                    elif acked:
                        state, reason = "acknowledged", "cloud_detail_not_yet_exposed"
                        acknowledged += 1
                    else:
                        state = "enqueued" if receipt_id is not None else "detected"
                        reason = "awaiting_acknowledgement"
                        pending += 1
                else:
                    failed += 1

                await self._record_operation_row(
                    operation_id=verification_id,
                    provider_session_id=provider_session_id,
                    local_values=current_local,
                    cloud_values=current_cloud,
                    chosen_values=chosen,
                    direction=str(prior["direction"]),
                    action="verify",
                    reason=reason,
                    state=state,
                    receipt_id=receipt_id,
                    write_intent_id=intent_id,
                    outcome=outcome,
                )
                if intent_id is not None and state in {"verified", "acknowledged"}:
                    await self._db.execute(
                        """UPDATE coding_session_title_push_intents
                           SET status=?, updated_at=? WHERE intent_id=?""",
                        (state, datetime.now(tz=UTC).isoformat(), intent_id),
                    )
                await self._db.commit()

            await self._finish_operation(
                verification_id,
                status="partial" if failed else "completed",
                bound_sessions=len(previous),
                compared_sessions=len(previous),
                detected_sessions=0,
                enqueued_sessions=pending,
                acknowledged_sessions=acknowledged,
                verified_sessions=verified,
                failed_sessions=failed,
                index_totals=index_totals,
                index_writable=_record_paths_writable(index),
            )
            return await self.operation(verification_id)

    async def retry_push_intent(self, intent_id: str) -> dict[str, Any]:
        """Retry one durable write/convergence intent with fresh local fences."""
        async with self._sync_lock:
            intent = await self._db.fetchone(
                """SELECT * FROM coding_session_title_push_intents WHERE intent_id=?""",
                (intent_id,),
            )
            if intent is None:
                raise ClaudeTitleSyncBlocked("unknown_push_intent")
            identities = await self._identities()
            identity = next(
                (
                    item
                    for item in identities
                    if item.get("provider_session_id") == intent["provider_session_id"]
                ),
                None,
            )
            if identity is None:
                raise ClaudeTitleSyncBlocked("binding_no_longer_available")
            index, index_totals = self._index_reader()
            entry = index.get(str(intent["cli_session_id"]))
            if entry is None:
                raise ClaudeTitleSyncBlocked("local_session_not_found")
            retry_id = await self._start_operation(
                mode="retry", parent_operation_id=str(intent["operation_id"])
            )
            title = str(intent["desired_title"])
            result = self._writer.write_title(
                cli_session_id=entry.cli_session_id,
                title=title,
                record_paths=entry.record_paths,
            )
            payload = json.loads(str(intent["desired_payload_json"]))
            receipt_id: int | None = None
            state = "failed"
            if result.applied:
                await self._record_pushed(
                    str(intent["provider_session_id"]), title_sha256(title)
                )
                request = session_metadata_request(
                    provider_session_id=str(intent["provider_session_id"]),
                    provider_project_key=(
                        str(identity["provider_project_key"])
                        if identity.get("provider_project_key") is not None
                        else None
                    ),
                    payload=payload,
                )
                receipt = await (
                    self._outbox or get_coding_session_bridge_outbox()
                ).enqueue(request)
                receipt_id = receipt.receipt_id
                state = "enqueued"
            outcome = {
                **result.summary(),
                "copies": [
                    {"status": item.status, "detail": item.detail}
                    for item in result.outcomes
                ],
            }
            await self._db.execute(
                """UPDATE coding_session_title_push_intents SET
                       status=?, receipt_id=?, copy_outcomes_json=?,
                       error_message=?, updated_at=? WHERE intent_id=?""",
                (
                    "convergence_queued" if result.applied else "refused",
                    receipt_id,
                    json.dumps(outcome, sort_keys=True),
                    None if result.applied else "one_or_more_copies_refused",
                    datetime.now(tz=UTC).isoformat(),
                    intent_id,
                ),
            )
            await self._record_operation_row(
                operation_id=retry_id,
                provider_session_id=str(intent["provider_session_id"]),
                local_values=entry.metadata_payload(),
                cloud_values=_cloud_detail_values(identity),
                chosen_values={**entry.metadata_payload(), "title": title},
                direction="ai_matrx_to_claude",
                action="retry_write_and_observe",
                reason="user_retried_push_intent",
                state=state,
                receipt_id=receipt_id,
                write_intent_id=intent_id,
                outcome=outcome,
            )
            await self._db.commit()
            await self._finish_operation(
                retry_id,
                status="completed" if result.applied else "partial",
                bound_sessions=1,
                compared_sessions=1,
                detected_sessions=1,
                enqueued_sessions=int(result.applied),
                acknowledged_sessions=0,
                verified_sessions=0,
                failed_sessions=int(not result.applied),
                index_totals=index_totals,
                index_writable=_record_paths_writable(index),
            )
            return await self.operation(retry_id)

    async def status(self) -> dict[str, Any]:
        index, index_totals = self._index_reader()
        sync = await self._sync_meta.get_last_sync("claude_session_metadata")
        row = await self._db.fetchone(
            "SELECT count(*) AS n FROM claude_session_metadata_sent"
        )
        pushed = await self._db.fetchone(
            "SELECT count(*) AS n FROM claude_session_title_pushed"
        )
        latest = await self._db.fetchone(
            """SELECT * FROM coding_session_metadata_sync_operations
               ORDER BY started_at DESC LIMIT 1"""
        )
        intents = await self._db.fetchall(
            """SELECT status, count(*) AS count
               FROM coding_session_title_push_intents GROUP BY status"""
        )
        return {
            "schema_version": 3,
            "source": "claude_desktop_session_index",
            "index_writable": _record_paths_writable(index),
            "pushed_sessions": int(pushed["n"]) if pushed else 0,
            "index_available": index_totals["files"] > 0,
            "index_files": index_totals["files"],
            "index_records": index_totals["records"],
            "index_unreadable": index_totals.get("unreadable", 0),
            "index_limit": MAX_INDEX_FILES,
            "index_limit_reached": index_totals["files"] >= MAX_INDEX_FILES,
            "synced_sessions": int(row["n"]) if row else 0,
            "acknowledged_sessions": int(row["n"]) if row else 0,
            "latest_operation": dict(latest) if latest is not None else None,
            "push_intents_by_state": {
                str(item["status"]): int(item["count"]) for item in intents
            },
            "last_sync": sync,
        }


_reconciler: ClaudeSessionMetadataReconciler | None = None


def get_claude_session_metadata_reconciler() -> ClaudeSessionMetadataReconciler:
    """One engine-owned reconciler: its lock serializes every HTTP operation."""
    global _reconciler
    if _reconciler is None:
        _reconciler = ClaudeSessionMetadataReconciler()
    return _reconciler


__all__ = [
    "ClaudeSessionMetadataReconciler",
    "ClaudeTitleSyncBlocked",
    "get_claude_session_metadata_reconciler",
    "payload_digest",
    "raw_session_id",
    "session_metadata_request",
]
