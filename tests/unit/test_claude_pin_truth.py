"""The app's OWN pin field is the pin truth — and an unpin must be observable.

Live bug, 2026-09-17 (Arman): "conversations are getting pinned but never
unpinned; the list of pinned conversations grows endlessly and sync never
resolves it." Measured on his machine that day:

    the app's real pinned set   218
    the sidebar ledger          257   (73 sessions pinned that are NOT pinned,
                                       34 truly-pinned sessions missing)
    the server                  276

Root cause: pins moved. The desktop app now records a pin as ``isStarred`` on
its own per-account session-index record; the machine's session-sync agent was
still deriving pins from ``pinnedOrder`` in the app's Chromium localStorage,
which is an append-only DISPLAY-ORDER array — it keeps a reference forever
after the person unpins, so an unpin was literally unrepresentable upstream.

So these tests pin down three things, using a fixture of the record shape
observed on 2026-09-17 (``fixtures/claude_index_records.json``):

1. The pin comes from the ACTIVE account+org scope's ``isStarred``.
2. ``isStarred: false`` there is an OBSERVED UNPIN — it must reach the payload
   as ``is_pinned=false``, even when a stale ledger still says pinned.
3. An absent ``isStarred`` (and an undeterminable scope) is UNKNOWN — never
   ``false``. Guessing ``false`` would mass-unpin every conversation the app
   has no opinion about.
"""

from __future__ import annotations

import json
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
    the real machine on 2026-09-18 and agreed
    (``d2eb2e1d-684b-41cc-a6a8-00bac619f70c`` = dev@aimatrx.com), matching the
    app's localStorage ``rq-cache-confirmed-account``. A fixture that writes
    neither is a machine the app has said nothing about, which is UNKNOWN.
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


def test_fixture_carries_the_observed_record_shape() -> None:
    """If the app renames these keys, this fails before anything mis-syncs."""
    record = _fixture_record()
    assert "isStarred" in record, "pin field renamed — re-measure the app"
    assert "lastFocusedAt" in record, "scope signal renamed — re-measure"
    assert "cliSessionId" in record
    assert isinstance(record["isStarred"], bool)


def test_active_scope_is_the_most_recently_focused(tmp_path: Path) -> None:
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=STALE_ORG,
        session="local_aaa",
        cli_session_id="cli-a",
        last_focused_at=1_000,
        is_starred=True,
    )
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_aaa",
        cli_session_id="cli-a",
        last_focused_at=9_000,
        is_starred=False,
    )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    assert active_index_scope(root) == root / ACTIVE_ACCOUNT / ACTIVE_ORG


def test_unpin_in_the_active_scope_beats_a_stale_pinned_ledger(
    tmp_path: Path,
) -> None:
    """THE BUG. The person unpinned; the ledger still says pinned."""
    root = tmp_path / "claude-code-sessions"
    # Another account still holds the old pin — this is the norm on Arman's
    # machine (48 scopes, each with its own isStarred). It must not win.
    _write(
        root,
        account="other-account",
        org=STALE_ORG,
        session="local_bbb",
        cli_session_id="cli-b",
        last_focused_at=1_000,
        is_starred=True,
    )
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_bbb",
        cli_session_id="cli-b",
        last_focused_at=9_000,
        is_starred=False,
    )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    ledger = _ledger(tmp_path, {"local_bbb.json": {"isPinned": True, "pinnedRank": 7}})

    entries, _ = read_session_index(root, ledger_path=ledger)

    entry = entries["cli-b"]
    assert entry.is_pinned is False, "an unpin must be observed, not ignored"
    assert entry.pinned_rank is None
    payload = entry.metadata_payload()
    assert payload["is_pinned"] is False, "the unpin must reach the server"
    assert "pinned_rank" not in payload


def test_pin_in_the_active_scope_is_reported_pinned(tmp_path: Path) -> None:
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_ccc",
        cli_session_id="cli-c",
        last_focused_at=9_000,
        is_starred=True,
    )
    ledger = _ledger(tmp_path, {"local_ccc.json": {"isPinned": True, "pinnedRank": 3}})

    entries, _ = read_session_index(root, ledger_path=ledger)

    entry = entries["cli-c"]
    assert entry.is_pinned is True
    assert entry.pinned_rank == 3, "rank stays a display hint from the ledger"
    assert entry.metadata_payload()["is_pinned"] is True


