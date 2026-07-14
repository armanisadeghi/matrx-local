"""Tool sync — code catalog ↔ cloud canon (tool.definition / tool.binding).

The CLOUD IS CANON: Supabase ``tool.definition`` rows joined through
``tool.binding`` on executor ``matrx-local`` define what the platform
advertises. The code side is ``app.tools.catalog.get_catalog()`` — one entry
per dispatched tool. This module diffs the two and, instead of writing the DB
directly from the desktop (forbidden — the desktop has no service-role
credentials and never should), emits a REVIEWABLE CHANGESET the operator
applies via the Supabase MCP.

Usage (from the repo root):

    uv run python -m app.tools.tool_sync status           # loud drift report (never blocks CI)
    uv run python -m app.tools.tool_sync diff              # status + per-tool field detail
    uv run python -m app.tools.tool_sync emit-changeset    # write .matrx/tool_registry_changeset.sql
    uv run python -m app.tools.tool_sync list              # list the catalog
    uv run python -m app.tools.tool_sync show <cloud_name> # one tool, full detail

Options:
    --baseline <file.json>   Use a saved cloud snapshot instead of the live
                             aidream route (accepts the route response shape
                             {"tools": [...]} or a bare list of rows).
    --out <file.sql>         Changeset output path (emit-changeset only).

Read path: ``GET {AIDREAM_SERVER_URL}/ai-tools/app/matrx-local/all`` — active
definition⨝binding rows. When the route is unreachable/undeployed the diff
falls back to the embedded ``FALLBACK_CLOUD_BASELINE`` (the 49 tool names
verified live on 2026-07-10) in NAMES-ONLY mode: NEW/REMOVED are still exact,
but parameter drift on existing rows cannot be checked and is reported as
UNVERIFIED, loudly.

Exit codes (mirrors matrx-extend scripts/check-tool-db-drift.ts semantics —
LOUD + NON-BLOCKING): 0 = in sync OR cloud unreachable ("couldn't check" is
not drift); 1 = drift detected (a signal for wrapping scripts, none of which
hard-gate on it).

Classification:
    NEW      — in catalog, no active matrx-local binding in the cloud
    CHANGED  — bound, but the parameters JSON Schema differs (descriptions
               and category are NOT diffed: descriptions are DB-canonical by
               house rule, and the cloud intentionally flattens category to
               'local')
    REMOVED  — bound in the cloud, absent from the catalog (never deleted
               automatically; the changeset only suggests deactivation)
    OK       — bound and structurally identical
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.tools.catalog import CatalogEntry, get_by_cloud_name, get_catalog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXECUTOR_NAME = "matrx-local"
DEFAULT_AIDREAM_URL = "https://server.app.matrxserver.com"
TOOLS_ROUTE = "/ai-tools/app/matrx-local/all"
ALL_TOOLS_ROUTE = "/ai-tools"
DEFAULT_CHANGESET_PATH = Path(".matrx/tool_registry_changeset.sql")

# tool.definition rows for executor matrx-local share these values (verified
# against live Supabase, project txzxabzwovsujtloxrus, 2026-07-10).
MATRX_ORG_ID = "39c38960-d30c-4840-b0c1-c9960de95582"
DEFINITION_DEFAULTS = {
    "source_kind": "native",
    "tool_group": "core",
    "visibility": "public",
}

# The 49 cloud names bound to executor matrx-local, verified live 2026-07-10.
# Used ONLY as a names-only fallback baseline when the aidream route is
# unreachable — never as a substitute for a real fetch when one succeeds.
FALLBACK_CLOUD_BASELINE: tuple[str, ...] = (
    "local_applescript", "local_archive_create", "local_archive_extract",
    "local_bash", "local_bash_output", "local_battery_status",
    "local_disk_usage", "local_edit_file", "local_fetch_url",
    "local_focus_app", "local_focus_window", "local_get_installed_apps",
    "local_glob", "local_grep", "local_hotkey", "local_image_ocr",
    "local_image_resize", "local_kill_process", "local_launch_app",
    "local_list_directory", "local_list_document_folders",
    "local_list_documents", "local_list_ports", "local_list_processes",
    "local_list_windows", "local_mdns_discover", "local_minimize_window",
    "local_mouse_click", "local_mouse_move", "local_move_window",
    "local_network_info", "local_network_scan", "local_notify",
    "local_open_path", "local_open_url", "local_pdf_extract",
    "local_port_scan", "local_powershell", "local_read_document",
    "local_read_file", "local_screenshot", "local_search_documents",
    "local_system_info", "local_system_resources", "local_task_stop",
    "local_top_processes", "local_type_text", "local_write_document",
    "local_write_file",
)


def _base_url() -> str:
    return os.environ.get("AIDREAM_SERVER_URL", DEFAULT_AIDREAM_URL).rstrip("/")


# ---------------------------------------------------------------------------
# Cloud fetch
# ---------------------------------------------------------------------------

@dataclass
class CloudState:
    """What we know about the cloud registry for this executor."""

    tools: dict[str, dict[str, Any]]     # cloud_name → definition row
    names_only: bool                     # True when rows carry no parameters
    source: str                          # human-readable provenance
    all_definition_names: set[str] | None = None  # platform-wide, for collisions


def _load_baseline_file(path: Path) -> CloudState:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["tools"] if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError(f"baseline file {path} is neither a row list nor {{'tools': [...]}}")
    tools = {r["name"]: r for r in rows}
    names_only = any("parameters" not in r for r in rows)
    return CloudState(tools=tools, names_only=names_only, source=f"baseline file {path}")


def _fetch_cloud_state(baseline: Path | None) -> CloudState:
    if baseline is not None:
        return _load_baseline_file(baseline)

    base = _base_url()
    url = base + TOOLS_ROUTE
    try:
        resp = httpx.get(url, timeout=20.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        print(f"\n!! Cloud registry UNREACHABLE ({url}): {exc}")
        print("!! Falling back to the embedded names-only baseline (49 names, 2026-07-10).")
        print("!! Parameter drift on existing tools CANNOT be verified in this mode.\n")
        return CloudState(
            tools={n: {"name": n} for n in FALLBACK_CLOUD_BASELINE},
            names_only=True,
            source="embedded fallback baseline (route unreachable)",
        )

    if resp.status_code == 404:
        print(f"\n!! Route not deployed yet: GET {url} → 404")
        print("!! (aidream serves /ai-tools/app/{executor}/all on newer builds.)")
        print("!! Falling back to the embedded names-only baseline (49 names, 2026-07-10).\n")
        return CloudState(
            tools={n: {"name": n} for n in FALLBACK_CLOUD_BASELINE},
            names_only=True,
            source="embedded fallback baseline (route 404)",
        )

    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("tools", [])
    state = CloudState(
        tools={r["name"]: r for r in rows},
        names_only=False,
        source=f"live {url} (executor={payload.get('executor_name')}, count={payload.get('count')})",
    )

    # Collision pre-flight: all definition names platform-wide.
    try:
        all_resp = httpx.get(base + ALL_TOOLS_ROUTE, timeout=30.0, follow_redirects=True)
        all_resp.raise_for_status()
        state.all_definition_names = {
            t["name"] for t in all_resp.json().get("tools", []) if t.get("name")
        }
    except httpx.HTTPError as exc:
        print(f"!! Collision pre-flight skipped — GET {base}{ALL_TOOLS_ROUTE} failed: {exc}")

    return state


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def _norm_params(params: Any) -> str:
    if isinstance(params, str):
        params = json.loads(params)
    return json.dumps(params or {}, sort_keys=True)


def _catalog_params(entry: CatalogEntry) -> dict[str, Any]:
    """The shape stored in tool.definition.parameters for this entry.

    Action-enum mega-tools carry a dedicated flat cloud dialect
    (``cloud_parameters``, incl. ``$variants``); plain tools store their
    standard input_schema unchanged."""
    return entry.cloud_parameters or entry.input_schema


@dataclass
class ToolDiff:
    new: list[CatalogEntry] = field(default_factory=list)
    changed: list[tuple[CatalogEntry, dict[str, Any]]] = field(default_factory=list)
    retired: list[CatalogEntry] = field(default_factory=list)  # legacy, collapsed into mega-tools
    removed: list[dict[str, Any]] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)  # names-only mode
    collisions: list[str] = field(default_factory=list)  # NEW name already a definition elsewhere
    cloud_source: str = ""
    names_only: bool = False

    @property
    def has_drift(self) -> bool:
        return bool(
            self.new or self.changed or self.retired or self.removed or self.collisions
        )


def compute_diff(cloud: CloudState) -> ToolDiff:
    diff = ToolDiff(cloud_source=cloud.source, names_only=cloud.names_only)
    catalog = get_catalog()
    catalog_names = {e.cloud_name for e in catalog}

    for entry in sorted(catalog, key=lambda e: e.cloud_name):
        row = cloud.tools.get(entry.cloud_name)

        if not entry.advertised:
            # Legacy tool collapsed into a mega-tool: still dispatchable, but
            # the cloud row must be RETIRED (binding deactivated). Only counts
            # as drift while the cloud still has an active binding for it.
            if row is not None:
                diff.retired.append(entry)
            continue

        if row is None:
            if (
                cloud.all_definition_names is not None
                and entry.cloud_name in cloud.all_definition_names
            ):
                # Exists platform-wide but is not bound to matrx-local: a real
                # collision — the changeset must not blindly INSERT.
                diff.collisions.append(entry.cloud_name)
            else:
                diff.new.append(entry)
        elif cloud.names_only:
            diff.unverified.append(entry.cloud_name)
        elif _norm_params(row.get("parameters")) != _norm_params(_catalog_params(entry)):
            diff.changed.append((entry, row))
        else:
            diff.ok.append(entry.cloud_name)

    diff.removed = [
        row for name, row in sorted(cloud.tools.items()) if name not in catalog_names
    ]
    return diff


def print_report(diff: ToolDiff) -> None:
    total = len(get_catalog())
    line = "═" * 72
    print(f"\n{line}")
    print(f"  TOOL REGISTRY DRIFT REPORT — code catalog ({total}) vs cloud canon")
    print(f"  cloud source: {diff.cloud_source}")
    print(line)

    def section(mark: str, title: str, names: list[str]) -> None:
        print(f"\n  {mark} {title} ({len(names)})")
        for n in names:
            print(f"      {mark} {n}")

    if diff.new:
        section("+", "NEW — in catalog, not bound in cloud", [e.cloud_name for e in diff.new])
    if diff.collisions:
        section(
            "‼", "COLLISION — definition exists platform-wide but is NOT bound "
            "to matrx-local (operator must decide: bind or rename)", diff.collisions,
        )
    if diff.changed:
        print(f"\n  ~ CHANGED — parameters differ ({len(diff.changed)})")
        for entry, _row in diff.changed:
            print(f"      ~ {entry.cloud_name}")
    if diff.retired:
        section(
            "×", "RETIRED — legacy tool collapsed into a mega-tool; cloud "
            "binding still active (changeset deactivates it)",
            [e.cloud_name for e in diff.retired],
        )
    if diff.removed:
        section(
            "-", "REMOVED — bound in cloud, absent from catalog (never auto-deleted)",
            [r["name"] for r in diff.removed],
        )
    if diff.unverified:
        print(
            f"\n  ? UNVERIFIED — {len(diff.unverified)} existing bindings could not be "
            "parameter-checked (names-only baseline). Re-run against the live route "
            "or a full baseline file for a complete diff."
        )
    if diff.ok:
        print(f"\n  ✓ OK — {len(diff.ok)} in sync")

    print()
    if diff.has_drift:
        print("  RESULT: DRIFT. The cloud is canon — emit a changeset "
              "(python -m app.tools.tool_sync emit-changeset) and apply it via "
              "the Supabase MCP. Never push code→DB silently.")
    else:
        print("  RESULT: no drift detected.")
    print(f"{line}\n")


def print_field_diff(diff: ToolDiff) -> None:
    for entry, row in diff.changed:
        print(f"\n── {entry.cloud_name} ──")
        print("  cloud parameters:")
        print("    " + _norm_params(row.get("parameters")))
        print("  catalog parameters:")
        print("    " + _norm_params(_catalog_params(entry)))


# ---------------------------------------------------------------------------
# Changeset emission
# ---------------------------------------------------------------------------

def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_jsonb(value: dict[str, Any]) -> str:
    return _sql_str(json.dumps(value, sort_keys=True)) + "::jsonb"


def _sql_text_array(values: tuple[str, ...]) -> str:
    if not values:
        return "ARRAY[]::text[]"
    return "ARRAY[" + ", ".join(_sql_str(v) for v in values) + "]::text[]"


def _new_tool_sql(entry: CatalogEntry) -> str:
    cols = (
        "name, description, parameters, category, tags, "
        "source_kind, tool_group, organization_id, visibility, is_active"
    )
    vals = ", ".join(
        [
            _sql_str(entry.cloud_name),
            _sql_str(entry.description),
            _sql_jsonb(_catalog_params(entry)),
            _sql_str(entry.category),
            _sql_text_array(entry.tags),
            _sql_str(DEFINITION_DEFAULTS["source_kind"]),
            _sql_str(DEFINITION_DEFAULTS["tool_group"]),
            _sql_str(MATRX_ORG_ID),
            _sql_str(DEFINITION_DEFAULTS["visibility"]),
            "true",
        ]
    )
    gate = f" [platforms: {', '.join(entry.platforms)}]" if entry.platforms else ""
    return (
        f"-- NEW: {entry.cloud_name} (dispatcher: {entry.dispatcher_name}){gate}\n"
        f"WITH def AS (\n"
        f"  INSERT INTO tool.definition ({cols})\n"
        f"  VALUES ({vals})\n"
        f"  RETURNING id\n"
        f")\n"
        f"INSERT INTO tool.binding (tool_id, executor_name, is_active)\n"
        f"SELECT id, {_sql_str(EXECUTOR_NAME)}, true FROM def;\n"
    )


def _changed_tool_sql(entry: CatalogEntry) -> str:
    return (
        f"-- CHANGED: {entry.cloud_name} — parameters updated to match the code catalog\n"
        f"UPDATE tool.definition\n"
        f"   SET parameters = {_sql_jsonb(_catalog_params(entry))},\n"
        f"       updated_at = now()\n"
        f" WHERE name = {_sql_str(entry.cloud_name)};\n"
    )


def _collision_sql(name: str) -> str:
    return (
        f"-- COLLISION (REVIEW REQUIRED): definition '{name}' already exists\n"
        f"-- platform-wide but is not bound to {EXECUTOR_NAME}. If it is the same\n"
        f"-- tool, apply the binding below; if it is a DIFFERENT tool, rename the\n"
        f"-- catalog entry instead (names are globally unique).\n"
        f"-- INSERT INTO tool.binding (tool_id, executor_name, is_active)\n"
        f"-- SELECT id, {_sql_str(EXECUTOR_NAME)}, true FROM tool.definition\n"
        f"--  WHERE name = {_sql_str(name)}\n"
        f"-- ON CONFLICT DO NOTHING;\n"
    )


def _retired_tool_sql(entry: CatalogEntry) -> str:
    """ACTIVE retirement — legacy tool collapsed into an action-enum mega-tool.

    Unlike REMOVED (a tool that vanished from the code, suggestion only),
    RETIRED is an intentional, reviewed transition: the handler still exists
    and is reachable through its mega-tool, so deactivating the flat binding
    is safe and required (no dual registrations — cloud is canon)."""
    return (
        f"-- RETIRED: '{entry.cloud_name}' collapsed into a mega-tool "
        f"(dispatcher: {entry.dispatcher_name})\n"
        f"UPDATE tool.binding b SET is_active = false, updated_at = now()\n"
        f"  FROM tool.definition d\n"
        f" WHERE d.id = b.tool_id AND d.name = {_sql_str(entry.cloud_name)}\n"
        f"   AND b.executor_name = {_sql_str(EXECUTOR_NAME)};\n"
        f"UPDATE tool.definition SET is_active = false, updated_at = now()\n"
        f" WHERE name = {_sql_str(entry.cloud_name)};\n"
    )


def _removed_tool_sql(row: dict[str, Any]) -> str:
    name = row.get("name", "?")
    return (
        f"-- REMOVED (SUGGESTION ONLY — commented out): '{name}' is bound in the\n"
        f"-- cloud but no longer exists in the code catalog. Deactivate the binding\n"
        f"-- if the removal was intentional:\n"
        f"-- UPDATE tool.binding b SET is_active = false, updated_at = now()\n"
        f"--   FROM tool.definition d\n"
        f"--  WHERE d.id = b.tool_id AND d.name = {_sql_str(name)}\n"
        f"--    AND b.executor_name = {_sql_str(EXECUTOR_NAME)};\n"
    )


def emit_changeset(diff: ToolDiff, out_path: Path) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    header = (
        f"-- ═══════════════════════════════════════════════════════════════════\n"
        f"-- matrx-local tool registry changeset\n"
        f"-- generated: {stamp} by app/tools/tool_sync.py emit-changeset\n"
        f"-- cloud baseline: {diff.cloud_source}\n"
        f"-- catalog: {len(get_catalog())} tools; "
        f"NEW={len(diff.new)} CHANGED={len(diff.changed)} "
        f"RETIRED={len(diff.retired)} "
        f"COLLISIONS={len(diff.collisions)} REMOVED={len(diff.removed)} "
        f"OK={len(diff.ok)} UNVERIFIED={len(diff.unverified)}\n"
        f"--\n"
        f"-- APPLY VIA SUPABASE MCP (project txzxabzwovsujtloxrus) AFTER REVIEW.\n"
        f"-- The desktop never writes tool.definition/tool.binding directly.\n"
        f"-- Wrap in a transaction; every statement is idempotent-safe to re-run\n"
        f"-- only as a whole (NEW inserts are not — do not apply twice).\n"
        f"-- ═══════════════════════════════════════════════════════════════════\n\n"
        f"BEGIN;\n\n"
    )

    parts: list[str] = [header]
    for entry in diff.new:
        parts.append(_new_tool_sql(entry) + "\n")
    for name in diff.collisions:
        parts.append(_collision_sql(name) + "\n")
    for entry, _row in diff.changed:
        parts.append(_changed_tool_sql(entry) + "\n")
    for entry in diff.retired:
        parts.append(_retired_tool_sql(entry) + "\n")
    for row in diff.removed:
        parts.append(_removed_tool_sql(row) + "\n")
    parts.append("COMMIT;\n")

    if not diff.has_drift:
        print("No drift — nothing to emit.")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"Changeset written: {out_path}")
    print(
        f"  NEW={len(diff.new)} CHANGED={len(diff.changed)} "
        f"RETIRED={len(diff.retired)} "
        f"COLLISIONS={len(diff.collisions)} REMOVED(suggested)={len(diff.removed)}"
    )


# ---------------------------------------------------------------------------
# Informational commands
# ---------------------------------------------------------------------------

def cmd_list() -> None:
    from collections import defaultdict

    by_cat: dict[str, list[CatalogEntry]] = defaultdict(list)
    for entry in get_catalog():
        by_cat[entry.category].append(entry)

    total = len(get_catalog())
    print(f"\n{'─' * 64}")
    print(f"  Canonical tool catalog  ({total} tools)")
    print(f"{'─' * 64}")
    for cat in sorted(by_cat):
        entries = sorted(by_cat[cat], key=lambda e: e.cloud_name)
        print(f"\n  {cat}  ({len(entries)} tools)")
        for e in entries:
            gate = f"  [{'/'.join(e.platforms)}]" if e.platforms else ""
            print(f"      {e.cloud_name:<34} {e.dispatcher_name}{gate}")
    print()


def cmd_show(name: str) -> None:
    entry = get_by_cloud_name(name)
    if entry is None:
        print(f"Tool '{name}' not found in the catalog (lookup is by cloud name, e.g. local_bash).")
        sys.exit(1)

    print(f"\n{'─' * 64}")
    print(f"  {entry.cloud_name}  (v{entry.version})")
    print(f"{'─' * 64}")
    print(f"  dispatcher:   {entry.dispatcher_name}")
    print(f"  category:     {entry.category}")
    print(f"  tags:         {list(entry.tags)}")
    print(f"  platforms:    {list(entry.platforms) if entry.platforms else 'all'}")
    print(f"  timeout:      {entry.timeout_seconds}s")
    print(f"  arg_model:    {entry.arg_model.__name__ if entry.arg_model else '(introspected)'}")
    print(f"\n  description:\n    {entry.description}")
    print(f"\n  input_schema:\n{json.dumps(entry.input_schema, indent=4)}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_opt(args: list[str], flag: str) -> str | None:
    if flag in args:
        idx = args.index(flag)
        if idx + 1 >= len(args):
            print(f"{flag} requires a value")
            sys.exit(2)
        value = args[idx + 1]
        del args[idx:idx + 2]
        return value
    return None


def main() -> None:
    args = sys.argv[1:]
    baseline_opt = _parse_opt(args, "--baseline")
    out_opt = _parse_opt(args, "--out")
    baseline = Path(baseline_opt) if baseline_opt else None

    command = args[0] if args else "status"

    if command in ("status", "diff"):
        diff = compute_diff(_fetch_cloud_state(baseline))
        print_report(diff)
        if command == "diff":
            print_field_diff(diff)
        sys.exit(1 if diff.has_drift else 0)

    elif command == "emit-changeset":
        diff = compute_diff(_fetch_cloud_state(baseline))
        print_report(diff)
        emit_changeset(diff, Path(out_opt) if out_opt else DEFAULT_CHANGESET_PATH)

    elif command == "list":
        cmd_list()

    elif command == "show":
        if len(args) < 2:
            print("Usage: tool_sync show <cloud_name>")
            sys.exit(2)
        cmd_show(args[1])

    else:
        print(f"Unknown command: {command}")
        print("Commands: status | diff | emit-changeset [--baseline f] [--out f] | list | show <name>")
        sys.exit(2)


if __name__ == "__main__":
    main()
