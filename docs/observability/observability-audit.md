# Matrx Local observability audit

Completed by Codex Agent on 09/14/2026

The following is the agent's exact output but this will need to be cleaned up and better organized to become an official document that always reflects the exact current status of what we have.


I found substantial diagnostic infrastructure, but it is primarily local, ephemeral, and operator-driven. It does not currently provide centralized, automatic, all-user error tracking or a guarantee that every failure is captured.

## Executive conclusion

Current system:

- Python engine logs most warnings/errors to rotating local files.
- HTTP requests are recorded in a structured local access log.
- Tauri records lifecycle events and engine stderr locally.
- React/Tauri/LLM activity is combined into an in-memory Activity panel.
- Service failures create local diagnostic JSON snapshots.
- Smoke tests capture packaged startup logs into per-run artifacts.
- Business-operation failures are sometimes persisted in SQLite and sometimes synchronized as product data to the cloud.
- No Sentry/Bugsnag/OpenTelemetry/PostHog/central log ingestion system exists in this repository.
- Generic local logs, diagnostic snapshots, React logs, Tauri logs, and crash reports are not automatically uploaded to Matrx cloud.
- There is no central fleet-wide view showing errors from every Matrx Local installation.
- There is no universal error event contract or guaranteed capture for all unhandled browser promises, Rust panics, process kills, or background-task failures.

## 1. Python engine logging

Canonical logger:

[app/common/system_logger.py](/Users/armanisadeghi/code/matrx-local/app/common/system_logger.py)

It provides:

- Console logging.
- Rotating `system.log`.
- Sensitive-value redaction.
- Root logger bridging for warnings/errors from third-party libraries and Uvicorn.
- Console deduplication for repeated INFO/DEBUG messages.
- Traceback capture for explicit `exc_info=True`.

Configuration is in [app/config.py](/Users/armanisadeghi/code/matrx-local/app/config.py:353):

- Default level: `DEBUG`.
- Maximum file size: 10 MB.
- Backups: 5.
- Total normal system-log capacity: approximately 60 MB.
- Dev console output omits timestamps.
- File logs retain timestamps.

Python coverage is broad: approximately 1,332 logger calls exist under `app/`. However, there are also approximately 221 direct `print()`/printing candidates. Those are not guaranteed to reach `system.log`; they are only captured when a parent process captures stdout/stderr.

Important limitation: the root bridge forwards only `WARNING` and above. Ordinary INFO logs from modules using standard `logging.getLogger(...)` can still disappear unless their logger is explicitly connected to the system logger.

## 2. Python request and access logging

[app/common/access_log.py](/Users/armanisadeghi/code/matrx-local/app/common/access_log.py)

Every HTTP request is written to:

```text
access.log
access.log.1
access.log.2
```

Each record contains:

- UTC timestamp.
- HTTP method.
- Path.
- Sanitized query string.
- Origin.
- Abbreviated user agent.
- HTTP status.
- Duration.

Rotation:

- 10 MB per file.
- Two backups.
- Approximately 30 MB maximum.
- An oversized old access log is deleted rather than retained.

The request middleware in [app/main.py](/Users/armanisadeghi/code/matrx-local/app/main.py:2250) also logs:

- Request start.
- Response status.
- Slow requests over five seconds.
- Request metadata.
- Error details.
- Failure ID.
- Sanitized traceback for non-private requests.

Private request bodies are intentionally excluded or reduced to shape-only diagnostics. This protects prompts, audio text, embedding input, and other private content, but it also means the exact request payload may not be available for debugging.

The access log does not contain:

- Response bodies.
- Request bodies.
- Correlation IDs for every request.
- User/account identity.
- Organization identity.
- Durable trace/span IDs.
- A direct link to the related database row or cloud request.

## 3. Durable engine diagnostic snapshots

[app/launcher.py](/Users/armanisadeghi/code/matrx-local/app/launcher.py:474)

When a managed service enters `FAILED`, the engine writes a JSON snapshot automatically.

Default location:

```text
~/.matrx/diagnostics/
```

Development location:

```text
~/.matrx-dev/diagnostics/
```

Each snapshot may contain:

- Failed service and error.
- Full service registry.
- Service state, PID, port, URL, metadata, and traceback.
- Engine PID/PPID.
- Memory, CPU, thread count, open files, and process creation time.
- Related Matrx, llama-server, cloudflared, and Playwright processes.
- Listening ports.
- Selected environment variables.
- Redacted secret-bearing environment values.
- Live Python thread stacks.
- Disk capacity.
- The triggering failure.

