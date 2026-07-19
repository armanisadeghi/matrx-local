from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.filesystem import index as index_module
from app.services.filesystem.index import FilesystemIndex


def test_repeated_initialize_does_not_rewrite_existing_path_keys(
    monkeypatch, tmp_path: Path
) -> None:
    """Established indexes must not perform a full path migration every boot."""
    database = tmp_path / "filesystem.sqlite3"
    index = FilesystemIndex(database)
    index.initialize()
    path = str(tmp_path / "root" / "file.txt")
    parent = str(tmp_path / "root")
    with sqlite3.connect(database) as db:
        db.execute(
            """INSERT INTO filesystem_entries(
                   path,path_key,parent_path,parent_key,root_id,name,kind,
                   size,modified_at,hidden,extension,indexed_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                path,
                index_module._path_key(path),
                parent,
                index_module._path_key(parent),
                "root",
                "file.txt",
                "file",
                1,
                1.0,
                0,
                ".txt",
                1.0,
            ),
        )

    def unexpected_backfill(_path: str) -> str:
        raise AssertionError("existing path key was re-normalized during startup")

    monkeypatch.setattr(index_module, "_path_key", unexpected_backfill)
    FilesystemIndex(database).initialize()
