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

2026-07-13 addendum: the fully managed file service (EC2/Cloudflare,
`files.matrxserver.com`) is operational — the desktop hits the matrx-files
API directly from the start (no aidream detour).

## Resources

- Feature contract + invariants: `app/services/file_sync/FEATURE.md` (read first)
- Doctrine row: `docs/SYNC_CONTRACT.md` § User files (matrx-files replica)
- Server wire contract: `/Users/armanisadeghi/code/aidream/packages/matrx-files/matrx_files/api/router_files.py` (`/files/sync/changes` + `/files/sync/folders`) + `common-docs/matrx-files-service/FEATURE.md`
- Tests: `tests/characterization/test_file_sync_characterization.py` (21) + aidream `packages/matrx-files/tests/test_sync_feed.py` (14)
- UI: Configurations → File Sync card (`desktop/src/components/files/FileSyncPanel.tsx`, `desktop/src/hooks/use-file-sync.ts`)

## Remaining work

1. **Deploy matrx-files 0.1.4** — blocked on Arman (see Decisions needed).
   Until it's live, `files.matrxserver.com` lacks `/files/sync/*` and the
   desktop engine's pulls 404/401-fail loudly each tick (by design). After
   deploy: run the DEPLOY.md triad + `GET /files/sync/changes?limit=1` with a
   user JWT.
2. **End-to-end drill against the live service** (FEATURE.md § Verification +
   the airplane-mode drill in SYNC_CONTRACT): enable full sync → files appear
   under the Files root → offline edit → reconnect → cloud converges; pointer
   Read hydrates transparently.
3. **First-run prompt** — plain-language mode choice on first sign-in (gentle
   prompt doctrine); today the default is `pointers` silently. Natural home:
   the existing first-run/setup flow in the desktop app.
4. **Per-folder mode overrides** (vision: "global default + per-folder
   override") — settings shape + engine filter; not started.
5. **Presigned uploads** — buffered multipart has a 512 MB ceiling
   (`_UPLOAD_MAX_BYTES`); the service needs `/files/upload/presigned` parity
   (exists in aidream host only), then the desktop client switches.
6. **Realtime channel** — polling interval is 300s; Supabase Realtime on
   `files.files` (column-safe publication exists per the sandbox bridge work)
   would make pulls near-instant. Optional enhancement.
7. **Sandbox bridge convergence (after 1–3)** — the sandbox's `/cloud-files`
   service-token feed and this JWT feed should converge on the package's
   `/files/sync/*`; do not build a separate sandbox sync.

## Done

- Server: device-replica sync feed in the matrx-files package (0.1.4, aidream `40893249f`) — keyset cursor + tombstones + folders; verified against the live DB via a locally-run standalone instance.
- Desktop: full engine + index + client + hydration seam + REST + lifecycle — see `app/services/file_sync/` (matrx-local `fb786ea39`).
- Modes/settings/UI: `file_sync_mode` (off|pointers|full, default pointers) both settings layers + Configurations File Sync card (`f367137a8`).
- Tools: `tool_read`/`tool_copy`/`tool_move` hydrate pointers transparently.
- Mirror: `files.files`/`files.folders` joined the structural mirror (snapshot refreshed live 2026-07-13).
- Tests: 21 desktop characterization + 14 package wire tests, all green.

## Decisions needed

**Deploy matrx-files 0.1.4.** Situation: the 0.1.4 code (sync feed) is
committed in aidream (`40893249f`, tag `matrx-files/v0.1.4` created locally)
and verified against the live DB, but publishing requires pushing the tag
(triggers the PyPI OIDC workflow) and bumping the container on EC2
`i-084f757c1e47d4efb` — both gated actions an agent could not take alone.
Decide: push the tag `matrx-files/v0.1.4` (or say "proceed" to an agent with
permission), then rebuild/restart the `matrx-files` container per
`packages/matrx-files/DEPLOY.md`.
