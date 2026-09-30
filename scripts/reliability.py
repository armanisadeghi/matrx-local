#!/usr/bin/env python3
"""Matrx Local reliability loop — the deterministic half.

One script does every mechanical step of "installed app → unique issue → fix → release → verify":

  scan      read the installed app's evidence on this Mac, fingerprint every failure into a
            stable issue, update the ledger, write _reliability/README.md (+ latest.json)
  status    two lines; exit 1 when an agent has work, exit 0 when nothing is open
  claim     an agent takes an issue          (claim MXL-R-012 --owner "<session or name>")
  fix       record the repairing commit      (fix MXL-R-012 --commit <sha>)
  ignore    disposition: expected state, external, duplicate ...  (ignore ID --reason "...")
  arman     needs a human decision           (arman ID --question "...")
  note      append a note                    (note ID "...")
  reopen    put an issue back to open

Evidence read (all read-only): ~/Library/Logs/MatrxLocal/{system,access,lifecycle,engine-stderr}.log*,
~/.matrx/diagnostics/*.json (service-failure snapshots, crash breadcrumbs, error-outbox-v1.json),
~/.matrx/matrx.db failure tables, GET /health on the installed engine (Bearer local-probe),
the installed bundle's version, and git tags. It never writes to the live app, its home, or its DB.

Ledger: _reliability/issues.json (committed; ship.sh syncs it). Machine state: ~/.matrx/reliability/.
Stdlib only. Runs the same under Claude Code and Codex.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER_DIR = REPO / "_reliability"
LEDGER = LEDGER_DIR / "issues.json"
README = LEDGER_DIR / "README.md"
HOME = Path.home()
LOG_DIR = Path(os.environ.get("MATRX_LOG_DIR", HOME / "Library" / "Logs" / "MatrxLocal"))
MATRX_HOME = Path(os.environ.get("MATRX_LIVE_HOME", HOME / ".matrx"))
DIAG_DIR = MATRX_HOME / "diagnostics"
DB_PATH = MATRX_HOME / "matrx.db"
STATE_DIR = MATRX_HOME / "reliability"
LATEST = STATE_DIR / "latest.json"
APP_PLIST = Path("/Applications/AI Matrx.app/Contents/Info.plist")
HEALTH_URL = os.environ.get("MATRX_LIVE_HEALTH", "http://127.0.0.1:22140/health")

WARNING_THRESHOLD = int(os.environ.get("MATRX_RELIABILITY_WARNING_THRESHOLD", "20"))  # per window
VERIFY_HOURS = int(os.environ.get("MATRX_RELIABILITY_VERIFY_HOURS", "24"))
INSTALL_LAG_RELEASES = int(os.environ.get("MATRX_RELIABILITY_INSTALL_LAG", "2"))
FIX_LAG_HOURS = int(os.environ.get("MATRX_RELIABILITY_FIX_LAG_HOURS", "3"))

UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)

# ----------------------------------------------------------------------------- normalizing

SYSLOG_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$")
LOGGER_RE = re.compile(r"^\[([\w.\-]+)\] ")
NORMALIZERS = [
    (re.compile(r"https?://[^\s'\"]+"), "<url>"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"(?<![\w/])/(?:[\w.\-@ ]+/)+[\w.\-@ ]*"), "<path>"),
    (re.compile(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?"), "<ts>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|MB|KB|GB|%)\b"), "<n>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def normalize(msg: str) -> str:
    out = msg.strip()
    for rx, rep in NORMALIZERS:
        out = rx.sub(rep, out)
    return out[:240]


def fingerprint(level: str, component: str, msg: str) -> tuple[str, str]:
    norm = normalize(msg)
    key = f"{level}|{component}|{norm}"
    return hashlib.sha1(key.encode()).hexdigest()[:12], norm


def version_tuple(v: str | None) -> tuple:
    if not v:
        return ()
    v = v.lstrip("v")
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v) if x)


# ----------------------------------------------------------------------------- evidence readers

class Occurrence:
    __slots__ = ("level", "component", "msg", "when", "source", "raw")

    def __init__(self, level, component, msg, when, source, raw):
        self.level, self.component, self.msg, self.when, self.source, self.raw = level, component, msg, when, source, raw


def local_to_utc(s: str) -> dt.datetime:
    naive = dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return naive.astimezone(UTC)  # system.log is local time without an offset


def read_system_log(since: dt.datetime) -> list[Occurrence]:
    occ: list[Occurrence] = []
    files = sorted(LOG_DIR.glob("system.log*"), key=lambda p: p.stat().st_mtime)
    current: Occurrence | None = None
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = SYSLOG_RE.match(line)
            if m:
                when = local_to_utc(m.group(1))
                level, body = m.group(2), m.group(3)
                current = None
                if level not in ("WARNING", "ERROR", "CRITICAL") or when < since:
                    continue
                comp = "engine"
                lm = LOGGER_RE.match(body)
                if lm:
                    comp, body = lm.group(1), body[lm.end():]
                current = Occurrence(level, comp, body, when, "system.log", line[:400])
                occ.append(current)
            elif current is not None and line.startswith(("Traceback", "  File", "    ", "Exception", "\t")):
                # keep the last traceback line as the exception class for better grouping
                if not line.startswith((" ", "\t", "Traceback")) and ":" in line:
                    current.msg = f"{current.msg} | {line.strip()}"
    return occ


def read_access_log(since: dt.datetime) -> list[Occurrence]:
    occ = []
    for f in sorted(LOG_DIR.glob("access.log*")):
        try:
            for line in f.read_text(errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if int(rec.get("status", 0)) < 500:
                    continue
                when = dt.datetime.fromisoformat(str(rec.get("timestamp", "")).replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                if when < since:
                    continue
                path = re.sub(r"/[0-9a-f-]{20,}", "/<id>", str(rec.get("path", "")))
                occ.append(Occurrence("ERROR", "http", f"{rec['status']} {rec.get('method')} {path}", when, "access.log", line[:300]))
        except (OSError, ValueError):
            continue
    return occ


LIFECYCLE_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)Z (?:\[unix:\d+\] )?(\[[\w\-]+\]) (.*)$")
LIFECYCLE_BAD = re.compile(r"fail|error|panic|orphan|kill|timed out|crash|unclean", re.I)


def read_lifecycle(since: dt.datetime) -> list[Occurrence]:
    occ = []
    for name in ("lifecycle.log", "lifecycle.log.1", "engine-stderr.log", "engine-stderr.log.1"):
        f = LOG_DIR / name
        if not f.exists():
            continue
        stderr = name.startswith("engine-stderr")
        for line in f.read_text(errors="replace").splitlines():
            if stderr:
                m = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)Z (.*)$", line)
                if not m or not re.search(r"Traceback|Error|error:|panic|FATAL", m.group(2)):
                    continue
                when = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                if when >= since:
                    occ.append(Occurrence("ERROR", "engine-stderr", m.group(2), when, name, line[:300]))
                continue
            m = LIFECYCLE_RE.match(line)
            if not m or not LIFECYCLE_BAD.search(m.group(3)):
                continue
            when = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            if when >= since:
                occ.append(Occurrence("ERROR", f"lifecycle{m.group(2)}", m.group(3), when, name, line[:300]))
    return occ


def read_diagnostics(since: dt.datetime) -> tuple[list[Occurrence], dict]:
    occ, outbox = [], {"total": 0, "without_identity": 0, "by_source": {}}
    if not DIAG_DIR.exists():
        return occ, outbox
    for f in DIAG_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if f.name.startswith("error-outbox"):
            recs = data if isinstance(data, list) else data.get("records", [])
            outbox["total"] = len(recs)
            for r in recs:
                if not (r.get("userId") and r.get("organizationId")):
                    outbox["without_identity"] += 1
                src = f"{r.get('source', '?')}/{r.get('level', '?')}"
                outbox["by_source"][src] = outbox["by_source"].get(src, 0) + 1
                try:
                    when = dt.datetime.fromisoformat(str(r.get("occurredAt", "")).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when >= since and str(r.get("level", "")).lower() in ("error", "fatal"):
                    occ.append(Occurrence("ERROR", f"outbox/{r.get('source', '?')}", str(r.get("message", ""))[:300], when, "error-outbox", ""))
            continue
        when_s = data.get("timestamp_iso") or data.get("timestamp") or data.get("detected_at")
        try:
            when = dt.datetime.fromisoformat(str(when_s).replace("Z", "+00:00")) if when_s else dt.datetime.fromtimestamp(f.stat().st_mtime, UTC)
        except ValueError:
            when = dt.datetime.fromtimestamp(f.stat().st_mtime, UTC)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if when < since:
            continue
        if data.get("event") == "unclean_shutdown_detected":
            occ.append(Occurrence("CRITICAL", "lifecycle", f"unclean shutdown detected (version {data.get('version')})", when, f.name, ""))
        elif data.get("focus") or data.get("error"):
            occ.append(Occurrence("CRITICAL", f"service/{data.get('focus', '?')}", f"service failure snapshot: {str(data.get('error', ''))[:200]}", when, f.name, ""))
    return occ, outbox


def read_local_db(since: dt.datetime) -> list[Occurrence]:
    occ = []
    if not DB_PATH.exists():
        return occ
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
    except sqlite3.Error:
        return occ
    queries = [
        ("sync_queue", "SELECT entity_type AS k, COUNT(*) AS c, MAX(COALESCE(updated_at, created_at)) AS t FROM sync_queue WHERE action='dead' GROUP BY entity_type", "dead sync_queue rows for {k}"),
        ("downloads", "SELECT substr(COALESCE(error_msg,''),1,120) AS k, COUNT(*) AS c, MAX(COALESCE(updated_at, created_at)) AS t FROM downloads WHERE status='failed' GROUP BY k", "failed download: {k}"),
        ("coding_session_bridge_quarantine", "SELECT COALESCE(http_status,'')||' '||substr(COALESCE(last_error,''),1,80) AS k, COUNT(*) AS c, MAX(COALESCE(updated_at, created_at)) AS t FROM coding_session_bridge_quarantine GROUP BY k", "quarantined coding-session envelope: {k}"),
        ("file_sync_state", "SELECT error AS k, COUNT(*) AS c, MAX(COALESCE(updated_at, last_synced_at)) AS t FROM file_sync_state WHERE error IS NOT NULL AND error<>'' GROUP BY error", "file sync error: {k}"),
    ]
    for table, sql, fmt in queries:
        try:
            rows = con.execute(sql).fetchall()
        except sqlite3.Error:
            continue  # table or column absent in this build: not an error of the app
        for r in rows:
            if not r["c"]:
                continue
            when = NOW
            try:
                when = dt.datetime.fromisoformat(str(r["t"]).replace("Z", "+00:00")) if r["t"] else NOW
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
            except ValueError:
                pass
            o = Occurrence("ERROR", f"db/{table}", fmt.format(k=r["k"]), when, "matrx.db", "")
            o.raw = json.dumps({"count": r["c"], "latest": r["t"]})
            occ.append(o)
    con.close()
    return occ


def read_health() -> dict:
    req = urllib.request.Request(HEALTH_URL, headers={"Authorization": "Bearer local-probe"})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
        return {"reachable": True, "version": data.get("version"), "health": data.get("health"),
                "failed": data.get("failed") or [], "degraded": data.get("degraded") or [], "boot_id": data.get("boot_id")}
    except Exception as e:  # noqa: BLE001 — the point is to record unreachability
        return {"reachable": False, "error": str(e)[:200]}


def installed_version() -> str | None:
    try:
        with APP_PLIST.open("rb") as fh:
            return plistlib.load(fh).get("CFBundleShortVersionString")
    except (OSError, ValueError):
        return None


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def latest_release_tag() -> str | None:
    git("fetch", "--tags", "--quiet", "origin")
    tags = git("tag", "--sort=-creatordate", "--list", "v*").splitlines()
    return tags[0] if tags else None


def releases_between(installed: str | None, latest: str | None) -> int:
    if not installed or not latest:
        return 0
    tags = [t for t in git("tag", "--sort=-creatordate", "--list", "v*").splitlines()]
    try:
        return tags.index(f"v{installed.lstrip('v')}")
    except ValueError:
        return sum(1 for t in tags if version_tuple(t) > version_tuple(installed))


def tag_containing(commit: str) -> str | None:
    tags = git("tag", "--contains", commit, "--list", "v*", "--sort=creatordate").splitlines()
    return tags[0] if tags else None


# ----------------------------------------------------------------------------- ledger

def load_ledger() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"next_id": 1, "issues": {}}


def save_ledger(led: dict) -> None:
    LEDGER_DIR.mkdir(exist_ok=True)
    LEDGER.write_text(json.dumps(led, indent=1, sort_keys=True) + "\n")


def find_issue(led: dict, ident: str) -> dict:
    ident = ident.upper()
    if ident in led["issues"]:
        return led["issues"][ident]
    for iss in led["issues"].values():
        if iss["fingerprint"].startswith(ident.lower()):
            return iss
    sys.exit(f"no issue {ident} in {LEDGER}")


def stamp(iss: dict, text: str) -> None:
    iss.setdefault("notes", []).append(f"{NOW.strftime('%Y-%m-%d %H:%M')}Z {text}")


# ----------------------------------------------------------------------------- scan

def scan(hours: int) -> dict:
    since = NOW - dt.timedelta(hours=hours)
    led = load_ledger()
    health = read_health()
    inst = installed_version()
    latest = latest_release_tag()
    lag = releases_between(inst, latest)
    diag_occ, outbox = read_diagnostics(since)
    occurrences = read_system_log(since) + read_access_log(since) + read_lifecycle(since) + diag_occ + read_local_db(since)

    groups: dict[str, list[Occurrence]] = defaultdict(list)
    norms: dict[str, str] = {}
    for o in occurrences:
        fp, norm = fingerprint(o.level, o.component, o.msg)
        groups[fp].append(o)
        norms[fp] = norm

    running_version = health.get("version") or inst
    by_fp = {iss["fingerprint"]: iss for iss in led["issues"].values()}
    new_ids = []
    for fp, occs in groups.items():
        occs.sort(key=lambda o: o.when)
        count = sum(int(json.loads(o.raw).get("count", 1)) if o.source == "matrx.db" and o.raw else 1 for o in occs)
        iss = by_fp.get(fp)
        if iss is None:
            iid = f"MXL-R-{led['next_id']:03d}"
            led["next_id"] += 1
            lvl = occs[0].level
            iss = {"id": iid, "fingerprint": fp, "level": lvl, "component": occs[0].component, "pattern": norms[fp],
                   "sample": (occs[-1].raw or occs[-1].msg)[:300], "first_seen": occs[0].when.isoformat(),
                   "status": "open", "owner": None, "fix_commit": None, "fix_version": None,
                   "versions_seen": {}, "count_total": 0, "notes": []}
            led["issues"][iid] = iss
            by_fp[fp] = iss
            new_ids.append(iid)
        prev_last = iss.get("last_seen")
        if occs[-1].source == "matrx.db":
            fresh = count  # all-time table counts: total mirrors the table
            iss["count_total"] = 0
        else:
            cutoff = dt.datetime.fromisoformat(prev_last) if prev_last else None
            fresh = sum(1 for o in occs if cutoff is None or o.when > cutoff)
        iss["last_seen"] = occs[-1].when.isoformat()
        iss["count_window"] = count
        iss["count_total"] = iss.get("count_total", 0) + fresh
        iss["last_scan"] = NOW.isoformat()
        iss["sample"] = (occs[-1].raw or occs[-1].msg)[:300]
        if running_version and occs[-1].source != "matrx.db" and fresh:
            iss["versions_seen"][running_version] = iss["versions_seen"].get(running_version, 0) + fresh
        # regression: occurrence on a build that should contain the fix
        fv = iss.get("fix_version")
        if iss["status"] in ("installed", "verified", "fixed") and fv and running_version and version_tuple(running_version) >= version_tuple(fv) and occs[-1].source != "matrx.db":
            if iss["status"] != "regressed":
                stamp(iss, f"REGRESSED: seen on {running_version}, fix was in {fv}")
            iss["status"] = "regressed"

    seen_fps = set(groups)
    for iss in led["issues"].values():
        if iss["fingerprint"] not in seen_fps:
            iss["count_window"] = 0
        # fixed → installed → verified
        if iss["status"] == "fixed" and iss.get("fix_commit"):
            if not iss.get("fix_version"):
                iss["fix_version"] = tag_containing(iss["fix_commit"])
            if iss.get("fix_version") and inst and version_tuple(inst) >= version_tuple(iss["fix_version"]):
                iss["status"] = "installed"
                iss["installed_at"] = NOW.isoformat()
                stamp(iss, f"installed build {inst} contains fix {iss['fix_version']}")
        if iss["status"] == "installed" and iss["fingerprint"] not in seen_fps:
            inst_at = dt.datetime.fromisoformat(iss.get("installed_at", NOW.isoformat()))
            if NOW - inst_at >= dt.timedelta(hours=VERIFY_HOURS):
                iss["status"] = "verified"
                iss["verified_at"] = NOW.isoformat()
                stamp(iss, f"verified: no recurrence for {VERIFY_HOURS}h on {inst}")

    save_ledger(led)
    summary = {
        "scanned_at": NOW.isoformat(), "window_hours": hours, "installed_version": inst, "latest_release": latest,
        "installed_behind_by": lag, "engine": health, "running_version": running_version,
        "window_counts": {lvl: sum(1 for o in occurrences if o.level == lvl) for lvl in ("CRITICAL", "ERROR", "WARNING")},
        "outbox": outbox, "new_issues": new_ids,
        "issues_by_status": {s: sum(1 for i in led["issues"].values() if i["status"] == s) for s in
                             ("open", "claimed", "fixed", "installed", "verified", "regressed", "needs_arman", "ignored")},
        "log_window_start": min((o.when for o in occurrences), default=NOW).isoformat(),
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(summary, indent=1) + "\n")
    write_readme(led, summary)
    return summary


# ----------------------------------------------------------------------------- README

HEADER = """# Matrx Local reliability — generated by scripts/reliability.py

