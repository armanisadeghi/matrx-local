"""A browser build the engine cannot launch is not an installed browser.

Live condition this pins (audit row SR-03, 2026-09-14): the running engine's
Playwright resolved ``chromium_headless_shell-1208`` while the only build in
``~/.matrx/playwright-browsers`` was ``-1234``. Every browser-rendered scrape
failed for 18.6+ hours with ``browser_launch_failed``; nothing re-resolved the
path while the engine lived, nothing told the user, and the one-click "Repair
browser" deliberately SKIPPED the download because a chromium directory was
present — so the one action that could have fixed it was the one action the
code refused to take.

These assertions fail on that behaviour:
  1. presence is judged against the build THIS engine resolves, not against
     "some chromium directory exists";
  2. the state says which build is needed, which one is there, and that no
     restart is required;
  3. it becomes a plain-language one-click ask;
  4. an unreadable pin never turns a working install into a broken one.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "playwright-browsers"))
    module = importlib.import_module("app.services.scraper.browser_runtime")
    importlib.reload(module)
    return module


def install_build(runtime, name: str, revision: int, complete: bool = True) -> None:
    directory = runtime.browsers_path() / f"{name}-{revision}"
    # Playwright's real on-disk shape (macOS arm64, 2026-09): an arch-suffixed
    # platform directory holding the binary.
    platform_dir = directory / "chrome-headless-shell-mac-arm64"
    platform_dir.mkdir(parents=True, exist_ok=True)
    (platform_dir / "chrome-headless-shell").write_text("#!/bin/sh\n")
    if complete:
        (directory / runtime.INSTALL_COMPLETE_MARKER).write_text("")


def test_builds_are_scanned_newest_first_and_half_downloads_are_ignored(runtime):
    install_build(runtime, "chromium_headless_shell", 1208)
    install_build(runtime, "chromium_headless_shell", 1234)
    install_build(runtime, "chromium_headless_shell", 1300, complete=False)

    builds = runtime.installed_browser_builds()

    assert [build.revision for build in builds] == [1234, 1208]
    assert runtime.installed_browser_builds(include_incomplete=True)[0].revision == 1300


def test_the_newest_build_on_disk_is_resolvable_by_path(runtime):
    install_build(runtime, "chromium_headless_shell", 1234)
    executable = runtime.resolve_browser_executable()

    assert executable is not None
    assert executable.exists()
    assert "1234" in str(executable)


def test_a_build_this_engine_cannot_launch_is_not_present(runtime, monkeypatch):
    """THE GUARD. 1234 on disk + 1208 pinned = nothing launchable."""
    monkeypatch.setattr(runtime, "expected_browser_revision", lambda *_a, **_k: 1208)
    install_build(runtime, "chromium_headless_shell", 1234)

    report = runtime.browser_install_report()
    assert report.mismatch is True
    assert report.expected_present is False
    assert runtime.browser_binary_present() is False, (
        "a complete install of the wrong revision is unlaunchable — calling it "
        "present is what made the one-click repair skip the download"
    )


def test_the_mismatch_names_both_builds_and_promises_no_restart(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "expected_browser_revision", lambda *_a, **_k: 1208)
    install_build(runtime, "chromium_headless_shell", 1234)

    status = runtime.status()

    assert status.available is False
    assert status.code == "browser_build_mismatch"
    assert status.reason is not None
    assert "1208" in status.reason and "1234" in status.reason
    assert "restart" in status.reason.lower()


def test_the_mismatch_is_a_one_click_ask_in_plain_language(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "expected_browser_revision", lambda *_a, **_k: 1208)
    install_build(runtime, "chromium_headless_shell", 1234)

    item = runtime.browser_action_needed()

    assert item is not None
    assert item.code == "browser_build_mismatch"
    assert item.action.kind == "install_browser_engine"
    # The person reading this is not a developer.
    for jargon in ("playwright", "chromium", "revision", "1208"):
        assert jargon not in item.message.lower()


def test_the_matching_build_is_present_and_asks_for_nothing(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "expected_browser_revision", lambda *_a, **_k: 1208)
    install_build(runtime, "chromium_headless_shell", 1208)

    assert runtime.browser_binary_present() is True
    assert runtime.browser_install_report().mismatch is False
    assert runtime.browser_action_needed() is None


def test_an_unreadable_pin_never_breaks_a_working_install(runtime, monkeypatch):
    """No pin to compare against → fall back to the old rule, not to broken."""
    monkeypatch.setattr(runtime, "expected_browser_revision", lambda *_a, **_k: None)
    install_build(runtime, "chromium_headless_shell", 1234)

    assert runtime.browser_binary_present() is True
    assert runtime.browser_install_report().mismatch is False


def test_the_automatic_repair_runs_once_and_brings_the_pool_up(runtime, monkeypatch):
    """Self-heal in the RUNNING engine: install the needed build, start the pool."""
    monkeypatch.setattr(runtime, "expected_browser_revision", lambda *_a, **_k: 1208)
    install_build(runtime, "chromium_headless_shell", 1234)

    installs: list[str] = []

    async def fake_install(path, browser="chromium"):
        installs.append(browser)
        install_build(runtime, "chromium_headless_shell", 1208)
        yield 'event: progress\ndata: {"percent": 100, "message": "done", "status": "installing"}\n\n'

    pool_starts: list[bool] = []

    class FakeEngine:
        async def ensure_browser_pool(self, timeout=None):
            pool_starts.append(True)
            return True

    import sys
    import types

    setup_stub = types.ModuleType("app.api.setup_routes")
    setup_stub._install_playwright_browsers = fake_install  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.api.setup_routes", setup_stub)
    monkeypatch.setattr(
        "app.services.scraper.engine.get_scraper_engine", lambda: FakeEngine()
    )
    monkeypatch.setattr(runtime, "sync_service_registry", lambda: runtime.status())

    async def no_publish() -> None:
        return None

    monkeypatch.setattr(runtime, "publish_action_needed", no_publish)

    import asyncio

    assert asyncio.run(runtime.self_heal_browser_build()) is True
    assert installs == [runtime.INSTALL_BROWSER]
    assert pool_starts == [True], "the pool must come up without an app restart"
    assert runtime.browser_binary_present() is True

    # Bounded: a second call never downloads again.
    assert asyncio.run(runtime.self_heal_browser_build()) is True
    assert installs == [runtime.INSTALL_BROWSER]
