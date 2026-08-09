"""A missing Playwright browser must be a visible, fixable STATE.

Before this, the only trace of it was a WARNING line: the engine reported a
plain READY, the Scraping page offered a "Browser" method that could only fail,
and nothing told the user what to install. These pin the three properties that
must not regress:

  1. The state is reported, with a reason, and it names THIS world's path.
  2. It becomes a one-click ActionNeeded — not an error, not an Arman task.
  3. It disappears completely once a browser is present (no phantom prompt).

Hard Rule 9 is part of the contract: the path is derived from MATRX_HOME_DIR /
PLAYWRIGHT_BROWSERS_PATH, so a dev engine never points at the installed app's
``~/.matrx``.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A fresh browser_runtime bound to an empty, isolated browsers path."""
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "playwright-browsers"))
    module = importlib.import_module("app.services.scraper.browser_runtime")
    importlib.reload(module)
    return module


def test_path_follows_the_world_not_a_hardcoded_home(tmp_path, monkeypatch):
    """A dev/--fresh home must never resolve to the installed app's ~/.matrx."""
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("MATRX_HOME_DIR", str(tmp_path / ".matrx-dev"))

    import app.config

    importlib.reload(app.config)
    module = importlib.import_module("app.services.scraper.browser_runtime")
    importlib.reload(module)

    assert module.browsers_path() == tmp_path / ".matrx-dev" / "playwright-browsers"


def test_missing_browser_is_reported_with_a_reason(runtime):
    status = runtime.status()

    assert status.available is False
    assert status.code == "browser_not_installed"
    assert status.reason and str(runtime.browsers_path()) in status.reason


def test_missing_browser_becomes_a_one_click_action(runtime):
    item = runtime.browser_action_needed()

    assert item is not None
    assert item.kind.value == "capability_install"
    assert item.action.kind == "install_browser_engine"
    # Plain language: no "Playwright", no "Chromium binary", no shell command.
    assert "playwright" not in item.message.lower()
    assert "install" in item.action.label.lower()


def test_launch_failure_reason_reaches_the_user_facing_state(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "browser_binary_present", lambda: True)
    runtime.record_launch_failure(RuntimeError("Executable doesn't exist"))

    status = runtime.status()
    assert status.available is False
    assert status.code == "browser_launch_failed"
    assert "Executable doesn't exist" in (status.reason or "")
    assert runtime.browser_action_needed() is not None


def test_service_record_says_degraded_with_the_reason_never_failed(runtime):
    """A missing browser is reduced functionality, not a dead scraper."""
    from app.launcher import ServiceState, get_registry

    runtime.sync_service_registry()
    record = get_registry().snapshot()["services"]["scraper"]

    assert record["state"] == ServiceState.DEGRADED.value
    assert "browser rendering unavailable" in record["error"]
    assert record["metadata"]["browser_available"] is False
    assert record["metadata"]["browsers_path"] == str(runtime.browsers_path())


def test_service_record_returns_to_ready_once_the_browser_is_there(runtime, monkeypatch):
    from app.launcher import ServiceState, get_registry

    runtime.sync_service_registry()
    monkeypatch.setattr(runtime, "browser_binary_present", lambda: True)
    runtime.record_pool_started()
    runtime.sync_service_registry()

    record = get_registry().snapshot()["services"]["scraper"]
    assert record["state"] == ServiceState.READY.value
    assert record["error"] is None
    assert record["metadata"]["browser_available"] is True


def test_present_browser_raises_no_prompt_at_all(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "browser_binary_present", lambda: True)
    runtime.record_pool_started()

    status = runtime.status()
    assert status.available is True
    assert status.code == "ready"
    assert runtime.browser_action_needed() is None
