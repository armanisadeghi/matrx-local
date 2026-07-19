# Matrx Local — Download System Audit & Fix Plan

> **Status:** open. Authored 2026-05-04 after a full audit. Tracked via `FOUND_DEFECTS.md` MXL-D-012/013/014 (holding) until promoted into `.matrx/AGENT_TASKS.md`.
> Implementation is phased — Phase 1 alone unblocks the most user-visible breakage.
> All references use `path:line` format.

---

## TL;DR

The download stack is genuinely broken in a way that explains the "always freezes and crashes and you never know where the actual download process is at" complaint. There are **two unified managers (Rust + Python)** that both **discard the bytes they download** because the read loop counts bytes and clears the buffer with no `file.write_all` in between. **Eleven** independent download sites total, each implementing some subset of the right pattern, none implementing all of it. Progress reporting fires through **four uncoordinated channels** (`dm-progress` Tauri events, `/downloads/stream` SSE, custom SSE in `setup_routes`, and on-progress callbacks).

The fix is a phased consolidation onto one architecture: **one canonical downloader that writes-and-counts in the same loop, one event channel, one queue with elastic bandwidth-aware concurrency, one UI panel that shows the active set + the pending queue + per-download stage.**

### Startup policy (2026-07-18)

Model downloads are user-initiated and never begin or resume automatically
during app startup. Both managers persist an interrupted model row as a failed
history item with an explicit Retry path; only non-model background transfers
(for example file sync) may re-enter the worker queue automatically.

Startup/reconnect hydration is also distinguished from live transfer events.
A persisted failure replay logs as `Previous failure restored` at warning
severity, including its original `updated_at`; only a failure emitted by a live
transfer is logged as an error. This prevents old LLM validation failures from
looking like new multi-gigabyte downloads were attempted during launch.

---

## Audit — every download site

