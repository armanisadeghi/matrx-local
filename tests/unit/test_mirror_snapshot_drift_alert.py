"""Cloud columns this build cannot store are an ALERT, said once — never a
per-row warning, and never silence.

WHY (SR-10, 2026-09-11 → 2026-09-14): `chat.request_snapshot` and
`chat.tool_trace` rows arrived carrying five columns the checked-in schema
snapshot (generated 2026-08-13) had never heard of. chat_sync logged
"values not stored" **8,566 times** — once per row, identical text — and
dropped every one of those values. The volume was the problem: it buried the
log and still told nobody. These guards hold the two halves of the fix.
"""

from __future__ import annotations

import pytest

from app.services.chat_sync import engine as chat_sync_engine


class _Recorder:
    """The engine uses the app's own logger (vcprint-backed), which does not
    propagate to caplog — record the real call instead of the handler."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, template: str, *args: object) -> None:
        self.errors.append(template % args if args else template)

    def __getattr__(self, _name: str):  # warning/info/debug are irrelevant here
        return lambda *a, **k: None


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(chat_sync_engine, "logger", rec)
    return rec


@pytest.fixture(autouse=True)
def _clean_drift_state():
    chat_sync_engine._reported_snapshot_drift.clear()
    chat_sync_engine._snapshot_drift_rows.clear()
    yield
    chat_sync_engine._reported_snapshot_drift.clear()
    chat_sync_engine._snapshot_drift_rows.clear()


def test_a_new_cloud_column_is_reported_once_with_a_remedy(recorder) -> None:
    for _ in range(500):
        chat_sync_engine._report_snapshot_drift(
            "request_snapshot", ["pinned_at", "pin_reason"]
        )

    assert len(recorder.errors) == 1, "8,566 identical lines is what this replaces"
    message = recorder.errors[0]
    assert "pinned_at" in message and "pin_reason" in message
    assert "NOT being stored" in message
    assert "WHAT TO DO" in message
    assert "generate_mirror_schema.py" in message


def test_a_further_new_column_is_still_reported(recorder) -> None:
    """Said once per COLUMN, not once per process — a second schema move must
    not be swallowed by the first one's report."""
    chat_sync_engine._report_snapshot_drift("request_snapshot", ["pinned_at"])
    chat_sync_engine._report_snapshot_drift(
        "request_snapshot", ["pinned_at", "deleted_at"]
    )

    messages = recorder.errors
    assert len(messages) == 2
    assert "deleted_at" in messages[1] and "pinned_at" not in messages[1]


def test_the_dropped_row_count_keeps_accruing_after_the_one_report() -> None:
    """Nothing fails silently: the state a status surface reads must say how
    much was lost, not just that something was."""
    for _ in range(37):
        chat_sync_engine._report_snapshot_drift("tool_trace", ["deleted_at"])

    state = chat_sync_engine.snapshot_drift_state()
    assert state == {"tool_trace": {"columns": ["deleted_at"], "rows": 37}}


# ── The snapshot itself ─────────────────────────────────────────────────────


def test_the_shipped_snapshot_stores_the_columns_sr10_was_dropping() -> None:
    """The five columns named in the audit, plus tool_trace's tombstone. A
    refreshed snapshot that still cannot store them is not a fix."""
    from app.services.local_db.mirror_schema import MIRROR_TABLES

    request_snapshot = MIRROR_TABLES["chat"]["request_snapshot"]
    for column in (
        "deleted_at",
        "pinned_at",
        "pin_reason",
        "agent_definition_version",
        "workflow_definition_version",
    ):
        assert column in request_snapshot["columns"], column
        assert column in request_snapshot["pg_types"], column

    assert "deleted_at" in MIRROR_TABLES["chat"]["tool_trace"]["columns"]


def test_the_live_drift_detector_can_still_fail() -> None:
    """The release gate is only worth having if a green run means something."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_mirror_snapshot_drift.py"
    spec = importlib.util.spec_from_file_location("check_mirror_snapshot_drift", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.self_test() == 0
    known = module.known_columns("chat", "request_snapshot")
    assert module.unknown_columns("chat", "request_snapshot", known) == []
    assert module.unknown_columns(
        "chat", "request_snapshot", known | {"a_column_from_tomorrow"}
    ) == ["a_column_from_tomorrow"]


def test_unreadable_relations_are_incomplete_not_a_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A sampled empty/RLS-hidden table cannot certify the whole snapshot."""
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_mirror_snapshot_drift.py"
    spec = importlib.util.spec_from_file_location("check_mirror_snapshot_drift_incomplete", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(module.httpx, "Client", lambda **_kwargs: Client())
    monkeypatch.setattr(module, "_admin_jwt", lambda _client: None)
    monkeypatch.setattr(module, "live_columns", lambda *_args: None)
    monkeypatch.setattr(sys, "argv", ["check_mirror_snapshot_drift.py"])

    assert module.main() == 2
    output = capsys.readouterr().out
    assert "INCOMPLETE CHECK" in output
    assert "snapshot knows every column" not in output
