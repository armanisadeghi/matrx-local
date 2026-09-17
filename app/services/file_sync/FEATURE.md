# File sync — the desktop replica of the matrx-files cloud tree

The user's cloud files (matrx-files service, `files.matrxserver.com`) appear
under the Files root (`~/Documents/Matrx/Files`, path key `files`, tool alias
`@files/`) in one of two user-choosable modes, so the platform is their cloud
drive instead of Google Drive. Work order: `docs/handoffs/file-sync-system.md`. Cross-repo system-of-record: `/Users/armanisadeghi/code/common-docs/systems/media/file-service/STATE.md` — read it before touching this feature in ANY repo.
Doctrine: `docs/SYNC_CONTRACT.md` (this feature has a row in its matrix).

## Modes (`file_sync_mode` setting: `off | pointers | full`, default `pointers`)

- **pointers** — the full metadata tree is local (mirror + zero-byte
  placeholder files); bytes hydrate on demand. The agent sees one uniform
  filesystem: `tool_read`/`tool_copy`/`tool_move` call
  `hydration.ensure_hydrated` and fetch bytes transparently.
- **full** — pointer entries are steadily hydrated (bounded backfill per
  cycle) until every byte is local; offline-capable, bidirectional.
- **off** — the loop stays alive but idles; watcher stopped.

## The pieces

| Piece | File | Notes |
|---|---|---|
| Engine | `engine.py` | One `FileSyncEngine` singleton (`get_file_sync_engine`), engine-owned loop (Phase 2e in `app/main.py`), creds from the persisted `auth_tokens` row each tick. Cycle = pull folders → pull change feed → drain pushes → (full) hydration backfill. |
| Cloud client | `client.py` | `MatrxFilesClient` — the matrx-files REST API with the user JWT. matrx-files is the ONLY file backend; URLs come from its envelope, never hand-built. Server contract: `packages/matrx-files/matrx_files/api/router_files.py` (aidream) — `GET /files/sync/changes` (keyset cursor + tombstones) + `GET /files/sync/folders`. |
| Index | `index.py` | Cloud truth in the ATTACHed structural mirror (`files.files` / `files.folders`, spec = `schema_mirror/snapshot.json`); on-disk state in the `file_sync_state` sidecar (schema V11): `pointer | synced | pending_push | conflict`, `pending_op` (`upload|delete|move`), `last_synced_hash`. |
| Tool seam | `hydration.py` | `ensure_hydrated(abs_path)` — cheap outside the Files root; loud error string when a pointer can't hydrate (an empty placeholder must never read as content). |
| REST | `app/api/file_sync_routes.py` | `/file-sync/status·sync·hydrate·conflicts·conflicts/{id}/resolve·mode`. |

## Invariants (violating these re-opens shipped bug classes)

- **Bytes move through the DownloadManager only** (category `file_sync`),
  fed by freshly-minted URL envelopes (`GET /files/{id}/url`) — signed URLs
  expire, so mint right before enqueue and re-mint on retry.
- **Conflicts are never destructive.** Both-changed → local copy stays put,
  remote copy lands in `.sync/conflicts/<file_id>/`, state = `conflict`,
  user resolves (`keep_local | keep_remote`). Edit-vs-remote-delete → the
  edit survives as a fresh local file pending upload.
- **Local deletions are tombstones**: cloud soft-delete (`deleted_at`),
  local removals go to the OS trash (send2trash), never unlinked.
- **`last_synced_hash` is written only after a push landed or a pull wrote
  the bytes** — never optimistically (the notes-engine lesson, pinned).
- **Echo suppression is state-based, not time-based**: a watcher event whose
  content hash equals the mirror row's checksum is our own pull landing.
- **Path safety**: cloud `file_path` values are sanitized (`_clean_rel_path`);
  anything that would escape the Files root (or collide with `.sync/`) is
  index-only, disk untouched, ERROR-logged.
- **Placeholders are zero-byte**; a placeholder that gains content is a local
  edit. A 0-byte watcher event on a pointer is churn, ignored.
- **A cloud RECORD is not a feed ENTRY.** The feed says `file_id` / `version` /
  `folder_id`; `GET /files/{id}` says `id` / `current_version` /
  `parent_folder_id` for the same three fields. Every post-write echo converts
  with `index.record_to_feed_entry` — reading feed names off a record payload
  yields `file_id=None`, which the mirror refuses (guard:
  `tests/unit/test_file_sync_record_echo.py`, incl. a census of the echo sites).
- **THE PATH IS THE IDENTITY, so every push DECLARES its placement.** An
  undeclared matrx-files write is implicitly `alias_existing`: bytes the
  account already holds resolve to the canonical row and the requested path is
  recorded NOWHERE, while the response echoes that path back. Two copies of one
  document in two folders is ordinary, and before 2026-09-17 the second folder's
  file existed in the cloud only as the first one's while the index called it
  `synced`. Pushes now send `intent="force_new_copy"` with a reason naming the
  path (the row is linked to the canonical content by `duplicate_of_file_id` —
  the platform's "one content, many placements" primitive, aidream `453fd1adb`),
  and the mirror **believes the ROW, never the response**: it reads
  `GET /files/{id}` and, if that row is filed under a different path, refuses to
  rekey or finalize, names the other path in `file_sync_state.error`, and
  re-sends. `placement_attempts` bounds that at `_MAX_PLACEMENT_ATTEMPTS`
  rounds, after which it stops and says the local copy is intact; `counts()`
  reports them as `unplaced`, never as `synced`. Guard:
  `tests/unit/test_file_sync_placement.py`.
- **An entry the mirror cannot key is named ONCE per path**, with its remedy,
  and the repeats accrue in `unmirrorable_entry_state()` — never one ERROR per
  occurrence. A skipped entry is counted as `skipped`, never as `applied`.
- **Cursor** = the feed's opaque `next_cursor`, checkpointed in `sync_meta`
  entity `files.files`. Never parse it; never substitute a raw timestamp.
- System paths (`generations/`, `system-files/`) never sync — excluded
  server-side by default.

## Known limits (deliberate v1)

- Buffered uploads with a 512 MB ceiling — presigned direct-to-S3 uploads
  are the planned follow-up once the service serves them.
- Local rename detection is content-hash-based within watcher batches; an
  undetected rename degrades to delete+upload (cloud version history still
  preserves the old object).
- `tool_grep`/content search do not see non-hydrated pointer bytes.
