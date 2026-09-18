"""ONE pin rule: the sidebar-ledger extractor and this engine must agree.

Two programs decide whether a Claude Code conversation is pinned:

* ``scripts/claude_code_pins_extract.py`` — installed as
  ``~/.claude/claude-code-pins-extract.py`` and run by the machine's launchd
  session-sync agent, which writes the canonical sidebar ledger every pass.
  Everything downstream of that ledger (rank, category, title, archive state,
  and every other consumer) believes what it says.
* ``app.services.coding_sessions.claude_session_index.LivePins`` — what this
  engine applies when it reads the app's records itself.

They drifted on 2026-09-17 and the ledger lied to every consumer: the engine
had moved to the app's own ``isStarred`` field while the extractor still
derived pins from ``pinnedOrder``, an append-only display-order array in the
app's localStorage that can only ever ADD. Measured on Arman's Mac that day:
the app showed 218 pinned, the extractor's ``pinnedOrder`` held 295 refs, and
the ledger claimed 257 — 73 pinned that were not, 34 truly-pinned missing.

So this is the agreement guard. It builds a tree of records in the shape the
app really writes (``fixtures/claude_index_records.json``, key names verbatim)
and asserts that BOTH implementations return the same verdict for every
conversation, including the two kinds of "unknown" that must never become
``false``. Either side changing its rule, or the app renaming its field, fails
here rather than quietly clearing someone's sidebar.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.coding_sessions.claude_session_index import read_session_index

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACTOR_PATH = REPO_ROOT / "scripts" / "claude_code_pins_extract.py"
FIXTURE = Path(__file__).parent / "fixtures" / "claude_index_records.json"

ACTIVE_ACCOUNT = "d2eb2e1d-684b-41cc-a6a8-00bac619f70c"
ACTIVE_ORG = "71840439-c44e-482f-a693-702306599d49"
STALE_ORG = "8fa7c825-32a9-4a72-a6d5-f88377421373"


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


def _record(**overrides: Any) -> dict[str, Any]:
    """The real record shape, so a future app rename fails here loudly."""
    record = json.loads(FIXTURE.read_text())["record"]
    for key, value in overrides.items():
        if value is _ABSENT:
            record.pop(key, None)
        else:
            record[key] = value
    return record


class _Absent:
    pass


_ABSENT = _Absent()


OTHER_ACCOUNT = "9a49ffc8-a284-4c45-b20d-68ba8cc14930"  # arman26@gmail.com


def _sessions_root(tmp_path: Path) -> Path:
    """The app's session-index root, a sibling of its own state files.

    It is not ``tmp_path`` itself: ``claude_scope`` reads the signed-in account
    from the app's plain JSON beside the index tree, so the fixture has to have
    both in their real relationship.
    """
    root = tmp_path / "claude-code-sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _signed_in(tmp_path: Path, account: str = ACTIVE_ACCOUNT) -> None:
    """Write the app's OWN statement of the account it is signed into."""
    (tmp_path / "config.json").write_text(
        json.dumps({"lastKnownAccountUuid": account})
    )
    (tmp_path / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": account})
    )


def _write(
    root: Path, *, org: str, session: str, account: str = ACTIVE_ACCOUNT, **fields: Any
) -> Path:
    scope = root / account / org
    scope.mkdir(parents=True, exist_ok=True)
    path = scope / f"local_{session}.json"
    record = _record(sessionId=f"local_{session}", cliSessionId=session, **fields)
    path.write_text(json.dumps(record))
    return path


def _extractor_scope(extractor: Any, root: Path) -> Any:
    """The extractor's scope, through the SHARED rule it imports."""
    assert extractor.SCOPE_IMPORT_ERROR is None, extractor.SCOPE_IMPORT_ERROR
    return extractor.resolve_active_scope(root)


def _extractor_verdicts(extractor: Any, root: Path) -> dict[str, bool] | None:
    resolution = _extractor_scope(extractor, root)
    if resolution.scope is None:
        return None
    return extractor.pin_verdicts(extractor.scope_pin_states(str(resolution.scope)))


