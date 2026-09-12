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