def test_absent_pin_field_in_a_live_scope_means_not_pinned(tmp_path: Path) -> None:
    """1,331 of 1,568 records had no isStarred key.

    The app documents an absent pin record as not pinned, and all 14 sampled
    absences matched that on 2026-09-17. So in a scope that demonstrably does
    record pins, absent is an honest ``false`` — and that is what clears the
    stale pins off the server.
    """
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_ddd",
        cli_session_id="cli-d",
        last_focused_at=9_000,
        is_starred=None,
    )
    # Something in this scope IS pinned, so the scope speaks pins.
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_ddd2",
        cli_session_id="cli-d2",
        last_focused_at=8_000,
        is_starred=True,
    )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    ledger = _ledger(tmp_path, {"local_ddd.json": {"isPinned": True, "pinnedRank": 4}})

    entries, _ = read_session_index(root, ledger_path=ledger)

    assert entries["cli-d"].is_pinned is False
    assert entries["cli-d"].metadata_payload()["is_pinned"] is False
    assert entries["cli-d2"].is_pinned is True


def test_a_scope_with_no_pins_at_all_never_mass_unpins(tmp_path: Path) -> None:
    """A freshly signed-in account has no pin records. That is not an unpin.

    Without this guard one sync pass after an account switch would unpin every
    conversation the person has on the server.
    """
    root = tmp_path / "claude-code-sessions"
    for n in range(3):
        _write(
            root,
            account="fresh-account",
            org=ACTIVE_ORG,
            session=f"local_h{n}",
            cli_session_id=f"cli-h{n}",
            last_focused_at=9_000 + n,
            is_starred=None,
        )
    ledger = _ledger(
        tmp_path,
        {f"local_h{n}.json": {"isPinned": True, "pinnedRank": n} for n in range(3)},
    )

    entries, _ = read_session_index(root, ledger_path=ledger)

    assert [entries[f"cli-h{n}"].is_pinned for n in range(3)] == [True, True, True]


def test_no_active_scope_falls_back_to_the_ledger(tmp_path: Path) -> None:
    """Scope undeterminable (no lastFocusedAt anywhere) = keep the old source.

    Degrading to "nothing is pinned" here would unpin the person's whole
    sidebar on the server the first time the signal went missing.
    """
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_eee",
        cli_session_id="cli-e",
        last_focused_at=0,
        is_starred=None,
    )
    ledger = _ledger(tmp_path, {"local_eee.json": {"isPinned": True, "pinnedRank": 1}})

    entries, _ = read_session_index(root, ledger_path=ledger)

    entry = entries["cli-e"]
    assert entry.is_pinned is True
    assert entry.pinned_rank == 1


def test_session_missing_from_the_active_scope_falls_back_to_the_ledger(
    tmp_path: Path,
) -> None:
    """A session the active account has never seen is not an unpin."""
    root = tmp_path / "claude-code-sessions"
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_fff",
        cli_session_id="cli-f",
        last_focused_at=9_000,
        is_starred=True,
    )
    _write(
        root,
        account="other-account",
        org=STALE_ORG,
        session="local_ggg",
        cli_session_id="cli-g",
        last_focused_at=1_000,
        is_starred=None,
    )
    ledger = _ledger(tmp_path, {"local_ggg.json": {"isPinned": True, "pinnedRank": 2}})

    entries, _ = read_session_index(root, ledger_path=ledger)

    assert entries["cli-g"].is_pinned is True
    assert entries["cli-g"].pinned_rank == 2


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

    The stale scope's record is the FRESHEST (higher lastActivityAt), so it
    wins the store's one-row-per-conversation collapse — which is precisely
    how a pin the person removed kept being re-asserted to the server.
    """
    root = tmp_path / "claude-code-sessions"
    stale = _write(
        root,
        account="other-account",
        org=STALE_ORG,
        session="local_iii",
        cli_session_id="cli-i",
        last_focused_at=1_000,
        is_starred=True,
    )
    record = json.loads(stale.read_text())
    record["lastActivityAt"] = 9_999_999_999
    stale.write_text(json.dumps(record))
    _write(
        root,
        account=ACTIVE_ACCOUNT,
        org=ACTIVE_ORG,
        session="local_iii",
        cli_session_id="cli-i",
        last_focused_at=9_000,
        is_starred=False,
    )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    ledger = _ledger(tmp_path, {"local_iii.json": {"isPinned": True, "pinnedRank": 5}})
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(ledger))

    store = _refresh(root, tmp_path / "store" / "index.sqlite3")
    snapshot = store.load(record_paths=True)

    entry = snapshot.entries["cli-i"]
    assert entry.is_pinned is False, "the store must see the unpin too"
    assert entry.metadata_payload()["is_pinned"] is False
    # And it must agree with the full scan, conversation for conversation.
    scan, _ = read_session_index(root, ledger_path=ledger)
    assert {k: v.is_pinned for k, v in snapshot.entries.items()} == {
        k: v.is_pinned for k, v in scan.items()
    }


def test_store_and_scan_agree_on_pins_across_scopes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Every pin state, both paths, one assertion."""
    root = tmp_path / "claude-code-sessions"
    cases = {
        "cli-p1": (True, True),  # pinned in active scope, stale says pinned
        "cli-p2": (False, True),  # UNPINNED in active scope, stale says pinned
        "cli-p3": (None, False),  # no pin key in active scope
        "cli-p4": (True, None),  # pinned in active scope only
    }
    for n, (active, stale_state) in enumerate(cases.items()):
        active_starred, stale_starred = stale_state
        _write(
            root,
            account=ACTIVE_ACCOUNT,
            org=ACTIVE_ORG,
            session=f"local_p{n}",
            cli_session_id=active,
            last_focused_at=9_000 + n,
            is_starred=active_starred,
        )
        if stale_starred is not None:
            _write(
                root,
                account="other-account",
                org=STALE_ORG,
                session=f"local_p{n}",
                cli_session_id=active,
                last_focused_at=1_000,
                is_starred=stale_starred,
            )
    _signed_in(tmp_path, ACTIVE_ACCOUNT)
    ledger = _ledger(
        tmp_path,
        {f"local_p{n}.json": {"isPinned": True, "pinnedRank": n} for n in range(4)},
    )
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(ledger))

    store = _refresh(root, tmp_path / "store" / "index.sqlite3")
    snapshot = store.load(record_paths=True)
    scan, _ = read_session_index(root, ledger_path=ledger)

    assert {k: v.is_pinned for k, v in snapshot.entries.items()} == {
        "cli-p1": True,
        "cli-p2": False,
        "cli-p3": False,
        "cli-p4": True,
    }
    assert {k: v.is_pinned for k, v in snapshot.entries.items()} == {
        k: v.is_pinned for k, v in scan.items()
    }


