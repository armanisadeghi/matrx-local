"""The app's own STARRED LIST is the pin truth — and an unpin must be observable.

Measured 2026-09-18 on Arman's Mac, overturning the 2026-09-17 premise that the
index records' ``isStarred`` flag is the pin:

* The Claude desktop sidebar draws pinned Claude Code conversations from ONE
  list in the app's claude.ai IndexedDB — database ``keyval-store``, store
  ``keyval``, key ``store:pin-state:dframe-starred-code``, value
  ``{"state": {"starredIds": ["local_<id>", ..., "session_<cloud id>"]},
  "updatedAt": <ms>}`` — one list per ACCOUNT, rebuilt on every account switch.
* The signed-in scope's index records carried ``isStarred: true`` on 206
  unarchived conversations; the sidebar showed ~48 and ``starredIds`` held 56.
  Conversations Arman sees pinned (``local_ba6006c0…``, ``local_6250b1df…``,
  ``local_69a4ccf1…``) are in ``starredIds``; conversations flagged
  ``isStarred: true`` that he does NOT see pinned (``local_d5542855…``
  "Prompt", ``local_08264bc8…`` "Extension vault") are absent from it. The
  flag also spreads: the session-sync agent copies whole records between the
  48 account/org folders. AI Matrx held 285 favourites vs 56 real.

Arman's ruling: a conversation is pinned iff at least one account's sidebar
shows it pinned. The session-sync agent records each account's latest list in
``~/.claude/claude-code-pin-observations.json`` (``CLAUDE_PIN_OBSERVATIONS``)
and computes ONE verdict there, the ``master`` section; the engine's
:class:`LivePins` applies ONLY that section. Fix round 1 (review R-P1,
2026-09-18): the first version let the engine re-derive a union from the raw
lists, so ONE observed account of eight turned ~1,900 conversations into
explicit unpins on AI Matrx. So these tests pin down:

1. ``master.pinned`` -> pinned, rank = position.
2. ``master.unpinned`` -> an explicit ``is_pinned=false`` that reaches the
   payload, whatever ``isStarred`` or a stale ledger says. The sync fills it
   ONLY when every known account was observed within 14 days.
3. In neither -> UNKNOWN: the ledger stands. With one account of eight
   observed nothing is unpinned at all.
4. No master (absent, unreadable, raw lists only) -> UNKNOWN everywhere.

The second half of this file guards the ACCOUNT/ORG scope rule, which still
decides which account a freshly read list belongs to.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.services.coding_sessions.claude_scope import resolve_active_scope
from app.services.coding_sessions.claude_session_index import (
    active_index_scope,
    read_session_index,
)

FIXTURE = Path(__file__).parent / "fixtures" / "claude_index_records.json"

# The app's active account+org on the machine where the bug was measured.
ACTIVE_ACCOUNT = "d2eb2e1d-684b-41cc-a6a8-00bac619f70c"
ACTIVE_ORG = "71840439-c44e-482f-a693-702306599d49"
STALE_ORG = "8fa7c825-32a9-4a72-a6d5-f88377421373"
OTHER_ACCOUNT = "9a49ffc8-a284-4c45-b20d-68ba8cc14930"  # arman26@gmail.com


def _fixture_record() -> dict[str, Any]:
    """The real record shape, so a future app change fails here loudly."""
    return json.loads(FIXTURE.read_text())["record"]


def _write(
    root: Path,
    *,
    account: str,
    org: str,
    session: str,
    cli_session_id: str,
    last_focused_at: int,
    is_starred: bool | None,
) -> Path:
    record = _fixture_record()
    record["sessionId"] = session
    record["cliSessionId"] = cli_session_id
    record["lastFocusedAt"] = last_focused_at
    if is_starred is None:
        record.pop("isStarred", None)
    else:
        record["isStarred"] = is_starred
    folder = root / account / org
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session}.json"
    path.write_text(json.dumps(record))
    return path


def _signed_in(app_support: Path, account: str) -> None:
    """Write the app's OWN statement of which account it is signed into.

    The desktop app keeps it in two plain files beside its session index:
    ``config.json`` -> ``lastKnownAccountUuid`` and
    ``cowork-enabled-cli-ops.json`` -> ``ownerAccountId``. Both were read on
    the real machine on 2026-09-18 and agreed. A fixture that writes neither
    is a machine the app has said nothing about, which is UNKNOWN.
    """
    app_support.mkdir(parents=True, exist_ok=True)
    (app_support / "config.json").write_text(
        json.dumps({"lastKnownAccountUuid": account})
    )
    (app_support / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": account})
    )


def _ledger(tmp_path: Path, entries: dict[str, dict[str, Any]]) -> Path:
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(entries))
    return path


def _master(pinned: list[str], unpinned: list[str] = (), **extra: Any) -> Path:
    """The sync's ONE verdict, in its real shape.

    Written to ``CLAUDE_PIN_OBSERVATIONS``, which ``tests/conftest.py`` points
    at a fresh file per test, so no test can read the real one. ``extra``
    lets a test add the raw ``accounts`` section the engine must IGNORE.
    """
    path = Path(os.environ["CLAUDE_PIN_OBSERVATIONS"])
    complete = bool(unpinned)
    path.write_text(json.dumps({
        **extra,
        "master": {
            "pinned": list(pinned),
            "unpinned": list(unpinned),
            "coverage": {"observed": 8 if complete else 1, "known": 8,
                         "complete": complete},
            "computed_at": "2026-09-18T12:00:00+00:00",
        },
    }))
    return path


def test_isstarred_true_and_proved_unpinned_is_not_pinned(tmp_path: Path) -> None:
    """GUARD (a) — "Prompt" was flagged ``isStarred: true``, never pinned.

    With full coverage the sync proves no account pins it, so it is in
    ``master.unpinned``: the unpin must reach the payload whatever the flag or
    a stale ledger says.
    """
    root = tmp_path / "claude-code-sessions"
    _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session="local_prompt",
           cli_session_id="cli-prompt", last_focused_at=9_000, is_starred=True)
    _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session="local_real",
           cli_session_id="cli-real", last_focused_at=8_000, is_starred=False)
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    _master(["local_real.json"], ["local_prompt.json"])
    ledger = _ledger(
        tmp_path, {"local_prompt.json": {"isPinned": True, "pinnedRank": 7}}
    )

    entries, _ = read_session_index(root, ledger_path=ledger)

    entry = entries["cli-prompt"]
    assert entry.is_pinned is False, "isStarred decided the pin again"
    assert entry.pinned_rank is None
    payload = entry.metadata_payload()
    assert payload["is_pinned"] is False, "the unpin must reach the server"
    assert "pinned_rank" not in payload
    assert entries["cli-real"].is_pinned is True


def test_one_of_eight_accounts_observed_emits_zero_unpins(tmp_path: Path) -> None:
    """THE R-P1 CRITICAL: partial coverage must never become explicit unpins.

    One account observed: its pins are added, and every other conversation —
    ledger-pinned or not, ``isStarred`` or not — keeps exactly the state it
    had. Not one ``is_pinned: false`` may be produced.
    """
    root = tmp_path / "claude-code-sessions"
    for n, name in enumerate(("mine", "ledger_pin", "flagged", "plain")):
        _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session=f"local_{name}",
               cli_session_id=f"cli-{name}", last_focused_at=9_000 - n,
               is_starred=name == "flagged")
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    _master(["local_mine.json"])  # coverage 1/8 -> unpinned is empty
    ledger = _ledger(tmp_path, {
        "local_ledger_pin.json": {"isPinned": True, "pinnedRank": 4},
        "local_plain.json": {"isPinned": False},
    })

    entries, _ = read_session_index(root, ledger_path=ledger)

    got = {k: (v.is_pinned, v.pinned_rank) for k, v in entries.items()}
    assert got == {
        "cli-mine": (True, 0),
        "cli-ledger_pin": (True, 4),   # untouched
        "cli-flagged": (None, None),   # UNKNOWN, never False
        "cli-plain": (False, None),    # the ledger's own prior state, untouched
    }
    unpins = [
        k for k, v in entries.items()
        if v.is_pinned is False and k != "cli-plain"
    ]
    assert unpins == [], f"partial coverage produced unpins: {unpins}"


def test_master_pinned_order_is_the_rank(tmp_path: Path) -> None:
    """GUARD (b): pinned with rank = position in ``master.pinned``."""
    root = tmp_path / "claude-code-sessions"
    _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session="local_first",
           cli_session_id="cli-first", last_focused_at=9_000, is_starred=None)
    _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session="local_second",
           cli_session_id="cli-second", last_focused_at=8_000, is_starred=False)
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    _master(["local_first.json", "local_second.json"])

    entries, _ = read_session_index(root, ledger_path=tmp_path / "missing.json")

    assert (entries["cli-first"].is_pinned, entries["cli-first"].pinned_rank) == (True, 0)
    assert (entries["cli-second"].is_pinned, entries["cli-second"].pinned_rank) == (True, 1)
    payload = entries["cli-second"].metadata_payload()
    assert payload["is_pinned"] is True
    assert payload["pinned_rank"] == 1


def test_the_engine_reads_only_master_never_the_raw_lists(tmp_path: Path) -> None:
    """ONE rule, computed once — by the sync. Raw per-account lists are inert.

    A file holding only raw ``accounts`` (the first, flat shape included) is
    UNKNOWN, and when a master exists the raw lists cannot add or remove.
    """
    root = tmp_path / "claude-code-sessions"
    for n, name in enumerate(("raw_only", "master_pin", "other")):
        _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session=f"local_{name}",
               cli_session_id=f"cli-{name}", last_focused_at=9_000 - n,
               is_starred=False)
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    ledger = _ledger(tmp_path, {"local_other.json": {"isPinned": True, "pinnedRank": 2}})
    raw = {ACTIVE_ACCOUNT: {"local": ["local_raw_only.json"], "cloud": []}}
    observations = Path(os.environ["CLAUDE_PIN_OBSERVATIONS"])

    for body in (raw, {"accounts": raw}):  # flat + current shape, no master
        observations.write_text(json.dumps(body))
        entries, _ = read_session_index(root, ledger_path=ledger)
        assert entries["cli-raw_only"].is_pinned is None, body
        assert entries["cli-other"].is_pinned is True, body

    _master(["local_master_pin.json"], accounts=raw)
    entries, _ = read_session_index(root, ledger_path=ledger)
    assert entries["cli-raw_only"].is_pinned is None, "a raw list decided a pin"
    assert entries["cli-master_pin"].is_pinned is True
    assert (entries["cli-other"].is_pinned, entries["cli-other"].pinned_rank) == (True, 2)


def test_no_observations_at_all_the_ledger_stands(tmp_path: Path) -> None:
    """GUARD (e): nothing observed is UNKNOWN — never "unpin everything"."""
    root = tmp_path / "claude-code-sessions"
    _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session="local_led",
           cli_session_id="cli-led", last_focused_at=9_000, is_starred=False)
    _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session="local_none",
           cli_session_id="cli-none", last_focused_at=8_000, is_starred=True)
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    assert not Path(os.environ["CLAUDE_PIN_OBSERVATIONS"]).exists()
    ledger = _ledger(tmp_path, {"local_led.json": {"isPinned": True, "pinnedRank": 3}})

    entries, _ = read_session_index(root, ledger_path=ledger)

    assert (entries["cli-led"].is_pinned, entries["cli-led"].pinned_rank) == (True, 3)
    # No ledger opinion and no observation: nothing is sent at all.
    assert entries["cli-none"].is_pinned is None
    assert "is_pinned" not in entries["cli-none"].metadata_payload()


def test_an_unreadable_or_malformed_observations_file_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "claude-code-sessions"
    _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session="local_u",
           cli_session_id="cli-u", last_focused_at=9_000, is_starred=False)
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    ledger = _ledger(tmp_path, {"local_u.json": {"isPinned": True, "pinnedRank": 2}})
    observations = Path(os.environ["CLAUDE_PIN_OBSERVATIONS"])
    for body in ("{not json", "{}", json.dumps({"master": {"pinned": "x"}})):
        observations.write_text(body)
        entries, _ = read_session_index(root, ledger_path=ledger)
        assert entries["cli-u"].is_pinned is True, f"{body!r} cleared a real pin"


# ── the persisted store is what production reads ─────────────────────────────
#
# The reconciler and the Coding Sessions screen never call read_session_index:
# they go through ClaudeIndexStore. A pin fix that lands only in the full scan
# is invisible in the running app, so the store gets the same tests.


def _refresh(root: Path, store_path: Path) -> Any:
    from app.services.coding_sessions.claude_index_store import (
        ClaudeIndexStore,
        refresh_store_sync,
    )

    store = ClaudeIndexStore(store_path)
    refresh_store_sync(root, store)
    return store


def test_store_reports_the_unpin_exactly_like_the_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """THE BUG, through the path production actually uses.

    A copy flagged ``isStarred: true`` is the FRESHEST record, so it wins the
    store's one-row-per-conversation collapse; the master proves it unpinned.
    """
    root = tmp_path / "claude-code-sessions"
    stale = _write(root, account=OTHER_ACCOUNT, org=STALE_ORG, session="local_iii",
                   cli_session_id="cli-i", last_focused_at=1_000, is_starred=True)
    record = json.loads(stale.read_text())
    record["lastActivityAt"] = 9_999_999_999
    stale.write_text(json.dumps(record))
    _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session="local_iii",
           cli_session_id="cli-i", last_focused_at=9_000, is_starred=True)
    _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session="local_kept",
           cli_session_id="cli-kept", last_focused_at=8_000, is_starred=False)
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    _master(["local_kept.json"], ["local_iii.json"])
    ledger = _ledger(tmp_path, {"local_iii.json": {"isPinned": True, "pinnedRank": 5}})
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(ledger))

    store = _refresh(root, tmp_path / "store" / "index.sqlite3")
    snapshot = store.load(record_paths=True)

    entry = snapshot.entries["cli-i"]
    assert entry.is_pinned is False, "the store must see the unpin too"
    assert entry.metadata_payload()["is_pinned"] is False
    assert snapshot.entries["cli-kept"].is_pinned is True
    scan, _ = read_session_index(root, ledger_path=ledger)
    assert {k: v.is_pinned for k, v in snapshot.entries.items()} == {
        k: v.is_pinned for k, v in scan.items()
    }


def test_store_and_scan_agree_on_pins_across_accounts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Every pin state, both paths, one assertion — ranks included."""
    root = tmp_path / "claude-code-sessions"
    for n in range(4):
        _write(root, account=ACTIVE_ACCOUNT, org=ACTIVE_ORG, session=f"local_p{n}",
               cli_session_id=f"cli-p{n}", last_focused_at=9_000 + n,
               is_starred=n % 2 == 0)
        _write(root, account=OTHER_ACCOUNT, org=STALE_ORG, session=f"local_p{n}",
               cli_session_id=f"cli-p{n}", last_focused_at=1_000, is_starred=True)
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    # p0 proved unpinned, p1/p3 pinned, p2 UNKNOWN (the ledger's rank stands).
    _master(["local_p1.json", "local_p3.json"], ["local_p0.json"])
    ledger = _ledger(
        tmp_path,
        {f"local_p{n}.json": {"isPinned": True, "pinnedRank": n} for n in range(4)},
    )
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(ledger))

    store = _refresh(root, tmp_path / "store" / "index.sqlite3")
    snapshot = store.load(record_paths=True)
    scan, _ = read_session_index(root, ledger_path=ledger)

    expected = {
        "cli-p0": (False, None),
        "cli-p1": (True, 0),
        "cli-p2": (True, 2),
        "cli-p3": (True, 1),
    }
    assert {k: (v.is_pinned, v.pinned_rank) for k, v in snapshot.entries.items()} == expected
    assert {k: (v.is_pinned, v.pinned_rank) for k, v in scan.items()} == expected


