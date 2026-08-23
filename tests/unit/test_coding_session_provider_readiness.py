from __future__ import annotations

import asyncio
import json
import os
import plistlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.services.coding_sessions.provider_readiness import (
    ProviderReadinessFacade,
    _executable_version,
)


def _json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _manifest(
    path: Path, *, name: str, version: str, publisher: str | None = None
) -> None:
    value: dict[str, Any] = {"name": name, "version": version}
    if publisher is not None:
        value["publisher"] = publisher
    _json(path, value)


def _app(applications: Path, name: str, version: str) -> None:
    info = applications / name / "Contents/Info.plist"
    info.parent.mkdir(parents=True)
    with info.open("wb") as handle:
        plistlib.dump({"CFBundleShortVersionString": version}, handle)


@pytest.fixture
def installed_host(tmp_path: Path) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    matrx_home = home / ".matrx"
    applications = tmp_path / "Applications"

    claude_root = home / ".claude/plugins/cache/matrx/matrx/0.2.0-alpha.6"
    _json(
        home / ".claude/plugins/installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "matrx@matrx": [
                    {
                        "scope": "user",
                        "version": "0.2.0-alpha.6",
                        "installPath": str(claude_root),
                    }
                ]
            },
        },
    )
    _manifest(
        claude_root / ".claude-plugin/plugin.json",
        name="matrx",
        version="0.2.0-alpha.6",
    )
    _json(claude_root / "hooks/hooks.json", {})
    _json(claude_root / ".mcp.json", {})

    _manifest(
        home
        / ".codex/plugins/cache/ai-matrx/matrx-codex-plugin/0.2.0-alpha.4/.codex-plugin/plugin.json",
        name="matrx-codex-plugin",
        version="0.2.0-alpha.4",
    )
    _manifest(
        home / ".cursor/plugins/local/matrx/.cursor-plugin/plugin.json",
        name="matrx-cursor-plugin",
        version="0.2.0-alpha.2",
    )
    _manifest(
        home / ".vscode/extensions/aimatrx.aimatrx-0.1.1/package.json",
        name="aimatrx",
        version="0.1.1",
        publisher="aimatrx",
    )
    _app(applications, "Codex.app", "1.2.3")
    _app(applications, "Cursor.app", "2.3.4")
    _app(applications, "Visual Studio Code.app", "3.4.5")
    return home, matrx_home, applications


