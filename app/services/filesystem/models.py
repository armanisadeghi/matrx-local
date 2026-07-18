"""Canonical wire models for local filesystem discovery."""

from __future__ import annotations

import os
import stat as stat_module
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

EntryKind = Literal["file", "dir", "symlink", "other"]


def entry_kind(mode: int) -> EntryKind:
    if stat_module.S_ISLNK(mode):
        return "symlink"
    if stat_module.S_ISDIR(mode):
        return "dir"
    if stat_module.S_ISREG(mode):
        return "file"
    return "other"


@dataclass(frozen=True)
class Place:
    id: str
    label: str
    path: str
    category: Literal["home", "standard", "configured", "volume"]
    priority: int
    available: bool = True
    configured: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    kind: EntryKind
    size: int
    modified_at: float | None
    hidden: bool
    extension: str | None
    indexed: bool = False

    @classmethod
    def from_dir_entry(cls, item: os.DirEntry[str], *, indexed: bool = False) -> "FileEntry":
        try:
            info = item.stat(follow_symlinks=False)
            mode = info.st_mode
            kind = entry_kind(mode)
            size = info.st_size if kind == "file" else 0
            modified_at = info.st_mtime
        except OSError:
            kind = "other"
            size = 0
            modified_at = None
        suffix = Path(item.name).suffix
        return cls(
            name=item.name,
            path=os.path.abspath(item.path),
            kind=kind,
            size=size,
            modified_at=modified_at,
            hidden=is_hidden(item.name, info if "info" in locals() else None),
            extension=suffix.lower() if suffix else None,
            indexed=indexed,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def is_hidden(name: str, info: os.stat_result | None) -> bool:
    if name.startswith("."):
        return True
    if info is None:
        return False
    file_attributes = getattr(info, "st_file_attributes", 0)
    hidden_attribute = getattr(stat_module, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    if file_attributes & hidden_attribute:
        return True
    flags = getattr(info, "st_flags", 0)
    hidden_flag = getattr(stat_module, "UF_HIDDEN", 0x00008000)
    return bool(flags & hidden_flag)


@dataclass(frozen=True)
class DirectoryPage:
    path: str
    entries: tuple[FileEntry, ...]
    next_cursor: str | None
    total: int
    source: Literal["disk", "index"] = "disk"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "filesystem.directory-page",
            "namespace": "host",
            "path": self.path,
            "entries": [entry.to_dict() for entry in self.entries],
            "next_cursor": self.next_cursor,
            "total": self.total,
            "source": self.source,
        }


@dataclass(frozen=True)
class SearchPage:
    query: str
    entries: tuple[FileEntry, ...]
    next_cursor: str | None
    source: Literal["index", "disk", "hybrid"] = "index"
    index_complete: bool = False
    root: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "filesystem.search-page",
            "namespace": "host",
            "query": self.query,
            "entries": [entry.to_dict() for entry in self.entries],
            "next_cursor": self.next_cursor,
            "source": self.source,
            "index_complete": self.index_complete,
            "root": self.root,
        }
