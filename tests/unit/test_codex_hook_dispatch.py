"""Why nothing was captured — the host answer, not a generic "install it".

Lane CS-35. Codex artifacts are captured inside a Codex HOOK, so every reason
the host never dispatched that hook is a reason the Artifacts panel is empty.
Before this, all of them produced ONE sentence — "Install or update that
plugin and run one Codex turn" — which is what the person had already done
(verifier note V-CS-34). A remedy that names the wrong action is worse than
silence: it sends the person round the same loop.

The states these guards pin, each read from a real isolated ``CODEX_HOME``
laid out the way this Mac's own is (measured 2026-09-21):

* ``features.hooks = false`` in ``config.toml`` — Codex dispatches no hook at
  all, so no plugin version and no number of turns can ever help. The remedy
  is ``codex features enable hooks``. (The flag is *stable and defaults to
  true* on ``codex-cli 0.155.0-alpha.9.2``, so this is the explicit-false
  case, not a fresh-install case.)
* no plugin installed — the original sentence, which was right only here.
* plugin installed, hooks on, but the emitter copy Codex actually executes for
  that install was never written: the telemetry hook has not run once since
  that version arrived. This is the LIVE state of this Mac on 2026-09-21 —
  ``matrx-codex-plugin`` 0.2.0-alpha.12 installed, hooks enabled, and
  ``telemetry-runtime`` holding only a copy keyed to an older install root.
* plugin installed, runtime copy in place, still no declaration — then it
  really is "run one Codex turn", with hook trust as the next thing to check.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.services.coding_sessions.codex_hooks import (
    RUNTIME_DIRECTORY,
    hook_dispatch_state,
    read_config_feature,
    runtime_copy_key,
)
from app.services.coding_sessions.codex_writes import CodexRolloutSessionSource

PLUGIN_ID = "matrx-codex-plugin-ai-matrx"
VERSION = "0.2.0-alpha.12"


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "codex-home"
    (home / "sessions").mkdir(parents=True)
    return home


def _install_plugin(home: Path, *, version: str = VERSION) -> Path:
    """The real installed layout: cache/<marketplace>/<plugin>/<version>."""
    root = home / "plugins/cache/ai-matrx/matrx-codex-plugin" / version
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "matrx-codex-plugin", "version": version})
    )
    (root / "hooks").mkdir()
    (root / "hooks/emit.py").write_text("# the installed emitter\n")
    (home / "plugins/data" / PLUGIN_ID / "coding-session-bridge/pending").mkdir(
        parents=True, exist_ok=True
    )
    return root


def _write_runtime_copy(home: Path, root: Path, *, content: str | None = None) -> Path:
    """What the SessionStart launcher does: a version-keyed copy under data."""
    directory = home / "plugins/data" / PLUGIN_ID / RUNTIME_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{runtime_copy_key(root)}.py"
    target.write_text(
        content if content is not None else (root / "hooks/emit.py").read_text()
    )
    return target


# ---------------------------------------------------------------------------
# The feature flag
# ---------------------------------------------------------------------------


def test_an_absent_features_table_is_never_read_as_disabled(tmp_path: Path) -> None:
    """The default lives in the Codex binary, so an unset key is unknown, and
    this engine must not invent a blocker out of it."""
    home = _home(tmp_path)
    assert read_config_feature(home) is None
    _install_plugin(home)
    assert hook_dispatch_state(home)["code"] != "codex_hooks_feature_disabled"


def test_an_explicit_false_is_the_blocker_and_names_the_codex_command(
    tmp_path: Path,
) -> None:
    home = _home(tmp_path)
    _install_plugin(home)
    (home / "config.toml").write_text("[features]\nhooks = false\n")
    state = hook_dispatch_state(home)
    assert state["code"] == "codex_hooks_feature_disabled"
    assert "codex features enable hooks" in state["remedy"]
    assert state["dispatches"] is False


def test_hooks_true_is_reported_as_dispatching(tmp_path: Path) -> None:
    home = _home(tmp_path)
    root = _install_plugin(home)
    _write_runtime_copy(home, root)
    (home / "config.toml").write_text("[features]\nhooks = true\n")
    state = hook_dispatch_state(home)
    assert state["dispatches"] is True
    assert state["code"] == "codex_hooks_ready"


# ---------------------------------------------------------------------------
# The other two host reasons
# ---------------------------------------------------------------------------


def test_no_plugin_installed_is_its_own_code(tmp_path: Path) -> None:
    home = _home(tmp_path)
    state = hook_dispatch_state(home)
    assert state["code"] == "codex_plugin_not_installed"
    assert state["plugin_versions"] == []


def test_an_install_whose_emitter_copy_was_never_written_is_named(
    tmp_path: Path,
) -> None:
    """The live state of this Mac: installed, hooks on, hook never ran."""
    home = _home(tmp_path)
    _install_plugin(home)
    state = hook_dispatch_state(home)
    assert state["code"] == "codex_hook_never_ran"
    assert VERSION in state["message"]
    assert "/hooks" in state["remedy"]


def test_a_stale_copy_from_an_older_install_does_not_count_as_this_one(
    tmp_path: Path,
) -> None:
    home = _home(tmp_path)
    root = _install_plugin(home)
    older = home / "plugins/cache/ai-matrx/matrx-codex-plugin" / "0.2.0-alpha.9"
    _write_runtime_copy(home, root, content="stale")
    stale = (
        home
        / "plugins/data"
        / PLUGIN_ID
        / RUNTIME_DIRECTORY
        / f"{runtime_copy_key(older)}.py"
    )
    (home / "plugins/data" / PLUGIN_ID / RUNTIME_DIRECTORY / f"{runtime_copy_key(root)}.py").unlink()
    stale.write_text("an older install's emitter\n")
    state = hook_dispatch_state(home)
    assert state["code"] == "codex_hook_never_ran"


def test_an_older_cached_runtime_copy_does_not_make_a_newer_install_ready(
    tmp_path: Path,
) -> None:
    """Only the newest cached plugin can prove the currently installed hook ran."""
    home = _home(tmp_path)
    older = _install_plugin(home, version="0.2.0-alpha.9")
    _install_plugin(home, version="0.2.0-alpha.12")
    _write_runtime_copy(home, older)

    state = hook_dispatch_state(home)

    assert state["code"] == "codex_hook_never_ran"
    assert state["dispatches"] is False
    assert "0.2.0-alpha.12" in state["message"]


def test_the_runtime_copy_key_is_the_launchers_own_key(tmp_path: Path) -> None:
    """Byte-for-byte the plugin launcher's rule: sha256 of the plugin root."""
    root = tmp_path / "some/plugin/root"
    assert runtime_copy_key(root) == hashlib.sha256(str(root).encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# What the person actually reads
# ---------------------------------------------------------------------------


def test_the_silence_sentence_names_the_disabled_feature_not_the_plugin(
    tmp_path: Path,
) -> None:
    home = _home(tmp_path)
    _install_plugin(home)
    (home / "config.toml").write_text("[features]\nhooks = false\n")
    source = CodexRolloutSessionSource(home=home)
    list(source.discover())
    sentences = source.describe_gaps({})
    joined = " ".join(sentences)
    assert "codex features enable hooks" in joined
    assert "Install or update that plugin" not in joined


def test_the_silence_sentence_still_says_install_when_nothing_is_installed(
    tmp_path: Path,
) -> None:
    home = _home(tmp_path)
    source = CodexRolloutSessionSource(home=home)
    list(source.discover())
    joined = " ".join(source.describe_gaps({}))
    assert "AI Matrx plugin for Codex" in joined
    assert "codex features enable hooks" not in joined


def test_the_silence_sentence_says_the_hook_never_ran_when_that_is_true(
    tmp_path: Path,
) -> None:
    home = _home(tmp_path)
    _install_plugin(home)
    (home / "config.toml").write_text("[features]\nhooks = true\n")
    source = CodexRolloutSessionSource(home=home)
    list(source.discover())
    joined = " ".join(source.describe_gaps({}))
    assert "has not run once" in joined
    assert "Install or update that plugin" not in joined


def test_a_ready_host_with_no_turn_yet_is_told_to_run_a_turn(tmp_path: Path) -> None:
    home = _home(tmp_path)
    root = _install_plugin(home)
    _write_runtime_copy(home, root)
    (home / "config.toml").write_text("[features]\nhooks = true\n")
    source = CodexRolloutSessionSource(home=home)
    list(source.discover())
    joined = " ".join(source.describe_gaps({}))
    assert "Run one Codex turn that writes a file" in joined
    assert "codex features enable hooks" not in joined


def test_a_pruned_cache_with_surviving_data_is_unknown_not_uninstalled(
    tmp_path: Path,
) -> None:
    """Codex prunes its plugin cache on some updates. The data directory that
    survives exists only because the hook once ran, so "not installed" would be
    a confident wrong answer — and would hide a real marker blocker behind it
    in the desktop's capture health."""
    home = _home(tmp_path)
    (home / "plugins/data" / PLUGIN_ID / "coding-session-bridge/pending").mkdir(
        parents=True
    )
    state = hook_dispatch_state(home)
    assert state["code"] == "codex_plugin_install_unreadable"
    assert state["dispatches"] is None


def test_the_state_record_carries_no_filesystem_path(tmp_path: Path) -> None:
    """It is rendered into the desktop readiness payload, which carries none."""
    home = _home(tmp_path)
    _install_plugin(home)
    rendered = json.dumps(hook_dispatch_state(home))
    assert str(home) not in rendered
    assert str(tmp_path) not in rendered


# ---------------------------------------------------------------------------
# Hook TRUST — the switch that was actually off on this Mac (lane CS-35)
# ---------------------------------------------------------------------------

PINNED = {
    "preToolUse": "sha256:" + "a" * 64,
    "sessionStart": "sha256:" + "b" * 64,
    "userPromptSubmit": "sha256:" + "c" * 64,
    "stop": "sha256:" + "d" * 64,
}
SNAKE = {
    "preToolUse": "pre_tool_use",
    "sessionStart": "session_start",
    "userPromptSubmit": "user_prompt_submit",
    "stop": "stop",
}


def _pin(root: Path) -> None:
    """What the plugin ships: the hashes Codex computes for ITS hooks."""
    (root / "hooks/trusted-hashes.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "hook_hashes": PINNED,
                "capture_events": [e for e in PINNED if e != "preToolUse"],
            }
        )
    )


