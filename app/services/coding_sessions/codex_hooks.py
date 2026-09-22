"""Will the host dispatch the Codex hook this capture lives inside?

Lane CS-35. Everything AI Matrx knows about a Codex session — the mirrored
conversation and the declaration of what the session WROTE — is produced by
the matrx-codex-plugin running inside a Codex HOOK. So every reason the HOST
never dispatched that hook is a reason the Artifacts panel is empty, and each
one has a different remedy:

1. ``features.hooks = false`` in ``$CODEX_HOME/config.toml``. Codex dispatches
   no hook at all; no plugin version and no number of turns can help. The
   remedy is one command, ``codex features enable hooks``.
2. The plugin is not installed. Install it.
3. The plugin is installed, but the emitter copy Codex actually EXECUTES for
   that install was never written — the plugin's launcher copies
   ``hooks/emit.py`` into ``PLUGIN_DATA/telemetry-runtime/<key>.py`` at
   ``SessionStart``, keyed by a hash of the install root, so a missing copy
   proves the telemetry hook has not run once since that version arrived
   (untrusted hooks, or a host that has not started an interactive session
   since the upgrade). Remedy: the one-time ``/hooks`` trust review.
4. Everything is in place and no turn has happened yet. Run one.

Before this module all four produced ONE sentence — "Install or update that
plugin and run one Codex turn" — which is precisely what the person had
already done (verifier note V-CS-34). That is the silent-failure law inverted:
a stand-in remedy that names the wrong action sends them round the same loop.

**Measured, not assumed.** On ``codex-cli 0.155.0-alpha.9.2`` (2026-09-21,
fresh ``CODEX_HOME``) ``hooks`` is a STABLE feature whose effective default is
``true``: ``codex features list`` prints ``hooks  stable  true`` with no config
at all. So an ABSENT key is never read here as disabled — the default lives in
the Codex binary, and only an explicit ``false`` is a blocker this engine will
name. Asking the CLI for the effective value costs a subprocess against a
234 MB binary (11 s cold on this Mac), so that read belongs to the plugin's own
``scripts/preflight_hooks.py`` at install time, never to an engine tick.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger

logger = get_logger()

FEATURE = "hooks"
CONFIG_NAME = "config.toml"
#: The installed plugin, as Codex lays it out:
#: ``plugins/cache/<marketplace>/matrx-codex-plugin/<version>``.
PLUGIN_CACHE_GLOB = "plugins/cache/*/matrx-codex-plugin/*"
PLUGIN_MANIFEST = ".codex-plugin/plugin.json"
#: Where the plugin's launcher keeps the emitter copy it executes.
RUNTIME_DIRECTORY = "telemetry-runtime"
PLUGIN_DATA_GLOB = "plugins/data/matrx-codex-plugin-*"
_MAX_CONFIG_BYTES = 8 * 1024 * 1024

CODE_DISABLED = "codex_hooks_feature_disabled"
CODE_NOT_INSTALLED = "codex_plugin_not_installed"
CODE_NEVER_RAN = "codex_hook_never_ran"
CODE_READY = "codex_hooks_ready"
CODE_INSTALL_UNREADABLE = "codex_plugin_install_unreadable"

_ENABLE_REMEDY = (
    "Run `codex features enable hooks` in a terminal, then run one Codex turn. "
    "(The plugin's own check does it for you: python3 scripts/preflight_hooks.py)"
)
_TRUST_REMEDY = (
    "Open an interactive Codex session on this Mac and run /hooks, then approve the "
    "AI Matrx hooks. Codex only offers that review inside an interactive session, so a "
    "host that has only run `codex exec` never sees it."
)


def read_config_feature(home: Path, feature: str = FEATURE) -> bool | None:
    """An EXPLICIT ``[features] <feature> = true|false``, else ``None``.

    ``None`` means the config says nothing, which is NOT "disabled": the
    default for an unset flag lives in the Codex binary. Guessing either way
    here would either hide a real blocker or invent one.
    """
    path = home / CONFIG_NAME
    try:
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            return None
        text = path.read_text(errors="replace")
    except OSError:
        return None
    try:
        parsed = tomllib.loads(text)
    except (ValueError, TypeError) as exc:
        logger.info("[codex_hooks] %s is not readable TOML: %s", path, exc)
        return None
    features = parsed.get("features")
    if not isinstance(features, dict):
        return None
    value = features.get(feature)
    return value if isinstance(value, bool) else None


def runtime_copy_key(plugin_root: Path) -> str:
    """The plugin launcher's own key: ``sha256(<install root>)[:24]``.

    Kept byte-identical to ``hooks/hooks.json``'s embedded launcher in
    matrx-codex-plugin. If that rule ever changes, this engine would silently
    start reporting "never ran" for a healthy host, so the plugin's own
    contract test and this module's guard both pin it.
    """
    return hashlib.sha256(str(plugin_root).encode()).hexdigest()[:24]


def installed_plugin_roots(home: Path) -> list[Path]:
    """Every installed matrx-codex-plugin version root, oldest path first."""
    try:
        candidates = sorted(home.glob(PLUGIN_CACHE_GLOB))
    except OSError:
        return []
    return [path for path in candidates if (path / PLUGIN_MANIFEST).is_file()]


def _plugin_data_roots(home: Path) -> list[Path]:
    try:
        return [path for path in home.glob(PLUGIN_DATA_GLOB) if path.is_dir()]
    except OSError:
        return []


def _has_runtime_copy(home: Path, plugin_root: Path) -> bool:
    key = runtime_copy_key(plugin_root)
    for data in _plugin_data_roots(home):
        if (data / RUNTIME_DIRECTORY / f"{key}.py").is_file():
            return True
    return False


def hook_dispatch_state(home: Path) -> dict[str, Any]:
    """What this host will actually do with the plugin's hooks, and what to do.

    Returns one record with ``dispatches`` (``True``/``False``/``None`` for
    unknown), a stable ``code``, and a plain-English ``message`` and ``remedy``
    the desktop and the artifacts panel both print verbatim.
    """
    explicit = read_config_feature(home)
    roots = installed_plugin_roots(home)
    versions = [root.name for root in roots]
    record: dict[str, Any] = {
        # No paths: this record is rendered into the desktop's readiness
        # payload, which deliberately carries no local filesystem paths.
        "feature": FEATURE,
        "config_explicit": explicit,
        "plugin_versions": versions,
        "runtime_copy_present": None,
        "dispatches": None,
        "code": CODE_READY,
        "message": "",
        "remedy": "",
    }
    if explicit is False:
        record.update(
            dispatches=False,
            code=CODE_DISABLED,
            message=(
                "Codex hook dispatch is turned off on this Mac (`features.hooks = false` "
                "in the Codex config), so the AI Matrx plugin for Codex never runs: no "
                "Codex session is mirrored and nothing a Codex session writes is kept, "
                "however many turns you run."
            ),
            remedy=_ENABLE_REMEDY,
        )
        return record
    if not roots and _plugin_data_roots(home):
        # Codex prunes its plugin CACHE on some updates while the plugin's own
        # data directory survives, and that directory exists only because the
        # hook once ran. Claiming "not installed" here would be a confident
        # wrong answer, so this is honestly unknown and blocks nothing.
        record.update(
            dispatches=None,
            code=CODE_INSTALL_UNREADABLE,
            message=(
                "The AI Matrx plugin for Codex has run on this Mac before, but its "
                "installed files are not where Codex keeps them, so which version is "
                "active cannot be read."
            ),
            remedy=(
                "Reinstall it in Codex (`codex plugin add matrx-codex-plugin@ai-matrx`) "
                "if nothing new is captured."
            ),
        )
        return record
    if not roots:
        record.update(
            dispatches=False,
            code=CODE_NOT_INSTALLED,
            message=(
                "The AI Matrx plugin for Codex is not installed on this Mac, and it is "
                "what records what a Codex session writes."
            ),
            remedy=(
                "Install it in Codex: `codex plugin marketplace add "
                "AI-Matrix-Engine/matrx-codex-plugin --ref main` then `codex plugin add "
                "matrx-codex-plugin@ai-matrx`."
            ),
        )
        return record
    present = any(_has_runtime_copy(home, root) for root in roots)
    record["runtime_copy_present"] = present
    if not present:
        record.update(
            dispatches=False,
            code=CODE_NEVER_RAN,
            message=(
                f"The AI Matrx plugin for Codex ({', '.join(versions)}) is installed and "
                "Codex hook dispatch is on, but its hook has not run once since that "
                "version was installed, so nothing has been captured from it."
            ),
            remedy=_TRUST_REMEDY,
        )
        return record
    record.update(
        dispatches=True,
        code=CODE_READY,
        message=(
            f"The AI Matrx plugin for Codex ({', '.join(versions)}) is installed and its "
            "hook has run on this Mac."
        ),
        remedy="",
    )
    return record


__all__ = [
    "CODE_DISABLED",
    "CODE_INSTALL_UNREADABLE",
    "CODE_NEVER_RAN",
    "CODE_NOT_INSTALLED",
    "CODE_READY",
    "FEATURE",
    "RUNTIME_DIRECTORY",
    "hook_dispatch_state",
    "installed_plugin_roots",
    "read_config_feature",
    "runtime_copy_key",
]
