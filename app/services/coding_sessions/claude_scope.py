"""THE one rule for which Claude Code account+org scope is signed in.

Pins are per account+org. This Mac holds eight accounts and 49
``<account>/<org>`` scopes over 79,000 index records, and their ``isStarred``
counts range 140-256 — so a pinned count means nothing until the scope is
resolved, and resolving it wrong publishes another account's sidebar.

TWO READERS, ONE RULE. This module is the ONLY place the rule lives:

* the engine, through
  :func:`app.services.coding_sessions.claude_session_index.active_index_scope`;
* the launchd pin extractor, ``scripts/claude_code_pins_extract.py``, installed
  as ``~/.claude/claude-code-pins-extract.py`` with THIS FILE copied beside it
  as ``~/.claude/claude_scope.py`` (``scripts/install_pins_extractor.sh`` does
  both, with timestamped backups). The extractor imports it and refuses to
  publish any pin verdict if the import fails — it never carries a second copy
  of the rule.

WHY ``lastFocusedAt`` ALONE IS NOT THE SIGNAL (measured 2026-09-17/18)
----------------------------------------------------------------------
The previous rule was "the scope holding the newest ``lastFocusedAt`` is the
live scope", justified by a docstring claiming the session-sync agent never
copies that stamp between scopes. It does get copied, and a zero-authorship
verifier caught it costing real pins:

* one record (``local_4c4f75fd-…json``) exists in 49 scopes, and the identical
  stamp ``1789692097566`` sat in TWO different accounts' scopes at once;
* re-measured 2026-09-18, the single stamp ``1789714654476`` is the maximum in
  NINE scopes spread over FIVE different accounts;
* at 18:04:13 on 2026-09-17 the ledger the extractor wrote was, byte for byte,
  ``dev@aimatrx.com``'s starred set (218) while the app's own confirmed account
  was ``arman26@gmail.com`` (228 stars) — 21 pins that were not pinned and 31
  real pins missing, and the running engine reads that ledger at load.

So the account is never inferred from a per-record timestamp. The app states
its own account in its own plain-JSON files, and those are read directly:

* ``<app support>/Claude/config.json`` -> ``lastKnownAccountUuid``
* ``<app support>/Claude/cowork-enabled-cli-ops.json`` -> ``ownerAccountId``
* the extractor passes a third, ``rq-cache-confirmed-account`` from the app's
  localStorage, which it already opens for the pin order.

Every signal that is PRESENT must agree. Ranking by focus survives only for
the ORG, and only among the one resolved account's own orgs — which is where
the contamination cannot reach, because it crossed accounts.

UNKNOWN IS NEVER FALSE. When the account cannot be resolved — no signal, or
signals that disagree — the scope is ``None`` with an English reason, and the
caller must keep the pins it already has. Concluding "nothing is pinned" would
clear the whole sidebar, and through the bridge the server's pinned list, in
one pass.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# Bounded reads: a scope scan must not be able to walk a runaway tree, and
# ``lastFocusedAt`` sits in the first object of a small record, so a prefix
# read is enough to rank orgs.
MAX_INDEX_FILES = 250_000
MAX_INDEX_FILE_BYTES = 8_388_608
MAX_SIGNAL_FILE_BYTES = 8_388_608
_FOCUSED_PREFIX_BYTES = 8192
_FOCUSED_RE = re.compile(rb'"lastFocusedAt"\s*:\s*(\d+)')

#: ``(filename, json key)`` of every plain-file signal in which the desktop app
#: states the account it is signed into. Read in this order; all that are
#: present must agree.
ACCOUNT_SIGNAL_FILES: tuple[tuple[str, str], ...] = (
    ("config.json", "lastKnownAccountUuid"),
    ("cowork-enabled-cli-ops.json", "ownerAccountId"),
)

#: The app writes one of these per organisation it has loaded, in the same
#: config file, and the newest names the organisation in use.
ORG_SIGNAL_FILE = "config.json"
ORG_SIGNAL_PREFIX = "dxt:allowlistLastUpdated:"

# The account id becomes ONE path segment under the session-index root, so it
# is validated as a safe segment rather than as a UUID: the app owns its own
# id format, and this module must not reject a future one. What it must reject
# is a separator or a ``..`` that would walk out of the index tree.
_SAFE_SEGMENT_RE = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z")


def _safe_account(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _SAFE_SEGMENT_RE.match(candidate) or candidate in {".", ".."}:
        return None
    return candidate


def parse_stated_stamp(value: object) -> dt.datetime | None:
    """One of the app's ``dxt:allowlistLastUpdated`` stamps as an INSTANT.

    These were compared as strings until 2026-09-18, which is wrong in the one
    place it matters: ``"…08.500Z"`` sorts BELOW ``"…08Z"``, so an org updated
    half a second later lost to its sibling. All eight stamps on the machine
    this was measured on happened to carry fractional seconds, so the bug was
    dormant rather than active — and dormant is not fixed.

    A value that is not a stamp returns ``None`` and is then treated exactly
    like a stamp the app never wrote: ignored, never a crash, never a winner.
    Concluding anything from an unparseable stamp would be a guess, and a
    guess here publishes another organisation's sidebar.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text[-1] in {"Z", "z"}:
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


