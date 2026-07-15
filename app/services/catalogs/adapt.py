"""Payload → legacy-dataclass adaptation for catalog consumers.

Catalog payloads are faithful snake_case mirrors of the legacy compiled
structs, so adaptation is mechanical: filter the payload to the dataclass's
fields and construct. Extra payload keys (``is_default``, future columns)
are dropped; a missing REQUIRED field makes that one entry fail.

Fault isolation (the /image-gen/loras lesson): one malformed remote entry
must degrade to "that one item is missing", never blank a whole catalog or
crash a service — each entry is adapted independently, failures are skipped
with a loud log.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable, TypeVar

from app.common.system_logger import get_logger

from app.services.catalogs.models import CatalogEntry

logger = get_logger()

T = TypeVar("T")


def entry_to_dataclass(
    entry: CatalogEntry,
    cls: type[T],
    *,
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> T:
    """Adapt one entry's payload into ``cls``. Raises on a bad payload."""
    payload = dict(entry.payload)
    if transform is not None:
        payload = transform(payload)
    field_names = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    kwargs = {k: v for k, v in payload.items() if k in field_names}
    return cls(**kwargs)


def entries_to_dataclasses(
    entries: list[CatalogEntry],
    cls: type[T],
    *,
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> list[T]:
    """Adapt entries independently — skip-and-scream on malformed ones."""
    out: list[T] = []
    for entry in entries:
        try:
            out.append(entry_to_dataclass(entry, cls, transform=transform))
        except Exception as exc:  # noqa: BLE001 — one bad entry never blanks the catalog
            logger.error(
                "[catalogs] skipping malformed %s/%s (cannot adapt to %s): %s",
                entry.kind,
                entry.key,
                cls.__name__,
                exc,
            )
    if entries and not out:
        logger.error(
            "[catalogs] EVERY %s entry failed adaptation to %s — the catalog "
            "payload schema has drifted from the code",
            entries[0].kind,
            cls.__name__,
        )
    return out