# ---------------------------------------------------------------------------
# 2026-09-18: the scope signal itself was contaminable (CS-33 / F1)
#
# These guard the ACCOUNT/ORG scope rule. Since the second 2026-09-18 finding
# the scope no longer decides a pin (the sync's master verdict does, above); it
# decides which account a freshly read starred list is recorded under and
# which records the extractor publishes verdicts for, so the rule still has
# to be right.
#
# A zero-authorship verifier measured the previous rule — "the scope holding
# the newest ``lastFocusedAt`` is the live scope" — failing on the real
# machine. ``lastFocusedAt`` IS copied between scopes: the identical stamp
# ``1789692097566`` sat in two different accounts' scopes at once, and
# re-measured on 2026-09-18 the single stamp ``1789714654476`` was the maximum
# in NINE scopes across FIVE accounts. At 18:04:13 on 2026-09-17 the ledger
# therefore held, byte for byte, dev@aimatrx.com's starred set (218) while the
# app was signed into arman26@gmail.com (228 stars): 21 pins that were not
# pinned, 31 real pins missing, and the running engine reads that ledger at
# load.
#
# So the ACCOUNT is now taken from the app's own statement of it and is never
# ranked by a timestamp; only the ORG is ranked, and only inside that one
# account. These tests are the two accounts sharing a stamp.
# ---------------------------------------------------------------------------



