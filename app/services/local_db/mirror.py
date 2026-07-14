"""Attach + ensure the local structural mirror of the canonical cloud schemas.

Each mirrored cloud schema lives in its own SQLite file
(``<data-dir>/mirror/<schema>.db``) ATTACHed under the schema name, so local
SQL uses the canonical qualified names — ``chat.conversation``,
``chat.message`` — exactly as they appear in the cloud database.

The DDL comes from the generated ``mirror_schema.py`` (cloud is the spec;
see schema_mirror/README.md). Drift handling is loud and additive-only:

- table missing            -> created
- column missing locally   -> ALTER TABLE ADD COLUMN
- column type mismatch     -> ERROR log naming schema.table.column (never silent)
- extra local column       -> ERROR log (someone hand-edited the mirror)

Incompatible drift never crashes the engine — the mirror is a replica and the
app must keep working offline — but it must never be silently absorbed.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

import aiosqlite

from app.common.system_logger import get_logger
from app.services.local_db.mirror_schema import MIRROR_TABLES, SNAPSHOT_HASH

logger = get_logger()

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def mirror_dir_for(db_path: Path) -> Path:
    """Mirror files live beside the main DB so tests inherit isolation."""
    return db_path.parent / "mirror"


async def attach_and_ensure_mirror(db: aiosqlite.Connection, main_db_path: Path) -> None:
    """Attach every mirrored schema and bring its tables up to spec."""
    mirror_dir = mirror_dir_for(main_db_path)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    if not sys.platform.startswith("win"):
        try:
            os.chmod(mirror_dir, stat.S_IRWXU)  # 0o700
        except OSError:
            logger.debug("[mirror] could not chmod mirror dir", exc_info=True)

    for schema, tables in MIRROR_TABLES.items():
        if not _IDENT_RE.match(schema):
            raise ValueError(f"unsafe mirror schema name: {schema!r}")
        file_path = mirror_dir / f"{schema}.db"
        await db.execute(f'ATTACH DATABASE ? AS "{schema}"', (str(file_path),))
        await db.execute(f'PRAGMA "{schema}".journal_mode=WAL')
        await db.execute(f'PRAGMA "{schema}".synchronous=NORMAL')
        if not sys.platform.startswith("win"):
            try:
                os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            except OSError:
                logger.debug("[mirror] could not chmod %s", file_path, exc_info=True)

        await _ensure_schema_tables(db, schema, tables)

        await db.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}"._mirror_meta '
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        await db.execute(
            f'INSERT INTO "{schema}"._mirror_meta (key, value) VALUES (?, ?) '
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("snapshot_hash", SNAPSHOT_HASH),
        )

    await db.commit()
    logger.info(
        "[mirror] attached %d schema(s) under %s (snapshot %s)",
        len(MIRROR_TABLES),
        mirror_dir,
        SNAPSHOT_HASH[:12],
    )


async def _ensure_schema_tables(db: aiosqlite.Connection, schema: str, tables: dict) -> None:
    for name, spec in tables.items():
        await db.execute(spec["create_sql"])
        for idx_sql in spec["index_sql"]:
            await db.execute(idx_sql)

        cursor = await db.execute(f'PRAGMA "{schema}".table_info("{name}")')
        rows = await cursor.fetchall()
        local_cols = {r[1]: (r[2] or "").upper() for r in rows}
        expected = spec["columns"]

        for col, typ in expected.items():
            if col not in local_cols:
                logger.warning(
                    "[mirror] %s.%s missing column %s — adding (cloud schema moved ahead)",
                    schema, name, col,
                )
                await db.execute(f'ALTER TABLE "{schema}"."{name}" ADD COLUMN "{col}" {typ}')
            elif local_cols[col] != typ:
                logger.error(
                    "[mirror] DRIFT: %s.%s.%s has local type %s but snapshot says %s — "
                    "regenerate the mirror or migrate this column; sync for this column "
                    "may be lossy until fixed",
                    schema, name, col, local_cols[col], typ,
                )

        for col in local_cols:
            if col not in expected:
                logger.error(
                    "[mirror] DRIFT: %s.%s has extra local column %s not in the cloud "
                    "snapshot — the mirror was hand-edited or the snapshot is stale; "
                    "refresh schema_mirror/snapshot.json",
                    schema, name, col,
                )
