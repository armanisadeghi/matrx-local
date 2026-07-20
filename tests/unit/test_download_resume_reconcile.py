"""The startup-resume RECONCILE contract (MXL "the system isn't checking if I
have the models").

`_resume_incomplete` must never blind-re-download a model during app startup.
Its single completion oracle is `artifact_present`, shared with `enqueue`'s
idempotency check so the two can never disagree. Completed artifacts settle as
done; incomplete model installs wait for explicit Retry; non-model transfers
may resume. These tests also pin the catalog self-validation that keeps one bad
LoRA entry from blanking the panel.

NETWORK-FREE, no engine, no DB — pure functions against a tmp dir.

    uv run --no-sync pytest tests/unit/test_download_resume_reconcile.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.downloads import manager as downloads_manager
from app.services.downloads.manager import DownloadManager, artifact_present
from app.services.media_gen.paths import DOWNLOAD_COMPLETE_MARKER


def _marker(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")


def test_artifact_present_requires_dest_dir() -> None:
    assert artifact_present(None, "x") is False
    assert artifact_present({}, "x") is False


def test_hf_snapshot_present_only_with_marker(tmp_path: Path) -> None:
    dest = tmp_path / "model"
    md = {"dest_dir": str(dest), "hf_repo_id": "org/name"}
    # weights on disk but no marker → NOT complete → would re-download
    dest.mkdir()
    (dest / "model.safetensors").write_bytes(b"x")
    assert artifact_present(md, "org--name") is False
    # marker present → complete → never re-download
    _marker(dest)
    assert artifact_present(md, "org--name") is True


def test_civitai_marker_gated_needs_marker_and_file(tmp_path: Path) -> None:
    dest = tmp_path / "lora"
    md = {
        "dest_dir": str(dest),
        "civitai_download": True,
        "write_complete_marker": True,
        "dest_filename": "style.safetensors",
    }
    dest.mkdir()
    # marker but no file (or vice versa) → incomplete
    (dest / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")
    assert artifact_present(md, "civitai--1-2") is False
    (dest / "style.safetensors").write_bytes(b"weights")
    assert artifact_present(md, "civitai--1-2") is True


def test_plain_single_file_present_when_file_exists(tmp_path: Path) -> None:
    dest = tmp_path / "gguf"
    dest.mkdir()
    md = {"dest_dir": str(dest), "dest_filename": "model.gguf"}
    assert artifact_present(md, "model") is False
    (dest / "model.gguf").write_bytes(b"x")
    assert artifact_present(md, "model") is True


def test_plain_file_falls_back_to_filename(tmp_path: Path) -> None:
    dest = tmp_path / "d"
    dest.mkdir()
    md = {"dest_dir": str(dest)}  # no dest_filename → use the download filename
    (dest / "cloudflared").write_bytes(b"bin")
    assert artifact_present(md, "cloudflared") is True


@pytest.mark.anyio
async def test_startup_defers_models_but_resumes_non_model_transfers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def row(download_id: str, category: str) -> dict[str, object]:
        now = "2026-07-18T23:20:00Z"
        return {
            "id": download_id,
            "category": category,
            "filename": f"{download_id}.bin",
            "display_name": download_id,
            "urls": json.dumps([f"https://example.test/{download_id}.bin"]),
            "total_bytes": 100,
            "bytes_done": 25,
            "status": "active",
            "error_msg": None,
            "priority": 0,
            "part_current": 1,
            "part_total": 1,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "metadata": json.dumps(
                {"dest_dir": str(tmp_path / download_id)}
            ),
        }

    class FakeDb:
        def __init__(self) -> None:
            self.rows = [row("model", "image_gen"), row("cloud-file", "file_sync")]
            self.updates: list[tuple[str, tuple[object, ...]]] = []
            self.committed = False

        async def fetchall(self, _query: str):
            return self.rows

        async def execute(self, query: str, params: tuple[object, ...]) -> None:
            self.updates.append((query, params))

        async def commit(self) -> None:
            self.committed = True

    db = FakeDb()
    monkeypatch.setattr(downloads_manager, "get_db", lambda: db)
    manager = DownloadManager()

    await manager._resume_incomplete()

    assert manager._entries["model"].status == "failed"
    assert "do not start automatically" in (
        manager._entries["model"].error_msg or ""
    )
    assert "model" not in manager._pending_ids
    assert manager._entries["cloud-file"].status == "queued"
    assert "cloud-file" in manager._pending_ids
    assert db.committed is True


def test_curated_catalog_is_valid() -> None:
    """The shipped catalog must have zero problems — a bad entry would be
    skipped in GET /image-gen/loras, so keep it clean at the source."""
    from app.services.image_gen.loras import validate_catalog

    assert validate_catalog() == []


def test_validate_catalog_flags_missing_key_and_dupes(monkeypatch) -> None:
    from app.services.image_gen import loras

    bad = [
        {"repo_id": "a/b", "name": "n", "description": "d", "weight_name": "w",
         "base_family": "sdxl"},  # missing license
        {"repo_id": "a/b", "name": "n2", "description": "d", "weight_name": "w",
         "base_family": "sdxl", "license": "x"},  # duplicate repo_id
    ]
    monkeypatch.setattr(loras, "CURATED_LORA_CATALOG", bad)
    problems = loras.validate_catalog()
    assert any("license" in p for p in problems)
    assert any("duplicate" in p for p in problems)


def test_validate_catalog_never_raises_on_non_dict_entry(monkeypatch) -> None:
    """A non-dict entry (stray string / None from an editing slip) is the exact
    mistake this guard exists to catch — it must be REPORTED, never crash import
    (calling .get() on it would AttributeError out of module load)."""
    from app.services.image_gen import loras

    monkeypatch.setattr(
        loras, "CURATED_LORA_CATALOG", ["civitai:123@456", None, ("t",)]
    )
    problems = loras.validate_catalog()  # must not raise
    assert len(problems) == 3
    assert all("not a dict" in p for p in problems)


# ── One artifact = one row; dismissible records ─────────────────────────────
#
# A failed/cancelled row must be re-queued IN PLACE by a retry (never left as
# an immortal action-needed blocker beside a second row), superseded rows are
# removed when a newer attempt exists, and dismiss deletes a terminal record
# so it cannot be "restored" on every later boot.


class _DismissFakeDb:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.committed = 0

    async def fetchall(self, _query: str, _params: tuple[object, ...] = ()):
        return []

    async def fetchone(self, _query: str, _params: tuple[object, ...] = ()):
        return None

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.executed.append((query, params))

    async def commit(self) -> None:
        self.committed += 1


def _terminal_entry(
    manager: DownloadManager,
    *,
    entry_id: str,
    filename: str,
    category: str,
    status: str,
    dest_dir: str,
) -> None:
    entry = downloads_manager.DownloadEntry(
        id=entry_id,
        category=category,
        filename=filename,
        display_name=filename,
        urls=[f"https://example.test/{filename}"],
        status=status,
        created_at="2026-03-29T18:00:00Z",
        updated_at="2026-03-29T18:00:00Z",
        metadata={"dest_dir": dest_dir},
    )
    if status == "failed":
        entry.error_msg = "401 gated repo"
        entry.set_resolution(
            {"code": "hf_gate_not_accepted", "title": "Accept the license"}
        )
    manager._entries[entry_id] = entry


@pytest.mark.anyio
async def test_enqueue_requeues_failed_row_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _DismissFakeDb()
    monkeypatch.setattr(downloads_manager, "get_db", lambda: db)
    manager = DownloadManager()
    _terminal_entry(
        manager,
        entry_id="old-fail",
        filename="org--model",
        category="image_gen_model",
        status="failed",
        dest_dir=str(tmp_path / "m"),
    )

    entry = await manager.enqueue(
        category="image_gen_model",
        filename="org--model",
        display_name="org/model",
        urls=["https://example.test/org--model"],
        metadata={"dest_dir": str(tmp_path / "m")},
    )

    # Same row, back in the queue, old ask cleared — no duplicate row.
    assert entry.id == "old-fail"
    assert entry.status == "queued"
    assert entry.resolution is None
    assert entry.error_msg is None
    assert "old-fail" in manager._pending_ids
    assert len(manager._entries) == 1


@pytest.mark.anyio
async def test_enqueue_purges_superseded_failure_next_to_live_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _DismissFakeDb()
    monkeypatch.setattr(downloads_manager, "get_db", lambda: db)
    manager = DownloadManager()
    _terminal_entry(
        manager,
        entry_id="stale-fail",
        filename="org--model",
        category="image_gen_model",
        status="failed",
        dest_dir=str(tmp_path / "m"),
    )
    _terminal_entry(
        manager,
        entry_id="live-dl",
        filename="org--model",
        category="image_gen_model",
        status="active",
        dest_dir=str(tmp_path / "m"),
    )

    entry = await manager.enqueue(
        category="image_gen_model",
        filename="org--model",
        display_name="org/model",
        urls=["https://example.test/org--model"],
        metadata={"dest_dir": str(tmp_path / "m")},
    )

    # The live transfer wins and the stale gate prompt is gone for good.
    assert entry.id == "live-dl"
    assert "stale-fail" not in manager._entries
    assert any("DELETE FROM downloads" in q for q, _ in db.executed)


@pytest.mark.anyio
async def test_dismiss_removes_terminal_row_and_refuses_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _DismissFakeDb()
    monkeypatch.setattr(downloads_manager, "get_db", lambda: db)
    manager = DownloadManager()
    _terminal_entry(
        manager,
        entry_id="march-fail",
        filename="old.gguf",
        category="llm",
        status="failed",
        dest_dir=str(tmp_path / "g"),
    )
    _terminal_entry(
        manager,
        entry_id="running",
        filename="new.gguf",
        category="llm",
        status="active",
        dest_dir=str(tmp_path / "g"),
    )

    assert await manager.dismiss("march-fail") is True
    assert "march-fail" not in manager._entries
    assert any(
        "DELETE FROM downloads" in q and params == ("march-fail",)
        for q, params in db.executed
    )

    with pytest.raises(ValueError):
        await manager.dismiss("running")
    assert "running" in manager._entries

    # Unknown everywhere → False (route turns this into a 404).
    assert await manager.dismiss("nope") is False
