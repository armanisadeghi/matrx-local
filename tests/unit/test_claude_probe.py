"""Canonical Claude CLI discovery and account-probe regression tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.coding_sessions.claude_probe import (
    read_account_snapshot,
    resolve_claude_executable,
)

pytestmark = pytest.mark.anyio


def _write_cli(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o700)
    return path


def test_packaged_gui_path_finds_official_native_launcher(tmp_path: Path) -> None:
    launcher = _write_cli(tmp_path / ".local" / "bin" / "claude", "exit 0\n")

    resolved = resolve_claude_executable(
        home=tmp_path,
        # The packaged macOS app sees only system locations.
        search_path="/usr/bin:/bin:/usr/sbin:/sbin",
        platform_name="darwin",
    )

    assert resolved == launcher


def test_legacy_native_launcher_is_supported_but_must_be_executable(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / ".claude" / "local" / "claude"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("#!/bin/sh\nexit 0\n")
    assert (
        resolve_claude_executable(
            home=tmp_path, search_path="", platform_name="darwin"
        )
        is None
    )
    legacy.chmod(0o700)
    assert resolve_claude_executable(
        home=tmp_path, search_path="", platform_name="darwin"
    ) == legacy


async def test_signed_in_account_uses_canonical_launcher_and_safe_identity(
    tmp_path: Path,
) -> None:
    auth = json.dumps(
        {
            "loggedIn": True,
            "apiProvider": "firstParty",
            "authMethod": "claude.ai",
            "orgId": "e883f812-239f-4dd8",
            "email": "Arman@TitaniumSuccess.com",
        }
    )
    launcher = _write_cli(
        tmp_path / ".local" / "bin" / "claude",
        f'if [ "$1" = "auth" ]; then echo \'{auth}\'; else echo "2.1.228"; fi\n',
    )

    snapshot = await read_account_snapshot(executable=launcher)

    assert snapshot.available is True
    assert snapshot.reason is None
    assert snapshot.probe_status == "ready"
    assert snapshot.executable_path == str(launcher)
    assert snapshot.client_version == "2.1.228"
    assert snapshot.account_label == "a***n@t***.com"
    assert snapshot.account_key is not None
    assert snapshot.fingerprint == snapshot.account_key[:12]


async def test_account_probe_ignores_inherited_developer_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = json.dumps(
        {
            "loggedIn": True,
            "apiProvider": "firstParty",
            "authMethod": "claude.ai",
            "orgId": "org-subscription",
        }
    )
    launcher = _write_cli(
        tmp_path / "claude",
        'if [ -n "$ANTHROPIC_API_KEY" ]; then '
        "echo '{\"loggedIn\":true,\"apiKeySource\":\"ANTHROPIC_API_KEY\","
        "\"email\":null,\"orgId\":null}'; "
        f'elif [ "$1" = "auth" ]; then echo \'{auth}\'; '
        "else echo '2.1.228'; fi\n",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "developer-key-must-not-win")

    snapshot = await read_account_snapshot(executable=launcher)

    assert snapshot.available is True
    assert snapshot.account_label == "org:org-subs"


@pytest.mark.parametrize(
    ("auth_payload", "expected_reason", "expected_status"),
    [
        ({"loggedIn": False}, "claude_not_signed_in", "signed_out"),
        (
            {"loggedIn": True, "apiProvider": "firstParty"},
            "claude_account_identity_unavailable",
            "identity_unavailable",
        ),
    ],
)
async def test_account_states_are_distinct(
    tmp_path: Path,
    auth_payload: dict[str, Any],
    expected_reason: str,
    expected_status: str,
) -> None:
    launcher = _write_cli(
        tmp_path / "claude",
        "if [ \"$1\" = \"auth\" ]; then "
        f"echo '{json.dumps(auth_payload)}'; else echo '2.1.228'; fi\n",
    )

    snapshot = await read_account_snapshot(executable=launcher)

    assert snapshot.available is False
    assert snapshot.reason == expected_reason
    assert snapshot.probe_status == expected_status


async def test_invalid_auth_command_is_execution_failure(tmp_path: Path) -> None:
    launcher = _write_cli(tmp_path / "claude", "echo 'broken' >&2\nexit 7\n")

    snapshot = await read_account_snapshot(executable=launcher)

    assert snapshot.reason == "claude_status_execution_failed"
    assert snapshot.probe_status == "execution_failed"
    assert snapshot.diagnostic == "broken"


async def test_missing_executable_is_not_found() -> None:
    snapshot = await read_account_snapshot(resolver=lambda: None)

    assert snapshot.reason == "claude_not_installed"
    assert snapshot.probe_status == "not_found"


class _HungProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.reaped = False
        self._released = asyncio.Event()

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._released.wait()
        self.reaped = True
        return b"", b""

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self._released.set()

    def kill(self) -> None:
        self.returncode = -9
        self._released.set()


async def test_timeout_terminates_and_reaps_child(tmp_path: Path) -> None:
    launcher = _write_cli(tmp_path / "claude", "exit 0\n")
    process = _HungProcess()

    async def factory(*_args: Any, **_kwargs: Any) -> _HungProcess:
        return process

    snapshot = await read_account_snapshot(
        executable=launcher,
        timeout=0.001,
        process_factory=factory,
    )

    assert snapshot.reason == "claude_status_timeout"
    assert snapshot.probe_status == "timeout"
    assert process.terminated is True
    assert process.reaped is True
