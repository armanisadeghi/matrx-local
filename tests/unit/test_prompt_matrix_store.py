"""Unit tests for on-disk prompt-matrix library/templates store."""

from __future__ import annotations

from pathlib import Path

from app.services.prompt_matrix.store import PromptMatrixStore


def test_library_round_trip(tmp_path: Path) -> None:
    store = PromptMatrixStore(root=tmp_path)
    entries = [
        {
            "id": "p1",
            "name": "Colors",
            "kind": "pool",
            "options": [{"id": "o1", "value": "red", "enabled": True}],
            "assign": "rotate",
            "updatedAt": 1,
        }
    ]
    saved = store.save_library(entries)
    assert saved["v"] == 1
    assert (tmp_path / "library.json").is_file()

    loaded = store.load_library()
    assert loaded["entries"] == entries


def test_templates_round_trip(tmp_path: Path) -> None:
    store = PromptMatrixStore(root=tmp_path)
    templates = [{"id": "t1", "name": "Portrait", "spec": {"fields": []}}]
    store.save_templates(templates)
    assert store.load_templates()["templates"] == templates


def test_corrupt_library_resets(tmp_path: Path) -> None:
    store = PromptMatrixStore(root=tmp_path)
    path = store.library_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    loaded = store.load_library()
    assert loaded == {"v": 1, "entries": []}


def test_atomic_write_replaces(tmp_path: Path) -> None:
    store = PromptMatrixStore(root=tmp_path)
    store.save_library([{"id": "a"}])
    store.save_library([{"id": "b"}])
    assert store.load_library()["entries"] == [{"id": "b"}]


def test_lists_round_trip(tmp_path: Path) -> None:
    store = PromptMatrixStore(root=tmp_path)
    lists = [
        {
            "id": "l1",
            "name": "Colors",
            "description": "Basic palette",
            "options": [{"id": "o1", "value": "red", "enabled": True}],
            "createdAt": 1,
            "updatedAt": 2,
        }
    ]
    saved = store.save_lists(lists)
    assert saved["v"] == 2
    assert (tmp_path / "lists.json").is_file()
    assert store.load_lists()["lists"] == lists


def test_lists_migrate_from_v1_library(tmp_path: Path) -> None:
    store = PromptMatrixStore(root=tmp_path)
    store.save_library(
        [
            {
                "id": "p1",
                "name": "Colors",
                "kind": "pool",
                "options": [{"id": "o1", "value": "blue", "enabled": True}],
                "assign": "rotate",
                "updatedAt": 99,
            }
        ]
    )
    loaded = store.load_lists()
    assert loaded["v"] == 2
    assert len(loaded["lists"]) == 1
    item = loaded["lists"][0]
    assert item["id"] == "p1"
    assert item["name"] == "Colors"
    assert "kind" not in item
    assert "assign" not in item
    assert item["options"][0]["value"] == "blue"
    assert not (tmp_path / "library.json.tmp").exists()
