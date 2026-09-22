"""Will the host dispatch the Cursor hook this capture lives inside?

Lane CS-35, Cursor half. Everything AI Matrx knows about a Cursor session is
produced by ``matrx-cursor-plugin``'s ``hooks/emit.py``, which Cursor runs as a
command hook (``python3 "${CURSOR_PLUGIN_ROOT}/hooks/emit.py"``). The plugin's
contract makes every failure inside that hook silent on purpose — no stdout, no
stderr, and a private log that records only an exception class, so observability
can never block a coding session. The cost was that a host which never
dispatched the hook at all produced nothing and said nothing, which is the same
defect the Codex half carried (verifier note V-CS-34): the person is told to
install a plugin they already installed.

So this module answers the one question that matters, from facts that can
really be read on this Mac, and names a different remedy for each answer:

1. No install trace anywhere. Cursor lays a marketplace install out as
   ``~/.cursor/plugins/cache/<marketplace>/<plugin>/<ref>/.cursor-plugin/plugin.json``
   (measured 2026-09-21: eleven ``cursor-public`` plugins in exactly that shape)
   and a development install as ``~/.cursor/plugins/local/<name>``.
2. Installed, but the plugin's own storage does not exist. The emitter creates
   ``~/.matrx/plugins/matrx-cursor-plugin/coding-session-bridge/pending`` the
   first time it runs, so its absence proves the hook has never run once.
3. Installed, storage present, but no run receipt — honestly unknown, and it
   blocks nothing: either no turn has happened since a release that records
   one, or an older release created the storage.
4. A run receipt. The only positive proof, and the reason it exists.

**The switch Cursor does not have.** Codex gates hooks behind a ``features``
flag and a per-entry trust hash, both readable offline, so its engine module can
say "dispatch is off" with evidence. Cursor exposes NO equivalent: there is no
hooks flag in ``~/.cursor/cli-config.json``, no ``~/.cursor/hooks.json``, and no
trust key anywhere in Cursor desktop's ``state.vscdb`` (searched 2026-09-21 on
Cursor Agent CLI ``2026.09.18-9a7762b``). The only thing the desktop records is
``cursor.plugins.installedIds.<team>|<workspace>`` — opaque numeric marketplace
IDs, per workspace, inside a live 27 GB SQLite database that carries no plugin
name and must not be copied or opened on an engine tick. That is why "the host
will dispatch" is proven here by a hook that DID run, and never asserted from a
switch. Nothing in this module guesses.

**No paths, ever.** This record is rendered into the desktop's readiness
payload, which deliberately carries no local filesystem path; the guard in
``tests/unit/test_coding_session_provider_readiness.py`` asserts it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLUGIN_NAME = "matrx-cursor-plugin"
PLUGIN_MANIFEST = ".cursor-plugin/plugin.json"
#: The two real install shapes, measured on this Mac.
CACHE_GLOB = "plugins/cache/*/*/*"
LOCAL_GLOB = "plugins/local/*"
#: Written by the emitter after every recognized hook event.
STORAGE_SUFFIX = Path("plugins") / PLUGIN_NAME / "coding-session-bridge"
RECEIPT_NAME = "last-hook.json"
_MAX_MANIFEST_BYTES = 1_000_000
_MAX_ROOTS = 200

CODE_NOT_INSTALLED = "cursor_plugin_not_installed"
CODE_NEVER_RAN = "cursor_hook_never_ran"
CODE_NO_TURN_YET = "cursor_hook_no_turn_yet"
CODE_READY = "cursor_hooks_ready"

_INSTALL_REMEDY = (
    "In Cursor, add the AI Matrx plugin and enable it for this workspace, then run one "
    "Cursor agent turn. (Until it is published, load it with `cursor-agent --plugin-dir "
    "<the matrx-cursor-plugin folder>`.)"
)
#: Deliberately does NOT contain the word "install": this state is the one the
#: person reaches AFTER installing, and repeating that instruction is the defect.
_NEVER_RAN_REMEDY = (
    "Open a Cursor window on the workspace where the AI Matrx plugin is enabled and run "
    "one agent turn. Cursor runs a plugin's hooks only in a workspace that has the "
    "plugin enabled, so a plugin enabled for one project captures nothing in another."
)
_NO_TURN_REMEDY = (
    "Run one Cursor agent turn, then refresh. If nothing is captured after that, the "
    "plugin is enabled for a different workspace than the one you are working in."
)


def _manifest_name_and_version(root: Path) -> tuple[str | None, str | None]:
    path = root / PLUGIN_MANIFEST
    try:
        if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
            return None, None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(value, dict):
        return None, None
    name = value.get("name")
    version = value.get("version")
    return (
        name if isinstance(name, str) else None,
        version if isinstance(version, str) and version else None,
    )


def installed_plugin_roots(cursor_home: Path) -> list[tuple[Path, str | None]]:
    """Every install of this plugin Cursor can load, with its version."""
    found: list[tuple[Path, str | None]] = []
    for pattern in (CACHE_GLOB, LOCAL_GLOB):
        try:
            candidates = sorted(cursor_home.glob(pattern))[:_MAX_ROOTS]
        except OSError:
            continue
        for root in candidates:
            name, version = _manifest_name_and_version(root)
            if name == PLUGIN_NAME:
                found.append((root, version))
    return found


def storage_roots(roots: Iterable[Path]) -> list[Path]:
    """The plugin storage directories, from every home that could hold one.

    The emitter writes under the REAL ``~/.matrx`` unless
    ``MATRX_CURSOR_PLUGIN_DATA`` overrides it, so a host with a configured
    ``MATRX_HOME_DIR`` elsewhere still has its receipts in the default place.
    Both are read; neither is assumed.
    """
    seen: list[Path] = []
    for root in roots:
        candidate = root / STORAGE_SUFFIX
        if candidate not in seen:
            seen.append(candidate)
    return seen


def read_run_receipt(roots: Iterable[Path]) -> dict[str, Any] | None:
    """The newest readable run receipt, or ``None``.

    A receipt that cannot be parsed is treated as absent rather than as a
    failure: it proves nothing about dispatch either way.
    """
    best: dict[str, Any] | None = None
    for storage in storage_roots(roots):
        path = storage / RECEIPT_NAME
        try:
            if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        at = value.get("at")
        if not isinstance(at, (int, float)) or isinstance(at, bool):
            continue
        if best is None or at > best["at"]:
            best = value
    return best


def _storage_exists(roots: Iterable[Path]) -> bool:
    return any(storage.is_dir() for storage in storage_roots(roots))


def _iso(at: float) -> str | None:
    try:
        return (
            datetime.fromtimestamp(at, tz=UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except (OSError, OverflowError, ValueError):
        return None


def hook_dispatch_state(
    cursor_home: Path, matrx_homes: Iterable[Path]
) -> dict[str, Any]:
    """What this host will actually do with the plugin's hooks, and what to do.

    Returns one record with ``dispatches`` (``True``/``False``/``None`` for
    honestly unknown), a stable ``code``, and a plain-English ``message`` and
    ``remedy`` the desktop prints verbatim. It carries no filesystem path.
    """
    homes = list(matrx_homes)
    installs = installed_plugin_roots(cursor_home)
    versions = [version for _, version in installs if version]
    receipt = read_run_receipt(homes)
    record: dict[str, Any] = {
        "feature": "cursor_hooks",
        "plugin_versions": versions,
        "ran_at": None,
        "ran_hook": None,
        "ran_plugin_version": None,
        # Cursor exposes no hook enable flag and no hook trust record; see this
        # module's docstring for what was searched. Said, never guessed.
        "hook_enable_state": "not_exposed",
        "dispatches": None,
        "code": CODE_READY,
        "message": "",
        "remedy": "",
    }
    if receipt is not None:
        hook = receipt.get("hook")
        version = receipt.get("plugin_version")
        record["ran_at"] = _iso(float(receipt["at"]))
        record["ran_hook"] = hook if isinstance(hook, str) else None
        record["ran_plugin_version"] = version if isinstance(version, str) else None
        named = record["ran_plugin_version"] or (versions[-1] if versions else None)
        record.update(
            dispatches=True,
            code=CODE_READY,
            message=(
                "The AI Matrx plugin for Cursor"
                + (f" ({named})" if named else "")
                + " is running: Cursor last ran its hook on this Mac at "
                + f"{record['ran_at']}."
            ),
            remedy="",
        )
        return record
    if not installs:
        record.update(
            dispatches=False,
            code=CODE_NOT_INSTALLED,
            message=(
                "The AI Matrx plugin for Cursor is not installed on this Mac, and it is "
                "what records what a Cursor session does."
            ),
            remedy=_INSTALL_REMEDY,
        )
        return record
    if not _storage_exists(homes):
        record.update(
            dispatches=False,
            code=CODE_NEVER_RAN,
            message=(
                f"The AI Matrx plugin for Cursor ({', '.join(versions) or 'version unknown'}) "
                "is installed, but Cursor has never run its hook on this Mac, so nothing "
                "has been captured from Cursor at all."
            ),
            remedy=_NEVER_RAN_REMEDY,
        )
        return record
    record.update(
        dispatches=None,
        code=CODE_NO_TURN_YET,
        message=(
            f"The AI Matrx plugin for Cursor ({', '.join(versions) or 'version unknown'}) "
            "is installed and has stored something on this Mac before, but no Cursor turn "
            "has been captured since it was last updated."
        ),
        remedy=_NO_TURN_REMEDY,
    )
    return record


__all__ = [
    "CODE_NEVER_RAN",
    "CODE_NOT_INSTALLED",
    "CODE_NO_TURN_YET",
    "CODE_READY",
    "PLUGIN_NAME",
    "RECEIPT_NAME",
    "STORAGE_SUFFIX",
    "hook_dispatch_state",
    "installed_plugin_roots",
    "read_run_receipt",
    "storage_roots",
]
