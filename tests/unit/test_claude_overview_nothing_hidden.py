"""Every transcript on disk is on the screen — including the ones Claude never indexed.

The 2026-09-11 defect: the overview iterated Claude's sidebar index, so a
session started from the plain `claude` CLI (which never gets an index record)
was invisible — 80 of them, 113 MB, growing. The importer already reached them;
only the screen hid them. This test builds a tiny real tree — one indexed
session, one CLI-only transcript — and fails if either is missing, if the
CLI-only row is not marked, or if its title is a placeholder when the
transcript plainly opens with a human message.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def _write_transcript(projects: Path, project_slug: str, session_id: str, first_prompt: str) -> None:
    folder = projects / project_slug
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "isMeta": False, "cwd": "/Users/x/code/demo",
         "message": {"role": "user", "content": first_prompt}},
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "Sure."}]}},
    ]
    (folder / f"{session_id}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines)
    )


def _write_index_record(sessions_root: Path, session_id: str, title: str) -> None:
    scope = sessions_root / "acct-1" / "org-1"
    scope.mkdir(parents=True, exist_ok=True)
    (scope / f"local_{session_id}.json").write_text(json.dumps({
        "sessionId": session_id,
        "cliSessionId": session_id,
        "title": title,
        "titleSource": "user",
        "cwd": "/Users/x/code/demo",
        "isArchived": False,
        "lastActivityAt": 1_788_868_800_000,
    }))


@pytest.fixture()
def claude_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    config_dir = tmp_path / "claude"
    projects = config_dir / "projects"
    sessions_root = tmp_path / "desktop-sessions"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("CLAUDE_DESKTOP_SESSIONS_DIR", str(sessions_root))
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(tmp_path / "absent-ledger.json"))

    indexed = "11111111-1111-4111-8111-111111111111"
    cli_only = "22222222-2222-4222-8222-222222222222"
    _write_transcript(projects, "-Users-x-code-demo", indexed, "Build the thing")
    _write_index_record(sessions_root, indexed, "Build the thing (renamed)")
    _write_transcript(projects, "-Users-x-code-demo", cli_only,
                      "Please audit the billing module for double charges")

    # A throwaway persisted index, refreshed exactly the way the engine's
    # startup does it — the screen reads the index, never the tree.
    import app.services.coding_sessions.claude_overview as overview_module
    from app.services.coding_sessions.claude_index_store import ClaudeIndexStore

    store = ClaudeIndexStore(tmp_path / "store" / "index.sqlite3")
    overview_module._reset_index_state_for_tests(store)
    asyncio.run(overview_module.warm_index_cache(sessions_root))
    yield {"indexed": indexed, "cli_only": cli_only}
    overview_module._reset_index_state_for_tests(None)


def _run_overview(monkeypatch: pytest.MonkeyPatch) -> dict:
    import app.services.coding_sessions.claude_overview as overview_module

    # No cloud, no queue: this test is about what is LISTED, not its state.
    async def _no_cloud():
        return {}, {"checked": False, "reason": "test"}

    async def _no_queue():
        return {}, {"checked": True, "reason": None, "detail": None}

    async def _no_totals():
        return (0, 0), {"checked": True, "reason": None, "detail": None}

    monkeypatch.setattr(overview_module, "cloud_inventory", _no_cloud)
    monkeypatch.setattr(overview_module, "_queue_by_session", _no_queue)
    monkeypatch.setattr(overview_module, "_queue_totals", _no_totals)
    return asyncio.run(overview_module.overview())


def test_every_transcript_on_disk_is_listed(claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run_overview(monkeypatch)
    listed = {row["session_id"]: row for row in out["conversations"]}

    assert claude_tree["indexed"] in listed
    assert claude_tree["cli_only"] in listed, "a CLI-only transcript must never be hidden"

    assert out["totals"]["transcripts_on_disk"] == 2
    assert out["totals"]["transcript_only"] == 1
    assert out["totals"]["conversations"] == 2


def test_cli_only_rows_are_marked_and_titled_by_their_opening_message(
    claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run_overview(monkeypatch)
    listed = {row["session_id"]: row for row in out["conversations"]}

    indexed = listed[claude_tree["indexed"]]
    assert indexed["in_claude_sidebar"] is True
    assert indexed["title"] == "Build the thing (renamed)"  # the sidebar label wins

    cli_only = listed[claude_tree["cli_only"]]
    assert cli_only["in_claude_sidebar"] is False
    assert cli_only["title"] == "Please audit the billing module for double charges"
    assert cli_only["project"] == "demo"
    assert cli_only["on_disk"] is True
    assert cli_only["pinned"] is False


def test_delivery_ledger_failure_is_unknown_not_zero(
    claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A list remains useful when its local delivery evidence cannot be read."""
    import app.services.coding_sessions.claude_overview as overview_module

    async def _cloud():
        return {
            claude_tree["indexed"]: {
                "last_seen_at": "2026-09-16T00:00:00+00:00",
                "fidelity": "event_mirror",
            }
        }, {"checked": True, "reason": None, "detail": None, "sessions": 1}

    async def _unavailable_queue():
        return None, overview_module._delivery_ledger_meta(checked=False)

    async def _known_totals():
        return (9, 4), overview_module._delivery_ledger_meta(checked=True)

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)
    monkeypatch.setattr(overview_module, "_queue_by_session", _unavailable_queue)
    monkeypatch.setattr(overview_module, "_queue_totals", _known_totals)

    out = asyncio.run(overview_module.overview())
    rows = {row["session_id"]: row for row in out["conversations"]}

    assert set(rows) == {claude_tree["indexed"], claude_tree["cli_only"]}
    assert out["delivery_ledger"]["checked"] is False
    assert out["delivery_ledger"]["reason"] == "local_delivery_ledger_unavailable"
    assert out["totals"]["waiting"] is None
    assert out["totals"]["quarantined"] is None
    assert out["totals"]["queued"] is None
    assert out["totals"]["failed"] is None
    assert rows[claude_tree["indexed"]]["state"] == "in_cloud"
    assert rows[claude_tree["cli_only"]]["state"] == "unknown"
    assert all(row["delivery"] == {"pending": None, "quarantined": None} for row in rows.values())


