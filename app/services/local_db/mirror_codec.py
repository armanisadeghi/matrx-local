"""Value codec between the SQLite mirror and PostgREST JSON rows.

The mirror stores every canonical column in a SQLite-compatible form:
jsonb/arrays as JSON text, bools as 0/1, uuids/timestamps as text. PostgREST
speaks typed JSON. These helpers translate whole rows both ways, driven by
the generated per-column Postgres types in mirror_schema.MIRROR_TABLES.
"""

from __future__ import annotations

import json
from typing import Any

from app.common.system_logger import get_logger
from app.services.local_db.mirror_schema import MIRROR_TABLES

logger = get_logger()

_JSON_UDTS = {"json", "jsonb"}


def _is_array(udt: str) -> bool:
    return udt.startswith("_")


def table_spec(schema: str, table: str) -> dict[str, Any]:
    return MIRROR_TABLES[schema][table]


def decode_remote_row(schema: str, table: str, row: dict[str, Any]) -> dict[str, Any]:
    """PostgREST JSON row -> SQLite mirror row (known columns only).

    Unknown columns mean the cloud schema moved ahead of the snapshot —
    logged loudly by the caller's drift path; here they are skipped so a pull
    never crashes on new columns.
    """
    spec = table_spec(schema, table)
    pg_types = spec["pg_types"]
    out: dict[str, Any] = {}
    for col, value in row.items():
        if col not in pg_types:
            continue
        if value is None:
            out[col] = None
        elif isinstance(value, bool):
            out[col] = int(value)
        elif isinstance(value, (dict, list)):
            out[col] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            out[col] = value
    return out


def encode_local_row(
    schema: str,
    table: str,
    row: dict[str, Any],
    *,
    strip: frozenset[str],
) -> dict[str, Any]:
    """SQLite mirror row -> PostgREST JSON payload.

    ``strip`` columns are omitted (cloud triggers own them). ``None`` values
    are omitted too: on insert the cloud defaults apply; on upsert-merge the
    cloud keeps its value — chat rows are append-mostly, and the one delete
    signal that matters (deleted_at) is only ever pushed non-null.
    """
    spec = table_spec(schema, table)
    pg_types = spec["pg_types"]
    out: dict[str, Any] = {}
    for col, value in row.items():
        if col in strip or col not in pg_types or value is None:
            continue
        udt = pg_types[col]
        if udt == "bool":
            out[col] = bool(value)
        elif udt in _JSON_UDTS or _is_array(udt):
            if isinstance(value, str):
                try:
                    out[col] = json.loads(value)
                except json.JSONDecodeError:
                    logger.error(
                        "[mirror_codec] %s.%s.%s holds invalid JSON locally — "
                        "wrapping as raw string so the push is not lost",
                        schema, table, col,
                    )
                    out[col] = {"raw": value} if udt in _JSON_UDTS else [value]
            else:
                out[col] = value
        else:
            out[col] = value
    return out
