# FEATURE — The platform agent catalog, mirrored for offline use

**The law first.** Arman, 2026-09-08 (ruling D4 in
`/Users/armanisadeghi/code/common-docs/projects/npm-package-extraction/AGENT-PICKER-DESIGN.md`):

> "the SQL light mirror is simply designed to give a user offline access, and so
> nothing should ever change … This is a bug that has been introduced over and
> over again by coding agents who take it upon themselves to decide that matrix
> local is going to be an exception. It cannot be an exception, and it will never
> be allowed to be an exception. All that happens is matrix local has an option
> to work locally. And when it works locally, the data is saved locally. But the
> structure, the format, and everything else must be absolutely identical."

**Offline is a data LOCATION. It is never a different list, structure, format,
sort, filter, or UI.** If you are about to add a field, drop a field, re-sort,
paginate differently, or "simplify" a row anywhere in this pipeline — stop. That
is the recurring defect this document exists to end.

## The one source

`public.agx_get_list_full()` — a SECURITY DEFINER Postgres function callable by
the authenticated role. It is the SAME catalog matrx-frontend, matrx-extend and
workflow-studio read. Membership and ordering are decided INSIDE the database:

- row set = the caller's own user agents + agents shared with them directly +
  agents shared with an organization they belong to + active builtins
  (`access_level = 'system'`); soft-deleted rows excluded;
- order = `is_favorite DESC, updated_at DESC, id` for the user rows, then the
  builtins block appended.

Its 19 columns (verified live 2026-09-08, project `brsgrqvjdzwihsvnfqkf`) are
pinned in `client.py::CATALOG_COLUMNS`; that tuple is the contract the mirror,
the served rows, and the shape test all read.

## The pipeline

| Step | Where |
|---|---|
| Read the RPC (publishable key + user JWT, `Content-Profile: public`, Range-paged, `count=exact` verified) | `app/services/agent_catalog/client.py::fetch_agent_catalog` |
| Mirror the rows verbatim into SQLite `agents` (19 platform columns + `user_id` / `catalog_position` / `raw_json` / `synced_at`) | `app/services/local_db/schema.py::_V32_AGENTS_PLATFORM_CATALOG`, `repositories.py::AgentsRepo` |
| Refresh on startup + every 10 min | `app/services/local_db/sync_engine.py::sync_agents` |
| Serve identically, offline | `app/api/agent_catalog_routes.py` (`GET /agents/catalog`, `POST /agents/catalog/rpc`, `GET /agents/catalog/status`) |

`catalog_position` exists so the served list replays the database's own ORDER BY
instead of re-deriving an order locally — the builtins block has no `ORDER BY`
of its own, so only replay is faithful.

## What was wrong before 2026-09-08

The mirror's source was the aidream route `GET /agents`: its membership is
builtins + agents the caller created, so **every shared and org-shared agent was
structurally absent**, and its row carried 7 of the 19 columns. On top of that
`GET /chat/agents` returned `"shared": []` as a hardcoded literal — a screen
telling the user something that could never be true. The desktop was therefore
reading a different catalog from every other Matrx client. Both are fixed;
`tests/parity/test_agent_catalog_contract.py` and the agent-catalog tests in
`tests/characterization/test_local_db_sync_characterization.py` fail if either
comes back.

## Failure posture — nothing silent

| Situation | Behaviour |
|---|---|
| No / expired stored JWT | Loud skip. Mirror kept. `sync_meta.status = skipped`. There is no anonymous refresh: the RPC is called AS the user. |
| RPC returns 401/403 | Loud error. Mirror kept — never a quiet downgrade to a membership this user may have lost. `status = error`. |
| Network failure | Loud error. Mirror kept. `status = offline`. |
| RPC returns zero rows | Loud error, mirror kept, `status = error`. Every signed-in user sees the active builtins, so zero rows is lost access, not an empty catalog. |
| Paging incomplete vs `count=exact` | Refused as a partial catalog. A short list is indistinguishable from lost access. |
| Mirror empty when served | `GET /agents/catalog` answers **503** with a stated reason and remedy, and starts a refresh. An empty array would be a lie. |
| Mirror present but last refresh failed | Rows are served (that is the point of offline) with `X-Matrx-Catalog-Sync-Status` / `X-Matrx-Catalog-Sync-Error` headers on every response, so a stale catalog can never look current. |

## Favourites — the one write, and why it is not queued here

The catalog's only write is `is_favorite` on `agent.definition`. It goes to
Supabase **directly, online**. There is deliberately **no offline write queue**:
a star that silently disappears on reconnect is worse than a refusal, so an
offline favourite toggle must refuse loudly and say why. Do not add a queue here
without a ruling — `sync_queue` is reserved for the conversation push pipeline.

## Retiring

`GET /chat/agents` and `GET /data/agents` still serve the legacy bucketed
`{builtins, user, shared}` shape (one implementation:
`app/api/agent_legacy_shape.py`), now derived from these same 19-column rows.
They die when the desktop adopts the shared agent-picker package
(`@ai-matrx/agents/catalog`) and reads `GET /agents/catalog` instead. Do not
grow them; new consumers use the catalog endpoint.

## Change log

- 2026-09-08 — Created. Catalog source switched from the aidream `GET /agents`
  route to `agx_get_list_full()`; SQLite migration V32; `/agents/catalog`
  endpoints added; `shared: []` lie removed from both legacy endpoints.
