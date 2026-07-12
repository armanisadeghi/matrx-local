# Project Structure

```
matrx_local/
├── app/                            # Python engine
│   ├── main.py                     # FastAPI app, lifespan, CORS
│   ├── config.py                   # Pydantic Settings
│   ├── websocket_manager.py
│   ├── api/                        # Route modules (tools, auth, extension, admin, …)
│   ├── tools/
│   │   ├── dispatcher.py           # TOOL_HANDLERS routing
│   │   ├── catalog.py              # Canonical tool catalog
│   │   ├── tool_sync.py            # Drift vs cloud canon
│   │   └── tools/                  # Per-tool implementations
│   └── services/                   # scraper, sync, image/video gen, tts, tunnel, …
├── scraper-service/                # Git subtree — READ-ONLY (see CLAUDE.md)
├── desktop/                        # Tauri + React
│   ├── src/                        # App, pages, hooks, contexts, lib/
│   └── src-tauri/                  # Rust: lib.rs, transcription/, llm/
├── scripts/                        # build-sidecar.sh, update-scraper.sh, …
├── specs/                          # PyInstaller specs (4 platforms)
├── migrations/                     # Supabase SQL
├── docs/                           # Feature guides + docs/official/
├── run.py                          # Engine entry (port discovery, uvicorn)
└── pyproject.toml
```

Route and service detail: grep `app/api/` and `app/services/`. Tool rules: `app/tools/FEATURE.md`.
