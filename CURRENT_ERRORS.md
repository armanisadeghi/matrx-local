# Current App Errors — Matrx Local

Last processed: **2026-07-09 12:26:31** (from Matrx Log export) · Last hygiene pass: 2026-07-12 (Inbox empty; T001/T002/T006/T008 routed out; only T009 remains open)

> **Error-dump inbox.** Arman pastes raw log exports (often hundreds of lines,
> hours after the fact — this is a shipped app, re-testing needs a new build)
> into **Inbox** below; a triaging agent reconciles EVERY line into a home,
> then clears the Inbox. This file must SHRINK on every triage pass — it is
> never an archive.
>
> **Triage contract** (`/task-hygiene errors`):
> 1. Fingerprint each error — ignore timestamps, log channel
>    (`[tauri]` / `[server]` / `[syslog]`), and traceback frame noise; same
>    message / root exception = same error. Dedupe against **Unique errors**.
> 2. Every NEW signature gets exactly one home: (a) **quick fix now** — and if
>    something is fixable RIGHT NOW, STOP and tell Arman directly what can be
>    resolved immediately; (b) a `FOUND_DEFECTS.md` entry; (c) a proposed
>    agent task (needs Arman approval); or (d) an ask-Arman item →
>    `.matrx/ARMAN_TASKS.md`. Errors owned by another repo (e.g. server 500s
>    from aidream) get filed in THAT repo, noted here by ID.
> 3. **Clear the Inbox** after triage — the table + IDs are the durable record.
> 4. Resolved rows: prune once a shipped build confirms the fix (or ~2 weeks).
>    An error that returns after resolution gets a NEW row referencing the
>    old ID — never silently re-open.

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

- [ ] **T009** ← E009 — Confirm `scraper_retry_queue` degraded state recovers when remote queue is healthy again (blocked on the aidream-side 500 fix, see T008)

---

## Resolved

<!-- Move checked tasks here with date resolved, e.g.:
- [x] **T000** ← E000 — … (resolved 2026-07-09)
-->

- [x] **T001** ← E001 — Server-side (aidream): `GET /api/ai-tools/app/matrx_local` 404 — filed in aidream `FOUND_DEFECTS.md` + already an ask in `.matrx/ARMAN_TASKS.md` ("Reconcile AIDream server ↔ matrx-ai tool registry"). Client already degrades honestly (v1.3.92). (routed 2026-07-12)
- [x] **T002** ← E002 — macOS Full Disk Access grant is an Arman action → added to `.matrx/ARMAN_TASKS.md` quick wins; degraded-state handling shipped in v1.3.93 (T004/T005). (routed 2026-07-12)
- [x] **T006** ← E006 — Screen Recording grant already an ask in `.matrx/ARMAN_TASKS.md`; the Review & Grant path was fixed 2026-07-11 (MXL-D-032). (routed 2026-07-12)
- [x] **T008** ← E008 — Server-side (aidream scraper service): persistent 500 on `/api/scraper/queue/pending` — filed in aidream `FOUND_DEFECTS.md`. Client hardening shipped v1.3.92. Local follow-up is T009. (routed 2026-07-12)
- [x] **T003** ← E003 — Permission-denied is no longer misdiagnosed as corrupt sync state: `load_sync_state` only logs "Corrupt sync state, resetting" on `json.JSONDecodeError`; PermissionError returns defaults quietly. (resolved 2026-07-09, ships v1.3.93)
- [x] **T004** ← E004 — All notes permission-denied paths (folders/notes/conflicts/mappings listing) route through a once-per-run access guard: ONE warning + `registry.degraded("notes_sync", …)` with the Full Disk Access hint, sync cycles skipped while degraded, auto-recovers to ready. No more WARN spam. (resolved 2026-07-09, ships v1.3.93)
- [x] **T005** ← E005 — `state.json` writes now raise a structured `NotesAccessError` handled by a global exception handler → clean 503 with actionable reason (no more unhandled ASGI PermissionError); `GET /notes/sync/status` exposes `notes_access_degraded` + reason for the UI. (resolved 2026-07-09, ships v1.3.93)
- [x] **T007** ← E007 — Cancelled download `stabilityai--sdxl-turbo` was a deliberate cancel issued during live diagnosis by the coding agent (dev session shares the ~/.matrx downloads DB with the packaged app). Not a bug. (resolved 2026-07-09)
