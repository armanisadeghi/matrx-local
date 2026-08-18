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
from typing import Any
from uuid import UUID

from app.common.system_logger import get_logger
from app.services.aidream.client import (
    AIDreamClient,
    AIDreamError,
    AIDreamOfflineError,
    get_aidream_client,
)
from app.services.coding_sessions.claude_label_writer import (
    ClaudeSessionIndexWriter,
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
        """Serialize title reconciliation so reports and mutation fences agree."""
        async with self._sync_lock:
            return await self._sync_once(dry_run=dry_run)

    async def _sync_once(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Reconcile every bound Claude session's label in BOTH directions."""
        identities = await self._identities()
        index, index_totals = self._index_reader()
        sent = await self._sent_digests()

        matched = 0
        unmatched: list[str] = []
        no_labels = 0
        unchanged = 0
        queued = 0
        already_queued = 0
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
            receipt = await outbox.enqueue(request)
            if receipt.duplicate:
                already_queued += 1
            else:
                queued += 1
            if entry.title and len(titles) < 25:
                titles.append(
                    {
                        "provider_session_id": provider_session_id,
                        "title": entry.title,
                    }
                )

        pushed = await self._push_titles_down(identities, index, dry_run=dry_run)

        if not dry_run:
            await self._sync_meta.set_last_sync(
                "claude_session_metadata",
                status=(
                    "queued"
                    if queued or already_queued or pushed["written"]
                    else "current"
                ),
            )
            if queued or already_queued or pushed["written"]:
                (self._outbox or get_coding_session_bridge_outbox()).wake()
        logger.info(
            "[coding_session_bridge] claude label sync bound=%s matched=%s "
            "unmatched=%s queued=%s already_queued=%s unchanged=%s "
            "pushed_down=%s refused=%s",
            len(identities),
            matched,
            len(unmatched),
            queued,
            already_queued,
            unchanged,
            pushed["written"],
            pushed["refused"],
        )
        return {
            "schema_version": 2,
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
            "already_queued": already_queued,
            "unreadable_identities": unreadable_identity,
            "sample_titles": titles,
            "push_down": pushed,
        }

    async def _push_titles_down(
        self,
        identities: list[dict[str, Any]],
        index: dict[str, ClaudeSessionIndexEntry],
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        """AI Matrx → Claude Code: write a user's rename into Claude's index.

        Only a title the server reports as ``title_source="user"`` travels this
        way, and only for a session the server already binds — the identity
        list is the allowlist for the write, exactly as it is for the read.

        The one thing that stops a push is evidence that Claude Code moved
        too: ``claude_title`` is the label the server last heard from Claude,
        so an index title that no longer equals it means Claude Code was
        renamed since. Claude wins there, and the inbound leg carries its value
        up rather than the two sides overwriting each other.
        """
        already = await self._pushed_titles()
        candidates = 0
        written = 0
        unchanged = 0
        refused = 0
        deferred = 0
        reasons: dict[str, int] = {}
        samples: list[dict[str, str]] = []

        for identity in identities:
            provider_session_id = identity.get("provider_session_id")
            if not isinstance(provider_session_id, str) or not provider_session_id:
                continue
            if identity.get("title_source") != TITLE_SOURCE_USER:
                continue
            raw_title = identity.get("conversation_title")
            if not isinstance(raw_title, str):
                continue
            title = " ".join(raw_title.split()).strip()
            if not title:
                continue
            native_id = raw_session_id(provider_session_id)
            entry = index.get(native_id) if native_id else None
            if entry is None or not entry.record_paths:
                continue
            if entry.title == title:
                # Both sides already show it; the ledger only records what we
                # wrote, so a label that matched on its own costs nothing.
                unchanged += 1
                continue
            candidates += 1
            if entry.title != identity.get("claude_title"):
                # Claude Code's label moved away from the one the server last
                # recorded, so Claude Code was renamed too. It wins.
                deferred += 1
                continue
            if already.get(provider_session_id) == title_sha256(title):
                # We wrote exactly this and Claude Code has since moved on. Its
                # value is newer; the inbound leg will pull it up next pass.
                deferred += 1
                continue
            if len(samples) < 25:
                samples.append(
                    {"provider_session_id": provider_session_id, "title": title}
                )
            if dry_run:
                continue
            result = self._writer.write_title(
                cli_session_id=entry.cli_session_id,
                title=title,
                record_paths=entry.record_paths,
            )
            if not result.applied:
                refused += 1
                for reason in result.summary()["refusal_reasons"]:
                    reasons[reason] = reasons.get(reason, 0) + 1
                continue
            written += 1
            await self._record_pushed(provider_session_id, title_sha256(title))
            # Tell the server the label it now shares with Claude Code, marked
            # as the user's. This keeps `applied_title` equal to what both
            # sides show, which is what later lets a rename made in Claude Code
            # win instead of being refused as an overwrite of a user title.
            project_key = identity.get("provider_project_key")
            request = session_metadata_request(
                provider_session_id=provider_session_id,
                provider_project_key=(
                    project_key if isinstance(project_key, str) else None
                ),
                payload={
                    "title": title,
                    "title_origin": TITLE_ORIGIN_AI_MATRX_USER,
                },
            )
            await (self._outbox or get_coding_session_bridge_outbox()).enqueue(request)

        return {
            "user_titled_sessions": candidates,
            "written": written,
            "already_identical": unchanged,
            "deferred_to_claude": deferred,
            "refused": refused,
            "refusal_reasons": reasons,
            "sample_titles": samples,
        }

    async def status(self) -> dict[str, Any]:
        index, index_totals = self._index_reader()
        sync = await self._sync_meta.get_last_sync("claude_session_metadata")
        row = await self._db.fetchone(
            "SELECT count(*) AS n FROM claude_session_metadata_sent"
        )
        pushed = await self._db.fetchone(
            "SELECT count(*) AS n FROM claude_session_title_pushed"
        )
        return {
            "schema_version": 2,
            "source": "claude_desktop_session_index",
            "index_writable": index_totals["files"] > 0,
            "pushed_sessions": int(pushed["n"]) if pushed else 0,
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