| # | Location | Downloads | Writes file? | Manager? | Progress style |
|---|----------|-----------|--------------|----------|----------------|
| 1 | [`desktop/src-tauri/src/downloads/manager.rs:download_part:993`](desktop/src-tauri/src/downloads/manager.rs) | Any URL via `enqueue` | **❌ NO — bytes vanish** | Is the manager | Tauri `dm-progress` |
| 2 | [`desktop/src-tauri/src/llm/commands.rs:cmd_download_model:330`](desktop/src-tauri/src/llm/commands.rs) | LLM models (multi-part GGUF) | ✅ via `dm.register_external` + caller writes | Manager-as-progress, NOT downloader | `dm-progress` + legacy `llm-download-progress` |
| 3 | [`desktop/src-tauri/src/transcription/downloader.rs:download_model:42`](desktop/src-tauri/src/transcription/downloader.rs) | Whisper from HF | ✅ `tokio::fs::File::create` + `write_all` per chunk | ❌ standalone | callback only |
| 4 | [`desktop/src-tauri/src/transcription/wake_word.rs`](desktop/src-tauri/src/transcription/wake_word.rs) | (reuses Whisper models) | n/a | n/a | n/a |
| 5 | [`app/services/downloads/manager.py:_download_part:643`](app/services/downloads/manager.py) | Any URL via `enqueue` | **❌ NO — bytes vanish** | Is the manager | SSE `/downloads/stream` |
| 6 | [`app/services/tts/service.py:download_model:212`](app/services/tts/service.py) | Kokoro TTS ONNX + voices | ✅ `with open(.tmp,"wb")` + atomic rename | partial — emits `dm-progress`-like events | Custom emitter + SSE |
| 7 | [`app/services/wake_word/models.py:download_model:129`](app/services/wake_word/models.py) | openWakeWord ONNX | ✅ inline write | ❌ standalone | callback only |
| 8 | [`app/api/setup_routes.py:_download_transcription_model:833`](app/api/setup_routes.py) | GGML Whisper from HF | ✅ inline write + atomic rename | ❌ runs inside SSE stream | Custom SSE |
| 9 | [`app/api/setup_routes.py:_download_tts_model:925`](app/api/setup_routes.py) | TTS (delegates to #6) | ✅ via #6 | partial | Custom SSE |
| 10 | [`app/api/setup_routes.py:_download_cloudflared:1028`](app/api/setup_routes.py) | cloudflared binary | ✅ inline write | ❌ standalone | Custom SSE |
| 11 | [`app/api/wake_word_routes.py:download_model_with_progress:251`](app/api/wake_word_routes.py) | OWW (delegates to #7) | ✅ via #7 | ❌ | Custom SSE |
| 12 | [`app/tools/tools/transfer.py:tool_download_file:16`](app/tools/tools/transfer.py) | Arbitrary user URLs | ✅ inline write | ❌ standalone | **silent — no progress at all** |

### Bug clusters

**Cluster A — managers that don't manage** (sites #1, #5)
Both the Rust and Python "unified download managers" have the same structural bug. The pattern in both:
```rust
buf.extend_from_slice(&chunk);
if buf.len() < chunk_size { continue; }   // Bug #2 — UI freezes for slow connections
let consumed = buf.len() as u64;
buf.clear();                              // Bug #1 — bytes are GONE
part_bytes_done += consumed;
emit_progress(part_bytes_done);
```

Two distinct bugs:

1. **No file write.** `buf.clear()` runs without ever writing to disk. The progress bar reports network reads accurately, but the file never lands. Consumers (LLM loader, Whisper loader, etc.) then can't find the file → hang or crash.
2. **Threshold buffering freezes UI.** The `if buf.len() < chunk_size { continue }` accumulates 20+ network chunks silently before emitting. On a slow connection users see frozen progress for 30+ seconds at a time.

These are why some downloads "work" (they go through standalone downloaders #3, #6, #7, #8, #10 that do their own writes) and some never finish (anything routed through the unified manager).

**Cluster B — silent or callback-only downloads** (#3, #4, #7, #11, #12)
These work but emit progress only through callbacks or not at all. The user has zero visibility into them from the global download panel. The `transfer` tool (#12) is the worst: pure silence even on multi-GB files.

**Cluster C — duplicated and uncoordinated managers**
Sites #1 and #5 are independent managers with independent SQLite stores (`~/.matrx/downloads.db` for Rust, separate file for Python). Same download enqueued on both sides = duplicate work + conflicting DB state. There is no source of truth for "what's downloading."

**Cluster D — four event channels**
- Tauri `dm-progress` (#1, #2)
- SSE `/downloads/stream` (#5, partially #6)
- SSE `/setup/install` (#8, #9, #10) — entirely separate stream
- `on_progress` callback (#3, #7) — never reaches the UI

The frontend listens to two of these ([`DownloadManagerContext.tsx:230-313`](desktop/src/contexts/DownloadManagerContext.tsx)) but the other two bypass it. Users see partial state.

**Cluster E — false 0% percent**
Multiple sites do `content_length().unwrap_or(0)` then divide by it for percent. HF, Cloudfront, and other CDNs frequently send chunked transfer encoding with no Content-Length, so percent stays at 0.0 forever even as bytes accumulate.

---

## Target architecture

### One downloader, one queue, one event stream

```
┌─────────────────────────────────────────────────────────────┐
│                       DownloadOrchestrator                   │
│  ┌──────────────┐                  ┌──────────────────────┐ │
│  │ Priority Q   │ ──pop──►  Slots  │ ActiveDownload tasks │ │
│  └──────────────┘ ◄──result──┐     │ (1 primary + N elastic)│
│         ▲                    │     └──────────────────────┘ │
│         │ enqueue()          │                  │           │
│         │                    │                  │ progress  │
│         │                    │                  ▼           │
│  ┌──────┴──────┐  callbacks  │     ┌──────────────────────┐ │
│  │ 11 callers  │             ◄──── │ event broadcaster    │ │
│  │ (one API)   │                   └──────────────────────┘ │
│  └─────────────┘                              │             │
└─────────────────────────────────────────────────────────────┘
                                                ▼
                                  ┌─────────────────────────┐
                                  │  Tauri events + SSE     │
                                  │   (single channel)      │
                                  └─────────────────────────┘
                                                ▼
                                       DownloadManagerContext
                                          (frontend, unchanged)
```

### Canonical download loop — the only one that should exist

```rust
// Rust — desktop/src-tauri/src/downloads/manager.rs
async fn download_one(
    spec: DownloadSpec,
    cancel: Arc<AtomicBool>,
    on_progress: impl Fn(u64) + Send,
) -> Result<u64, DownloadError> {
    let mut file = tokio::fs::File::create(&spec.target_path).await?;   // OPEN FILE FIRST
    let resp = client.get(&spec.url).send().await?;
    let mut stream = resp.bytes_stream();
    let mut written: u64 = 0;
    let mut last_emit_bytes: u64 = 0;
    let mut last_emit_at = Instant::now();

    while let Some(item) = stream.next().await {
        if cancel.load(SeqCst) { break; }
        let chunk = item?;
        file.write_all(&chunk).await?;          // 1. WRITE
        written += chunk.len() as u64;          // 2. COUNT
        if written - last_emit_bytes >= EMIT_BYTES
            || last_emit_at.elapsed() >= EMIT_INTERVAL {
            last_emit_bytes = written;
            last_emit_at = Instant::now();
            on_progress(written);               // 3. EMIT
        }
    }
    file.flush().await?;
    on_progress(written);                       // final
    Ok(written)
}
```

Same shape in Python — see [`aidream/packages/matrx-legal/matrx_legal/courtlistener/bulk/s3_dumps.py:download_dump`](../../aidream/packages/matrx-legal/matrx_legal/courtlistener/bulk/s3_dumps.py) for the canonical reference. **No threshold buffer. No `continue`. Three steps in this exact order: write, count, emit.**

### Concurrency model — elastic with primary protection

The user's intuition is right that source-side rate limits make parallel downloads useful — but the implementation isn't "shift focus when primary slows," it's "open secondary slots when bandwidth indicates the source is the bottleneck."

**Slot model:**
- 1 **primary slot** — always runs the highest-priority queued item.
- 0–N **secondary slots** — run the next items in priority order, gated by bandwidth.

**Slot expansion algorithm** (already partially in [`manager.rs:should_expand_slots:267`](desktop/src-tauri/src/downloads/manager.rs) — reuse and fix):

```
Every 5 s, after at least 30 s of measured throughput:
  let aggregate = sum(active_download.speed_bps for active_download in active_set)
  let peak = max(peak, aggregate)
  if active_count < MAX_SLOTS
     and aggregate < BANDWIDTH_UTILISATION_THRESHOLD * peak
     and time_since_last_expansion >= 10 s:
       active_count += 1
       dispatch_next_from_queue()
       last_expansion_at = now
```

`BANDWIDTH_UTILISATION_THRESHOLD = 0.80`. `MAX_SLOTS = 4`. These match the existing constants — just need them to actually fire.

**Why this is correct:**
- TCP gives each connection a fair share. We can't "give bandwidth back to the primary" — but we don't need to. If primary's source is the bottleneck (HF rate-limits per connection), primary uses what it can and the rest sits idle. Opening a secondary connection from a *different* source uses the slack with zero cost to primary.
- If primary's source is fast and we ARE saturating the local pipe, opening another connection just contends with primary. The threshold check (`aggregate < 80% of peak`) detects exactly this and refuses to expand when we're already maxed out.
- The cooldown prevents thrashing — we don't add a slot, see aggregate jump, then immediately remove it.

**Pause semantics:** if user pins a primary as "exclusive" (e.g., critical LLM download), we lock `MAX_SLOTS = 1` for the duration. Default off. Surface as a UI toggle.

**Same-source detection (optional v2):** parse hostname from URL; if N active downloads share a host, hold off on expanding to that host. Most CDNs allow multiple connections, but this avoids burning credit budget on rate-limited APIs.

### One event channel

Drop the four channels down to one. Both sides emit the same envelope shape:

```typescript
// All download events flow through the single event stream
type DownloadEvent =
  | { event: "queued";    download: DownloadEntry }
  | { event: "started";   id: string }
  | { event: "progress";  id: string; bytes_done: number; total_bytes: number | null;
                          speed_bps: number; eta_seconds: number | null;
                          stage: "downloading" | "verifying" | "extracting" | "moving" }
  | { event: "completed"; id: string; bytes_done: number; final_path: string }
  | { event: "failed";    id: string; error: string; bytes_done: number }
  | { event: "cancelled"; id: string; bytes_done: number }
  | { event: "queue_state"; active: string[]; pending: string[]; aggregate_speed_bps: number };
```

- **Rust manager** publishes via Tauri events (existing channel name `dm-progress` etc., kept for compatibility — but payload shape standardised).
- **Python sidecar** publishes the same events via the existing `/downloads/stream` SSE.
- **`DownloadManagerContext`** merges both into a single `Map<id, DownloadEntry>`. (It already does this — just needs the event vocabulary tightened.)

The `setup_routes` `/setup/install` SSE goes away as a separate stream. Setup downloads enqueue through the same orchestrator and the wizard subscribes to the same event channel filtered by `category == "setup"`.

The `transfer` tool's silent download (#12) starts emitting events through the orchestrator. Even if the user doesn't have the panel open, the events feed the run history.

### Heartbeat decoupling

In addition to byte-threshold-driven `progress` events, the orchestrator emits a `progress` event **every 2 seconds** for every active download — same `bytes_done` value as the last emit if nothing has moved. This is what makes stalls visible. The frontend's `last_event_at` watcher already exists in `DownloadManagerContext.tsx` ([line 58 — `LOG_INTERVAL_MS = 15_000`](desktop/src/contexts/DownloadManagerContext.tsx) — different purpose but same shape) — extend to flip a download to `stalled` after 2× heartbeat with no `bytes_done` change.

---

## Frontend — the queue panel users actually need

Current `DownloadManagerModal.tsx` shows downloads but doesn't distinguish queue position. After the fix:

```
┌─ Downloads ──────────────────────────────────────────── [×] ─┐
│                                                              │
│ 🟢 ACTIVE  (3 of 4 slots)               ↓ 142 MB/s aggregate │
│ ┌──────────────────────────────────────────────────────┐    │
│ │ ⬇ Mistral-Nemo-Q4_K_M.gguf  [primary]               │    │
│ │   ████████████████░░░░░░░░  6.2 / 12.4 GB · 50%     │    │
│ │   84 MB/s · ETA 1m 14s · downloading                 │    │
│ └──────────────────────────────────────────────────────┘    │
│ ┌──────────────────────────────────────────────────────┐    │
│ │ ⬇ whisper-large-v3.bin                               │    │
│ │   ████░░░░░░░░░░░░░░░░░░░░  240 / 3120 MB · 7%      │    │
│ │   38 MB/s · ETA 1m 16s · downloading                 │    │
│ └──────────────────────────────────────────────────────┘    │
│ ┌──────────────────────────────────────────────────────┐    │
│ │ ⬇ kokoro-voices-v1.0.bin                            │    │
│ │   ██████████████████████░░  280 / 337 MB · 83%      │    │
│ │   20 MB/s · ETA 3s · downloading                     │    │
│ └──────────────────────────────────────────────────────┘    │
│                                                              │
│ ⏸ PENDING  (4 in queue)                                     │
│   ↳ #4  llama-3.3-70B-Q5_K_M.gguf      48.7 GB              │
│   ↳ #5  hf-tunnel-binary               12.4 MB              │
│   ↳ #6  hunyuan-dit-v1.2.safetensors   8.3 GB               │
│   ↳ #7  bert-base-uncased              440 MB               │
│                                                              │
│ ✓ COMPLETED  (12 in last hour)                  show all →   │
│ ✗ FAILED  (1)                                                │
│                                                              │
│ [pin primary] [pause queue] [reorder]                        │
└──────────────────────────────────────────────────────────────┘
```

**Key UI behaviors:**

1. **Three sections always visible:** active, pending, recent history (last hour by default).
2. **Active items show the live stage** — `downloading | verifying | extracting | moving`. Right now the UI doesn't differentiate; a 90% download that's actually post-processing gets misread as "stuck."
3. **Pending items show queue position + total size** so the user can estimate "how much do I have left to wait."
4. **Aggregate speed at top** gives a sanity check — if it drops below historical peak with multiple actives running, the source is the bottleneck (which is normal). If it drops with one active running, network problem.
5. **Stall detection** — if no `bytes_done` change for >5s, badge changes to amber `stalled (waiting for server)`. After 30s, red `stalled — retrying`.
6. **Per-download cancel + per-download "promote to primary"** (re-prioritize without cancelling).
7. **Pause queue** — finish current actives but don't dispatch new ones. Useful for "I'm about to take a flight."

The existing context (`DownloadManagerContext`) is the right shape — it already merges Tauri + SSE into a single `Map<string, DownloadEntry>`. The work is in adding an `enum stage` field, an `aggregate_speed_bps` field, splitting the rendered view into active/pending/history sections, and updating reducers for the new event vocabulary.

---

## Phased migration

Each phase is independently shippable. Phase 1 alone unblocks the user-visible breakage.

### Phase 1 — Stop losing bytes (P0, 1 day)

Just fix the data-loss bug in both managers. Keep the existing manager-as-progress vs. external-downloader split for now. This is the smallest change that makes downloads actually work.

**Rust:** [`desktop/src-tauri/src/downloads/manager.rs:download_part`](desktop/src-tauri/src/downloads/manager.rs)
- Add `target_path: PathBuf` to `DownloadEntry` struct.
- Open `tokio::fs::File::create(target_path)` before the chunk loop.
- Replace `buf.extend_from_slice(&chunk); if buf.len() < chunk_size { continue }; buf.clear()` with `file.write_all(&chunk).await?` per chunk.
- Drop the threshold buffer entirely — count and emit per chunk, with the existing time/byte throttle on emits.
- `file.flush().await?` after the loop.

**Python:** [`app/services/downloads/manager.py:_download_part`](app/services/downloads/manager.py) — mirror the same change. Use `aiofiles.open(target_path, "wb")` + `await f.write(chunk)`.

**Validation:** sha256 the resulting file against `expected_checksum` if the spec carries one. Surface a `failed` event with `error: "checksum mismatch"` rather than letting downstream silently load garbage.

This phase keeps the rest of the architecture untouched. The existing 11 callers don't change.

### Phase 2 — Fix progress accuracy (P1, 1 day)

In both managers, remove the threshold-accumulator pattern entirely. Emit per chunk, throttled by **(a)** ≥256 KB delta OR **(b)** ≥1 second elapsed, whichever fires first. Drop `if buf.len() < chunk_size { continue }`.

Add the **2-second wall-clock heartbeat** in both managers — emits the current `bytes_done` even if no chunk arrived in the last 2s, so stalls become visible. This is the same pattern used in [aidream/packages/matrx-legal/matrx_legal/courtlistener/bulk/s3_dumps.py](../../aidream/packages/matrx-legal/matrx_legal/courtlistener/bulk/s3_dumps.py) — model on that.

Handle missing `Content-Length`:
- Don't divide by zero. When `total_bytes == 0`, set `percent = null` (NOT 0.0).
- Frontend treats `percent: null` as "indeterminate" — show bytes-downloaded + speed but no progress bar fill.

### Phase 3 — Hook the orphan downloaders into the manager (P1, 2 days)

Make every download flow through the orchestrator. Each orphan site gets the same treatment:

- [#3 transcription downloader](desktop/src-tauri/src/transcription/downloader.rs) → call `dm.enqueue` + `dm.register_external` (the LLM commands pattern, site #2). Keep the actual file write where it is, but report progress to the manager.
- [#7 OWW downloader](app/services/wake_word/models.py) → same.
- [#8/#9/#10 setup_routes inline downloads](app/api/setup_routes.py) → hand off to the Python manager. The setup wizard subscribes to `/downloads/stream` filtered by `category == "setup"`.
- [#12 transfer tool](app/tools/tools/transfer.py) → enqueue with category `"tool"`. The user sees tool-driven downloads in the same panel as everything else.

Result: every download in the system fires `dm-progress` / `/downloads/stream` events. The frontend panel becomes the actual single source of truth.

### Phase 4 — Unify the two managers (P2, 3 days)

The Rust and Python managers persist to separate SQLite files and emit to separate channels. Merge them so the React frontend doesn't need to maintain two parallel reducers.

Approach: **the Rust manager is the only authoritative one.** The Python sidecar's manager becomes a thin RPC client of the Rust manager:
- Python `enqueue()` → Tauri command → Rust `enqueue()`
- Rust `dm-progress` event → bridge re-emits as Python SSE event for any non-Tauri context (e.g., remote scraper subscribers)
- Single SQLite store (the Rust one).

This eliminates the duplicate-DB-state risk and removes 1,000 lines of Python download manager code. Keep the FastAPI route surface (`/downloads/stream`, `POST /downloads`) so external clients don't break — they just become RPC fronts.

**Alternative approach if Tauri ↔ sidecar IPC is undesirable:** keep both managers, but make the Python one a strict subset (no SQLite, in-memory only) and have the React frontend treat the Rust events as the source of truth. Python events are advisory only and tagged with `source: "python-sidecar"` so the frontend can deduplicate by `id`.

### Phase 5 — Elastic concurrency + priority queue (P2, 2 days)

The Rust manager already has the *bones* of this in `should_expand_slots:267` — the `BANDWIDTH_UTILISATION_THRESHOLD` and `peak_speed_bps` tracking. But it doesn't actually expand slots in practice (the `update_bandwidth()` path is fragile and the cooldown logic doesn't work as intended). Rebuild as described in **Concurrency model** above:

- 1 primary + 0..N secondary slots (`MAX_SLOTS = 4` default, configurable per-user).
- Sample aggregate throughput every 5s after a 30s warm-up.
- Expand when aggregate < 80% peak, with 10s cooldown.
- Contract when aggregate exceeds peak_speed by more than 10% (we're saturating).
- Per-priority dispatch — primary always runs first; ties broken by enqueue time.

Add **same-source backoff** as a follow-up: track host of each active download; don't expand to a host that already has 2+ actives. (This is conservative; HF actually allows ~6 parallel per IP.)

### Phase 6 — Frontend queue panel (P2, 2 days)

[`DownloadManagerModal.tsx`](desktop/src/components/downloads/DownloadManagerModal.tsx) — re-render with three sections: active / pending / history, as in the mockup above. Add stage badge (`downloading / verifying / extracting / moving`), aggregate-speed strip at the top, "promote to primary" + "pause queue" controls. Stall detection per-download (amber after 5s no progress, red after 30s).

---

## Concrete next-step tasks (propose for `.matrx/AGENT_TASKS.md` after Arman approval)

```
## 🔴 Active Bugs & Regressions

- [ ] **Download manager: bytes never reach disk (P0 data loss)** — Both
      `desktop/src-tauri/src/downloads/manager.rs:download_part:993` and
      `app/services/downloads/manager.py:_download_part:643` read chunks from the
      network, count them, and `buf.clear()`/discard them with no `file.write_all`
      in the loop. LLM/Whisper/TTS downloads that route through these paths "complete"
      with progress hitting 100% but no file on disk → loaders hang or crash. Fix:
      open file before the loop, write each chunk before counting it. See
      docs/DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md (Phase 1).

- [ ] **Download progress freezes on slow connections** — Both managers accumulate
      chunks in a `chunk_size` buffer before counting/emitting. On <1 MB/s
      connections users see frozen progress for 30+ seconds. Fix: drop the
      threshold buffer; emit per-chunk with byte+time throttle. See Phase 2.

- [ ] **Download progress shows 0% on chunked-encoding responses** — All managers
      and standalone downloaders divide by `content_length().unwrap_or(0)`. HF and
      Cloudfront often omit Content-Length → percent stays 0.0 forever. Fix: when
      total is unknown, return `percent: null` and have the frontend show bytes
      only (indeterminate mode). See Phase 2.

- [ ] **Download stalls are invisible** — 60s idle timeout in `manager.rs:1036` with
      no events between. Frontend has no way to distinguish "running slowly" from
      "frozen." Fix: 2s wall-clock heartbeat per active download, frontend marks
      stalled after 2× heartbeat with no bytes_done change. See Phase 2.

## 🟠 Active Bugs & Regressions (P1)

- [ ] **Five download paths bypass the unified manager** —
      `desktop/src-tauri/src/transcription/downloader.rs`,
      `app/services/wake_word/models.py`,
      `app/api/setup_routes.py:_download_transcription_model`,
      `app/api/setup_routes.py:_download_cloudflared`,
      `app/tools/tools/transfer.py`
      all do their own downloads with progress reported via callback or custom SSE
      events. None of them appear in the global download panel; the `transfer` tool
      is fully silent. Fix: each enqueues through the manager, reporting progress
      via `dm.register_external`. See Phase 3.

- [ ] **Two parallel download managers, two SQLite stores** — Rust and Python
      managers persist independently to `~/.matrx/downloads.db` and a separate
      Python file. Same download enqueued on both sides = duplicate work +
      conflicting DB state. Fix: Rust authoritative, Python sidecar becomes RPC
      client. See Phase 4.

## 🟡 Important Missing Features

- [ ] **Queue panel doesn't show pending or aggregate state** — `DownloadManagerModal`
      lists downloads but doesn't separate active from pending or show aggregate
      bandwidth. Users don't know "there are 4 more queued behind this one." Fix:
      three-section layout (active / pending / history) + aggregate-speed strip +
      stage badge per active download. See Phase 6.

- [ ] **Bandwidth-aware concurrency exists in code but never expands** — `manager.rs`
      has `should_expand_slots:267` and `BANDWIDTH_UTILISATION_THRESHOLD` but the
      slot count never grows in practice. Source-rate-limited downloads (HF
      single-connection cap) leave bandwidth on the table. Fix: rebuild expansion
      logic per the doc; 1 primary + 0..N secondary slots, expand when aggregate
      < 80% peak with cooldown. See Phase 5.
```

---

## Anti-patterns to never reintroduce

These are documented in [aidream/.claude/skills/accurate-download-progress.md](../../aidream/.claude/skills/accurate-download-progress.md). The matrx-local code currently violates 4 of the 5:

1. ❌ Counter and writer in different places — Sites #1, #5 (the bug).
2. ❌ Threshold-buffer before counting — Sites #1, #5.
3. ❌ Polling vs. push events — none of the current code does this; don't introduce it.
4. ❌ Trusting `Content-Length` — Sites #1, #2, #5, #6, #8 all have `unwrap_or(0)` percent calculations.
5. ❌ Smoothing/debouncing displayed values — none currently; don't introduce it.

Before merging any download-related PR, walk the eight-item checklist at the bottom of the skill doc.

---

## Rollback / safety

- Phase 1 is a strict bug fix. Worst-case rollback: revert the diff. No DB migrations; no event-schema changes.
- Phase 2 changes the `progress` event payload (adds heartbeat + `percent: null`). Frontend needs deploying together with backend, but old frontend will gracefully ignore unknown fields.
- Phase 3–6 each gate behind feature flags initially (`MATRX_USE_UNIFIED_DOWNLOADS=1` env var). Cut over per-site, validate, then remove the flag. No site needs to migrate before its dependencies do.

---

## References

- This audit's source: full directory walk by an Explore agent on 2026-05-04.
- Reference impl that does this right: [aidream/packages/matrx-legal/matrx_legal/courtlistener/bulk/s3_dumps.py](../../aidream/packages/matrx-legal/matrx_legal/courtlistener/bulk/s3_dumps.py) — same author, ~50 LOC, downloads multi-GB files with byte-accurate progress and stall heartbeats.
- General pattern doc: [aidream/.claude/skills/accurate-download-progress.md](../../aidream/.claude/skills/accurate-download-progress.md).
