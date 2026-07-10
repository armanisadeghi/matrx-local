"""Characterization gate: the engine's tool surface is pinned EXACTLY.

This test snapshots the complete tool surface as of 2026-07-10:

  - TOOL_HANDLERS names        (app/tools/dispatcher.py)         — 90 tools
  - LOCAL_TOOL_MANIFEST names  (app/tools/local_tool_manifest.py) — 62 tools
  - tool_* source functions    (app/tools/tools/*.py)             — 108 funcs
  - orphan source functions    (source − dispatcher handlers)     — 18 funcs

plus the derived relationships between them. Any drift — a tool added,
removed, or renamed anywhere — fails loudly here.

IF A FAILURE IS INTENTIONAL: update tool_surface_snapshot.json (regenerate
with the snippet in `_regenerate_hint` below) AND make sure the cloud tool
registry (aidream `tool_def` / `tool_binding`) is updated in the same change.
The manifest is what the platform advertises; silently changing it strands
cloud-registered tools.

Runs without an engine, network, or credentials.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from app.tools.dispatcher import TOOL_HANDLERS
from app.tools.local_tool_manifest import LOCAL_TOOL_MANIFEST

PROJECT_ROOT = Path(__file__).parent.parent.parent
TOOLS_SRC_DIR = PROJECT_ROOT / "app" / "tools" / "tools"
SNAPSHOT_FILE = Path(__file__).parent / "tool_surface_snapshot.json"

_regenerate_hint = (
    "Regenerate the snapshot (from the repo root) with:\n"
    "  uv run python -c \""
    "import tests.characterization.test_tool_registry_shape as t; t.regenerate()\"\n"
    "then review the diff of tests/characterization/tool_surface_snapshot.json "
    "line by line — every added/removed name must be intentional — and update "
    "the cloud tool registry (aidream tool_def/tool_binding) to match."
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
    manifest_names = sorted(e.name for e in LOCAL_TOOL_MANIFEST)
    manifest_targets = sorted(
        {e.function_path.rsplit(".", 1)[-1] for e in LOCAL_TOOL_MANIFEST}
    )
    source_funcs = _source_tool_functions()
    orphans = sorted(set(source_funcs) - set(handler_funcs))
    return {
        "dispatcher_tool_names": dispatcher_names,
        "manifest_tool_names": manifest_names,
        "source_tool_functions": source_funcs,
        "dispatcher_handler_function_names": handler_funcs,
        "orphan_source_functions": orphans,
        "manifest_function_targets": manifest_targets,
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
        "cloud via LOCAL_TOOL_MANIFEST and to matrx-extend via /extension/rpc "
        "capabilities). If this change is INTENTIONAL, update the snapshot "
        "AND the cloud registry.\n\n" + _regenerate_hint
    )


# ---------------------------------------------------------------------------
# Exact-set pins
# ---------------------------------------------------------------------------


def test_dispatcher_tool_names_exact() -> None:
    expected = _snapshot()["dispatcher_tool_names"]
    actual = sorted(TOOL_HANDLERS.keys())
    assert actual == expected, _diff_message("dispatcher TOOL_HANDLERS names", expected, actual)


def test_manifest_tool_names_exact() -> None:
    expected = _snapshot()["manifest_tool_names"]
    actual = sorted(e.name for e in LOCAL_TOOL_MANIFEST)
    assert actual == expected, _diff_message("LOCAL_TOOL_MANIFEST names", expected, actual)


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
    """Source tool_* functions NOT wired into the dispatcher — 18 known orphans
    (mail, messages, contacts, calendar, photos, location, speech recognition,
    tool_browser_close). A new orphan means someone wrote a tool and forgot to
    register it; a vanished orphan means one was wired up or deleted — both
    must be deliberate."""
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
        "dispatcher_tool_names": 90,
        "manifest_tool_names": 62,
        "source_tool_functions": 108,
        "dispatcher_handler_function_names": 90,
        "orphan_source_functions": 18,
        "manifest_function_targets": 62,
    }, (
        f"TOOL SURFACE COUNTS CHANGED: {counts}. If intentional, update this "
        "test, the snapshot, AND the cloud registry.\n" + _regenerate_hint
    )


def test_every_manifest_tool_targets_a_dispatcher_handler() -> None:
    """Every LOCAL_TOOL_MANIFEST entry's function_path must resolve to a
    function that the dispatcher also wires. The manifest is the cloud-facing
    surface; a manifest entry pointing at an unwired function would advertise
    a tool the engine cannot serve through the dispatcher."""
    handler_funcs = {h.__name__ for h in TOOL_HANDLERS.values()}
    dangling = sorted(
        e.name
        for e in LOCAL_TOOL_MANIFEST
        if e.function_path.rsplit(".", 1)[-1] not in handler_funcs
    )
    assert not dangling, (
        f"MANIFEST TOOLS NOT BACKED BY THE DISPATCHER: {dangling}. Each "
        "function_path must point at a function also present in "
        "TOOL_HANDLERS values. Wire the handler or remove the manifest entry "
        "(and update the cloud registry)."
    )


def test_manifest_targets_exist_in_source() -> None:
    """Every manifest function_path leaf must be a real tool_* source function."""
    source = set(_source_tool_functions())
    missing = sorted(
        e.name
        for e in LOCAL_TOOL_MANIFEST
        if e.function_path.rsplit(".", 1)[-1] not in source
    )
    assert not missing, (
        f"MANIFEST TOOLS POINTING AT NONEXISTENT SOURCE FUNCTIONS: {missing}. "
        "function_path must reference a tool_* function in app/tools/tools/."
    )


def test_dispatcher_names_unique_per_handler() -> None:
    """The dispatcher maps 90 names onto 90 distinct handler functions —
    no two tool names share a handler (current behavior; a deliberate alias
    would change this and must update the snapshot)."""
    assert len(TOOL_HANDLERS) == len({h.__name__ for h in TOOL_HANDLERS.values()}), (
        "Two dispatcher tool names now share one handler function — if an "
        "alias is intentional, update this characterization and the snapshot."
    )


def test_manifest_names_unique() -> None:
    names = [e.name for e in LOCAL_TOOL_MANIFEST]
    assert len(names) == len(set(names)), (
        f"Duplicate LOCAL_TOOL_MANIFEST names: "
        f"{sorted(n for n in set(names) if names.count(n) > 1)}"
    )
