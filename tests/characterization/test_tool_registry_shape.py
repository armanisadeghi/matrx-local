"""Characterization gate: the engine's tool surface is pinned EXACTLY.

This test snapshots the complete tool surface (post Phase-4 registry
unification, 2026-07-10):

  - TOOL_HANDLERS names        (app/tools/dispatcher.py)   — 108 tools
  - catalog cloud names        (app/tools/catalog.py)      — 108 names
  - tool_* source functions    (app/tools/tools/*.py)      — 108 funcs
  - orphan source functions    (source − dispatcher)       — 0 funcs

plus the derived relationships between them. Any drift — a tool added,
removed, or renamed anywhere — fails loudly here.

IF A FAILURE IS INTENTIONAL: update tool_surface_snapshot.json (regenerate
with the snippet in `_regenerate_hint` below) AND emit + apply a cloud
changeset (`uv run python -m app.tools.tool_sync emit-changeset`). The
catalog's cloud names are live Supabase `tool.definition` rows bound to
executor `matrx-local`; silently changing them strands cloud-registered
tools.

Runs without an engine, network, or credentials.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from app.tools.catalog import get_catalog
from app.tools.dispatcher import TOOL_HANDLERS

PROJECT_ROOT = Path(__file__).parent.parent.parent
TOOLS_SRC_DIR = PROJECT_ROOT / "app" / "tools" / "tools"
SNAPSHOT_FILE = Path(__file__).parent / "tool_surface_snapshot.json"

_regenerate_hint = (
    "Regenerate the snapshot (from the repo root) with:\n"
    "  uv run python -c \""
    "import tests.characterization.test_tool_registry_shape as t; t.regenerate()\"\n"
    "then review the diff of tests/characterization/tool_surface_snapshot.json "
    "line by line — every added/removed name must be intentional — and emit a "
    "cloud changeset (python -m app.tools.tool_sync emit-changeset) so "
    "tool.definition/tool.binding stay in step."
)


# ---------------------------------------------------------------------------
# Current-state extraction
# ---------------------------------------------------------------------------


def _source_tool_functions() -> list[str]:
    """All tool_* function names defined across app/tools/tools/*.py (AST-based,
    no imports — counts what exists in source, wired or not)."""
    names: set[str] = set()
    for py_file in sorted(TOOLS_SRC_DIR.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name.startswith("tool_")
            ):
                names.add(node.name)
    return sorted(names)


def current_surface() -> dict[str, list[str]]:
    dispatcher_names = sorted(TOOL_HANDLERS.keys())
    handler_funcs = sorted({h.__name__ for h in TOOL_HANDLERS.values()})
    source_funcs = _source_tool_functions()
    orphans = sorted(set(source_funcs) - set(handler_funcs))
    catalog_cloud_names = sorted(e.cloud_name for e in get_catalog())
    return {
        "dispatcher_tool_names": dispatcher_names,
        "catalog_cloud_names": catalog_cloud_names,
        "source_tool_functions": source_funcs,
        "dispatcher_handler_function_names": handler_funcs,
        "orphan_source_functions": orphans,
    }


def regenerate() -> None:
    """Rewrite the committed snapshot from the current tool surface."""
    SNAPSHOT_FILE.write_text(json.dumps(current_surface(), indent=2) + "\n")
    print(f"Snapshot regenerated: {SNAPSHOT_FILE}")


def _snapshot() -> dict[str, list[str]]:
    return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))


def _diff_message(kind: str, expected: list[str], actual: list[str]) -> str:
    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    return (
        f"TOOL SURFACE CHANGED — {kind}:\n"
        f"  expected {len(expected)} entries, got {len(actual)}\n"
        f"  ADDED (not in snapshot):   {added or '—'}\n"
        f"  REMOVED (missing now):     {removed or '—'}\n\n"
        "The engine's tool surface is a platform contract (advertised to the "
        "cloud via the catalog's cloud names and to matrx-extend via "
        "/extension/rpc capabilities). If this change is INTENTIONAL, update "
        "the snapshot AND the cloud registry.\n\n" + _regenerate_hint
    )


# ---------------------------------------------------------------------------
# Exact-set pins
# ---------------------------------------------------------------------------


def test_dispatcher_tool_names_exact() -> None:
    expected = _snapshot()["dispatcher_tool_names"]
    actual = sorted(TOOL_HANDLERS.keys())
    assert actual == expected, _diff_message("dispatcher TOOL_HANDLERS names", expected, actual)


def test_catalog_cloud_names_exact() -> None:
    """The catalog's canonical cloud names (tool.definition names) — the
    platform-wide contract. 49 of these are LIVE Supabase rows bound to
    executor 'matrx-local'; renames strand cloud tools."""
    expected = _snapshot()["catalog_cloud_names"]
    actual = sorted(e.cloud_name for e in get_catalog())
    assert actual == expected, _diff_message("catalog cloud names", expected, actual)


def test_source_tool_functions_exact() -> None:
    expected = _snapshot()["source_tool_functions"]
    actual = _source_tool_functions()
    assert actual == expected, _diff_message(
        "tool_* source functions in app/tools/tools/", expected, actual
    )


def test_dispatcher_handler_functions_exact() -> None:
    expected = _snapshot()["dispatcher_handler_function_names"]
    actual = sorted({h.__name__ for h in TOOL_HANDLERS.values()})
    assert actual == expected, _diff_message(
        "dispatcher handler function names", expected, actual
    )


def test_orphan_source_functions_exact() -> None:
    """Source tool_* functions NOT wired into the dispatcher — ZERO since the
    2026-07 registry unification (the 18 historical orphans — mail, messages,
    contacts, calendar, photos, location, speech, browser_close — were all
    wired). A new orphan means someone wrote a tool and forgot to register
    it; wire it into TOOL_HANDLERS (and the catalog picks it up) or delete it."""
    expected = _snapshot()["orphan_source_functions"]
    surface = current_surface()
    actual = surface["orphan_source_functions"]
    assert actual == expected, _diff_message(
        "orphan tool functions (source − dispatcher)", expected, actual
    )


# ---------------------------------------------------------------------------
# Derived relationships
# ---------------------------------------------------------------------------


def test_snapshot_counts() -> None:
    """Headline counts — makes count drift obvious at a glance."""
    snap = _snapshot()
    counts = {k: len(v) for k, v in snap.items()}
    assert counts == {
        "dispatcher_tool_names": 108,
        "catalog_cloud_names": 108,
        "source_tool_functions": 108,
        "dispatcher_handler_function_names": 108,
        "orphan_source_functions": 0,
    }, (
        f"TOOL SURFACE COUNTS CHANGED: {counts}. If intentional, update this "
        "test, the snapshot, AND the cloud registry.\n" + _regenerate_hint
    )


def test_dispatcher_names_unique_per_handler() -> None:
    """The dispatcher maps N names onto N distinct handler functions —
    no two tool names share a handler (current behavior; a deliberate alias
    would change this and must update the snapshot)."""
    assert len(TOOL_HANDLERS) == len({h.__name__ for h in TOOL_HANDLERS.values()}), (
        "Two dispatcher tool names now share one handler function — if an "
        "alias is intentional, update this characterization and the snapshot."
    )
