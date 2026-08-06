# Access Health — the canonical filesystem access authority

**One authority.** This package is the ONLY place in the engine that holds
filesystem access-health state. The predecessor was a global boolean
(`notes_access_guard` in `documents/file_manager.py`, deleted 2026-07) that
any `EACCES` anywhere poisoned, any success anywhere cleared, and that
translated every macOS permission error into a definitive "grant Full Disk
Access" claim. Do not reintroduce any global access flag, anywhere.

## The model

- **Resource-scoped.** Each watched directory root is a registered resource
  (`notes-canonical`, `notes-mapping:<folder_id>:<sha8>`, `files-replica`,
  `path:<name>`). An observation mutates only its own resource.
- **Capability-scoped.** `enumerate / read / create / write / replace /
  delete` are tracked separately; status is degraded iff any capability's
  latest observation failed. A WRITE success never clears an ENUMERATE
  failure.
- **Evidence ≠ diagnosis.** Observations record what actually happened
  (path, op, errno, source, timestamp). The message shown to users is built
  from evidence plus `diagnose_fda()`, which only asserts "Full Disk Access
  is not granted" when a probe of FDA-protected locations (Messages/Mail/
  Safari, from the ENGINE process) positively established the denial.
  `granted` evidence *exonerates* FDA; `indeterminate` may only hedge.
- **Generation-fenced.** `recheck()`/`reset()` bump the generation; an
  in-flight op that started earlier can no longer flip status (it stays in
  `recent` as evidence). This prevents a slow failing op from re-poisoning
  state the user just fixed and verified.
- **In-memory only.** Startup probes rebuild the truth in milliseconds;
  persisted health would lie the moment a Privacy toggle changes.

## How to integrate a new code path

- Wrap real filesystem ops in `observing(resource_id, capability, op=...,
  source=...)` — success records ok; `EPERM/EACCES` records failure and
  raises `AccessDeniedError` (→ clean 503 via the handler in `app/main.py`);
  all other exceptions pass through untouched (a missing file is NOT access
  evidence).
- For best-effort paths that must not raise, use `record(...)` directly.
- Generic tools feed `note_external_io(path, capability, exc=...)` — a no-op
  for paths outside registered roots. This is why active agent I/O and the
  health banner can never contradict each other again.
- Gate periodic work with `should_attempt(resource_id)` (once-per-60s probe
  window while degraded).
- React to recovery with `on_transition(resource_id, cb)` — sync callbacks
  run inline; coroutines are scheduled on the loop captured at startup
  (`capture_loop()` in lifespan Phase 2c).
- Mirror a resource into the launcher registry by passing
  `registry_service="..."` at registration (notes-canonical → `notes_sync`).

## HTTP surface (`app/api/access_routes.py`)

- `GET /access/health` — cached evidence snapshot (no I/O).
- `POST /access/recheck {resource_ids?, create_missing?}` — capability probe
  (disposable dot-prefixed probe file: enumerate → create → write+fsync →
  atomic replace → delete; removed in `finally`; never touches user
  content). Cloud-synced mapped dirs may briefly observe the probe file.
- `POST /access/reset` — clear all evidence, bump generation, re-probe.
  Every user-facing "reset" that could affect access state must call this.

## macOS Files & Folders (per-folder TCC) — separate from FDA

Documents/Desktop/Downloads (and network/removable volumes) are gated per
app by their own TCC services, independently of Full Disk Access. Two facts
matter here:

- **A bundle without the matching `NS*FolderUsageDescription` key is denied
  SILENTLY** — EPERM on readdir, no prompt, while create/read/write/delete
  inside the folder can still succeed. The 2026-08 notes bug was exactly
  this: the engine helper shipped without `NSDocumentsFolderUsageDescription`
  and could not enumerate `~/Documents/Matrx/Notes` on any launch, with FDA
  granted and every other capability green. The keys now live on BOTH
  bundles (engine helper via `specs/matrx-engine-*-apple-darwin.spec`,
  parent via `desktop/src-tauri/Info.plist`) and are pinned by
  `tests/unit/test_folder_usage_descriptions.py`.
- **Diagnosis:** `probes.tcc_protected_folder(path)` names the governing
  folder service; `probes.posix_owner_can_read(path)` positively exonerates
  classic POSIX permissions. Only when FDA is granted AND POSIX is
  exonerated AND the path is under a TCC folder does `_build_message` name
  the Files & Folders control (evidence-based, same doctrine as FDA:
  never assert what wasn't established).

## Hard rules

1. No global access boolean. No cross-resource clearing. Ever.
2. Never claim FDA from an errno. Only `diagnose_fda() == denied` justifies
   the FDA remediation message; tests pin this
   (`tests/smoke/test_access_health.py`).
3. This package stays leaf-level: resolvers are callables, the launcher
   import is lazy. Importing `documents`/`paths` at module level will cycle.
4. Windows/Linux wording never mentions Full Disk Access (pinned by test).
5. Probe files must clean up in `finally` and never touch user content.
