---
status: active
updated: 2026-07-18
repos: [matrx-local, aidream, matrx-sandbox]
owner-context: local filesystem intelligence, authoritative tool routing, and user-visible filesystem results
---

# Local filesystem intelligence — vision, gap analysis, and handoff

## Start here

The end-to-end implementation is complete on the three local `main` branches,
including the host Files page, full indexing controls, hardened routing
authority, and bounded sandbox APIs. The remaining work is release/deployment
authority, real signed-in cloud drills, packaged Windows/Linux proof, and
measured scale/failure validation. Do not start a competing index, path model,
tree component, or tool-routing policy.

Read these contracts before changing code:

- This repo: `app/services/filesystem/FEATURE.md`
- This repo: `desktop/src/features/filesystem/FEATURE.md`
- This repo: `app/services/file_sync/FEATURE.md`
- aidream: `aidream/services/tooling/FEATURE.md`
- aidream: `docs/cx_chat/TOOL_ROUTING_RULES.md`
- matrx-sandbox: `SANDBOX_CLIENT_GUIDE.md`

## Product vision

1. **One authoritative filesystem per run.** When an agent is bound to a real
   local machine or sandbox, the cloud server must remove every cloud-filesystem
   reference that competes with that target. `cloud_file` must not be present.
   A hard exclusion must survive agent declarations, user additions, dynamic
   discovery, preloaded tools, and continuation turns.
2. **Cloud Files become local files on desktop.** The managed Files system is
   already represented beneath the local sync root in full or pointer mode.
   Agents use the host filesystem and transparent hydration; the cloud tool is
   not a fallback while a real filesystem is bound.
3. **Useful immediately, better continuously.** Places and direct directory
   listing must work before indexing finishes. After installation, the app
   should quickly learn the shallow structure, then crawl deeper and enrich the
   representation for as long as idle resources permit.
4. **Index the whole accessible machine.** Discover Home, standard user
   folders, configured Matrx locations, code and document trees, and readable
   mounted volumes. A favored root must never become an allowlist that silently
   omits everything else.
5. **Let users express priority.** A user can mark code roots, archives, or
   other important folders for earlier and deeper work. The default discovery
   still finds them when possible, and low-priority locations must not starve.
6. **Treat Windows, Linux, and macOS as first-class.** Paths are opaque; use
   Known Folders, XDG directories, platform conventions, and actual mount
   discovery. OS-specific helpers may accelerate a platform but cannot define
   the shared contract or remove the portable path.
7. **Spend idle desktop capacity.** Metadata, categorization, text extraction,
   and embeddings are low-priority background work. They must yield to user
   activity, battery/memory/CPU/disk pressure, and clean shutdown.
8. **Stay fresh.** Watch important roots, reconcile the wider machine
   periodically, survive crashes, and correctly handle creates, edits, moves,
   deletes, permissions, removable media, and restarts.
9. **Build in layers.** Fast metadata/name/path search is the base. Content
   search comes next. Embeddings and richer categorization are additive—not a
   prerequisite for basic browsing or finding a known file.
10. **Keep intelligence local.** Indexes, extracted content, and embeddings
    belong on the user's machine, with understandable limits and controls.
11. **Make filesystem work visible.** Tool results should render as real places,
    directory trees, breadcrumbs, files, paging, and selectable paths. Users
    must be able to open/reveal items and insert selected paths back into chat.
12. **Give the model the right primitive.** Common navigation and indexed
    queries should be fast and structured. Broad unbounded scans should be a
    bounded fallback, not the model's default discovery strategy.
13. **Preserve conversation evidence.** Live and stored tool calls/results must
    render consistently after reload. Large-result fetching must be owner scoped
    and usable rather than failing after the model already did the work.
14. **Degrade honestly.** The app remains usable with a cold/partial index,
    missing embedding runtime, offline cloud sync, inaccessible folders, or an
    unplugged drive. The user sees actionable state instead of false completion.

## Why the work was necessary

The initiating traces showed that the problem was larger than “add vectors”:

- One code-directory request took about 207 seconds; an unscoped `fs_search`
  consumed roughly 120 seconds before timing out.
- A screenshot follow-up took about 170 seconds and 8 of 10 tool attempts
  failed, even though the local files existed.
- A correctly scoped local directory listing returned in 0–9 ms.
- Cloud/workspace `fs_*` tools were being used against host paths; list/read
  parameters were lost through proxies; cancellation did not stop a worker;
  structured metadata was discarded by the UI; and large-result retrieval
  could return 403.

The response therefore covered routing authority, proxy contracts, progressive
indexing, structured results, durable chat state, and UI—not just search.

