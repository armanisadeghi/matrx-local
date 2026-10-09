# schema_mirror — the cloud schema, from the ONE database description

The cloud database (Supabase project `brsgrqvjdzwihsvnfqkf`) is the spec for
the local SQLite mirror. `description/` holds byte copies of the ONE database description's
`ai`, `chat`, `files` and `workbench` schemas (aidream `db-contract/`) that
`scripts/generate_mirror_schema.py` turns into
`app/services/local_db/mirror_schema.py` — the generated DDL the engine uses
to create and drift-check the local mirror tables.

**Never hand-edit `description/` or `mirror_schema.py`.** The whole point is
that drift between local and cloud is mechanically detectable.

## Refreshing the description

matrx-local no longer introspects the cloud itself. The ONE database
description is emitted by `@ai-matrx/data` in aidream and read by every client
(TypeScript doors, Swift/Kotlin later, and this mirror):

1. In aidream: `node apps/shared/data/bin/matrx-data.mjs emit --out db-contract`
   (read-only against the live DB; `--check` fails on drift).
2. Here: `python scripts/refresh_mirror_snapshot.py` copies the mirrored schemas
   (`ai`, `chat`, `files`, `workbench`) into `description/` with `source.json`
   naming the contract hash. It refuses removed columns or changed table /
   primary-key identity on a mirrored table until the retirement impact is reviewed.
3. When the refresh removes a column that an older app already mirrored, add
   it to `retired_columns.json`. This is a non-destructive local upgrade ledger:
   the column remains on disk, is excluded from sync, and is no longer reported
   as unknown drift. Never remove an entry while supported installations may
   still carry that column.
4. `python scripts/generate_mirror_schema.py` and commit the description copies,
   retirement ledger (when changed), and generated module together.

`python scripts/refresh_mirror_snapshot.py --check` fails when the copies differ
from aidream's `db-contract/` (needs the sibling checkout).

CI/parity: `python scripts/generate_mirror_schema.py --check` fails when the
generated module is stale relative to the snapshot.

**Staleness of the SNAPSHOT itself** (the `--check` above cannot see it — it
only compares the module to the snapshot) is caught by
`python scripts/check_mirror_snapshot_drift.py`, which asks the live cloud for
one real row per mirrored relation and exits 1 listing every column the
snapshot has never heard of. `--self-test` proves the detector still works
without touching the network. `release.sh` runs both (self-test blocking,
live check loud and non-blocking). Why it exists: the snapshot sat at
2026-08-13 while the cloud grew `chat.request_snapshot.{pinned_at, pin_reason,
deleted_at, agent_definition_version, workflow_definition_version}` and
`deleted_at` on four more chat tables, and `chat_sync` dropped every one of
those values on every pulled row — 8,566 rows in 72 hours, announced only as
one WARNING per row in a log file (SR-10).

## Design rules (see also docs/SYNC_CONTRACT.md)

- Mirror tables live in per-schema SQLite files (`~/.matrx/mirror/<schema>.db`)
  ATTACHed under the schema name, so local SQL uses the canonical qualified
  names (`chat.conversation`, `chat.message`, …).
- Structural mirror only: same table/column names, SQLite-compatible types.
  Constraints are deliberately relaxed (only PK NOT NULL, no FKs, no
  defaults) — the cloud enforces integrity; a replica must never reject rows
  the cloud accepted.
- Views (`chat.conversation_summary`, `ai.model_*` views) are captured in the
  snapshot but not mirrored as tables in phase 1.
- `chat.coding_session` and `chat.coding_session_entry` are always excluded.
  They are owner-only raw provider ledgers that can contain commands, paths,
  file content, and secrets; shared-conversation access does not convey them.
  The coding-session local edge uses its dedicated forwarding outbox, never a
  generic structural mirror of these cloud tables.
