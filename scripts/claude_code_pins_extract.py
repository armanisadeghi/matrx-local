#!/Users/armanisadeghi/.claude/.sync-venv/bin/python3
"""Extract Claude Code pins and category (custom group) assignments.

THE CANONICAL SOURCE of ``~/.claude/claude-code-pins-extract.py`` — the helper
the machine's launchd session-sync agent (``~/.claude/sync-claude-code-sessions.py``)
runs every pass to refresh the canonical sidebar ledger
(``~/.claude/claude-code-sidebar-state.json``). Install it with::

    cp scripts/claude_code_pins_extract.py ~/.claude/claude-code-pins-extract.py

It lives in this repo because the pin rule it applies is the SAME rule this
repo's engine applies in
:class:`app.services.coding_sessions.claude_session_index.LivePins`, and the two
must not drift: ``tests/unit/test_claude_pins_extractor.py`` fails if they
disagree on one conversation.

WHERE EACH PIECE OF STATE LIVES
-------------------------------
Two different stores, and confusing them is the bug this file was rewritten to
fix (2026-09-17).

* **The pin** is ``isStarred`` on the app's own per-conversation index record,
  under ``<app support>/Claude/claude-code-sessions/<account>/<org>/local_<id>.json``.
  It is per account+org: this Mac holds 48 scopes whose pinned counts range
  140-256, and only the scope the app is signed into matches what the sidebar
  shows. An unpin sets the field to ``false`` (or the record simply carries no
  pin key), so an unpin is finally representable.

* **The display order** of pinned items, and the custom sidebar groups, live in
  the app's embedded Chromium localStorage (a LevelDB) for the claude.ai origin::

      LSS-persisted.dframe-local-slice   {"value": {"pinnedOrder": ["code:local_<id>", ...]}}
      LSS-persisted.dframe-group-scopes  {"value": {"<account>/<org>": {
                                            "groups": [{"id": "cg-...", "name": "..."}],
                                            "assignments": {"code:local_<id>": "cg-..."}}}}

  ``pinnedOrder`` is APPEND-ONLY: it keeps a reference forever after the person
  unpins. Measured 2026-09-17 on this Mac — it held 295 refs while the app
  showed 218 pinned (111 of its refs were for conversations that are not
  pinned, and it was missing 34 that are). So it is read for RANK ONLY and is
  never allowed to decide whether something is pinned.

UNKNOWN IS NEVER FALSE
----------------------
When the pin cannot be read — no session-index root, no scope carrying the
app's ``lastFocusedAt`` signal, or a freshly signed-in scope with no pin
opinion at all — the ``pin_states`` key is OMITTED and ``pin_note`` says why.
The caller must then leave every ledger pin exactly as it was. Writing
``false`` in that case would clear the whole sidebar (and, through the bridge,
the server's pinned list) in one pass.

LevelDB cannot be read with the stdlib (snappy-compressed tables), so this
helper runs under ~/.claude/.sync-venv (Python 3.14 + ccl-chromium-reader).
READ-ONLY: the database is copied to a temp dir first, so it is safe while the
app is running. The session-index records are only ever read.

Output (stdout, JSON)::

  {"ok": true,
   "pin_source": "isStarred",
   "pin_scope": "<account>/<org>",
   "pin_states": {"local_<id>.json": true|false, ...},   # omitted when unknown
   "pinned": {"local_<id>.json": <rank or null>, ...},    # the pinned subset
   "categories": {"local_<id>.json": "<group name>", ...},
   "groups": [{"id": "cg-...", "name": "...", "scope": "<account>/<org>"}, ...],
   "counts": {...}}

``--dry-run --diff`` prints a human-readable per-conversation diff of what this
extraction would change in the ledger, and re-derives the app's ``isStarred``
truth independently to prove the published verdicts match it, instead of the
JSON. It writes nothing either way — this helper never writes.

On any failure prints {"ok": false, "error": "..."} and exits 0 so the caller
degrades gracefully.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import stat
import sys
import tempfile

LEVELDB_DIR = os.path.expanduser(
    "~/Library/Application Support/Claude/Local Storage/leveldb"
)
SESSIONS_ROOT = os.path.expanduser(
    "~/Library/Application Support/Claude/claude-code-sessions"
)
LEDGER_PATH = os.path.expanduser("~/.claude/claude-code-sidebar-state.json")
ORDER_KEYS = ("LSS-persisted.dframe-local-slice", "dframe-store")
GROUPS_KEY = "LSS-persisted.dframe-group-scopes"
# This Mac holds 75,666 index records across 48 scopes. The cap is a guard
# against a runaway tree, not a working limit.
MAX_INDEX_FILES = 250_000
MAX_INDEX_FILE_BYTES = 8_388_608
# ``lastFocusedAt`` sits in the first object of a small record; reading a
# bounded prefix keeps the scope scan to one short read per file.
_FOCUSED_PREFIX_BYTES = 8192
_FOCUSED_RE = re.compile(rb'"lastFocusedAt"\s*:\s*(\d+)')


def session_name(ref: object) -> str | None:
    """'code:local_<id>' -> 'local_<id>.json' (the index/ledger key)."""
    if not isinstance(ref, str):
        return None
    sid = ref.removeprefix("code:")
    if not sid.startswith("local_"):
        return None
    return f"{sid}.json"


def record_is_starred(record: dict) -> bool | None:
    """The app's pin field on one record. ``None`` = no pin key at all.

    Mirrors ``app.services.coding_sessions.claude_session_index.record_is_starred``.
    """
    value = record.get("isStarred")
    return value if isinstance(value, bool) else None


def _index_files(root: str) -> list[str]:
    if not os.path.isdir(root):
        return []
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.startswith("local_") and name.endswith(".json"):
                found.append(os.path.join(dirpath, name))
                if len(found) >= MAX_INDEX_FILES:
                    return found
    return found


def active_index_scope(root: str = SESSIONS_ROOT) -> str | None:
    """The ``<account>/<org>`` folder the desktop app is CURRENTLY signed into.

    ``lastFocusedAt`` is the signal, exactly as
    :func:`app.services.coding_sessions.claude_session_index.active_index_scope`
    uses it: the app writes it only in the scope in use, and the session-sync
    agent never copies it between scopes (it syncs title / titleSource /
    isArchived only), so the scope holding the newest one is the live scope.
    Verified 2026-09-17 against the app's own reported pin state on 28 of 28
    sampled conversations, including all 14 with no pin record.

    ``None`` when no record carries the signal — the caller must then keep the
    pins it already has rather than conclude that nothing is pinned.
    """
    best: tuple[int, str] | None = None
    for path in _index_files(root):
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INDEX_FILE_BYTES:
                continue
            with open(path, "rb") as handle:
                blob = handle.read(_FOCUSED_PREFIX_BYTES)
        except OSError:
            continue
        match = _FOCUSED_RE.search(blob)
        if match is None:
            continue
        focused = int(match.group(1))
        if focused > 0 and (best is None or focused > best[0]):
            best = (focused, os.path.dirname(path))
    return best[1] if best else None


def scope_pin_states(scope: str) -> dict[str, bool | None]:
    """Ledger key -> ``isStarred`` for every record in one scope.

    ``None`` = the record carries no pin key. Records are keyed by their own
    filename, which is what the sidebar ledger is keyed by (the app's
    ``local_<sessionId>``, which is NOT the ``cliSessionId``: they differ on
    1,542 of this Mac's 1,568 in-scope records).
    """
    states: dict[str, bool | None] = {}
    try:
        names = sorted(os.listdir(scope))
    except OSError:
        return states
    for name in names:
        if not (name.startswith("local_") and name.endswith(".json")):
            continue
        path = os.path.join(scope, name)
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INDEX_FILE_BYTES:
                continue
            with open(path, "rb") as handle:
                record = json.loads(handle.read())
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(record, dict):
            states[name] = record_is_starred(record)
    return states


def pin_verdicts(states: dict[str, bool | None]) -> dict[str, bool] | None:
    """The published pin opinion, or ``None`` when there is none to publish.

    THE RULE, identical to
    :meth:`app.services.coding_sessions.claude_session_index.LivePins.resolve`:

    * ``isStarred: true``  -> pinned.
    * ``isStarred: false`` -> an OBSERVED UNPIN.
    * no pin key at all, in a scope that speaks -> an honest "not pinned"; the
      app reports an absent pin record that way, and 14 of 14 sampled absences
      matched on 2026-09-17.
    * a scope with NO boolean anywhere is a freshly signed-in account, not a
      person who unpinned everything: ``None``, so the caller changes nothing.
    """
    if not any(isinstance(value, bool) for value in states.values()):
        return None
    return {name: value is True for name, value in states.items()}


def read_localstorage(leveldb_dir: str = LEVELDB_DIR) -> dict[str, str]:
    """Latest raw value for each key this helper reads. Raises on failure."""
    from ccl_chromium_reader.ccl_chromium_localstorage import LocalStoreDb

    tmp = tempfile.mkdtemp(prefix="claude-ls-")
    try:
        copy = os.path.join(tmp, "leveldb")
        shutil.copytree(leveldb_dir, copy)
        # LOCK belongs to the live app process; the copy must not look locked.
        lock = os.path.join(copy, "LOCK")
        if os.path.exists(lock):
            os.remove(lock)
        latest: dict[str, tuple[int, str]] = {}
        db = LocalStoreDb(pathlib.Path(copy))
        wanted = set(ORDER_KEYS) | {GROUPS_KEY}
        for rec in db.iter_all_records():
            if rec.script_key in wanted and rec.value is not None:
                seq = rec.leveldb_seq_number
                cur = latest.get(rec.script_key)
                if cur is None or seq > cur[0]:
                    latest[rec.script_key] = (seq, rec.value)
        return {key: value for key, (_seq, value) in latest.items()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pinned_order_ranks(raw: dict[str, str]) -> dict[str, int]:
    """Ledger key -> display rank from ``pinnedOrder``. RANK ONLY.

    Its membership is NOT a pin: it is append-only and keeps refs to
    conversations the person unpinned long ago.
    """
    ranks: dict[str, int] = {}
    for key in ORDER_KEYS:
        blob = raw.get(key)
        if blob is None:
            continue
        try:
            doc = json.loads(blob)
        except (TypeError, ValueError):
            continue
        body = doc.get("value") if "value" in doc else doc.get("state", {})
        order = body.get("pinnedOrder") if isinstance(body, dict) else None
        if isinstance(order, list) and order:
            for rank, ref in enumerate(order):
                name = session_name(ref)
                if name is not None and name not in ranks:
                    ranks[name] = rank
            break  # first key with a non-empty order wins
    return ranks


def categories_and_groups(raw: dict[str, str]) -> tuple[dict[str, str], list[dict]]:
    categories: dict[str, str] = {}
    groups: list[dict[str, str]] = []
    blob = raw.get(GROUPS_KEY)
    if blob is None:
        return categories, groups
    try:
        scopes = json.loads(blob).get("value", {})
    except (TypeError, ValueError):
        scopes = {}
    if not isinstance(scopes, dict):
        return categories, groups
    for scope, data in scopes.items():
        if not isinstance(data, dict):
            continue
        names: dict[str, str] = {}
        for group in data.get("groups", []) or []:
            if isinstance(group, dict) and group.get("id") and group.get("name"):
                names[group["id"]] = group["name"]
                groups.append(
                    {"id": group["id"], "name": group["name"], "scope": scope}
                )
        for ref, gid in (data.get("assignments") or {}).items():
            name = session_name(ref)
            label = names.get(gid)
            if name is not None and label and name not in categories:
                categories[name] = label
    return categories, groups


def extract(
    *, sessions_root: str = SESSIONS_ROOT, leveldb_dir: str = LEVELDB_DIR
) -> dict:
    """The whole extraction. ``ok: false`` only when nothing could be read."""
    if not os.path.isdir(leveldb_dir):
        return {"ok": False, "error": f"not found: {leveldb_dir}"}
    raw = read_localstorage(leveldb_dir)
    ranks = pinned_order_ranks(raw)
    categories, groups = categories_and_groups(raw)
    result: dict = {
        "ok": True,
        "pin_source": "isStarred",
        "categories": categories,
        "groups": groups,
    }
    scope = active_index_scope(sessions_root)
    states: dict[str, bool | None] = {}
    verdicts: dict[str, bool] | None = None
    if scope is None:
        result["pin_note"] = (
            "no account+org scope carries the app's lastFocusedAt signal, so the "
            "pin is UNKNOWN — keep the pins the ledger already holds"
        )
    else:
        result["pin_scope"] = os.path.join(
            os.path.basename(os.path.dirname(scope)), os.path.basename(scope)
        )
        states = scope_pin_states(scope)
        verdicts = pin_verdicts(states)
        if verdicts is None:
            result["pin_note"] = (
                f"the signed-in scope holds {len(states)} record(s) and no pin "
                "opinion at all (a freshly signed-in account), so the pin is "
                "UNKNOWN — keep the pins the ledger already holds"
            )
    if verdicts is not None:
        result["pin_states"] = verdicts
        result["pinned"] = {
            name: ranks.get(name) for name, pinned in verdicts.items() if pinned
        }
    result["counts"] = {
        "scope_records": len(states),
        "starred_true": sum(1 for v in states.values() if v is True),
        "starred_false": sum(1 for v in states.values() if v is False),
        "starred_absent": sum(1 for v in states.values() if v is None),
        "order_refs": len(ranks),
        "ranked_pins": sum(
            1 for name in (result.get("pinned") or {}) if ranks.get(name) is not None
        ),
    }
    return result


def _load_ledger(path: str) -> dict[str, dict]:
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def print_diff(result: dict, ledger_path: str, sessions_root: str) -> int:
    """Per-conversation diff vs the ledger and vs the app's own truth.

    Two comparisons, because they answer different questions:

    * **vs the ledger** — what this extraction would change on the next pass.
    * **vs the app's truth** — the ``isStarred`` state re-read straight from the
      records, keyed independently of the published verdicts. It must be 0
      differences: anything else is a bug in the join or the rank, and shipping
      on a non-zero number would put a fresh lie in the ledger.
    """
    ledger = _load_ledger(ledger_path)
    states = result.get("pin_states")
    print(f"extraction: pin_source={result.get('pin_source')} "
          f"scope={result.get('pin_scope', '(none)')}")
    print(f"counts: {json.dumps(result.get('counts', {}), sort_keys=True)}")
    if result.get("pin_note"):
        print(f"pin opinion: UNKNOWN — {result['pin_note']}")
    if states is None:
        print("\nNo pin verdicts to publish; the ledger would be left untouched.")
        return 0

    # ---- vs the app's own truth, re-derived independently ------------------
    scope = active_index_scope(sessions_root)
    truth = {
        name: value is True
        for name, value in (scope_pin_states(scope) if scope else {}).items()
    }
    truth_pinned = {name for name, pinned in truth.items() if pinned}
    published = {name for name, pinned in states.items() if pinned}
    only_published = sorted(published - truth_pinned)
    only_truth = sorted(truth_pinned - published)
    keyspace = sorted(set(states) ^ set(truth))
    print(f"\nvs the app's isStarred truth: {len(truth_pinned)} pinned in the app, "
          f"{len(published)} published")
    for name in only_published:
        print(f"  DIFF published-pinned, app-not-pinned: {name}")
    for name in only_truth:
        print(f"  DIFF app-pinned, published-not-pinned: {name}")
    for name in keyspace:
        print(f"  DIFF keyspace mismatch: {name}")
    truth_diffs = len(only_published) + len(only_truth) + len(keyspace)
    print(f"  differences vs the app's truth: {truth_diffs}")

    # ---- vs the current ledger --------------------------------------------
    ranks = {name: rank for name, rank in (result.get("pinned") or {}).items()}
    pin_flips: list[str] = []
    rank_only: list[str] = []
    unknown_kept: list[str] = []
    for name, fields in sorted(ledger.items()):
        if name not in states:
            if fields.get("isPinned") is not None:
                unknown_kept.append(name)
            continue
        was, now = fields.get("isPinned"), states[name]
        if was != now:
            pin_flips.append(
                f"  {'PIN  ' if now else 'UNPIN'} {name}  ledger={was!r} -> {now!r}"
                f"   {fields.get('title') or '(untitled)'}"
            )
        elif now and fields.get("pinnedRank") != ranks.get(name):
            rank_only.append(
                f"  RANK  {name}  {fields.get('pinnedRank')!r} -> {ranks.get(name)!r}"
            )
    added = sorted(set(states) - set(ledger))
    cats = result.get("categories") or {}
    cat_flips = [
        name for name, fields in ledger.items()
        if name in states and (fields.get("categoryName") or None) != cats.get(name)
    ]
    ledger_pinned = sum(1 for f in ledger.values() if f.get("isPinned"))
    print(f"\nvs the current ledger ({len(ledger)} entries, {ledger_pinned} pinned):")
    for line in pin_flips:
        print(line)
    for line in rank_only:
        print(line)
    print(f"  pin flips: {len(pin_flips)} "
          f"({sum(1 for line in pin_flips if line.strip().startswith('PIN'))} pin, "
          f"{sum(1 for line in pin_flips if line.strip().startswith('UNPIN'))} unpin)")
    print(f"  rank-only changes: {len(rank_only)}")
    print(f"  category changes: {len(cat_flips)}")
    print(f"  ledger entries this extraction cannot speak to (left untouched): "
          f"{len(unknown_kept)}")
    print(f"  conversations not yet in the ledger: {len(added)}")
    print(f"\nledger pinned {ledger_pinned} -> {len(published)}")
    print("SHIPPABLE" if truth_diffs == 0 else "NOT SHIPPABLE: see the DIFF lines above")
    return 0 if truth_diffs == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="never write anything (this helper never does)")
    parser.add_argument("--diff", action="store_true",
                        help="print a per-conversation diff instead of JSON")
    parser.add_argument("--ledger", default=LEDGER_PATH)
    parser.add_argument("--sessions-root", default=SESSIONS_ROOT)
    parser.add_argument("--leveldb", default=LEVELDB_DIR)
    args = parser.parse_args(argv)
    try:
        from ccl_chromium_reader.ccl_chromium_localstorage import (  # noqa: F401
            LocalStoreDb,
        )
    except ImportError as exc:
        print(json.dumps({"ok": False, "error": f"ccl_chromium_reader missing: {exc}"}))
        return 0
    try:
        result = extract(sessions_root=args.sessions_root, leveldb_dir=args.leveldb)
    except Exception as exc:  # noqa: BLE001 - caller must always get JSON
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 0
    if args.diff:
        if not result.get("ok"):
            print(f"extraction failed: {result.get('error')}")
            return 1
        return print_diff(result, args.ledger, args.sessions_root)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
