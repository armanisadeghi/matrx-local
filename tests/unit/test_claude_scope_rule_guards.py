"""Guards for the two edges of the scope rule that nothing else holds down.

CS-33/R2, 2026-09-18. Two of the rule's branches were correct in the code and
correct on the real machine, and NOTHING held either one there — the whole
73-test suite stayed green with both of them broken:

1. **A stated-org TIE must be UNKNOWN, never the first winner.** The focus tie
   already had a guard; the stated tie did not. Mutating ``decide_scope`` to
   return ``resolved(winners[0], …)`` when two organisations carry the same
   stated stamp left all 73 tests green, and it would publish one of two
   organisations' sidebars on a coin flip — exactly the failure the stated
   stamp exists to prevent (measured 01:29 on 2026-09-18: five organisations
   tied on ``lastFocusedAt`` while holding 203, 203, 204, 204 and 218 stars).

2. **A stamp that cannot be parsed is ignored, never a crash and never a
   winner.** The stamps are strings the app writes; this module does not own
   their format, so it must survive a format it has never seen by declining to
   conclude anything from it.

The instant-ordering half of (2) is the verifier's test in
``test_claude_scope_and_spend_verification.py``; these are the siblings of it
that the verifier did not write.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.coding_sessions.claude_scope import (
    account_org_dirs,
    decide_scope,
    org_focus_for,
    resolve_active_scope,
    stated_org_stamps,
)

ACCOUNT = "acct-guard-1"
ORG_A = "org-guard-aaaa"
ORG_B = "org-guard-bbbb"
SIGNALS = {"config.json:lastKnownAccountUuid": ACCOUNT}


def _tree(tmp_path: Path, orgs: dict[str, int | None]) -> tuple[Path, Path]:
    """``(app support, account dir)`` with one record per named org.

    ``None`` writes a record with NO ``lastFocusedAt`` — the shape that made
    the two readers of the rule disagree.
    """
    support = tmp_path / "Claude"
    root = support / "claude-code-sessions"
    support.mkdir(parents=True, exist_ok=True)
    for org, focus in orgs.items():
        folder = root / ACCOUNT / org
        folder.mkdir(parents=True, exist_ok=True)
        record: dict[str, object] = {
            "sessionId": f"local_{org}",
            "cliSessionId": f"cli-{org}",
            "isStarred": True,
        }
        if focus is not None:
            record["lastFocusedAt"] = focus
        (folder / f"local_{org}.json").write_text(json.dumps(record))
    (support / "config.json").write_text(
        json.dumps({"lastKnownAccountUuid": ACCOUNT})
    )
    (support / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": ACCOUNT})
    )
    return support, root / ACCOUNT


def test_a_stated_org_tie_names_the_tie_and_refuses_to_pick(tmp_path: Path) -> None:
    """THE unguarded branch: two stated organisations, one instant.

    Mutate ``decide_scope`` to take ``winners[0]`` and this is the only test
    on the machine that notices.
    """
    same = "2026-09-18T08:33:08.496Z"
    resolution = decide_scope(
        ACCOUNT,
        SIGNALS,
        "stated",
        {ORG_A: 10, ORG_B: 20},
        org_stated={ORG_A: same, ORG_B: same},
        account_dir=tmp_path / ACCOUNT,
    )
    assert resolution.scope is None, "a stated tie was resolved by a coin flip"
    assert resolution.org is None
    assert "UNKNOWN" in resolution.reason
    # The tie itself must be visible, not merely described: both organisations
    # and the shared stamp, so a diagnosis reads the machine's real state.
    assert ORG_A in resolution.reason and ORG_B in resolution.reason
    assert same in resolution.reason


def test_two_stamp_spellings_of_the_same_instant_are_still_a_tie(
    tmp_path: Path,
) -> None:
    """``…08Z`` and ``…08.000Z`` are ONE instant, so they tie — not a winner.

    String comparison called these different and picked the fractional one.
    """
    resolution = decide_scope(
        ACCOUNT,
        SIGNALS,
        "stated",
        {ORG_A: 1, ORG_B: 1},
        org_stated={ORG_A: "2026-09-18T08:33:08Z", ORG_B: "2026-09-18T08:33:08.000Z"},
        account_dir=tmp_path / ACCOUNT,
    )
    assert resolution.scope is None, resolution.reason
    assert "UNKNOWN" in resolution.reason


def test_an_unparseable_stated_stamp_is_ignored_and_never_wins(
    tmp_path: Path,
) -> None:
    """A stamp in a format this module has never seen decides nothing.

    It must not crash, and it must not win by being the only "stated" org: the
    org it names falls back to focus ranking with the rest.
    """
    resolution = decide_scope(
        ACCOUNT,
        SIGNALS,
        "stated",
        {ORG_A: 10, ORG_B: 20},
        org_stated={ORG_A: "yesterday afternoon", ORG_B: "2026-09-18T08:33:08.496Z"},
        account_dir=tmp_path / ACCOUNT,
    )
    assert resolution.org == ORG_B, resolution.reason

    # And when the ONLY stated stamp is unparseable, the statement is gone
    # entirely: the rule falls back to focus, it does not pick the org whose
    # stamp it could not read.
    fallback = decide_scope(
        ACCOUNT,
        SIGNALS,
        "stated",
        {ORG_A: 10, ORG_B: 20},
        org_stated={ORG_A: "not a stamp at all"},
        account_dir=tmp_path / ACCOUNT,
    )
    assert fallback.org == ORG_B, fallback.reason
    assert "lastFocusedAt" in fallback.reason


def test_an_unparseable_stamp_never_reaches_the_decision_from_the_config(
    tmp_path: Path,
) -> None:
    """The reader and the decision agree on what a stamp IS.

    ``stated_org_stamps`` drops what ``decide_scope`` could not order, so the
    config cannot hand the decision a value it will silently discard.
    """
    support, _account_dir = _tree(tmp_path, {ORG_A: 5_000, ORG_B: 7_000})
    config = support / "config.json"
    document = json.loads(config.read_text())
    document[f"dxt:allowlistLastUpdated:{ORG_A}"] = {"nested": "object"}
    document[f"dxt:allowlistLastUpdated:{ORG_B}"] = "18/09/2026 08:33"
    config.write_text(json.dumps(document))

    assert stated_org_stamps(support) == {}
    resolution = resolve_active_scope(support / "claude-code-sessions")
    assert resolution.org == ORG_B, resolution.reason
    assert "lastFocusedAt" in resolution.reason


# ── the one enumerator: which organisations EXIST ───────────────────────────


def test_an_org_with_no_focus_stamp_still_exists(tmp_path: Path) -> None:
    """Existence is the directory listing, never "the orgs I hold a stamp for".

    This is the shape behind Defect A: an org whose records carry no
    ``lastFocusedAt`` must still be in ``org_focus`` — as 0 — or the app's
    statement about it is ignored.
    """
    _support, account_dir = _tree(tmp_path, {ORG_A: 5_000, ORG_B: None})
    assert account_org_dirs(account_dir) == (ORG_A, ORG_B)
    assert org_focus_for(account_dir) == {ORG_A: 5_000, ORG_B: 0}


def test_the_stamp_supplier_cannot_change_which_orgs_exist(tmp_path: Path) -> None:
    """The engine passes stamps, not an org set — so it cannot invent or hide one.

    A stamp for a folder that is not there must not add an organisation, and a
    folder with no stamp must not lose one. That is what makes the two readers
    structurally unable to disagree about existence.
    """
    _support, account_dir = _tree(tmp_path, {ORG_A: 5_000, ORG_B: None})
    supplied = org_focus_for(
        account_dir,
        {ORG_A: 5_000, "org-that-is-not-on-disk": 9_999_999},
    )
    assert supplied == {ORG_A: 5_000, ORG_B: 0}
    assert set(supplied) == set(org_focus_for(account_dir)), (
        "the two readers of THE one rule see different organisations"
    )