## What is implemented

### `matrx-local`

The released foundation landed through merge commit `d7b4c2fd0` in tag
`v1.3.135`. The completion and adversarial-hardening pass is on local `main`
through `63fd21bbe` and has not yet been pushed or released.

- Cross-platform Places resolution for standard folders, configured roots,
  user priority roots, and readable real volumes.
- Fair breadth-first background crawling with a persistent leased queue,
  SQLite WAL metadata store, FTS5 name/path search, watchers, and periodic
  reconciliation. Traversal does not cross device boundaries or follow
  symlinked directories.
- Direct paged listing and bounded disk fallback remain available before the
  index is warm.
- Idle text-content FTS with a configurable quota. Optional FastEmbed semantic
  indexing is off by default, quota bounded, and does not import at startup.
- Resource-pressure throttling, clean lifecycle registration, crash-safe queue
  recovery, stale-content guards, and Windows-aware canonical path keys.
- Structured APIs under `/filesystem/*` and structured `local_file` actions:
  `places`, `list`, `find`, and `semantic_find`.
- Tool metadata survives local/delegated execution. Pointer-mode synced files
  hydrate transparently for read/copy/move operations.
- One reusable desktop filesystem result renderer with tree/list navigation,
  breadcrumbs, lazy children, paging, multiselect, reference insertion, and
  portable open/reveal actions.
- Live and stored tool execution cards share one renderer; structured results
  are preserved by call ID and rehydrated from conversation messages.
- Storage Settings exposes index state, failures, unavailable roots, priority
  roots, content/semantic controls and quotas, pause/resume, rebuild, clear,
  storage use, and refresh/scan timestamps. Request fencing prevents poll,
  refresh, mutation, disconnect, and reconnect races.
- A dedicated **This Device** Files page composes the canonical models and
  renderer for immediate browsing and scoped/all-location search. It exposes
  cold/partial/paused background-index state without blocking file use.
- Windows drive and UNC share roots are pinned in navigation and breadcrumbs;
  Up never enters an invalid UNC server-only namespace.
- Hydration and the actual file operation now share one per-tree guard for
  reads, copies, and moves, including destination-only writes into managed
  Files. Cancellation drains synchronous workers before releasing ownership.
- Reads reject special files and enforce scan/materialization/output bounds;
  copy/move rejects a directory destination inside its source before mutation.
- Startup schema migration only backfills legacy/null path keys. Enrichment
  survives SQLite contention, and first-page search materialization has a
  cancellation-safe concurrency admission cap.
- Tool catalog changes were applied to the canonical database and reported
  drift-free at verification time.
- An adjacent conversation-position defect that could drop continuation
  messages was fixed and regression tested.

### `aidream`

The released foundation is in `v0.1.560`/`matrx-ai/v0.4.19`. A further routing
authority and declaration-lifecycle hardening pass is complete on local `main`
through `1996eae67`, five commits ahead of `origin/main`, and is not released.

- A bound sandbox drops `cloud_file` and keeps `fs_*` rebound to that sandbox.
- A desktop-native request drops `cloud_file` plus hosted workspace
  filesystem/shell/git tools, and uses the desktop's local capability.
- Host hard exclusions are re-applied at the single merge write path, including
  dynamic mutations and continuation turns.
- Direct sandbox binding arms filesystem authority even when a redundant
  capability declaration is absent.
- Detaching restores the original tool declaration; reused context metadata is
  cleared so authority cannot leak between turns.
- Local-machine path validation supports absolute Windows drive/UNC paths and
  fails closed when the authoritative home is missing.
- The proxy now forwards bounded read ranges and recursive/patterned/paged list
  options.
- `fetch_tool_result` uses owner-scoped truncation entitlement.
- Authored registered/inline/MCP declarations survive executor filtering and
  reload; request-only MCP additions remain ephemeral.
- Effective authority is rehydrated after detach/reattach and registry changes;
  hidden, reference, handoff, silent, and system children cannot inherit an
  executor, and incomplete child reference results fail closed.
- Inline tool identity now requires semantically equal schemas rather than a
  name-only match.

### `matrx-sandbox`

The filesystem and atomic-release completion pass is on local `main` through
`e6786a9`, sixteen commits ahead of `origin/main`, and is **not yet deployed**.

- `/fs/list` supports bounded recursion, depth, pattern filtering,
  deterministic paging, truncation state, and no symlink-directory following.
- `/fs/read` supports bounded offset/limit and Range semantics with exact byte
  and UTF-8-safe responses.
