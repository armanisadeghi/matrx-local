"""Path-manager provenance: a fallback from an unusable override is LOUD.

Historically safe_dir() silently fell back to the default while all_paths()
kept reporting the custom path as active — the UI showed a location that was
not in use. Now the effective path + provenance are recorded and an
access-health observation is filed for the failed override.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# app.config and app.common form an import cycle that only resolves when
# app.common loads first (the engine's own import order). Tests importing
# app.config-dependent modules directly must do the same.
import app.common  # noqa: F401  — must precede app.config-dependent imports

from app.services.paths import manager


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores POSIX permissions")
def test_safe_dir_fallback_is_recorded_and_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = tmp_path / "default"
    read_only_parent = tmp_path / "ro"
    read_only_parent.mkdir()
    read_only_parent.chmod(0o500)
    override = read_only_parent / "custom"

    monkeypatch.setitem(
        manager._PATH_CATALOG, "testpath", (default, "Test path", True)
    )
    monkeypatch.setattr(manager, "_stored_paths", lambda: {"testpath": str(override)})

    try:
        resolved = manager.safe_dir("testpath")
        assert resolved == default, "unusable override must fall back to default"
        assert manager._effective["testpath"] == (str(default), "fallback")
        assert manager.path_provenance("testpath").value == "fallback"

        from app.services.access_health import get_access_health

        health = get_access_health().health("path:testpath")
        assert health is not None, "override failure must be filed as evidence"
        assert health["status"] == "degraded"
        assert health["kind"] == "permission"
        assert str(override) in (health["last_failure"] or {}).get("path", "")
    finally:
        read_only_parent.chmod(0o700)
        from app.services.access_health import get_access_health

        get_access_health().unregister("path:testpath")


def test_safe_dir_default_and_override_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = tmp_path / "default"
    monkeypatch.setitem(
        manager._PATH_CATALOG, "testpath", (default, "Test path", True)
    )

    monkeypatch.setattr(manager, "_stored_paths", lambda: {})
    assert manager.safe_dir("testpath") == default
    assert manager._effective["testpath"][1] == "default"

    custom = tmp_path / "custom"
    monkeypatch.setattr(manager, "_stored_paths", lambda: {"testpath": str(custom)})
    assert manager.safe_dir("testpath") == custom
    assert manager._effective["testpath"][1] == "override"


def test_all_paths_reports_effective_and_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = tmp_path / "default"
    monkeypatch.setattr(
        manager,
        "_PATH_CATALOG",
        {"testpath": (default, "Test path", True)},
    )
    monkeypatch.setattr(manager, "_stored_paths", lambda: {})
    entries = manager.all_paths()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["effective"] == str(default)
    assert entry["provenance"] == "default"
    assert entry["in_use_matches_config"] is True
