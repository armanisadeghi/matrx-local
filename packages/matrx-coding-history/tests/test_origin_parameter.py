"""Which desktop app delivered is recorded: Matrx Local keeps ``matrx_local`` (the default), Matrx 2
passes ``matrx_desktop``. RED WHEN the importer hard-codes the origin again."""

from __future__ import annotations

import asyncio
from pathlib import Path

from coding_history_fixture import write_fixture
from coding_history_support import RecordingSink, SyncMeta, User, account_a
from matrx_coding_history.importer import ClaudeHistoryImporter, ClaudeHistoryImportRequest, ClaudeHistorySelection

SESSION = "6f1c3a52-9a2e-4d8e-b6f1-2c4d5e6f7a80"


def _origins(tmp_path: Path, **kwargs: str) -> set[str]:
    config_dir = tmp_path / ".claude"
    write_fixture(config_dir, SESSION)
    sink = RecordingSink()
    importer = ClaudeHistoryImporter(
        sink=lambda: sink, user=User(), sync_meta=SyncMeta(), config_dir=config_dir,
        sessions_dir=tmp_path / "no-index", account_reader=account_a, **kwargs,
    )

    async def run() -> None:
        _account, sources = await importer.capture_inventory()
        source = sources[0]
        fresh = await importer.capture_revisions({(SESSION, source.project_key)})
        revision = fresh[(SESSION, source.project_key)].source_revision
        assert revision is not None
        await importer.import_selected(
            ClaudeHistoryImportRequest(
                provider_account_key="a" * 64,
                sessions=[ClaudeHistorySelection(session_id=SESSION, provider_project_key=source.project_key, source_revision=revision)],
            )
        )

    asyncio.run(run())
    return {str(r.origin) for r in sink.requests if str(r.action) == "append_native"}


def test_matrx_local_keeps_its_origin(tmp_path: Path) -> None:
    assert _origins(tmp_path) == {"matrx_local"}


def test_matrx_2_stamps_matrx_desktop(tmp_path: Path) -> None:
    assert _origins(tmp_path, origin="matrx_desktop") == {"matrx_desktop"}
