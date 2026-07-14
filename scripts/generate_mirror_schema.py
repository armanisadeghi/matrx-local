#!/usr/bin/env python3
"""Generate the local SQLite mirror schema from the canonical cloud snapshot.

The cloud database (Supabase project txzxabzwovsujtloxrus) is the spec. This
script consumes ``schema_mirror/snapshot.json`` — a checked-in introspection
dump of the cloud schemas — and emits
``app/services/local_db/mirror_schema.py``, the generated module the engine
uses to create and drift-check the local mirror tables.

The generated DDL is a *structural* mirror: same table names, same column
names, SQLite-compatible types. Constraints are intentionally relaxed:

- Only primary-key columns are NOT NULL. The cloud enforces integrity; a
  replica must never reject rows the cloud accepted.
- No foreign keys. Pull order must not be able to deadlock on FK ordering,
  and partial pulls must be able to land child rows before parents.
- No cloud defaults. Local writers supply what they need; pulled rows carry
  cloud-computed values.

Refreshing the snapshot (when the cloud schema changes):
1. Run the introspection SQL in schema_mirror/README.md against the live DB
   (Supabase MCP execute_sql or psql) and rebuild snapshot.json.
2. Re-run this script. Commit both files together.

Usage:
    python scripts/generate_mirror_schema.py            # regenerate
    python scripts/generate_mirror_schema.py --check    # verify no drift
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO_ROOT / "schema_mirror" / "snapshot.json"
OUTPUT_PATH = REPO_ROOT / "app" / "services" / "local_db" / "mirror_schema.py"

# Phase 1 scope: the chat system. workbench.* and ai.* are present in the
# snapshot and become one-line additions here once their cutover lands.
MIRRORED_SCHEMAS: dict[str, dict] = {
    "chat": {"include_views": False},
}

# Postgres udt_name -> SQLite column type. Everything unlisted maps to TEXT
# (uuids, enums, timestamps, json — all stored as their PostgREST JSON string
# form so pulled rows round-trip losslessly).
_INTEGER_UDTS = {"int2", "int4", "int8", "smallint", "integer", "bigint"}
_REAL_UDTS = {"float4", "float8", "numeric"}
_BLOB_UDTS = {"bytea"}


def sqlite_type(udt: str) -> str:
    if udt in _INTEGER_UDTS or udt == "bool":
        return "INTEGER"
    if udt in _REAL_UDTS:
        return "REAL"
    if udt in _BLOB_UDTS:
        return "BLOB"
    return "TEXT"


def cursor_column(columns: list[dict]) -> str | None:
    names = {c["name"] for c in columns}
    if "updated_at" in names:
        return "updated_at"
    if "created_at" in names:
        return "created_at"
    return None


_INDEX_CANDIDATES = ("conversation_id", "user_id", "created_by", "updated_at", "created_at")


def build_table_entry(schema: str, name: str, spec: dict) -> dict:
    pk = spec["pk"]
    cols = spec["columns"]
    col_defs = []
    for c in cols:
        typ = sqlite_type(c["udt"])
        notnull = " NOT NULL" if c["name"] in pk else ""
        col_defs.append(f'"{c["name"]}" {typ}{notnull}')
    pk_clause = ", PRIMARY KEY (" + ", ".join(f'"{c}"' for c in pk) + ")" if pk else ""
    create_sql = (
        f'CREATE TABLE IF NOT EXISTS "{schema}"."{name}" (\n    '
        + ",\n    ".join(col_defs)
        + f"{pk_clause}\n)"
    )
    names = {c["name"] for c in cols}
    # SQLite attaches the schema qualifier to the index name, not the table.
    indexes = [
        f'CREATE INDEX IF NOT EXISTS "{schema}"."idx_{name}_{ic}" ON "{name}" ("{ic}")'
        for ic in _INDEX_CANDIDATES
        if ic in names and ic not in pk
    ]
    return {
        "columns": {c["name"]: sqlite_type(c["udt"]) for c in cols},
        "pk": pk,
        "cursor_col": cursor_column(cols),
        "has_deleted_at": "deleted_at" in names,
        "create_sql": create_sql,
        "index_sql": indexes,
    }


def generate(snapshot: dict) -> str:
    snap_text = json.dumps(snapshot, indent=1, sort_keys=True) + "\n"
    snap_hash = hashlib.sha256(snap_text.encode()).hexdigest()

    mirror: dict[str, dict[str, dict]] = {}
    for schema, opts in MIRRORED_SCHEMAS.items():
        tables = snapshot["schemas"].get(schema)
        if tables is None:
            raise SystemExit(f"schema '{schema}' missing from snapshot — refresh snapshot.json")
        mirror[schema] = {}
        for name, spec in sorted(tables.items()):
            if spec["kind"] != "table" and not opts["include_views"]:
                continue
            mirror[schema][name] = build_table_entry(schema, name, spec)

    body = json.dumps(mirror, indent=4, sort_keys=True)
    # Render as a Python literal (json booleans/nulls -> Python).
    body = body.replace(": true", ": True").replace(": false", ": False").replace(": null", ": None")

    return f'''"""GENERATED FILE — do not edit by hand.

Structural mirror of the canonical cloud schemas for the local SQLite store.
Regenerate with: python scripts/generate_mirror_schema.py
Source snapshot: schema_mirror/snapshot.json (cloud DB is the spec).
"""

SNAPSHOT_HASH = "{snap_hash}"
SNAPSHOT_GENERATED_AT = "{snapshot["generated_at"]}"

# schema -> table -> {{columns, pk, cursor_col, has_deleted_at, create_sql, index_sql}}
MIRROR_TABLES = {body}
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the generated module is current")
    args = parser.parse_args()

    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    rendered = generate(snapshot)

    if args.check:
        current = OUTPUT_PATH.read_text() if OUTPUT_PATH.exists() else ""
        if current != rendered:
            print(
                "DRIFT: app/services/local_db/mirror_schema.py is stale relative to "
                "schema_mirror/snapshot.json. Run scripts/generate_mirror_schema.py.",
                file=sys.stderr,
            )
            return 1
        print("mirror_schema.py is current.")
        return 0

    OUTPUT_PATH.write_text(rendered)
    n = sum(len(t) for t in json.loads(SNAPSHOT_PATH.read_text())["schemas"].values())
    print(f"wrote {OUTPUT_PATH} (snapshot has {n} relations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