@dataclass(frozen=True)
class ScopeResolution:
    """What the rule concluded, and — always — why, in English.

    ``scope`` is the ``<account>/<org>`` directory, or ``None`` when the rule
    refuses to guess. ``signals`` maps each signal's name to the account it
    reported, so a disagreement can be shown rather than described.
    """

    scope: Path | None = None
    account: str | None = None
    org: str | None = None
    reason: str = ""
    signals: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str | None:
        """``"<account>/<org>"`` for display, or ``None``."""
        if self.account and self.org:
            return f"{self.account}/{self.org}"
        return None


def default_app_support_dir() -> Path:
    """Where the desktop app keeps its own state on this platform."""
    configured = os.environ.get("CLAUDE_DESKTOP_APP_SUPPORT_DIR")
    if configured:
        return Path(configured).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/Claude"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData/Roaming"
        return base / "Claude"
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else home / ".config"
    return base / "Claude"


def default_sessions_root() -> Path:
    """The app's session-index root: ``<app support>/claude-code-sessions``."""
    configured = os.environ.get("CLAUDE_DESKTOP_SESSIONS_DIR")
    if configured:
        return Path(configured).expanduser()
    return default_app_support_dir() / "claude-code-sessions"


def app_support_for(sessions_root: Path) -> Path:
    """The app-support dir that owns ``sessions_root``.

    The signals and the index tree are siblings, so a fixture (and an override
    of either path) stays coherent without a second environment variable.
    """
    configured = os.environ.get("CLAUDE_DESKTOP_APP_SUPPORT_DIR")
    if configured:
        return Path(configured).expanduser()
    return sessions_root.parent


def _read_signal(path: Path, key: str) -> str | None:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SIGNAL_FILE_BYTES:
            return None
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    return _safe_account(document.get(key))


