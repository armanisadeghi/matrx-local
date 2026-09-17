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


def _write(root: Path, *, org: str, session: str, **fields: Any) -> Path:
    scope = root / ACTIVE_ACCOUNT / org
    scope.mkdir(parents=True, exist_ok=True)
    path = scope / f"local_{session}.json"
    record = _record(sessionId=f"local_{session}", cliSessionId=session, **fields)
    path.write_text(json.dumps(record))
    return path


def _extractor_verdicts(extractor: Any, root: Path) -> dict[str, bool] | None:
    scope = extractor.active_index_scope(str(root))
    if scope is None:
        return None
    return extractor.pin_verdicts(extractor.scope_pin_states(scope))


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
    _build_live_tree(tmp_path)
    published = _extractor_verdicts(extractor, tmp_path)
    assert published is not None, "the live scope speaks; verdicts must be published"
    engine = _engine_verdicts(tmp_path)
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
    for index, session in enumerate(CASES):
        _write(tmp_path, org=ACTIVE_ORG, session=session,
               isStarred=_ABSENT, lastFocusedAt=1789684595000 + index)
    assert _extractor_verdicts(extractor, tmp_path) is None
    engine = _engine_verdicts(tmp_path)
    assert set(engine.values()) == {None}, engine


def test_no_identifiable_scope_is_unknown_never_false(
    extractor: Any, tmp_path: Path
) -> None:
    """No ``lastFocusedAt`` anywhere: nothing may be concluded about a pin."""
    for session in CASES:
        _write(tmp_path, org=ACTIVE_ORG, session=session,
               isStarred=True, lastFocusedAt=_ABSENT)
    assert extractor.active_index_scope(str(tmp_path)) is None
    assert _extractor_verdicts(extractor, tmp_path) is None
    engine = _engine_verdicts(tmp_path)
    assert set(engine.values()) == {None}, engine


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
