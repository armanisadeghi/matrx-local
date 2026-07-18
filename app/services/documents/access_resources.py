"""Registration of documents-domain resources with the access-health service.

Resource id scheme:
    notes-canonical                      — the canonical notes directory
    notes-mapping:<folder_id>:<sha8>     — one user-mapped external directory

Each mapped directory is its OWN resource: a dead external drive or read-only
mapped folder degrades only its mapping, never the canonical notes health.
That isolation is the fix for the historical "one bad mapped dir shows a
global Full Disk Access banner" defect.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.services.access_health import Provenance, get_access_health

logger = logging.getLogger(__name__)

NOTES_RESOURCE = "notes-canonical"
NOTES_REGISTRY_SERVICE = "notes_sync"


def mapping_resource_id(folder_id: str, mapped_path: str) -> str:
    digest = hashlib.sha256(str(mapped_path).encode("utf-8")).hexdigest()[:8]
    return f"notes-mapping:{folder_id}:{digest}"


def _notes_root() -> Path:
    from app.services.documents.file_manager import file_manager

    return file_manager.base_dir


def _notes_provenance() -> Provenance:
    try:
        from app.services.paths.manager import path_provenance

        return path_provenance("notes")
    except ImportError:
        return Provenance.DEFAULT


def register_notes_canonical() -> None:
    get_access_health().register(
        NOTES_RESOURCE,
        resolver=_notes_root,
        label="Notes folder",
        provenance=_notes_provenance,
        registry_service=NOTES_REGISTRY_SERVICE,
    )


def register_mapping(folder_id: str, mapped_path: str) -> str:
    """Register one mapped directory as its own resource. Returns resource id."""
    resource_id = mapping_resource_id(folder_id, mapped_path)
    path = Path(mapped_path)
    get_access_health().register(
        resource_id,
        resolver=lambda: path,
        label=f"Mapped folder: {mapped_path}",
        provenance=Provenance.MAPPED,
    )
    return resource_id


def unregister_mapping(folder_id: str, mapped_path: str) -> None:
    get_access_health().unregister(mapping_resource_id(folder_id, mapped_path))


def register_all_mappings() -> list[str]:
    """Register every mapped directory found in mappings.json."""
    from app.services.documents.file_manager import file_manager

    ids: list[str] = []
    try:
        mappings = file_manager.load_local_mappings()
    except Exception:
        logger.warning("[access] could not load notes mappings", exc_info=True)
        return ids
    for folder_id, paths in mappings.items():
        for mapped_path in paths:
            ids.append(register_mapping(folder_id, mapped_path))
    return ids