def test_a_stamp_copied_into_another_account_cannot_steal_the_scope(
    tmp_path: Path,
) -> None:
    """Two accounts, one identical stamp: the app's own account decides.

    The other account's scope is written LAST and sorts first, so anything
    resolving by stamp-then-order lands on it. Only the signed-in account's
    scope may speak.
    """
    root = tmp_path / "claude-code-sessions"
    shared_stamp = 1_789_714_654_476  # the real copied stamp
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_shared",
        cli_session_id="cli-shared",
        last_focused_at=shared_stamp,
        is_starred=False,  # the person UNPINNED it in the account they use
    )
    _write(
        root,
        account=OTHER_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_shared",
        cli_session_id="cli-shared",
        last_focused_at=shared_stamp,  # identical — the contamination
        is_starred=True,  # a stale pin in an account nobody is signed into
    )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)

    scope = active_index_scope(root)
    assert scope == root / ACTIVE_ACCOUNT / ACTIVE_ORG, (
        "the scope was taken from an account the app is not signed into"
    )


def test_a_newer_stamp_in_another_account_cannot_steal_the_scope(
    tmp_path: Path,
) -> None:
    """The other account is not merely tied — it is NEWER. Still irrelevant."""
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_newer",
        cli_session_id="cli-newer",
        last_focused_at=1_000,
        is_starred=False,
    )
    _write(
        root,
        account=OTHER_ACCOUNT,
        org=STALE_ORG,
        session="local_newer",
        cli_session_id="cli-newer",
        last_focused_at=9_999_999_999_999,
        is_starred=True,
    )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)

    assert active_index_scope(root) == root / ACTIVE_ACCOUNT / ACTIVE_ORG


