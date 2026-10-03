"""The Claude history importer and capture reconciler moved into ``packages/matrx-coding-history``
(shared with Matrx 2). Two promises keep that move invisible to Matrx Local:

1. every name any engine module, script or test imports from an old ``coding_sessions`` path
   still resolves there — the moved modules are the SAME module objects (aliases), and the two
   split modules re-export what their callers use;
2. the package never imports Matrx Local (``app.``), so Matrx 2 can run it alone.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "matrx-coding-history" / "src" / "matrx_coding_history"
OLD_PATHS = {
    "claude_history",
    "capture_reconciler",
    "identity_client",
    "title_sync",
    "service",
    "models",
    "claude_probe",
    "claude_scope",
    "claude_session_index",
    "continuation",
}
ALIASES = {
    "models": "bridge",
    "claude_probe": "account",
    "claude_scope": "scope",
    "claude_session_index": "session_index",
    "continuation": "continuation",
}


def _imported_names() -> dict[str, set[tuple[str, str]]]:
    """``{old module: {(name, file)}}`` for every ``from app.services.coding_sessions.<m> import``."""
    found: dict[str, set[tuple[str, str]]] = {m: set() for m in OLD_PATHS}
    for top in ("app", "tests", "scripts"):
        for path in (ROOT / top).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                prefix = "app.services.coding_sessions."
                if node.module.startswith(prefix) and node.module[len(prefix) :] in OLD_PATHS:
                    for alias in node.names:
                        if alias.name != "*":
                            found[node.module[len(prefix) :]].add((alias.name, str(path.relative_to(ROOT))))
    return found


def test_every_name_imported_from_an_old_path_still_resolves() -> None:
    missing = []
    for module_name, names in _imported_names().items():
        module = importlib.import_module(f"app.services.coding_sessions.{module_name}")
        for name, where in sorted(names):
            if not hasattr(module, name):
                missing.append(f"{module_name}.{name} (imported by {where})")
    assert not missing, "names that vanished from their old import path:\n" + "\n".join(missing)


@pytest.mark.parametrize(("old", "new"), sorted(ALIASES.items()))
def test_moved_modules_are_the_same_module_object(old: str, new: str) -> None:
    importlib.import_module(f"app.services.coding_sessions.{old}")
    moved = importlib.import_module(f"matrx_coding_history.{new}")
    assert sys.modules[f"app.services.coding_sessions.{old}"] is moved
    # Attribute access through the package resolves to the same object, too.
    package = importlib.import_module("app.services.coding_sessions")
    assert getattr(package, old) is moved


def test_split_modules_hand_back_the_shared_objects() -> None:
    from matrx_coding_history import identity, importer, reconciler

    from app.services.coding_sessions import capture_reconciler, claude_history, identity_client

    assert claude_history._hash_source is importer._hash_source
    assert claude_history.ClaudeHistoryConflict is importer.ClaudeHistoryConflict
    assert issubclass(claude_history.ClaudeHistoryImporter, importer.ClaudeHistoryImporter)
    assert capture_reconciler.CaptureReconcileBlocked is reconciler.CaptureReconcileBlocked
    assert issubclass(capture_reconciler.ClaudeCaptureReconciler, reconciler.ClaudeCaptureReconciler)
    assert identity_client.IdentityInventoryBlocked is identity.IdentityInventoryBlocked


def test_the_package_never_imports_matrx_local() -> None:
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            offenders += [f"{path.name}: {n}" for n in names if n == "app" or n.startswith("app.")]
    assert not offenders, offenders
