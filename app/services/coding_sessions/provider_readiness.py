"""Truthful local readiness evidence for coding-session providers.

This facade deliberately does not return a ``connected`` boolean. A running
editor, an installed adapter, an OAuth session, hook trust, a local durable
enqueue, and a cloud acknowledgement are different facts. The desktop can
show all of them without promoting any one weak probe into a live-connection
claim.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import plistlib
import re
import shutil
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from app.config import MATRX_HOME_DIR

_PROVIDER_ORDER = ("claude_code", "codex", "cursor", "vscode")
_DISPLAY_NAMES = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "vscode": "VS Code",
}
_EXECUTABLE_NAMES = {
    "claude_code": ("claude",),
    "codex": ("codex",),
    "cursor": ("cursor",),
    "vscode": ("code",),
}
_PROCESS_NAMES = {
    "claude_code": frozenset({"claude"}),
    "codex": frozenset({"codex"}),
    "cursor": frozenset({"cursor"}),
    "vscode": frozenset({"code", "visual studio code"}),
}
_MAC_APP_NAMES = {
    "codex": "Codex.app",
    "cursor": "Cursor.app",
    "vscode": "Visual Studio Code.app",
}
_VERSION_RE = re.compile(r"\b\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?\b")

VersionProbe = Callable[[str], Awaitable[str | None]]
ProcessProbe = Callable[[], set[str]]
WhichProbe = Callable[[str], str | None]
ProcessFactory = Callable[..., Awaitable[Any]]


def _utc(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > 1_000_000:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _manifest_version(path: Path) -> str | None:
    value = _safe_json(path)
    version = value.get("version") if value else None
    return version if isinstance(version, str) and version else None


def _bounded_manifests(root: Path, name: str) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    found: list[Path] = []
    try:
        for path in root.rglob(name):
            found.append(path)
            if len(found) >= 500:
                break
    except OSError:
        pass
    return found


def _process_names() -> set[str]:
    names: set[str] = set()
    for process in psutil.process_iter(["name", "exe"]):
        try:
            name = process.info.get("name")
            executable = process.info.get("exe")
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        for value in (name, Path(executable).name if executable else None):
            if isinstance(value, str) and value:
                names.add(value.casefold())
    return names


async def _executable_version(
    executable: str,
    *,
    timeout: float = 2.0,
    process_factory: ProcessFactory = asyncio.create_subprocess_exec,
) -> str | None:
    try:
        process = await process_factory(
            executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError:
        return None
    communicate = asyncio.create_task(process.communicate())
    try:
        output, _ = await asyncio.wait_for(asyncio.shield(communicate), timeout=timeout)
    except asyncio.TimeoutError:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
        try:
            await asyncio.wait_for(asyncio.shield(communicate), timeout=0.5)
        except asyncio.TimeoutError:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await communicate
        return None
    except asyncio.CancelledError:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
        try:
            await asyncio.wait_for(asyncio.shield(communicate), timeout=0.5)
        except asyncio.TimeoutError:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await communicate
        raise
    text = output.decode("utf-8", errors="replace")[:512]
    match = _VERSION_RE.search(text)
    return match.group(0) if match else None


def _app_version(app: Path) -> str | None:
    info = app / "Contents" / "Info.plist"
    try:
        if not info.is_file() or info.stat().st_size > 1_000_000:
            return None
        with info.open("rb") as handle:
            value = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException):
        return None
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        version = value.get(key) if isinstance(value, dict) else None
        if isinstance(version, str) and version:
            return version
    return None


def _spool_status(roots: Iterable[Path], now: datetime) -> dict[str, Any]:
    pending: list[Path] = []
    poison: list[Path] = []
    in_flight: list[Path] = []
    temporary: list[Path] = []
    for root in dict.fromkeys(roots):
        pending_dir = root / "pending"
        poison_dir = root / "poison"
        try:
            pending.extend(pending_dir.glob("*.json"))
            in_flight.extend(pending_dir.glob("*.json.claim-*"))
            temporary.extend(pending_dir.glob("*.json.tmp"))
            poison.extend(poison_dir.glob("*.json"))
        except OSError:
            continue
    oldest_at: datetime | None = None
    for path in [*pending, *in_flight]:
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if oldest_at is None or modified < oldest_at:
            oldest_at = modified
    age = None
    if oldest_at is not None:
        age = max(0, int((now - oldest_at).total_seconds()))
    return {
        "supported": True,
        "pending": len(pending),
        "poison": len(poison),
        "in_flight": len(in_flight),
        "temporary": len(temporary),
        "oldest_pending_at": _utc(oldest_at),
        "oldest_pending_age_seconds": age,
    }


def _no_spool() -> dict[str, Any]:
    return {
        "supported": False,
        "pending": None,
        "poison": None,
        "in_flight": None,
        "temporary": None,
        "oldest_pending_at": None,
        "oldest_pending_age_seconds": None,
    }


class ProviderReadinessFacade:
    def __init__(
        self,
        *,
        home: Path | None = None,
        matrx_home: Path | None = None,
        applications: Path | None = None,
        process_probe: ProcessProbe = _process_names,
        which_probe: WhichProbe = shutil.which,
        version_probe: VersionProbe = _executable_version,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._home = home or Path.home()
        self._matrx_home = matrx_home or Path(MATRX_HOME_DIR)
        self._applications = applications or Path("/Applications")
        self._process_probe = process_probe
        self._which = which_probe
        self._version_probe = version_probe
        self._now = now

    def _claude_adapter(self) -> dict[str, Any]:
        registry = _safe_json(self._home / ".claude/plugins/installed_plugins.json")
        plugins = registry.get("plugins") if registry else None
        entries = plugins.get("matrx@matrx") if isinstance(plugins, dict) else None
        if isinstance(entries, dict):
            entries = [entries]
        candidates = [item for item in entries or [] if isinstance(item, dict)]
        selected = candidates[-1] if candidates else None
        version = selected.get("version") if selected else None
        root = None
        install_path = selected.get("installPath") if selected else None
        if isinstance(install_path, str) and install_path:
            root = Path(install_path)
        elif isinstance(version, str) and version:
            root = self._home / ".claude/plugins/cache/matrx/matrx" / version
        configured = bool(
            root
            and (root / ".claude-plugin/plugin.json").is_file()
            and (root / "hooks/hooks.json").is_file()
            and (root / ".mcp.json").is_file()
        )
        detected = selected is not None
        return {
            "detected": detected,
            "configured": configured if detected else False,
            "version": version if isinstance(version, str) else None,
            "authorization": "unknown",
            "hook_trust": "not_applicable",
            "evidence": ["host_install_registry"] if detected else [],
        }

    def _codex_adapter(self) -> dict[str, Any]:
        root = self._home / ".codex/plugins/cache/ai-matrx/matrx-codex-plugin"
        manifests = list(_bounded_manifests(root, "plugin.json"))
        manifests = [path for path in manifests if path.parent.name == ".codex-plugin"]
        versions = [
            version for path in manifests if (version := _manifest_version(path))
        ]
        detected = bool(versions)
        return {
            "detected": detected,
            # Cache presence proves files, not activation or persisted hook trust.
            "configured": None if detected else False,
            "version": versions[-1] if versions else None,
            "authorization": "unknown",
            "hook_trust": "review_required" if detected else "unknown",
            "evidence": ["adapter_cache_detected"] if detected else [],
        }

    def _cursor_adapter(self) -> dict[str, Any]:
        manifests = list(
            _bounded_manifests(self._home / ".cursor/plugins", "plugin.json")
        )
        matches: list[tuple[Path, str | None]] = []
        for path in manifests:
            value = _safe_json(path)
            if value and value.get("name") == "matrx-cursor-plugin":
                matches.append((path, _manifest_version(path)))
        detected = bool(matches)
        configured: bool | None = False
        evidence: list[str] = []
        if detected:
            configured = any("local" in path.parts for path, _ in matches) or None
            evidence = ["host_plugin_files_detected"]
        return {
            "detected": detected,
            "configured": configured,
            "version": matches[-1][1] if matches else None,
            "authorization": "unknown",
            "hook_trust": "unknown",
            "evidence": evidence,
        }

    def _vscode_adapter(self) -> dict[str, Any]:
        matches: list[str | None] = []
        root = self._home / ".vscode/extensions"
        try:
            packages = list(root.glob("*/package.json"))[:500]
        except OSError:
            packages = []
        for path in packages:
            value = _safe_json(path)
            if (
                value
                and value.get("publisher") == "aimatrx"
                and value.get("name") == "aimatrx"
            ):
                matches.append(_manifest_version(path))
        detected = bool(matches)
        return {
            "detected": detected,
            "configured": True if detected else False,
            "version": matches[-1] if matches else None,
            "authorization": "unknown",
            "hook_trust": "not_applicable",
            "evidence": ["host_extension_registry"] if detected else [],
        }

    def _adapter(self, provider: str) -> dict[str, Any]:
        return {
            "claude_code": self._claude_adapter,
            "codex": self._codex_adapter,
            "cursor": self._cursor_adapter,
            "vscode": self._vscode_adapter,
        }[provider]()

    def _spool(self, provider: str, now: datetime) -> dict[str, Any]:
        if provider == "codex":
            data_root = self._home / ".codex/plugins/data"
            roots = (
                [
                    path / "coding-session-bridge"
                    for path in data_root.glob("matrx-codex-plugin-*")
                    if path.is_dir()
                ]
                if data_root.is_dir()
                else []
            )
            return _spool_status(roots, now)
        if provider == "cursor":
            roots = [
                self._home / ".matrx/plugins/matrx-cursor-plugin/coding-session-bridge"
            ]
            configured_root = (
                self._matrx_home / "plugins/matrx-cursor-plugin/coding-session-bridge"
            )
            if configured_root not in roots:
                roots.append(configured_root)
            return _spool_status(roots, now)
        return _no_spool()

    @staticmethod
    def _activity(provider: str, delivery: dict[str, Any] | None) -> dict[str, Any]:
        providers = delivery.get("providers") if isinstance(delivery, dict) else None
        state = providers.get(provider) if isinstance(providers, dict) else None
        enqueue = state.get("last_enqueue") if isinstance(state, dict) else None
        acknowledgement = (
            state.get("last_acknowledgement") if isinstance(state, dict) else None
        )
        local_at = _utc(enqueue.get("at") if isinstance(enqueue, dict) else None)
        cloud_at = _utc(
            acknowledgement.get("at") if isinstance(acknowledgement, dict) else None
        )
        candidates = [
            {"kind": "local_enqueue", "at": local_at} if local_at else None,
            {"kind": "cloud_acknowledgement", "at": cloud_at} if cloud_at else None,
        ]
        present = [item for item in candidates if item is not None]
        return {
            "last_local_enqueue_at": local_at,
            "last_cloud_acknowledgement_at": cloud_at,
            "most_recent": max(present, key=lambda item: item["at"])
            if present
            else None,
        }

    @staticmethod
    def _actions(
        provider: str, product: dict[str, Any], adapter: dict[str, Any]
    ) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        if product["installed"] is not True:
            actions.append(
                {
                    "id": f"open_{provider}_product_setup",
                    "label": f"Set up {_DISPLAY_NAMES[provider]}",
                    "kind": "guided_instruction",
                    "instruction": f"Install {_DISPLAY_NAMES[provider]} using its official setup, then refresh this check.",
                }
            )
        if provider == "claude_code":
            if not adapter["detected"]:
                instruction = "In Claude Code, add the AI Matrx marketplace and install matrx@matrx, then refresh."
                action_id = "open_claude_adapter_setup"
                label = "Set up AI Matrx in Claude Code"
            else:
                instruction = "In Claude Code, run /matrx:health. If it asks for authorization, open /mcp and complete the plugin-scoped AI Matrx connection."
                action_id = "test_claude_connection"
                label = "Test in Claude Code"
            actions.append(
                {
                    "id": action_id,
                    "label": label,
                    "kind": "guided_instruction",
                    "instruction": instruction,
                }
            )
        elif provider == "codex":
            if not adapter["detected"]:
                actions.append(
                    {
                        "id": "open_codex_adapter_setup",
                        "label": "Set up AI Matrx in Codex",
                        "kind": "guided_instruction",
                        "instruction": "Install matrx-codex-plugin from the official AI Matrx Codex marketplace, then refresh.",
                    }
                )
            else:
                actions.append(
                    {
                        "id": "review_codex_hook_trust",
                        "label": "Review hook trust",
                        "kind": "guided_instruction",
                        "instruction": "Open an interactive Codex session, run /hooks, and review the AI Matrx hook trust state. This app cannot safely infer that state from cache files.",
                    }
                )
        elif provider == "cursor":
            actions.append(
                {
                    "id": "open_cursor_adapter_setup"
                    if not adapter["detected"]
                    else "test_cursor_delivery",
                    "label": "Open Cursor setup"
                    if not adapter["detected"]
                    else "Test from Cursor",
                    "kind": "guided_instruction",
                    "instruction": (
                        "The AI Matrx Cursor adapter is not published for automatic installation. Open its official setup guidance; this app will not install a private development artifact."
                        if not adapter["detected"]
                        else "Start one supported Cursor agent turn, then refresh to inspect upstream and cloud delivery evidence."
                    ),
                }
            )
        else:
            actions.append(
                {
                    "id": "open_vscode_adapter_setup"
                    if not adapter["detected"]
                    else "test_vscode_connection",
                    "label": "Open VS Code setup"
                    if not adapter["detected"]
                    else "Check connection in VS Code",
                    "kind": "guided_instruction",
                    "instruction": (
                        "The AI Matrx VS Code extension is pre-release and is not silently installed by this app. Obtain it through the official signed release process."
                        if not adapter["detected"]
                        else "In the VS Code Command Palette, run AI Matrx: Check Connection."
                    ),
                }
            )
        return actions

    async def status(
        self, delivery_status: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        current = self._now()
        now = (
            current if current.tzinfo is not None else current.replace(tzinfo=UTC)
        ).astimezone(UTC)
        try:
            processes: set[str] | None = self._process_probe()
        except (
            Exception
        ):  # A process inventory is optional evidence, never a route outage.
            processes = None
        executable_paths: dict[str, str | None] = {}
        for provider in _PROVIDER_ORDER:
            executable_paths[provider] = next(
                (
                    path
                    for name in _EXECUTABLE_NAMES[provider]
                    if (path := self._which(name)) is not None
                ),
                None,
            )

        async def product(provider: str) -> dict[str, Any]:
            executable = executable_paths[provider]
            app_name = _MAC_APP_NAMES.get(provider)
            app = self._applications / app_name if app_name else None
            app_installed = bool(app and app.is_dir())
            running = (
                bool(_PROCESS_NAMES[provider] & processes)
                if processes is not None
                else None
            )
            version = await self._version_probe(executable) if executable else None
            if version is None and app_installed and app is not None:
                version = _app_version(app)
            evidence: list[str] = []
            if executable:
                evidence.append("cli_executable_detected")
            if app_installed:
                evidence.append("application_bundle_detected")
            if running is True:
                evidence.append("exact_product_process_detected")
            return {
                # Known evidence can prove installation. Its absence cannot
                # prove uninstall when a host uses a nonstandard location.
                "installed": True
                if executable or app_installed or running is True
                else None,
                "version": version,
                "running": running,
                "evidence": evidence,
            }

        product_states = await asyncio.gather(
            *(product(provider) for provider in _PROVIDER_ORDER)
        )
        providers: dict[str, Any] = {}
        for provider, product_state in zip(
            _PROVIDER_ORDER, product_states, strict=True
        ):
            adapter = self._adapter(provider)
            spool = self._spool(provider, now)
            activity = self._activity(provider, delivery_status)
            if activity["last_cloud_acknowledgement_at"]:
                detail = "A prior cloud acknowledgement is proven; it does not prove a current live connection."
                connection_evidence = ["prior_cloud_acknowledgement"]
            elif spool["supported"] and any(
                (spool["pending"], spool["in_flight"], spool["temporary"])
            ):
                detail = "Undelivered adapter files are present; no current live connection is proven."
                connection_evidence = ["undelivered_upstream_files"]
            elif activity["last_local_enqueue_at"]:
                detail = "A prior local enqueue is proven; no cloud acknowledgement or current live connection is proven."
                connection_evidence = ["prior_local_enqueue"]
            else:
                detail = "No safe local probe proves a current live connection."
                connection_evidence = []
            providers[provider] = {
                "provider": provider,
                "display_name": _DISPLAY_NAMES[provider],
                "product": product_state,
                "adapter": adapter,
                "upstream_spool": spool,
                "activity": activity,
                "connection": {
                    "state": "unverified",
                    "detail": detail,
                    "evidence": connection_evidence,
                },
                "actions": self._actions(provider, product_state, adapter),
            }
        return {
            "schema_version": 1,
            "generated_at": _utc(now),
            "providers": providers,
        }


_facade: ProviderReadinessFacade | None = None


def get_provider_readiness() -> ProviderReadinessFacade:
    global _facade
    if _facade is None:
        _facade = ProviderReadinessFacade()
    return _facade


__all__ = ["ProviderReadinessFacade", "get_provider_readiness"]
