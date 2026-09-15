"""A cloud record echoed into the mirror keeps its id, and a skip is said once.

WHY (observed live 2026-09-15 on a source engine signed in as admin@admin.com):
the engine log carried

    [file_sync] files feed entry missing id — skipped:
        {'file_id': None, 'file_path': 'education/.../official-mcat-content-outline.pdf',
         ..., 'version': None, 'folder_id': None}

as an ERROR, with the whole row, over and over for the same file.

Two defects, one in the producer and one in the announcement:

1. THE PRODUCER. The change feed (GET /files/sync/changes) names three fields
   `file_id`, `version`, `folder_id`. The record read (GET /files/{id},
   matrx-files `FileRecord`) names those same three `id`, `current_version`,
   `parent_folder_id`. Both post-write echo sites in engine.py hand-built a
   feed entry out of a RECORD payload using the FEED's names, so every echo
   after an upload, rename or move carried file_id=None — refused by the
   mirror, and silently wrong in `version`/`folder_id` even when an id was
   present. One converter now owns that translation.

2. THE ANNOUNCEMENT. The refusal is correct (a row with no id cannot be
   keyed), but it was one ERROR per occurrence with no remedy, so a producer
   bug that repeated per push became a log loop. It is now named once per
   path, with what to do, and the repeats accrue in a state function.

The pre-fix inline mapping is reproduced by `_legacy_echo` below, so this file
fails on the OLD behaviour rather than on a missing import.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.file_sync import index as index_module

# Exactly the fields matrx-files' FileRecord sends for a real file
# (packages/matrx-files/matrx_files/api/records.py) — note `id`,
# `current_version`, `parent_folder_id`.
RECORD: dict[str, Any] = {
    "id": "4b1d7a52-0000-4000-8000-000000000001",
    "owner_id": "87a6e699-3622-4869-8843-d0867456c0dd",
    "organization_id": "11111111-2222-4333-8444-555555555555",
    "file_path": "education/Official Exam Sources/official-mcat-content-outline.pdf",
    "file_name": "official-mcat-content-outline.pdf",
    "mime_type": "application/pdf",
    "size_bytes": 3145728,
    "checksum": "c0ffee" * 10,
    "visibility": "personal",
    "current_version": 7,
    "parent_folder_id": "9f0d1c33-0000-4000-8000-000000000002",
    "created_at": "2026-09-15T03:32:20.709519+00:00",
    "updated_at": "2026-09-15T03:40:00.000000+00:00",
    "deleted_at": None,
    "url": "https://files.example/x",
}


def _legacy_echo(record: dict[str, Any], checksum: str | None = None) -> dict[str, Any]:
    """The pre-fix inline mapping, verbatim: feed names read off a record."""
    return {
        "file_id": record.get("file_id"),
        "file_path": record.get("file_path"),
        "file_name": record.get("file_name"),
        "mime_type": record.get("mime_type"),
        "size_bytes": record.get("size_bytes"),
        "checksum": checksum,
        "visibility": record.get("visibility"),
        "version": record.get("version"),
        "folder_id": record.get("folder_id"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "deleted_at": record.get("deleted_at"),
    }


def _convert(record: dict[str, Any], **kw: Any) -> dict[str, Any]:
    converter = getattr(index_module, "record_to_feed_entry", None)
    if converter is None:
        return _legacy_echo(record, kw.get("checksum"))
    return converter(record, **kw)


def test_the_echo_carries_the_record_id_not_none() -> None:
    """THE bug: the mirror row for an uploaded file had no id to key on."""
    entry = _convert(RECORD, checksum="abc123")

    assert entry["file_id"] == RECORD["id"], (
        "the echo must carry the record's id — with file_id=None the mirror "
        "upsert refuses the row and the local replica never learns about the "
        "file this engine just uploaded"
    )


def test_the_echo_translates_version_and_folder_too() -> None:
    """The same rename, twice more: current_version and parent_folder_id were
    read under their feed names and silently became None."""
    entry = _convert(RECORD)

    assert entry["version"] == 7
    assert entry["folder_id"] == RECORD["parent_folder_id"]


def test_a_caller_supplied_checksum_wins_over_the_records() -> None:
    """The upload echo hashes the bytes it just wrote; that is the truth for
    the row it is echoing."""
    assert _convert(RECORD, checksum="locallyhashed")["checksum"] == "locallyhashed"
    assert _convert(RECORD)["checksum"] == RECORD["checksum"]


def test_a_feed_shaped_entry_still_converts() -> None:
    """Feed entries already use file_id/version/folder_id — passing one
    through the converter must not lose them."""
    feed_entry = {"file_id": "abc", "version": 3, "folder_id": "def"}
    entry = _convert(feed_entry)
    assert (entry["file_id"], entry["version"], entry["folder_id"]) == (
        "abc", 3, "def",
    )


class _Recorder:
    """The repo's logger is not a stdlib logger that caplog can see (it writes
    its own stream), so the module's logger is swapped, exactly as
    test_auth_rejection_log_throttle.py does."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, template: str, *args: object) -> None:
        self.errors.append(template % args if args else template)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(index_module, "logger", rec)
    if hasattr(index_module, "_reported_unmirrorable"):
        index_module._reported_unmirrorable.clear()
        index_module._unmirrorable_counts.clear()
    return rec


def test_an_unkeyable_entry_is_named_once_and_counted(recorder: _Recorder) -> None:
    """The log loop: the same refusal said the same thing every cycle."""
    report = getattr(index_module, "report_unmirrorable_entry", None)
    state = getattr(index_module, "unmirrorable_entry_state", None)
    if report is None or state is None:
        pytest.fail(
            "file_sync has no once-only skip primitive — the pre-fix code "
            "logged one ERROR per occurrence, with the whole row and no remedy"
        )
    entry = {"file_id": None, "file_path": "education/x.pdf"}
    for _ in range(5):
        report("files", entry, reason="carries no id")

    said = [m for m in recorder.errors if "cannot be stored" in m]
    assert len(said) == 1, f"expected exactly one announcement, got {len(said)}"
    assert state()["files"] == 5, "every repeat must still be counted"


def test_the_one_announcement_names_the_remedy(recorder: _Recorder) -> None:
    """Nothing fails silently — and nothing screams without saying what to do."""
    report = getattr(index_module, "report_unmirrorable_entry", None)
    if report is None:
        pytest.fail("no skip primitive to carry a remedy")
    report("files", {"file_id": None, "file_path": "a/b.pdf"}, reason="carries no id")

    message = "\n".join(recorder.errors)
    assert "record_to_feed_entry" in message, "the remedy must name the converter"
    assert "WHAT TO DO" in message


def test_no_echo_site_reads_feed_names_off_a_record() -> None:
    """THE CENSUS — the class, not the instance. Two sites had this bug; the
    next one anybody writes must go through the converter too."""
    from pathlib import Path

    engine_src = (
        Path(__file__).resolve().parents[2]
        / "app" / "services" / "file_sync" / "engine.py"
    ).read_text()

    for line_no, line in enumerate(engine_src.splitlines(), 1):
        if 'record.get("file_id")' in line or "record.get('file_id')" in line:
            pytest.fail(
                f"engine.py:{line_no} reads the FEED's `file_id` off a record "
                f"payload, which is always None: {line.strip()!r} — build the "
                "entry with index.record_to_feed_entry instead"
            )
    assert "record_to_feed_entry(" in engine_src, (
        "the post-write echoes must convert the record through the one "
        "converter"
    )
