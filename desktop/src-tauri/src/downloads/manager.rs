//! Universal download manager (Rust/Tauri side).
//!
//! Provides a concurrent priority-aware download queue that:
//! - Survives app restarts via SQLite persistence at ~/.matrx/downloads.db
//! - Continues running when the UI window is hidden/closed (tokio task, not window-bound)
//! - Runs up to MAX_CONCURRENT downloads in parallel, respecting priority ordering
//! - Emits fine-grained throttled progress via Tauri events: dm-progress, dm-queued,
//!   dm-completed, dm-failed, dm-cancelled
//! - Supports per-download cancellation tokens (one AtomicBool per download ID)
//! - Also emits legacy aliases: llm-download-progress, llm-download-cancelled so existing
//!   hook code keeps working during the transition
//! - Logs full state every 15 seconds via the `log` crate

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use futures_util::StreamExt;
use log::{error, info, warn};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::{Mutex, Semaphore};
use tokio::time::{timeout, Duration};

// ── Constants ──────────────────────────────────────────────────────────────

/// Maximum number of concurrent downloads.
const MAX_CONCURRENT: usize = 3;

/// Minimum bytes changed between two dm-progress events (throttle).
/// Prevents flooding the IPC channel at high speeds.
const PROGRESS_THROTTLE_BYTES: u64 = 256 * 1024; // 256 KB

/// Also emit at least once per second even if bytes threshold not met.
const PROGRESS_THROTTLE_SECS: u64 = 1;

/// How often to log the full queue state.
const LOG_INTERVAL_SECS: u64 = 15;

/// Rolling-window size for speed calculation.
const SPEED_WINDOW_SIZE: usize = 10;

/// Chunk size for primary slot.
const PRIMARY_CHUNK: usize = 65536; // 64 KB

/// Chunk size for secondary slots (less competition with primary).
const SECONDARY_CHUNK: usize = 32768; // 32 KB

/// Bandwidth probe: fraction of peak below which we open another slot.
const BANDWIDTH_UTILISATION_THRESHOLD: f64 = 0.8;

/// Minimum seconds between slot expansions.
const SLOT_EXPAND_COOLDOWN_SECS: u64 = 10;

// ── Types ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum DownloadStatus {
    Queued,
    Active,
    Completed,
    Failed,
    Cancelled,
}

