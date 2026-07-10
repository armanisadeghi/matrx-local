"""Tool registry parity — dispatcher ↔ catalog exact-set consistency.

Replaced the old floor-79 count test (2026-07, Phase 4 registry unification):
a floor lets tools silently drift; these tests pin the EXACT relationship
between the three surfaces that must always agree:

  1. TOOL_HANDLERS (app/tools/dispatcher.py)   — what the engine can run
  2. the catalog   (app/tools/catalog.py)      — what the platform advertises
  3. the snapshot  (tests/characterization/tool_surface_snapshot.json)
                                               — the reviewed, committed pin

Plus structural guarantees: every catalog input_schema is valid JSON Schema
(object-shaped, required ⊆ properties, sane property types), cloud names are
canonical `local_*` snake_case and unique, and platform gating is declared
for every known OS-specific tool.

Runs without an engine, network, or credentials.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.tools.catalog import get_catalog, get_by_cloud_name, get_by_dispatcher_name
from app.tools.dispatcher import TOOL_HANDLERS

SNAPSHOT_FILE = (
    Path(__file__).parent.parent / "characterization" / "tool_surface_snapshot.json"
)

# Tools that MUST declare platform gating in the catalog (they self-gate at
# runtime; the catalog declaration is the advertised contract). Extend this
# list when adding a new OS-specific tool.
EXPECTED_DARWIN_ONLY = {
    "AppleScript",
    "ListEmails", "SendEmail", "GetEmailAccounts",
    "ListMessages", "ListConversations", "SendMessage",
    "SearchContacts", "GetContact",
    "ListEvents", "CreateEvent", "ListReminders", "CreateReminder",
    "SearchPhotos", "GetPhoto",
    "GetLocation",
    "TranscribeWithSpeech", "ListSpeechLocales",
}
EXPECTED_WINDOWS_ONLY = {
    "PSSetEnv", "RegistryRead", "RegistryWrite", "EventLog", "WindowsFeatures",
}

_JSON_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}


def _snapshot() -> dict[str, list[str]]:
    return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Exact-set consistency
# ---------------------------------------------------------------------------


def test_catalog_covers_dispatcher_exactly() -> None:
    """Every dispatcher tool has exactly one catalog entry and vice versa."""
    dispatcher_names = set(TOOL_HANDLERS)
    catalog_names = {e.dispatcher_name for e in get_catalog()}
    assert catalog_names == dispatcher_names, (
        "DISPATCHER ↔ CATALOG DRIFT:\n"
        f"  in dispatcher, not catalog: {sorted(dispatcher_names - catalog_names) or '—'}\n"
        f"  in catalog, not dispatcher: {sorted(catalog_names - dispatcher_names) or '—'}"
    )


def test_catalog_matches_characterization_snapshot() -> None:
    """Catalog cloud names match the committed snapshot exactly (the reviewed
    platform contract). On intentional change, regenerate the snapshot — see
    tests/characterization/test_tool_registry_shape.py."""
    snap = _snapshot()
    expected = snap["catalog_cloud_names"]
    actual = sorted(e.cloud_name for e in get_catalog())
    assert actual == expected, (
        "CATALOG CLOUD NAMES CHANGED vs snapshot:\n"
        f"  added:   {sorted(set(actual) - set(expected)) or '—'}\n"
        f"  removed: {sorted(set(expected) - set(actual)) or '—'}\n"
        "If intentional: regenerate tool_surface_snapshot.json AND emit a "
        "cloud changeset (python -m app.tools.tool_sync emit-changeset)."
    )


def test_catalog_handlers_are_dispatcher_handlers() -> None:
    """Each catalog entry's handler IS the dispatcher's handler object."""
    mismatched = [
        e.dispatcher_name
        for e in get_catalog()
        if TOOL_HANDLERS.get(e.dispatcher_name) is not e.handler
    ]
    assert not mismatched, f"catalog handler is not the dispatcher handler: {mismatched}"


def test_lookups_agree() -> None:
    for e in get_catalog():
        assert get_by_cloud_name(e.cloud_name) is e
        assert get_by_dispatcher_name(e.dispatcher_name) is e


# ---------------------------------------------------------------------------
# Naming rules
# ---------------------------------------------------------------------------


def test_cloud_names_unique_and_canonical() -> None:
    names = [e.cloud_name for e in get_catalog()]
    dupes = sorted(n for n in set(names) if names.count(n) > 1)
    assert not dupes, f"duplicate cloud names: {dupes}"
    import re

    bad = [n for n in names if not re.match(r"^local_[a-z0-9_]+$", n)]
    assert not bad, f"non-canonical cloud names (must be local_* snake_case): {bad}"


# ---------------------------------------------------------------------------
# Schema validity
# ---------------------------------------------------------------------------


def _assert_property_schema(tool: str, prop: str, schema: object) -> None:
    assert isinstance(schema, dict), f"{tool}.{prop}: property schema is not a dict"
    if "anyOf" in schema:
        branches = schema["anyOf"]
        assert isinstance(branches, list) and branches, f"{tool}.{prop}: empty anyOf"
        for branch in branches:
            assert isinstance(branch, dict), f"{tool}.{prop}: anyOf branch not a dict"
            if "type" in branch:
                assert branch["type"] in _JSON_SCHEMA_TYPES, (
                    f"{tool}.{prop}: bad anyOf branch type {branch['type']!r}"
                )
        return
    if "enum" in schema or "$ref" in schema or "allOf" in schema:
        return  # enum-only / referenced sub-model schemas are valid without `type`
    assert "type" in schema, f"{tool}.{prop}: property schema has no type/anyOf/enum"
    assert schema["type"] in _JSON_SCHEMA_TYPES, (
        f"{tool}.{prop}: invalid JSON Schema type {schema['type']!r}"
    )


def test_every_input_schema_is_valid() -> None:
    """Structural JSON Schema validation for every catalog entry."""
    for e in get_catalog():
        s = e.input_schema
        assert isinstance(s, dict), f"{e.cloud_name}: input_schema not a dict"
        assert s.get("type") == "object", f"{e.cloud_name}: input_schema.type != 'object'"
        props = s.get("properties", {})
        assert isinstance(props, dict), f"{e.cloud_name}: properties not a dict"
        required = s.get("required", [])
        assert isinstance(required, list), f"{e.cloud_name}: required not a list"
        missing = sorted(set(required) - set(props))
        assert not missing, (
            f"{e.cloud_name}: required fields not present in properties: {missing}"
        )
        for prop_name, prop_schema in props.items():
            _assert_property_schema(e.cloud_name, prop_name, prop_schema)


def test_every_entry_has_description_and_category() -> None:
    missing_desc = [e.cloud_name for e in get_catalog() if not e.description.strip()]
    missing_cat = [e.cloud_name for e in get_catalog() if not e.category.strip()]
    assert not missing_desc, f"catalog entries without a description: {missing_desc}"
    assert not missing_cat, f"catalog entries without a category: {missing_cat}"


# ---------------------------------------------------------------------------
# Platform gating
# ---------------------------------------------------------------------------


def test_platform_gating_declared() -> None:
    darwin = {e.dispatcher_name for e in get_catalog() if e.platforms == ("darwin",)}
    windows = {e.dispatcher_name for e in get_catalog() if e.platforms == ("win32",)}
    assert darwin == EXPECTED_DARWIN_ONLY, (
        "macOS-only gating drift:\n"
        f"  gated but unexpected: {sorted(darwin - EXPECTED_DARWIN_ONLY) or '—'}\n"
        f"  expected but ungated: {sorted(EXPECTED_DARWIN_ONLY - darwin) or '—'}"
    )
    assert windows == EXPECTED_WINDOWS_ONLY, (
        "Windows-only gating drift:\n"
        f"  gated but unexpected: {sorted(windows - EXPECTED_WINDOWS_ONLY) or '—'}\n"
        f"  expected but ungated: {sorted(EXPECTED_WINDOWS_ONLY - windows) or '—'}"
    )