def _engine_verdicts(root: Path) -> dict[str, bool | None]:
    """``local_<sessionId>.json`` -> ``is_pinned`` as this engine resolves it."""
    entries, _totals = read_session_index(root, ledger_path=root / "no-ledger.json")
    out: dict[str, bool | None] = {}
    for entry in entries.values():
        for path in entry.record_paths:
            out[path.name] = entry.is_pinned
    return out


# The cases the app really produces, and what the ONE rule says about each.
#   pinned           the app's pin, set on the signed-in scope's record
#   unpinned         the person removed the pin — an OBSERVED false
#   never-pinned     no pin key at all: an honest false in a scope that speaks
#   stale-scope-only pinned in a signed-OUT scope; that scope does not decide
CASES = {
    "11111111-1111-1111-1111-111111111111": True,   # isStarred: true
    "22222222-2222-2222-2222-222222222222": False,  # isStarred: false
    "33333333-3333-3333-3333-333333333333": False,  # no isStarred key
}
STALE_ONLY = "44444444-4444-4444-4444-444444444444"


def _build_live_tree(root: Path) -> None:
    _write(root, org=ACTIVE_ORG, session=list(CASES)[0],
           isStarred=True, lastFocusedAt=1789684595177)
    _write(root, org=ACTIVE_ORG, session=list(CASES)[1],
           isStarred=False, lastFocusedAt=1789684595100)
    _write(root, org=ACTIVE_ORG, session=list(CASES)[2],
           isStarred=_ABSENT, lastFocusedAt=1789684595000)
    # A signed-out scope that still holds a pin the person removed long ago —
    # the exact shape that grew the server's pinned list to 276.
    _write(root, org=STALE_ORG, session=list(CASES)[0],
           isStarred=False, lastFocusedAt=1700000000000)
    _write(root, org=STALE_ORG, session=STALE_ONLY,
           isStarred=True, lastFocusedAt=1700000000001)


def test_extractor_and_engine_agree_on_every_conversation(
    extractor: Any, tmp_path: Path
) -> None:
    root = _sessions_root(tmp_path)
    _build_live_tree(root)
    _signed_in(tmp_path)
    published = _extractor_verdicts(extractor, root)
    assert published is not None, "the live scope speaks; verdicts must be published"
    engine = _engine_verdicts(root)
    for session, expected in CASES.items():
        name = f"local_{session}.json"
        assert published[name] is expected, f"extractor disagrees on {name}"
        assert engine[name] is expected, f"engine disagrees on {name}"
    # The signed-out scope's record is not the signed-in account's opinion, so
    # the extractor never publishes a verdict for it.
    assert f"local_{STALE_ONLY}.json" not in published


def test_a_scope_with_no_pin_opinion_is_unknown_never_false(
    extractor: Any, tmp_path: Path
) -> None:
    """A freshly signed-in account is not a person who unpinned everything."""
    root = _sessions_root(tmp_path)
    for index, session in enumerate(CASES):
        _write(root, org=ACTIVE_ORG, session=session,
               isStarred=_ABSENT, lastFocusedAt=1789684595000 + index)
    _signed_in(tmp_path)
    assert _extractor_verdicts(extractor, root) is None
    engine = _engine_verdicts(root)
    assert set(engine.values()) == {None}, engine


def test_no_identifiable_scope_is_unknown_never_false(
    extractor: Any, tmp_path: Path
) -> None:
    """No ``lastFocusedAt`` anywhere: nothing may be concluded about a pin."""
    root = _sessions_root(tmp_path)
    for session in CASES:
        _write(root, org=ACTIVE_ORG, session=session,
               isStarred=True, lastFocusedAt=_ABSENT)
    _signed_in(tmp_path)
    resolution = _extractor_scope(extractor, root)
    assert resolution.scope is None
    assert "lastFocusedAt" in resolution.reason
    assert _extractor_verdicts(extractor, root) is None
    engine = _engine_verdicts(root)
    assert set(engine.values()) == {None}, engine


