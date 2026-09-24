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
3. Codex has the hooks registered and REFUSES to run them, because hook trust
   is granted per entry and is invalidated the moment that entry's definition
   changes. This was the live state of this Mac on 2026-09-21: plugin
   0.2.0-alpha.12 installed and enabled, ``features.hooks`` on, all nine hooks
   in Codex's registry, and eight of them ``trustStatus: "modified"`` — only
   the git guard (not capture) was trusted. Nothing had been captured since
   2026-08-30 and no screen said a word. The decision is readable offline:
   ``[hooks.state."<key>"].trusted_hash`` in ``$CODEX_HOME/config.toml`` is
   exactly what Codex compares, and the installed plugin ships the hashes its
   own hooks produce in ``hooks/trusted-hashes.json`` (path- and
   version-independent, so an unchanged ``hooks.json`` keeps a host's trust).
   Remedy: the interactive ``/hooks`` review — approval to execute code, which
   no agent and no engine may grant on the person's behalf.
4. The plugin is installed, but the emitter copy Codex actually EXECUTES for
   that install was never written — the plugin's launcher copies
   ``hooks/emit.py`` into ``PLUGIN_DATA/telemetry-runtime/<key>.py`` at
   ``SessionStart``, keyed by a hash of the install root, so a missing copy
   proves the telemetry hook has not run once since that version arrived
   (a host that has not started an interactive session since the upgrade).
   Remedy: the one-time ``/hooks`` trust review.
5. Everything is in place and no turn has happened yet. Run one.

Before this module all of them produced ONE sentence — "Install or update that
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
import json
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
CODE_TRUST_STALE = "codex_hook_trust_stale"
CODE_NEVER_TRUSTED = "codex_hook_never_trusted"

#: Shipped by the plugin; the hashes Codex computes for ITS hook entries.
PIN_FILE = "hooks/trusted-hashes.json"
#: ``<plugin id>:hooks/hooks.json:<snake event>:0:0`` — only the suffix is
#: stable, because the plugin id carries the marketplace name.
_KEY_SUFFIX = "hooks/hooks.json:%s:0:0"
SNAKE_EVENTS = {
    "preToolUse": "pre_tool_use",
    "postToolUse": "post_tool_use",
    "sessionStart": "session_start",
    "sessionEnd": "session_end",
    "userPromptSubmit": "user_prompt_submit",
    "subagentStart": "subagent_start",
    "subagentStop": "subagent_stop",
    "postCompact": "post_compact",
    "stop": "stop",
}

_ENABLE_REMEDY = (
    "Run `codex features enable hooks` in a terminal, then run one Codex turn. "
    "(The plugin's own check does it for you: python3 scripts/preflight_hooks.py)"
)
_TRUST_REMEDY = (
    "Open an interactive Codex session on this Mac and run /hooks, then approve the "
    "AI Matrx hooks. Codex only offers that review inside an interactive session, so a "
    "host that has only run `codex exec` never sees it. Approving it is approval to run "
    "code, so only you can do it — AI Matrx will never do it for you."
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


def read_hook_trust(home: Path) -> dict[str, str]:
    """``[hooks.state]`` for the AI Matrx plugin, as ``event -> trusted_hash``.

    Another plugin's entries and the person's own ``hooks.json`` entries are
    ignored: a trusted hook that is not ours proves nothing about capture.
    """
    path = home / CONFIG_NAME
    try:
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            return {}
        parsed = tomllib.loads(path.read_text(errors="replace"))
    except (OSError, ValueError, TypeError):
        return {}
    table = parsed.get("hooks")
    state = table.get("state") if isinstance(table, dict) else None
    if not isinstance(state, dict):
        return {}
    found: dict[str, str] = {}
    for event, snake in SNAKE_EVENTS.items():
        suffix = _KEY_SUFFIX % snake
        for key, value in state.items():
            if not key.startswith("matrx-codex-plugin@") or not key.endswith(suffix):
                continue
            recorded = value.get("trusted_hash") if isinstance(value, dict) else None
            if isinstance(recorded, str) and recorded:
                found[event] = recorded
    return found


def read_pinned_hashes(roots: list[Path]) -> dict[str, Any]:
    """The hashes the INSTALLED plugin says its hooks produce, newest install
    first. An older build ships none, and that is honestly unknown."""
    for root in reversed(roots):
        try:
            record = json.loads((root / PIN_FILE).read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        hashes = record.get("hook_hashes")
        if isinstance(hashes, dict) and hashes:
            capture = record.get("capture_events")
            return {
                "hook_hashes": {
                    str(k): str(v) for k, v in hashes.items() if isinstance(v, str)
                },
                "capture_events": [str(e) for e in capture or [] if isinstance(e, str)],
            }
    return {"hook_hashes": {}, "capture_events": []}


def trust_state(home: Path, roots: list[Path]) -> dict[str, Any]:
    """Which of the installed plugin's hooks Codex will actually dispatch.

    ``never_reviewed`` (a fresh host), ``stale`` (approved for an OLDER version
    of these hooks — what stopped capture here), ``trusted``, or ``unknown``
    when the install ships no pin. ``capture_dispatches`` ignores the git
    guard on purpose.
    """
    pinned = read_pinned_hashes(roots)
    hashes: dict[str, str] = pinned["hook_hashes"]
    capture: list[str] = pinned["capture_events"]
    recorded = read_hook_trust(home)
    trusted = sorted(e for e, h in hashes.items() if recorded.get(e) == h)
    stale = sorted(
        e for e, h in hashes.items() if e in recorded and recorded[e] != h
    )
    untrusted = [e for e in capture if e not in trusted]
    if not hashes:
        state = "unknown"
    elif not untrusted:
        state = "trusted"
    elif any(e in stale for e in capture):
        state = "stale"
    else:
        state = "never_reviewed"
    return {
        "state": state,
        "trusted": trusted,
        "stale": stale,
        "capture_untrusted": untrusted,
        "capture_dispatches": state == "trusted",
    }


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
        "trust": {"state": "unknown"},
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
    trust = trust_state(home, roots)
    record["trust"] = trust
    if trust["state"] == "stale":
        record.update(
            dispatches=False,
            code=CODE_TRUST_STALE,
            message=(
                f"Codex has the AI Matrx hooks ({', '.join(versions)}) and will not run "
                f"{len(trust['capture_untrusted'])} of them: this Mac approved an older "
                "version of these hooks, and Codex refuses a hook whose definition "
                "changed since it was approved. Nothing from Codex is being recorded, "
                "however many turns you run."
            ),
            remedy=_TRUST_REMEDY,
        )
        return record
    if trust["state"] == "never_reviewed":
        record.update(
            dispatches=False,
            code=CODE_NEVER_TRUSTED,
            message=(
                f"Codex has the AI Matrx hooks ({', '.join(versions)}) registered and has "
                "never been asked to approve them, so it runs none of them and records "
                "nothing from Codex."
            ),
            remedy=_TRUST_REMEDY,
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
    "CODE_NEVER_TRUSTED",
    "CODE_TRUST_STALE",
    "CODE_INSTALL_UNREADABLE",
    "CODE_NEVER_RAN",
    "CODE_NOT_INSTALLED",
    "CODE_READY",
    "FEATURE",
    "RUNTIME_DIRECTORY",
    "hook_dispatch_state",
    "installed_plugin_roots",
    "read_config_feature",
    "read_hook_trust",
    "runtime_copy_key",
    "trust_state",
]
