"""Why nothing was captured from Cursor — the host answer, not silence.

Lane CS-35, Cursor half. Everything AI Matrx knows about a Cursor session is
produced by ``matrx-cursor-plugin``'s ``hooks/emit.py`` running inside a Cursor
command hook, and that plugin's own contract makes every failure silent: the
emitter writes no stdout, no stderr, and a log that contains only an exception
class. So a host that never dispatched the hook produced nothing and SAID
nothing — the exact defect the Codex half was fixed for.

The states these guards pin, each read from a real isolated Cursor home laid
out the way this Mac's own is (measured 2026-09-21, Cursor Agent CLI
``2026.09.18-9a7762b``, Cursor desktop 3.x):

* no install trace anywhere — ``~/.cursor/plugins/cache/<marketplace>/<plugin>/
  <ref>/.cursor-plugin/plugin.json`` and ``~/.cursor/plugins/local/*`` are the
  two real install shapes, and THIS Mac has neither for matrx-cursor-plugin.
* installed, but the plugin's own storage under ``~/.matrx`` does not exist:
  the hook has not run once, because the emitter creates that storage on its
  first run.
* installed, storage present, but no run receipt: a turn has not happened since
  a release that records one. Honestly unknown, and it blocks nothing.
* a run receipt — the only positive proof the host really does dispatch.

Unlike Codex, Cursor exposes NO readable hook enable flag and NO hook trust
record: there is no ``features``-style switch in ``~/.cursor/cli-config.json``,
no ``~/.cursor/hooks.json``, and no trust key in Cursor desktop's
``state.vscdb`` (searched 2026-09-21). So "the host will dispatch" can only be
proven by a hook that DID run, which is why the receipt exists at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.coding_sessions.cursor_hooks import (
    CODE_NEVER_RAN,
    CODE_NOT_INSTALLED,
    CODE_NO_TURN_YET,
    CODE_READY,
    RECEIPT_NAME,
    hook_dispatch_state,
)
from app.services.coding_sessions.provider_readiness import ProviderReadinessFacade

VERSION = "0.2.0-alpha.4"


def _install(cursor_home: Path, *, version: str = VERSION, local: bool = False) -> Path:
    """The two real Cursor install shapes, as measured on this Mac."""
    root = (
        cursor_home / "plugins/local/matrx-cursor-plugin"
        if local
        else cursor_home / "plugins/cache/ai-matrx/matrx-cursor-plugin/abc123"
    )
    (root / ".cursor-plugin").mkdir(parents=True)
    (root / ".cursor-plugin/plugin.json").write_text(
        json.dumps({"name": "matrx-cursor-plugin", "version": version})
    )
    return root


def _storage(matrx_home: Path) -> Path:
    """What the emitter creates on its first run, before any delivery."""
    root = matrx_home / "plugins/matrx-cursor-plugin/coding-session-bridge"
    (root / "pending").mkdir(parents=True)
    return root


def _receipt(matrx_home: Path, **overrides: Any) -> Path:
    root = _storage(matrx_home)
    value = {
        "schema_version": 1,
        "hook": "SessionStart",
        "at": 1789996815.5,
        "plugin_version": VERSION,
    }
    value.update(overrides)
    path = root / RECEIPT_NAME
    path.write_text(json.dumps(value))
    return path


# ---------------------------------------------------------------------------
# The four states
# ---------------------------------------------------------------------------


def test_no_install_trace_is_named_as_not_installed(tmp_path: Path) -> None:
    record = hook_dispatch_state(tmp_path / ".cursor", [tmp_path / ".matrx"])

    assert record["code"] == CODE_NOT_INSTALLED
    assert record["dispatches"] is False
    assert record["plugin_versions"] == []
    assert "Cursor" in record["message"]
    assert record["remedy"]


def test_installed_with_no_storage_is_named_as_never_ran(tmp_path: Path) -> None:
    _install(tmp_path / ".cursor")

    record = hook_dispatch_state(tmp_path / ".cursor", [tmp_path / ".matrx"])

    assert record["code"] == CODE_NEVER_RAN
    assert record["dispatches"] is False
    assert record["plugin_versions"] == [VERSION]
    # The remedy must name the ACTION, never "install the plugin" again.
    assert "install" not in record["remedy"].lower()
    assert record["remedy"]


def test_storage_without_a_receipt_is_unknown_not_a_blocker(tmp_path: Path) -> None:
    _install(tmp_path / ".cursor", local=True)
    _storage(tmp_path / ".matrx")

    record = hook_dispatch_state(tmp_path / ".cursor", [tmp_path / ".matrx"])

    assert record["code"] == CODE_NO_TURN_YET
    assert record["dispatches"] is None
    assert record["ran_at"] is None
    assert record["remedy"]


def test_a_run_receipt_is_the_one_proof_of_dispatch(tmp_path: Path) -> None:
    _install(tmp_path / ".cursor")
    _receipt(tmp_path / ".matrx")

    record = hook_dispatch_state(tmp_path / ".cursor", [tmp_path / ".matrx"])

    assert record["code"] == CODE_READY
    assert record["dispatches"] is True
    assert record["ran_at"] == "2026-09-21T13:20:15Z"
    assert record["ran_hook"] == "SessionStart"
    assert record["remedy"] == ""


def test_a_receipt_without_an_install_trace_still_proves_dispatch(
    tmp_path: Path,
) -> None:
    """``cursor-agent --plugin-dir`` leaves no install trace at all.

    The plugin is not published to a marketplace yet, so the development
    loading path is how it actually runs today. Reporting "not installed" for a
    host whose hook demonstrably ran would be a confident wrong answer.
    """
    _receipt(tmp_path / ".matrx")

    record = hook_dispatch_state(tmp_path / ".cursor", [tmp_path / ".matrx"])

    assert record["code"] == CODE_READY
    assert record["dispatches"] is True


def test_an_unreadable_receipt_never_crashes_and_never_guesses(
    tmp_path: Path,
) -> None:
    _install(tmp_path / ".cursor")
    root = _storage(tmp_path / ".matrx")
    (root / RECEIPT_NAME).write_text("{not json")

    record = hook_dispatch_state(tmp_path / ".cursor", [tmp_path / ".matrx"])

    assert record["code"] == CODE_NO_TURN_YET
    assert record["dispatches"] is None


def test_the_legacy_home_storage_root_is_read_too(tmp_path: Path) -> None:
    """The emitter hardcodes ``~/.matrx`` unless MATRX_CURSOR_PLUGIN_DATA is set.

    A host with a configured MATRX_HOME_DIR elsewhere still has its receipts
    written under the real home, so both roots are read.
    """
    _install(tmp_path / ".cursor")
    _receipt(tmp_path / "home/.matrx")

    record = hook_dispatch_state(
        tmp_path / ".cursor",
        [tmp_path / "configured-matrx", tmp_path / "home/.matrx"],
    )

    assert record["dispatches"] is True


# ---------------------------------------------------------------------------
# Surfaced exactly the way codex's capture/dispatch is
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_readiness_surfaces_the_cursor_dispatch_answer(tmp_path: Path) -> None:
    home = tmp_path / "home"
    matrx_home = home / ".matrx"
    _install(home / ".cursor")

    facade = ProviderReadinessFacade(
        home=home,
        matrx_home=matrx_home,
        applications=tmp_path / "Applications",
        process_probe=lambda: set(),
        which_probe=lambda name: None,
        version_probe=_none,
    )
    status = await facade.status()
    capture = status["providers"]["cursor"]["capture"]

    assert capture is not None
    assert capture["state"] == "blocked"
    assert capture["blocker"]["code"] == CODE_NEVER_RAN
    assert capture["blocker"]["message"]
    assert capture["blocker"]["remedy"]
    assert capture["dispatch"]["code"] == CODE_NEVER_RAN
    # The readiness payload carries no local filesystem path, ever.
    rendered = json.dumps(status)
    assert str(home) not in rendered
    assert ".matrx" not in rendered


@pytest.mark.anyio
async def test_readiness_reports_a_running_cursor_capture_as_ok(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    matrx_home = home / ".matrx"
    _install(home / ".cursor")
    _receipt(matrx_home)

    facade = ProviderReadinessFacade(
        home=home,
        matrx_home=matrx_home,
        applications=tmp_path / "Applications",
        process_probe=lambda: set(),
        which_probe=lambda name: None,
        version_probe=_none,
    )
    status = await facade.status()
    capture = status["providers"]["cursor"]["capture"]

    assert capture["state"] == "ok"
    assert capture["blocker"] is None
    assert capture["dispatch"]["dispatches"] is True


async def _none(executable: str) -> str | None:
    return None