This folder is permanent. When every list below is empty, nothing on the installed app needs an agent.
`python3 scripts/reliability.py scan` rewrites this file from the installed app's own evidence
(logs, diagnostics, health, local DB); it never edits the app. `status` exits 1 while work is open.

## What the items are
- **Issue** — one stable failure signature (`MXL-R-nnn`), counted across builds. Not a log line.
- **Open** — needs a root cause and a fix. Errors first; warnings only above {thr}/window.
- **Claimed** — an agent owns it. `claim ID --owner "<you>"`. Do not take a claimed issue.
- **Fixed, awaiting install** — `fix ID --commit <sha>` was recorded; the release watch ships it,
  the installed app has not picked it up yet. Nothing for an agent to do unless it is stale.
- **Installed, verifying** — the installed build contains the fix; it flips to verified after
  {vh}h with zero recurrence, or to Regressed on the first recurrence.
- **Regressed** — recurred on a build that contains its fix. Back to the top of the list.
- **Needs Arman** — one plain-English question, written by the agent who could not decide.

## Marking an item done
An issue is done only when it is **verified**: fixed in source, released, installed, and silent for
{vh}h on the installed app. `ignore ID --reason "..."` is for expected states and external causes,
never for "too noisy". The fix itself follows the `diagnose` and `forcing-function-tests` skills.
"""


def fmt_issue(i: dict) -> str:
    v = ", ".join(f"{k}×{n}" for k, n in sorted(i.get("versions_seen", {}).items(), key=lambda kv: version_tuple(kv[0]))[-3:])
    extra = []
    if i.get("owner"):
        extra.append(f"owner: {i['owner']}")
    if i.get("fix_commit"):
        extra.append(f"fix {i['fix_commit'][:9]}" + (f" in {i['fix_version']}" if i.get("fix_version") else " (not in a release yet)"))
    if i.get("question"):
        extra.append(f"Q: {i['question']}")
    if i.get("reason"):
        extra.append(f"reason: {i['reason']}")
    tail = f" — {'; '.join(extra)}" if extra else ""
    return (f"- **{i['id']}** {i['level']} `{i['component']}` — {i['pattern'][:150]}  \n"
            f"  window {i.get('count_window', 0)} · total {i.get('count_total', 0)} · last {i.get('last_seen', '')[:16]} · builds {v or '?'}{tail}")


def write_readme(led: dict, s: dict) -> None:
    issues = list(led["issues"].values())
    order = lambda i: (-(i.get("count_window", 0)), i["id"])  # noqa: E731
    open_err = sorted([i for i in issues if i["status"] == "open" and i["level"] != "WARNING" and i.get("count_window", 0) > 0], key=order)
    open_warn = sorted([i for i in issues if i["status"] == "open" and i["level"] == "WARNING" and i.get("count_window", 0) >= WARNING_THRESHOLD], key=order)
    quiet = [i for i in issues if i["status"] == "open" and i not in open_err and i not in open_warn]
    sections = [
        ("Regressed", [i for i in issues if i["status"] == "regressed"]),
        ("Open — errors", open_err),
        ("Open — warnings above threshold", open_warn),
        ("Claimed", [i for i in issues if i["status"] == "claimed"]),
        ("Fixed, awaiting install", [i for i in issues if i["status"] == "fixed"]),
        ("Installed, verifying", [i for i in issues if i["status"] == "installed"]),
        ("Needs Arman", [i for i in issues if i["status"] == "needs_arman"]),
    ]
    eng = s["engine"]
    eng_line = (f"reachable, health `{eng.get('health')}`, version {eng.get('version')}, failed {eng.get('failed') or 'none'}, degraded {eng.get('degraded') or 'none'}"
                if eng.get("reachable") else f"**NOT REACHABLE** ({eng.get('error')})")
    problems = []
    if s["installed_behind_by"] > INSTALL_LAG_RELEASES:
        problems.append(f"installed app {s['installed_version']} is {s['installed_behind_by']} releases behind {s['latest_release']} — the release watch's install step is not keeping up")
    if not eng.get("reachable"):
        problems.append("installed engine not reachable on its port — the app is not running or discovery is broken")
    if eng.get("failed"):
        problems.append(f"engine reports failed services: {eng['failed']}")
    if s["outbox"]["total"] and s["outbox"]["without_identity"] * 2 >= s["outbox"]["total"]:
        problems.append(f"error outbox holds {s['outbox']['total']} events and {s['outbox']['without_identity']} carry no identity, so they can never upload to the platform (desktop/src/App.tsx drain requires userId+organizationId)")
    for i in issues:
        if i["status"] == "fixed" and i.get("fix_version") is None and i.get("fix_commit"):
            ts = next((n for n in reversed(i.get("notes", [])) if "fix recorded" in n), "")
            if ts:
                try:
                    rec = dt.datetime.strptime(ts[:17], "%Y-%m-%d %H:%MZ").replace(tzinfo=UTC)
                    if NOW - rec > dt.timedelta(hours=FIX_LAG_HOURS):
                        problems.append(f"{i['id']} fix {i['fix_commit'][:9]} has not been released for {int((NOW - rec).total_seconds() // 3600)}h — is it pushed to main?")
                except ValueError:
                    pass
    wc = s["window_counts"]
    out = [HEADER.format(thr=WARNING_THRESHOLD, vh=VERIFY_HOURS)]
    out.append(f"## Scan\n- {s['scanned_at'][:16]}Z · window {s['window_hours']}h (evidence from {s['log_window_start'][:16]}Z)\n"
               f"- installed **{s['installed_version']}** · latest release **{s['latest_release']}** · behind by {s['installed_behind_by']}\n"
               f"- engine: {eng_line}\n"
               f"- window: {wc.get('CRITICAL', 0)} critical · {wc.get('ERROR', 0)} errors · {wc.get('WARNING', 0)} warnings · "
               f"outbox {s['outbox']['total']} queued ({s['outbox']['without_identity']} without identity)\n"
               f"- issues: " + " · ".join(f"{k} {v}" for k, v in s["issues_by_status"].items() if v) + "\n"
               f"- new this scan: {', '.join(s['new_issues']) or 'none'}\n")
    out.append("## PROBLEMS\n" + ("\n".join(f"- {p}" for p in problems) if problems else "- none") + "\n")
    for title, items in sections:
        out.append(f"## {title}\n" + ("\n".join(fmt_issue(i) for i in items) if items else "") + "\n")
    out.append(f"## Quiet open issues (no occurrence this window, or below threshold): {len(quiet)}\n"
               + "\n".join(f"- {i['id']} {i['level']} `{i['component']}` — {i['pattern'][:100]} (total {i.get('count_total', 0)})" for i in sorted(quiet, key=lambda i: -i.get('count_total', 0))[:15]) + "\n")
    out.append(f"## Verified (kept 7 days)\n" + "\n".join(fmt_issue(i) for i in issues if i["status"] == "verified" and NOW - dt.datetime.fromisoformat(i.get("verified_at", NOW.isoformat())) < dt.timedelta(days=7)) + "\n")
    ign = [i for i in issues if i["status"] == "ignored"]
    out.append(f"## Ignored: {len(ign)}\n" + "\n".join(f"- {i['id']} — {i.get('reason', '')} — `{i['pattern'][:80]}`" for i in ign) + "\n")
    LEDGER_DIR.mkdir(exist_ok=True)
    README.write_text("\n".join(out))


# ----------------------------------------------------------------------------- commands

def cmd_status() -> int:
    if not LATEST.exists():
        print("no scan yet — run: python3 scripts/reliability.py scan")
        return 1
    s = json.loads(LATEST.read_text())
    led = load_ledger()
    issues = led["issues"].values()
    actionable = [i for i in issues if i["status"] in ("regressed", "claimed") or
                  (i["status"] == "open" and i.get("count_window", 0) > 0 and (i["level"] != "WARNING" or i["count_window"] >= WARNING_THRESHOLD))]
    problems = README.read_text().split("## PROBLEMS\n", 1)[1].split("\n## ", 1)[0].strip() if README.exists() else ""
    has_problems = problems and not problems.startswith("- none")
    print(f"installed {s['installed_version']} (latest {s['latest_release']}, behind {s['installed_behind_by']}) · engine {'ok' if s['engine'].get('reachable') else 'UNREACHABLE'} · "
          f"{s['window_counts'].get('ERROR', 0)} errors/{s['window_counts'].get('WARNING', 0)} warnings in {s['window_hours']}h")
    print(f"{len(actionable)} issues need an agent · {s['issues_by_status'].get('fixed', 0)} awaiting install · "
          f"{s['issues_by_status'].get('installed', 0)} verifying · {s['issues_by_status'].get('needs_arman', 0)} need Arman · PROBLEMS: {'yes' if has_problems else 'none'}")
    return 1 if actionable or has_problems else 0


def transition(ident: str, status: str, **fields) -> None:
    led = load_ledger()
    iss = find_issue(led, ident)
    iss["status"] = status
    for k, v in fields.items():
        if v is not None:
            iss[k] = v
    stamp(iss, f"{status}" + (f" {json.dumps(fields)}" if fields else ""))
    save_ledger(led)
    if LATEST.exists():
        write_readme(led, json.loads(LATEST.read_text()))
    print(f"{iss['id']} → {status}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan"); p.add_argument("--hours", type=int, default=24); p.add_argument("--json", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("claim"); p.add_argument("id"); p.add_argument("--owner", required=True)
    p = sub.add_parser("fix"); p.add_argument("id"); p.add_argument("--commit", required=True)
    p = sub.add_parser("ignore"); p.add_argument("id"); p.add_argument("--reason", required=True)
    p = sub.add_parser("arman"); p.add_argument("id"); p.add_argument("--question", required=True)
    p = sub.add_parser("note"); p.add_argument("id"); p.add_argument("text")
    p = sub.add_parser("reopen"); p.add_argument("id")
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        s = scan(a.hours)
        if a.json:
            print(json.dumps(s, indent=1))
        else:
            print(f"wrote {README.relative_to(REPO)} and {LATEST}")
            cmd_status()
        return 0
    if a.cmd == "status":
        return cmd_status()
    if a.cmd == "claim":
        transition(a.id, "claimed", owner=a.owner); return 0
    if a.cmd == "fix":
        full = git("rev-parse", "--verify", f"{a.commit}^{{commit}}")
        if not full:
            sys.exit(f"commit {a.commit} not found in this repo")
        fv = tag_containing(full)
        led = load_ledger(); iss = find_issue(led, a.id)
        iss.update(status="fixed", fix_commit=full, fix_version=fv)
        stamp(iss, f"fix recorded {full[:9]}" + (f" (already in {fv})" if fv else ""))
        save_ledger(led)
        if LATEST.exists():
            write_readme(led, json.loads(LATEST.read_text()))
        print(f"{iss['id']} → fixed by {full[:9]}" + (f", released in {fv}" if fv else ", not released yet")); return 0
    if a.cmd == "ignore":
        transition(a.id, "ignored", reason=a.reason); return 0
    if a.cmd == "arman":
        transition(a.id, "needs_arman", question=a.question); return 0
    if a.cmd == "note":
        led = load_ledger(); iss = find_issue(led, a.id); stamp(iss, a.text); save_ledger(led); print("noted"); return 0
    if a.cmd == "reopen":
        transition(a.id, "open", owner=None); return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
