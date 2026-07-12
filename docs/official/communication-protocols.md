# Communication Protocols

Base URL: `http://127.0.0.1:{port}` from `~/.matrx/local.json` (default 22140).

---

## REST — core

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/tools/list` | GET | List tools |
| `/tools/invoke` | POST | Invoke tool (stateless session) |
| `/notes/*` | CRUD | Local-first documents |
| `/settings/*` | GET/PUT | Engine settings |
| `/cloud-sync/*` | Various | Instance registration + settings sync |
| `/proxy/*` | Various | Local HTTP proxy |
| `/admin/status` | GET | Service registry (lifecycle) |
| `/admin/shutdown` | POST | Graceful shutdown signal |

**POST /tools/invoke** — body: `{ "tool": "Scrape", "input": { ... } }`. Response: `{ "type", "output", "image", "metadata" }`. Fresh session per request.

---

## REST — media & AI services

| Prefix | Purpose |
|--------|---------|
| `/image-gen/*` | Status, models, download, load, generate, install (SSE) |
| `/video-gen/*` | Job-based generation (202 + job_id), one job at a time |
| `/tts/*` | Voices, synthesize, synthesize-stream |
| `/wake-word/*` | Detection status |

Full route list: `app/api/*_routes.py`.

---

## WebSocket (`/ws`)

Persistent sessions; concurrent tool calls with cancellation.

```json
{ "id": "req-1", "tool": "Scrape", "input": { "urls": ["https://example.com"] } }
{ "id": "req-1", "action": "cancel" }
{ "action": "cancel_all" }
```

---

## Remote scraper proxy (`/remote-scraper/*`)

Frontend → Python engine → remote server. Keeps tokens server-side.

| Endpoint | Proxies to |
|----------|------------|
| `/remote-scraper/status` | `GET /api/v1/health` |
| `/remote-scraper/scrape` | `POST /api/v1/scrape` |
| `/remote-scraper/search` | `POST /api/v1/search` |
| `/remote-scraper/search-and-scrape` | `POST /api/v1/search-and-scrape` |
| `/remote-scraper/research` | `POST /api/v1/research` |

Auth: `SCRAPER_API_KEY` in dev; production uses user Supabase JWT. Details: [remote-scraper.md](./remote-scraper.md).

---

## Extension surface

`/extension/rpc`, `/extension/ws`, broadcast RPC — [extension-bridge.md](./extension-bridge.md).
