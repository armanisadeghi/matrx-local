"""Home-connection (residential egress) supervisor lifecycle.

These exercise the real supervisor against a real child process — a tiny
stand-in that honours the same CLI the Rust helper does (``run --token-stdin
--status-file``, writes the status JSON, exits on stdin EOF). No mock stands in
for the subprocess, because every bug this file is guarding against lives in
the subprocess boundary: does the token reach stdin and nowhere else, does the
identity get persisted for preflight, does stop() actually reap the child, and
is the missing-binary state honest instead of silent.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

import psutil
import pytest

from app.services.residential_egress import supervisor as module

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


STAND_IN = textwrap.dedent(
    '''
    """Stand-in for the Rust matrx-egress helper: same CLI, same status file."""
    import json, sys, time, pathlib

    argv = sys.argv[1:]
    if argv and argv[0] == "--version":
        print("matrx-egress 0.0.0-standin")
        raise SystemExit(0)

    status_file = pathlib.Path(argv[argv.index("--status-file") + 1])
    name = argv[argv.index("--name") + 1] if "--name" in argv else ""

    token = sys.stdin.readline().strip()
    if not token:
        print(json.dumps({"state": "error", "last_error": "no token on stdin"}), flush=True)
        raise SystemExit(2)

    # Prove the token arrived without ever writing it anywhere it could rest.
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(json.dumps({
        "state": "connected",
        "since": "2026-09-18T00:00:00Z",
        "device_name": name,
        "server": "https://example.invalid",
        "streams_active": 0,
        "streams_total": 3,
        "bytes_relayed": 2048,
        "last_error": None,
        "remedy": None,
        "helper_version": "0.0.0-standin",
        "token_len": len(token),
    }))
    print(json.dumps({"state": "connected"}), flush=True)

    # Exit on stdin EOF, exactly like the real helper.
    sys.stdin.read()
    raise SystemExit(0)
    '''
).lstrip()


@pytest.fixture
def stand_in_binary(tmp_path: Path) -> Path:
    """An executable that speaks the helper's CLI."""
    script = tmp_path / "matrx-egress"
    script.write_text(f"#!{sys.executable}\n" + STAND_IN)
    script.chmod(0o755)
    return script


@pytest.fixture
def egress_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / "egress").mkdir(parents=True)
    monkeypatch.setattr(module, "MATRX_HOME_DIR", home)
    return home


@pytest.fixture
def no_discovery(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict | None]]:
    """Capture the discovery writes instead of touching a real ~/.matrx."""
    written: list[tuple[str, dict | None]] = []
    import app.preflight as preflight

    monkeypatch.setattr(
        preflight,
        "update_discovery_service",
        lambda key, info: written.append((key, info)),
    )
    return written


def _fake_registration(monkeypatch: pytest.MonkeyPatch, *, seen: list[str]) -> None:
    async def _register(self, binary):  # noqa: ANN001
        return {
            "device_id": "dev-1",
            "display_name": "Test Computer",
            "device_token": "mxe_dev-1_secret",
        }

    monkeypatch.setattr(module.ResidentialEgressSupervisor, "_register_device", _register)

    def _cmd(self, binary, status_file):  # noqa: ANN001
        seen.append(str(binary))
        return [
            str(binary),
            "run",
            "--token-stdin",
            "--server",
            "https://example.invalid",
            "--status-file",
            str(status_file),
            "--no-tray",
            "--name",
            "Test Computer",
        ]

    monkeypatch.setattr(module.ResidentialEgressSupervisor, "_build_command", _cmd)


# ---------------------------------------------------------------------------
# Not installed — the state is honest, and it names its remedy
# ---------------------------------------------------------------------------


