"""Backfill Claude Code sessions the hook path failed to deliver — Matrx Local's wiring.

The reconciler itself (THE ERA RULE, the bounded oldest-first pass, the retry budget, the loud
WARNING) lives in ``matrx_coding_history.reconciler`` (packages/matrx-coding-history), shared
with Matrx 2; read its module docstring for the consent boundary. This module hands it what only
Matrx Local has: the ``claude_capture_backfill`` ledger, the sync daemon's token, the AIDream
client, the opt-in raw transcript backup, and the engine's logger — and keeps the singleton the
engine starts and stops.
"""

from __future__ import annotations

from typing import Any

from matrx_coding_history.reconciler import (
    AUTO_BACKFILL_ENABLED,
    BATCH_BYTE_BUDGET,
    MAX_ATTEMPTS,
    PASS_INTERVAL_SECONDS,
    CaptureReconcileBlocked,
    _parse_iso,
    _sdk_identity,
)
from matrx_coding_history.reconciler import (
    ClaudeCaptureReconciler as _SharedReconciler,
)

from app.common.system_logger import get_logger
from app.services.aidream.client import AIDreamClient, get_aidream_client
from app.services.coding_sessions.claude_history import ClaudeHistoryImporter
from app.services.coding_sessions.history_ports import SqliteBackfillLedger
from app.services.coding_sessions.identity_client import (
    IdentityInventoryBlocked,
    fetch_complete_identity_inventory,
)
from app.services.coding_sessions.raw_backup_scheduler import schedule_notify
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import TokenRepo

logger = get_logger()


class ClaudeCaptureReconciler(_SharedReconciler):
    """Diffs local Claude sessions against the platform and backfills the gap."""

    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        importer: ClaudeHistoryImporter | None = None,
        client: AIDreamClient | None = None,
    ) -> None:
        self._db = db or get_db()
        self._client = client
        self._tokens = TokenRepo(self._db)
        super().__init__(
            importer=importer or ClaudeHistoryImporter(db=self._db),
            identity_source=self._cloud_identities,
            ledger=SqliteBackfillLedger(self._db),
            logger=logger,
            # Looked up at call time, exactly as the module-level call it replaces.
            known_session_changed=lambda *args: schedule_notify(*args),
        )

    async def _cloud_identities(self) -> list[dict[str, Any]]:
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
            return await fetch_complete_identity_inventory(
                client=client,
                jwt=str(token_row["access_token"]),
                provider="claude_code",
            )
        except IdentityInventoryBlocked as exc:
            raise CaptureReconcileBlocked(exc.reason) from exc


_reconciler: ClaudeCaptureReconciler | None = None


def get_claude_capture_reconciler() -> ClaudeCaptureReconciler:
    global _reconciler
    if _reconciler is None:
        _reconciler = ClaudeCaptureReconciler()
    return _reconciler


__all__ = [
    "AUTO_BACKFILL_ENABLED",
    "BATCH_BYTE_BUDGET",
    "CaptureReconcileBlocked",
    "ClaudeCaptureReconciler",
    "MAX_ATTEMPTS",
    "PASS_INTERVAL_SECONDS",
    "_parse_iso",
    "_sdk_identity",
    "get_claude_capture_reconciler",
]
