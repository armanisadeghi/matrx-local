#!/usr/bin/env python3
"""Report drift between matrx-local's executable tool contracts and Supabase.

This is deliberately loud and non-blocking at the integration points. The
script exits 1 when it finds drift so callers receive a useful signal, while
release/CI callers explicitly continue. An unavailable registry exits 0 after
a prominent warning: "could not verify" is not evidence of drift.

The database is the source of truth. This script is read-only and never emits
or applies database changes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx

# Direct script execution sets sys.path[0] to scripts/, not the repository
# root. Make the documented `python scripts/...` command import the real app.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL  # noqa: E402
from app.services.delegation.user_review import USER_REVIEW_TOOLS  # noqa: E402
from app.tools.catalog import get_advertised_catalog  # noqa: E402

EXECUTOR_NAME = "matrx-local"
SURFACE_NAME = "matrx-local/desktop"

# This discovery tool is always offered on the desktop surface but executes in
# matrx-ai-core, not matrx-local. It therefore must not have a matrx-local
# binding. Every other always_include_tools entry needs an active local binding.
SURFACE_BINDING_ALLOWLIST = frozenset({"load_desktop_tools"})


@dataclass(frozen=True)
class LocalTool:
    name: str
    parameters: Mapping[str, Any]
    tier: str | None
    category: str | None
    admin_only: bool


@dataclass(frozen=True)
class DbTool:
    id: str
    name: str
    parameters: Mapping[str, Any]
    tier: str | None
    category: str | None
    admin_only: bool
    is_active: bool


@dataclass(frozen=True)
class RegistryState:
    tools: tuple[DbTool, ...]
    active_binding_ids: frozenset[str]
    active_binding_count: int
    always_include_tools: frozenset[str]
    surface_active: bool


@dataclass
class DriftReport:
    bound_not_implemented: list[str] = field(default_factory=list)
    implemented_not_bound: list[str] = field(default_factory=list)
    contract_issues: dict[str, list[str]] = field(default_factory=dict)
    surface_without_binding: list[str] = field(default_factory=list)
    registry_issues: list[str] = field(default_factory=list)

    @property
    def problem_count(self) -> int:
        return (
            len(self.bound_not_implemented)
            + len(self.implemented_not_bound)
            + sum(len(issues) for issues in self.contract_issues.values())
            + len(self.surface_without_binding)
            + len(self.registry_issues)
        )


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _value_set(values: Iterable[Any]) -> set[str]:
    return {_json_key(value) for value in values}


def _field_map(parameters: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    """Return real parameter fields, excluding $-prefixed contract metadata."""

    return {
        name: spec if isinstance(spec, Mapping) else {}
        for name, spec in (parameters or {}).items()
        if not name.startswith("$")
    }


def _type_key(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return "|".join(sorted(str(item) for item in value))
    return str(value)


def compare_tool(local: LocalTool, db: DbTool) -> list[str]:
    """Compare the shared code↔DB structural contract for one tool."""

    issues: list[str] = []
    if local.tier != db.tier:
        issues.append(f"tier differs (local={local.tier!r}, db={db.tier!r})")
    if local.admin_only != db.admin_only:
        issues.append(
            "admin_only differs "
            f"(local={local.admin_only!r}, db={db.admin_only!r})"
        )
    if local.category != db.category:
        issues.append(
            f"category differs (local={local.category!r}, db={db.category!r})"
        )

    local_fields = _field_map(local.parameters)
    db_fields = _field_map(db.parameters)
    local_names = set(local_fields)
    db_names = set(db_fields)

    if only_local := sorted(local_names - db_names):
        issues.append(f"fields only in local: {', '.join(only_local)}")
    if only_db := sorted(db_names - local_names):
        issues.append(f"fields only in DB: {', '.join(only_db)}")

    local_required = {
        name for name, spec in local_fields.items() if spec.get("required") is True
    }
    db_required = {
        name for name, spec in db_fields.items() if spec.get("required") is True
    }
    if only_local := sorted(local_required - db_required):
        issues.append(f"required only in local: {', '.join(only_local)}")
    if only_db := sorted(db_required - local_required):
        issues.append(f"required only in DB: {', '.join(only_db)}")

    for name in sorted(local_names & db_names):
        local_spec = local_fields[name]
        db_spec = db_fields[name]

        local_type = _type_key(local_spec.get("type"))
        db_type = _type_key(db_spec.get("type"))
        if local_type != db_type:
            issues.append(
                f"{name}: type differs (local={local_type!r}, db={db_type!r})"
            )

        local_has_enum = "enum" in local_spec
        db_has_enum = "enum" in db_spec
        if local_has_enum != db_has_enum:
            side = "local" if local_has_enum else "DB"
            issues.append(f"{name}: enum only in {side}")
        elif local_has_enum and db_has_enum:
            local_enum = _value_set(local_spec.get("enum") or [])
            db_enum = _value_set(db_spec.get("enum") or [])
            if local_enum != db_enum:
                only_local = sorted(local_enum - db_enum)
                only_db = sorted(db_enum - local_enum)
                parts: list[str] = []
                if only_local:
                    parts.append(f"only in local: {only_local}")
                if only_db:
                    parts.append(f"only in DB: {only_db}")
                issues.append(f"{name}: enum drift — {'; '.join(parts)}")

        local_has_default = "default" in local_spec
        db_has_default = "default" in db_spec
        if local_has_default != db_has_default:
            side = "local" if local_has_default else "DB"
            value = local_spec.get("default") if local_has_default else db_spec.get("default")
            issues.append(f"{name}: default only in {side} ({value!r})")
        elif local_has_default and (
            _json_key(local_spec.get("default")) != _json_key(db_spec.get("default"))
        ):
            issues.append(
                f"{name}: default differs "
                f"(local={local_spec.get('default')!r}, db={db_spec.get('default')!r})"
            )

    return issues


def load_local_tools() -> tuple[LocalTool, ...]:
    """Load only the 19 executable, advertised mega-tool contracts.

    ``cloud_parameters`` is composed from the Pydantic models that
    ``actions.make_group_handler`` validates before fan-out and, for actions
    without a model, from the real handler signatures enforced by ``dispatch``.
    Reading it here therefore inspects the executable contract, not a generated
    mirror disconnected from runtime validation.
    """

    tools: list[LocalTool] = []
    for entry in get_advertised_catalog():
        if entry.cloud_parameters is None:
            raise RuntimeError(
                f"advertised tool {entry.cloud_name!r} has no executable cloud contract"
            )
        tools.append(
            LocalTool(
                name=entry.cloud_name,
                parameters=entry.cloud_parameters,
                tier=entry.tier,
                category=entry.category,
                admin_only=entry.admin_only,
            )
        )
    return tuple(sorted(tools, key=lambda tool: tool.name))


def _headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {SUPABASE_PUBLISHABLE_KEY}",
        "Accept": "application/json",
        # The registry lives in the exposed `tool` schema. Without this header
        # PostgREST resolves names against its default schema and returns PGRST205.
        "Accept-Profile": "tool",
    }


def fetch_registry_state(client: httpx.Client) -> RegistryState:
    base = f"{SUPABASE_URL.rstrip('/')}/rest/v1"
    bindings_response = client.get(
        f"{base}/binding",
        headers=_headers(),
        params={
            "or": (
                f"(executor_name.eq.{EXECUTOR_NAME},"
                f"executor_name.like.{EXECUTOR_NAME}.*)"
            ),
            "is_active": "eq.true",
            "select": "tool_id,executor_name,is_active",
        },
    )
    bindings_response.raise_for_status()
    bindings = bindings_response.json()
    if not isinstance(bindings, list):
        raise ValueError("tool.binding response was not a row list")

    binding_ids = frozenset(
        str(row["tool_id"])
        for row in bindings
        if isinstance(row, Mapping) and row.get("tool_id")
    )
    definitions: list[Mapping[str, Any]] = []
    if binding_ids:
        definitions_response = client.get(
            f"{base}/definition",
            headers=_headers(),
            params={
                "id": f"in.({','.join(sorted(binding_ids))})",
                "select": (
                    "id,name,parameters,tier,category,admin_only,is_active"
                ),
                "order": "name.asc",
            },
        )
        definitions_response.raise_for_status()
        raw_definitions = definitions_response.json()
        if not isinstance(raw_definitions, list):
            raise ValueError("tool.definition response was not a row list")
        definitions = raw_definitions

    surface_response = client.get(
        f"{base}/surface_defaults",
        headers=_headers(),
        params={
            "surface_name": f"eq.{SURFACE_NAME}",
            "select": "surface_name,always_include_tools,never_include_tools,is_active",
        },
    )
    surface_response.raise_for_status()
    surfaces = surface_response.json()
    if not isinstance(surfaces, list):
        raise ValueError("tool.surface_defaults response was not a row list")
    if len(surfaces) > 1:
        raise ValueError(f"{SURFACE_NAME} returned multiple surface-default rows")
    surface = surfaces[0] if surfaces else {}

    tools = tuple(
        DbTool(
            id=str(row["id"]),
            name=str(row["name"]),
            parameters=(
                row.get("parameters")
                if isinstance(row.get("parameters"), Mapping)
                else {}
            ),
            tier=row.get("tier"),
            category=row.get("category"),
            admin_only=bool(row.get("admin_only")),
            is_active=bool(row.get("is_active")),
        )
        for row in definitions
        if isinstance(row, Mapping) and row.get("id") and row.get("name")
    )
    return RegistryState(
        tools=tools,
        active_binding_ids=binding_ids,
        active_binding_count=len(bindings),
        always_include_tools=frozenset(surface.get("always_include_tools") or []),
        surface_active=bool(surface.get("is_active")) if surface else False,
    )


def compute_drift(
    local_tools: Iterable[LocalTool],
    registry: RegistryState,
) -> DriftReport:
    report = DriftReport()
    local_by_name = {tool.name: tool for tool in local_tools}
    db_by_name = {tool.name: tool for tool in registry.tools if tool.is_active}
    active_bound_names = set(db_by_name)

    missing_definition_ids = registry.active_binding_ids - {
        tool.id for tool in registry.tools
    }
    for tool_id in sorted(missing_definition_ids):
        report.registry_issues.append(
            f"active binding points to missing definition {tool_id}"
        )
    for tool in registry.tools:
        if not tool.is_active:
            report.registry_issues.append(
                f"active binding points to inactive definition {tool.name}"
            )

    # Review-only tools are intentionally bound to this client but never enter
    # the executable dispatcher. The desktop parks them for an explicit human
    # decision, which is the authorization boundary rather than drift.
    report.bound_not_implemented = sorted(
        set(db_by_name) - set(local_by_name) - set(USER_REVIEW_TOOLS)
    )
    report.implemented_not_bound = sorted(set(local_by_name) - set(db_by_name))

    for name in sorted(set(local_by_name) & set(db_by_name)):
        issues = compare_tool(local_by_name[name], db_by_name[name])
        if issues:
            report.contract_issues[name] = issues

    if not registry.surface_active:
        report.registry_issues.append(
            f"surface defaults row {SURFACE_NAME!r} is missing or inactive"
        )
    report.surface_without_binding = sorted(
        name
        for name in registry.always_include_tools
        if name not in active_bound_names and name not in SURFACE_BINDING_ALLOWLIST
    )
    return report


def print_report(
    report: DriftReport,
    *,
    local_count: int,
    registry: RegistryState,
) -> None:
    print(
        "Tool-registry drift check — executable catalog ↔ "
        "tool.{definition,binding,surface_defaults}"
    )
    print(f"  local advertised tools:              {local_count}")
    print(f"  active matrx-local binding rows:      {registry.active_binding_count}")
    print(f"  active bound definitions read:        {sum(t.is_active for t in registry.tools)}")
    print(f"  {SURFACE_NAME} always_include_tools: {len(registry.always_include_tools)}")
    print()

    if report.problem_count == 0:
        print("✓ No tool-registry drift detected.")
        return

    bar = "!" * 76
    print(bar)
    print("!!! TOOL-REGISTRY / DISPATCHER DRIFT DETECTED !!!")
    print(f"!!! {report.problem_count} problem(s); runtime tool calls can fail or misvalidate.")
    print(bar)
    print()

    if report.bound_not_implemented:
        print("DB-bound but not implemented by this dispatcher:")
        for name in report.bound_not_implemented:
            print(f"  - {name}")
        print()
    if report.implemented_not_bound:
        print("Implemented by this dispatcher but missing an active binding:")
        for name in report.implemented_not_bound:
            print(f"  - {name}")
        print()
    if report.contract_issues:
        print("Definition/schema divergence:")
        for name, issues in report.contract_issues.items():
            print(f"  - {name}")
            for issue in issues:
                print(f"      * {issue}")
        print()
    if report.surface_without_binding:
        print(f"{SURFACE_NAME} advertises names with no active matrx-local binding:")
        for name in report.surface_without_binding:
            print(f"  - {name}")
        print()
    if report.registry_issues:
        print("Registry integrity issues:")
        for issue in report.registry_issues:
            print(f"  - {issue}")
        print()

    print(bar)
    print("Fix tool.definition/tool.binding or the executable local models; do not")
    print("reactivate the 115 retired granular bindings.")
    print(bar)


def main() -> int:
    try:
        local_tools = load_local_tools()
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            registry = fetch_registry_state(client)
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print("!" * 76, file=sys.stderr)
        print(
            "TOOL-REGISTRY DRIFT CHECK COULD NOT VERIFY THE LIVE DATABASE",
            file=sys.stderr,
        )
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        print("This is not reported as drift; release/CI remains non-blocking.", file=sys.stderr)
        print("!" * 76, file=sys.stderr)
        return 0

    report = compute_drift(local_tools, registry)
    print_report(report, local_count=len(local_tools), registry=registry)
    return 1 if report.problem_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
