# Matrx Local

Companion desktop app for **AI Matrx** — Tauri shell + React UI + Python/FastAPI engine on loopback. Exposes tools (filesystem, shell, scraping, local AI) via REST and WebSocket for cloud agents and the Chrome extension.

**Agents:** start at [CLAUDE.md](CLAUDE.md) → [docs/official/ARCHITECTURE.md](docs/official/ARCHITECTURE.md).  
**Humans:** quick start below; build/ship details in [development-and-build.md](docs/official/development-and-build.md).

---

## Quick start

```bash
cp .env.example .env          # set API_KEY (any value for local dev)
uv sync
uv run playwright install chromium

# Terminal 1 — engine (wait for Uvicorn on :22140)
uv run python run.py

# Terminal 2 — UI
cd desktop && pnpm install && pnpm dev    # http://localhost:1420

# Full desktop (build sidecar first: ./scripts/build-sidecar.sh)
cd desktop && pnpm tauri:dev
```

Health check: `curl http://127.0.0.1:22140/` · Swagger: `http://127.0.0.1:22140/docs`

---

## Why local?

Residential IP for scraping, direct filesystem/shell access, native OS integration (clipboard, notifications, automation), no datacenter proxy costs.

Tool catalog (authoritative): `uv run python -m app.tools.tool_sync list` · Overview: [tools-overview.md](docs/official/tools-overview.md)

---

## License

Proprietary — internal use only.
