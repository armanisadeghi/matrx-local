#!/usr/bin/env python3
"""Refresh the checked-in mirror snapshot from the canonical cloud database.

Requires SUPABASE_MATRIX_HOST/PORT/USER/PASSWORD in the environment. This is
read-only against Postgres and refuses a destructive snapshot change: removed
relations/columns or changed relation/primary-key identity require review.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import sys

import psycopg


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "schema_mirror" / "snapshot.json"
sys.path.insert(0, str(ROOT / "scripts"))
from generate_mirror_schema import MIRRORED_SCHEMAS  # noqa: E402


def is_mirrored(schema: str, relation: str) -> bool:
    """True when the generator builds a local SQLite table for this relation.

    Only those can hold local data an app upgrade must not lose; a column dropped from a
    relation the device never mirrors (``files.sync_mappings``) needs no retirement review.
    """
    opts = MIRRORED_SCHEMAS.get(schema)
    if opts is None:
        return False
    scope = opts.get("tables")
    if scope is not None and relation not in scope:
        return False
    return relation not in opts.get("exclude_tables", ())


def validate_non_destructive(old: dict, updated: dict) -> None:
    concerns = []
    for schema, tables in old["schemas"].items():
        for relation, details in tables.items():
            if not is_mirrored(schema, relation):
                continue
            live = updated["schemas"].get(schema, {}).get(relation)
            if live is None:
                concerns.append(f"{schema}.{relation} (relation removed)")
                continue
            if details["kind"] != live["kind"]:
                concerns.append(f"{schema}.{relation} (relation kind changed)")
            if details["pk"] != live["pk"]:
                concerns.append(f"{schema}.{relation} (primary key changed)")
            old_names = {column["name"] for column in details["columns"]}
            live_names = {column["name"] for column in live["columns"]}
            concerns.extend(f"{schema}.{relation}.{name}" for name in sorted(old_names - live_names))
    if concerns:
        raise SystemExit("review retired columns or changed identity before refresh: " + ", ".join(concerns))


def main() -> None:
    old = json.loads(SNAPSHOT.read_text())
    schemas = tuple(old["schemas"])
    if set(schemas) != {"ai", "chat", "files", "workbench"}:
        raise SystemExit("unexpected snapshot schema scope")

    with psycopg.connect(
        host=os.environ["SUPABASE_MATRIX_HOST"],
        port=os.environ["SUPABASE_MATRIX_PORT"],
        user=os.environ["SUPABASE_MATRIX_USER"],
        password=os.environ["SUPABASE_MATRIX_PASSWORD"],
        dbname="postgres",
        sslmode="require",
        connect_timeout=10,
    ) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                """select table_schema, table_name, table_type
                   from information_schema.tables
                  where table_schema = any(%s)
                  order by table_schema, table_name""",
                (list(schemas),),
            )
            relations = cur.fetchall()
            cur.execute(
                """select table_schema, table_name, column_name,
                          data_type, udt_name, is_nullable, column_default
                   from information_schema.columns
                  where table_schema = any(%s)
                  order by table_schema, table_name, ordinal_position""",
                (list(schemas),),
            )
            columns = cur.fetchall()
            cur.execute(
                """select tc.table_schema, tc.table_name, kcu.column_name
                   from information_schema.table_constraints tc
                   join information_schema.key_column_usage kcu
                     on tc.table_catalog = kcu.table_catalog
                    and tc.table_schema = kcu.table_schema
                    and tc.table_name = kcu.table_name
                    and tc.constraint_catalog = kcu.constraint_catalog
                    and tc.constraint_schema = kcu.constraint_schema
                    and tc.constraint_name = kcu.constraint_name
                  where tc.constraint_type = 'PRIMARY KEY'
                    and tc.table_schema = any(%s)
                  order by tc.table_schema, tc.table_name, kcu.ordinal_position""",
                (list(schemas),),
            )
            primary_keys = cur.fetchall()

    if old.get("snapshot_version") != 1:
        raise SystemExit("unexpected or missing snapshot_version")
    updated = {**old, "generated_at": date.today().isoformat(), "schemas": {}}
    for schema, relation, table_type in relations:
        updated["schemas"].setdefault(schema, {})[relation] = {
            "columns": [],
            "kind": "view" if table_type == "VIEW" else "table",
            "pk": [],
        }
    for schema, relation, name, data_type, udt, nullable, default in columns:
        updated["schemas"][schema][relation]["columns"].append(
            {
                "data_type": data_type,
                "default": default,
                "name": name,
                "nullable": nullable == "YES",
                "udt": udt,
            }
        )
    for schema, relation, name in primary_keys:
        updated["schemas"][schema][relation]["pk"].append(name)
    validate_non_destructive(old, updated)

    SNAPSHOT.write_text(json.dumps(updated, indent=1) + "\n")
    print(f"Refreshed {len(relations)} relations and {len(columns)} columns")


if __name__ == "__main__":
    main()
