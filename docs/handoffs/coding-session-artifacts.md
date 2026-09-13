# Coding-session artifacts — capture, keep, and publish what a session produced

Status: BUILD STARTED 2026-09-12 (Work session, Claude). Owner: the Work session.

## The defect (Arman, 2026-09-12)

Claude Code writes its deliverables into a per-session scratchpad under
`/private/tmp/claude-<uid>/<project-slug>/<session-id>/scratchpad/…` (example:
`…/bf9b08ed-…/scratchpad/qd/page/desk-v2.html`). The coding-session sync
mirrors the CONVERSATION to AI Matrx but never the files, so:

1. switching Claude accounts hides the session and its artifacts are gone
   from view (the bytes still sit in /tmp until macOS purges them);
2. AI Matrx shows the conversation without the things it built;
3. another session on the same Mac has no way to find them.

## The design (simple version, platform primitive: artifacts are files)

- **Engine lane `coding_session_artifacts`** (matrx-local, Python): every
  cycle, for every Claude session known to the session index, scan
  `<scratchpad>` and copy *deliverable* files into a durable per-session
  folder `~/.matrx/coding-sessions/artifacts/claude/<session-id>/`, preserving
  relative paths. Deliverable = not inside a git checkout/worktree,
  node_modules, .venv/venv, __pycache__, dist/build/target/.next, not a
  symlink, ≤ 25 MB. Unchanged (size+mtime) files are skipped. A
  `manifest.json` per session lists path, size, sha256, mtime, upload state.
- **Screen**: the Coding Sessions page gets an Artifacts column (count) with
  Reveal-in-Finder and the lane's status/blocker line, like every other lane.
- **Publish to AI Matrx**: the engine uploads each new/changed artifact through
  the coding-session bridge (server resolves the organization exactly as it
  does for the session itself — no organization header, no user question),
  and the server stores the bytes in matrx-files under
  `Coding Sessions/<provider>/<session>/…` and records the manifest on the
  coding session so the conversation page lists its artifacts.
  Server work: `POST /coding-sessions/{cli_session_id}/artifacts` (multipart)
  in aidream; frontend: artifact list on the conversation.

Order: local durable mirror + screen first (no dependency, stops the loss
today), then the server endpoint, then engine upload, then the frontend list.

## State — 2026-09-12 17:25 PT (Work session)

Shipped on origin/main (engine): `app/services/coding_sessions/artifacts.py`
+ routes `/coding-session/artifacts/{status,sessions,sessions/{id},sync}`
(commit 1660c7145), organization resolver personal-org fallback
(dba7c70bc — this also unblocks file sync and screenshot publishing for
multi-org users with no default). Tests: `tests/unit/test_coding_session_artifacts.py`,
`tests/unit/test_organization_resolver.py`.

Measured on Arman's Mac: 723 scratchpads → 8,599 deliverables / 600 MB
captured in 6.8 s (4,630 files skipped by the 2,000-per-session cap in two
sessions full of browser-profile/cache files; 6 files over 25 MB). One real
session (bf9b08ed…, 191 files, 34 MB) published to the server in 21 s as
admin@admin.com; `files.files` rows carry `metadata.kind =
coding_session_artifact` + `metadata.cli_session_id`.

Known transport defect (NOT fixed, documented): uploads through the edge
(Cloudflare → files.matrxserver.com AND server.app) drop the connection
mid-body intermittently (12 MB bodies died at 2 MB and 11 MB, then
succeeded; a 20 MB file failed 8/8). Small files finish before the drop.
The lane retries across ticks and abandons after 8 attempts with a visible
`upload_error`/`abandoned_upload` count; the local durable copy is kept
regardless. Root cause (Cloudflare vs. this Mac's network under load)
unproven — direct-to-origin is firewalled so it could not be isolated.

In flight: desktop screen (Artifacts lane line + column + diagnosis
dialog) and the AI Matrx conversation "Artifacts" panel (matrx-frontend),
both dispatched to Opus builders 17:20 PT. Release v1.4.92 (engine lane,
resolver, throughput publisher) building; screen ships in the next one.

## Live on Arman's Mac — 2026-09-12 18:58 PT (v1.4.94 installed)

v1.4.92 built clean but macOS killed it at spawn (host carried the Vault
work's profile-backed entitlements with no profile — SIGKILL, error 163,
while Gatekeeper/notarization said accepted). Fixed by d7716b470:
entitlements split (Entitlements.plist = no-profile host;
Entitlements.vault.plist only on the sealed path) + verifier guard proven
red on the killed bundle. v1.4.94 verified before install and launched.

Measured on the installed engine: delivery 191 envelopes/min (was 12),
100-row tick 29.9 s, concurrency 8, no blocker; artifacts lane active —
728 sessions, 5,986 files (394 MB) captured to
~/Library/Application Support/MatrxLocal/coding-sessions/artifacts/claude_code/,
uploading ~40/min under Arman's own account, 0 failed, no blocker.
Peer cut v1.4.95 (Windows hook-file fix) right after.