impl DownloadStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            DownloadStatus::Queued => "queued",
            DownloadStatus::Active => "active",
            DownloadStatus::Completed => "completed",
            DownloadStatus::Failed => "failed",
            DownloadStatus::Cancelled => "cancelled",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadEntry {
    pub id: String,
    pub category: String,
    pub filename: String,
    pub display_name: String,
    pub urls: Vec<String>,
    pub total_bytes: u64,
    pub bytes_done: u64,
    pub status: DownloadStatus,
    pub error_msg: Option<String>,
    pub priority: i32,
    pub part_current: usize,
    pub part_total: usize,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

impl DownloadEntry {
    pub fn percent(&self) -> f64 {
        if self.total_bytes == 0 {
            return 0.0;
        }
        (self.bytes_done as f64 / self.total_bytes as f64 * 100.0).min(100.0)
    }
}

/// A pending queue item with sort key for priority ordering.
#[derive(Debug, Clone)]
struct PendingItem {
    /// Negative priority so higher priority sorts first in BTreeMap.
    neg_priority: i32,
    created_at: String,
    id: String,
}

impl PartialEq for PendingItem {
    fn eq(&self, other: &Self) -> bool {
        self.id == other.id
    }
}
impl Eq for PendingItem {}

impl PartialOrd for PendingItem {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for PendingItem {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.neg_priority
            .cmp(&other.neg_priority)
            .then(self.created_at.cmp(&other.created_at))
            .then(self.id.cmp(&other.id))
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct DownloadProgressEvent {
    pub id: String,
    pub category: String,
    pub filename: String,
    pub display_name: String,
    pub status: String,
    pub bytes_done: u64,
    pub total_bytes: u64,
    pub percent: f64,
    pub part_current: usize,
    pub part_total: usize,
    pub speed_bps: f64,
    pub eta_seconds: Option<f64>,
    pub error_msg: Option<String>,
    pub updated_at: String,
    pub bandwidth_bps: f64,
}

/// Rolling-window speed tracker (ring buffer of (instant, bytes_done) samples).
struct SpeedTracker {
    samples: std::collections::VecDeque<(std::time::Instant, u64)>,
}

impl SpeedTracker {
    fn new() -> Self {
        Self {
            samples: std::collections::VecDeque::with_capacity(SPEED_WINDOW_SIZE + 1),
        }
    }

    fn record(&mut self, bytes_done: u64) {
        self.samples.push_back((std::time::Instant::now(), bytes_done));
        while self.samples.len() > SPEED_WINDOW_SIZE {
            self.samples.pop_front();
        }
    }

    fn speed_bps(&self) -> f64 {
        if self.samples.len() < 2 {
            return 0.0;
        }
        let (t0, b0) = self.samples.front().unwrap();
        let (t1, b1) = self.samples.back().unwrap();
        let dt = t1.duration_since(*t0).as_secs_f64();
        if dt <= 0.0 {
            return 0.0;
        }
        let db = b1.saturating_sub(*b0);
        db as f64 / dt
    }
}

// ── Shared concurrency state ────────────────────────────────────────────────

struct ManagerState {
    entries: HashMap<String, DownloadEntry>,
    /// Priority-sorted pending queue (use BTreeSet for O(log n) insert + min).
    pending: std::collections::BTreeSet<PendingItem>,
    /// IDs currently being downloaded.
    active_ids: std::collections::HashSet<String>,
    cancel_flags: HashMap<String, Arc<AtomicBool>>,
    /// Rolling speed trackers keyed by download ID.
    speed_trackers: HashMap<String, SpeedTracker>,
    /// Current effective concurrency (starts at 1, grows with bandwidth probe).
    active_slots: usize,
    /// Peak observed aggregate speed in bytes/sec.
    peak_speed_bps: f64,
    /// Aggregate current speed.
    bandwidth_bps: f64,
    /// Last time we expanded the active_slots.
    last_slot_expand: std::time::Instant,
}

impl ManagerState {
    fn new() -> Self {
        Self {
            entries: HashMap::new(),
            pending: std::collections::BTreeSet::new(),
            active_ids: std::collections::HashSet::new(),
            cancel_flags: HashMap::new(),
            speed_trackers: HashMap::new(),
            active_slots: 1,
            peak_speed_bps: 0.0,
            bandwidth_bps: 0.0,
            last_slot_expand: std::time::Instant::now(),
        }
    }

    fn insert_pending(&mut self, id: String, priority: i32, created_at: String) {
        self.pending.insert(PendingItem {
            neg_priority: -priority,
            created_at,
            id,
        });
    }

    fn pop_next_pending(&mut self) -> Option<String> {
        let item = self.pending.iter().next().cloned()?;
        self.pending.remove(&item);
        Some(item.id)
    }

    fn remove_pending(&mut self, id: &str) {
        self.pending.retain(|item| item.id != id);
    }

    fn update_bandwidth(&mut self) {
        let total: f64 = self
            .active_ids
            .iter()
            .filter_map(|id| self.speed_trackers.get(id))
            .map(|t| t.speed_bps())
            .sum();
        self.bandwidth_bps = total;
        if total > self.peak_speed_bps {
            self.peak_speed_bps = total;
        }
    }

    fn should_expand_slots(&self) -> bool {
        if self.active_slots >= MAX_CONCURRENT {
            return false;
        }
        if self.pending.is_empty() {
            return false;
        }
        if self.peak_speed_bps <= 0.0 {
            return false;
        }
        if self.last_slot_expand.elapsed().as_secs() < SLOT_EXPAND_COOLDOWN_SECS {
            return false;
        }
        self.bandwidth_bps < BANDWIDTH_UTILISATION_THRESHOLD * self.peak_speed_bps
    }
}

// ── DownloadManager ────────────────────────────────────────────────────────

pub struct DownloadManager {
    db_path: PathBuf,
    state: Arc<Mutex<ManagerState>>,
    semaphore: Arc<Semaphore>,
}

impl DownloadManager {
    /// Create and start the manager. Call once from lib.rs setup.
    pub fn new(app: AppHandle, db_path: PathBuf) -> Arc<Self> {
        let state = Arc::new(Mutex::new(ManagerState::new()));
        // Start with 1 permit; _maybe_expand_slots() adds permits up to MAX_CONCURRENT
        // as bandwidth headroom is detected, matching the Python manager's behavior.
        let semaphore = Arc::new(Semaphore::new(1));

        let mgr = Arc::new(DownloadManager {
            db_path: db_path.clone(),
            state: state.clone(),
            semaphore: semaphore.clone(),
        });

        // Initialize DB schema
        if let Err(e) = init_db(&db_path) {
            error!("[downloads] DB init error: {}", e);
        }

        // Start dispatcher task
        let mgr_clone = Arc::clone(&mgr);
        let app_clone = app.clone();
        tauri::async_runtime::spawn(async move {
            mgr_clone.dispatcher(app_clone).await;
        });

        // Start periodic state-log task
        let state_clone = Arc::clone(&mgr.state);
        let db_path_clone = mgr.db_path.clone();
        tauri::async_runtime::spawn(async move {
            periodic_state_log(state_clone, db_path_clone).await;
        });

        // Re-queue incomplete downloads from previous session
        let mgr_clone2 = Arc::clone(&mgr);
        let app_clone2 = app.clone();
        tauri::async_runtime::spawn(async move {
            mgr_clone2.resume_incomplete(app_clone2).await;
        });

        mgr
    }

    /// Register an externally-managed download (already being downloaded by other code).
    pub async fn register_external(
        &self,
        app: &AppHandle,
        id: String,
        category: String,
        filename: String,
        display_name: String,
        urls: Vec<String>,
    ) -> DownloadEntry {
        {
            let state = self.state.lock().await;
            if let Some(existing) = state.entries.get(&id) {
                if matches!(
                    existing.status,
                    DownloadStatus::Active | DownloadStatus::Queued | DownloadStatus::Completed
                ) {
                    return existing.clone();
                }
            }
        }

        let now = now_str();
        let part_total = urls.len().max(1);
        let entry = DownloadEntry {
            id: id.clone(),
            category,
            filename,
            display_name,
            urls,
            total_bytes: 0,
            bytes_done: 0,
            status: DownloadStatus::Active,
            error_msg: None,
            priority: 0,
            part_current: 1,
            part_total,
            created_at: now.clone(),
            updated_at: now.clone(),
            completed_at: None,
        };

        if let Err(e) = db_upsert(&self.db_path, &entry, None) {
            error!("[downloads] DB upsert (external) failed for {}: {}", entry.filename, e);
        }
        {
            let mut state = self.state.lock().await;
            state.cancel_flags.insert(id.clone(), Arc::new(AtomicBool::new(false)));
            state.active_ids.insert(id.clone());
            state.entries.insert(id.clone(), entry.clone());
        }

        let ev = build_event(&entry, 0.0, None, 0.0, &now);
        let _ = app.emit("dm-progress", &ev);
        entry
    }

    /// Mark an externally-managed download as completed.
    pub async fn mark_external_completed(&self, app: &AppHandle, id: &str, total_bytes: u64) {
        let entry_clone = {
            let mut state = self.state.lock().await;
            // Mutate entry fields first, then clone before touching other state fields.
            {
                let Some(e) = state.entries.get_mut(id) else { return };
                if matches!(e.status, DownloadStatus::Cancelled) {
                    // The user cancelled while the external transfer was
                    // finishing — keep the Cancelled record.
                    return;
                }
                e.status = DownloadStatus::Completed;
                e.bytes_done = total_bytes;
                if total_bytes > 0 {
                    e.total_bytes = total_bytes;
                }
                e.completed_at = Some(now_str());
                e.updated_at = now_str();
            }
            state.active_ids.remove(id);
            state.entries.get(id).cloned().unwrap()
        };
        let _ = db_update_status(&self.db_path, id, "completed", None);
        let now = now_str();
        let ev = build_event(&entry_clone, 100.0, None, 0.0, &now);
        let _ = app.emit("dm-completed", &ev);
        let _ = app.emit("dm-progress", &ev);
        info!("[downloads] External completed: {} (id={})", entry_clone.filename, id);
    }

    /// Mark an externally-managed download as failed.
    pub async fn mark_external_failed(&self, app: &AppHandle, id: &str, error_msg: &str) {
        let entry_clone = {
            let mut state = self.state.lock().await;
            {
                let Some(e) = state.entries.get_mut(id) else { return };
                e.status = DownloadStatus::Failed;
                e.error_msg = Some(error_msg.to_string());
                e.updated_at = now_str();
            }
            state.active_ids.remove(id);
            state.entries.get(id).cloned().unwrap()
        };
        let _ = db_update_status(&self.db_path, id, "failed", Some(error_msg));
        let now = now_str();
        let pct = entry_clone.percent();
        let ev = build_event(&entry_clone, pct, None, 0.0, &now);
        let _ = app.emit("dm-failed", &ev);
        let _ = app.emit("dm-progress", &ev);
        error!(
            "[downloads] External FAILED: {} (id={}) — {}",
            entry_clone.filename, id, error_msg
        );
    }

    /// Enqueue a new download. Returns the entry (or existing if already queued/active).
    pub async fn enqueue(
        &self,
        app: &AppHandle,
        id: String,
        category: String,
        filename: String,
        display_name: String,
        urls: Vec<String>,
        priority: i32,
        metadata: Option<String>,
    ) -> Result<DownloadEntry, String> {
        {
            let state = self.state.lock().await;
            // Idempotency by ID
            if let Some(existing) = state.entries.get(&id) {
                if matches!(
                    existing.status,
                    DownloadStatus::Queued | DownloadStatus::Active | DownloadStatus::Completed
                ) {
                    return Ok(existing.clone());
                }
            }
            // Idempotency by filename+category
            for entry in state.entries.values() {
                if entry.filename == filename && entry.category == category {
                    if matches!(
                        entry.status,
                        DownloadStatus::Queued | DownloadStatus::Active | DownloadStatus::Completed
                    ) {
                        return Ok(entry.clone());
                    }
                }
            }
        }

        let now = now_str();
        let part_total = urls.len().max(1);
        let entry = DownloadEntry {
            id: id.clone(),
            category: category.clone(),
            filename: filename.clone(),
            display_name: display_name.clone(),
            urls: urls.clone(),
            total_bytes: 0,
            bytes_done: 0,
            status: DownloadStatus::Queued,
            error_msg: None,
            priority,
            part_current: 1,
            part_total,
            created_at: now.clone(),
            updated_at: now.clone(),
            completed_at: None,
        };

        if let Err(e) = db_upsert(&self.db_path, &entry, metadata) {
            error!("[downloads] DB upsert failed for {}: {}", filename, e);
        }

        {
            let mut state = self.state.lock().await;
            state.cancel_flags.insert(id.clone(), Arc::new(AtomicBool::new(false)));
            state.entries.insert(id.clone(), entry.clone());
            state.insert_pending(id.clone(), priority, now.clone());
        }

        let ev = build_event(&entry, 0.0, None, 0.0, &now);
        let _ = app.emit("dm-queued", &ev);
        let _ = app.emit("dm-progress", &ev);

        info!(
            "[downloads] Enqueued: {} (id={} category={} priority={})",
            filename, id, category, priority
        );
        Ok(entry)
    }

    /// Cancel a download by ID.
    pub async fn cancel(&self, app: &AppHandle, id: &str) -> bool {
        let entry = {
            let mut state = self.state.lock().await;
            // Phase 1: validate and mutate the entry (scoped borrow).
            {
                let Some(entry) = state.entries.get_mut(id) else {
                    return false;
                };
                if !matches!(entry.status, DownloadStatus::Queued | DownloadStatus::Active) {
                    return false;
                }
                entry.status = DownloadStatus::Cancelled;
                entry.updated_at = now_str();
            }
            // Phase 2: mutate other state fields (entry borrow released).
            state.remove_pending(id);
            if let Some(flag) = state.cancel_flags.get(id) {
                flag.store(true, Ordering::SeqCst);
            }
            state.entries.get(id).cloned().unwrap()
        };

        if let Err(e) = db_update_status(&self.db_path, id, "cancelled", None) {
            warn!("[downloads] DB cancel update failed for {}: {}", id, e);
        }

        let now = now_str();
        let pct = entry.percent();
        let ev = build_event(&entry, pct, None, 0.0, &now);
        let _ = app.emit("dm-cancelled", &ev);
        let _ = app.emit("dm-progress", &ev);

        // External (llm/whisper) transfer loops never read the DM's internal
        // cancel flag — they poll their own category atomics. Without this,
        // dm_cancel marked the entry Cancelled while the HTTP transfer kept
        // streaming gigabytes in the background (and completion later flipped
        // the entry back to Completed).
        match entry.category.as_str() {
            "llm" => {
                if let Some(c) = app.try_state::<crate::llm::commands::LlmDownloadCancelState>() {
                    c.0.store(true, Ordering::SeqCst);
                }
            }
            "whisper" => {
                if let Some(c) =
                    app.try_state::<crate::transcription::commands::WhisperDownloadCancelState>()
                {
                    c.0.store(true, Ordering::SeqCst);
                }
            }
            _ => {}
        }

        if entry.category == "llm" {
            let _ = app.emit(
                "llm-download-cancelled",
                serde_json::json!({"reason": "user_cancelled"}),
            );
        }

        info!("[downloads] Cancelled: {} (id={})", entry.filename, id);
        true
    }

    /// Get all entries sorted by status priority then creation time.
    pub async fn list(&self) -> Vec<DownloadEntry> {
        let state = self.state.lock().await;
        let mut list: Vec<DownloadEntry> = state.entries.values().cloned().collect();
        list.sort_by(|a, b| {
            let rank = |s: &DownloadStatus| match s {
                DownloadStatus::Active => 0,
                DownloadStatus::Queued => 1,
                DownloadStatus::Failed => 2,
                DownloadStatus::Cancelled => 3,
                DownloadStatus::Completed => 4,
            };
            rank(&a.status)
                .cmp(&rank(&b.status))
                .then(a.created_at.cmp(&b.created_at))
        });
        list
    }

    // ── Internal: concurrent dispatcher ────────────────────────────────────

    async fn dispatcher(&self, app: AppHandle) {
        loop {
            // Acquire a semaphore permit (blocks until one is free)
            let permit = self.semaphore.clone().acquire_owned().await;
            let permit = match permit {
                Ok(p) => p,
                Err(_) => break, // Semaphore closed
            };

            // Pop the highest-priority pending item
            let dl_id = {
                let mut state = self.state.lock().await;
                state.pop_next_pending()
            };

            let dl_id = match dl_id {
                Some(id) => id,
                None => {
                    // No work; drop permit and wait before polling again
                    drop(permit);
                    tokio::time::sleep(Duration::from_millis(200)).await;
                    continue;
                }
            };

            // Validate and prepare entry — split into two borrows to satisfy the
            // borrow checker: first read+validate, then mutate non-overlapping fields.
            let (entry, cancel_flag, is_primary) = {
                let mut state = self.state.lock().await;

                // Phase 1: validate and clone the entry (releases the &mut entries borrow).
                let entry_clone = {
                    let Some(entry) = state.entries.get(&dl_id) else {
                        drop(permit);
                        continue;
                    };
                    if matches!(
                        entry.status,
                        DownloadStatus::Cancelled | DownloadStatus::Completed
                    ) {
                        drop(permit);
                        continue;
                    }
                    entry.clone()
                };

                let flag = state
                    .cancel_flags
                    .get(&dl_id)
                    .cloned()
                    .unwrap_or_else(|| Arc::new(AtomicBool::new(false)));
                if flag.load(Ordering::SeqCst) {
                    drop(permit);
                    continue;
                }
                let is_primary = state.active_ids.is_empty();

                // Phase 2: mutate state fields now that entries borrow is released.
                if let Some(e) = state.entries.get_mut(&dl_id) {
                    e.status = DownloadStatus::Active;
                    e.updated_at = now_str();
                }
                state.active_ids.insert(dl_id.clone());
                state.speed_trackers.insert(dl_id.clone(), SpeedTracker::new());

                (entry_clone, flag, is_primary)
            };

            let chunk_size = if is_primary {
                PRIMARY_CHUNK
            } else {
                SECONDARY_CHUNK
            };

            let _ = db_update_status(&self.db_path, &dl_id, "active", None);
            // Extract data needed for the event, then drop the lock BEFORE emitting
            // to avoid a potential deadlock if a Tauri command handler acquires state.
            let ev = {
                let state = self.state.lock().await;
                let bw = state.bandwidth_bps;
                let now = now_str();
                build_event(&entry, entry.percent(), None, bw, &now)
            };
            let _ = app.emit("dm-progress", &ev);

            // Spawn the download as an independent task; permit moves in so it's
            // released when the task finishes.
            let mgr = Arc::new(DownloadSlotHandle {
                db_path: self.db_path.clone(),
                state: Arc::clone(&self.state),
                semaphore: Arc::clone(&self.semaphore),
            });
            let app_clone = app.clone();
            tauri::async_runtime::spawn(async move {
                mgr.run(app_clone, entry, cancel_flag, chunk_size).await;
                drop(permit);
            });
        }
    }

    async fn resume_incomplete(&self, app: AppHandle) {
        let rows = match db_load_incomplete(&self.db_path) {
            Ok(r) => r,
            Err(e) => {
                error!("[downloads] resume_incomplete DB error: {}", e);
                return;
            }
        };

        for row in rows {
            let id = row.id.clone();

            // LLM and Whisper downloads are externally managed — mark failed so UI
            // re-offers the download instead of spinning in a queued state forever.
            if row.category == "llm" || row.category == "whisper" {
                warn!(
                    "[downloads] resume_incomplete: {} (category='{}') was interrupted — marking failed so UI re-offers.",
                    row.filename, row.category
                );
                let _ = db_update_status(
                    &self.db_path,
                    &id,
                    "failed",
                    Some("Interrupted: app was closed mid-download. Please re-download."),
                );
                continue;
            }

            let mut entry = row;
            entry.status = DownloadStatus::Queued;
            entry.bytes_done = 0;
            entry.part_current = 1;
            entry.updated_at = now_str();

            let _ = db_update_status(&self.db_path, &id, "queued", None);

            {
                let mut state = self.state.lock().await;
                state
                    .cancel_flags
                    .insert(id.clone(), Arc::new(AtomicBool::new(false)));
                state.insert_pending(id.clone(), entry.priority, entry.created_at.clone());
                state.entries.insert(id.clone(), entry.clone());
            }

            let now = now_str();
            let ev = build_event(&entry, 0.0, None, 0.0, &now);
            let _ = app.emit("dm-queued", &ev);
            let _ = app.emit("dm-progress", &ev);
            info!(
                "[downloads] Resuming: {} (id={} priority={})",
                entry.filename, id, entry.priority
            );
        }
    }
}

// ── Download slot handle (moved into spawned tasks) ────────────────────────

struct DownloadSlotHandle {
    db_path: PathBuf,
    state: Arc<Mutex<ManagerState>>,
    semaphore: Arc<Semaphore>,
}

impl DownloadSlotHandle {
    async fn run(
        &self,
        app: AppHandle,
        entry: DownloadEntry,
        cancel_flag: Arc<AtomicBool>,
        chunk_size: usize,
    ) {
        let id = entry.id.clone();
        match self.download(&app, entry, cancel_flag, chunk_size).await {
            Ok(()) => {}
            Err(e) => {
                // Extract state data, then drop the lock before emitting events.
                let (failed_entry, bw) = {
                    let mut state = self.state.lock().await;
                    state.active_ids.remove(&id);
                    state.speed_trackers.remove(&id);
                    state.update_bandwidth();
                    let Some(e_ref) = state.entries.get_mut(&id) else {
                        return;
                    };
                    if matches!(e_ref.status, DownloadStatus::Active) {
                        e_ref.status = DownloadStatus::Failed;
                        e_ref.error_msg = Some(e.clone());
                        e_ref.updated_at = now_str();
                    }
                    (e_ref.clone(), state.bandwidth_bps)
                }; // lock drops here

                let _ = db_update_status(&self.db_path, &id, "failed", Some(&e));
                let now = now_str();
                let pct = failed_entry.percent();
                let ev = build_event(&failed_entry, pct, None, bw, &now);
                let _ = app.emit("dm-failed", &ev);
                let _ = app.emit("dm-progress", &ev);

                error!(
                    "[downloads] FAILED: {} (id={} category={} bytes_done={} total_bytes={} part={}/{}) — {}",
                    failed_entry.filename,
                    id,
                    failed_entry.category,
                    failed_entry.bytes_done,
                    failed_entry.total_bytes,
                    failed_entry.part_current,
                    failed_entry.part_total,
                    e
                );

                // Check bandwidth probe expansion after lock is free.
                {
                    let mut state = self.state.lock().await;
                    if state.should_expand_slots() {
                        state.active_slots += 1;
                        state.last_slot_expand = std::time::Instant::now();
                        // Add a semaphore permit so the dispatcher can launch one more
                        // concurrent download — mirrors the Python manager behavior.
                        self.semaphore.add_permits(1);
                        info!(
                            "[downloads] Expanding concurrency to {} slots (bw={:.0} peak={:.0})",
                            state.active_slots, state.bandwidth_bps, state.peak_speed_bps
                        );
                    }
                }
            }
        }
    }

    async fn download(
        &self,
        app: &AppHandle,
        mut entry: DownloadEntry,
        cancel_flag: Arc<AtomicBool>,
        chunk_size: usize,
    ) -> Result<(), String> {
        let id = entry.id.clone();

        if entry.urls.is_empty() {
            return Err("No URLs provided".to_string());
        }

        let hf_token = get_hf_token_from_app(app);

        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(30))
            .timeout(Duration::from_secs(7200))
            .build()
            .map_err(|e| e.to_string())?;

        let total_bytes = probe_total_bytes(&client, &entry.urls, hf_token.as_deref()).await;
        if total_bytes > 0 {
            let mut state = self.state.lock().await;
            if let Some(e) = state.entries.get_mut(&id) {
                e.total_bytes = total_bytes;
            }
            entry.total_bytes = total_bytes;
        }

        let part_total = entry.urls.len();
        let mut bytes_before: u64 = 0;

        for (part_idx, url) in entry.urls.clone().iter().enumerate() {
            if cancel_flag.load(Ordering::SeqCst) {
                self.mark_cancelled(app, &id).await;
                return Ok(());
            }

            let part_num = part_idx + 1;
            {
                let mut state = self.state.lock().await;
                if let Some(e) = state.entries.get_mut(&id) {
                    e.part_current = part_num;
                }
            }

            let mut last_error = String::new();
            let mut success = false;

            for attempt in 0..3u32 {
                if cancel_flag.load(Ordering::SeqCst) {
                    self.mark_cancelled(app, &id).await;
                    return Ok(());
                }
                if attempt > 0 {
                    let wait = 2u64.pow(attempt);
                    warn!(
                        "[downloads] Retry {}/{} for {} part {} in {}s (last error: {})",
                        attempt + 1,
                        3,
                        entry.filename,
                        part_num,
                        wait,
                        last_error
                    );
                    tokio::time::sleep(Duration::from_secs(wait)).await;
                }

                match self
                    .download_part(
                        app,
                        &id,
                        &client,
                        url,
                        part_num,
                        part_total,
                        bytes_before,
                        total_bytes,
                        &entry.filename,
                        &entry.display_name,
                        &entry.category,
                        hf_token.as_deref(),
                        &cancel_flag,
                        chunk_size,
                    )
                    .await
                {
                    Ok(part_bytes) => {
                        bytes_before += part_bytes;
                        success = true;
                        break;
                    }
                    Err(e) if e.contains("cancelled") => {
                        self.mark_cancelled(app, &id).await;
                        return Ok(());
                    }
                    Err(e) => {
                        last_error = format!(
                            "Part {}/{} attempt {}: {}",
                            part_num,
                            part_total,
                            attempt + 1,
                            e
                        );
                        warn!("[downloads] {}", last_error);
                    }
                }
            }

            if !success {
                return Err(format!(
                    "Download failed after 3 attempts. Last error: {}",
                    last_error
                ));
            }
        }

        // Mark completed
        let (bw, entry_final) = {
            let mut state = self.state.lock().await;
            state.active_ids.remove(&id);
            state.speed_trackers.remove(&id);
            state.update_bandwidth();

            let maybe_expand = state.should_expand_slots();
            if maybe_expand {
                state.active_slots += 1;
                state.last_slot_expand = std::time::Instant::now();
                // Add a semaphore permit so the dispatcher can launch one more
                // concurrent download — mirrors the Python manager behavior.
                self.semaphore.add_permits(1);
                info!(
                    "[downloads] Expanding concurrency to {} slots (bw={:.0} peak={:.0})",
                    state.active_slots, state.bandwidth_bps, state.peak_speed_bps
                );
            }

            {
                let Some(e) = state.entries.get_mut(&id) else {
                    return Ok(());
                };
                e.status = DownloadStatus::Completed;
                e.bytes_done = if e.total_bytes > 0 {
                    e.total_bytes
                } else {
                    bytes_before
                };
                e.completed_at = Some(now_str());
                e.updated_at = now_str();
            }
            let bw = state.bandwidth_bps;
            let e_clone = state.entries.get(&id).cloned().unwrap();
            (bw, e_clone)
        };

        let _ = db_update_status(&self.db_path, &id, "completed", None);
        let now = now_str();
        let ev = build_event(&entry_final, 100.0, None, bw, &now);
        let _ = app.emit("dm-completed", &ev);
        let _ = app.emit("dm-progress", &ev);

        // Legacy aliases
        if entry_final.category == "llm" {
            emit_legacy_llm_progress(
                app,
                &entry_final.filename,
                part_total,
                bytes_before,
                total_bytes,
                100.0,
            );
        }

        info!(
            "[downloads] Completed: {} (id={} bytes={})",
            entry_final.filename, id, entry_final.bytes_done
        );
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    async fn download_part(
        &self,
        app: &AppHandle,
        id: &str,
        client: &reqwest::Client,
        url: &str,
        part_num: usize,
        part_total: usize,
        bytes_before: u64,
        grand_total: u64,
        filename: &str,
        display_name: &str,
        category: &str,
        hf_token: Option<&str>,
        cancel_flag: &Arc<AtomicBool>,
        chunk_size: usize,
    ) -> Result<u64, String> {
        let mut req = client.get(url);
        if let Some(tok) = hf_token {
            req = req.header("Authorization", format!("Bearer {}", tok));
        }

        let response = req.send().await.map_err(|e| e.to_string())?;
        if !response.status().is_success() {
            return Err(format!("HTTP {}", response.status()));
        }

        let part_total_bytes = response.content_length().unwrap_or(0);

        let mut stream = response.bytes_stream();
        let mut part_bytes_done: u64 = 0;
        let mut last_db_update = std::time::Instant::now();
        let mut last_emit_bytes: u64 = 0;
        let mut last_emit_time = std::time::Instant::now();

        // Read in configurable chunk sizes
        let mut buf = Vec::with_capacity(chunk_size);

        loop {
            if cancel_flag.load(Ordering::SeqCst) {
                return Err("cancelled".to_string());
            }

            let chunk_result = timeout(Duration::from_secs(60), stream.next()).await;
            match chunk_result {
                Err(_) => return Err("Download stalled (60s idle timeout)".to_string()),
                Ok(None) => break,
                Ok(Some(Err(e))) => return Err(e.to_string()),
                Ok(Some(Ok(chunk))) => {
                    buf.extend_from_slice(&chunk);

                    // Only process when we've accumulated chunk_size bytes or stream ended
                    if buf.len() < chunk_size {
                        continue;
                    }

                    let consumed = buf.len() as u64;
                    buf.clear();

                    part_bytes_done += consumed;
                    let overall_bytes = bytes_before + part_bytes_done;

                    // Update in-memory state and speed tracker
                    {
                        let mut state = self.state.lock().await;
                        if let Some(e) = state.entries.get_mut(id) {
                            e.bytes_done = overall_bytes;
                            e.updated_at = now_str();
                        }
                        if let Some(tracker) = state.speed_trackers.get_mut(id) {
                            tracker.record(overall_bytes);
                        }
                        state.update_bandwidth();
                    }

                    let bytes_changed = overall_bytes.saturating_sub(last_emit_bytes);
                    let time_since_emit = last_emit_time.elapsed().as_secs();

                    if bytes_changed >= PROGRESS_THROTTLE_BYTES
                        || time_since_emit >= PROGRESS_THROTTLE_SECS
                    {
                        last_emit_bytes = overall_bytes;
                        last_emit_time = std::time::Instant::now();

                        let (speed_bps, bw, pct) = {
                            let state = self.state.lock().await;
                            let spd = state
                                .speed_trackers
                                .get(id)
                                .map(|t| t.speed_bps())
                                .unwrap_or(0.0);
                            let p = if grand_total > 0 {
                                (overall_bytes as f64 / grand_total as f64 * 100.0).min(100.0)
                            } else if part_total_bytes > 0 {
                                (part_bytes_done as f64 / part_total_bytes as f64 * 100.0)
                                    .min(100.0)
                            } else {
                                0.0
                            };
                            (spd, state.bandwidth_bps, p)
                        };

                        let remaining = if grand_total > overall_bytes {
                            grand_total - overall_bytes
                        } else {
                            0
                        };
                        let eta = if speed_bps > 0.0 && remaining > 0 {
                            Some(remaining as f64 / speed_bps)
                        } else {
                            None
                        };

                        let now = now_str();
                        let ev = DownloadProgressEvent {
                            id: id.to_string(),
                            category: category.to_string(),
                            filename: filename.to_string(),
                            display_name: display_name.to_string(),
                            status: "active".to_string(),
                            bytes_done: overall_bytes,
                            total_bytes: grand_total,
                            percent: pct,
                            part_current: part_num,
                            part_total,
                            speed_bps,
                            eta_seconds: eta,
                            error_msg: None,
                            updated_at: now.clone(),
                            bandwidth_bps: bw,
                        };
                        let _ = app.emit("dm-progress", &ev);

                        // Legacy aliases
                        if category == "llm" {
                            let _ = app.emit(
                                "llm-download-progress",
                                serde_json::json!({
                                    "filename": filename,
                                    "part": part_num,
                                    "total_parts": part_total,
                                    "part_bytes_downloaded": part_bytes_done,
                                    "part_total_bytes": part_total_bytes,
                                    "bytes_downloaded": overall_bytes,
                                    "total_bytes": grand_total,
                                    "percent": pct,
                                    "status": "downloading",
                                }),
                            );
                        }
                        if category == "whisper" {
                            let _ = app.emit(
                                "whisper-download-progress",
                                serde_json::json!({
                                    "filename": filename,
                                    "bytes_downloaded": overall_bytes,
                                    "total_bytes": grand_total,
                                    "percent": pct as f32,
                                }),
                            );
                        }

                        // Persist progress to DB every 5s
                        if last_db_update.elapsed().as_secs() >= 5 {
                            let _ = db_update_progress(
                                &self.db_path,
                                id,
                                overall_bytes,
                                grand_total,
                                part_num,
                            );
                            last_db_update = std::time::Instant::now();
                        }
                    }
                }
            }
        }

        // Flush remaining buffer bytes
        if !buf.is_empty() {
            part_bytes_done += buf.len() as u64;
            buf.clear();
        }

        // Final DB progress write
        let overall = bytes_before + part_bytes_done;
        let _ = db_update_progress(&self.db_path, id, overall, grand_total, part_num);

        Ok(part_bytes_done)
    }

    async fn mark_cancelled(&self, app: &AppHandle, id: &str) {
        let (entry_clone, bw) = {
            let mut state = self.state.lock().await;
            {
                let Some(e) = state.entries.get_mut(id) else {
                    return;
                };
                if matches!(e.status, DownloadStatus::Active | DownloadStatus::Queued) {
                    e.status = DownloadStatus::Cancelled;
                    e.updated_at = now_str();
                }
            }
            state.active_ids.remove(id);
            state.speed_trackers.remove(id);
            state.update_bandwidth();
            let bw = state.bandwidth_bps;
            let e_clone = state.entries.get(id).cloned().unwrap();
            (e_clone, bw)
        };
        let _ = db_update_status(&self.db_path, id, "cancelled", None);
        let now = now_str();
        let pct = entry_clone.percent();
        let ev = build_event(&entry_clone, pct, None, bw, &now);
        let _ = app.emit("dm-cancelled", &ev);
        let _ = app.emit("dm-progress", &ev);
        if entry_clone.category == "llm" {
            let _ = app.emit(
                "llm-download-cancelled",
                serde_json::json!({"reason": "user_cancelled"}),
            );
        }
        info!("[downloads] Cancelled: {} (id={})", entry_clone.filename, id);
    }
}

// ── Periodic state log ──────────────────────────────────────────────────────

async fn periodic_state_log(state: Arc<Mutex<ManagerState>>, _db_path: PathBuf) {
    let mut interval = tokio::time::interval(Duration::from_secs(LOG_INTERVAL_SECS));
    interval.tick().await; // consume first immediate tick
    loop {
        interval.tick().await;
        let state_guard = state.lock().await;

        let active_info: Vec<serde_json::Value> = state_guard
            .active_ids
            .iter()
            .filter_map(|id| state_guard.entries.get(id))
            .map(|e| {
                let spd = state_guard
                    .speed_trackers
                    .get(&e.id)
                    .map(|t| t.speed_bps())
                    .unwrap_or(0.0);
                let remaining = e.total_bytes.saturating_sub(e.bytes_done);
                let eta = if spd > 0.0 && remaining > 0 {
                    remaining as f64 / spd
                } else {
                    0.0
                };
                serde_json::json!({
                    "id": e.id,
                    "filename": e.filename,
                    "category": e.category,
                    "percent": format!("{:.1}", e.percent()),
                    "speed_bps": spd.round() as u64,
                    "bytes_done": e.bytes_done,
                    "total_bytes": e.total_bytes,
                    "eta_seconds": eta.round() as u64,
                })
            })
            .collect();

        let queued_info: Vec<serde_json::Value> = state_guard
            .pending
            .iter()
            .filter_map(|item| state_guard.entries.get(&item.id))
            .map(|e| {
                serde_json::json!({
                    "id": e.id,
                    "filename": e.filename,
                    "priority": e.priority,
                })
            })
            .collect();

        let failed_count = state_guard
            .entries
            .values()
            .filter(|e| e.status == DownloadStatus::Failed)
            .count();
        let completed_count = state_guard
            .entries
            .values()
            .filter(|e| e.status == DownloadStatus::Completed)
            .count();
        let cancelled_count = state_guard
            .entries
            .values()
            .filter(|e| e.status == DownloadStatus::Cancelled)
            .count();

        info!(
            "[downloads] STATE | active={} queued={} completed={} fails={} cancelled={} \
             bandwidth_bps={:.0} peak_bps={:.0} active_slots={} max_concurrent={} | \
             active={} queued={}",
            state_guard.active_ids.len(),
            state_guard.pending.len(),
            completed_count,
            failed_count,
            cancelled_count,
            state_guard.bandwidth_bps,
            state_guard.peak_speed_bps,
            state_guard.active_slots,
            MAX_CONCURRENT,
            serde_json::to_string(&active_info).unwrap_or_default(),
            serde_json::to_string(&queued_info).unwrap_or_default(),
        );
    }
}

// ── SQLite helpers ────────────────────────────────────────────────────────

fn db_path_default(app: &AppHandle) -> PathBuf {
    app.path()
        .home_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(".matrx")
        .join("downloads.db")
}

fn init_db(path: &Path) -> rusqlite::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let conn = Connection::open(path)?;
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA foreign_keys=ON;
         PRAGMA synchronous=NORMAL;
         CREATE TABLE IF NOT EXISTS downloads (
             id           TEXT PRIMARY KEY,
             category     TEXT NOT NULL,
             filename     TEXT NOT NULL,
             display_name TEXT NOT NULL,
             urls         TEXT NOT NULL DEFAULT '[]',
             total_bytes  INTEGER NOT NULL DEFAULT 0,
             bytes_done   INTEGER NOT NULL DEFAULT 0,
             status       TEXT NOT NULL DEFAULT 'queued',
             error_msg    TEXT,
             priority     INTEGER NOT NULL DEFAULT 0,
             part_current INTEGER NOT NULL DEFAULT 1,
             part_total   INTEGER NOT NULL DEFAULT 1,
             created_at   TEXT NOT NULL,
             updated_at   TEXT NOT NULL,
             completed_at TEXT,
             metadata     TEXT
         );
         CREATE INDEX IF NOT EXISTS idx_dl_status ON downloads(status);
         CREATE INDEX IF NOT EXISTS idx_dl_category ON downloads(category);",
    )?;
    Ok(())
}

fn db_upsert(path: &Path, entry: &DownloadEntry, metadata: Option<String>) -> rusqlite::Result<()> {
    let conn = Connection::open(path)?;
    let urls_json = serde_json::to_string(&entry.urls).unwrap_or_default();
    conn.execute(
        "INSERT INTO downloads
             (id, category, filename, display_name, urls, total_bytes, bytes_done,
              status, error_msg, priority, part_current, part_total,
              created_at, updated_at, completed_at, metadata)
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16)
         ON CONFLICT(id) DO UPDATE SET
             status=excluded.status, bytes_done=excluded.bytes_done,
             total_bytes=excluded.total_bytes, error_msg=excluded.error_msg,
             part_current=excluded.part_current, updated_at=excluded.updated_at,
             completed_at=excluded.completed_at, metadata=excluded.metadata",
        params![
            entry.id,
            entry.category,
            entry.filename,
            entry.display_name,
            urls_json,
            entry.total_bytes as i64,
            entry.bytes_done as i64,
            entry.status.as_str(),
            entry.error_msg,
            entry.priority,
            entry.part_current as i64,
            entry.part_total as i64,
            entry.created_at,
            entry.updated_at,
            entry.completed_at,
            metadata,
        ],
    )?;
    Ok(())
}

