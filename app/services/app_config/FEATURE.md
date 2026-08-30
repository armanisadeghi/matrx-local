# App Config — remote runtime configuration consumer

Non-secret runtime values (server URLs, public web origin, feature flags, minimum supported
version, operator notice) come from ONE anon-readable Supabase row
(`public.app_config`, `app = 'matrx-local'`) — never from `.env` (developer-only)
and never hardcoded at consumer callsites. This module fetches that row,
caches it to disk, and serves the resolved values process-wide.

Cross-repo system-of-record (schema, DB, aidream endpoint, admin UI):
`/Users/armanisadeghi/code/common-docs/systems/platform/app-config/FEATURE.md` — read it before
touching this feature in ANY repo.

Sibling system: **Remote Catalogs** ([app/services/catalogs/FEATURE.md](../catalogs/FEATURE.md))
carries the *catalog-shaped* data (models, LoRAs, voices, presets, prompts)
with the same precedence/cache/refresh architecture — it imports this
module's semver helpers; keep `app_config` itself deliberately small.

## Precedence chain (implemented exactly, in `service.py`)

1. **Env var dev override** — a `config.py` URL constant differing from its
   compiled `*_DEFAULT` (i.e. developer-only `AIDREAM_SERVER_URL_LIVE`,
   `MATRX_FILES_URL`, `SCRAPER_SERVER_URL` env vars). Wins per-key; logged
   LOUDLY at boot.
2. **Fresh remote** — fetched this session (PostgREST primary with the
   publishable key AND `Accept-Profile: public` — the server's default
   exposed schema is no longer `public`, so an unprofiled request 404s with
   PGRST205 (see `SUPABASE_PROFILE_HEADERS` in `app/config.py`); aidream
   `GET /api/app-config/matrx-local` fallback; 5s timeouts — works
   pre-login).
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
| `__init__.py` | Public surface: `get_aidream_server_url` / `get_matrx_files_url` / `get_scraper_server_url` / `get_web_app_origin` / `get_flag` / `get_notice` / `get_app_config` |

`config.coding_session_runtime` is the typed, public control plane for local
Claude execution: admission concurrency, execution/idle/identity/mirror/
interrupt/shutdown timeouts, mirror retry timing, and replay/history bounds.
Every field has a validated compiled fallback in `service.py`; a missing key in
an older remote/cache row uses that fallback, while an invalid known value
rejects the row atomically. Coding Sessions capability/status APIs surface the
effective values with per-field provenance, so a field supplied by remote or
cache is distinguishable from a compiled fallback inside a partial/older row.

## Wiring

- **Startup:** `app/main.py` Phase 00 (before every sync engine that consumes
  a URL): apply cache/defaults synchronously → registry `degraded(tier=…)` →
  `start_background()` (initial fetch + 6h loop). Teardown stops the loop in
  Phase S1.
- **Status:** `GET /health` carries `app_config: {tier, fetched_at,
  update_required, notice, env_overrides}` — `env_overrides` lists the key
  names an active dev override is masking (tier stays remote/cache/defaults;
  the `"env"` Tier literal is reserved since overrides are per-key);
  `GET /admin/status` reflects the registry record.
- **Manual refresh:** `POST /admin/refresh-config` (local-bootstrap auth, like
  the other `/admin/*` routes) → `refresh_now()` → provenance payload.
- **Consumers:** `aidream/client.py` (singleton rebuilt on URL change),
  `delegation/engine.py` (captured at engine construction),
  `ai/engine.py` (`initialize_matrx_ai()` captures the aidream URL once —
  remote changes apply on restart, same posture as delegation),
  `file_sync/client.py` + `scraper/remote_client.py` (read per-request via
  property), `tools/tool_sync.py` (per-use). New code reads the accessors —
  never `config.AIDREAM_SERVER_URL` / `MATRX_FILES_URL` / `SCRAPER_SERVER_URL`
  directly.
- **Version gate:** `update_required` compares the running app version to the
  row's `min_supported_app_version` (tolerant semver tuple compare) — a STATE
  surfaced on `/health` and `/admin/refresh-config`, never an error.
- **Desktop UI:** `desktop/src/lib/app-config.ts` reads and caches the public
  row in webview local storage before refreshing it in the background; its
  AIDream and web-origin consumers never read environment variables.
  `desktop/src/hooks/use-app-config-status.ts` polls
  `/health` (60s, engine-connected only) and `AppConfigBanner`
  (`desktop/src/components/AppConfigBanner.tsx`, mounted in `AppLayout`)
  renders (a) a persistent non-blocking update-required strip wired into the
  existing auto-update flow (`use-auto-update`), and (b) the operator
  `notice` — shown once per content (hash of level|title|body persisted in
  localStorage), styled by level, with optional "Learn more" link.

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
- **Version gate fails open.** An app version that resolves to the `"0.0.0"`
  probe fallback (or any unparseable version) means "cannot gate":
  `update_required=False` + loud warning — a broken probe must never brand a
  healthy install with a permanent false "Update required" banner.
- **Refresh knob is defensive.** `MATRX_APP_CONFIG_REFRESH_INTERVAL` parse
  failures fall back to 6h with a loud warning, and values below 60s are
  clamped (a 0 would hot-loop against Supabase).

## Tests

- `tests/unit/test_app_config.py` — precedence chain, tolerant/strict parsing,
  version gate, atomic cache round trip.
- `tests/characterization/test_app_config_offline.py` — total network failure
  still boots on compiled defaults.
- `tests/parity/test_app_config_live.py` — release validation: live row via
  both fetch paths parses against `AppConfigV1` (aidream path skips on 404
  until that endpoint deploys); compiled defaults parse against the same
  schema. Marked `network` and gated behind `MATRX_LIVE_CHECKS=1` so plain
  offline `pytest tests/` skips it; `scripts/check.sh` Step 1 sets the env
  var so the release gate still runs it.

## Change Log

- **2026-07-17 — Desktop resolver parity.** The webview now resolves the
  same public config row with a validated last-good local cache; the AIDream
  URL and public web origin no longer come from renderer build variables or
  hard-coded consumer values.
- **2026-07-14 — Adversarial-review hardening.** Non-object JSON cache no
  longer crashes boot (falls to defaults); `ai/engine.py` migrated onto
  `get_aidream_server_url()` (restart-applies); `/health` reports
  `env_overrides`; live parity test gated behind `MATRX_LIVE_CHECKS=1`;
  version gate fails open on the `"0.0.0"` probe fallback; refresh-interval
  knob parse-hardened + clamped to a 60s floor.
- **2026-07-14 — Desktop UI surface.** `/health` `app_config` now includes
  `notice`; added `use-app-config-status` polling hook + `AppConfigBanner`
  (update-required strip + once-per-content operator notice) in the Tauri UI.
- **2026-07-14 — Created.** Full consumer implementation per the approved
  cross-repo spec: module, Phase 00 wiring, `/health` + `/admin/refresh-config`
  surfaces, consumer migration off the raw config constants, tests + release
  validation.
