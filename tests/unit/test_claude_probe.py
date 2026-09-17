"""Canonical Claude CLI discovery and account-probe regression tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.coding_sessions.claude_probe import (
    derive_account_key,
    read_account_snapshot,
    resolve_claude_executable,
)

pytestmark = pytest.mark.anyio


def _write_cli(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o700)
    return path


class _CompletedProcess:
    def __init__(self, stdout: bytes, *, returncode: int = 0) -> None:
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _completed_process_factory(*outputs: bytes):
    remaining = iter(outputs)

    async def factory(*_args: Any, **_kwargs: Any) -> _CompletedProcess:
        return _CompletedProcess(next(remaining))

    return factory


def _oauth_record(path: Path, *, email: str | None, org_id: str | None) -> None:
    account: dict[str, str] = {}
    if email is not None:
        account["emailAddress"] = email
    if org_id is not None:
        account["organizationUuid"] = org_id
    path.write_text(json.dumps({"oauthAccount": account}))


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
    assert snapshot.account_label == "arman@titaniumsuccess.com"
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
    assert snapshot.account_label == "org:org-subscription"


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

    snapshot = await read_account_snapshot(
        executable=launcher,
        oauth_record_path=tmp_path / "no-desktop-oauth-record.json",
    )

    assert snapshot.available is False
    assert snapshot.reason == expected_reason
    assert snapshot.probe_status == expected_status


async def test_signed_out_status_falls_back_to_desktop_oauth_record(
    tmp_path: Path,
) -> None:
    """`auth status` misreporting signed-out must not lose identity.

    Observed on Claude 2.1.228: status says logged out while runs execute.
    The desktop OAuth record carries the same identity fields, and the
    derived key must be byte-identical to a CLI-derived one so sessions
    never fork across probe sources.
    """
    launcher = _write_cli(
        tmp_path / "claude",
        "if [ \"$1\" = \"auth\" ]; then "
        "echo '{\"loggedIn\": false, \"authMethod\": \"none\", "
        "\"apiProvider\": \"firstParty\"}'; else echo '2.1.228'; fi\n",
    )
    record = tmp_path / "claude.json"
    record.write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "emailAddress": "User@Example.com",
                    "organizationUuid": "org-1234",
                }
            }
        )
    )

    snapshot = await read_account_snapshot(
        executable=launcher, oauth_record_path=record
    )

    assert snapshot.available is True
    assert snapshot.probe_status == "ready"
    assert snapshot.account_key == derive_account_key(
        api_provider="firstParty",
        auth_method="claude.ai",
        org_id="org-1234",
        email="user@example.com",
    )
    assert snapshot.account_label == "user@example.com"
    assert snapshot.diagnostic is not None and "desktop OAuth" in snapshot.diagnostic


async def test_cli_account_snapshot_trims_org_and_email_without_rotating_v2_key(
    tmp_path: Path,
) -> None:
    """CLI formatting must match the hook's trimmed OAuth-record inputs."""
    canonical_key = "3f4585247ba86157b1c99d62f332746019cb37f8c50c70addaef1ae192130910"
    plain_auth = json.dumps(
        {
            "loggedIn": True,
            "apiProvider": "firstParty",
            "authMethod": "claude.ai",
            "orgId": "org-1234",
            "email": "user@example.com",
        }
    ).encode()
    padded_auth = json.dumps(
        {
            "loggedIn": True,
            "apiProvider": "firstParty",
            "authMethod": "claude.ai",
            "orgId": "  org-1234  ",
            "email": " User@Example.com ",
        }
    ).encode()
    launcher = tmp_path / "fake-claude"
    plain = await read_account_snapshot(
        executable=launcher,
        process_factory=_completed_process_factory(plain_auth, b"2.1.228"),
    )
    padded = await read_account_snapshot(
        executable=launcher,
        process_factory=_completed_process_factory(padded_auth, b"2.1.228"),
    )

    assert plain.account_key == canonical_key
    assert padded.account_key == canonical_key
    assert padded.account_label == "user@example.com"


