"""A missing Playwright browser must be a visible, fixable STATE.

Before this, the only trace of it was a WARNING line: the engine reported a
plain READY, the Scraping page offered a "Browser" method that could only fail,
and nothing told the user what to install. These pin the three properties that
must not regress:

  1. The state is reported with a privacy-safe reason.
  2. It becomes a one-click ActionNeeded — not an error, not an Arman task.
  3. It disappears completely once a browser is present (no phantom prompt).

Hard Rule 9 is part of the contract: the path is derived from MATRX_HOME_DIR /
PLAYWRIGHT_BROWSERS_PATH, so a dev engine never points at the installed app's
``~/.matrx``.
"""

from __future__ import annotations

import asyncio
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


def test_missing_browser_is_reported_with_a_safe_reason(runtime):
    status = runtime.status()

    assert status.available is False
    assert status.code == "browser_not_installed"
    assert status.reason == "No built-in browser is installed yet."
    assert str(runtime.browsers_path()) not in status.to_dict().values()


def test_missing_browser_becomes_a_one_click_action(runtime):
    item = runtime.browser_action_needed()

    assert item is not None
    assert item.kind.value == "capability_install"
    assert item.action.kind == "install_browser_engine"
    # Plain language: no "Playwright", no "Chromium binary", no shell command.
    assert "playwright" not in item.message.lower()
    assert "install" in item.action.label.lower()


def test_terminal_install_failure_has_a_safe_persisted_code(runtime):
    runtime.record_install_failure()

    status = runtime.status()
    assert status.available is False
    assert status.code == "browser_install_failed"
    assert status.reason == "The built-in browser download did not finish. Try again."
    assert str(runtime.browsers_path()) not in status.to_dict().values()
    item = runtime.browser_action_needed()
    assert item is not None
    assert item.action.label == "Try again"


def test_terminal_package_install_failure_is_not_demoted_to_missing(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "playwright_package_present", lambda: False)
    runtime.record_install_failure()

    assert runtime.status().code == "browser_install_failed"


def test_verified_live_pool_clears_a_stale_installer_failure(runtime, monkeypatch):
    runtime.record_install_failure()
    monkeypatch.setattr(runtime, "playwright_package_present", lambda: True)
    monkeypatch.setattr(runtime, "browser_binary_present", lambda: True)

    runtime.record_pool_started()

    status = runtime.status()
    assert status.available is True
    assert status.code == "ready"


def test_background_installer_shutdown_terminates_child_and_cancels_stuck_owner(runtime):
    class FakeProcess:
        returncode = None
        terminated = False
        killed = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def scenario():
        process = FakeProcess()
        never = asyncio.Event()
        signals = 0

        async def signal_tree(_process):
            nonlocal signals
            signals += 1
            if signals == 2:
                _process.returncode = -9

        async def stuck_installer():
            await never.wait()

        task = asyncio.create_task(stuck_installer())
        stopped = await runtime.stop_background_install(
            task,
            process,
            timeout=0.01,
            signal_tree=signal_tree,
        )

        assert stopped is True
        assert signals == 2
        assert task.cancelled()

    asyncio.run(scenario())


def test_background_installer_owner_prevents_spawn_after_shutdown_starts(runtime):
    async def scenario():
        owner = runtime.BackgroundInstallOwner()
        ready = asyncio.Event()
        allow_spawn = asyncio.Event()
        create_calls = 0

        async def create(*_args, **_kwargs):
            nonlocal create_calls
            create_calls += 1
            raise AssertionError("shutdown must fence a late installer spawn")

        async def installer():
            ready.set()
            await allow_spawn.wait()
            await owner.spawn("installer", create=create)

        owner.start(installer())
        await ready.wait()
        stop_task = asyncio.create_task(owner.stop(timeout=0.1))
        await asyncio.sleep(0)
        allow_spawn.set()

        assert await stop_task is True
        assert create_calls == 0

    asyncio.run(scenario())