def _trust(home: Path, entries: dict[str, str]) -> None:
    body = ["[hooks.state]\n"]
    for event, value in entries.items():
        key = f"matrx-codex-plugin@ai-matrx:hooks/hooks.json:{SNAKE[event]}:0:0"
        body.append(f'\n[hooks.state."{key}"]\ntrusted_hash = "{value}"\n')
    with (home / "config.toml").open("a") as handle:
        handle.write("".join(body))


def test_trust_recorded_for_an_older_hooks_file_is_the_blocker(tmp_path: Path) -> None:
    """THE LIVE STATE OF THIS MAC on 2026-09-21, measured with `codex
    app-server` + `hooks/list`: the git guard trusted at its current hash and
    all eight capture hooks recorded at an older one, so Codex registered them
    and dispatched none. Before this the screen said "install the plugin"."""
    home = _home(tmp_path)
    root = _install_plugin(home)
    _write_runtime_copy(home, root)
    _pin(root)
    _trust(home, {"preToolUse": PINNED["preToolUse"], "sessionStart": "sha256:" + "0" * 64,
                  "userPromptSubmit": "sha256:" + "0" * 64, "stop": "sha256:" + "0" * 64})
    state = hook_dispatch_state(home)
    assert state["code"] == "codex_hook_trust_stale"
    assert state["dispatches"] is False
    assert "/hooks" in state["remedy"]
    assert "older version" in state["message"]


