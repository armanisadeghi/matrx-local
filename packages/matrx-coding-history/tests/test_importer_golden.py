"""The shared importer builds, byte for byte, the envelopes Matrx Local built before it moved.

``golden/claude_history_envelopes.json`` was captured from Matrx Local's own
``ClaudeHistoryImporter`` at the commit before the extraction (matrx-local 1a708167f7), over the
history in ``coding_history_fixture.py``. Values derived from the temp path are placeholders.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from coding_history_fixture import write_fixture
from matrx_coding_history.importer import (
    ClaudeHistoryImporter,
    ClaudeHistoryImportRequest,
    ClaudeHistorySelection,
)
from coding_history_support import RecordingSink, SyncMeta, User, account_a

GOLDEN = Path(__file__).parent / "golden" / "claude_history_envelopes.json"
SESSION = "6f1c3a52-9a2e-4d8e-b6f1-2c4d5e6f7a80"


def test_envelopes_match_the_matrx_local_golden(tmp_path: Path) -> None:
    config_dir = tmp_path / ".claude"
    write_fixture(config_dir, SESSION)
    sink = RecordingSink()
    sync_meta = SyncMeta()
    importer = ClaudeHistoryImporter(
        sink=lambda: sink,
        user=User(),
        sync_meta=sync_meta,
        config_dir=config_dir,
        sessions_dir=tmp_path / "no-index",
        account_reader=account_a,
    )

    async def run() -> tuple[dict[str, object], str, str]:
        _account, sources = await importer.capture_inventory()
        source = sources[0]
        fresh = await importer.capture_revisions({(SESSION, source.project_key)})
        revision = fresh[(SESSION, source.project_key)].source_revision
        assert revision is not None
        result = await importer.import_selected(
            ClaudeHistoryImportRequest(
                provider_account_key="a" * 64,
                sessions=[
                    ClaudeHistorySelection(
                        session_id=SESSION, provider_project_key=source.project_key, source_revision=revision
                    )
                ],
            )
        )
        return result, source.project_key, revision

    result, project_key, revision = asyncio.run(run())
    digest = hashlib.sha256(project_key.encode()).hexdigest()
    envelopes = []
    for request in sink.requests:
        text = json.dumps(
            request.model_dump(mode="json", exclude_none=True), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        text = text.replace(project_key, "<PROJECT_KEY>").replace(digest, "<PROJECT_DIGEST>")
        if request.conversation is not None:
            text = text.replace(str(request.conversation.conversation_id), "<CONVERSATION>")
        envelopes.append(json.loads(text))
    golden = json.loads(GOLDEN.read_text())
    assert revision == golden["revision"]
    assert envelopes == golden["envelopes"]
    assert {k: v for k, v in result.items() if k != "continuation_hint"} == golden["result"]
    assert sync_meta.calls and sync_meta.calls[0][0] == "claude_history_import"


def test_import_discovered_sends_what_import_selected_sends(tmp_path: Path) -> None:
    """The batch entry point skips the history walk, never the identity checks or the bytes."""
    config_dir = tmp_path / ".claude"
    write_fixture(config_dir, SESSION)
    first, second = RecordingSink(), RecordingSink()

    def importer(sink: RecordingSink) -> ClaudeHistoryImporter:
        return ClaudeHistoryImporter(
            sink=lambda: sink, user=User(), sync_meta=SyncMeta(), config_dir=config_dir,
            sessions_dir=tmp_path / "no-index", account_reader=account_a,
        )

    async def run() -> None:
        a = importer(first)
        _account, sources = await a.capture_inventory()
        key = (SESSION, sources[0].project_key)
        fresh = await a.capture_revisions({key})
        request = ClaudeHistoryImportRequest(
            provider_account_key="a" * 64,
            sessions=[ClaudeHistorySelection(session_id=SESSION, provider_project_key=key[1], source_revision=fresh[key].source_revision)],
        )
        await a.import_selected(request)
        b = importer(second)
        prepared = await b.capture_for_import({key})
        assert prepared[key].source_revision == fresh[key].source_revision
        # Its summary was read (the placeholder title means it was not).
        assert prepared[key].title != f"Claude session {SESSION[:8]}"
        await b.import_discovered(request, prepared)

    asyncio.run(run())
    dump = lambda reqs: [r.model_dump(mode="json", exclude_none=True) for r in reqs]  # noqa: E731
    assert dump(first.requests) == dump(second.requests)


def test_signed_out_is_refused_before_any_envelope(tmp_path: Path) -> None:
    from matrx_coding_history.importer import ClaudeHistoryConflict

    config_dir = tmp_path / ".claude"
    write_fixture(config_dir, SESSION)
    sink = RecordingSink()
    importer = ClaudeHistoryImporter(
        sink=lambda: sink, user=User(None), sync_meta=SyncMeta(), config_dir=config_dir,
        sessions_dir=tmp_path / "no-index", account_reader=account_a,
    )

    async def run() -> None:
        request = ClaudeHistoryImportRequest(
            provider_account_key="a" * 64,
            sessions=[ClaudeHistorySelection(session_id=SESSION, provider_project_key="p", source_revision="0" * 64)],
        )
        try:
            await importer.import_discovered(request, {})
        except ClaudeHistoryConflict as exc:
            assert "Sign in" in str(exc)
        else:
            raise AssertionError("a signed-out import must be refused")

    asyncio.run(run())
    assert sink.requests == []