def account_signals(
    app_support: Path | None = None,
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Every signal that currently states an account, by signal name.

    ``extra`` is for a caller holding a signal this module cannot read on its
    own — the extractor passes ``{"rq-cache-confirmed-account": "<uuid>"}``
    from the localStorage it already opens for the pin order.
    """
    base = app_support or default_app_support_dir()
    found: dict[str, str] = {}
    for filename, key in ACCOUNT_SIGNAL_FILES:
        value = _read_signal(base / filename, key)
        if value:
            found[f"{filename}:{key}"] = value
    for name, value in (extra or {}).items():
        safe = _safe_account(value)
        if safe:
            found[name] = safe
    return found


def signed_in_account(
    app_support: Path | None = None,
    *,
    extra: dict[str, str] | None = None,
) -> tuple[str | None, dict[str, str], str]:
    """``(account, signals, reason)`` — the app's OWN answer, never a guess.

    ``account`` is ``None`` when no signal states one, or when two signals
    disagree. A disagreement is exactly the contamination window this rule
    exists to close, so it resolves to UNKNOWN rather than to a majority: a
    wrong account publishes another person's sidebar.
    """
    signals = account_signals(app_support, extra=extra)
    if not signals:
        names = ", ".join(name for name, _key in ACCOUNT_SIGNAL_FILES)
        return (
            None,
            signals,
            "the desktop app states no signed-in account (looked in "
            f"{names}), so the account — and therefore the pin — is UNKNOWN",
        )
    distinct = sorted(set(signals.values()))
    if len(distinct) > 1:
        shown = "; ".join(f"{name} says {value}" for name, value in sorted(signals.items()))
        return (
            None,
            signals,
            "the desktop app's own signals disagree about which account is "
            f"signed in ({shown}), so the pin is UNKNOWN until they agree",
        )
    return distinct[0], signals, f"the desktop app states it is signed into {distinct[0]}"


def record_focused_at(record: dict) -> int:
    """``lastFocusedAt`` as an int, 0 when the app never recorded one.

    It lives here, with the scope rule, because that is its ONLY purpose: it
    ranks one account's organisations. It is never a signal about which
    ACCOUNT is signed in — see the module docstring.
    """
    value = record.get("lastFocusedAt")
    return value if isinstance(value, int) and value > 0 else 0


def stated_org_stamps(app_support: Path | None = None) -> dict[str, str]:
    """Organisation -> the app's own last-updated stamp for it, if stated.

    The values are ISO-8601 UTC strings the app writes itself. What counts as
    one is decided by :func:`parse_stated_stamp` — the SAME function that
    later orders them — so this reader cannot accept a stamp the decision
    would then silently drop, nor drop one the decision could have used.
    Anything unparseable is ignored rather than guessed at.
    """
    base = app_support or default_app_support_dir()
    path = base / ORG_SIGNAL_FILE
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SIGNAL_FILE_BYTES:
            return {}
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    stamps: dict[str, str] = {}
    for key, value in document.items():
        if not isinstance(key, str) or not key.startswith(ORG_SIGNAL_PREFIX):
            continue
        org = _safe_account(key[len(ORG_SIGNAL_PREFIX) :])
        if org and parse_stated_stamp(value) is not None:
            stamps[org] = str(value).strip()
    return stamps


def _newest_focus_in(org_dir: Path) -> int:
    """The newest ``lastFocusedAt`` among one org's records, 0 if none."""
    newest = 0
    seen = 0
    try:
        names = sorted(os.listdir(org_dir))
    except OSError:
        return 0
    for name in names:
        if not (name.startswith("local_") and name.endswith(".json")):
            continue
        seen += 1
        if seen > MAX_INDEX_FILES:
            break
        path = org_dir / name
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INDEX_FILE_BYTES:
                continue
            with open(path, "rb") as handle:
                blob = handle.read(_FOCUSED_PREFIX_BYTES)
        except OSError:
            continue
        match = _FOCUSED_RE.search(blob)
        if match is None:
            continue
        value = int(match.group(1))
        if value > newest:
            newest = value
    return newest


def account_org_dirs(account_dir: Path) -> tuple[str, ...]:
    """Every organisation folder under one account, sorted.

    THE answer to which organisations EXIST for an account, and the ONLY
    place either reader may get it. Existence is a directory listing and
    nothing else — never "the orgs I happen to hold a stamp for".
    """
    try:
        names = sorted(os.listdir(account_dir))
    except OSError:
        return ()
    return tuple(name for name in names if (account_dir / name).is_dir())


def org_focus_for(
    account_dir: Path,
    stamps: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """``{organisation: newest lastFocusedAt}`` for ONE account's orgs.

    The org SET is :func:`account_org_dirs` — the same listing for both
    readers of the rule — and only the VALUES differ in how they are
    gathered: ``stamps=None`` walks each org's records (the extractor, which
    has no index), and a caller that already stores every record's focus
    stamp passes them in (the engine's SQLite index, which must not re-walk
    79,000 files to answer this).

    This function exists because the two readers DID disagree (CS-33/R2,
    2026-09-18): the extractor enumerated org directories while the engine
    enumerated records ``WHERE lastrecord_focused_at > 0``, so an org the app
    NAMES whose records carry no focus stamp was invisible to the engine —
    and :func:`decide_scope` only honours a statement about an org it can
    see, so the engine silently fell back to focus ranking and published a
    different sidebar from the ledger on the same machine. An org with no
    stamp is now an org with stamp 0 in BOTH readers: a missing stamp cannot
    hide an organisation, and a stamp naming a folder that is not there
    cannot invent one.
    """
    orgs = account_org_dirs(account_dir)
    if stamps is None:
        return {org: _newest_focus_in(account_dir / org) for org in orgs}
    focus: dict[str, int] = {}
    for org in orgs:
        try:
            value = int(stamps.get(org) or 0)
        except (TypeError, ValueError):
            value = 0
        focus[org] = value if value > 0 else 0
    return focus


def decide_scope(
    account: str | None,
    signals: dict[str, str],
    account_reason: str,
    org_focus: dict[str, int],
    *,
    org_stated: dict[str, str] | None = None,
    account_dir: Path | None = None,
) -> ScopeResolution:
    """THE decision, given the account and one stamp per organisation.

    Held apart from how the stamps were gathered because there are two
    gatherers that must not drift: the filesystem scan
    (:func:`resolve_active_scope`) and the engine's persisted SQLite index
    (:mod:`app.services.coding_sessions.claude_index_store`, which already
    stores every record's focus stamp and must not re-walk 79,000 files to
    answer this).

    ``org_stated`` is what the APP says — ``dxt:allowlistLastUpdated:<org>``
    from its own config — and it decides whenever it names one of this
    account's organisations. ``org_focus`` (organisation -> its newest
    ``lastFocusedAt``, already restricted to ``account``) is only the fallback,
    and a TIE on the maximum there is UNKNOWN: on 2026-09-18 all five of the
    signed-in account's organisations carried the identical stamp
    ``1789718916078`` while holding 203, 203, 204, 204 and 218 stars, so
    "the newest" was a coin flip that landed on 203.
    """
    if account is None:
        return ScopeResolution(reason=account_reason, signals=signals)

    def resolved(org: str, how: str) -> ScopeResolution:
        return ScopeResolution(
            scope=(account_dir / org) if account_dir is not None else None,
            account=account,
            org=org,
            signals=signals,
            reason=(
                f"the app states it is signed into {account}; of its "
                f"{len(org_focus)} organisation folder(s), {org} {how}"
            ),
        )

    # ``org_focus`` is the org set from :func:`account_org_dirs` in BOTH
    # readers, so "an org it can see" no longer depends on which reader is
    # asking. The stamps are ordered as INSTANTS, never as strings: ".500Z"
    # sorts below "Z", and an unparseable stamp is dropped here exactly as it
    # is in :func:`stated_org_stamps` — ignored, never a winner.
    stated: dict[str, tuple[dt.datetime, str]] = {}
    for org, stamp in (org_stated or {}).items():
        if org not in org_focus:
            continue
        parsed = parse_stated_stamp(stamp)
        if parsed is None:
            continue
        stated[org] = (parsed, str(stamp).strip())
    if stated:
        best = max(instant for instant, _text in stated.values())
        winners = sorted(
            org for org, (instant, _text) in stated.items() if instant == best
        )
        if len(winners) == 1:
            shown = stated[winners[0]][1]
            return resolved(winners[0], f"is the one the app last updated ({shown})")
        shown = "; ".join(f"{org} at {stated[org][1]}" for org in winners)
        return ScopeResolution(
            account=account,
            signals=signals,
            reason=(
                f"the app states it is signed into {account} but names "
                f"{len(winners)} of its organisations as last updated at the "
                f"same moment ({shown}), so the organisation — and therefore "
                "the pin — is UNKNOWN"
            ),
        )

    ranked = sorted(
        ((focus, org) for org, focus in org_focus.items() if focus > 0),
        reverse=True,
    )
    if not ranked:
        return ScopeResolution(
            account=account,
            signals=signals,
            reason=(
                f"the app states it is signed into {account}, but it names no "
                f"organisation as last updated and none of its "
                f"{len(org_focus)} organisation folder(s) carries a "
                "lastFocusedAt stamp, so the organisation — and therefore the "
                "pin — is UNKNOWN"
            ),
        )
    top = ranked[0][0]
    tied = sorted(org for focus, org in ranked if focus == top)
    if len(tied) > 1:
        return ScopeResolution(
            account=account,
            signals=signals,
            reason=(
                f"the app states it is signed into {account} and names no "
                f"organisation as last updated, and {len(tied)} of its "
                f"organisations share the same newest lastFocusedAt ({top}) — "
                "that stamp is copied between scopes, so picking one would be "
                "a coin flip; the pin is UNKNOWN"
            ),
        )
    return resolved(tied[0], f"carries the newest lastFocusedAt ({top})")


def resolve_active_scope(
    sessions_root: Path | None = None,
    *,
    app_support: Path | None = None,
    extra_signals: dict[str, str] | None = None,
) -> ScopeResolution:
    """THE rule against the real session-index tree.

    See the module docstring for why the account is never ranked by
    ``lastFocusedAt`` and the org still is.
    """
    root = sessions_root or default_sessions_root()
    support = app_support or app_support_for(root)
    account, signals, reason = signed_in_account(support, extra=extra_signals)
    if account is None:
        return ScopeResolution(reason=reason, signals=signals)
    if not root.is_dir():
        return ScopeResolution(
            account=account,
            signals=signals,
            reason=(
                f"the app states it is signed into {account} but its session "
                f"index is not readable at {root}, so the pin is UNKNOWN"
            ),
        )
    account_dir = root / account
    if not account_dir.is_dir():
        return ScopeResolution(
            account=account,
            signals=signals,
            reason=(
                f"the app states it is signed into {account} but that account "
                "has no session-index folder yet, so the pin is UNKNOWN"
            ),
        )
    org_focus = org_focus_for(account_dir)
    return decide_scope(
        account,
        signals,
        reason,
        org_focus,
        org_stated=stated_org_stamps(support),
        account_dir=account_dir,
    )


def active_index_scope(
    sessions_root: Path | None = None,
    *,
    app_support: Path | None = None,
    extra_signals: dict[str, str] | None = None,
) -> Path | None:
    """The signed-in ``<account>/<org>`` folder, or ``None`` when unknown."""
    return resolve_active_scope(
        sessions_root, app_support=app_support, extra_signals=extra_signals
    ).scope
