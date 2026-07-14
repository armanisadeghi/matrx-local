# Configuration

Env file locations: [CLAUDE.md](../../CLAUDE.md) (root `.env`, `desktop/.env`).

---

## Python engine (root `.env`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `API_KEY` | Yes | — | Engine access key (any value for local dev) |
| `SCRAPER_API_KEY` | No | `""` | Remote scraper Bearer token |
| `SCRAPER_SERVER_URL` | No | `https://scraper.app.matrxserver.com` | Remote scraper base |
| `DATABASE_URL` | No | `""` | Local PostgreSQL scrape cache (not remote server DB) |
| `BRAVE_API_KEY` | No | — | Search + Research tools |
| `DATACENTER_PROXIES` | No | — | Comma-separated |
| `RESIDENTIAL_PROXIES` | No | — | Comma-separated |
| `MATRX_PORT` | No | `22140` | Fixed port |
| `DEBUG` | No | `True` | Debug mode |
| `LOG_LEVEL` | No | `DEBUG` | Log level |
| `HF_TOKEN` | No | — | HuggingFace (gated models) |
| `SUPABASE_URL` | No | — | Enables JWKS for extension auth |
| `ALLOWED_ORIGINS` | No | (see CORS) | Comma-separated override |
| `MATRX_PORT_BASE` | No | `22140` live / `22240` dev | Base of the engine port scan range; the proxy port derives from it (`base + 40` → 22180 live / 22280 dev) |
| `MATRX_LIVE_ENGINE` | No | unset | Set `1` to run a source engine in the LIVE world (`~/.matrx`, 22140–22159) instead of auto-isolating as dev. Requires the installed app quit. Same as `./scripts/dev.sh --live` |
| `MATRX_INSTANCE_SALT` | No | unset (live) / `"dev"` (dev) | Salts the cloud instance id so a dev engine never collides with the live app's registered instance |
| `MATRX_HOME_DIR` | No | `~/.matrx` live / `~/.matrx-dev` dev | Override the engine home/discovery dir (e.g. a throwaway private home for isolated tests) |

---

## Dev/live isolation (MXL-D-043)

Dev and live are two parallel worlds that never touch — enforced in code
(`run.py` self-isolates whenever it is not the frozen sidecar), not by
discipline. Source-run engines, `pnpm dev`, and `pnpm tauri:dev` auto-select
the DEV world; only the installed/packaged app (or an explicit
`MATRX_LIVE_ENGINE=1` escape hatch) uses the LIVE world.

| | LIVE world | DEV world |
|---|---|---|
| Who lives here | Installed/packaged app only (`sys.frozen`) | Every source-run engine, `pnpm dev`, `pnpm tauri:dev` |
| Home / discovery | `~/.matrx` / `~/.matrx/local.json` | `~/.matrx-dev` / `~/.matrx-dev/local.json` |
| Engine ports (`MATRX_PORT_BASE`) | 22140–22159 | 22240–22259 |
| Proxy port (`base + 40`) | 22180 | 22280 |
| Cloud instance id | hardware hash | hardware hash salted `"dev"` |
| Orphan sweeps | normal | off (a dev engine never kills other processes) |

Full model and the testing-rung guidance live in the non-official
[TESTING_LADDER.md](../TESTING_LADDER.md).

---

## Desktop (`desktop/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_SUPABASE_URL` | Yes | Supabase project URL |
| `VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY` | Yes | Publishable key (RLS) |

---

## CORS

Default allowed origins:

- `https://aimatrx.com`, `https://www.aimatrx.com`
- `http://localhost:1420`, `3000`–`3002`, `5173` (and `127.0.0.1` variants)
- `tauri://localhost`

Override: `ALLOWED_ORIGINS` env var.

User-facing settings catalog: [settings-catalog.md](./settings-catalog.md). Audit: [settings-audit.md](./settings-audit.md).
