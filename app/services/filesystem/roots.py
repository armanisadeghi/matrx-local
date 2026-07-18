"""OS-neutral Places and crawl-root discovery.

No absolute platform path is compiled here.  Semantic user folders are
resolved from XDG configuration when present and otherwise relative to the
runtime home returned by ``Path.home()``.  Mounted volumes come from psutil's
portable partition API.
"""

from __future__ import annotations

import os
import ntpath
import re
import sys
import uuid
import hashlib
from pathlib import Path

import psutil

from app.services.filesystem.models import Place
_COMMON_STANDARD_DIRS = {
    "desktop": ("Desktop", "Desktop"),
    "documents": ("Documents", "Documents"),
    "downloads": ("Downloads", "Downloads"),
    "pictures": ("Pictures", "Pictures"),
    "music": ("Music", "Music"),
}

_WINDOWS_KNOWN_FOLDER_IDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}

_VIRTUAL_FILESYSTEMS = {
    "autofs", "cgroup", "cgroup2", "devfs", "devtmpfs", "fusectl", "mqueue",
    "proc", "procfs", "securityfs", "sysfs", "tmpfs", "tracefs",
}


def _xdg_user_dirs(home: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))) / "user-dirs.dirs"
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        match = re.match(r'^XDG_([A-Z]+)_DIR="(.+)"$', line.strip())
        if not match:
            continue
        value = match.group(2).replace("$HOME", str(home))
        result[match.group(1).lower()] = Path(value).expanduser()
    return result


def normalize_path_key(path: str, *, platform: str | None = None) -> str:
    """Stable dedupe key, including Windows drive/UNC semantics in tests."""
    target = platform or sys.platform
    if target.startswith("win"):
        windows_path = path.replace("/", "\\")
        folded = windows_path.casefold()
        if folded.startswith("\\\\?\\unc\\"):
            windows_path = "\\\\" + windows_path[8:]
        elif folded.startswith("\\\\?\\"):
            windows_path = windows_path[4:]
        return ntpath.normcase(ntpath.normpath(windows_path))
    return os.path.normcase(os.path.normpath(path))


def _path_id(prefix: str, path: Path) -> str:
    digest = hashlib.sha256(normalize_path_key(str(path)).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _windows_known_folders() -> dict[str, Path]:
    if not sys.platform.startswith("win"):
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
            ]

        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        result: dict[str, Path] = {}
        for key, raw_guid in _WINDOWS_KNOWN_FOLDER_IDS.items():
            guid = GUID.from_buffer_copy(uuid.UUID(raw_guid).bytes_le)
            output = ctypes.c_wchar_p()
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(output)) == 0:
                try:
                    result[key] = Path(output.value)
                finally:
                    ole32.CoTaskMemFree(output)
        return result
    except Exception:
        return {}


def _standard_user_dirs(home: Path) -> dict[str, tuple[str, Path]]:
    if sys.platform.startswith("win"):
        resolved = _windows_known_folders()
        fallbacks = {**_COMMON_STANDARD_DIRS, "videos": ("Videos", "Videos")}
    elif sys.platform == "darwin":
        resolved = {}
        fallbacks = {**_COMMON_STANDARD_DIRS, "videos": ("Movies", "Movies")}
    else:
        resolved = _xdg_user_dirs(home)
        fallbacks = {**_COMMON_STANDARD_DIRS, "videos": ("Videos", "Videos")}
    return {
        key: (label, resolved.get(key, home / fallback_name))
        for key, (label, fallback_name) in fallbacks.items()
    }


def configured_priority_roots() -> list[dict[str, str]]:
    """Return the user-authored priority-root settings without Places dedupe.

    Places are a presentation of discovered locations and intentionally merge
    overlapping paths.  Settings must round-trip the authored list instead of
    trying to reconstruct it from that merged presentation.
    """
    try:
        from app.services.cloud_sync.settings_sync import get_settings_sync

        stored = get_settings_sync().get("filesystem_index", {}) or {}
        values = stored.get("priority_roots", []) if isinstance(stored, dict) else []
    except Exception:
        values = []
    roots: list[dict[str, str]] = []
    for index, raw in enumerate(values):
        if isinstance(raw, str):
            roots.append({"label": Path(raw).name or f"Priority root {index + 1}", "path": raw})
        elif isinstance(raw, dict) and isinstance(raw.get("path"), str):
            path = raw["path"]
            roots.append(
                {
                    "label": str(raw.get("label") or Path(path).name or f"Priority root {index + 1}"),
                    "path": path,
                }
            )
    return roots


def discover_places() -> list[Place]:
    from app.services.paths.manager import all_paths

    home = Path.home().expanduser().resolve()
    discovered: list[Place] = [
        Place("home", "Home", str(home), "home", 100, available=home.is_dir())
    ]
    seen = {normalize_path_key(str(home))}

    for item in all_paths():
        if item["name"] not in {"notes", "files", "code", "workspaces", "agent_data"}:
            continue
        path = Path(str(item.get("effective") or item["current"])).expanduser().absolute()
        key = normalize_path_key(str(path))
        if key in seen:
            continue
        seen.add(key)
        discovered.append(
            Place(
                f"configured-{item['name']}",
                str(item["label"]),
                str(path),
                "configured",
                125 if item["name"] == "code" else 115,
                available=path.is_dir(),
                configured=bool(item["is_custom"]),
            )
        )

    for configured_root in configured_priority_roots():
        label = configured_root["label"]
        raw = configured_root["path"]
        path = Path(raw).expanduser().absolute()
        key = normalize_path_key(str(path))
        if key in seen:
            for index, existing in enumerate(discovered):
                if normalize_path_key(existing.path) == key:
                    discovered[index] = Place(
                        existing.id,
                        label or existing.label,
                        existing.path,
                        existing.category,
                        max(existing.priority, 130),
                        existing.available,
                        True,
                    )
                    break
            continue
        seen.add(key)
        discovered.append(
            Place(_path_id("priority", path), label, str(path), "configured", 130, path.is_dir(), True)
        )

    for key, (label, resolved_path) in _standard_user_dirs(home).items():
        path = resolved_path.expanduser().absolute()
        normalized = normalize_path_key(str(path))
        if normalized in seen or not path.is_dir():
            continue
        seen.add(normalized)
        discovered.append(Place(key, label, str(path), "standard", 80, True))

    for partition in psutil.disk_partitions(all=True):
        if partition.fstype.lower() in _VIRTUAL_FILESYSTEMS:
            continue
        path = Path(partition.mountpoint).absolute()
        normalized = normalize_path_key(str(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        label = path.name or partition.device or str(path)
        discovered.append(Place(_path_id("volume", path), label, str(path), "volume", 10, True))

    return sorted(discovered, key=lambda place: (-place.priority, place.label.casefold()))
