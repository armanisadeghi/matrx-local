# Current App Errors

Last processed: **2026-07-09 12:26:31** (from Matrx Log export)

## How to use

1. Paste each new export into **Inbox (raw paste)** below (keep prior exports or replace — either is fine).
2. Ask the agent to: *dedupe CURRENT_ERRORS.md and update tasks*.
3. The agent will:
   - Move unique new signatures into **Unique errors**
   - Add a matching open task for each new signature
   - Leave resolved tasks checked; do not re-open them unless the error returns after resolution

Fingerprint rule: ignore timestamps, log channel (`[tauri]` / `[server]` / `[syslog]`), and traceback frame noise. Same message / root exception = same error.

---

## Inbox (raw paste)

<!-- Paste the next Matrx Log export below this line. -->

```
(paste next export here)
```

---

## Unique errors

| ID | First seen | Level | Signature |
|----|------------|-------|-----------|
| E001 | 2026-07-09 12:14:58 | WARN | `[engine] matrx-ai: server tool registry returned 0 tools — falling back to the local manifest` |
| E002 | 2026-07-09 12:15:05 | WARN | `[app.services.documents.file_manager] Permission denied listing folders in ~/Documents/Matrx/Notes` (`[Errno 1] Operation not permitted`) |
| E003 | 2026-07-09 12:15:05 | WARN | `[app.services.documents.file_manager] Corrupt sync state, resetting` |
| E004 | 2026-07-09 12:15:05 | WARN | `[app.services.documents.file_manager] Permission denied listing conflicts in ~/Documents/Matrx/Notes/.sync/conflicts` |
| E005 | 2026-07-09 12:15:05 | ERR | ASGI `PermissionError` renaming sync temp → `~/Documents/Matrx/Notes/.sync/state.json` (`[Errno 1] Operation not permitted`) |
| E006 | 2026-07-09 12:15:15 | WARN | `Device Permissions: warning — Not granted: Screen Recording` (Mic/Camera granted) |
| E007 | 2026-07-09 12:15:16 | WARN | `[downloads] CANCELLED: file=stabilityai--sdxl-turbo` |
| E008 | 2026-07-09 12:18:04 | WARN | `[app.services.scraper.retry_queue] RetryQueue: remote queue unreachable` — scraper `GET /api/scraper/queue/pending` returned `500` |
| E009 | 2026-07-09 12:18:04 | WARN | `[launcher] scraper_retry_queue → degraded — remote retry queue unreachable` (same 500 as E008) |

### Notes from first export

- E002–E005 are the same macOS Documents / Full Disk Access failure surfacing as list, conflict, corrupt-state, and ASGI write errors. Kept as separate IDs because they are distinct log signatures.
- E008/E009 are the same remote 500; E009 is the launcher state transition that follows E008.
- Duplicate channels (`[tauri]` vs `[server]` vs `[syslog]`) for the same event were collapsed.

---

## Tasks

- [ ] **T001** ← E001 — Investigate why matrx-ai server tool registry returns 0 tools and falls back to the local manifest
      _2026-07-09 triage: root cause is server-side — `GET https://server.app.matrxserver.com/api/ai-tools/app/matrx_local` → 404 (missing app registration or moved route in aidream). Needs an aidream-side fix; matrx-local now at least reports `tools` as degraded instead of ready (v1.3.92)._
- [ ] **T002** ← E002 — Fix / guide macOS permission for listing `~/Documents/Matrx/Notes` (Full Disk Access or Documents entitlement)
- [ ] **T006** ← E006 — Screen Recording permission denied — ensure Review & Grant path works / document when it’s required
- [ ] **T008** ← E008 — Fix or harden against remote scraper retry queue `500` on `/api/scraper/queue/pending`
      _2026-07-09 triage: client side is already hardened (exponential backoff + degraded state, v1.3.92 — E009 is that mechanism working). The persistent 500 itself is a server-side bug in the scraper service (aidream repo)._
- [ ] **T009** ← E009 — Confirm `scraper_retry_queue` degraded state recovers when remote queue is healthy again

---

## Resolved

<!-- Move checked tasks here with date resolved, e.g.:
- [x] **T000** ← E000 — … (resolved 2026-07-09)
-->

- [x] **T003** ← E003 — Permission-denied is no longer misdiagnosed as corrupt sync state: `load_sync_state` only logs "Corrupt sync state, resetting" on `json.JSONDecodeError`; PermissionError returns defaults quietly. (resolved 2026-07-09, ships v1.3.93)
- [x] **T004** ← E004 — All notes permission-denied paths (folders/notes/conflicts/mappings listing) route through a once-per-run access guard: ONE warning + `registry.degraded("notes_sync", …)` with the Full Disk Access hint, sync cycles skipped while degraded, auto-recovers to ready. No more WARN spam. (resolved 2026-07-09, ships v1.3.93)
- [x] **T005** ← E005 — `state.json` writes now raise a structured `NotesAccessError` handled by a global exception handler → clean 503 with actionable reason (no more unhandled ASGI PermissionError); `GET /notes/sync/status` exposes `notes_access_degraded` + reason for the UI. (resolved 2026-07-09, ships v1.3.93)
- [x] **T007** ← E007 — Cancelled download `stabilityai--sdxl-turbo` was a deliberate cancel issued during live diagnosis by the coding agent (dev session shares the ~/.matrx downloads DB with the packaged app). Not a bug. (resolved 2026-07-09)
