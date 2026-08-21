# Local Storage Architecture

> **FLAGGED 2026-08-20 (centralization sweep, doc-migration row 33) — contradicts ratified
> doctrine.** This doc's "local first, cloud is an afterthought, sync is cloud→local" model
> directly conflicts with `docs/SYNC_CONTRACT.md`, which is cited by this repo's `CLAUDE.md` as
> **the ratified doctrine** ("Everything lives in the cloud. The local SQLite database … is a
> full FIRST-ACCESS REPLICA — not a competing server"). Not silently resolved — verify against
> live code (`app/services/local_db/`, `app/services/documents/`) before trusting either
> document; `SYNC_CONTRACT.md` is the higher-authority doc pending that verification.

> **Core principle: Local first. Always. The cloud is an afterthought.**
>
> This app exists to do things on the user's machine. If we wanted cloud storage,
> we'd stay in the cloud. Everything works offline. Sync happens when convenient,
> never when critical, never blocking, never causes a failure.

---

## The Golden Rules

1. **Every operation writes locally first.** If local succeeds, the operation succeeded. Full stop.
2. **Cloud sync never blocks, never fails the user.** Fire-and-forget. No connection? No problem. Queue it for later.
3. **Sync is almost always cloud → local**, not the other way. The user comes to this app to pull things down and work locally.
4. **When conflicts happen, save both copies.** Never auto-merge, never discard. Let the user decide.
5. **Never sync during heavy agent work.** When the agent is spinning through hundreds of thousands of tokens, the last thing we want is network traffic. Sync is a calm-time activity.
6. **Binary files sync to S3, not Supabase.** And never in real time — always on-demand or scheduled.

---

## Directory Structure

The user-visible root is called `Matrx` inside the OS-native documents folder.

### OS-native documents folder

| OS | Native documents folder | Our root |
|----|------------------------|----------|
| Windows | `C:\Users\<user>\Documents` | `C:\Users\<user>\Documents\Matrx\` |
| macOS | `/Users/<user>/Documents` | `/Users/<user>/Documents/Matrx/` |
| Linux | `~/Documents` (XDG) | `~/Documents/Matrx/` |

> **Why native Documents?** Users can find their files with File Explorer / Finder without knowing anything about our app. It's their data — they own it, it should be where they expect it.

```
~/Documents/Matrx/                     ← user-visible root (env: MATRX_USER_DIR)
  Notes/                               ← .md and .txt files (env: MATRX_NOTES_DIR)
    Work/
      project-plan.md
    Personal/
      ideas.md
  Files/                               ← binary files: PDF, DOCX, XLSX, PNG, MP3, MP4, etc. (env: MATRX_FILES_DIR)
    reports/
      q1-2026.pdf
    images/
      diagram.png
  Code/                                ← user's git repos (visible to user) (env: MATRX_CODE_DIR)
    my-project/
      ...
```

### Engine internals (hidden from user)

```
~/.matrx/                              ← engine config root (env: MATRX_HOME_DIR)
  local.json                           ← engine discovery (port, pid, url)
  settings.json                        ← engine settings
  instance.json                        ← device identity
  data/                                ← structured JSON: prompts, agent defs, tool configs
  workspaces/                          ← agent working copies of repos (never shown to user)
  .sync/                               ← sync state files, conflict queue
    state.json                         ← last-synced versions, hashes
    queue.json                         ← pending outbound sync items
    conflicts/                         ← conflict pairs (local + remote copy) awaiting user resolution
```

### Engine cache / temp (platform-appropriate, never synced)

```
Windows:  %LOCALAPPDATA%\MatrxLocal\cache\
macOS:    ~/Library/Caches/MatrxLocal/
Linux:    ~/.cache/matrx-local/
  screenshots/
  audio/
  code_saves/
  extracted/
```

---

## The 5 Storage Categories

### 1. Notes
**What:** `.md` and `.txt` files the user writes. Pure text, no binary.

**Local:** `~/Documents/Matrx/Notes/` — this is the source of truth.
**Cloud:** Supabase `notes` table — text content + metadata. A mirror, not the master.

**Sync behavior:**
- Write local immediately, always.
- Queue a background push to Supabase. If it fails, retry later. Never block.
- Pull from Supabase on user request, on timer, or on startup (not during agent work).
- Conflict = save two files (`note.md` and `note.conflict-TIMESTAMP.md`), notify user.

**Tables:** `notes`, `note_folders` (migration 001 — already applied)

---

### 2. Files (binary)
**What:** PDFs, Word docs, spreadsheets, images, audio, video. Anything binary.

**Local:** `~/Documents/Matrx/Files/` — source of truth.
**Cloud:** Amazon S3 — on-demand only, never real-time.

**Sync behavior:**
- All operations are purely local.
- User explicitly triggers upload to S3 (button, scheduled job).
- Download from S3 on user request only.
- No automatic sync, ever. These files can be gigabytes.
- Conflict = both copies kept, user resolves.

**No database tables for file content** — S3 object keys stored in a Supabase `user_files` manifest table (just metadata: name, size, s3_key, hash, last_modified).

---

### 3. Code / Agent Workspaces
**What:** Git repositories. Some the user owns and sees. Some are agent working copies (hidden).

**User repos:** `~/Documents/Matrx/Code/` — visible.
**Agent workspaces:** `~/.matrx/workspaces/` — hidden.

**Sync behavior:**
- **None from our side.** Git is the sync mechanism for code.
- We never touch `node_modules`, `.venv`, build artifacts.
- We never push code to S3 or Supabase.
- Agent workspaces are ephemeral — created for a task, deleted when done (or kept for inspection).

---

### 4. Structured Data / Config
**What:** Prompts, agent definitions, tool configs, workflow definitions, user preferences. Machine-readable JSON.

**Local:** `~/.matrx/data/` — working copy.
**Cloud:** Supabase JSONB tables — source of truth for this category (because it originates in the cloud).

**Sync behavior:**
- Cloud → local on startup and on demand. This is the primary direction.
- Local → cloud when user explicitly saves/publishes a config.
- Small enough to sync instantly as fire-and-forget when connected.
- A failed sync never blocks anything — local copy works fine offline.

---

### 5. Engine Internals
**What:** Discovery file, device identity, logs, screenshots, temp files, cache.

**Local:** `~/.matrx/` and platform cache dir.
**Cloud:** Nothing. Ever.

---

## What "Sync" Actually Means

Sync is **not** a real-time operation. It is:

1. **A background queue** — operations are logged locally and pushed when convenient.
2. **A user-triggered action** — "Sync Now" button that pushes/pulls on demand.
3. **A startup check** — pull latest from cloud when the engine starts (not during agent work).
4. **A scheduled job** — optional timer, user-configurable, defaults to off.

### Conflict resolution
- Never auto-merge.
- Never discard either version.
- Save both: `filename.md` (local) and `filename.conflict-20260302-143022.md` (remote).
- Show a conflict badge in the UI. User picks which to keep, or keeps both.

---

## What Needs to Change in the Code

### config.py
- Add `MATRX_USER_DIR` — OS-native Documents/Matrx path
- Add `MATRX_NOTES_DIR` — Notes subfolder (replaces `DOCUMENTS_BASE_DIR`)
- Add `MATRX_FILES_DIR` — Files subfolder (binary)
- Add `MATRX_CODE_DIR` — Code subfolder (user repos)
- Add `MATRX_WORKSPACES_DIR` — Agent workspaces (hidden, under `~/.matrx/`)
- Keep `MATRX_HOME_DIR` for engine internals

### document_routes.py (rename to notes_routes.py)
- All CRUD: write local first, then fire-and-forget Supabase sync
- List/read: always from local filesystem, never Supabase
- Supabase 404/500/timeout: log it, queue for retry, return success to caller
- Remove all code that blocks on Supabase

### New: sync_routes.py
- `POST /sync/notes/push` — push local notes to Supabase
- `POST /sync/notes/pull` — pull Supabase notes to local
- `GET /sync/status` — pending queue size, last sync time, conflicts
- `GET /sync/conflicts` — list conflict files
- `POST /sync/conflicts/{id}/resolve` — user picks a version

### New: files_routes.py
- `GET /files` — list local Files directory
- `POST /files/upload` — upload a file to S3 (user-triggered)
- `POST /files/download` — download a file from S3 (user-triggered)
- `GET /files/s3` — list files available in S3 manifest

---

## Naming Conventions Going Forward

| Old name | New name | Why |
|----------|----------|-----|
| `DOCUMENTS_BASE_DIR` | `MATRX_NOTES_DIR` | Documents = binary files to most people; Notes = text |
| `document_routes.py` | `notes_routes.py` | Matches what the data actually is |
| `DocumentFileManager` | `NotesFileManager` | Same reason |
| `/documents/*` endpoints | `/notes/*` endpoints | API clarity |
| `note_folders` (Supabase) | keep as-is | Already in DB, migration applied |

> The Supabase table is named `note_folders` which is actually correct. The confusion was naming the *local file manager* and *API routes* "documents" when they manage notes.

---

## Current State vs. Target State

| Component | Current | Target |
|-----------|---------|--------|
| `document_routes.py` | Supabase first, local as side-effect | Local first, Supabase as background queue |
| `file_manager.py` | Correct foundation | Keep, rename to `notes_file_manager.py` |
| `supabase_client.py` | Called on every CRUD operation | Only called from sync operations |
| Config paths | `DOCUMENTS_BASE_DIR` → `~/.matrx/documents` | `MATRX_NOTES_DIR` → `~/Documents/Matrx/Notes` |
| Binary files | Not implemented | `MATRX_FILES_DIR` → `~/Documents/Matrx/Files` + S3 sync |
| Agent workspaces | Not implemented | `~/.matrx/workspaces/` |
| Sync | Blocking, inline | Background queue, user-triggered, fire-and-forget |
