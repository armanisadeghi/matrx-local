"""ONE pin rule: the sidebar-ledger extractor and this engine must agree.

Two programs decide whether a Claude Code conversation is pinned:

* ``scripts/claude_code_pins_extract.py`` — installed as
  ``~/.claude/claude-code-pins-extract.py`` and run by the machine's launchd
  session-sync agent every pass. It reads the signed-in account's starred list
  from the app's IndexedDB; the agent records it per account in
  ``~/.claude/claude-code-pin-observations.json`` and pins the ledger to the
  union of every account's list.
* ``app.services.coding_sessions.claude_session_index.LivePins`` — what this
  engine applies, reading that same observations file.

Measured 2026-09-18 on Arman's Mac: the sidebar draws pins from ONE list per
account — IndexedDB ``keyval-store`` / ``keyval`` /
``store:pin-state:dframe-starred-code`` (``fixtures/claude_starred_pin_state.json``
carries the value shape verbatim, including the partial-then-full write the app
makes on every account switch). ``isStarred`` on the index records is NOT the
pin: 206 unarchived records carried it while the sidebar showed ~48 and the
list held 56 (``local_d5542855…`` "Prompt" and ``local_08264bc8…``
"Extension vault" flagged, not pinned). The 2026-09-17 extractor used the flag
and AI Matrx grew to 285 favourites vs 56 real.

So this is the agreement guard, plus the extractor's own UNKNOWN rules: an
empty latest value, an unreadable store, or an account the app has not stated
publishes NO list — never "unpin everything".
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.services.coding_sessions.claude_session_index import read_session_index

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACTOR_PATH = REPO_ROOT / "scripts" / "claude_code_pins_extract.py"
FIXTURE = Path(__file__).parent / "fixtures" / "claude_index_records.json"
STARRED_FIXTURE = Path(__file__).parent / "fixtures" / "claude_starred_pin_state.json"

ACTIVE_ACCOUNT = "d2eb2e1d-684b-41cc-a6a8-00bac619f70c"
ACTIVE_ORG = "71840439-c44e-482f-a693-702306599d49"
OTHER_ACCOUNT = "9a49ffc8-a284-4c45-b20d-68ba8cc14930"  # arman26@gmail.com

ONE = "local_11111111-1111-1111-1111-111111111111.json"
TWO = "local_22222222-2222-2222-2222-222222222222.json"
THREE = "local_33333333-3333-3333-3333-333333333333.json"
FLAGGED = "local_44444444-4444-4444-4444-444444444444.json"  # "Prompt"
PLAIN = "local_55555555-5555-5555-5555-555555555555.json"


@pytest.fixture(scope="module")
def extractor() -> Any:
    """The installed extractor's own module, loaded from its real source file."""
    spec = importlib.util.spec_from_file_location(
        "claude_code_pins_extract", EXTRACTOR_PATH
    )
    assert spec and spec.loader, EXTRACTOR_PATH
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _versions() -> list[tuple[int, str]]:
    """Every stored version of the starred key, as the IndexedDB reader yields."""
    doc = json.loads(STARRED_FIXTURE.read_text())
    return [(v["ldb_seq_no"], v["value"]) for v in doc["versions"]]


def _record(**overrides: Any) -> dict[str, Any]:
    record = json.loads(FIXTURE.read_text())["record"]
    for key, value in overrides.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    return record


def _signed_in(tmp_path: Path, account: str = ACTIVE_ACCOUNT) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"lastKnownAccountUuid": account}))
    (tmp_path / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": account})
    )


def _tree(tmp_path: Path, flags: dict[str, bool | None]) -> Path:
    """The session-index root with one record per name in the active scope."""
    root = tmp_path / "claude-code-sessions"
    scope = root / ACTIVE_ACCOUNT / ACTIVE_ORG
    scope.mkdir(parents=True, exist_ok=True)
    for n, (name, starred) in enumerate(flags.items()):
        sid = name.removesuffix(".json")
        (scope / name).write_text(json.dumps(_record(
            sessionId=sid, cliSessionId=sid.removeprefix("local_"),
            isStarred=starred, lastFocusedAt=1789684595177 - n,
        )))
    _signed_in(tmp_path)
    return root


# The app's own statement of the 10:27 switch into the signed-in account
# (localStorage, captured 2026-09-18): the session marker and the valve.
SWITCH_MARKER = 1789752419131
SWITCH_RAW = {
    "rq-cache-confirmed-account-session": json.dumps(
        {"account": ACTIVE_ACCOUNT, "marker": str(SWITCH_MARKER)}
    ),
    f"frame-pinned-valve:{ACTIVE_ORG}:{ACTIVE_ACCOUNT}": "1789752419844",
}


