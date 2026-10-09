"""The ONE database description, read for the local SQLite mirror.

The cloud database is the spec. Its description is emitted ONCE, by
``@ai-matrx/data``'s ``matrx-data emit`` (aidream ``db-contract/``), and every
client generates from it — TypeScript doors, Swift and Kotlin later, and this
mirror. matrx-local is never an exception: it no longer runs its own catalogue
query (the retired ``schema_mirror/snapshot.json``). ``schema_mirror/description/``
holds byte copies of the schemas the mirror reads plus ``source.json`` naming the
contract they came from; ``scripts/refresh_mirror_snapshot.py`` refreshes them.

``load_snapshot()`` returns the shape the mirror generator always consumed —
``{"generated_at", "schemas": {schema: {table: {kind, pk, columns[{name, udt, ...}]}}}}`` —
so the generated DDL is unchanged by where the catalogue came from.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESCRIPTION_DIR = REPO_ROOT / "schema_mirror" / "description"
SOURCE_PATH = DESCRIPTION_DIR / "source.json"
SCHEMAS: tuple[str, ...] = ("ai", "chat", "files", "workbench")

# The description names a type as Postgres prints it (format_type); the mirror and
# the sync codec speak information_schema's udt_name.
_UDT = {
    "integer": "int4",
    "smallint": "int2",
    "bigint": "int8",
    "boolean": "bool",
    "real": "float4",
    "double precision": "float8",
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "time with time zone": "timetz",
    "time without time zone": "time",
    "character varying": "varchar",
    "character": "bpchar",
    "bit varying": "varbit",
}


def udt_of(column: dict) -> str:
    """information_schema.udt_name for a description column."""
    if column.get("enum"):
        base = column["enum"].split(".", 1)[1]
    else:
        base = _UDT.get(column["type"], column["type"])
        if "." in base:  # a composite/domain named schema.name
            base = base.split(".", 1)[1]
    return f"_{base}" if column.get("array") else base


def read_schema(schema: str, directory: Path = DESCRIPTION_DIR) -> dict:
    return json.loads((directory / f"{schema}.json").read_text())


def to_snapshot(descriptions: dict[str, dict], label: str) -> dict:
    schemas: dict[str, dict] = {}
    for schema, desc in descriptions.items():
        out: dict[str, dict] = {}
        for kind_key, kind in (("tables", "table"), ("views", "view")):
            for name, rel in desc.get(kind_key, {}).items():
                out[name] = {
                    "kind": "view" if kind == "view" else "table",
                    "pk": list(rel.get("primaryKey", [])),
                    "columns": [
                        {
                            "name": c["name"],
                            "udt": udt_of(c),
                            "nullable": bool(c.get("nullable")),
                            "default": bool(c.get("default")),
                        }
                        for c in rel["columns"]
                    ],
                }
        schemas[schema] = out
    return {"generated_at": label, "schemas": schemas}


def load_snapshot(directory: Path = DESCRIPTION_DIR) -> dict:
    source = json.loads((directory / "source.json").read_text())
    descriptions = {schema: read_schema(schema, directory) for schema in SCHEMAS}
    return to_snapshot(descriptions, f"contract {source['contractSha256'][:12]}")