def test_the_org_is_still_ranked_by_focus_inside_the_signed_in_account(
    tmp_path: Path,
) -> None:
    """Ranking survives where contamination cannot reach: one account's orgs."""
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=STALE_ORG,
        session="local_org",
        cli_session_id="cli-org",
        last_focused_at=1_000,
        is_starred=True,
    )
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_org",
        cli_session_id="cli-org",
        last_focused_at=9_000,
        is_starred=False,
    )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)

    assert active_index_scope(root) == root / ACTIVE_ACCOUNT / ACTIVE_ORG


def test_signals_that_disagree_resolve_to_unknown_not_to_a_majority(
    tmp_path: Path,
) -> None:
    """A disagreement IS the contamination window — so it is never a guess.

    Two of the app's own files naming different accounts means the app is
    mid-switch or one file is stale. Picking either one can publish the wrong
    person's sidebar, so the pin stays UNKNOWN and every ledger pin stands.
    """
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_dis",
        cli_session_id="cli-dis",
        last_focused_at=9_000,
        is_starred=False,
    )
    (tmp_path / "config.json").write_text(
        json.dumps({"lastKnownAccountUuid": ACTIVE_ACCOUNT})
    )
    (tmp_path / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": OTHER_ACCOUNT})
    )

    resolution = resolve_active_scope(root)
    assert resolution.scope is None
    assert resolution.account is None
    assert "disagree" in resolution.reason
    assert ACTIVE_ACCOUNT in resolution.reason and OTHER_ACCOUNT in resolution.reason


