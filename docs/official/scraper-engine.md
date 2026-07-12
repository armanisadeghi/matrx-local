# Scraper Engine

Git subtree from `aidream` — **read-only** in this repo. Update: `./scripts/update-scraper.sh`. Source editable in aidream's `scraper-service`.

---

## Import isolation

Both repos have an `app/` package. `app/services/scraper/engine.py` aliases scraper's `app` as `scraper_app` via `sys.modules` — zero upstream edits.

---

## Multi-strategy fetching

Escalates on failure:

1. **httpx** — fast HTTP
2. **curl-cffi** — TLS fingerprint impersonation
3. **Playwright** — full headless browser (Turnstile, JS-heavy sites)

---

## Graceful degradation

| Resource | With | Without |
|----------|------|---------|
| `DATABASE_URL` | Persistent page cache | In-memory TTLCache |
| Playwright | Browser fallback | httpx + curl-cffi only |
| `BRAVE_API_KEY` | Search + Research | Search disabled; Scrape works |
| Proxies | Rotation on blocks | Direct only |

---

## Update workflow

```bash
./scripts/update-scraper.sh --local   # local aidream checkout
./scripts/update-scraper.sh           # GitHub
uv sync                               # if deps changed
```
