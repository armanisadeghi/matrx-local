"""The stale cross-launch API-key verdict blob is removed on upgrade."""

from __future__ import annotations

import json
import sqlite3

from app.services.local_db.schema import _V17_DROP_STALE_API_KEY_VALIDATION


def test_migration_drops_only_historical_validation_state() -> None:
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, settings TEXT, updated_at TEXT)"
    )
    original = {
        "api_keys": {"civitai": "secret-stays"},
        "api_key_validation": {
            "civitai": {"verdict": "invalid", "checked_at": "yesterday"}
        },
        "theme": "dark",
    }
    db.execute(
        "INSERT INTO app_settings (key, settings, updated_at) VALUES (?, ?, ?)",
        ("settings", json.dumps(original), "before"),
    )

    db.execute(_V17_DROP_STALE_API_KEY_VALIDATION)

    stored = json.loads(
        db.execute(
            "SELECT settings FROM app_settings WHERE key = 'settings'"
        ).fetchone()[0]
    )
    assert "api_key_validation" not in stored
    assert stored["api_keys"] == original["api_keys"]
    assert stored["theme"] == "dark"
