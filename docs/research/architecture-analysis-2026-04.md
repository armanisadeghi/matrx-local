# Architecture Analysis & Overhaul Plan

> **Historical audit** (2026-04-10). Layer-boundary findings — see also [FOUND_DEFECTS.md](../../FOUND_DEFECTS.md) for tracked items. Index: [ARCHITECTURE.md](../official/ARCHITECTURE.md).

> Generated 2026-04-10. Based on comprehensive analysis of the full codebase.

## Executive Summary

The Matrx Local codebase has strong foundational architecture — clean route→service→tool separation in Python, proper singleton patterns, sophisticated download management, and a thoughtful unified logging system. However, **systemic issues at the boundaries between layers** cause cascading failures and silent breakage:

1. **Silent error swallowing** at critical junctions (WebSocket send, React catch handlers, Python bare excepts)
2. **Duplicate state management** where Python and React independently track the same feature state
3. **Unmemoized React action objects** causing unnecessary re-renders and infinite loop risk
4. **Errors logged at wrong severity** making production issues invisible
5. **No centralized error→user notification bridge** on the frontend

These issues compound: an error occurs in Python, gets logged at DEBUG (invisible in production), the response fails to reach React via a silent WebSocket send failure, React's `.catch(() => {})` swallows the timeout, and the user sees a frozen UI with clean logs on both sides.

---

## Root Cause Analysis

### 1. The WebSocket Black Hole (CRITICAL)

**File:** `app/websocket_manager.py:189-193`

```python
async def _send(self, conn: Connection, data: dict) -> None:
    try:
        await conn.websocket.send_json(data)
    except Exception:
        pass  # ← Every failed WebSocket response vanishes silently
```

This is the single most damaging line in the codebase. When a tool executes successfully but the WebSocket send fails (connection dropped, buffer full, client disconnected), the result is silently discarded. The Python logs show success, the React side sees nothing, and the user gets a frozen spinner.

**Impact:** Affects ALL WebSocket-based tool execution (80+ tools).

### 2. React Silent Catch Handlers (HIGH)

**64 instances** of `.catch(() => {})` across 25 files. Key locations:

| File | Count | What's silenced |
|------|-------|-----------------|
| `hooks/use-tts.ts` | 7 | AudioContext failures, playback errors |
| `hooks/use-engine.ts` | 5 | Cloud sync, token refresh, heartbeat |
| `pages/LocalModels.tsx` | 7 | Hardware detection, model operations |
| `pages/Voice.tsx` | 5 | Audio device failures |
| `lib/api.ts` | 6 | Heartbeat, token refresh |
| `hooks/use-chat-tts.ts` | 2 | Audio playback failures |

### 3. Python Errors at Wrong Log Level (HIGH)

Errors logged at DEBUG that should be WARNING or ERROR:

| File | Line | What's hidden |
|------|------|---------------|
| `services/proxy/server.py` | 172 | Proxy connection failures |
| `tools/tools/contacts.py` | 124 | Data loss (skipped contacts) |
| `services/scraper/retry_queue.py` | 104, 171 | Network failures |

In production (LOG_LEVEL=INFO), these are completely invisible.

### 4. Unmemoized React Action Objects (HIGH)

Per CLAUDE.md, unstable action references have caused **production infinite loop bugs**. These hooks still return unmemoized actions:

| Hook | Line | Risk |
|------|------|------|
| `use-chat.ts` | 733-754 | Chat re-renders on every state change |
| `use-auth.ts` | 364-372 | Auth actions unstable |
| `use-engine.ts` | 441 | Engine object spread creates new ref |
| `use-notifications.ts` | 142-150 | Notification actions unstable |
| `use-agents.ts` | 110-120 | Agent actions unstable |
| `use-scrape.ts` | 566-577 | Scrape actions unstable |
| `use-tool-execution.ts` | 173-183 | Tool execution unstable |

### 5. Dual State Management

Several features maintain independent state in Python and React:

- **TTS:** Python tracks `_loaded_model_id` and synthesis status; React tracks `playbackState`, `selectedVoice`, `audioContext` state. No sync mechanism.
- **Transcription:** Rust manages actual recording; React maintains parallel `isRecording`, `isProcessingTail` with 15-second timeout band-aid.
- **Downloads:** Both Rust and Python have download managers; React merges events from both with no conflict resolution.

### 6. Scattered API Calls

Not all frontend HTTP calls go through the centralized `engine` API client:

- `use-chat.ts:291` — Direct fetch to `/chat/models`
- `use-agents.ts:76` — Direct fetch to `/chat/agents`
- `ChatPanel.tsx:50` — Direct fetch to `/chat/tools`
- Multiple pages — Inline `fetch()` without error handling

---

## Architecture Map

### Python Backend (Source of Truth)

