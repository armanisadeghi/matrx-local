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
in `client.py::CATALOG_COLUMNS`.

## The contract is a MINIMUM, never an exact set

**`CATALOG_COLUMNS` is the REQUIRED minimum. Extra columns are welcome and are
carried through verbatim.** This is not politeness — it is the difference
between an additive platform migration and a fleet outage. Every installed
desktop reads the SAME function the browser does, so if a new column were
treated as a shape violation, one `ALTER`-shaped change to `agx_get_list_full`
would take every shipped desktop's catalog offline for a change that adds
information and removes nothing. (The next one is real: a nullable
`orchestra jsonb` — the conductor badge, `{mode, tagline, depth_budget,
member_count, member_titles}` or null.)

| Situation | Behaviour |
|---|---|
| A REQUIRED column is missing | Loud refusal (`AgentCatalogError`), mirror kept. The function dropped or renamed part of the contract; that is a conversation, not a silent gap. |
| An EXTRA column arrives | Accepted. Stored in `raw_json` and served back byte-identically through `/agents/catalog` and `/agents/catalog/rpc`, so the desktop's structural client sees exactly what a browser sees — with no desktop release. |
| An extra column this build knows by name (`OPTIONAL_CATALOG_COLUMNS`) | Also gets a first-class nullable SQLite column so the mirror can query it. Today: `orchestra` (migration `_V33_…`). |
| An extra column this build does NOT know | Announced once per read with the remedy (add it to `OPTIONAL_CATALOG_COLUMNS` + a migration, in one change). Never absorbed silently. |

`AgentsRepo._to_catalog_row` replays `raw_json` — the row as the database
handed it over — because that is the ONLY reconstruction that is faithful for
a column this build has never heard of. Rebuilding from typed columns survives
only as a loudly-announced fallback.

## The pipeline

| Step | Where |
|---|---|
| Read the RPC (publishable key + user JWT, `Content-Profile: public`, Range-paged, `count=exact` verified) | `app/services/agent_catalog/client.py::fetch_agent_catalog` |
| Mirror the rows verbatim into SQLite `agents` (19 platform columns + `user_id` / `catalog_position` / `raw_json` / `synced_at`) | `app/services/local_db/schema.py::_V32_AGENTS_PLATFORM_CATALOG`, `repositories.py::AgentsRepo` |
| Refresh on startup + every 10 min | `app/services/local_db/sync_engine.py::sync_agents` |
| Serve identically, offline | `app/api/agent_catalog_routes.py` (`GET /agents/catalog`, `POST /agents/catalog/rpc`, `GET /agents/catalog/status`) |
| ONE agent's execution detail, lazily | `client.py::fetch_agent_execution` → `public.agx_get_execution_full(p_agent_id)`, cached in SQLite `agent_execution_details`, served verbatim by `GET /agents/catalog/{agent_id}/execution` |

`catalog_position` exists so the served list replays the database's own ORDER BY
instead of re-deriving an order locally — the builtins block has no `ORDER BY`
of its own, so only replay is faithful.

## What was wrong before 2026-09-08

The mirror's source was the aidream route `GET /agents`: its membership is
builtins + agents the caller created, so **every shared and org-shared agent was
structurally absent**, and its row carried 7 of the 19 columns. On top of that
the retired `GET /chat/agents` returned `"shared": []` as a hardcoded literal — a screen
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

## Retiring — DONE 2026-09-08

`GET /chat/agents`, `GET /data/agents` and `app/api/agent_legacy_shape.py` are
**deleted**. Their bucketed `{builtins, user, shared}` payload existed only to
feed the desktop's hand-rolled picker; the desktop now renders
`@ai-matrx/agents/catalog` and reads `POST /agents/catalog/rpc`, so the last
caller is gone. Do not reintroduce a bucketed list: buckets are a VIEW the
shared package computes from these rows, never a second server shape.

`variable_definitions` / `settings` survive as what they always were —
per-agent execution detail, never list columns — served ONE agent at a time by
`GET /agents/catalog/{agent_id}/execution`.

## Execution detail — the SECOND platform RPC, and the only other one

`public.agx_get_execution_full(p_agent_id)` returns
`{id, variable_definitions, model_id, settings, tools, custom_tools,
context_policies, auto_context_disabled, ui_gates}`. It is the SAME read Cloud
Chat makes in the browser, SECURITY DEFINER and gated on
`iam.has_access('agent', id, 'viewer')` or an active builtin — membership is
decided in the database exactly as it is online. The engine serves that row
VERBATIM, so `desktop/src/lib/agent-execution.ts` normalizes both lanes with
ONE mapper (`executionPayloadFromRow`). There is no projection here and there
must never be one: a projection is a second structure.

**Refresh policy — lazy, never a stampede.** The row is fetched on the first
`/execution` request for an agent and cached in SQLite for
`EXECUTION_CACHE_TTL_SECONDS` (600s). 468 agents are never 468 RPC calls at
startup: the catalog list is one call, and detail is one call for the one
agent a person actually opened. This is the fetch-through cache pattern
`agent_execution_definitions` already uses (`docs/SYNC_CONTRACT.md`).

| Situation | Behaviour |
|---|---|
| Cache fresh | Served, `X-Matrx-Agent-Detail-Stale: false`. |
| Cache stale/absent, RPC reachable | Refetched, stored, served. |
| Cache present, RPC unreachable or signed out | Cached row served with `X-Matrx-Agent-Detail-Stale: true` and a reason header — offline is the point, but a stale form never looks current. |
| Nothing cached, nothing fetchable | 404 / 503 with a reason and a remedy. An empty payload reads exactly like "this agent takes no variables", and a form that silently drops a required question is the failure this refuses. |
| The RPC returns zero rows | 404 `agent_not_visible`, and any stale cache for that agent is DROPPED — access was removed, so the detail goes with it. |

**What this replaced.** The detail cache used to be a hand-made
`{variable_defaults, settings}` projection of the aidream `GET /agents`
listing route, refreshed wholesale on every catalog sync. That route could
only see agents the caller CREATED, and it 400s outright
("Cannot name an organization for this request") for a user belonging to
several organizations with no default — which left EVERY variables form
silently empty. `AIDreamClient.fetch_agents` and the `prompt_builtins` table
are deleted; a characterization test refuses their return.

## Change log

- 2026-09-08 — **Superset tolerance** (`_V33`): the platform column list became
  a REQUIRED MINIMUM; extra columns round-trip verbatim, `orchestra` gained a
  first-class SQLite column. **Execution detail moved off the aidream
  `GET /agents` route** onto `agx_get_execution_full(p_agent_id)` over the same
  PostgREST lane, lazily with a 600s TTL; `prompt_builtins` and
  `AIDreamClient.fetch_agents` deleted.
- 2026-09-08 — Legacy retirement executed with the desktop's adoption of
  `@ai-matrx/agents/catalog`: `/chat/agents`, `/data/agents` and
  `agent_legacy_shape.py` deleted; `GET /agents/catalog/{agent_id}/execution`
  added as the ONE per-agent variables/settings door.
- 2026-09-08 — Created. Catalog source switched from the aidream `GET /agents`
  route to `agx_get_list_full()`; SQLite migration V32; `/agents/catalog`
  endpoints added; `shared: []` lie removed from both legacy endpoints.
