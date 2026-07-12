# Remote Scraper Server

Delegate scraping to `scraper.app.matrxserver.com` via engine `/remote-scraper/*` proxy ([communication-protocols.md](./communication-protocols.md)).

---

## Authentication

1. **API key** — `Authorization: Bearer <SCRAPER_API_KEY>` (dev / server-to-server)
2. **Supabase JWT** — validated via JWKS (`https://db.matrxserver.com/auth/v1/.well-known/jwks.json`, ES256)

Production ships without embedded scraper API keys; user JWT is the path.

---

## Local vs remote

| Mode | IP | Best for |
|------|-----|----------|
| Local | User residential IP | Sites blocking datacenters |
| Remote | Server + proxy rotation | Bulk / parallel jobs |

Scraping page exposes a real-time toggle for both.
