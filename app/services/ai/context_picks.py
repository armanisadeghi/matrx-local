"""The pick — a NARROW LOCAL READING of the wire shape until matrx-connect ships it to this repo.

Canonical: aidream ``packages/matrx-connect/matrx_connect/context/context_picks.py`` (matrx-connect
0.1.258+; this repo's lock pins 0.1.221, which has no pick model). Contract: aidream
``common-docs/systems/data/scopes-context/projects/any-table-context/CONTRACT.md`` §1, §2. Held to the
canonical fixture by ``tests/unit/test_local_request_reads_context_picks.py`` (a copy of
``packages/matrx-connect/tests/fixtures/context_picks_cases.json``). When the lock reaches a
matrx-connect with ``matrx_connect.context.context_picks``, delete this module and import that one.

Only what a local request needs: the model, ``canonical_picks``, ``fold_legacy_selection``,
``legacy_selection`` and the construction-time mixin. The local runtime declares ``scope_ids`` only
(no ``active_scope_type_ids``), so whole-table picks travel in ``context_picks`` alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ENTITY_PREFIX = "entity:"
# Mirrors matrx_utils.entity_address.ENTITY_ADDRESS_PATTERN exactly.
_ENTITY_ADDRESS = re.compile(
    r"^entity:([a-z][a-z0-9_]{0,62})(?:@([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}))?$"
)
_LEGACY_FIELDS = ("scope_ids", "active_scope_type_ids")


class ContextPick(BaseModel):
    """One picked table, or records of it, or fields of those, in one organization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID | None
    table: str | None
    view: str | None = None
    record_ids: list[UUID] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)

    @field_validator("table")
    @classmethod
    def _one_table_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if text.startswith(ENTITY_PREFIX):
            found = _ENTITY_ADDRESS.match(text)
            if found is None:
                raise ValueError(
                    f"{value!r} is not a built-in table address (entity:<token>)"
                )
            if found.group(2) is not None:
                raise ValueError(
                    "a pick names its organization in organization_id, never as '@org' on the table"
                )
            return text
        try:
            return str(UUID(text))
        except ValueError:
            raise ValueError(
                f"{value!r} is neither a custom Table id nor an entity:<token> address"
            ) from None

    @model_validator(mode="after")
    def _a_tableless_pick_is_a_folded_record_pick(self) -> ContextPick:
        if self.table is None and (
            self.organization_id is not None or not self.record_ids
        ):
            raise ValueError(
                "a pick with no table is a folded legacy record pick: organization_id must be null "
                "and record_ids non-empty"
            )
        return self

    @property
    def is_built_in(self) -> bool:
        return self.table is not None and self.table.startswith(ENTITY_PREFIX)


class LegacySelection(NamedTuple):
    scope_ids: list[str]
    active_scope_type_ids: list[str]
    built_in: list[ContextPick]


type PickInput = ContextPick | Mapping[str, object]


def _as_pick(value: PickInput) -> ContextPick:
    return (
        value if isinstance(value, ContextPick) else ContextPick.model_validate(value)
    )


