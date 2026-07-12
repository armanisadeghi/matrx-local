# Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| **Desktop shell** | Tauri v2 (Rust) | 2.x |
| **Frontend** | React + TypeScript + Vite | React 19, TS 7.0 (native), Vite 6 |
| **Styling** | Tailwind CSS + shadcn/ui | TW 3.4, `darkMode: "class"` |
| **Python** | Python + FastAPI + Uvicorn | 3.13+ |
| **Auth** | Supabase Auth | OAuth + email |
| **Local DB** | PostgreSQL (optional scrape cache) | asyncpg |
| **Remote scraper** | REST at scraper.app.matrxserver.com | httpx |
| **Scraping** | httpx, curl-cffi, Playwright, BeautifulSoup, PyMuPDF | See pyproject.toml |
| **Search** | Brave Search API | Optional |
| **Local LLM** | llama-server (llama.cpp) | Rust-owned sidecar |
| **Image / video gen** | Diffusers (on-demand install) | torch + diffusers ≥ 0.39 |
| **TTS** | Kokoro ONNX | Core dep |
| **Wake word** | openWakeWord | Core dep |
| **Package managers** | pnpm 10.x, uv | — |

Not a Next.js project. Dependency rules: [CLAUDE.md](../../CLAUDE.md).