- Structured errors and compatibility behavior are documented and tested.
- Bounded listing sessions run off the event loop and retain cleanup ownership
  through cancellation.
- Deployment now promotes tested immutable releases atomically, revalidates
  authority at the migration boundary, and rejects stale or mixed releases.

## Gap analysis

| Priority | Vision area | Current state | Remaining gap / proof |
|---|---|---|---|
| P0 | One authoritative filesystem | Policy and tests exist in aidream | Run real cloud-chat drills for desktop, sandbox, continuation, dynamic discovery, bind→detach, and unbound cloud-only control. Inspect the final model-visible tool list in every case. |
| P0 | Ship the complete chain | All implementation is complete on local `main` in all three repos | Review/push the local commit sets, publish compatible releases, deploy the sandbox image atomically, and verify every running version. Do not describe the chain as shipped before this. |
| P0 | Cross-platform correctness | Code is OS-neutral and unit tested | Exercise packaged installs on actual Windows and Linux as well as macOS: Known Folders/XDG, drive and UNC roots, mounts, hidden attributes, permissions, watchers, removable/offline media, and open/reveal actions. |
| P0 | Real agent/user experience | Components and contracts exist | Perform signed-in in-app E2E: “find my code projects,” a screenshot follow-up, path selection/reference, reload the conversation, and repeat with a large paged result. Confirm no hosted/cloud filesystem tool appears when desktop is bound. |
| P1 | User control and transparency | Full controls, quota/state metrics, failure guidance, and nonblocking cold/partial state are implemented | Validate wording, accessibility, and recovery behavior with real users and protected folders; no known implementation gap remains. |
| P1 | Full filesystem surface | Dedicated host Files page composes the canonical renderer and APIs | Complete the signed-in packaged UX drill, including reference insertion and conversation reload; do not fork a second tree or confuse it with managed cloud Files. |
| P1 | Performance evidence | Fast paths, bounds, throttles, and crash-safe queues exist | Benchmark cold/warm behavior at 100k, 500k, and 1M entries on all OSes. Pin regressions and observe source selection, queue progress, failures, staleness, CPU, memory, disk, battery, and cancellation. |
| P1 | Sync invariant under failure | Pointer hydration is implemented | Drill full and pointer sync, offline state, unhydrated pointers, sync disabled, and remote-only files. `cloud_file` must remain excluded while bound; local tools must hydrate or return a clear sync action—not silently fall back. |
| P1 | Maintenance correctness | Watchers plus reconciliation exist | Stress rapid rename/move/delete, permission loss/recovery, drive detach/reattach, huge directories, restarts, and overlapping priority roots. A very large single directory currently restarts from its beginning after lease expiry rather than resuming at an internal cursor. |
| P2 | Rich local understanding | Plain-text files up to 1 MiB feed content FTS | Add canonical extraction for PDF/Office and selected image/audio formats (including OCR/transcription where appropriate), with provenance, quotas, invalidation, and privacy controls. Reuse existing document/media pipelines. |
| P2 | Semantic quality and scale | Optional one-vector-per-text-file FastEmbed search, bounded to 10k vectors | Unify runtime/model install UX with the existing capability/download system. Measure usefulness before adding chunking, hybrid ranking, or ANN storage; then add only what the evidence justifies. |
| P2 | Kernel/network edge cases | Cooperative operations and admitted search builds retain ownership through outer cancellation | An inner `wait_for(to_thread(...))` timeout cannot kill the underlying OS/SQLite thread, and a blocking OS/network filesystem syscall cannot be cancelled mid-syscall. Add process isolation only where real drills justify it; preserve correct results over aggressive “completion.” |

## Acceptance bar for the next owner

The feature is ready to close only when all of these are true:

- A bound desktop or sandbox model-visible toolset contains exactly one
  filesystem authority and never contains `cloud_file`.
- The unbound/cloud-only control still receives `cloud_file`; detach restores
  the pre-binding declaration.
- Places and direct listing work with an empty index. A user can immediately
  select or reference a returned path.
- Priority roots demonstrably run sooner while an ordinary low-priority volume
  still makes progress.
- A warm 500k-entry fixture meets the intended baseline: Places p95 below
  50 ms, a directory page below 150 ms, indexed name/path search below 100 ms,
  useful shallow knowledge within 2 seconds of first start, and cooperative
  cancellation observed within 250 ms outside an in-flight OS syscall.
- Create/edit/move/delete and restart tests leave no stale search hits or false
  “complete” state.
- Windows, Linux, and macOS packaged drills pass and are recorded with versions,
  fixtures, timings, and screenshots/logs.