Retention is capped at 50 JSON files. Old files are deleted.

This is one of the strongest systems currently present. Its main limitations are:

- It only triggers for failures routed through the service registry.
- A random exception in an unregistered background task may not create a snapshot.
- It is not uploaded automatically.
- It may contain sensitive machine metadata such as paths, process command lines, usernames, and thread stacks.
- It captures current state after detection, not necessarily the exact state at the original failure moment.

## 4. Crash and unclean-shutdown breadcrumbs

[app/preflight.py](/Users/armanisadeghi/code/matrx-local/app/preflight.py:1258)

If the next engine boot finds a stale discovery file whose PID is dead, it writes:

```text
~/.matrx/diagnostics/crash-<timestamp>-pid<id>.json
```

It records:

- Detection time.
- Dead PID.
- Previous reported version.
- The fact that the prior engine did not perform a clean shutdown.

This detects likely crashes, OOM kills, SIGKILLs, power loss, and external termination after the fact.

It does not identify:

- Who killed the process.
- The exact exception if the process produced none.
- Whether the process was killed by the OS, Tauri, Activity Monitor, an updater, or another process.
- The preceding application state unless other logs survived.

## 5. Tauri/Rust logging

[desktop/src-tauri/src/lifecycle_log.rs](/Users/armanisadeghi/code/matrx-local/desktop/src-tauri/src/lifecycle_log.rs)

Durable Rust files:

```text
macOS:   ~/Library/Logs/MatrxLocal/lifecycle.log
Windows: %LOCALAPPDATA%\MatrxLocal\logs\lifecycle.log
Linux:   ~/.local/state/matrx-local/logs/lifecycle.log
```

`lifecycle.log` records:

- Engine spawn.
- Engine termination.
- Signals and shutdown paths.
- Orphan sweeps.
- Window lifecycle events.
- Other Rust-owned process-control events.

It rotates at 5 MB, retaining one backup.

Durable engine stderr:

```text
engine-stderr.log
engine-stderr.log.1
```

This is important because Python import-time failures, PyInstaller failures, `SystemExit`, and early boot tracebacks may occur before Python logging initializes.

Major gap: engine stdout is not durable. In Tauri it is held in a Rust in-memory ring buffer of only 200 lines. Finder-launched packaged applications do not reliably provide a user-readable stdout file.

Another gap: `lifecycle.log` uses OS-native locations even for Tauri development runs, while Python development logging uses the repository’s `system/logs` directory. This makes dev evidence split across multiple places and can cause agents to inspect the wrong log directory.

## 6. llama-server logging

[desktop/src-tauri/src/llm/server.rs](/Users/armanisadeghi/code/matrx-local/desktop/src-tauri/src/llm/server.rs)

llama-server output is:

- Captured in memory.
- Trimmed to approximately 12–16 KB.
- Classified into loading, ready, warning, and error messages.
- Sent live to the React Activity panel.
- Used to produce a diagnostic excerpt on startup failure.

It is not durably written to an app-owned file.

Therefore, if the application closes or crashes before the user copies the Activity output, much of the llama-server evidence is lost.

## 7. React/frontend logging

[desktop/src/hooks/use-unified-log.ts](/Users/armanisadeghi/code/matrx-local/desktop/src/hooks/use-unified-log.ts)

The unified frontend bus combines:

- Python setup log SSE.
- Python system-log SSE.
- Structured HTTP access-log SSE.
- Tauri sidecar IPC.
- llama-server IPC.
- Engine discovery lifecycle.
- Supabase auth lifecycle.
- Voice/transcription.
- Setup wizard.
- Background tasks.
- React console output.
- Download activity.

Frontend retention is in memory only:

- 5,000 text log lines.
- 1,000 structured access entries.
- No disk persistence.
- No cloud upload.
- Clearing Activity deletes only the current in-memory view.

React console methods are intercepted:

- `console.log`
- `console.info`
- `console.debug`
- `console.warn`
- `console.error`

The app also has centralized helpers in [desktop/src/lib/error-reporting.ts](/Users/armanisadeghi/code/matrx-local/desktop/src/lib/error-reporting.ts) for logging errors instead of silently swallowing promises.

Current frontend logging count is approximately:

- 145 `emitClientLog` calls.
- 156 direct console calls.

Important gaps:

- No `window.onerror` handler was found.
- No global `unhandledrejection` handler was found.
- A rejected promise that is not explicitly caught may escape the unified logger.
- Console capture is initialized in a React effect, so an initial boot/render failure may occur before console interception starts.
- Error boundaries capture render failures, but not every event-handler or asynchronous failure.
- The log bus disappears when the renderer reloads or the app exits.

