# Local Storage Layout — what lives where on disk

> **Scope: on-disk layout only.** Every question about *what syncs, in which direction, and
> who wins a conflict* is answered by [`SYNC_CONTRACT.md`](SYNC_CONTRACT.md) — the ratified,
> test-pinned doctrine this repo's `CLAUDE.md` cites. This document deliberately states no
> sync behaviour of its own.
>
> **Verified against live code 2026-08-22** (`app/config.py`, `app/preflight.py`,
> `app/services/documents/file_manager.py`, `app/services/local_db/mirror.py`,
> `app/services/cloud_sync/instance_manager.py`).

## The doctrine this document used to contradict

This file was written as a pre-doctrine design proposal and led with *"Local first. Always.
The cloud is an afterthought… sync is almost always cloud → local."* **That model is dead and
was never built.** The ratified doctrine is the opposite ownership call, and the code
implements it: everything lives in the cloud, the local SQLite database and local files are a
full FIRST-ACCESS REPLICA, and the notes / chat / settings / user-files subsystems all sync
**bidirectionally and automatically**. Arman's desktop vision says the same thing —
*"NOTHING should be set up to just be local on the user's machine. All settings and configs
must go to the web and be controllable from the web as well"*
(`common-docs/systems/clients/desktop/VISION.md`). "Local-first" in that vision names the
*experience* — instant reads, full offline writes — not data ownership.

## User-visible root

The root is `Matrx` inside the OS-native documents folder (`config._os_documents_dir()`).

| OS | Native documents folder | Our root |
|----|------------------------|----------|
| Windows | `%USERPROFILE%\Documents` | `…\Documents\Matrx\` |
| macOS | `~/Documents` | `~/Documents/Matrx/` |
| Linux | `$XDG_DOCUMENTS_DIR`, else `~/Documents` | `~/Documents/Matrx/` |

> **Why native Documents?** Users can find their files in Finder / File Explorer without
> knowing anything about our app. It's their data — it should be where they expect it.

```
~/Documents/Matrx/            ← MATRX_USER_DIR
  Notes/                      ← MATRX_NOTES_DIR — .md and .txt
    .sync/                    ← sync state for the notes subsystem, INSIDE the notes dir
      state.json              ← device_id, last_pull_at, note_hashes
      mappings.json
      conflicts/<note_id>/    ← {local,remote}.md pairs awaiting user resolution
  Files/                      ← MATRX_FILES_DIR — binary files (matrx-files replica)
  Code/                       ← MATRX_CODE_DIR — the user's own git repos
```

Every path is overridable by the identically-named environment variable.

## Engine internals (hidden from the user)

```
~/.matrx/                     ← MATRX_HOME_DIR
  local.json                  ← engine discovery: port, pid, url, tunnel (app/preflight.py)
  settings.json               ← engine settings blob (synced; see SYNC_CONTRACT)
  instance.json               ← device identity (instance_id)
  matrx.db                    ← the local SQLite replica
  mirror/                     ← per-schema canonical mirror DBs, e.g. mirror/chat.db
  data/                       ← MATRX_DATA_DIR — prompts, agent defs, tool configs
  workspaces/                 ← MATRX_WORKSPACES_DIR — agent working copies of repos
```

## Cache / temp (platform-appropriate, never synced)

`config._platform_cache_dir()`:

```
Windows:  %LOCALAPPDATA%\MatrxLocal\
macOS:    ~/Library/Caches/MatrxLocal/
Linux:    ~/.cache/matrx-local/   (XDG)
  code_saves/  screenshots/  audio/  extracted/
```

## The storage categories, and who owns their sync

| Category | On disk | Sync owner (see SYNC_CONTRACT for behaviour) |
|---|---|---|
| Notes | `MATRX_NOTES_DIR` + SQLite `notes` | `app/services/documents/` ↔ cloud `workbench.notes` |
| User files (binary) | `MATRX_FILES_DIR` + mirrored `files.files` | `app/services/file_sync/` ↔ the matrx-files service |
| Chat | `~/.matrx/mirror/chat.db` | `app/services/chat_sync/` ↔ cloud `chat.*` |
| Code / agent workspaces | `MATRX_CODE_DIR`, `~/.matrx/workspaces/` | **None — git is the sync mechanism.** Never pushed to cloud storage by us |
| Structured data / config | `~/.matrx/data/`, `settings.json` | `app/services/cloud_sync/` + the pull-only catalog engine |
| Engine internals & cache | `~/.matrx/*`, platform cache dir | **Never synced** |

**There is no S3 path for user files.** The earlier plan to push binaries to S3 behind a
Supabase `user_files` manifest was never built and is superseded by the matrx-files replica
(mode-gated `off | pointers | full`, automatic, watcher-driven). No `boto3` user-file writer
exists in `app/` outside the vendored `z_from_matrx` audio package.

## Naming: partially applied, and that is the current state

The directory plan above shipped. The accompanying rename did **not**, and the old names are
still the live ones — do not document them as renamed:

| Planned | Actual today |
|---|---|
| `notes_routes.py` | `app/api/document_routes.py` |
| `NotesFileManager` / `notes_file_manager.py` | `app/services/documents/file_manager.py` |
| `/notes/*` endpoints | `/documents/*` and `/notes/*` both live in `document_routes.py` |
| `DOCUMENTS_BASE_DIR` retired | still exported as a deprecated alias of `MATRX_NOTES_DIR` |

# Changelog

- 2026-08-22 — **Rewritten (dedupe-and-verify).** The "local first, the cloud is an
  afterthought, sync is cloud → local" doctrine and the entire "What needs to change in the
  code" plan were **deleted**, not flagged: they contradicted `SYNC_CONTRACT.md` and the
  desktop VISION, and live code implements the opposite. What survived is the disk layout,
  re-verified line by line. Corrections made in the process: `.sync/` lives under the **notes
  dir**, not `~/.matrx/`; `~/.matrx/mirror/` and `matrx.db` were missing entirely; the notes
  cloud table is `workbench.notes`, not `notes`; binaries go to the matrx-files service, not
  S3; sync is automatic (600 s loop + watcher + realtime), not "scheduled, defaults to off".