def test_no_account_signal_at_all_is_unknown_with_a_reason(tmp_path: Path) -> None:
    """No statement from the app = UNKNOWN, and the reason says so in English."""
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_none",
        cli_session_id="cli-none",
        last_focused_at=9_000,
        is_starred=False,
    )
    resolution = resolve_active_scope(root)
    assert resolution.scope is None
    assert "no signed-in account" in resolution.reason


def test_the_store_applies_the_same_account_rule_as_the_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The store is what production reads — the contamination must not reach it.

    The store used to answer this with one ``ORDER BY lastrecord_focused_at
    DESC LIMIT 1`` over every record on the machine, so the copied stamp chose
    the scope there too. Both readers must land on the same conversation state.
    """
    root = tmp_path / "claude-code-sessions"
    shared_stamp = 1_789_714_654_476
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_store",
        cli_session_id="cli-store",
        last_focused_at=shared_stamp,
        is_starred=False,
    )
    _write(
        root,
        account=OTHER_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_store",
        cli_session_id="cli-store",
        last_focused_at=shared_stamp,
        is_starred=True,
    )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)

    store = _refresh(root, tmp_path / "store" / "index.sqlite3")
    snapshot = store.load()
    assert snapshot.active_scope == str(root / ACTIVE_ACCOUNT / ACTIVE_ORG)
    assert ACTIVE_ACCOUNT in (snapshot.active_scope_reason or "")
    assert active_index_scope(root) == root / ACTIVE_ACCOUNT / ACTIVE_ORG


def test_an_account_signal_that_would_escape_the_index_tree_is_refused(
    tmp_path: Path,
) -> None:
    """The account id becomes a path segment, so a separator is not an account.

    The signal is a file the app writes, but it is still untrusted input to a
    path join. A traversal value must read as "no account stated" — UNKNOWN,
    which keeps the ledger's pins — never as a directory outside the index.
    """
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_esc",
        cli_session_id="cli-esc",
        last_focused_at=9_000,
        is_starred=False,
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.json").write_text(
        json.dumps({"lastKnownAccountUuid": "../../../etc"})
    )
    resolution = resolve_active_scope(root)
    assert resolution.scope is None
    assert resolution.account is None


# ---------------------------------------------------------------------------
# The same law, one level down: the ORG is stated too (CS-33 / F1, second pass)
#
# Ranking lastFocusedAt among ONE account's organisations looked safe — the
# contamination that was measured crossed accounts. Then it was measured
# inside an account: at 01:29 on 2026-09-18 all FIVE organisations of the
# signed-in account carried the identical maximum stamp 1789718916078 while
# holding 203, 203, 204, 204 and 218 stars, and "the newest" was a coin flip
# that landed on 203 while the app was showing 218. The app names its own
# organisation in the same config file it names its account in, one
# `dxt:allowlistLastUpdated:<org>` key per organisation, and on that machine
# the right one (218 stars) was hours ahead of every sibling.
# ---------------------------------------------------------------------------

SECOND_ORG = "09b4b1ef-090b-42f2-a680-2c51863e549d"


def _stated_orgs(app_support: Path, stamps: dict[str, str]) -> None:
    """Merge the app's per-organisation stamps into its config, as it does."""
    config = app_support / "config.json"
    document = json.loads(config.read_text()) if config.exists() else {}
    for org, stamp in stamps.items():
        document[f"dxt:allowlistLastUpdated:{org}"] = stamp
    config.write_text(json.dumps(document))


