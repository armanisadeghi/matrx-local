"""
Wake word API smoke tests.

Tests the Python FastAPI wake word endpoints (/wake-word/*) to verify:
  - Status endpoint returns expected shape
  - Model list includes pre-trained models
  - Start/stop lifecycle works (no actual audio device needed — tests handle graceful errors)
  - Settings GET/PUT round-trips correctly

These require the engine to be running (session-scoped fixture).
"""

from __future__ import annotations

import httpx
import pytest


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_wake_word_status(http: httpx.Client):
    """GET /wake-word/status returns the expected shape."""
    r = http.get("/wake-word/status")
    assert r.status_code == 200
    data = r.json()
    assert "running" in data
    assert "mode" in data
    assert isinstance(data["running"], bool)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_wake_word_models_list(http: httpx.Client):
    """GET /wake-word/models lists available wake-word models."""
    r = http.get("/wake-word/models")
    assert r.status_code == 200
    data = r.json()
    assert "models" in data, f"expected a 'models' key, got {list(data)}"
    assert isinstance(data["models"], list) and data["models"]
    # Each entry carries a filename + description; a hey_jarvis model ships
    # built-in (closest phonetics to "Hey Matrix").
    blob = " ".join(
        f"{m.get('filename', '')} {m.get('description', '')}".lower()
        for m in data["models"]
    )
    assert "jarvis" in blob, "expected a hey_jarvis model in the list"


# ---------------------------------------------------------------------------
# Settings round-trip
# ---------------------------------------------------------------------------


def test_wake_word_settings_get(http: httpx.Client):
    """GET /settings/wake-word returns settings with expected fields."""
    r = http.get("/settings/wake-word")
    assert r.status_code == 200
    data = r.json()
    assert "engine" in data
    assert data["engine"] in ("whisper", "oww")
    assert "oww_model" in data
    assert "oww_threshold" in data
    assert "custom_keyword" in data


def test_wake_word_settings_roundtrip(http: httpx.Client):
    """PUT /settings/wake-word persists and GET reads back the same values."""
    original = http.get("/settings/wake-word").json()

    updated = {**original, "custom_keyword": "hey test"}
    r = http.put("/settings/wake-word", json=updated)
    assert r.status_code == 200

    readback = http.get("/settings/wake-word").json()
    assert readback["custom_keyword"] == "hey test"

    http.put("/settings/wake-word", json=original)


# ---------------------------------------------------------------------------
# Start without audio device (graceful failure expected)
# ---------------------------------------------------------------------------


def test_wake_word_start_without_device(http: httpx.Client):
    """POST /wake-word/start should succeed or fail gracefully without a real mic."""
    r = http.post("/wake-word/start", json={})
    # Accept 200 (started) or 500 (no audio device in CI/test environment)
    assert r.status_code in (200, 500), f"Unexpected status: {r.status_code}"
    if r.status_code == 200:
        http.post("/wake-word/stop")


def test_wake_word_stop_when_not_running(http: httpx.Client):
    """POST /wake-word/stop is safe to call when not running."""
    r = http.post("/wake-word/stop")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Configure
# ---------------------------------------------------------------------------


def test_wake_word_configure(http: httpx.Client):
    """POST /wake-word/configure accepts model_name and threshold updates."""
    r = http.post(
        "/wake-word/configure",
        json={"threshold": 0.7},
    )
    assert r.status_code == 200
