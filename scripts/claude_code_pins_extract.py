#!/Users/armanisadeghi/.claude/.sync-venv/bin/python3
"""Extract Claude Code pins and category (custom group) assignments.

THE CANONICAL SOURCE of ``~/.claude/claude-code-pins-extract.py`` — the helper
the machine's launchd session-sync agent (``~/.claude/sync-claude-code-sessions.py``)
runs every pass. The agent stores each account's starred list this helper reads
in ``~/.claude/claude-code-pin-observations.json`` and folds the union of every
account's list into the canonical sidebar ledger
(``~/.claude/claude-code-sidebar-state.json``). Install it with::

    scripts/install_pins_extractor.sh        # this file AND claude_scope.py

It lives in this repo because this repo's engine reads the SAME observations
file (:class:`app.services.coding_sessions.claude_session_index.LivePins`), and
``tests/unit/test_claude_pins_extractor.py`` fails if the two disagree.

THE SCOPE RULE IS NOT IN THIS FILE. Which account the app is signed into is
decided once, in ``app/services/coding_sessions/claude_scope.py``, which the
installer copies beside this script as ``~/.claude/claude_scope.py`` and which
this script imports. If that import fails, this script publishes NO pin verdict
at all rather than fall back to a second copy of the rule that could drift.

WHERE THE PIN LIVES (measured 2026-09-18 on Arman's Mac)
-------------------------------------------------------
The Claude desktop sidebar draws pinned Claude Code conversations from ONE list
in the app's claude.ai IndexedDB::

    <app support>/Claude/IndexedDB/https_claude.ai_0.indexeddb.leveldb
      database keyval-store, object store keyval,
      key "store:pin-state:dframe-starred-code"
      value {"state": {"starredIds": ["local_<id>", ..., "session_<cloud id>"]},
             "version": 0, "updatedAt": <ms>}

``local_<id>`` names a local conversation by its index-record filename
(``local_<id>.json``, the ledger's key); ``session_<id>`` is a cloud session.
The list is rebuilt on every account switch: at the 10:27 switch the app wrote
a PARTIAL value (53 local, 0 cloud) at 10:27:00.245 and the full one (53 local
+ 2 cloud) at 10:27:00.786. So the latest value (highest ``updatedAt``) is the
signed-in account's list ONLY when it was written after that switch, and an
EMPTY LATEST VALUE IS UNKNOWN — never "unpin everything".

ATTRIBUTION. The app states each switch itself, in localStorage:
``rq-cache-confirmed-account-session`` = ``{"account": <uuid>, "marker": <ms>}``
and ``frame-pinned-valve:<org>:<account>`` = ``<ms>`` (10:26:59.844 at that
switch). A list is attributed to the signed-in account only when its
``updatedAt`` is LATER than the newest such marker for that account (and the
session marker names the same account). Otherwise a pass landing between the
switch and the refill would file the previous account's list under the new
one. Without any app marker the ``config.json`` mtime stands in; a list not
later than the marker is UNKNOWN for this pass. (Whether LOCAL pins are really
per account is unproven: the local part did not change across that switch.)

``isStarred`` on the per-conversation index records is NOT the pin. That day
the signed-in scope carried ``isStarred: true`` on 206 unarchived
conversations while the sidebar showed ~48 and ``starredIds`` held 56;
conversations Arman does not see pinned (``local_d5542855…`` "Prompt",
``local_08264bc8…`` "Extension vault") had ``isStarred: true`` and are absent
from ``starredIds``. The flag also spreads, because the session-sync agent
copies whole records between the 48 account/org folders. It is reported in
``counts`` as a diagnostic only and decides nothing. (The 2026-09-17 version of
this file used it as the truth; AI Matrx ended up with 285 favourites vs 56.)

The custom sidebar groups still live in the app's Chromium localStorage::

    LSS-persisted.dframe-group-scopes  {"value": {"<account>/<org>": {
                                          "groups": [{"id": "cg-...", "name": "..."}],
                                          "assignments": {"code:local_<id>": "cg-..."}}}}

UNKNOWN IS NEVER FALSE
----------------------
When the pin cannot be read — the IndexedDB is unreadable, its latest value is
empty, or the app has not stated which account it is signed into (or two of
its own files disagree) — ``app_starred`` and ``pin_states`` are OMITTED and
``pin_note`` says why in English. The caller must then record no observation
and leave every ledger pin exactly as it was.

Both stores are LevelDBs that cannot be read with the stdlib, so this helper
runs under ~/.claude/.sync-venv (Python 3.14 + ccl-chromium-reader).
READ-ONLY: each database is copied to a temp dir (LOCK removed from the copy)
first, so it is safe while the app is running. Nothing the app owns is written.

Output (stdout, JSON)::

  {"ok": true,
   "pin_source": "app_starred",
   "app_starred": {"key": "...", "updated_at": <ms>, "account": "<uuid>",
                   "local": ["local_<id>.json", ...],   # the list's order
                   "cloud": ["session_...", ...]},       # omitted when unknown
   "pin_scope": "<account>/<org>",
   "pin_states": {"local_<id>.json": true|false, ...},   # the active scope's
                                                         # records; omitted when unknown
   "pinned": {"local_<id>.json": <rank>, ...},            # the ordered local list
   "categories": {"local_<id>.json": "<group name>", ...},
   "groups": [{"id": "cg-...", "name": "...", "scope": "<account>/<org>"}, ...],
   "counts": {...}}                                       # isStarred = diagnostics

``--dry-run --diff`` prints a human-readable per-conversation diff of this
account's list against the ledger, and re-reads the IndexedDB independently to
prove the published verdicts match it, instead of the JSON. It writes nothing
either way — this helper never writes.

On any failure prints {"ok": false, "error": "..."} and exits 0 so the caller
degrades gracefully.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile

# THE scope rule, imported — never re-implemented here. Two layouts:
# installed (this script and claude_scope.py side by side in ~/.claude) and
# in-repo (tests load this file by path while the repo is importable).
try:  # installed layout
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from claude_scope import (  # type: ignore[import-not-found]
        ScopeResolution,
        resolve_active_scope,
    )
except ImportError:  # in-repo layout
    try:
        from app.services.coding_sessions.claude_scope import (  # noqa: F401
            ScopeResolution,
            resolve_active_scope,
        )
    except ImportError as _scope_exc:  # nothing to apply the rule with
        SCOPE_IMPORT_ERROR: str | None = (
            "the shared scope rule (claude_scope.py) is not importable "
            f"({_scope_exc}); install it beside this script with "
            "scripts/install_pins_extractor.sh. No pin verdict is published, "
            "so every pin the ledger holds stands."
        )
    else:
        SCOPE_IMPORT_ERROR = None
else:
    SCOPE_IMPORT_ERROR = None

# The app's own localStorage statement of the signed-in account — a third
# signal claude_scope cannot read for itself, because reading it needs the
# LevelDB this script already opens for the categories.
ACCOUNT_KEY = "rq-cache-confirmed-account"
# The app's own account-switch markers (see ATTRIBUTION above).
SESSION_MARKER_KEY = "rq-cache-confirmed-account-session"
VALVE_PREFIX = "frame-pinned-valve:"

LEVELDB_DIR = os.path.expanduser(
    "~/Library/Application Support/Claude/Local Storage/leveldb"
)
INDEXEDDB_DIR = os.path.expanduser(
    "~/Library/Application Support/Claude/IndexedDB/https_claude.ai_0.indexeddb.leveldb"
)
SESSIONS_ROOT = os.path.expanduser(
    "~/Library/Application Support/Claude/claude-code-sessions"
)
LEDGER_PATH = os.path.expanduser("~/.claude/claude-code-sidebar-state.json")
GROUPS_KEY = "LSS-persisted.dframe-group-scopes"
STARRED_DATABASE = "keyval-store"
STARRED_STORE = "keyval"
STARRED_KEY = "store:pin-state:dframe-starred-code"
# This Mac holds 79,152 index records across 49 scopes. The cap is a guard
# against a runaway tree, not a working limit.
MAX_INDEX_FILE_BYTES = 8_388_608


def session_name(ref: object) -> str | None:
    """'code:local_<id>' or 'local_<id>' -> 'local_<id>.json' (the ledger key)."""
    if not isinstance(ref, str):
        return None
    sid = ref.removeprefix("code:")
    if not sid.startswith("local_") or len(sid) <= len("local_"):
        return None
    return f"{sid}.json"


def record_is_starred(record: dict) -> bool | None:
    """The record's ``isStarred`` — a DIAGNOSTIC, never the pin (see above)."""
    value = record.get("isStarred")
    return value if isinstance(value, bool) else None