fn db_update_status(
    path: &Path,
    id: &str,
    status: &str,
    error_msg: Option<&str>,
) -> rusqlite::Result<()> {
    let conn = Connection::open(path)?;
    let now = now_str();
    let completed_at: Option<&str> = if status == "completed" { Some(&now) } else { None };
    conn.execute(
        "UPDATE downloads SET status=?1, error_msg=?2, updated_at=?3, \
         completed_at=COALESCE(?4, completed_at) WHERE id=?5",
        params![status, error_msg, now, completed_at, id],
    )?;
    Ok(())
}

fn db_update_progress(
    path: &Path,
    id: &str,
    bytes_done: u64,
    total_bytes: u64,
    part_current: usize,
) -> rusqlite::Result<()> {
    let conn = Connection::open(path)?;
    conn.execute(
        "UPDATE downloads SET bytes_done=?1, total_bytes=?2, part_current=?3, updated_at=?4 WHERE id=?5",
        params![
            bytes_done as i64,
            total_bytes as i64,
            part_current as i64,
            now_str(),
            id
        ],
    )?;
    Ok(())
}

fn db_load_incomplete(path: &Path) -> rusqlite::Result<Vec<DownloadEntry>> {
    if !path.exists() {
        return Ok(vec![]);
    }
    let conn = Connection::open(path)?;
    let mut stmt = conn.prepare(
        "SELECT id, category, filename, display_name, urls, total_bytes, bytes_done,
                status, error_msg, priority, part_current, part_total,
                created_at, updated_at, completed_at
         FROM downloads
         WHERE status IN ('queued','active')
         ORDER BY priority DESC, created_at ASC",
    )?;

    let rows = stmt.query_map([], |row| {
        let urls_json: String = row.get(4)?;
        let urls: Vec<String> = serde_json::from_str(&urls_json).unwrap_or_default();
        Ok(DownloadEntry {
            id: row.get(0)?,
            category: row.get(1)?,
            filename: row.get(2)?,
            display_name: row.get(3)?,
            urls,
            total_bytes: row.get::<_, i64>(5)? as u64,
            bytes_done: row.get::<_, i64>(6)? as u64,
            status: DownloadStatus::Queued,
            error_msg: row.get(8)?,
            priority: row.get(9)?,
            part_current: row.get::<_, i64>(10)? as usize,
            part_total: row.get::<_, i64>(11)? as usize,
            created_at: row.get(12)?,
            updated_at: row.get(13)?,
            completed_at: row.get(14)?,
        })
    })?;

    let mut result = Vec::new();
    for row in rows {
        match row {
            Ok(entry) => result.push(entry),
            Err(e) => warn!("[downloads] Failed to parse DB row: {}", e),
        }
    }
    Ok(result)
}

