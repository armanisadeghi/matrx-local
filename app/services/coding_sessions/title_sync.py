"""Pull-sync of Claude's own session labels onto already-mirrored sessions.

Arman's ruling (2026-08-16): *"Those labels cannot be different. They must be
exactly the same, and they must remain in sync. So if it's changed in Claude
Code, we are able to update it in our system."*

The join is exact. aidream answers ``GET /coding-sessions/sessions`` with the
owner's Claude bindings; Claude's desktop session index answers with the label
it shows for each ``cliSessionId``. This module matches the two and enqueues a
``SessionMetadata`` observation for every session whose labels changed, so the
platform title tracks a rename made in Claude Code on the next sync pass.

Deliberate boundaries:

- **Only already-mirrored sessions.** Labels of local sessions the user never
  mirrored never leave this machine; the server list is the allowlist.
- **Metadata plane only.** ``SessionMetadata`` updates a binding's labels; it
  never mints a binding, never appends a transcript entry, and applies the
  server's title ladder (a title the user set in AI Matrx always wins).
- **Idempotent.** The exact payload last enqueued per session is recorded
  locally, so an unchanged label costs zero network work.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from uuid import UUID

from app.common.system_logger import get_logger
from app.services.aidream.client import (
    AIDreamClient,
    AIDreamError,
    AIDreamOfflineError,
    get_aidream_client,
)
from app.services.coding_sessions.claude_session_index import (
    ClaudeSessionIndexEntry,
    read_session_index,
)
from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.service import (
    SESSION_METADATA_EVENT,
    CodingSessionBridgeOutbox,
    get_coding_session_bridge_outbox,
)
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import SyncMetaRepo, TokenRepo

logger = get_logger()

IDENTITY_PATH = "/coding-sessions/sessions"
MAX_IDENTITY_ROWS = 1000
_CLAUDE_SDK_PREFIX = "claude-sdk:"


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
    """Matches bound Claude sessions to Claude's own labels and syncs them."""

    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        outbox: CodingSessionBridgeOutbox | None = None,
        client: AIDreamClient | None = None,
        index_reader: Any = read_session_index,
    ) -> None:
        self._db = db or get_db()
        self._outbox = outbox
        self._client = client
        self._index_reader = index_reader
        self._tokens = TokenRepo(self._db)
        self._sync_meta = SyncMetaRepo(self._db)

    async def _sent_digests(self) -> dict[str, str]:
        rows = await self._db.fetchall(
            "SELECT provider_session_id, payload_sha256 FROM claude_session_metadata_sent"
        )
        return {
            str(row["provider_session_id"]): str(row["payload_sha256"]) for row in rows
        }

    async def _record_sent(self, provider_session_id: str, digest: str) -> None:
        await self._db.execute(
            """INSERT INTO claude_session_metadata_sent
                   (provider_session_id, payload_sha256, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(provider_session_id) DO UPDATE SET
                   payload_sha256 = excluded.payload_sha256,
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
            response = await client.get(
                f"{IDENTITY_PATH}?provider=claude_code&limit={MAX_IDENTITY_ROWS}",
                jwt=str(token_row["access_token"]),
            )
        except AIDreamOfflineError as exc:
            raise ClaudeTitleSyncBlocked("aidream_unreachable") from exc
        except AIDreamError as exc:
            raise ClaudeTitleSyncBlocked(f"aidream_error:{exc}") from exc
        sessions = response.get("sessions") if isinstance(response, dict) else None
        if not isinstance(sessions, list):
            raise ClaudeTitleSyncBlocked("identity_list_malformed")
        return [row for row in sessions if isinstance(row, dict)]

    async def sync(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Reconcile every bound Claude session against Claude's own labels."""
        identities = await self._identities()
        index, index_totals = self._index_reader()
        sent = await self._sent_digests()

        matched = 0
        unmatched: list[str] = []
        no_labels = 0
        unchanged = 0
        queued = 0
        unreadable_identity = 0
        titles: list[dict[str, str]] = []

        for identity in identities:
            provider_session_id = identity.get("provider_session_id")
            if not isinstance(provider_session_id, str) or not provider_session_id:
                unreadable_identity += 1
                continue
            native_id = raw_session_id(provider_session_id)
            entry: ClaudeSessionIndexEntry | None = (
                index.get(native_id) if native_id else None
            )
            if entry is None:
                unmatched.append(provider_session_id)
                continue
            matched += 1
            payload = entry.metadata_payload()
            if not payload:
                no_labels += 1
                continue
            digest = payload_digest(payload)
            if sent.get(provider_session_id) == digest:
                unchanged += 1
                continue
            if dry_run:
                queued += 1
                if entry.title and len(titles) < 25:
                    titles.append(
                        {
                            "provider_session_id": provider_session_id,
                            "title": entry.title,
                        }
                    )
                continue
            project_key = identity.get("provider_project_key")
            request = session_metadata_request(
                provider_session_id=provider_session_id,
                provider_project_key=(
                    project_key if isinstance(project_key, str) else None
                ),
                payload=payload,
            )
            outbox = self._outbox or get_coding_session_bridge_outbox()
            await outbox.enqueue(request)
            await self._record_sent(provider_session_id, digest)
            queued += 1
            if entry.title and len(titles) < 25:
                titles.append(
                    {
                        "provider_session_id": provider_session_id,
                        "title": entry.title,
                    }
                )

        if not dry_run:
            await self._sync_meta.set_last_sync(
                "claude_session_metadata",
                status="queued" if queued else "current",
            )
            if queued:
                (self._outbox or get_coding_session_bridge_outbox()).wake()
        logger.info(
            "[coding_session_bridge] claude label sync bound=%s matched=%s "
            "unmatched=%s queued=%s unchanged=%s",
            len(identities),
            matched,
            len(unmatched),
            queued,
            unchanged,
        )
        return {
            "schema_version": 1,
            "source": "claude_desktop_session_index",
            "dry_run": dry_run,
            "bound_sessions": len(identities),
            "index_files": index_totals["files"],
            "index_records": index_totals["records"],
            "matched": matched,
            "unmatched": len(unmatched),
            "unmatched_session_ids": unmatched[:50],
            "matched_without_labels": no_labels,
            "unchanged": unchanged,
            "queued": queued,
            "unreadable_identities": unreadable_identity,
            "sample_titles": titles,
        }

    async def status(self) -> dict[str, Any]:
        index, index_totals = self._index_reader()
        sync = await self._sync_meta.get_last_sync("claude_session_metadata")
        row = await self._db.fetchone(
            "SELECT count(*) AS n FROM claude_session_metadata_sent"
        )
        return {
            "schema_version": 1,
            "source": "claude_desktop_session_index",
            "index_available": index_totals["files"] > 0,
            "index_files": index_totals["files"],
            "index_records": index_totals["records"],
            "synced_sessions": int(row["n"]) if row else 0,
            "last_sync": sync,
        }


__all__ = [
    "ClaudeSessionMetadataReconciler",
    "ClaudeTitleSyncBlocked",
    "payload_digest",
    "raw_session_id",
    "session_metadata_request",
]
