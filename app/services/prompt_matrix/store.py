"""Atomic JSON persistence for prompt-matrix library + templates.

Files live under ``MATRX_HOME_DIR / "prompt-matrix"`` so they survive renderer
localStorage wipes and are easy to back up / copy between machines.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LIBRARY_VERSION = 1
TEMPLATES_VERSION = 1


def _prompt_matrix_dir() -> Path:
    # Lazy import — app.config pulls platform_ctx which can circular-import
    # during test collection if we bind MATRX_HOME_DIR at module load.
    from app.config import MATRX_HOME_DIR

    return MATRX_HOME_DIR / "prompt-matrix"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        logger.error(
            "[prompt-matrix] Corrupt or unreadable %s — resetting: %s",
            path,
            err,
        )
        return None
    if not isinstance(data, dict):
        logger.error(
            "[prompt-matrix] %s is not a JSON object — resetting",
            path,
        )
        return None
    return data


class PromptMatrixStore:
    """Thread-safe read/write of library.json + templates.json."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else _prompt_matrix_dir()
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def library_path(self) -> Path:
        return self._root / "library.json"

    @property
    def templates_path(self) -> Path:
        return self._root / "templates.json"

    def paths(self) -> dict[str, str]:
        return {
            "root": str(self._root),
            "library": str(self.library_path),
            "templates": str(self.templates_path),
        }

    def empty_library(self) -> dict[str, Any]:
        return {"v": LIBRARY_VERSION, "entries": []}

    def empty_templates(self) -> dict[str, Any]:
        return {"v": TEMPLATES_VERSION, "templates": []}

    def load_library(self) -> dict[str, Any]:
        with self._lock:
            raw = _read_json(self.library_path)
            if raw is None:
                return self.empty_library()
            entries = raw.get("entries")
            if not isinstance(entries, list):
                logger.error(
                    "[prompt-matrix] library.json missing entries[] — resetting",
                )
                return self.empty_library()
            return {"v": LIBRARY_VERSION, "entries": entries}

    def save_library(self, entries: list[Any]) -> dict[str, Any]:
        if not isinstance(entries, list):
            raise ValueError("entries must be a list")
        payload = {"v": LIBRARY_VERSION, "entries": entries}
        with self._lock:
            _atomic_write(self.library_path, payload)
        logger.info(
            "[prompt-matrix] Wrote library (%d entries) → %s",
            len(entries),
            self.library_path,
        )
        return payload

    def load_templates(self) -> dict[str, Any]:
        with self._lock:
            raw = _read_json(self.templates_path)
            if raw is None:
                return self.empty_templates()
            templates = raw.get("templates")
            if not isinstance(templates, list):
                logger.error(
                    "[prompt-matrix] templates.json missing templates[] — resetting",
                )
                return self.empty_templates()
            return {"v": TEMPLATES_VERSION, "templates": templates}

    def save_templates(self, templates: list[Any]) -> dict[str, Any]:
        if not isinstance(templates, list):
            raise ValueError("templates must be a list")
        payload = {"v": TEMPLATES_VERSION, "templates": templates}
        with self._lock:
            _atomic_write(self.templates_path, payload)
        logger.info(
            "[prompt-matrix] Wrote templates (%d) → %s",
            len(templates),
            self.templates_path,
        )
        return payload


_store: PromptMatrixStore | None = None


def get_prompt_matrix_store() -> PromptMatrixStore:
    global _store
    if _store is None:
        _store = PromptMatrixStore()
    return _store
