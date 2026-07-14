# schema_mirror — canonical cloud schema snapshot

The cloud database (Supabase project `txzxabzwovsujtloxrus`) is the spec for
the local SQLite mirror. `snapshot.json` is a checked-in introspection dump of
the cloud schemas (`chat`, `workbench`, `ai`) that
`scripts/generate_mirror_schema.py` turns into
`app/services/local_db/mirror_schema.py` — the generated DDL the engine uses
to create and drift-check the local mirror tables.

**Never hand-edit `snapshot.json` or `mirror_schema.py`.** The whole point is
that drift between local and cloud is mechanically detectable.

## Refreshing the snapshot

When the cloud schema changes (or on suspicion of drift):

1. Run this against the live DB (Supabase MCP `execute_sql` or psql):

```sql
select table_schema, table_name, column_name, ordinal_position,
       data_type, udt_name, is_nullable, column_default
from information_schema.columns
where table_schema in ('chat','workbench','ai')
order by table_schema, table_name, ordinal_position;
```

   Also confirm which relations are views (`pg_views`) and the primary keys
   (`information_schema.table_constraints` / `key_column_usage`) — the
   snapshot stores `kind` and `pk` per relation.

2. Rebuild `snapshot.json` (same shape: `schemas.<schema>.<table>` with
   `kind`, `pk`, `columns[{name,udt,data_type,nullable,default}]`), bump
   `generated_at`.

3. `python scripts/generate_mirror_schema.py` and commit both files together.

CI/parity: `python scripts/generate_mirror_schema.py --check` fails when the
generated module is stale relative to the snapshot.

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