def test_launch_failure_state_does_not_reflect_raw_diagnostics(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "browser_binary_present", lambda: True)
    runtime.record_launch_failure(RuntimeError("Executable doesn't exist"))

    status = runtime.status()
    assert status.available is False
    assert status.code == "browser_launch_failed"
    assert status.reason == "The built-in browser did not start. Repair it and restart the app if needed."
    assert runtime.browser_action_needed() is not None


def test_service_record_says_degraded_with_the_reason_never_failed(runtime):
    """A missing browser is reduced functionality, not a dead scraper."""
    from app.launcher import ServiceState, get_registry

    runtime.sync_service_registry()
    record = get_registry().snapshot()["services"]["scraper"]

    assert record["state"] == ServiceState.DEGRADED.value
    assert "browser rendering unavailable" in record["error"]
    assert record["metadata"]["browser_available"] is False


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


def test_a_retried_launch_reports_starting_and_asks_for_nothing(runtime, monkeypatch):
    """One starved boot attempt must not accuse a healthy browser.

    The launch bound is wall clock, and the engine's own startup can eat it:
    on 2026-09-13 it expired 47s into Phase 3, inside the minute-long Claude
    session-index scan, and every boot told the user the browser "would not
    start" while it launched in 3.7s from a terminal. While
    the one scheduled retry is pending, the honest state is "still starting" —
    and a transient state is never an ask.
    """
    monkeypatch.setattr(runtime, "browser_binary_present", lambda: True)
    runtime.record_launch_failure(
        RuntimeError("Chromium did not finish launching within 30s")
    )
    runtime.record_retry_pending()

    status = runtime.status()
    assert status.available is False
    assert status.code == "browser_starting"
    assert status.reason == "The built-in browser is still starting"
    assert status.pool_restart_pending is True
    assert "would not start" not in (status.reason or "")
    assert runtime.browser_action_needed() is None


def test_the_ask_returns_once_the_retry_has_also_failed(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "browser_binary_present", lambda: True)
    runtime.record_launch_failure(RuntimeError("first attempt"))
    runtime.record_retry_pending()
    assert runtime.browser_action_needed() is None

    # The retry ran and failed: the failure is final, and now it IS an ask.
    runtime.record_launch_failure(RuntimeError("Chromium crashed on launch"))

    status = runtime.status()
    assert status.code == "browser_launch_failed"
    assert status.reason == "The built-in browser did not start. Repair it and restart the app if needed."
    item = runtime.browser_action_needed()
    assert item is not None
    assert item.action.label == "Repair browser"


def test_a_retry_that_succeeds_clears_the_state_completely(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "browser_binary_present", lambda: True)
    runtime.record_launch_failure(RuntimeError("first attempt"))
    runtime.record_retry_pending()

    runtime.record_pool_started()

    assert runtime.retry_pending() is False
    assert runtime.pool_is_live() is True
    assert runtime.status().code == "ready"
    assert runtime.browser_action_needed() is None


def test_a_half_downloaded_browser_directory_is_not_an_installed_browser(
    runtime, tmp_path, monkeypatch
):
    """The versioned directory appears when the download STARTS; only Playwright's
    INSTALLATION_COMPLETE marker proves the executable is there. Without this,
    first boot said "starting" about an empty folder and would never re-download.

    The pin is stated explicitly so this test keeps testing the MARKER rule:
    presence is also judged against the build this engine resolves
    (test_browser_build_mismatch.py owns that rule), which would otherwise make
    the outcome depend on whatever revision the local Playwright happens to pin.
    """
    monkeypatch.setattr(runtime, "expected_browser_revision", lambda *_a, **_k: 1234)
    browsers = tmp_path / "playwright-browsers"
    partial = browsers / "chromium_headless_shell-1234"
    partial.mkdir(parents=True)
    assert runtime.browser_binary_present() is False
    assert runtime.status().code == "browser_not_installed"

    (partial / runtime.INSTALL_COMPLETE_MARKER).write_text("")
    assert runtime.browser_binary_present() is True
    assert runtime.status().code != "browser_not_installed"
