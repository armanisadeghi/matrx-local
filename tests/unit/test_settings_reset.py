from __future__ import annotations

from app.services.cloud_sync import settings_sync as module


def test_scoped_settings_reset_previews_then_backs_up(monkeypatch, tmp_path) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(module, "LOCAL_SETTINGS_FILE", settings_file)
    sync = module.SettingsSync()
    sync.set_many({"theme": "light", "proxy_enabled": False})

    preview = sync.preview_reset("application")
    assert "theme" in preview["changes"]
    assert "proxy_enabled" not in preview["keys"]
    assert sync.get("theme") == "light"

    result = sync.apply_reset("application", confirmed=True)
    assert sync.get("theme") == module.DEFAULT_SETTINGS["theme"]
    assert sync.get("proxy_enabled") is False
    assert result["backup_path"] is not None


def test_settings_reset_requires_confirmation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(module, "LOCAL_SETTINGS_FILE", tmp_path / "settings.json")
    sync = module.SettingsSync()

    try:
        sync.apply_reset("all", confirmed=False)
    except ValueError as exc:
        assert "confirmation" in str(exc)
    else:
        raise AssertionError("reset unexpectedly applied without confirmation")