## 8. React error boundaries

[desktop/src/components/ErrorBoundary.tsx](/Users/armanisadeghi/code/matrx-local/desktop/src/components/ErrorBoundary.tsx)

The top-level boundary:

- Shows a user-facing recovery screen.
- Logs the render exception.
- Includes the first component-stack line.
- Keeps the error visible in Activity.

[desktop/src/components/recovery/SurfaceErrorBoundary.tsx](/Users/armanisadeghi/code/matrx-local/desktop/src/components/recovery/SurfaceErrorBoundary.tsx) provides narrower page-level recovery.

This prevents one screen from necessarily taking down the entire frontend, but it does not persist crashes or upload them.

## 9. Activity panel and issue reports

[desktop/src/pages/Activity.tsx](/Users/armanisadeghi/code/matrx-local/desktop/src/pages/Activity.tsx)

The Activity page provides:

- Overview.
- Server logs.
- Client logs.
- HTTP access logs.
- All logs.
- Filtering by level/source.
- Search.
- Grouping.
- Pause/resume.
- Scoped clearing.
- Copy filtered logs.
- Copy Issue Report.

The Issue Report is a manual clipboard export. It is not automatically submitted anywhere.

This means the current operational workflow is:

1. User notices a problem.
2. User opens Activity or recovery UI.
3. User copies a report.
4. User sends/pastes it to an agent or developer.
5. The error is manually triaged into `CURRENT_ERRORS.md` or `FOUND_DEFECTS.md`.

That is useful, but it is not automatic incident collection.

## 10. Smoke-test logging

[scripts/smoke.sh](/Users/armanisadeghi/code/matrx-local/scripts/smoke.sh)

Smoke output is stored in:

```text
.smoke/runs/<timestamp>/
```

Typical files include:

- `summary.md`
- `app.log`
- `web.log`
- `build.log`
- `health.json`
- `admin-status.json`
- Other probe results.

The smoke harness checks for patterns such as:

- Python tracebacks.
- Import errors.
- Package metadata failures.
- Rust panics.
- React crash screens.
- Port collisions.
- Unexpected termination.
- Failed launcher services.

It tests:

- Production Vite bundle in a browser.
- Packaged Tauri app.
- PyInstaller sidecar.
- Engine startup.
- `/health`.
- `/admin/status`.
- Shutdown.
- Orphan processes.

It does not currently prove:

- Installer behavior.
- Signed/notarized app behavior.
- Full authenticated user workflows.
- Long-running stability.
- All API routes.
- All background jobs.
- All OS permissions.
- All cloud synchronization paths.
- Runtime errors occurring after startup.

The smoke harness is also explicitly not wired into `release.sh`; it remains opt-in.

## 11. Local SQLite error and failure state

The local database stores many operational states, including:

- AI request status and error.
- Tool-call status, output, error type, and error message.
- Download status and `error_msg`.
- Scrape cloud-sync status and `cloud_sync_error`.
- File-sync state and error.
- Delegation outbox state and result payload.
- Coding-session execution and mirror errors.
- Filesystem scan failures.
- Sync metadata and error messages.
- Background operation states.

Examples are defined in [app/services/local_db/schema.py](/Users/armanisadeghi/code/matrx-local/app/services/local_db/schema.py).

These are not equivalent to a general application log:

- They are feature-specific.
- They may overwrite the previous error.
- They may be cleared after retry.
- Retention differs by feature.
- Some contain only a final message, not a traceback or event timeline.
- They may sync to the cloud as part of normal product-state synchronization.

## 12. What reaches the cloud

I found no generic Matrx Local log/telemetry upload pipeline.

There are no repository dependencies or implementations for:

- Sentry.
- Bugsnag.
- Datadog.
- Honeycomb.
- OpenTelemetry.
- PostHog.
- Segment.
- Centralized `app_log` ingestion from this client.
- Centralized `system_error` submission from this client.

However, ordinary product data and operation state do reach cloud services.

Potential cloud-synchronized error-bearing data includes:

- AI request status/error fields.
- Chat request and tool-call status/error fields.
- Conversation execution metadata.
- Notes and document synchronization states.
- Scrape records and cloud-sync outcomes.
- File-sync operations and delivery state.
- Coding-session metadata and delivery errors.
- Delegation and tool-call state.
- App instance and cloud-sync status.
- Cloud heartbeat/registration failures.
- Server-side AI request logs generated by aidream itself.

These are product records or synchronization metadata, not a complete local-device diagnostic stream.