def _extract(
    extractor: Any, monkeypatch: Any, root: Path, versions: Any,
    raw: dict[str, str] | None = None,
) -> dict:
    ls = SWITCH_RAW if raw is None else raw
    monkeypatch.setattr(extractor, "read_localstorage", lambda _dir: dict(ls))
    if isinstance(versions, Exception):
        def _raise(_dir: str) -> list:
            raise versions
        monkeypatch.setattr(extractor, "read_starred_values", _raise)
    else:
        monkeypatch.setattr(extractor, "read_starred_values", lambda _dir: versions)
    result = extractor.extract(
        sessions_root=str(root), leveldb_dir=str(root.parent),
        indexeddb_dir=str(root.parent),
    )
    assert result["ok"] is True
    return result


def test_the_fixture_carries_the_app_value_shape(extractor: Any) -> None:
    """If the app renames the key or the value shape, this fails first."""
    doc = json.loads(STARRED_FIXTURE.read_text())
    assert doc["key"] == extractor.STARRED_KEY == "store:pin-state:dframe-starred-code"
    value = json.loads(doc["versions"][-1]["value"])
    assert isinstance(value["state"]["starredIds"], list)
    assert isinstance(value["updatedAt"], int)


def test_the_latest_value_wins_over_the_account_switch_empty_write(
    extractor: Any,
) -> None:
    """At a switch the app writes a PARTIAL list, then the full one (10:27:00.245
    then .786, measured 2026-09-18); the latest by ``updatedAt`` is truth."""
    latest = extractor.latest_starred(_versions())
    assert latest["updated_at"] == 1789752420786
    local, cloud = extractor.split_starred(latest["ids"])
    assert local == [ONE, TWO, THREE]
    assert cloud == ["session_01AAAAAAAAAAAAAAAAAAAAAA", "session_01BBBBBBBBBBBBBBBBBBBBBB"]
    # Order of arrival is irrelevant: updatedAt decides, not the read order.
    assert extractor.latest_starred(list(reversed(_versions())))["updated_at"] == 1789752420786