- Full and pointer cloud-sync modes behave through local tools when online,
  offline, and partially hydrated.
- Tool trees/results survive conversation reload and large-result retrieval is
  owner scoped.
- Users can see and control local index scope, enrichment, storage use, state,
  and recovery without learning implementation terminology.

## Recommended pickup order

1. **Protect the worktrees.** Re-read every repo's `AGENTS.md`, inspect status,
   and do not reset or sweep unrelated changes. `aidream` has substantial
   unrelated active document/RAG/catalog work; preserve it exactly.
2. **Review and publish the three local commit sets**, then build/deploy the
   sandbox image through its atomic approved-ref flow and verify the running
   release contract.
3. **Run the authority E2E matrix** with signed-in cloud turns. Capture the
   resolved tool names and actual execution target, not just the final answer.
4. **Run the desktop UX drill** from cold index through warm index, tree
   interaction, path reference, screenshot follow-up, and conversation reload.
5. **Add Windows and Linux packaged drills** and record real measurements.
6. **Build performance and mutation stress fixtures**, fix measured failures,
   and pin the acceptance baselines.
7. **Drill pointer/full sync failure modes** online/offline and verify local
   hydration or explicit recovery without restoring `cloud_file`.
8. **Then deepen enrichment**: canonical document extraction first, semantic
   install/model UX second, chunking/ANN only after measured need.

## Verification references

Previously recorded results for this work:

- `matrx-local`: 124 focused filesystem/file-sync tests passed, 1 skipped;
  prior full backend pass was 517 passed, 1 skipped.
- Desktop: 187 unit tests passed; TypeScript and production Vite build passed.
- Source engine lifecycle drill: clean startup/background indexing/shutdown;
  the contained optional image runtime was verified on a fresh dev home.
- Production web smoke: clean at `18c5743e5`; repeat packaged smoke from the
  settled final tree because the previous cold build overlapped concurrent
  commits and was not valid evidence.
- Existing-index migration drill preserved 18,668 metadata rows and 351 content
  documents (about 7.0 MiB indexed text).
- `aidream`: 3,081 passed, 13 skipped broadly; independent authority-focused
  adversarial runs passed 129 tests plus 43 schema tests.
- `matrx-sandbox`: 86 orchestrator tests passed, 7 skipped; 58 SDK tests passed.

Minimum local regression commands:

```bash
# matrx-local
uv run pytest tests/unit/test_filesystem_service.py -q
cd desktop && pnpm test:unit && pnpm build
cd .. && ./scripts/smoke.sh

# aidream
uv run pytest \
  aidream/api/tests/test_local_pc_binding.py \
  aidream/api/tests/test_real_filesystem_tool_routing.py \
  aidream/api/tests/test_realtime_tools.py \
  packages/matrx-ai/tests/test_bound_filesystem_adapter.py -q

# matrx-sandbox
pytest sandbox-image/sdk/tests/test_filesystem_api.py -q
```

Use `docs/TESTING_LADDER.md` and `docs/SMOKE_HARNESS.md` for the correct
desktop rung. A green unit suite is not evidence that the packaged app starts
or that the cloud request resolved the right tools.

## Repository state and safety notes

- `matrx-local`: local `main` is fifteen commits ahead of `origin/main` through
  `63fd21bbe`; the working tree was clean at this update. These commits include
  adjacent startup/image-runtime/bridge fixes discovered during the same
  packaged validation loop.
- `aidream`: local `main` is five commits ahead of `origin/main` through
  `1996eae67`. Its working tree contains unrelated active document, RAG,
  catalog, and generated-declaration work; do not clean, stash, or absorb it.
- `matrx-sandbox`: local `main` is sixteen commits ahead of `origin/main`
  through `e6786a9`. Coordinate review, push, and atomic deployment.
- The cloud tool-registry change was already applied. Before editing registry
  definitions, run the existing drift/status command and follow the Supabase
  migration/tool-registry rules; do not create a second definition source.
- The local index and managed Files sync are different services. The sync root
  is one discoverable local place; filesystem intelligence must not duplicate
  sync state or call the cloud Files API directly.

## Suggested skills for the next coding agent

- `feature-deep-dive` for auditing the remaining end-to-end feature before a
  broad completion pass.
- `build-sub-feature` for a bounded addition such as full indexing controls or
  the dedicated host Files surface.
- `browser:control-in-app-browser` for signed-in cloud-chat and UI E2E drills.
- `supabase` and `supabase-postgres-best-practices` only when touching the
  canonical tool registry or database-backed contracts.
- `handoff` when transferring ownership again; update this document rather
  than creating a competing status file.