// ── Utility helpers ───────────────────────────────────────────────────────

fn now_str() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    // unwrap_or_else handles the rare case where the system clock goes backwards
    // (NTP adjustments, virtualized environments on Windows). In that case,
    // use the absolute duration (time before epoch) to avoid returning epoch silently.
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_else(|e| e.duration())
        .as_millis();
    let secs = millis / 1000;
    let ms = millis % 1000;
    let dt = chrono_from_epoch(secs as u64, ms as u32);
    dt
}

fn chrono_from_epoch(secs: u64, ms: u32) -> String {
    let minutes = secs / 60;
    let hours = minutes / 60;
    let days = hours / 24;

    let sec = secs % 60;
    let min = minutes % 60;
    let hr = hours % 24;

    let (y, mo, d) = days_to_ymd(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:03}Z",
        y, mo, d, hr, min, sec, ms
    )
}

fn days_to_ymd(mut days: u64) -> (u64, u64, u64) {
    let mut year = 1970u64;
    loop {
        let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
        let days_in_year = if leap { 366 } else { 365 };
        if days < days_in_year {
            break;
        }
        days -= days_in_year;
        year += 1;
    }
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    let months = [
        31u64,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    let mut month = 1u64;
    for &days_in_month in &months {
        if days < days_in_month {
            break;
        }
        days -= days_in_month;
        month += 1;
    }
    (year, month, days + 1)
}

fn build_event(
    entry: &DownloadEntry,
    percent: f64,
    eta: Option<f64>,
    bandwidth_bps: f64,
    updated_at: &str,
) -> DownloadProgressEvent {
    DownloadProgressEvent {
        id: entry.id.clone(),
        category: entry.category.clone(),
        filename: entry.filename.clone(),
        display_name: entry.display_name.clone(),
        status: entry.status.as_str().to_string(),
        bytes_done: entry.bytes_done,
        total_bytes: entry.total_bytes,
        percent,
        part_current: entry.part_current,
        part_total: entry.part_total,
        speed_bps: 0.0,
        eta_seconds: eta,
        error_msg: entry.error_msg.clone(),
        updated_at: updated_at.to_string(),
        bandwidth_bps,
    }
}

fn emit_legacy_llm_progress(
    app: &AppHandle,
    filename: &str,
    total_parts: usize,
    bytes: u64,
    total: u64,
    pct: f64,
) {
    let _ = app.emit(
        "llm-download-progress",
        serde_json::json!({
            "filename": filename,
            "part": total_parts,
            "total_parts": total_parts,
            "part_bytes_downloaded": bytes,
            "part_total_bytes": total,
            "bytes_downloaded": bytes,
            "total_bytes": total,
            "percent": pct,
            "status": "already_complete",
        }),
    );
}

async fn probe_total_bytes(
    client: &reqwest::Client,
    urls: &[String],
    hf_token: Option<&str>,
) -> u64 {
    let mut total = 0u64;
    for url in urls {
        let mut req = client.head(url);
        if let Some(tok) = hf_token {
            req = req.header("Authorization", format!("Bearer {}", tok));
        }
        if let Ok(resp) = req.send().await {
            if let Some(cl) = resp.content_length() {
                total += cl;
            }
        }
    }
    total
}

fn get_hf_token_from_app(app: &AppHandle) -> Option<String> {
    let config_dir = app.path().app_data_dir().ok()?;
    let config_path = config_dir.join("llm.json");
    let content = std::fs::read_to_string(config_path).ok()?;
    let json: serde_json::Value = serde_json::from_str(&content).ok()?;
    json.get("hf_token")?.as_str().map(String::from)
}

/// Public accessor so lib.rs can get the db path for the manager constructor
pub fn default_db_path(app: &AppHandle) -> PathBuf {
    db_path_default(app)
}
