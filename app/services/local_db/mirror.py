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
- known retired column     -> preserved locally and ignored by sync
- unknown extra column     -> ERROR log (someone hand-edited the mirror)

Incompatible drift never crashes the engine — the mirror is a replica and the
app must keep working offline — but it must never be silently absorbed.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import sys
import time
from pathlib import Path

import aiosqlite

from app.common.system_logger import get_logger
from app.services.local_db.mirror_schema import (
    MIRROR_TABLES,
    RETIRED_MIRROR_COLUMNS,
    SNAPSHOT_HASH,
)

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

    # Two passes: attach + WAL pragmas for EVERY schema first, tables second.
    # journal_mode cannot change inside a transaction, and the DDL/INSERT
    # statements of an earlier schema open an implicit one — interleaving the
    # passes breaks the moment there is more than one mirrored schema.
    for schema in MIRROR_TABLES:
        if not _IDENT_RE.match(schema):
            raise ValueError(f"unsafe mirror schema name: {schema!r}")
        file_path = mirror_dir / f"{schema}.db"
        await _attach_schema_with_corruption_recovery(db, schema, file_path)
        await db.execute(f'PRAGMA "{schema}".journal_mode=WAL')
        await db.execute(f'PRAGMA "{schema}".synchronous=NORMAL')
        if not sys.platform.startswith("win"):
            try:
                os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            except OSError:
                logger.debug("[mirror] could not chmod %s", file_path, exc_info=True)

    retained_retired: list[str] = []
    for schema, tables in MIRROR_TABLES.items():
        retained_retired.extend(await _ensure_schema_tables(db, schema, tables))

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
    if retained_retired:
        logger.info(
            "[mirror] preserving %d retired local column(s), excluded from sync: %s",
            len(retained_retired),
            ", ".join(sorted(retained_retired)),
        )
    logger.info(
        "[mirror] attached %d schema(s) under %s (snapshot %s)",
        len(MIRROR_TABLES),
        mirror_dir,
        SNAPSHOT_HASH[:12],
    )


# SQLite messages that mean the FILE is unusable (not merely busy). Only these
# authorize the quarantine-and-rebuild below — a transient lock must never
# destroy a healthy mirror.
_CORRUPTION_MARKERS = ("malformed", "not a database", "file is encrypted")


def _is_corruption(exc: BaseException) -> bool:
    return any(marker in str(exc).lower() for marker in _CORRUPTION_MARKERS)


async def _attach_schema_with_corruption_recovery(
    db: aiosqlite.Connection, schema: str, file_path: Path
) -> None:
    """Attach one mirror schema; a corrupt file is quarantined and rebuilt.

    The mirror is a REPLICA of the cloud schemas (module docstring): losing
    the local file loses nothing durable — chat/notes sync re-pulls it. On
    2026-08-30 a corrupted ``chat.db`` ("database disk image is malformed")
    made every chat_sync tick crash forever with no recovery path. Now:
    ``quick_check`` probes each schema at attach time, and a corrupt file is
    moved aside (``<schema>.corrupt-<ts>.db``, kept for forensics) and
    recreated empty, loudly.
    """
    for attempt in (1, 2):
        try:
            await db.execute(f'ATTACH DATABASE ? AS "{schema}"', (str(file_path),))
        except (sqlite3.DatabaseError, aiosqlite.Error) as exc:
            if attempt == 2 or not _is_corruption(exc):
                raise
            _quarantine_mirror_file(schema, file_path, exc)
            continue

        try:
            cursor = await db.execute(f'PRAGMA "{schema}".quick_check(1)')
            row = await cursor.fetchone()
            verdict = str(row[0]) if row else "no result"
        except (sqlite3.DatabaseError, aiosqlite.Error) as exc:
            if attempt == 2 or not _is_corruption(exc):
                raise
            await _detach_quietly(db, schema)
            _quarantine_mirror_file(schema, file_path, exc)
            continue

        if verdict.lower() == "ok":
            return
        if attempt == 2:
            raise sqlite3.DatabaseError(
                f"mirror schema {schema!r} still fails quick_check after rebuild: {verdict}"
            )
        await _detach_quietly(db, schema)
        _quarantine_mirror_file(schema, file_path, sqlite3.DatabaseError(verdict))


async def _detach_quietly(db: aiosqlite.Connection, schema: str) -> None:
    try:
        await db.execute(f'DETACH DATABASE "{schema}"')
    except Exception:
        logger.debug("[mirror] detach of corrupt %s failed", schema, exc_info=True)


def _quarantine_mirror_file(schema: str, file_path: Path, exc: BaseException) -> None:
    """Move the corrupt DB (+ WAL/SHM sidecars) aside so a fresh one is built."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(file_path) + suffix)
        if not source.exists():
            continue
        target = file_path.with_name(f"{schema}.corrupt-{stamp}.db{suffix}")
        try:
            source.replace(target)
        except OSError:
            # Last resort: a corrupt file we cannot move is deleted — an
            # unreadable replica has no forensic value worth blocking boot for.
            logger.error(
                "[mirror] could not quarantine %s — deleting it", source, exc_info=True
            )
            source.unlink(missing_ok=True)
    logger.error(
        "[mirror] %s mirror was CORRUPT (%s) — quarantined to %s.corrupt-%s.db and "
        "rebuilding empty; sync will re-pull it from the cloud",
        schema,
        exc,
        schema,
        stamp,
    )


async def _ensure_schema_tables(
    db: aiosqlite.Connection, schema: str, tables: dict
) -> list[str]:
    retained_retired: list[str] = []
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

        retired = set(RETIRED_MIRROR_COLUMNS.get(schema, {}).get(name, ()))
        for col in local_cols:
            if col not in expected:
                if col in retired:
                    retained_retired.append(f"{schema}.{name}.{col}")
                else:
                    logger.error(
                        "[mirror] DRIFT: %s.%s has unknown local column %s not in "
                        "the cloud snapshot or retirement ledger — the mirror may "
                        "have been hand-edited; verify schema_mirror sources",
                        schema, name, col,
                    )
    return retained_retired