def _unique(values: Iterable[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


def _uuid_or_none(value: object) -> UUID | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return UUID(value.strip())
    except ValueError:
        return None


def canonical_picks(picks: Iterable[PickInput] | None) -> list[ContextPick]:
    """Merge picks of one organization, table, view and whole-or-records; drop what a folded legacy
    pick repeats of a filled one (CONTRACT §1)."""
    given = [_as_pick(p) for p in (picks or [])]
    filled_records = {r for p in given if p.table is not None for r in p.record_ids}
    filled_tables = {
        p.table
        for p in given
        if p.organization_id is not None and p.table and not p.record_ids
    }
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for pick in given:
        records = pick.record_ids
        if pick.table is None:
            records = [r for r in records if r not in filled_records]
            if not records:
                continue
        elif (
            pick.organization_id is None and not records and pick.table in filled_tables
        ):
            continue
        key = (pick.organization_id, pick.table, pick.view, bool(records))
        slot = merged.setdefault(key, {"pick": pick, "record_ids": [], "fields": []})
        slot["record_ids"].extend(records)
        slot["fields"].extend(pick.fields)
    return [
        slot["pick"].model_copy(
            update={
                "record_ids": _unique(slot["record_ids"]),
                "fields": _unique(slot["fields"]),
            }
        )
        for slot in merged.values()
    ]


def fold_legacy_selection(
    scope_ids: Iterable[object] | None,
    active_scope_type_ids: Iterable[object] | None,
    picks: Iterable[PickInput] | None = None,
) -> list[ContextPick]:
    """``picks`` plus the legacy fields read as picks, canonical; a non-uuid legacy id is skipped."""
    record_ids = [
        u for u in (_uuid_or_none(s) for s in (scope_ids or [])) if u is not None
    ]
    type_ids = [
        u
        for u in (_uuid_or_none(t) for t in (active_scope_type_ids or []))
        if u is not None
    ]
    folded: list[ContextPick] = []
    if record_ids:
        folded.append(
            ContextPick(organization_id=None, table=None, record_ids=record_ids)
        )
    folded.extend(ContextPick(organization_id=None, table=str(t)) for t in type_ids)
    return canonical_picks([*(picks or []), *folded])


def legacy_selection(picks: Iterable[PickInput] | None) -> LegacySelection:
    """Custom-table record ids, whole custom tables, and the built-in picks (never in scope_ids)."""
    scope_ids: list[str] = []
    type_ids: list[str] = []
    built_in: list[ContextPick] = []
    for pick in canonical_picks(picks):
        if pick.is_built_in:
            built_in.append(pick)
        elif pick.record_ids:
            scope_ids.extend(str(r) for r in pick.record_ids)
        elif pick.table is not None:
            type_ids.append(pick.table)
    return LegacySelection(_unique(scope_ids), _unique(type_ids), built_in)


def _legacy_ids_kept(sent: object, derived: list[str]) -> list[str] | None:
    kept = list(sent) if isinstance(sent, list | tuple) else []
    named = {str(_uuid_or_none(s) or s) for s in kept}
    out = kept + [d for d in derived if d not in named]
    return out or (None if sent is None else out)


class CarriesContextPicks(BaseModel):
    """``context_picks`` kept in agreement with the legacy fields the model declares: the picks
    become ``fold_legacy_selection(legacy fields, context_picks)``; each legacy field becomes what
    the body sent, then the ids only the picks name (a legacy body's fields stay byte-identical)."""

    context_picks: list[ContextPick] | None = None

    @model_validator(mode="after")
    def _carry_context_picks(self) -> CarriesContextPicks:
        fields = type(self).model_fields
        sent = {
            name: getattr(self, name, None) for name in _LEGACY_FIELDS if name in fields
        }
        picks = fold_legacy_selection(
            sent.get("scope_ids"), sent.get("active_scope_type_ids"), self.context_picks
        )
        legacy = legacy_selection(picks)
        derived = {
            "scope_ids": legacy.scope_ids,
            "active_scope_type_ids": legacy.active_scope_type_ids,
        }
        mark_set = "context_picks" in self.__pydantic_fields_set__
        update: dict[str, object] = {"context_picks": picks or None}
        for name in _LEGACY_FIELDS:
            if name in fields:
                update[name] = _legacy_ids_kept(sent.get(name), derived[name])
        for name, value in update.items():
            if value != getattr(self, name, None):
                self.__dict__[name] = value
                if mark_set:
                    self.__pydantic_fields_set__.add(name)
        return self


__all__ = [
    "CarriesContextPicks",
    "ContextPick",
    "LegacySelection",
    "canonical_picks",
    "fold_legacy_selection",
    "legacy_selection",
]