def test_the_extractor_refuses_a_scope_stolen_by_a_copied_stamp(
    extractor: Any, tmp_path: Path
) -> None:
    """THE 18:04 FAILURE, as a fixture: two accounts, one identical stamp.

    On 2026-09-17 the extractor published dev@aimatrx.com's 218 starred
    sessions as the pin truth while the app was signed into arman26@gmail.com
    (228 stars) — 21 pins that were not pinned, 31 real pins missing — because
    both readers ranked ``lastFocusedAt`` ACROSS accounts and that stamp is
    copied between scopes. Here the signed-out account holds the stamp and the
    stale pin; the extractor must publish the signed-in account's verdict, and
    the engine must agree conversation for conversation.
    """
    root = _sessions_root(tmp_path)
    shared_stamp = 1_789_714_654_476  # the real copied value
    session = list(CASES)[0]
    _write(root, org=ACTIVE_ORG, session=session,
           isStarred=False, lastFocusedAt=shared_stamp)
    _write(root, org=ACTIVE_ORG, session=session, account=OTHER_ACCOUNT,
           isStarred=True, lastFocusedAt=shared_stamp)
    _signed_in(tmp_path, ACTIVE_ACCOUNT)

    resolution = _extractor_scope(extractor, root)
    assert resolution.account == ACTIVE_ACCOUNT, resolution.reason
    published = _extractor_verdicts(extractor, root)
    assert published == {f"local_{session}.json": False}, published
    engine = _engine_verdicts(root)
    assert engine[f"local_{session}.json"] is False


def test_the_extractor_publishes_nothing_when_the_signals_disagree(
    extractor: Any, tmp_path: Path
) -> None:
    """Two of the app's own files naming different accounts = UNKNOWN."""
    root = _sessions_root(tmp_path)
    _write(root, org=ACTIVE_ORG, session=list(CASES)[0],
           isStarred=True, lastFocusedAt=1789684595177)
    (tmp_path / "config.json").write_text(
        json.dumps({"lastKnownAccountUuid": ACTIVE_ACCOUNT})
    )
    (tmp_path / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": OTHER_ACCOUNT})
    )
    resolution = _extractor_scope(extractor, root)
    assert resolution.scope is None
    assert "disagree" in resolution.reason
    assert _extractor_verdicts(extractor, root) is None


def test_pinned_order_supplies_rank_but_never_the_pin(extractor: Any) -> None:
    """``pinnedOrder`` is a display order, and append-only: rank only.

    It held 295 refs while the app showed 218 pinned. If its membership were
    ever read as a pin again, the ledger would start lying the same way.
    """
    slice_key = extractor.ORDER_KEYS[0]
    raw = {
        slice_key: json.dumps(
            {"value": {"pinnedOrder": [
                "code:local_11111111-1111-1111-1111-111111111111",
                "code:local_99999999-9999-9999-9999-999999999999",  # stale ref
                "chat:something-else",                               # not a code session
            ]}}
        )
    }
    ranks = extractor.pinned_order_ranks(raw)
    assert ranks == {
        "local_11111111-1111-1111-1111-111111111111.json": 0,
        "local_99999999-9999-9999-9999-999999999999.json": 1,
    }
    # And the rule that decides a pin cannot see this structure at all.
    assert extractor.pin_verdicts({"local_99999999-9999-9999-9999-999999999999.json": None}) is None


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
    root = _sessions_root(tmp_path)
    _write(root, org=ACTIVE_ORG, session=list(CASES)[0],
           isStarred=True, lastFocusedAt=1789684595177)
    _signed_in(tmp_path)
    monkeypatch.setattr(
        extractor,
        "SCOPE_IMPORT_ERROR",
        "the shared scope rule (claude_scope.py) is not importable",
    )
    monkeypatch.setattr(extractor, "read_localstorage", lambda _dir: {})
    result = extractor.extract(sessions_root=str(root), leveldb_dir=str(tmp_path))
    assert result["ok"] is True
    assert "pin_states" not in result, "a verdict was published with no rule"
    assert "claude_scope.py" in result["pin_note"]
