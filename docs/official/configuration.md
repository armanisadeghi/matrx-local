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