```
run.py → app/main.py (FastAPI lifespan)
  │
  ├── REST Routes (app/api/*.py) ─────→ Services (app/services/*/) ─→ External APIs
  │     27 routers, ~80+ endpoints          Singleton pattern           Supabase
  │                                         get_*_service()             Scraper server
  │                                                                     Kokoro ONNX
  ├── WebSocket (/ws) ────────────────→ Tool Dispatcher ──────────→ Tool Handlers
  │     websocket_manager.py               dispatcher.py              tools/tools/*.py
  │     Per-connection ToolSession         80+ tools registered       File, Shell, Net...
  │
  ├── SSE Streams
  │     /downloads/stream  (download progress)
  │     /logs/stream       (system.log tail)
  │     /logs/access/stream (HTTP access log)
  │     /setup/logs        (structured startup log)
  │
  └── Logging
        common/system_logger.py  (singleton, file+console, sensitive data masking)
        common/access_log.py     (HTTP request/response logging)
```

### React Frontend (UI + State)

```
App.tsx
  ├── Providers (10 nested contexts)
  │     DownloadManager → TTS → LLM → WakeWord → TranscriptionSessions
  │     → Permissions → AudioDevices → Transcription → ...
  │
  ├── Hooks (28 custom hooks)
  │     use-engine.ts    — Engine discovery, lifecycle
  │     use-chat.ts      — Chat conversations, streaming
  │     use-tts.ts       — TTS synthesis, AudioContext
  │     use-chat-tts.ts  — Chat↔TTS bridge
  │     use-transcription.ts — Whisper recording
  │     ...
  │
  ├── API Layer
  │     lib/api.ts       — Main EngineAPI class (singleton)
  │     lib/tts/api.ts   — TTS-specific API
  │     lib/llm/api.ts   — LLM-specific API
  │
  └── Logging
        hooks/use-unified-log.ts  — Module-level singleton bus
        10 log sources, ring buffers, dedup, crash detection
```

### Rust/Tauri Layer (Process Management)

```
lib.rs
  ├── Sidecar Management  — Spawn/stop Python engine
  ├── LLM Server          — llama-server lifecycle (llm/*.rs)
  ├── Transcription        — Whisper.cpp integration (transcription/*.rs)
  ├── Downloads            — Rust-side download manager (downloads/*.rs)
  └── System               — Tray, updates, deep links, OAuth
```

### Key Principle: Rust doesn't proxy API calls

Frontend connects **directly** to Python on port 22140. Rust only manages process lifecycle, GPU-accelerated transcription, and LLM server orchestration.

---

## Overhaul Plan

### Phase 1: Stop Silent Failures (Critical)

Fix the error-swallowing patterns that make debugging impossible.

1. **WebSocket `_send()` must log failures** — Never silently discard responses
2. **Replace bare `.catch(() => {})` with error reporting** — Bridge to unified log
3. **Fix Python log severity levels** — Errors must be at ERROR/WARNING level
4. **Create centralized error reporting utilities** for both Python and React

### Phase 2: Stabilize React State (High)

Fix the patterns that cause cascading re-renders and stale state.

1. **Memoize all hook action objects** — Wrap in `useMemo`
2. **Route all API calls through centralized client** — No scattered `fetch()`

### Phase 3: API Documentation & Testing (Medium)

Ensure Python APIs are independently testable without the frontend.

1. **Document all API endpoints** with request/response schemas
2. **Add API integration tests** for critical paths (TTS, downloads, chat)
3. **Add health check endpoints** per subsystem

### Phase 4: Simplify State Ownership (Lower priority, future)

Reduce dual state management by making Python the single source of truth.

1. **Server-Sent Events for state push** — Python pushes state changes
2. **Reduce React state** to UI-only concerns (which tab is active, form inputs)
3. **Eliminate retry logic duplication** across layers

---

## Files Modified in This Overhaul

### Python
- `app/websocket_manager.py` — Fix silent `_send()`, add error logging
- `app/common/route_errors.py` — NEW: Centralized route error handler
- `app/api/*.py` — Apply error handler decorator to routes
- `app/services/proxy/server.py` — Fix log severity
- `app/tools/tools/network_discovery.py` — Fix silent failures
- `app/services/scraper/retry_queue.py` — Fix log severity
- `app/services/documents/file_manager.py` — Fix silent failure

### React/TypeScript
- `desktop/src/lib/error-reporting.ts` — NEW: Centralized error bridge
- `desktop/src/hooks/use-tts.ts` — Replace bare catches with error reporting
- `desktop/src/hooks/use-engine.ts` — Replace bare catches, memoize return
- `desktop/src/hooks/use-chat.ts` — Memoize actions
- `desktop/src/hooks/use-chat-tts.ts` — Replace bare catches
- `desktop/src/hooks/use-auth.ts` — Memoize actions
- `desktop/src/hooks/use-notifications.ts` — Memoize actions
- `desktop/src/hooks/use-agents.ts` — Memoize actions
- `desktop/src/hooks/use-scrape.ts` — Memoize actions
- `desktop/src/hooks/use-tool-execution.ts` — Memoize actions
- `desktop/src/hooks/use-transcription.ts` — Replace bare catches
- `desktop/src/pages/LocalModels.tsx` — Replace bare catches
- `desktop/src/lib/api.ts` — Replace bare catches
- `desktop/src/components/chat/ChatPanel.tsx` — Replace bare catches
