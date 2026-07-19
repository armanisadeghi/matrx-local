"""Regression coverage for optional capability import isolation."""

from __future__ import annotations

from pathlib import Path

import pytest


def _complete(path: Path) -> None:
    path.mkdir(parents=True)
    (path / ".install-complete").write_text("{}", encoding="utf-8")


def test_managed_capability_path_is_fallback_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.capabilities import installer

    managed = tmp_path / "ner-packages"
    _complete(managed)
    monkeypatch.setattr(
        installer, "get_capability_packages_dir", lambda _cap_id: managed
    )
    monkeypatch.setattr(installer.sys, "path", ["/frozen-engine"])

    assert installer.inject_capability_path("ner") is True
    assert installer.sys.path == ["/frozen-engine", str(managed)]


def test_lightweight_capability_path_is_fallback_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.capabilities import installer

    lightweight = tmp_path / "capability-ocr"
    _complete(lightweight)
    monkeypatch.setattr(
        installer,
        "get_lightweight_capability_packages_dir",
        lambda _cap_id: lightweight,
    )
    monkeypatch.setattr(installer.sys, "path", ["/frozen-engine"])

    assert installer.inject_lightweight_capability_path("ocr") is True
    assert installer.sys.path == ["/frozen-engine", str(lightweight)]


def test_startup_restores_managed_and_lightweight_capability_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.capabilities import installer

    managed = tmp_path / "ner-packages"
    lightweight = tmp_path / "capability-ocr"
    _complete(managed)
    _complete(lightweight)
    monkeypatch.setattr(installer, "CAPABILITY_INSTALL", {"ner": {}})
    monkeypatch.setattr(installer, "LIGHTWEIGHT_CAPABILITY_IDS", ("ocr",))
    monkeypatch.setattr(
        installer, "get_capability_packages_dir", lambda _cap_id: managed
    )
    monkeypatch.setattr(
        installer,
        "get_lightweight_capability_packages_dir",
        lambda _cap_id: lightweight,
    )
    monkeypatch.setattr(installer.sys, "path", ["/frozen-engine"])

    assert installer.inject_all_capability_paths() == ["ner", "ocr"]
    assert installer.sys.path == [
        "/frozen-engine",
        str(managed),
        str(lightweight),
    ]


def test_every_non_managed_api_capability_has_a_startup_target() -> None:
    from app.api.capabilities_routes import CAPABILITY_SPECS
    from app.services.capabilities.installer import (
        LIGHTWEIGHT_CAPABILITY_IDS,
        uses_managed_installer,
    )

    lightweight_specs = {
        cap_id
        for cap_id in CAPABILITY_SPECS
        if not uses_managed_installer(cap_id)
    }
    assert lightweight_specs == set(LIGHTWEIGHT_CAPABILITY_IDS)


def test_capability_injectors_never_prepend_optional_targets() -> None:
    root = Path(__file__).resolve().parents[2]
    service = (root / "app/services/capabilities/installer.py").read_text(
        encoding="utf-8"
    )
    routes = (root / "app/api/capabilities_routes.py").read_text(encoding="utf-8")

    assert "sys.path.insert(0" not in service
    assert "sys.path.insert(0" not in routes
