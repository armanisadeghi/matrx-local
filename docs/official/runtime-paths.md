# Runtime Paths

Port rules summary: [CLAUDE.md](../../CLAUDE.md) (22140, scan 22140–22159).

---

## Port discovery

| Priority | Mechanism |
|----------|-----------|
| 1 | `MATRX_PORT` (exact, no fallback) |
| 2 | Default 22140 |
| 3 | Scan 22140–22159 |

**Discovery file** `~/.matrx/local.json`:

```json
{
  "port": 22140,
  "host": "127.0.0.1",
  "url": "http://127.0.0.1:22140",
  "ws": "ws://127.0.0.1:22140/ws",
  "pid": 12345,
  "version": "0.3.0"
}
```

---

## Data persistence

| Data | Location |
|------|----------|
| Scrape cache | In-memory TTLCache; `DATABASE_URL` → PostgreSQL |
| Auth session | `localStorage` (Supabase) |
| App settings | `localStorage` (`lib/settings.ts`); cloud via settings sync |
| Theme | `use-theme.ts` + `matrx-theme` / `matrx-settings` |
| Logs | `system/logs/` (rotating) |
| Temp | `system/temp/` |
| TTS models | `~/.matrx/tts/` |
| Image / video weights | `~/.matrx/image-models/`, `~/.matrx/video-models/` |
| Image-gen packages | `~/.matrx/image-gen-packages/` |
| Local SQLite replica | `~/.matrx/matrx.db` |
| Playwright browsers | `~/.matrx/playwright-browsers` |
| Transcription / LLM config | App data dir (`transcription.json`, `llm.json`, `*.bin`, `*.gguf`) |
| Diagnostics | `~/.matrx/diagnostics/` |

Disk layout: [local-storage-architecture.md](../local-storage-architecture.md). Sync doctrine:
[SYNC_CONTRACT.md](../SYNC_CONTRACT.md).
