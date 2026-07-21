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
LISTS_VERSION = 2
PROMPTS_VERSION = 1
VARIATION_BATCHES_VERSION = 1
TEMPLATES_VERSION = 1


def _prompt_matrix_dir() -> Path:
    # Bind the configured home only when a default store is constructed.
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

    @property
    def lists_path(self) -> Path:
        return self._root / "lists.json"

    @property
    def prompts_path(self) -> Path:
        return self._root / "prompts.json"

    @property
    def variation_batches_path(self) -> Path:
        return self._root / "variation-batches.json"

    def paths(self) -> dict[str, str]:
        return {
            "root": str(self._root),
            "library": str(self.library_path),
            "lists": str(self.lists_path),
            "prompts": str(self.prompts_path),
            "variationBatches": str(self.variation_batches_path),
            "templates": str(self.templates_path),
        }

    def empty_library(self) -> dict[str, Any]:
        return {"v": LIBRARY_VERSION, "entries": []}

    def empty_templates(self) -> dict[str, Any]:
        return {"v": TEMPLATES_VERSION, "templates": []}

    def empty_lists(self) -> dict[str, Any]:
        return {"v": LISTS_VERSION, "lists": []}

    def empty_prompts(self) -> dict[str, Any]:
        return {"v": PROMPTS_VERSION, "prompts": []}

    def empty_variation_batches(self) -> dict[str, Any]:
        return {"v": VARIATION_BATCHES_VERSION, "batches": []}

    @staticmethod
    def _migrate_library_entry_to_list(entry: dict[str, Any]) -> dict[str, Any] | None:
        """Drop v1 pool/variable kind — a saved list is just options + metadata."""
        entry_id = entry.get("id")
        name = entry.get("name")
        options = entry.get("options")
        updated_at = entry.get("updatedAt")
        if not isinstance(entry_id, str) or not isinstance(name, str):
            return None
        if not isinstance(options, list):
            return None
        ts = updated_at if isinstance(updated_at, (int, float)) else 0
        ts_int = int(ts)
        return {
            "id": entry_id,
            "name": name.strip() or "Untitled list",
            "description": "",
            "options": options,
            "createdAt": ts_int,
            "updatedAt": ts_int,
        }

    def _migrate_library_entries_to_lists(
        self, library_entries: list[Any]
    ) -> list[dict[str, Any]]:
        migrated: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in library_entries:
            if not isinstance(raw, dict):
                continue
            item = self._migrate_library_entry_to_list(raw)
            if item is None or item["id"] in seen:
                continue
            seen.add(item["id"])
            migrated.append(item)
        migrated.sort(key=lambda row: row.get("updatedAt", 0), reverse=True)
        return migrated

    def load_lists(self) -> dict[str, Any]:
        with self._lock:
            raw = _read_json(self.lists_path)
            if raw is not None:
                lists = raw.get("lists")
                if isinstance(lists, list) and len(lists) > 0:
                    return {"v": LISTS_VERSION, "lists": lists}

            library_raw = _read_json(self.library_path)
            if library_raw is not None:
                entries = library_raw.get("entries")
                if isinstance(entries, list) and len(entries) > 0:
                    migrated = self._migrate_library_entries_to_lists(entries)
                    if migrated:
                        payload = {"v": LISTS_VERSION, "lists": migrated}
                        _atomic_write(self.lists_path, payload)
                        logger.info(
                            "[prompt-matrix] Migrated %d library.json entries → %s",
                            len(migrated),
                            self.lists_path,
                        )
                        return payload

            if raw is None:
                return self.empty_lists()
            lists = raw.get("lists")
            if not isinstance(lists, list):
                logger.error(
                    "[prompt-matrix] lists.json missing lists[] — resetting",
                )
                return self.empty_lists()
            return {"v": LISTS_VERSION, "lists": lists}

    def save_lists(self, lists: list[Any]) -> dict[str, Any]:
        if not isinstance(lists, list):
            raise ValueError("lists must be a list")
        payload = {"v": LISTS_VERSION, "lists": lists}
        with self._lock:
            _atomic_write(self.lists_path, payload)
        logger.info(
            "[prompt-matrix] Wrote lists (%d) → %s",
            len(lists),
            self.lists_path,
        )
        return payload

    def load_prompts(self) -> dict[str, Any]:
        with self._lock:
            raw = _read_json(self.prompts_path)
            if raw is None:
                return self.empty_prompts()
            prompts = raw.get("prompts")
            if not isinstance(prompts, list):
                logger.error(
                    "[prompt-matrix] prompts.json missing prompts[] — resetting",
                )
                return self.empty_prompts()
            return {"v": PROMPTS_VERSION, "prompts": prompts}

    def save_prompts(self, prompts: list[Any]) -> dict[str, Any]:
        if not isinstance(prompts, list):
            raise ValueError("prompts must be a list")
        payload = {"v": PROMPTS_VERSION, "prompts": prompts}
        with self._lock:
            _atomic_write(self.prompts_path, payload)
        logger.info(
            "[prompt-matrix] Wrote prompts (%d) → %s",
            len(prompts),
            self.prompts_path,
        )
        return payload

    def load_variation_batches(self) -> dict[str, Any]:
        with self._lock:
            raw = _read_json(self.variation_batches_path)
            if raw is None:
                return self.empty_variation_batches()
            batches = raw.get("batches")
            if not isinstance(batches, list):
                logger.error(
                    "[prompt-matrix] variation-batches.json missing batches[] — resetting",
                )
                return self.empty_variation_batches()
            return {"v": VARIATION_BATCHES_VERSION, "batches": batches}

    def save_variation_batches(self, batches: list[Any]) -> dict[str, Any]:
        if not isinstance(batches, list):
            raise ValueError("batches must be a list")
        payload = {"v": VARIATION_BATCHES_VERSION, "batches": batches}
        with self._lock:
            _atomic_write(self.variation_batches_path, payload)
        logger.info(
            "[prompt-matrix] Wrote variation batches (%d) → %s",
            len(batches),
            self.variation_batches_path,
        )
        return payload

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