def test_the_org_the_app_names_wins_over_a_tied_focus_stamp(tmp_path: Path) -> None:
    """The real 01:29 failure: every org tied, and the app named the right one."""
    root = tmp_path / "claude-code-sessions"
    tied = 1_789_718_916_078  # the real shared stamp
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_tie",
        cli_session_id="cli-tie",
        last_focused_at=tied,
        is_starred=True,
    )
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=STALE_ORG,
        session="local_tie",
        cli_session_id="cli-tie",
        last_focused_at=tied,
        is_starred=False,
    )
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=SECOND_ORG,
        session="local_tie",
        cli_session_id="cli-tie",
        last_focused_at=tied,
        is_starred=False,
    )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    _stated_orgs(
        tmp_path,
        {
            ACTIVE_ORG: "2026-09-18T08:33:08.496Z",
            STALE_ORG: "2026-09-18T04:33:08.532Z",
            SECOND_ORG: "2026-09-16T00:19:21.832Z",
        },
    )

    resolution = resolve_active_scope(root)
    assert resolution.org == ACTIVE_ORG, resolution.reason
    assert "last updated" in resolution.reason


def test_a_tied_focus_stamp_with_no_stated_org_is_unknown_not_a_coin_flip(
    tmp_path: Path,
) -> None:
    """No statement + a tie = UNKNOWN. An arbitrary winner is not an answer."""
    root = tmp_path / "claude-code-sessions"
    tied = 1_789_718_916_078
    for org, starred in ((ACTIVE_ORG, True), (STALE_ORG, False), (SECOND_ORG, False)):
        _write(
            root,
            account=ACTIVE_ACCOUNT,
            org=org,
            session="local_flip",
            cli_session_id="cli-flip",
            last_focused_at=tied,
            is_starred=starred,
        )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)

    resolution = resolve_active_scope(root)
    assert resolution.scope is None
    assert resolution.org is None
    assert "coin flip" in resolution.reason
    assert str(tied) in resolution.reason


def test_the_store_uses_the_stated_org_too(tmp_path: Path, monkeypatch: Any) -> None:
    """Production reads the store, so the store must not flip the coin either."""
    root = tmp_path / "claude-code-sessions"
    tied = 1_789_718_916_078
    for org, starred in ((ACTIVE_ORG, True), (STALE_ORG, False)):
        _write(
            root,
            account=ACTIVE_ACCOUNT,
            org=org,
            session="local_sorg",
            cli_session_id="cli-sorg",
            last_focused_at=tied,
            is_starred=starred,
        )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    _stated_orgs(
        tmp_path,
        {
            ACTIVE_ORG: "2026-09-18T08:33:08.496Z",
            STALE_ORG: "2026-09-18T04:33:08.532Z",
        },
    )
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_SUPPORT_DIR", str(tmp_path))

    store = _refresh(root, tmp_path / "store" / "index.sqlite3")
    snapshot = store.load()
    assert snapshot.active_scope == str(root / ACTIVE_ACCOUNT / ACTIVE_ORG)