@pytest.mark.anyio
async def test_reports_each_evidence_layer_without_claiming_connection(
    installed_host: tuple[Path, Path, Path],
) -> None:
    home, matrx_home, applications = installed_host
    now = datetime(2026, 8, 23, 18, 0, tzinfo=UTC)
    codex_spool = (
        home / ".codex/plugins/data/matrx-codex-plugin-ai-matrx/coding-session-bridge"
    )
    cursor_spool = matrx_home / "plugins/matrx-cursor-plugin/coding-session-bridge"
    for path in (
        codex_spool / "pending/one.json",
        codex_spool / "pending/two.json.claim-1",
        codex_spool / "pending/three.json.tmp",
        codex_spool / "poison/four.json",
        cursor_spool / "pending/five.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        stamp = (now - timedelta(minutes=5)).timestamp()
        os.utime(path, (stamp, stamp))

    async def versions(executable: str) -> str | None:
        return {"/bin/claude": "2.1.228", "/bin/cursor": "9.9.9"}.get(executable)

    facade = ProviderReadinessFacade(
        home=home,
        matrx_home=matrx_home,
        applications=applications,
        process_probe=lambda: {"cursor", "visual studio code"},
        which_probe=lambda name: {"claude": "/bin/claude", "cursor": "/bin/cursor"}.get(
            name
        ),
        version_probe=versions,
        now=lambda: now,
    )
    status = await facade.status(
        {
            "providers": {
                "codex": {
                    "last_enqueue": {"at": "2026-08-23 17:40:00"},
                    "last_acknowledgement": {"at": "2026-08-23T17:45:00+00:00"},
                }
            }
        }
    )

    assert status["generated_at"] == "2026-08-23T18:00:00Z"
    claude = status["providers"]["claude_code"]
    assert claude["product"] == {
        "installed": True,
        "version": "2.1.228",
        "running": False,
        "evidence": ["cli_executable_detected"],
    }
    assert claude["adapter"]["configured"] is True
    assert claude["adapter"]["authorization"] == "unknown"

    codex = status["providers"]["codex"]
    assert codex["product"]["version"] == "1.2.3"
    assert codex["adapter"]["detected"] is True
    assert codex["adapter"]["configured"] is None
    assert codex["adapter"]["hook_trust"] == "review_required"
    assert codex["upstream_spool"] == {
        "supported": True,
        "pending": 1,
        "poison": 1,
        "in_flight": 1,
        "temporary": 1,
        "oldest_pending_at": "2026-08-23T17:55:00Z",
        "oldest_pending_age_seconds": 300,
    }
    assert codex["activity"] == {
        "last_local_enqueue_at": "2026-08-23T17:40:00Z",
        "last_cloud_acknowledgement_at": "2026-08-23T17:45:00Z",
        "most_recent": {
            "kind": "cloud_acknowledgement",
            "at": "2026-08-23T17:45:00Z",
        },
    }
    assert codex["connection"]["state"] == "unverified"
    assert codex["connection"]["evidence"] == ["prior_cloud_acknowledgement"]
    assert any(action["id"] == "review_codex_hook_trust" for action in codex["actions"])

    cursor = status["providers"]["cursor"]
    assert cursor["product"]["running"] is True
    assert cursor["adapter"]["configured"] is True
    assert cursor["upstream_spool"]["pending"] == 1

    vscode = status["providers"]["vscode"]
    assert vscode["product"]["running"] is True
    assert vscode["adapter"]["configured"] is True
    assert any(action["id"] == "test_vscode_connection" for action in vscode["actions"])

    rendered = json.dumps(status)
    assert str(home) not in rendered
    assert "/bin/claude" not in rendered
    assert '"connected"' not in rendered


@pytest.mark.anyio
async def test_near_match_helper_processes_do_not_claim_products_are_running(
    tmp_path: Path,
) -> None:
    facade = ProviderReadinessFacade(
        home=tmp_path / "home",
        matrx_home=tmp_path / "matrx",
        applications=tmp_path / "Applications",
        process_probe=lambda: {
            "claude-language-server",
            "codex-helper",
            "cursor helper",
            "code helper",
        },
        which_probe=lambda _name: None,
        now=lambda: datetime(2026, 8, 23, tzinfo=UTC),
    )

    providers = (await facade.status())["providers"]

    assert all(item["product"]["running"] is False for item in providers.values())
    assert all(item["product"]["installed"] is None for item in providers.values())
    assert all(
        item["connection"]["state"] == "unverified" for item in providers.values()
    )


@pytest.mark.anyio
async def test_unpublished_adapters_offer_guidance_not_install_execution(
    tmp_path: Path,
) -> None:
    facade = ProviderReadinessFacade(
        home=tmp_path / "home",
        matrx_home=tmp_path / "matrx",
        applications=tmp_path / "Applications",
        process_probe=lambda: set(),
        which_probe=lambda _name: None,
        now=lambda: datetime(2026, 8, 23, tzinfo=UTC),
    )
    providers = (await facade.status())["providers"]

    for provider in ("cursor", "vscode"):
        actions = providers[provider]["actions"]
        assert all(action["kind"] == "guided_instruction" for action in actions)
        assert (
            "not published" in actions[-1]["instruction"]
            or "pre-release" in actions[-1]["instruction"]
        )
        assert all("command" not in action for action in actions)


@pytest.mark.anyio
async def test_timed_out_version_probe_terminates_and_reaps_child() -> None:
    class _Process:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.terminated = False
            self.reaped = False
            self._finished = asyncio.Event()

        async def communicate(self) -> tuple[bytes, bytes]:
            await self._finished.wait()
            self.reaped = True
            return b"", b""

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15
            self._finished.set()

        def kill(self) -> None:
            self.returncode = -9
            self._finished.set()

    process = _Process()

    async def factory(*_args: object, **_kwargs: object) -> _Process:
        return process

    assert (
        await _executable_version("fake", timeout=0.001, process_factory=factory)
        is None
    )
    assert process.terminated is True
    assert process.reaped is True
