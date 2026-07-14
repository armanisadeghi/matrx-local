# App Config — remote runtime configuration consumer

Non-secret runtime values (server URLs, feature flags, minimum supported
version, operator notice) come from ONE anon-readable Supabase row
(`public.app_config`, `app = 'matrx-local'`) — never from `.env` (developer-only)
and never hardcoded at consumer callsites. This module fetches that row,
caches it to disk, and serves the resolved values process-wide.

Cross-repo system-of-record (schema, DB, aidream endpoint, admin UI):
`/Users/armanisadeghi/code/common-docs/app-config/FEATURE.md` — read it before
touching this feature in ANY repo.

## Precedence chain (implemented exactly, in `service.py`)

1. **Env var dev override** — a `config.py` URL constant differing from its
   compiled `*_DEFAULT` (i.e. `AIDREAM_SERVER_URL_LIVE`, `MATRX_FILES_URL`,
   `SCRAPER_SERVER_URL` env vars). Wins per-key; logged LOUDLY at boot.
2. **Fresh remote** — fetched this session (PostgREST primary, aidream
   `GET /api/app-config/matrx-local` fallback; 5s timeouts, publishable key
   only — works pre-login).
3. **Last-good disk cache** — `MATRX_HOME_DIR / "app_config.json"` (dev world
   caches under `~/.matrx-dev` automatically), written atomically
   (temp file + `os.replace`) on every successful fetch.
4. **Compiled defaults** — the `*_DEFAULT` constants in `app/config.py`,
   mirrored in `service._COMPILED_DEFAULTS_ROW`.

## The pieces

| File | Role |
|---|---|
| `models.py` | `AppConfigV1` / `AppConfigRow` (tolerant of unknown keys, strict on known), `AppConfigNotice`, `ResolvedAppConfig` (+provenance tier), semver gate helpers, typed errors |
| `client.py` | `fetch_remote()` — PostgREST primary → aidream fallback; validation failure = fetch failure for that path |
| `service.py` | `AppConfigService` engine: boot resolution (sync, offline), atomic disk cache, 6h refresh loop + `refresh_now()`, launcher-registry reporting, singleton + accessors |
| `__init__.py` | Public surface: `get_aidream_server_url` / `get_matrx_files_url` / `get_scraper_server_url` / `get_flag` / `get_notice` / `get_app_config` |

## Wiring

- **Startup:** `app/main.py` Phase 00 (before every sync engine that consumes
  a URL): apply cache/defaults synchronously → registry `degraded(tier=…)` →
  `start_background()` (initial fetch + 6h loop). Teardown stops the loop in
  Phase S1.
- **Status:** `GET /health` carries `app_config: {tier, fetched_at,
  update_required}`; `GET /admin/status` reflects the registry record.
- **Manual refresh:** `POST /admin/refresh-config` (local-bootstrap auth, like
  the other `/admin/*` routes) → `refresh_now()` → provenance payload.
- **Consumers:** `aidream/client.py` (singleton rebuilt on URL change),
  `delegation/engine.py` (captured at engine construction),
  `file_sync/client.py` + `scraper/remote_client.py` (read per-request via
  property), `tools/tool_sync.py` (per-use). New code reads the accessors —
  never `config.AIDREAM_SERVER_URL` / `MATRX_FILES_URL` / `SCRAPER_SERVER_URL`
  directly.
- **Version gate:** `update_required` compares the running app version to the
  row's `min_supported_app_version` (tolerant semver tuple compare) — a STATE
  surfaced on `/health` and `/admin/refresh-config`, never an error.

## Invariants

- **Never blocks startup.** Boot resolution is one synchronous disk read; the
  network fetch is a background task. Total network failure still yields a
  working config (pinned by `tests/characterization/test_app_config_offline.py`).
- **Bootstrap constants are NEVER remotely configurable.** The Supabase URL +
  publishable key and the aidream fallback URL used to FETCH config are
  compiled-in; a hostile row cannot repoint them.
- **Invalid payload = fetch failure.** Loud warning, fall to the next tier,
  never a crash, never partially applied (`extra="ignore"` for unknown keys,
  strict types + https validation for known keys).
- **Cache lives under `MATRX_HOME_DIR`** and is written atomically — the dev
  world (`~/.matrx-dev`) and live world never share a cache.
- **Loud recovery.** Running on cache/defaults is `degraded` in the launcher
  registry with the tier in metadata; env dev overrides scream at boot; a
  failed cache write warns.
- **No secrets.** The row, the payload schema, and every code path here are
  public by definition.

## Tests

- `tests/unit/test_app_config.py` — precedence chain, tolerant/strict parsing,
  version gate, atomic cache round trip.
- `tests/characterization/test_app_config_offline.py` — total network failure
  still boots on compiled defaults.
- `tests/parity/test_app_config_live.py` — release validation: live row via
  both fetch paths parses against `AppConfigV1` (aidream path skips on 404
  until that endpoint deploys); compiled defaults parse against the same
  schema. Wired into `scripts/check.sh` Step 1.

## Change Log

- **2026-07-14 — Created.** Full consumer implementation per the approved
  cross-repo spec: module, Phase 00 wiring, `/health` + `/admin/refresh-config`
  surfaces, consumer migration off the raw config constants, tests + release
  validation.
