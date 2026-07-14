---
status: active
updated: 2026-07-13
repos: [matrx-local, aidream]
owner-context: user file sync — full local replica OR pointer-mapped virtual files; the Matrx alternative to Google Drive
---

# File sync system — handoff

## Arman's vision (verbatim intent)

"One of the most important roles the local machine plays is syncing all of
the users' files if they choose to have that feature turned on. Otherwise,
all of the files still need to be mapped and available so that the AI agent
acts as though they're physically here, but they don't need to physically be
stored here — we can have just pointers. But for people who have plenty of
drive space like I do, I will absolutely want to create a full sync. And the
point is that we want the user to use our file system for their full cloud
sync system, instead of using Google Drive or something else."

Two modes, user-choosable (global default + per-folder override is the
natural shape):
1. **Full sync** — bytes mirrored locally, bidirectional, offline-capable.
2. **Virtual mapping** — full metadata tree locally (paths, names, sizes,
   types), bytes fetched on demand; the agent sees one uniform filesystem.

## Where things stand (verified 2026-07-13)

- Nothing is built. The only artifact is a `"file_sync"` download-category
  enum value with zero code paths (`app/services/downloads/manager.py:46`,
  `app/services/local_db/schema.py:427`).
- Cloud side: the platform file service is **matrx-files**
  (`files.matrxserver.com` cutover, aidream handoff `matrx-files-cutover.md`;
  package `matrx-files` 0.1.3 on PyPI). Cloud tables: `files.files`,
  `files.file_versions`, `files.file_rag_jobs`. Private files route through
  the server (signed URLs); public files get durable CDN URLs (workspace-root
  CLAUDE.md § Files).
- Local building blocks that MUST be reused, not duplicated: the unified
  DownloadManager (queue, bandwidth, resume, actionable failures —
  `app/services/downloads/`), the notes sync engine's proven patterns
  (content-hash conflict handling, tombstones, `.sync/state.json` device
  identity, engine-owned background loop — `app/services/documents/sync_engine.py`),
  and the file watcher (watchfiles).

## Priority work queue

### 1. Contract first: the file index
Local mirror of the user's `files.files` tree (per the canonical-DB-mirror
handoff — this is one of its schemas) + a local root, e.g.
`~/Matrx/Files/…` mirroring the cloud folder tree. Every entry carries:
cloud id, path, size, content hash, visibility, and local state
(`synced | pointer | pending_push | conflict`). The index IS the virtual
mapping — mode 2 is "index only, bytes on demand".

### 2. Pull path (cloud → local)
Incremental (checkpoint on the cloud's version/updated_at), bytes via the
DownloadManager (it already does resume/bandwidth/failure attribution).
Private files need signed URLs from the server (or a brokered credential —
see the token-broker FEATURE doc in common-docs); never hand-construct URLs.
On-demand hydration for pointer mode: an agent Read/Copy of a pointer file
triggers a fetch-then-serve (tool layer integration in
`app/tools/tools/file_ops.py` + `app/tools/session.py` named path
`@matrx-files/`).

### 3. Push path (local → cloud)
Watcher on the local root (reuse the watchfiles pattern); local
create/modify/delete → upload/patch/tombstone through matrx-files APIs with
the user JWT. Conflicts: never destructive — both versions preserved,
`.sync/conflicts/` pattern from notes.

### 4. Modes, settings, UI
Setting: `file_sync_mode` (`off | pointers | full`) + per-folder overrides;
sync status page mirroring the notes Sync UI (pending / conflicts / last
sync); first-run prompt that explains the choice in plain language (gentle
prompt doctrine — no red errors, no jargon).

### 5. Sandbox bridge (after 1–3)
The cloud sandbox (matrx-sandbox) already integrates with aidream's
cloud-files bridge. Once local file sync exists, the same index makes
sandbox↔desktop file flow "just another remote" — do not build a separate
sandbox sync.

## Contracts

- matrx-files is the ONLY file backend — no S3/scraper side-channels.
- Private bytes go through server-issued URLs; public bytes are durable CDN
  URLs (never expiring links in stored data).
- DownloadManager is the ONLY byte-transfer path on desktop (no second
  downloader — the repo already had that incident, see
  DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md).

## Verification

Drill: enable full sync → cloud files appear under `~/Matrx/Files` →
airplane mode: agent reads/edits a synced file offline → reconnect →
cloud converges. Pointer mode: agent `Read` of a non-hydrated file fetches
and returns bytes transparently; `ListDirectory` shows the full tree either
way. Both modes exercised in tests with a seeded cloud fixture.
