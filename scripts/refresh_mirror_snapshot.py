#!/usr/bin/env python3
"""Refresh the mirror's copy of the ONE database description.

matrx-local no longer introspects the cloud itself (the retired
``schema_mirror/snapshot.json`` was a second catalogue emitter). The description
is emitted once, by ``@ai-matrx/data``:

    (aidream) node apps/shared/data/bin/matrx-data.mjs emit --out db-contract

and this script copies the schemas the mirror reads (``mirror_description.SCHEMAS``)
from that directory into ``schema_mirror/description/`` with ``source.json`` naming
the contract hash. It refuses a destructive change to a mirrored relation (removed
relation/column, changed kind or primary key) until the retirement ledger is reviewed.

    python scripts/refresh_mirror_snapshot.py [--from ../aidream/db-contract] [--check]

--check: exit 1 when the copies differ from the source description (CI parity with aidream).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from generate_mirror_schema import MIRRORED_SCHEMAS  # noqa: E402
from mirror_description import DESCRIPTION_DIR, SCHEMAS, SOURCE_PATH, load_snapshot, read_schema, to_snapshot  # noqa: E402

DEFAULT_FROM = ROOT.parent / "aidream" / "db-contract"


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
            if not is_mirrored(schema, relation) or details["kind"] != "table":
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", default=str(DEFAULT_FROM), help="the description directory (aidream db-contract/)")
    parser.add_argument("--check", action="store_true", help="exit 1 when the copies differ from the source")
    args = parser.parse_args()
    source = Path(args.source)
    index_path = source / "index.json"
    if not index_path.exists():
        print(f"no database description at {source} — emit it in aidream first (matrx-data emit)", file=sys.stderr)
        return 2
    index = json.loads(index_path.read_text())
    texts = {schema: (source / f"{schema}.json").read_text() for schema in SCHEMAS}
    src = {
        "contractSha256": index["contractSha256"],
        "format": index["format"],
        "from": "aidream db-contract/ (matrx-data emit)",
        "schemas": {schema: index["schemas"][schema]["sha256"] for schema in SCHEMAS},
    }
    src_text = json.dumps(src, indent=1, sort_keys=True) + "\n"

    if args.check:
        stale = [s for s in SCHEMAS if not (DESCRIPTION_DIR / f"{s}.json").exists() or (DESCRIPTION_DIR / f"{s}.json").read_text() != texts[s]]
        if stale:
            print(f"DRIFT: schema_mirror/description/ differs from {source} for: {', '.join(stale)}. "
                  "Run scripts/refresh_mirror_snapshot.py then scripts/generate_mirror_schema.py.", file=sys.stderr)
            return 1
        print(f"schema_mirror/description/ matches contract {index['contractSha256'][:12]}.")
        return 0

    old = load_snapshot() if SOURCE_PATH.exists() else {"schemas": {}}
    updated = to_snapshot({s: json.loads(t) for s, t in texts.items()}, src["contractSha256"][:12])
    validate_non_destructive(old, updated)
    DESCRIPTION_DIR.mkdir(parents=True, exist_ok=True)
    for schema, text in texts.items():
        (DESCRIPTION_DIR / f"{schema}.json").write_text(text)
    SOURCE_PATH.write_text(src_text)
    print(f"copied {', '.join(SCHEMAS)} from contract {index['contractSha256'][:12]} — now run scripts/generate_mirror_schema.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
