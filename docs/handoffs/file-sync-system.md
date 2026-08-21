---
status: active
updated: 2026-07-14
repos: [matrx-local, aidream]
owner-context: user file sync — full local replica OR pointer-mapped virtual files; the Matrx alternative to Google Drive
---

# File sync system — handoff

## Vision

Arman's verbatim intent for this capability lives at
`common-docs/systems/media/file-service/VISION.md` (moved there 2026-08-20, centralization
sweep) — read it first.

2026-07-13 addendum: the managed file service (EC2/Cloudflare,
`files.matrxserver.com`) is operational — the desktop hits the matrx-files
API directly from the start (no aidream detour).

## Resources

- Feature contract + invariants: `app/services/file_sync/FEATURE.md` (read first)
- Doctrine row: `docs/SYNC_CONTRACT.md` § User files (matrx-files replica)
- Server wire contract: aidream `packages/matrx-files/matrx_files/api/router_files.py` (`/files/sync/changes` + `/files/sync/folders`) + `common-docs/systems/media/file-service/FEATURE.md`
- Tests: `tests/characterization/test_file_sync_characterization.py` (35) + aidream `packages/matrx-files/tests/test_sync_feed.py` (14)
- UI: Configurations → File Sync card (`desktop/src/components/files/FileSyncPanel.tsx`, `desktop/src/hooks/use-file-sync.ts`)
- E2E drill script pattern (isolated tmp DB/root + minted agent JWT + locally-run service): recreate from this doc's Verification section; the original ran from a session scratchpad.

## Remaining work

1. ~~**Deploy matrx-files 0.1.4**~~ — DONE: matrx-files **0.1.5** deployed to
   `files.matrxserver.com` 2026-07-14 (Arman ran deploy.sh; verified health
   green + `/files/sync/*` returning 401 = live).
2. **Verification remaining** (not yet EXERCISED):
   - The engine's own background loop against the PROD service with the real
     desktop sign-in (drill used an isolated engine + locally-run service —
     see progress log).
   - `full` mode backfill on a large tree (only pointers mode drilled live).
   - Airplane-mode drill: offline edits → reconnect → convergence.
   - Tool-layer hydration through a real agent `Read` (unit-tested only).
   - React panel against a live engine (typechecked only).
3. **First-run prompt** — plain-language mode choice on first sign-in; today
   the default is `pointers` silently.
4. **Per-folder mode overrides** (vision: "global default + per-folder
   override") — settings shape + engine filter; not started.
5. **Presigned uploads** — buffered multipart has a 512 MB ceiling
   (`_UPLOAD_MAX_BYTES`); needs `/files/upload/presigned` parity on the
   service, then the desktop client switches.
6. **Upload optimistic concurrency** — `POST /files/upload` has no
   expected-checksum precondition, so a push that races a remote edit is
   cloud-side LWW (server version history still preserves the old bytes;
   pull-before-push shrinks the window). Server contract addition
   (`expected_checksum` → 412), then thread through `_push_upload`.
7. **Folders feed cursor** — `/files/sync/folders` is one bounded page
   (5000) with a loud `truncated` flag; grow a cursor if any user exceeds it.
8. **Realtime channel** — Supabase Realtime on `files.files` would make
   pulls near-instant vs the 300s poll. Optional.
9. **Sandbox bridge convergence (after 1–2)** — converge the sandbox's
   service-token `/cloud-files` feed onto the package's `/files/sync/*`;
   never build a separate sandbox sync.

## Done

- Server: device-replica sync feed in the matrx-files package (0.1.4) — keyset cursor + tombstones + stability lag + folders (aidream `packages/matrx-files`, pinned by `tests/test_sync_feed.py`).
- Desktop: engine + index + client + hydration seam + REST + Phase 2e lifecycle — `app/services/file_sync/` (`fb786ea39`).
- Modes/settings/UI: `file_sync_mode` both settings layers + Configurations File Sync card (`f367137a8`).
- Adversarial hardening: 30 confirmed findings fixed — non-destructive hydration, first-sight adoption, conflict discipline (pending_op clearing, drain skip, watcher guards), guarded upload completion, `.part` filter, path safety, cursor edge cases (`56340714e`).
- Tools: `tool_read`/`tool_copy`/`tool_move` hydrate pointers transparently.
- Mirror: `files.files`/`files.folders` joined the structural mirror; V11 `file_sync_state` sidecar.
- Tests: 35 desktop characterization + 14 package wire tests green (`66ff16c28`).

## Progress log — verification ledger (2026-07-14)

**EXERCISED for real:**
- Live wire: `/files/sync/changes` + `/files/sync/folders` served by a
  locally-run matrx-files standalone against the LIVE cloud DB, real JWT —
  pagination, composite cursor, tombstones, 400-on-garbage-cursor.
- Full E2E drill (isolated engine, tmp DB/root, agent identity, local
  service, REAL DownloadManager): bootstrap 55 files/41 folders → 52
  pointers + placeholders + dirs; idempotent second cycle; hydration with
  checksum verify; real multipart upload + cloud-id rekey; local delete →
  cloud tombstone → tombstone returned on the feed. DRILL PASSED.
- Engine boot on this machine: Phase 2e green, watcher running, loud 404s
  against prod (endpoints not deployed yet — expected).
- `./scripts/smoke.sh` web: CLEAN at `f367137a8`.

**Unit/typecheck only:** everything listed under "Remaining work —
verification" above.

## Decisions needed

(escalated to `.matrx/ARMAN_TASKS.md` — deploy 0.1.4 tag push + container
bump; settings-catalog official-doc update approval.)
