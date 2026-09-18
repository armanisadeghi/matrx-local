"""The Claude Code adapter — the same interface as every other provider.

Claude Code's reader, its persisted index and its per-account record rules are
unchanged and still live in :mod:`app.services.coding_sessions.claude_overview`
and :mod:`app.services.coding_sessions.claude_index_store`. This module only
puts that machinery behind the ONE interface the screen now asks through, so
"Claude Code" stops being the shape of the list and becomes one row's value.

TWO KINDS OF ROW, AS BEFORE. A conversation Claude's sidebar indexed, and a
transcript on disk that it never did (80 of them and 113 MB here on
2026-09-11). Both are listed; ``in_claude_sidebar`` says which, and that field
is ``None`` for every other provider because none of them has a sidebar.
"""

from __future__ import annotations

from typing import Any

from app.services.coding_sessions.continuation import continuation_hint
from app.services.coding_sessions.session_providers import (
    ProviderListing,
    SessionSummary,
)

PROVIDER = "claude_code"


class ClaudeCodeSessionProvider:
    """Claude Code's sessions, from the index that already existed."""

    provider = PROVIDER

    async def listing(self) -> ProviderListing:
        from app.services.coding_sessions.claude_overview import (
            _transcript_only_rows,
            index_report,
            index_snapshot,
            list_accounts,
            start_index_refresh,
        )

        snapshot = await index_snapshot()
        start_index_refresh()
        entries = snapshot.entries
        transcripts = snapshot.transcripts
        rows: list[SessionSummary] = []
        pinned_total = 0
        for session_id, entry in entries.items():
            size, _mtime_ns = transcripts.get(session_id, (0, 0))
            if entry.is_pinned:
                pinned_total += 1
            rows.append(
                SessionSummary(
                    provider=PROVIDER,
                    session_id=session_id,
                    title=entry.title or "Untitled",
                    title_source=entry.title_source,
                    project=entry.workspace_name,
                    last_activity_at=int(entry.last_activity_at or 0),
                    bytes=size,
                    on_disk=size > 0,
                    pinned=bool(entry.is_pinned),
                    pinned_rank=entry.pinned_rank,
                    category=entry.category,
                    archived=bool(entry.is_archived),
                    in_claude_sidebar=True,
                    facts={
                        "git_branch": entry.git_branch,
                        "worktree_name": entry.worktree_name,
                    },
                )
            )
        # Everything on disk that Claude never indexed. Same row shape, and
        # the overview judges its state by exactly the same rules.
        orphan_ids = sorted(set(transcripts) - set(entries))
        transcript_only = 0
        for row in _transcript_only_rows(
            orphan_ids, transcripts, snapshot.orphan_summaries
        ):
            transcript_only += 1
            rows.append(
                SessionSummary(
                    provider=PROVIDER,
                    session_id=row["session_id"],
                    title=row["title"],
                    title_source=None,
                    project=row["project"],
                    last_activity_at=int(row["mtime_ns"] // 1_000_000),
                    bytes=row["bytes"],
                    on_disk=row["bytes"] > 0,
                    pinned=False,
                    pinned_rank=None,
                    category=None,
                    archived=False,
                    in_claude_sidebar=False,
                )
            )
        totals = snapshot.totals
        return ProviderListing(
            provider=PROVIDER,
            rows=rows,
            index=index_report(snapshot),
            totals={
                "sessions": len(rows),
                "pinned": pinned_total,
                "transcript_only": transcript_only,
                "transcripts_on_disk": len(transcripts),
                "index_files_read": totals.get("files", 0),
                "unreadable": totals.get("unreadable", 0),
                "index_limit_reached": bool(totals.get("truncated")),
                "accounts": list_accounts(snapshot.accounts),
            },
            note=self._note(snapshot),
            supports_pins=True,
            supports_resume=True,
        )

    def start_refresh(self) -> bool:
        from app.services.coding_sessions.claude_overview import start_index_refresh

        return start_index_refresh()

    @staticmethod
    def _note(snapshot: Any) -> str | None:
        totals = snapshot.totals
        if totals.get("truncated"):
            return (
                "Claude's own session index hit this reader's file cap on this Mac, "
                "so the Claude Code rows below are incomplete."
            )
        unreadable = int(totals.get("unreadable", 0) or 0)
        if unreadable:
            return (
                f"{unreadable:,} Claude sidebar record(s) could not be parsed. They "
                "are counted, never dropped; a conversation whose every record is "
                "unreadable still appears if its transcript is on disk."
            )
        return None


def continuation(session_id: str) -> dict[str, Any]:
    return continuation_hint(session_id, PROVIDER)


__all__ = ["PROVIDER", "ClaudeCodeSessionProvider"]