def test_delivery_total_failure_invalidates_row_delivery_counts(
    claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial ledger read cannot combine real rows with invented totals."""
    import app.services.coding_sessions.claude_overview as overview_module

    async def _cloud():
        return {}, {"checked": True, "reason": None, "detail": None, "sessions": 0}

    async def _queue():
        return {
            claude_tree["indexed"]: {"pending": 2, "quarantined": 0}
        }, overview_module._delivery_ledger_meta(checked=True)

    async def _unavailable_totals():
        return None, overview_module._delivery_ledger_meta(checked=False)

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)
    monkeypatch.setattr(overview_module, "_queue_by_session", _queue)
    monkeypatch.setattr(overview_module, "_queue_totals", _unavailable_totals)

    out = asyncio.run(overview_module.overview())
    indexed = next(row for row in out["conversations"] if row["session_id"] == claude_tree["indexed"])
    assert out["delivery_ledger"]["checked"] is False
    assert out["totals"]["waiting"] is None
    assert out["totals"]["queued"] is None
    assert indexed["state"] == "unknown"
    assert indexed["delivery"] == {"pending": None, "quarantined": None}


def test_delivery_ledger_error_log_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local database error reports its class and operation, never its text."""
    import app.services.coding_sessions.claude_overview as overview_module

    secret = "query-and-user-data-must-not-escape"

    class _FailingDb:
        async def fetchall(self, *_args, **_kwargs):
            raise RuntimeError(secret)

    logged: list[tuple[object, ...]] = []

    def _record_error(message: object, *args: object) -> None:
        logged.append((message, *args))

    monkeypatch.setattr(overview_module, "get_db", lambda: _FailingDb())
    monkeypatch.setattr(overview_module.logger, "error", _record_error)
    rows, meta = asyncio.run(overview_module._queue_by_session())

    assert rows is None
    assert meta["checked"] is False
    assert len(logged) == 1
    rendered = " ".join(str(part) for part in logged[0])
    assert "queue-by-session" in rendered
    assert "RuntimeError" in rendered
    assert "fetchall@" in rendered
    assert secret not in rendered


def test_delivery_ledger_reports_database_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening the local database is itself a ledger read that can fail."""
    import app.services.coding_sessions.claude_overview as overview_module

    secret = "database-path-must-not-escape"
    logged: list[tuple[object, ...]] = []

    def _unavailable_db():
        raise OSError(secret)

    def _record_error(message: object, *args: object) -> None:
        logged.append((message, *args))

    monkeypatch.setattr(overview_module, "get_db", _unavailable_db)
    monkeypatch.setattr(overview_module.logger, "error", _record_error)
    totals, meta = asyncio.run(overview_module._queue_totals())

    assert totals is None
    assert meta["checked"] is False
    rendered = " ".join(str(part) for part in logged[0])
    assert "queue-totals" in rendered
    assert "OSError" in rendered
    assert secret not in rendered


def test_the_screen_never_walks_the_index_tree(
    claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The response may not stat or parse the session-index tree at all.

    This is the 31.76 s / 58.96 s / 1,209 s defect in one assertion: the walk
    and the record reads happen in the background refresh, never inside the
    request. Both are booby-trapped here, so a future edit that puts either
    back on the request path fails immediately.
    """
    import app.services.coding_sessions.claude_index_store as store_module
    import app.services.coding_sessions.claude_overview as overview_module

    def _must_not_walk(*_a, **_k):
        raise AssertionError("the overview walked the session-index tree")

    def _must_not_read(*_a, **_k):
        raise AssertionError("the overview parsed a session-index record")

    monkeypatch.setattr(overview_module, "plan_refresh", _must_not_walk)
    monkeypatch.setattr(overview_module, "read_record_rows", _must_not_read)
    monkeypatch.setattr(store_module, "walk_records", _must_not_walk)
    monkeypatch.setattr(store_module, "walk_transcripts", _must_not_walk)
    # Any tree walk at all, by any route: counting the accounts with rglob is
    # exactly how 67,224 files were stat-ed on the event loop per response.
    monkeypatch.setattr(Path, "rglob", _must_not_walk)
    monkeypatch.setattr(Path, "glob", _must_not_walk)
    # ...and no helper process either: a refresh kicked behind the response is
    # allowed, but it must not be what answers it.
    monkeypatch.setattr(
        overview_module.claude_index_helper,
        "helper_command",
        lambda *_a, **_k: ["/nonexistent/matrx-helper"],
    )

    out = _run_overview(monkeypatch)
    assert out["totals"]["conversations"] == 2
    assert out["index"]["state"] in {"fresh", "refreshing"}
    assert out["index"]["files_read"] == 1
    assert out["index"]["updated_at"]


def test_a_cold_index_says_so_instead_of_pretending_to_be_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the first refresh there are no rows — and the screen is told."""
    import app.services.coding_sessions.claude_overview as overview_module
    from app.services.coding_sessions.claude_index_store import ClaudeIndexStore

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_DESKTOP_SESSIONS_DIR", str(tmp_path / "sessions"))
    overview_module._reset_index_state_for_tests(
        ClaudeIndexStore(tmp_path / "store" / "index.sqlite3")
    )
    monkeypatch.setattr(
        overview_module.claude_index_helper,
        "helper_command",
        lambda *_a, **_k: ["/nonexistent/matrx-helper"],
    )
    try:
        out = _run_overview(monkeypatch)
    finally:
        overview_module._reset_index_state_for_tests(None)

    assert out["index"]["state"] == "cold"
    assert out["index"]["files_read"] == 0
    assert out["index"]["updated_at"] is None
    assert out["conversations"] == []


def test_a_cli_only_session_opens_a_diagnosis_instead_of_a_404(
    claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every listed row must open. A CLI-only row used to 404 because the
    diagnosis looked the session up in the sidebar index alone."""
    import app.services.coding_sessions.claude_overview as overview_module

    async def _no_cloud():
        return {}, {"checked": False, "reason": "test"}

    async def _none(*_a, **_k):
        return [], {"checked": True, "reason": None, "detail": None}

    async def _empty_capture(*_a, **_k):
        return [], {"checked": True, "reason": None, "detail": None}

    async def _empty_labels(*_a, **_k):
        return (
            {"metadata_sent": None, "title_pushed": None},
            {"checked": True, "reason": None, "detail": None},
        )

    monkeypatch.setattr(overview_module, "cloud_inventory", _no_cloud)
    monkeypatch.setattr(overview_module, "_session_envelopes", _none)
    monkeypatch.setattr(overview_module, "_capture_facts", _empty_capture)
    monkeypatch.setattr(overview_module, "_label_facts", _empty_labels)

    async def _no_acknowledgements(*_a, **_k):
        return {}, {"checked": True, "reason": None, "detail": None}

    monkeypatch.setattr(overview_module, "_delivered_by_this_mac", _no_acknowledgements)

    class _Outbox:
        publisher_blocker = None

    import app.services.coding_sessions.service as service_module
    monkeypatch.setattr(service_module, "get_coding_session_bridge_outbox", lambda: _Outbox())

    out = asyncio.run(overview_module.session_diagnosis(claude_tree["cli_only"]))
    assert out is not None, "a listed CLI-only session must open a diagnosis"
    assert out["index"]["in_claude_sidebar"] is False
    assert out["index"]["title"] == "Please audit the billing module for double charges"
    assert out["index"]["accounts"] == []  # the dialog's row must not crash
    assert out["transcript"]["on_disk"] is True

    # And a session that exists nowhere is still unknown.
    missing = asyncio.run(overview_module.session_diagnosis("99999999-9999-4999-8999-999999999999"))
    assert missing is None


def test_diagnosis_marks_delivery_evidence_unavailable_instead_of_empty(
    claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.coding_sessions.claude_overview as overview_module

    async def _cloud():
        return {}, {"checked": True, "reason": None, "detail": None, "sessions": 0}

    async def _unavailable_envelopes(*_args, **_kwargs):
        return None, overview_module._delivery_ledger_meta(checked=False)

    async def _unavailable_acknowledgements(*_args, **_kwargs):
        return None, overview_module._delivery_ledger_meta(checked=False)

    async def _empty(*_args, **_kwargs):
        return [], {"checked": True, "reason": None, "detail": None}

    async def _empty_labels(*_args, **_kwargs):
        return (
            {"metadata_sent": None, "title_pushed": None},
            {"checked": True, "reason": None, "detail": None},
        )

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)
    monkeypatch.setattr(overview_module, "_session_envelopes", _unavailable_envelopes)
    monkeypatch.setattr(overview_module, "_delivered_by_this_mac", _unavailable_acknowledgements)
    monkeypatch.setattr(overview_module, "_capture_facts", _empty)
    monkeypatch.setattr(overview_module, "_label_facts", _empty_labels)

    class _Outbox:
        publisher_blocker = None

    import app.services.coding_sessions.service as service_module
    monkeypatch.setattr(service_module, "get_coding_session_bridge_outbox", lambda: _Outbox())

    out = asyncio.run(overview_module.session_diagnosis(claude_tree["cli_only"]))
    assert out is not None
    assert out["delivery"]["ledger"]["checked"] is False
    assert out["delivery"]["envelopes"] is None
    assert out["delivery"]["delivered_by_this_mac_at"] is None
    assert "delivery ledger" in out["verdict"]["summary"]


def test_full_diagnosis_marks_every_local_database_section_unavailable(
    claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No sibling diagnosis read can convert a failed DB open into an empty fact."""
    import app.services.coding_sessions.claude_overview as overview_module

    async def _cloud():
        return {}, {"checked": True, "reason": None, "detail": None, "sessions": 0}

    def _unavailable_db():
        raise OSError("private database path")

    class _Outbox:
        publisher_blocker = None

    import app.services.coding_sessions.service as service_module
    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)
    monkeypatch.setattr(overview_module, "get_db", _unavailable_db)
    monkeypatch.setattr(service_module, "get_coding_session_bridge_outbox", lambda: _Outbox())

    out = asyncio.run(overview_module.session_diagnosis(claude_tree["cli_only"]))
    assert out is not None
    assert out["delivery"]["ledger"]["checked"] is False
    assert out["delivery"]["envelopes"] is None
    assert out["capture"] is None
    assert out["capture_ledger"]["checked"] is False
    assert out["labels"] is None
    assert out["labels_ledger"]["checked"] is False


def test_malformed_ledger_rows_become_unavailable_not_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.coding_sessions.claude_overview as overview_module

    class _MalformedDb:
        async def fetchall(self, *_args, **_kwargs):
            return [{"session_key": "session", "queue_state": "pending", "n": "not-a-number"}]

    monkeypatch.setattr(overview_module, "get_db", lambda: _MalformedDb())
    rows, meta = asyncio.run(overview_module._queue_by_session())
    assert rows is None
    assert meta["checked"] is False


def test_malformed_envelope_rows_become_unavailable_not_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.coding_sessions.claude_overview as overview_module

    class _MalformedEnvelopeDb:
        calls = 0

        async def fetchall(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return [{"session_key": "session"}]
            if self.calls == 2:
                return [{"id": "not-an-id"}]
            return []

    monkeypatch.setattr(overview_module, "get_db", _MalformedEnvelopeDb)
    envelopes, meta = asyncio.run(overview_module._session_envelopes("session"))
    assert envelopes is None
    assert meta["checked"] is False