async def test_missing_binary_is_a_named_state_with_a_remedy(
    egress_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(module._BINARY_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "find_binary", lambda: None)

    supervisor = module.ResidentialEgressSupervisor()
    assert await supervisor.start() is False

    status = supervisor.get_status(enabled=True)
    assert status["state"] == "not_installed"
    assert status["installed"] is False
    assert status["enabled"] is True
    assert status["last_error"] == module.NOT_INSTALLED_MESSAGE
    assert status["remedy"]  # never a dead end


async def test_disabled_reads_as_disabled_not_as_an_error(
    egress_home: Path, stand_in_binary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "find_binary", lambda: stand_in_binary)
    status = module.ResidentialEgressSupervisor().get_status(enabled=False)
    assert status["state"] == "disabled"
    assert status["installed"] is True
    assert status["last_error"] is None


# ---------------------------------------------------------------------------
# Start / stop against a real child
# ---------------------------------------------------------------------------


async def test_start_spawns_the_child_hands_it_the_token_and_reports_connected(
    egress_home: Path,
    stand_in_binary: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_discovery: list[tuple[str, dict | None]],
) -> None:
    monkeypatch.setattr(module, "find_binary", lambda: stand_in_binary)
    seen: list[str] = []
    _fake_registration(monkeypatch, seen=seen)

    supervisor = module.ResidentialEgressSupervisor()
    try:
        assert await supervisor.start() is True
        assert supervisor.running is True
        assert supervisor.device_id == "dev-1"

        status = supervisor.get_status(enabled=True)
        assert status["state"] == "connected"
        assert status["installed"] is True
        assert status["bytes_relayed"] == 2048

        # The token reached the child's stdin — and only there.
        helper_status = supervisor.read_status_file()
        assert helper_status["token_len"] == len("mxe_dev-1_secret")
        assert seen == [str(stand_in_binary)]

        # ...and it is nowhere in the argv the rest of the machine can read.
        argv = " ".join(psutil.Process(supervisor._process.pid).cmdline())
        assert "secret" not in argv

        # The identity preflight needs was persisted the moment we spawned.
        key, info = no_discovery[-1]
        assert key == "residential_egress"
        assert info["pid"] == supervisor._process.pid
        assert info["process_started_at"] > 0
        assert info["executable"]
    finally:
        await supervisor.stop()


async def test_stop_reaps_the_child_and_clears_its_identity(
    egress_home: Path,
    stand_in_binary: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_discovery: list[tuple[str, dict | None]],
) -> None:
    monkeypatch.setattr(module, "find_binary", lambda: stand_in_binary)
    _fake_registration(monkeypatch, seen=[])

    supervisor = module.ResidentialEgressSupervisor()
    assert await supervisor.start() is True
    pid = supervisor._process.pid

    await supervisor.stop()

    assert supervisor.running is False
    assert supervisor.process_identity is None
    assert not module.status_file_path().exists()
    assert no_discovery[-1] == ("residential_egress", None)
    # The child is genuinely gone, not merely forgotten.
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


# ---------------------------------------------------------------------------
# Orphan identity — the three-way proof preflight demands
# ---------------------------------------------------------------------------


async def test_process_identity_is_the_three_way_proof_preflight_requires(
    egress_home: Path, stand_in_binary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import preflight

    monkeypatch.setattr(module, "find_binary", lambda: stand_in_binary)
    _fake_registration(monkeypatch, seen=[])
    monkeypatch.setattr(preflight, "update_discovery_service", lambda *a: None)

    supervisor = module.ResidentialEgressSupervisor()
    assert await supervisor.start() is True
    try:
        identity = supervisor.process_identity
        assert set(identity) == {"pid", "process_started_at", "executable"}

        service = next(
            s for s in preflight.SERVICES if s.discovery_key == "residential_egress"
        )
        assert service.require_discovery_identity is True
        assert service.orphan_only is True
        assert service.spawned_by == "python"

        proc = psutil.Process(identity["pid"])
        # The recorded identity matches the live process...
        assert (
            preflight._matches_discovery_identity(
                proc,
                pid=identity["pid"],
                service=service,
                discovery={"services": {"residential_egress": identity}},
            )
            is not None
        )
        # ...and a record with the right PID but the wrong creation time does
        # not, so a reused PID can never be mistaken for our child.
        wrong = dict(identity, process_started_at=identity["process_started_at"] + 60)
        assert (
            preflight._matches_discovery_identity(
                proc,
                pid=identity["pid"],
                service=service,
                discovery={"services": {"residential_egress": wrong}},
            )
            is None
        )
    finally:
        await supervisor.stop()


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def test_env_value_names_the_binary_it_is_not_a_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "custom-egress"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv(module._BINARY_ENV_VAR, str(binary))
    assert module.find_binary() == binary

    # A path that does not exist falls through to the normal order rather than
    # disabling anything — the variable is a VALUE, never a switch.
    monkeypatch.setenv(module._BINARY_ENV_VAR, str(tmp_path / "nope"))
    assert module.find_binary() != binary


# ---------------------------------------------------------------------------
# The one decision point
# ---------------------------------------------------------------------------


async def test_reconcile_starts_only_when_signed_in_and_enabled(
    egress_home: Path, stand_in_binary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import preflight

    monkeypatch.setattr(module, "find_binary", lambda: stand_in_binary)
    _fake_registration(monkeypatch, seen=[])
    monkeypatch.setattr(preflight, "update_discovery_service", lambda *a: None)

    supervisor = module.ResidentialEgressSupervisor()
    monkeypatch.setattr(module, "get_egress_supervisor", lambda: supervisor)

    enabled = {"value": False}
    signed_in = {"value": False}
    monkeypatch.setattr(module, "egress_enabled", lambda: enabled["value"])

    async def _signed_in() -> bool:
        return signed_in["value"]

    monkeypatch.setattr(module, "_signed_in", _signed_in)

    try:
        # Neither: nothing runs.
        await module.reconcile_residential_egress()
        assert supervisor.running is False

        # Enabled but signed out: still nothing — this is the user's own work,
        # lent by the user, so there must be a user.
        enabled["value"] = True
        await module.reconcile_residential_egress()
        assert supervisor.running is False

        # Signed in and enabled: it runs.
        signed_in["value"] = True
        await module.reconcile_residential_egress()
        assert supervisor.running is True

        # Signing out takes it down.
        signed_in["value"] = False
        await module.reconcile_residential_egress()
        assert supervisor.running is False

        # So does turning the switch off.
        signed_in["value"] = True
        await module.reconcile_residential_egress()
        assert supervisor.running is True
        enabled["value"] = False
        await module.reconcile_residential_egress()
        assert supervisor.running is False
    finally:
        await supervisor.stop()


async def test_enabled_but_signed_out_says_so_instead_of_reading_as_stopped(
    egress_home: Path, stand_in_binary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A switch the user turned on that quietly does nothing is the failure.

    Shipped for about an hour during this build: enabled + signed out returned
    `state="stopped"`, `last_error=null`, `remedy=null` — the user flips the
    switch and the screen says nothing at all. Caught by a dev engine run, not
    by any test, which is why this one exists.
    """
    from app import preflight
    from app.launcher import get_registry

    monkeypatch.setattr(module, "find_binary", lambda: stand_in_binary)
    monkeypatch.setattr(preflight, "update_discovery_service", lambda *a: None)

    supervisor = module.ResidentialEgressSupervisor()
    monkeypatch.setattr(module, "get_egress_supervisor", lambda: supervisor)
    monkeypatch.setattr(module, "egress_enabled", lambda: True)

    async def _signed_in() -> bool:
        return False

    monkeypatch.setattr(module, "_signed_in", _signed_in)

    await module.reconcile_residential_egress()

    status = supervisor.get_status(enabled=True)
    assert supervisor.running is False
    assert status["state"] == "signed_out"
    assert status["enabled"] is True
    assert "signed in" in status["last_error"].lower()
    assert status["remedy"]
    # ...and /admin/status carries the reason too, rather than an empty slot
    # that reads as "nobody wanted this".
    record = get_registry().snapshot()
    assert "signed in" in json.dumps(record)

    # The witness for the bug: with the reason cleared — exactly what the code
    # did before this fix — the same situation reads as a bare "stopped" with
    # nothing to explain it. This assertion FAILS the moment someone drops
    # `_blocked_reason` again.
    supervisor._blocked_reason = None
    silent = supervisor.get_status(enabled=True)
    assert silent["state"] == "stopped"
    assert silent["last_error"] is None
    assert silent["remedy"] is None


def test_the_setting_is_off_by_default_and_the_old_proxy_keys_are_gone() -> None:
    from app.services.cloud_sync import settings_sync

    assert settings_sync.DEFAULT_SETTINGS["residential_egress_enabled"] is False
    assert "proxy_enabled" not in settings_sync.DEFAULT_SETTINGS
    assert "proxy_port" not in settings_sync.DEFAULT_SETTINGS
    assert "residential_egress_" in settings_sync.RESET_SCOPES["network"]
