# Matrx Local / Cloud Chat Continuation Handoff

Snapshot: 2026-07-18 23:37 PDT. This is the pickup document for the next
agent. It summarizes current truth and points to canonical artifacts rather
than duplicating their full contents.

## Mission

Finish the cross-repository system that lets AI Matrx Cloud Chat use local
models and securely delegate computer/file/browser tools to the user's Matrx
Local desktop. The core path is shipped and the current desktop release is
healthy. The next phase is deployment verification, packaged end-to-end
testing, and closing the remaining local/cloud conflict and surface-parity
gaps.

## Current Executive State

- **Matrx Local `v1.3.142` is successfully released and installed.** The
  installed engine at loopback reports `version=1.3.142`, healthy, remote
  catalogs/app-config loaded, and `update_required=false` (instance identity
  deliberately omitted here).
- Release workflow
  [29672420522](https://github.com/armanisadeghi/matrx-local/actions/runs/29672420522)
  completed successfully. Verify, Linux, Windows, macOS Apple Silicon, macOS
  Intel, asset finalization, and published-release verification all passed.
- Published release:
  [AI Matrx v1.3.142](https://github.com/armanisadeghi/matrx-local/releases/tag/v1.3.142).
  It contains signed Linux, Windows, Intel macOS, Apple Silicon macOS, and
  updater assets plus `latest.json`.
- The earlier `v1.3.135` macOS verifier failure and the `v1.3.141` packaged
  shutdown failure are superseded by the corrective `v1.3.142` release.
- Core cloud-chat code is materially stronger, but **the entire system is not
  yet complete**. The biggest immediate gap is that AIDream's latest chat
  security commits are still local/unpushed and their production deployment
  is not confirmed.

## Worktree Safety Snapshot

Always re-run status before editing. Several unrelated workstreams are active.
Do not stash, reset, clean, or broadly stage them.

### Matrx Local

- Path: `~/code/matrx-local`
- Branch/HEAD at snapshot: `main` at `3c9528849`, tag `v1.3.142`, aligned
  with `origin/main`.
- Dirty work is an **unrelated download-system/frozen-dependency slice**
  (`app/services/downloads`, Tauri/frontend download manager, build specs,
  `pyproject.toml`, `uv.lock`, audit docs, and tests). Preserve it; do not mix
  it into chat commits.

### Matrx Frontend

- Path: `~/code/matrx-frontend`
- Branch/HEAD: `main` at `79f78c3ab`, tag `v0.3.672`, aligned with origin.
- Dirty work is an unrelated controlled data-table + direct-Supabase
  foundation slice. Preserve it.
- The chat commits from this work are already on main before `v0.3.672`.

### AIDream

- Path: `~/code/aidream`
- Branch/HEAD: `main` at `351713216`, **seven commits ahead of
  `origin/main`/`v0.1.563`**.
- Chat commits in that unpublished range:
  - `5fac24c97` enforce conversation ownership before hydration
  - `75545118a` bind tool results to the target/claiming desktop instance
  - `163abca6e` authenticate and owner-scope conversation warming
  - `ba81a1e0c` preserve same-instance result idempotency after claim expiry
  - `b1d82b0a9` preserve desktop tools across resumed continuations
- Two later `updates` commits are also ahead of origin. Review them before
  pushing; do not cherry-pick only the security commits without understanding
  their dependency order.
- Dirty work is unrelated assignment/scraper/web-crawl work. Preserve it.
- Production `/health` is currently healthy. Its observed process uptime
  began around 2026-07-19 02:06 UTC, just before the first local security
  commit above was created. Therefore it is a strong inference—not a proven
  version response—that production does **not** yet include these commits.

### Matrx Extend

- Path: `~/code/matrx-extend`
- Branch/HEAD: `main` at `d7ae21a`, aligned with origin.
- Five existing dirty screenshot/guidance/CDP/read/extract files remain an
  unrelated Content-IR workstream. Preserve them.

## Canonical Architecture References

Read these before changing behavior:

- Cloud Chat surface vision and parity ledger:
  `~/code/matrx-local/docs/CLOUD_CHAT_SURFACE.md`
- Current surface handoff:
  `~/code/matrx-local/docs/handoffs/cloud-chat-surface.md`
- Delegation protocol/runtime:
  `~/code/matrx-local/app/services/delegation/FEATURE.md`
- Canonical resume wire contract:
  `~/code/matrx-frontend/features/agents/docs/CLIENT_TOOL_SUSPEND_RESUME.md`
- Local/cloud sync doctrine:
  `~/code/matrx-local/docs/SYNC_CONTRACT.md`
- Canonical chat mirror:
  `~/code/matrx-local/docs/handoffs/canonical-local-db-mirror.md`
- Instance targeting:
  `~/code/matrx-local/docs/TIER2_DESKTOP_INSTANCE_TARGETING.md`
- Testing ladder:
  `~/code/matrx-local/docs/TESTING_LADDER.md`
- Defect ledgers:
  `~/code/matrx-local/FOUND_DEFECTS.md`,
  `~/code/matrx-frontend/FOUND_DEFECTS.md`, and
  `~/code/aidream/FOUND_DEFECTS.md`

Some prose in these documents is now stale. In particular, the older
`SYNC_CONTRACT.md` code-map paragraph still describes stripping four columns
and unconditional upsert behavior; the actual engine now uses explicit
allowlists, insert-ignore, version-CAS PATCH, and durable conflicts. Trust
current code plus commit `460eecdb1`, then repair the stale prose.

## What Is Done

### Desktop/cloud delegation and continuation

- Durable local execution remains single-path: the delegation engine executes;
  the UI only claims the continuation stream.
- UI claim/release calls now carry Supabase authentication.
- Long-running delegation waits refresh the Supabase access token instead of
  reusing an expired token.
- Run-scoped/synchronous gating blocks rapid double-submit races and prevents
  an older run from clearing or releasing a newer run.
- Target instance and claimant instance are enforced end to end. Matrx Local
  sends the same instance identity when posting results; AIDream uses atomic
  claim/result checks and non-disclosing wrong-instance failures.
- Same-instance duplicate delivery is idempotent even after lease expiry;
  unresolved submissions still require a live lease.
- Cold/background desktop continuation is durable and later reconciles into
  Cloud Chat rather than remaining permanently absent.
- Durable `chat.tool_call` output/error rows hydrate into the transcript when
  role=`tool` message content is null. Cancelled calls render as errors, and
  legacy/canonical call-id variants are accepted.

### Cloud Chat frontend reliability/privacy

- Cloud conversation cache is scoped by authenticated user; legacy unscoped
  cache is discarded, and sign-out/account switch aborts runs and clears
  in-memory state.
- Async auth hydration cannot overwrite a newer auth event.
- Conversations refresh and message hydration reconcile canonical durable
  history with optimistic/cache rows, including legacy combined-prompt rows.
- History fetches the newest 200 messages descending and restores display
  order, rather than returning the oldest 200.
- Background tool completions reconcile on bounded polls.
- Agent sends are blocked while the selected agent cannot be resolved; the
  requested ID is preserved and an actionable warning is shown.
- Sensitive request/context/variable/header/body logging was removed or
  redacted in frontend chat execution paths.

Relevant frontend commits:
`19278ba73`, `85f8853e8`, `76856b6e6`, `6b7e1a656`, `678f192d7`.

### Local chat/model runtime

- Local-model registration is no longer destroyed by one transient probe.
  Registration and reachability are separate; probes run off the event loop;
  generation/lock guards keep status snapshots coherent.
- Chat cache is account-scoped, stale auth hydration is ignored, the selected
  agent must resolve before send, and cloud/background completion reconciliation
  is active.
- Conversation hydration fetches newest history correctly.

Relevant commits:
`6ca69ad9a`, `efe8fd30a`, `2d8bd14b8`, `e50c17110`, `e4625ed96`,
`ce4af3622`, `1c39217c4`, `7e5bf4e9f`, `20cf3bf20`, `64e22ef22`.

### Chat mirror data safety

- Commit `460eecdb1` removed destructive merge-upserts.
- New rows use `resolution=ignore-duplicates`; existing versioned rows use
  conditional PATCH on `(id, version)`.
- Per-table desktop-authoring allowlists prevent newly added/server-owned
  columns from automatically being pushed.
- Tool-claim/target/resolution ledger fields are excluded from desktop
  payloads.
- Pull and echo SQL atomically require no pending local intent, closing the
  preflight-to-write race.
- Proven local/cloud divergence stores both copies in
  `sync_queue.action='conflict'`; generic/RLS-ambiguous 409s are not silently
  frozen as user conflicts.
- Lost-response idempotency compares equivalent timestamp representations
  correctly.
- `MXL-D-059` is mitigated/fixed for the original destructive cloud-owned-row
  corruption path. The server-side message tool-graph guard remains defense
  in depth.

### Release/runtime lifecycle

- Keychain access has a bounded signed-helper path that tolerates packaged
  cold-start time.
- Long-lived HTTP/SSE streams observe process shutdown.
- Uvicorn shutdown cancellation completes eligible responses cleanly.
- Hardware/native probes are cached, cancellable, and reaped during shutdown.
- Server completion has an explicit load-bearing barrier.
- These corrective changes are included in the successful `v1.3.142` release.

## Validation Evidence Already Obtained

- Matrx Local broader chat/sync suite: **111 passed**.
- Chat mirror characterization at final mirror iteration: **24 passed**;
  Ruff and compile checks passed.
- Matrx Local desktop chat suite: **202 passed** with TypeScript green before
  release; late hydration/reconciliation focus: **34 passed** with TypeScript
  green.
- Lifecycle/keychain/tunnel focused suite: **33 passed**; later packaged
  shutdown/keychain focus: **21 passed**.
- AIDream conversation ownership/security paths: **110 tests** in the
  ownership pass and **67 related tests** in the claim/result pass; Ruff and
  compile checks passed.
- Matrx Frontend focused privacy/delegation suites: **22 tests** and full
  typecheck passed for the touched state.
- Release `v1.3.142` CI passed byte-compile, smoke/parity tests, TypeScript,
  production-bundle boot smoke, every platform build, both macOS signing
  verification jobs, asset rename/finalization, and published-release
  verification.
- Earlier real-system evidence (before `v1.3.142`): signed-in frontend chat
  delegated a real 5120x2880 screenshot; localhost and tunnel streaming
  worked; a second machine successfully controlled the desktop through the
  Cloudflare tunnel.

## Exact Pending Work

The list below is current priority order. Items are not waiting on user input
unless explicitly stated.

### P0 — Deploy and prove the server-side security changes

1. **Review, push, and deploy AIDream's seven local commits.** The five chat
   commits named above protect conversation ownership, warm endpoints,
   target/claim instance binding, continuation tool preservation, and result
   idempotency. Production inclusion is not confirmed.
2. After deployment, run authenticated integration tests against production:
   owner continuation succeeds; cross-user conversation hydration/warm fails;
   wrong-instance claim/result fails without disclosure; same-instance replay
   is idempotent; expired unresolved claim is rejected; resumed turns retain
   desktop tools.

### P0 — Packaged `v1.3.142` Cloud Chat acceptance drill

3. Exercise the installed `v1.3.142` app—not a dev server—through this matrix:
   - cloud-model text turn and local-model text turn;
   - delegated `local_file`, `local_system`, `local_screen`, and one browser
     action;
   - foreground live continuation and cold/background continuation;
   - restart/reopen hydration of successful, failed, and cancelled tool calls;
   - user-disabled tool produces a clean refusal and no execution;
   - rapid double-send does not create overlapping runs;
   - tunnel disconnect/reconnect and offline-to-online recovery;
   - two devices on one account route only to the target instance;
   - a genuinely different authenticated user cannot claim/read/continue the
     first user's work.
4. Record concrete evidence in `docs/handoffs/cloud-chat-surface.md` and the
   defect ledgers. Release success proves packaging, not these user flows.

### P1 — Finish chat mirror correctness and multi-account isolation

5. **Per-user SQLite mirror partitioning.** Move mirrors to
   `~/.matrx/mirror/<user_id>/<schema>.db`, partition/wipe outbox state on
   account switch, and make repository reads owner-scoped. The frontend cache
   is fixed, but the shared local SQLite mirror can still expose account A's
   rows to account B on the same OS profile.
6. **Persist dirty columns/base version in the outbox.** Current update
   payloads project the whole allowlisted local row, not only fields changed by
   the local mutation. Split insert/update contracts and send only dirty
   fields. This prevents stale derived values such as conversation counts,
   cache state, request usage totals, content history, immutable FKs, and
   creation timestamps from being republished after an otherwise-valid CAS.
7. **Make NULL clears first-class.** `encode_local_row` omits `None`, so
   clearing fields such as `message.error` never clears cloud state. Dirty
   column tracking should distinguish “unchanged” from “explicitly set NULL.”
8. **Add chat conflict list/detail/resolve API and UI.** Conflicts are counted
   in `/chat/mirror/status` and both snapshots are durable, but users cannot
   choose keep-local, keep-cloud, or merge. Reuse the mature notes/file
   conflict UX patterns.
9. **Remove PostgREST/RLS ambiguity.** Empty conditional PATCH/GET responses
   can mean version miss, row absence, or RLS invisibility. Preferred fix: an
   invoker-RLS database RPC returning structured outcomes
   (`updated|conflict|missing|forbidden`) atomically. Keep service-role keys
   out of the desktop.
10. **Prevent queue starvation and add missing regression cases.** Quarantine
    unknown-table/no-allowlist rows; bound generic 409 retry behavior; test
    mixed insert/duplicate batches, existing-row fallback after batch failure,
    generic 409 retention, RLS zero-row outcomes, and conflict resolution.
11. **Add the reconcile backstop (`MXL-D-049`) and fix shared-connection
    transaction tearing (`MXL-D-050`).** A swallowed enqueue or another sync
    loop's commit can still leave a local mirror change with no durable push
    intent. Use a dedicated sync connection or explicit transaction ownership,
    then add a nightly/state-based outbox reconcile.

### P1 — Finish desktop chat behavior

12. **Replace the local `use-chat.ts` partial stream parser.** It manually
    handles only data/chunk/completion/error/end, silently drops malformed
    lines, and ignores canonical tool, reasoning, warning, retry, render, and
    diagnostic events. Reuse `parseAIDreamStream` plus the Cloud Chat reducer.
13. **Do not advertise an executor when engine health/identity is unavailable.**
    `desktop-client-context.ts` currently sends `desktop-native` even after
    `/health` fails, with empty `instance_id`. Separate surface identity from
    executor availability so cloud agents do not create stranded desktop calls.
14. **Make desktop Chat read the canonical mirror and run an airplane-mode
    drill.** The desktop hook still uses localStorage compatibility state rather
    than the mirror as its read model. Verify web-started chat visibility,
    offline writes, restart, reconnect, and no duplicated/lost rich parts.
15. **Paginate beyond newest 200 messages.** The immediate “oldest 200” bug is
    fixed, but long conversations still need backward pagination/history
    loading.

### P1 — Remaining security/remote-operation verification

16. Directly verify a second-device wrong-owner rejection with a different
    authenticated user/session. Prior remote tests used the same user's tunnel.
17. Verify Wake-on-LAN / wake-the-computer behavior from web/mobile. This has
    not been demonstrated end to end.

### P2 — Complete the Cloud Chat surface vision

18. Replace the remaining honest “Soon” rows in the desktop `+` menu:
    skills picker (`skill_config.included`), images/media and screenshot
    attachments, audio/Whisper input, notes/documents/scratchpad, active scopes,
    memory controls, and sandbox/compute-target binding.
19. Add purpose-based tool bundles (coding, email, etc.) on top of the two
    coarse desktop categories. Do not reuse bundle name `desktop`; it already
    belongs to the extension taxonomy.
20. Track `loaded_categories` across turns so discovery does not restart every
    turn.
21. Add a true web editor for per-instance `cloud_tools` settings with remote
    edit conflict handling.

### P2 — Sync architecture and documentation cleanup

22. Add Supabase Realtime for sub-second `chat.*` convergence; current mirror
    interval defaults to 300 seconds.
23. Fix the frontend auth-token `expires_at` writer so it stores the access
    token expiry rather than session expiry; engine-side JWT decoding already
    protects runtime behavior.
24. Plan `ai.*` mirror cutover before the later `workbench.*` re-platform, as
    ordered in `canonical-local-db-mirror.md`.
25. Reconcile stale ledgers/docs: several entries still describe fixed agent
    selection, local-model deregistration, Tier-2 instance behavior, or old
    blind-upsert mechanics. Do not close lifecycle defects solely from CI;
    first confirm the installed `v1.3.142` shutdown behavior.
26. Optional release housekeeping: obsolete draft releases `v1.3.135` and
    duplicate/draft `v1.3.139` remain visible. They are not functional blockers
    but can be cleaned up deliberately.

## Recommended Next-Agent Order

1. Preserve all unrelated dirty worktrees.
2. Review the seven AIDream-ahead commits, push/deploy them, and verify the
   production security matrix.
3. Run the packaged `v1.3.142` Cloud Chat acceptance drill and file every real
   failure before changing code.
4. Implement mirror dirty-column/base-version tracking, NULL clears, and
   conflict resolution as one coherent data-safety slice; use Supabase docs and
   live trigger/RLS inspection before choosing the RPC shape.
5. Partition the local mirror by user and perform the airplane-mode/account
   switch drill.
6. Unify the local stream parser and executor-availability advertisement.
7. Continue surface-parity product work only after the correctness/security
   gates are proven.

## Non-Negotiable Traps

- Never expose a service-role/secret key in Matrx Local or frontend code.
- Never send tool schemas from the client; send the surface/capability/state
  envelope and let the server inject canonical definitions.
- Do not add a second local execution path. The delegation engine owns
  execute-once/deliver-until-ack; the UI owns only stream continuation.
- Never resolve chat/document/file conflicts destructively by default.
- Do not treat release CI as proof of authenticated user flows.
- Do not broadly stage the current dirty worktrees.
- Do not log prompts, variables, request bodies, tokens, tunnel credentials,
  user paths, or instance identifiers.

## Suggested Skills

- `supabase:supabase`: required for chat RPC/RLS/trigger work and live schema
  verification.
- `supabase:supabase-postgres-best-practices`: use when designing the atomic
  conflict RPC, indexes, or transaction changes.
- `browser:control-in-app-browser`: signed-in Cloud Chat acceptance and
  desktop UI verification when the in-app browser policy permits it.
- `chrome:control-chrome`: authenticated Chrome/extension and second-session
  verification.
- `github:github` or `github:gh-fix-ci`: inspect deployments/actions if the
  AIDream or release pipeline fails.
- `github:yeet`: only if the user explicitly asks to publish and the intended
  commit scope is isolated.
- `handoff`: refresh this document after AIDream deployment and the packaged
  acceptance drill.

## Completion Standard

Do not call Cloud Chat flawless until all of the following are true:

- AIDream security commits are deployed and authenticated owner/instance
  tests pass against production.
- Installed `v1.3.142` completes the full foreground/background/offline/tunnel
  delegation matrix without stranded turns or stale tool cards.
- A second authenticated user and a second same-account instance are both
  correctly isolated.
- The local mirror is per-user, publishes only dirty fields (including NULL
  clears), preserves conflicts, exposes a resolver, and cannot lose an outbox
  intent through transaction tearing.
- Local and cloud chat consume the same canonical stream semantics.
- Remaining “Soon” surface features are either implemented or explicitly
  accepted as product backlog rather than hidden gaps.