def scope_starred_flags(scope: str) -> dict[str, bool | None]:
    """Ledger key -> ``isStarred`` for every record in one scope.

    The keys are the scope's conversations — the keyspace ``pin_states`` is
    published over. The values are diagnostics only. Records are keyed by
    their own filename, which is what the sidebar ledger and ``starredIds``
    are keyed by (the app's ``local_<sessionId>``, which is NOT the
    ``cliSessionId``).
    """
    flags: dict[str, bool | None] = {}
    try:
        names = sorted(os.listdir(scope))
    except OSError:
        return flags
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
            flags[name] = record_is_starred(record)
    return flags


def _copy_leveldb(source: str, prefix: str) -> tuple[str, str]:
    """Copy a live LevelDB to a temp dir the reader can open. -> (tmp, copy)."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    copy = os.path.join(tmp, os.path.basename(source.rstrip("/")))
    shutil.copytree(source, copy)
    # LOCK belongs to the live app process; the copy must not look locked.
    lock = os.path.join(copy, "LOCK")
    if os.path.exists(lock):
        os.remove(lock)
    return tmp, copy


def read_localstorage(leveldb_dir: str = LEVELDB_DIR) -> dict[str, str]:
    """Latest raw value for each localStorage key this helper reads."""
    from ccl_chromium_reader.ccl_chromium_localstorage import LocalStoreDb

    tmp, copy = _copy_leveldb(leveldb_dir, "claude-ls-")
    try:
        latest: dict[str, tuple[int, str]] = {}
        db = LocalStoreDb(pathlib.Path(copy))
        wanted = {GROUPS_KEY, ACCOUNT_KEY, SESSION_MARKER_KEY}
        for rec in db.iter_all_records():
            if rec.value is not None and (
                rec.script_key in wanted or rec.script_key.startswith(VALVE_PREFIX)
            ):
                seq = rec.leveldb_seq_number
                cur = latest.get(rec.script_key)
                if cur is None or seq > cur[0]:
                    latest[rec.script_key] = (seq, rec.value)
        return {key: value for key, (_seq, value) in latest.items()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_starred_values(indexeddb_dir: str = INDEXEDDB_DIR) -> list[tuple[int, str]]:
    """Every stored version of the starred key: ``[(leveldb seq, raw value)]``.

    All versions, live or superseded, because the latest-by-``updatedAt`` rule
    (:func:`latest_starred`) is applied to the values themselves. Raises when
    the database cannot be opened.
    """
    from ccl_chromium_reader import ccl_chromium_indexeddb

    tmp, copy = _copy_leveldb(indexeddb_dir, "claude-idb-")
    try:
        wrapped = ccl_chromium_indexeddb.WrappedIndexDB(pathlib.Path(copy))
        names = {entry.name for entry in wrapped.database_ids}
        if STARRED_DATABASE not in names:
            raise LookupError(
                f"the app's IndexedDB holds no {STARRED_DATABASE!r} database"
            )
        store = wrapped[STARRED_DATABASE][STARRED_STORE]
        values: list[tuple[int, str]] = []
        for rec in store.iterate_records():
            if getattr(rec.key, "value", None) != STARRED_KEY:
                continue
            value = rec.value
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
            if isinstance(value, str):
                values.append((int(getattr(rec, "ldb_seq_no", 0) or 0), value))
        return values
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def latest_starred(values: list[tuple[int, str]]) -> dict | None:
    """The newest parseable version: ``{"updated_at": ms, "ids": [...]}``.

    Newest = highest ``updatedAt`` (the app's own write time), then the
    LevelDB sequence number. ``None`` when no version parses at all.
    """
    best: tuple[int, int, list] | None = None
    for seq, raw in values:
        try:
            doc = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        state = doc.get("state")
        ids = state.get("starredIds") if isinstance(state, dict) else None
        updated = doc.get("updatedAt")
        if not isinstance(ids, list) or not isinstance(updated, int):
            continue
        if best is None or (updated, seq) > (best[0], best[1]):
            best = (updated, seq, ids)
    if best is None:
        return None
    return {"updated_at": best[0], "ids": best[2]}


def split_starred(ids: list) -> tuple[list[str], list[str]]:
    """``starredIds`` -> (local ledger keys, cloud session ids), order kept."""
    local: list[str] = []
    cloud: list[str] = []
    for ref in ids:
        name = session_name(ref)
        if name is not None:
            if name not in local:
                local.append(name)
        elif isinstance(ref, str) and ref.startswith("session_") and ref not in cloud:
            cloud.append(ref)
    return local, cloud


def switch_marker(
    raw: dict[str, str], account: str | None, config_path: str | None = None
) -> tuple[int | None, str, str | None]:
    """``(marker ms, source, refusal)`` — the newest switch INTO ``account``.

    ``refusal`` is set when the app's own session marker names a DIFFERENT
    account: the app is mid-switch, so nothing may be attributed this pass.
    """
    markers: list[tuple[int, str]] = []
    blob = raw.get(SESSION_MARKER_KEY)
    if blob:
        try:
            doc = json.loads(blob)
        except (TypeError, ValueError):
            doc = None
        if isinstance(doc, dict):
            named = doc.get("account")
            if account and isinstance(named, str) and named and named != account:
                return None, SESSION_MARKER_KEY, (
                    f"the app's session marker names account {named} while the "
                    f"signed-in account is {account} — mid-switch, so the list is "
                    "UNKNOWN for this pass"
                )
            try:
                markers.append((int(str(doc.get("marker"))), SESSION_MARKER_KEY))
            except (TypeError, ValueError):
                pass
    for key, value in raw.items():
        if account and key.startswith(VALVE_PREFIX) and key.endswith(f":{account}"):
            try:
                markers.append((int(str(value).strip().strip('"')), key))
            except (TypeError, ValueError):
                pass
    if markers:
        at, source = max(markers)
        return at, source, None
    if config_path:
        try:
            return int(os.stat(config_path).st_mtime * 1000), config_path, None
        except OSError:
            pass
    return None, "none", None


def app_starred_from(
    values: list[tuple[int, str]],
    account: str | None,
    marker: int | None = None,
) -> tuple[dict | None, str | None]:
    """``(app_starred, note)`` — exactly one of the two is ``None``.

    THE RULE: the latest value is the signed-in account's list only when it
    postdates the switch ``marker``; an empty latest value, an unparseable
    store, an unknown account, or a list not later than the marker is UNKNOWN.
    """
    latest = latest_starred(values)
    if latest is None:
        return None, (
            f"the app's IndexedDB holds no readable {STARRED_KEY!r} value, so the "
            "pin is UNKNOWN — keep the pins the ledger already holds"
        )
    local, cloud = split_starred(latest["ids"])
    if not local and not cloud:
        return None, (
            f"the latest {STARRED_KEY!r} value (updatedAt {latest['updated_at']}) is "
            "empty, so the pin is UNKNOWN — never 'unpin everything'"
        )
    if not account:
        return None, (
            "the app's starred list was read but the account it belongs to is "
            "UNKNOWN — keep the pins the ledger already holds"
        )
    if marker is not None and latest["updated_at"] <= marker:
        return None, (
            f"the starred list (updatedAt {latest['updated_at']}) is not later than "
            f"the app's last switch into account {account} (marker {marker}), so it "
            "may still be the previous account's list — UNKNOWN for this pass"
        )
    return {
        "key": STARRED_KEY,
        "updated_at": latest["updated_at"],
        "switch_marker_at": marker,
        "account": account,
        "local": local,
        "cloud": cloud,
    }, None


def categories_and_groups(raw: dict[str, str]) -> tuple[dict[str, str], list[dict]]:
    categories: dict[str, str] = {}
    groups: list[dict[str, str]] = []
    blob = raw.get(GROUPS_KEY)
    if blob is None:
        return categories, groups
    try:
        scopes = json.loads(blob).get("value", {})
    except (TypeError, ValueError, AttributeError):
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


def _counts(flags: dict[str, bool | None], app_starred: dict | None) -> dict:
    local = set((app_starred or {}).get("local") or [])
    return {
        "scope_records": len(flags),
        "app_starred_local": len(local),
        "app_starred_cloud": len((app_starred or {}).get("cloud") or []),
        "app_starred_in_scope": sum(1 for name in local if name in flags),
        # Diagnostics only: the records' own flag, which is NOT the pin.
        "diag_isStarred_true": sum(1 for v in flags.values() if v is True),
        "diag_isStarred_true_not_starred": sum(
            1 for name, v in flags.items() if v is True and name not in local
        ),
    }


def extract(
    *,
    sessions_root: str = SESSIONS_ROOT,
    leveldb_dir: str = LEVELDB_DIR,
    indexeddb_dir: str = INDEXEDDB_DIR,
) -> dict:
    """The whole extraction. ``ok: false`` only when nothing could be read."""
    if not os.path.isdir(leveldb_dir):
        return {"ok": False, "error": f"not found: {leveldb_dir}"}
    raw = read_localstorage(leveldb_dir)
    categories, groups = categories_and_groups(raw)
    result: dict = {
        "ok": True,
        "pin_source": "app_starred",
        "categories": categories,
        "groups": groups,
    }
    flags: dict[str, bool | None] = {}
    if SCOPE_IMPORT_ERROR is not None:
        result["pin_note"] = SCOPE_IMPORT_ERROR
        result["counts"] = _counts(flags, None)
        return result
    # The app's localStorage statement of the account, handed to the shared
    # rule as a third signal. Every signal that is present must agree; a
    # disagreement is UNKNOWN, never a majority vote.
    extra = {}
    confirmed = (raw.get(ACCOUNT_KEY) or "").strip().strip('"')
    if confirmed:
        extra[ACCOUNT_KEY] = confirmed
    resolution: ScopeResolution = resolve_active_scope(
        pathlib.Path(sessions_root), extra_signals=extra or None
    )
    result["pin_scope_signals"] = dict(resolution.signals)
    try:
        values = read_starred_values(indexeddb_dir)
    except Exception as exc:  # noqa: BLE001 - an unreadable store is UNKNOWN
        values = []
        store_error: str | None = f"{type(exc).__name__}: {exc}"
    else:
        store_error = None
    if store_error is not None:
        app_starred, note = None, (
            f"the app's IndexedDB could not be read ({store_error}), so the pin "
            "is UNKNOWN — keep the pins the ledger already holds"
        )
    else:
        marker, marker_source, refusal = switch_marker(
            raw, resolution.account,
            os.path.join(os.path.dirname(os.path.abspath(sessions_root)), "config.json"),
        )
        result["switch_marker"] = {"at": marker, "source": marker_source}
        if refusal is not None:
            app_starred, note = None, refusal
        else:
            app_starred, note = app_starred_from(values, resolution.account, marker)
        if resolution.account is None and note is not None:
            note = f"{note} ({resolution.reason})"
    if note is not None:
        result["pin_note"] = note
    scope = str(resolution.scope) if resolution.scope else None
    if app_starred is not None:
        result["app_starred"] = app_starred
        result["pinned"] = {name: rank for rank, name in enumerate(app_starred["local"])}
        if scope is None:
            result["pin_note"] = (
                f"{resolution.reason} — the starred list is recorded for account "
                f"{app_starred['account']}, but no per-conversation verdict is "
                "published for an unresolved org scope"
            )
    if scope is not None:
        result["pin_scope"] = resolution.label
        result["pin_scope_reason"] = resolution.reason
        flags = scope_starred_flags(scope)
        if app_starred is not None:
            starred = set(app_starred["local"])
            result["pin_states"] = {name: name in starred for name in flags}
    result["counts"] = _counts(flags, app_starred)
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


def print_diff(result: dict, ledger_path: str, indexeddb_dir: str) -> int:
    """Per-conversation diff vs the app's starred list and vs the ledger.

    * **vs the app's truth** — the IndexedDB is read a SECOND time and the
      starred list re-derived independently of the published verdicts. It
      must be 0 differences: anything else is a bug in the join.
    * **vs the ledger** — what this ACCOUNT's list says against the ledger.
      The ledger holds the union of every account's list (the session-sync
      agent builds it), so a conversation another account pins shows here as
      "ledger pinned, not in this account's list" without being a fault.
    """
    ledger = _load_ledger(ledger_path)
    states = result.get("pin_states")
    starred = result.get("app_starred")
    print(f"extraction: pin_source={result.get('pin_source')} "
          f"scope={result.get('pin_scope', '(none)')}")
    print(f"counts: {json.dumps(result.get('counts', {}), sort_keys=True)}")
    if result.get("pin_note"):
        print(f"pin note: {result['pin_note']}")
    if starred is None:
        print("\nNo starred list to publish; no observation is recorded and the "
              "ledger is left untouched.")
        return 0
    print(f"app starred list: account {starred['account']}, updatedAt "
          f"{starred['updated_at']}, {len(starred['local'])} local, "
          f"{len(starred['cloud'])} cloud")

    # ---- vs the app's own truth, re-read independently ---------------------
    again, _note = app_starred_from(
        read_starred_values(indexeddb_dir), starred["account"],
        starred.get("switch_marker_at"),
    )
    truth = list((again or {}).get("local") or [])
    published = list(starred["local"])
    truth_diffs = 0
    if truth != published:
        for name in sorted(set(published) - set(truth)):
            print(f"  DIFF published, not in the app's list: {name}")
        for name in sorted(set(truth) - set(published)):
            print(f"  DIFF in the app's list, not published: {name}")
        if set(truth) == set(published):
            print("  DIFF same members, different order")
        truth_diffs = max(1, len(set(truth) ^ set(published)))
    if states is not None:
        wrong = sorted(
            name for name, pinned in states.items() if pinned != (name in set(truth))
        )
        for name in wrong:
            print(f"  DIFF pin_states disagrees with the app's list: {name}")
        truth_diffs += len(wrong)
    print(f"  differences vs the app's list: {truth_diffs}")

    # ---- vs the current ledger --------------------------------------------
    ranks = result.get("pinned") or {}
    ledger_pinned = {n for n, f in ledger.items() if f.get("isPinned")}
    this_account = set(published)
    pin_now = sorted(n for n in this_account if n in ledger and n not in ledger_pinned)
    only_ledger = sorted(ledger_pinned - this_account)
    not_in_ledger = sorted(this_account - set(ledger))
    print(f"\nvs the current ledger ({len(ledger)} entries, {len(ledger_pinned)} pinned):")
    for name in pin_now:
        print(f"  PIN   {name}  rank {ranks.get(name)}   "
              f"{ledger[name].get('title') or '(untitled)'}")
    for name in only_ledger:
        print(f"  LEDGER-ONLY {name}   {ledger[name].get('title') or '(untitled)'}")
    print(f"  in this account's list, not pinned in the ledger: {len(pin_now)}")
    print(f"  pinned in the ledger, not in this account's list: {len(only_ledger)} "
          "(pinned by another account, or unpinned on the next pass)")
    print(f"  in this account's list, not in the ledger at all: {len(not_in_ledger)}")
    isstarred_extra = (result.get("counts") or {}).get("diag_isStarred_true_not_starred")
    if isstarred_extra is not None:
        print(f"  diagnostic: records flagged isStarred=true but NOT in the list: "
              f"{isstarred_extra}")
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
    parser.add_argument("--indexeddb", default=INDEXEDDB_DIR)
    args = parser.parse_args(argv)
    try:
        from ccl_chromium_reader import ccl_chromium_indexeddb  # noqa: F401
        from ccl_chromium_reader.ccl_chromium_localstorage import (  # noqa: F401
            LocalStoreDb,
        )
    except ImportError as exc:
        print(json.dumps({"ok": False, "error": f"ccl_chromium_reader missing: {exc}"}))
        return 0
    try:
        result = extract(
            sessions_root=args.sessions_root,
            leveldb_dir=args.leveldb,
            indexeddb_dir=args.indexeddb,
        )
    except Exception as exc:  # noqa: BLE001 - caller must always get JSON
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 0
    if args.diff:
        if not result.get("ok"):
            print(f"extraction failed: {result.get('error')}")
            return 1
        return print_diff(result, args.ledger, args.indexeddb)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