async def test_oauth_fallback_trims_org_and_rejects_whitespace_only_org(
    tmp_path: Path,
) -> None:
    signed_out = b'{"loggedIn": false}'
    record = tmp_path / "claude.json"
    record.write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "emailAddress": " User@Example.com ",
                    "organizationUuid": "  org-1234  ",
                }
            }
        )
    )
    snapshot = await read_account_snapshot(
        executable=tmp_path / "fake-claude",
        oauth_record_path=record,
        process_factory=_completed_process_factory(signed_out),
    )
    assert (
        snapshot.account_key
        == "3f4585247ba86157b1c99d62f332746019cb37f8c50c70addaef1ae192130910"
    )
    assert snapshot.account_label == "user@example.com"

    record.write_text(json.dumps({"oauthAccount": {"organizationUuid": "   "}}))
    unavailable = await read_account_snapshot(
        executable=tmp_path / "fake-claude",
        oauth_record_path=record,
        process_factory=_completed_process_factory(signed_out),
    )
    assert unavailable.available is False
    assert unavailable.reason == "claude_not_signed_in"


async def test_known_subscription_without_cli_identity_uses_oauth_record(
    tmp_path: Path,
) -> None:
    record = tmp_path / "claude.json"
    _oauth_record(record, email="User@Example.com", org_id="org-1234")
    snapshot = await read_account_snapshot(
        executable=tmp_path / "fake-claude",
        oauth_record_path=record,
        process_factory=_completed_process_factory(
            b'{"loggedIn":true,"apiProvider":"firstParty","authMethod":"claude.ai"}',
            b"2.1.271",
        ),
    )

    assert snapshot.account_key == derive_account_key(
        api_provider="firstParty",
        auth_method="claude.ai",
        org_id="org-1234",
        email="user@example.com",
    )
    assert snapshot.client_version == "2.1.271"
    assert snapshot.diagnostic is not None and "desktop OAuth" in snapshot.diagnostic


@pytest.mark.parametrize("record_body", [None, "{invalid"])
async def test_known_subscription_without_cli_identity_needs_readable_oauth_record(
    tmp_path: Path, record_body: str | None
) -> None:
    record = tmp_path / "claude.json"
    if record_body is not None:
        record.write_text(record_body)
    snapshot = await read_account_snapshot(
        executable=tmp_path / "fake-claude",
        oauth_record_path=record,
        process_factory=_completed_process_factory(
            b'{"loggedIn":true,"apiProvider":"firstParty","authMethod":"claude.ai"}',
            b"2.1.271",
        ),
    )

    assert snapshot.available is False
    assert snapshot.reason == "claude_account_identity_unavailable"
    assert snapshot.client_version == "2.1.271"


async def test_partial_cli_identity_is_not_mixed_with_oauth_record(tmp_path: Path) -> None:
    record = tmp_path / "claude.json"
    _oauth_record(record, email="other@example.com", org_id="other-org")
    snapshot = await read_account_snapshot(
        executable=tmp_path / "fake-claude",
        oauth_record_path=record,
        process_factory=_completed_process_factory(
            b'{"loggedIn":true,"apiProvider":"firstParty","authMethod":"claude.ai",'
            b'"email":"User@Example.com"}',
            b"2.1.271",
        ),
    )

    assert snapshot.account_key == derive_account_key(
        api_provider="firstParty",
        auth_method="claude.ai",
        org_id=None,
        email="user@example.com",
    )
    assert snapshot.diagnostic is None


async def test_complete_cli_and_non_subscription_login_do_not_use_oauth_record(
    tmp_path: Path,
) -> None:
    record = tmp_path / "claude.json"
    _oauth_record(record, email="other@example.com", org_id="other-org")
    complete = await read_account_snapshot(
        executable=tmp_path / "fake-claude",
        oauth_record_path=record,
        process_factory=_completed_process_factory(
            b'{"loggedIn":true,"apiProvider":"firstParty","authMethod":"claude.ai",'
            b'"orgId":"cli-org","email":"cli@example.com"}',
            b"2.1.271",
        ),
    )
    api_key = await read_account_snapshot(
        executable=tmp_path / "fake-claude",
        oauth_record_path=record,
        process_factory=_completed_process_factory(
            b'{"loggedIn":true,"apiProvider":"apiKey","authMethod":"api_key"}',
            b"2.1.271",
        ),
    )
    unknown = await read_account_snapshot(
        executable=tmp_path / "fake-claude",
        oauth_record_path=record,
        process_factory=_completed_process_factory(
            b'{"loggedIn":true,"apiProvider":"unknown","authMethod":"unknown"}',
            b"2.1.271",
        ),
    )

    assert complete.account_key == derive_account_key(
        api_provider="firstParty",
        auth_method="claude.ai",
        org_id="cli-org",
        email="cli@example.com",
    )
    assert complete.diagnostic is None
    assert api_key.available is False
    assert api_key.reason == "claude_account_identity_unavailable"
    assert unknown.available is False
    assert unknown.reason == "claude_account_identity_unavailable"


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
