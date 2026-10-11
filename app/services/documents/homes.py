"""Homes -> local directories.

Every note has exactly one HOME, a scope (item-homes plan, 2026-10-10). On
disk the home is the note's directory:

* the directory is the home's NAME;
* the organization's name is appended ("General · AI Matrx") ONLY when two
  homes the person can see share a name;
* a directory that matches no home is never turned into a home — creating
  scopes from disk is not a thing; the note stays in its current home and the
  app says so (see ``SyncEngine.home_notices``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.documents.file_manager import _safe_filename

#: The separator between a home's name and its organization's name.
ORG_SEPARATOR = " · "
#: Directory used while a note has no resolved home yet (created locally; the
#: cloud places it in the organization's General on its first push).
DEFAULT_HOME_DIR = "General"


@dataclass(frozen=True)
class Home:
    id: str
    organization_id: str | None
    name: str
    organization_name: str
    dir: str


@dataclass
class HomesDirectory:
    homes: list[Home] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_id = {h.id: h for h in self.homes}
        self._by_dir = {h.dir.casefold(): h for h in self.homes}

    @property
    def empty(self) -> bool:
        return not self.homes

    def dir_for(self, scope_id: str | None) -> str | None:
        home = self._by_id.get(scope_id) if scope_id else None
        return home.dir if home else None

    def home_for_dir(self, directory: str | None) -> Home | None:
        if not directory:
            return None
        return self._by_dir.get(directory.casefold())

    def general_for_org(self, organization_id: str | None) -> Home | None:
        for h in self.homes:
            if h.organization_id == organization_id and h.name.casefold() == "general":
                return h
        return None

    def as_list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": h.id,
                "organization_id": h.organization_id,
                "organization_name": h.organization_name,
                "name": h.name,
                "dir": h.dir,
            }
            for h in sorted(self.homes, key=lambda x: (x.dir.casefold(), x.id))
        ]


def build_directory(
    scopes: list[dict[str, Any]], organization_names: dict[str, str]
) -> HomesDirectory:
    """Compute each visible home's local directory name."""
    rows = [
        (str(s["id"]), s.get("organization_id"), str(s.get("name") or "").strip())
        for s in scopes
        if s.get("id") and str(s.get("name") or "").strip()
    ]
    counts: dict[str, int] = {}
    for _id, _org, name in rows:
        key = _safe_filename(name).casefold()
        counts[key] = counts.get(key, 0) + 1

    homes: list[Home] = []
    taken: set[str] = set()
    for scope_id, org_id, name in sorted(rows, key=lambda r: (r[2].casefold(), r[0])):
        org_name = organization_names.get(str(org_id), "") if org_id else ""
        base = _safe_filename(name)
        directory = base
        if counts[base.casefold()] > 1 and org_name:
            directory = _safe_filename(f"{name}{ORG_SEPARATOR}{org_name}")
        if directory.casefold() in taken:
            # Same name inside one organization: the id keeps directories distinct.
            directory = _safe_filename(f"{directory} ({scope_id[:8]})")
        taken.add(directory.casefold())
        homes.append(Home(scope_id, org_id, name, org_name, directory))
    return HomesDirectory(homes)