def test_an_empty_latest_value_is_unknown_never_unpin_everything(
    extractor: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """GUARD (c): an empty latest value publishes nothing."""
    root = _tree(tmp_path, {ONE: True, TWO: False})
    empty_latest = [
        *_versions(),
        (9999, json.dumps({"state": {"starredIds": []}, "version": 0,
                           "updatedAt": 1789752499999})),
    ]
    caught_mid_switch = empty_latest
    result = _extract(extractor, monkeypatch, root, caught_mid_switch)
    assert "app_starred" not in result
    assert "pin_states" not in result, "an empty list was published as unpins"
    assert "empty" in result["pin_note"]


def test_isstarred_is_not_the_pin_and_the_list_order_is_the_rank(
    extractor: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """GUARDS (a) and (b), at the extractor."""
    root = _tree(tmp_path, {ONE: None, TWO: False, THREE: True, FLAGGED: True, PLAIN: None})
    result = _extract(extractor, monkeypatch, root, _versions())
    assert result["pin_source"] == "app_starred"
    assert result["app_starred"]["account"] == ACTIVE_ACCOUNT
    assert result["app_starred"]["local"] == [ONE, TWO, THREE]
    assert result["pin_states"] == {
        ONE: True, TWO: True, THREE: True, FLAGGED: False, PLAIN: False,
    }
    assert result["pinned"] == {ONE: 0, TWO: 1, THREE: 2}
    assert result["counts"]["diag_isStarred_true_not_starred"] == 1


def test_extractor_and_engine_agree_on_every_conversation(
    extractor: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """The observation the extractor publishes is exactly what the engine pins."""
    root = _tree(tmp_path, {ONE: None, TWO: False, THREE: True, FLAGGED: True, PLAIN: None})
    result = _extract(extractor, monkeypatch, root, _versions())
    starred = result["app_starred"]
    # What the session-sync agent writes for the signed-in account.
    # What the session-sync agent's master holds once coverage is complete:
    # this list pinned, every other conversation proved unpinned.
    Path(os.environ["CLAUDE_PIN_OBSERVATIONS"]).write_text(json.dumps({
        "accounts": {starred["account"]: {"local": starred["local"], "cloud": []}},
        "master": {
            "pinned": starred["local"],
            "unpinned": [n for n, p in result["pin_states"].items() if not p],
        },
    }))
    entries, _ = read_session_index(root, ledger_path=root / "no-ledger.json")
    engine = {e.record_paths[0].name: (e.is_pinned, e.pinned_rank) for e in entries.values()}
    published = {
        name: (pinned, result["pinned"].get(name))
        for name, pinned in result["pin_states"].items()
    }
    assert engine == published


def test_a_list_not_newer_than_the_switch_marker_is_unknown(
    extractor: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """R-P1 attribution race: a list older than the switch is the OLD account's.

    A pass landing between the app's switch and its refill would otherwise
    file the previous account's list under the new account.
    """
    root = _tree(tmp_path, {ONE: True})
    later_switch = {
        **SWITCH_RAW,
        "rq-cache-confirmed-account-session": json.dumps(
            {"account": ACTIVE_ACCOUNT, "marker": "1789752500000"}
        ),
    }
    result = _extract(extractor, monkeypatch, root, _versions(), raw=later_switch)
    assert "app_starred" not in result
    assert "pin_states" not in result
    assert "previous account" in result["pin_note"]
    # And the valve alone is enough when it is the newer marker.
    valve_later = {f"frame-pinned-valve:{ACTIVE_ORG}:{ACTIVE_ACCOUNT}": "1789752500000"}
    result = _extract(extractor, monkeypatch, root, _versions(), raw=valve_later)
    assert "app_starred" not in result
    # A list written after the switch is accepted and carries the marker.
    result = _extract(extractor, monkeypatch, root, _versions())
    assert result["app_starred"]["switch_marker_at"] == 1789752419844


def test_a_session_marker_naming_another_account_is_unknown(
    extractor: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    root = _tree(tmp_path, {ONE: True})
    mid_switch = {"rq-cache-confirmed-account-session": json.dumps(
        {"account": OTHER_ACCOUNT, "marker": "1789752419131"}
    )}
    result = _extract(extractor, monkeypatch, root, _versions(), raw=mid_switch)
    assert "app_starred" not in result
    assert "mid-switch" in result["pin_note"]


def test_an_account_the_app_has_not_stated_publishes_no_list(
    extractor: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    root = _tree(tmp_path, {ONE: True})
    (tmp_path / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": OTHER_ACCOUNT})
    )  # the app's own files now disagree
    result = _extract(extractor, monkeypatch, root, _versions())
    assert "app_starred" not in result
    assert "pin_states" not in result
    assert "UNKNOWN" in result["pin_note"]


def test_an_unreadable_store_is_unknown_and_says_why(
    extractor: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    root = _tree(tmp_path, {ONE: True})
    result = _extract(extractor, monkeypatch, root, OSError("copy failed"))
    assert "app_starred" not in result
    assert "pin_states" not in result
    assert "could not be read" in result["pin_note"]
    assert "copy failed" in result["pin_note"]


def test_the_extractor_carries_no_second_copy_of_the_scope_rule() -> None:
    """One rule, one file — a drift guard, not a style check.

    The whole class of bug this lane closed was TWO readers each holding their
    own scope resolution: fixing one left the other publishing the wrong
    account's sidebar, and the engine reads the extractor's ledger at load. So
    the extractor may not define the rule, only import it.
    """
    source = EXTRACTOR_PATH.read_text()
    assert "def resolve_active_scope" not in source
    assert "def decide_scope" not in source
    assert "def signed_in_account" not in source
    assert "def active_index_scope" not in source
    # and it must not re-derive the org ranking either
    assert "lastFocusedAt" not in source.split('"""', 2)[-1], (
        "the extractor is reading the focus stamp itself again"
    )
    assert "from claude_scope import" in source
    assert "SCOPE_IMPORT_ERROR" in source


def test_the_installer_ships_the_rule_next_to_the_script() -> None:
    """A partial install must be impossible to get silently wrong.

    The extractor imports ``claude_scope`` from its own directory, so the
    installer has to place the module too — and BEFORE the script, so a
    half-finished install never leaves a script that cannot find its rule.
    """
    installer = EXTRACTOR_PATH.parent / "install_pins_extractor.sh"
    text = installer.read_text()
    assert installer.stat().st_mode & 0o111, "installer is not executable"
    module_at = text.index("claude_scope.py")
    script_at = text.index("claude-code-pins-extract.py")
    assert module_at < script_at, "the module must be installed before the script"
    assert "bak-" in text, "an install that replaces a live script must back it up"


def test_a_missing_scope_rule_publishes_no_verdict_and_says_why(
    extractor: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """Nothing fails silently: no rule = no pin verdict, with the remedy."""
    root = _tree(tmp_path, {ONE: True})
    monkeypatch.setattr(
        extractor,
        "SCOPE_IMPORT_ERROR",
        "the shared scope rule (claude_scope.py) is not importable",
    )
    monkeypatch.setattr(extractor, "read_localstorage", lambda _dir: {})
    monkeypatch.setattr(extractor, "read_starred_values", lambda _dir: _versions())
    result = extractor.extract(
        sessions_root=str(root), leveldb_dir=str(tmp_path), indexeddb_dir=str(tmp_path)
    )
    assert result["ok"] is True
    assert "pin_states" not in result, "a verdict was published with no rule"
    assert "app_starred" not in result
    assert "claude_scope.py" in result["pin_note"]
