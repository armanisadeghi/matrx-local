# Matrx Local — Architecture Index

> **Start with [CLAUDE.md](../../CLAUDE.md)** — rules, doc map, dev commands.  
> This index is for technical depth. Each link is one topic; not a copy of CLAUDE.md.

---

## System map (30-second scan)

| Layer | Role | Entry points |
|-------|------|--------------|
| **Tauri (Rust)** | Shell, tray, sidecar lifecycle, Whisper, wake word, llama-server | `desktop/src-tauri/src/lib.rs` |
| **React UI** | Dashboard, settings, feature pages | `desktop/src/App.tsx` → `pages/`, `hooks/` |
| **Python engine** | FastAPI on loopback, tools, scraping, sync, AI services | `run.py` → `app/main.py` → `app/tools/dispatcher.py` |

Default engine: `http://127.0.0.1:22140` (scan 22140–22159). Discovery: `~/.matrx/local.json`. Tool count: `app/tools/catalog.py` (parity test enforced).

---

## Core topics (`docs/official/`)

| Topic | Doc |
|-------|-----|
| Diagram, request flow, connection paths | [overview.md](./overview.md) |
| Repo layout | [project-structure.md](./project-structure.md) |
| Stack versions | [technology-stack.md](./technology-stack.md) |
| REST, WebSocket, remote-scraper proxy | [communication-protocols.md](./communication-protocols.md) |
| Sync subsystems | [sync-architecture.md](./sync-architecture.md) → [SYNC_CONTRACT.md](../SYNC_CONTRACT.md) |
| Tool catalog | [tools-overview.md](./tools-overview.md) → `app/tools/catalog.py` |
| Local scraper engine | [scraper-engine.md](./scraper-engine.md) |
| Remote scraper server | [remote-scraper.md](./remote-scraper.md) |
| OAuth, JWT, extension auth | [authentication.md](./authentication.md) |
| matrx-extend bridge | [extension-bridge.md](./extension-bridge.md) → [MATRX_EXTEND_CONNECTION.md](../MATRX_EXTEND_CONNECTION.md) |
| Tauri shell, packaging, tray, CSP | [desktop-tauri.md](./desktop-tauri.md) |
| **Lifecycle & ownership** | [lifecycle-ownership.md](./lifecycle-ownership.md) |
| Ports, `local.json`, data paths | [runtime-paths.md](./runtime-paths.md) |
| Dev, build, CI/CD | [development-and-build.md](./development-and-build.md) |
| Env vars, CORS | [configuration.md](./configuration.md) |
| App settings keys | [settings-catalog.md](./settings-catalog.md) |
| Settings audit / gaps | [settings-audit.md](./settings-audit.md) |
| CI/build lessons | [build-lessons.md](./build-lessons.md) |
| OS permissions (doc TBD) | Code: `app/services/permissions/`, `PermissionsProvider` |

---

## Feature guides (`docs/`)

| Guide | Covers |
|-------|--------|
| [whisper-transcription-integration.md](../whisper-transcription-integration.md) | Rust Whisper |
| [local-llm-inference-integration.md](../local-llm-inference-integration.md) | llama-server |
| [local-storage-architecture.md](../local-storage-architecture.md) | Local-first storage |
| [proxy-integration-guide.md](../proxy-integration-guide.md) · [proxy-testing-guide.md](../proxy-testing-guide.md) | Cloud-to-local proxy |
| [WEB_CLIENT_INTEGRATION.md](../WEB_CLIENT_INTEGRATION.md) | Web ↔ desktop engine |
| [activity-log.md](../activity-log.md) | HTTP logging + SSE |
| [react-migration-notes-api.md](../react-migration-notes-api.md) | `/documents/` → `/notes/` |
| [path-resolution-guide.md](../path-resolution-guide.md) | `@matrx/`, `@notes/` aliases in tool calls |
| [DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md](../DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md) | Download pipeline audit |

---

## Research / historical (`docs/research/`)

| Doc | Notes |
|-----|-------|
| [architecture-analysis-2026-04.md](../research/architecture-analysis-2026-04.md) | Layer-boundary audit (2026-04-10) |
| [tool-registry-migration.md](../research/tool-registry-migration.md) | Tool naming migration guide |
| [sd-server-spike.md](../research/sd-server-spike.md) | Image-gen stack spike |

Cloud API reference (AIDream, not local engine): [aidream-api-endpoints.md](../reference/aidream-api-endpoints.md)

---

## Verify / resolve

| Item | Note |
|------|------|
| **Tool count in prose** | Marketing says "~80"; catalog + `tests/parity/test_tool_count.py` enforce actual count. |
| **Extension E2E** | Engine smoke tests pass; manual browser steps in MATRX_EXTEND_CONNECTION.md. |