def test_hooks_never_approved_is_its_own_state(tmp_path: Path) -> None:
    home = _home(tmp_path)
    root = _install_plugin(home)
    _write_runtime_copy(home, root)
    _pin(root)
    state = hook_dispatch_state(home)
    assert state["code"] == "codex_hook_never_trusted"
    assert state["dispatches"] is False


def test_a_trusted_enforcement_hook_alone_never_counts_as_capture(tmp_path: Path) -> None:
    """The git guard is not capture. A host with only that approved records
    nothing, and reading it as healthy is exactly how this was missed."""
    home = _home(tmp_path)
    root = _install_plugin(home)
    _write_runtime_copy(home, root)
    _pin(root)
    _trust(home, {"preToolUse": PINNED["preToolUse"]})
    assert hook_dispatch_state(home)["dispatches"] is False


def test_every_hook_approved_at_the_shipped_hashes_is_ready(tmp_path: Path) -> None:
    home = _home(tmp_path)
    root = _install_plugin(home)
    _write_runtime_copy(home, root)
    _pin(root)
    _trust(home, dict(PINNED))
    state = hook_dispatch_state(home)
    assert state["code"] == "codex_hooks_ready"
    assert state["dispatches"] is True


def test_an_install_without_the_pin_file_falls_back_and_never_invents_a_blocker(
    tmp_path: Path,
) -> None:
    """An older plugin build ships no pin, so trust is unreadable — which is
    reported as unknown, never as trusted and never as broken."""
    home = _home(tmp_path)
    root = _install_plugin(home)
    _write_runtime_copy(home, root)
    state = hook_dispatch_state(home)
    assert state["trust"]["state"] == "unknown"
    assert state["code"] == "codex_hooks_ready"


def test_the_trust_record_carries_no_filesystem_path(tmp_path: Path) -> None:
    home = _home(tmp_path)
    root = _install_plugin(home)
    _write_runtime_copy(home, root)
    _pin(root)
    _trust(home, dict(PINNED))
    rendered = json.dumps(hook_dispatch_state(home))
    assert str(home) not in rendered