# ---------------------------------------------------------------------------
# 2026-09-18: the scope signal itself was contaminable (CS-33 / F1)
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

OTHER_ACCOUNT = "9a49ffc8-a284-4c45-b20d-68ba8cc14930"  # arman26@gmail.com


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
    entries, _totals = read_session_index(root, ledger_path=tmp_path / "missing.json")
    assert entries["cli-shared"].is_pinned is False, (
        "another account's stale pin reached the payload"
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
    entries, _totals = read_session_index(root, ledger_path=tmp_path / "missing.json")
    assert entries["cli-newer"].is_pinned is False


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
    entries, _totals = read_session_index(root, ledger_path=tmp_path / "missing.json")
    assert entries["cli-org"].is_pinned is False


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

    ledger = _ledger(
        tmp_path, {"local_dis.json": {"isPinned": True, "pinnedRank": 0}}
    )
    entries, _totals = read_session_index(root, ledger_path=ledger)
    assert entries["cli-dis"].is_pinned is True, (
        "an undeterminable scope cleared a pin the ledger held"
    )


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
    ledger = _ledger(
        tmp_path, {"local_none.json": {"isPinned": True, "pinnedRank": 3}}
    )
    entries, _totals = read_session_index(root, ledger_path=ledger)
    assert entries["cli-none"].is_pinned is True


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
    assert {k: v.is_pinned for k, v in snapshot.entries.items()} == {
        "cli-store": False
    }

    scan, _totals = read_session_index(root, ledger_path=tmp_path / "missing.json")
    assert {k: v.is_pinned for k, v in scan.items()} == {
        k: v.is_pinned for k, v in snapshot.entries.items()
    }


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
    ledger = _ledger(
        tmp_path, {"local_esc.json": {"isPinned": True, "pinnedRank": 0}}
    )
    entries, _totals = read_session_index(root, ledger_path=ledger)
    assert entries["cli-esc"].is_pinned is True


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
    entries, _totals = read_session_index(root, ledger_path=tmp_path / "missing.json")
    assert entries["cli-tie"].is_pinned is True


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

    ledger = _ledger(
        tmp_path, {"local_flip.json": {"isPinned": True, "pinnedRank": 2}}
    )
    entries, _totals = read_session_index(root, ledger_path=ledger)
    assert entries["cli-flip"].is_pinned is True, "a coin flip cleared a real pin"


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
    assert {k: v.is_pinned for k, v in snapshot.entries.items()} == {"cli-sorg": True}