The generated API types contain aidream administrative concepts such as `system_error` and `app_log`, but those are server-side contracts from the broader platform. I found no corresponding Matrx Local producer that submits every local exception to them.

## 13. What is deliberately excluded

Current logging intentionally excludes or redacts:

- API keys.
- Passwords.
- Bearer tokens.
- Cookies.
- Authorization headers.
- OAuth `code` and `state`.
- Private AI request bodies.
- Prompts and speech payloads in request diagnostics.
- Large binary payloads.
- Full response bodies.
- Most user content.

This is correct for privacy and security, but it creates a debugging tradeoff: many failures can be identified only from metadata and stack traces, not by replaying the exact user input.

Diagnostic snapshots may still contain:

- Local filesystem paths.
- Usernames.
- Process command lines.
- Workspace names.
- Ports.
- Environment configuration names and non-secret values.
- Thread stack contents.
- Service URLs.
- Machine and operating-system information.

## 14. Dev versus installed app

Current separation is:

| World | Engine home | Ports | Logs |
|---|---|---:|---|
| Installed/live | `~/.matrx` | 22140–22159 | OS-native log directory |
| Source/dev | `~/.matrx-dev` | 22240–22259 | Python normally under repository `system/logs`; Tauri lifecycle uses OS-native path |
| Smoke | `.smoke/runs/<id>/matrx-home` | 23000–65000 | `.smoke/runs/<id>/` |

Source dev runs also default to:

- No orphan scanning.
- A salted instance ID.
- Cloud coordination disabled.
- Separate database/discovery/settings state.

This avoids dev engines stealing cloud work or overwriting the installed app’s discovery state.

The main observability weakness is that logs are not uniformly colocated across dev components. Python, Tauri, smoke, and packaged output can all live in different locations.

## 15. Current repository triage ledgers

`CURRENT_ERRORS.md` is a temporary inbox for pasted user log exports. It is explicitly not an archive.

`FOUND_DEFECTS.md` is an evidence-backed holding area for discovered defects.

These documents are useful for agent workflow, but:

- They require manual input.
- They do not receive errors automatically.
- They are not user/device telemetry.
- They do not provide fleet-wide counts.
- They do not guarantee every error is assigned a durable owner.
- An empty inbox does not mean the application is error-free.

## Highest-priority gaps

### P0/P1 gaps

1. No centralized opt-in error reporting for all users.
2. No durable universal event ID/correlation ID spanning React → Tauri → Python → cloud.
3. No global browser `unhandledrejection` capture.
4. No durable React log file.
5. No durable llama-server stdout/stderr file.
6. Rust panic/crash collection is not app-owned.
7. Process kills can be detected later but not attributed.
8. Background-task failures are not guaranteed to enter the launcher registry or create diagnostic snapshots.
9. Dev Tauri logs and dev Python logs are split.
10. Smoke tests are not release-blocking.
11. No fleet-wide version/error recurrence dashboard.
12. No automatic attachment of diagnostic snapshots to an incident record.
13. No retention policy covering all local logs, databases, screenshots, and diagnostic files together.
14. No guaranteed “every error has an owner” workflow enforced by code.

### P2 gaps

- Structured JSON logging instead of mostly human-readable strings.
- Explicit severity taxonomy separating expected state, degraded state, user-action-needed state, and defects.
- Correlation IDs across every request and background job.
- Durable event journal for lifecycle and background operations.
- Automatic diagnostic bundle generation.
- User-controlled “Send diagnostic report” workflow with preview/redaction.
- Centralized recurrence/fingerprint aggregation.
- Release/version/build identity attached to every event.
- Automated canary tests that force each major failure class.
- A single diagnostic command that collects live, dev, Tauri, engine, and smoke evidence.

## Current local files observed in this checkout

The current repository dev log directory contains approximately:

- `system.log`: 8.2 MB.
- Five rotated system logs at roughly 10 MB each.
- `access.log`: 4.4 MB.
- Two rotated access logs at roughly 10 MB each.
- Several screenshots and browser artifacts.

This is local development evidence, not evidence of what every installed user has.

## Bottom line

Matrx Local currently has a good forensic foundation for failures that remain inside the Python service registry and packaged engine lifecycle. It is not yet a complete observability platform.

The biggest strategic decision is whether to add a privacy-preserving, opt-in, centralized diagnostic pipeline that captures only structured metadata and sanitized failures, while keeping prompts, credentials, raw content, and local files out by default. That would close the largest gap: today, most failures remain isolated on the user’s machine until someone manually copies an Activity report.