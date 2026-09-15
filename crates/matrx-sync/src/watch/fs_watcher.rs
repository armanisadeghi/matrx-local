//! The filesystem watcher: `notify` plus a debouncer, feeding paths to the scanner.
//!
//! It answers one question — *which paths changed recently?* — and it is allowed to be wrong in
//! one direction only: it may report a path that did not change (the scanner will find nothing),
//! and it may MISS a path, which is why [`super::schedule`]'s periodic rescan exists. Nothing here
//! is trusted as the mechanism.
//!
//! **The debounce is a knob** (`sync.watcher_debounce_ms`, 750, range 100–5000). Editors write a
//! file three or four times in a second — a temp file, a rename, a metadata touch — and without a
//! debounce each of those is a plan, an upload and a new remote version.

use notify::RecursiveMode;
use notify_debouncer_full::{new_debouncer, DebouncedEvent, Debouncer, FileIdMap};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

/// Why a watcher could not be started or could not continue.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WatcherError {
    /// Linux ran out of inotify watches. This is the one failure with a precise, actionable
    /// remedy, and SCOPE §3.1 item 12 requires it be surfaced with that remedy rather than as a
    /// generic failure. The mapping falls back to rescan-only and the state says so.
    WatchLimitReached,
    /// The root does not exist or cannot be watched — an unmounted volume, a deleted folder.
    RootUnavailable(String),
    /// Anything else the platform said.
    Other(String),
}

impl WatcherError {
    /// The honest state this failure puts the mapping in (SPEC-ENGINE §3.6 table c).
    pub const fn honest_state(&self) -> &'static str {
        match self {
            WatcherError::WatchLimitReached => "watcher_exhausted",
            // A root that cannot be watched is a root that is not there; the marker check is what
            // distinguishes "unmounted" from "deleted", and both suspend rather than propagate.
            WatcherError::RootUnavailable(_) => "root_missing",
            // Any other watcher failure is not a reason to stop syncing — the periodic rescan is
            // the mechanism — so the mapping keeps its state and only its cadence changes.
            WatcherError::Other(_) => "polling_fallback",
        }
    }

    /// The sentence a surface shows, with what to actually do about it.
    ///
    /// Law 4: a state announces itself **with a remedy**. "Watcher exhausted" on its own tells a
    /// user nothing; the sysctl name is the entire useful content.
    pub fn remedy(&self) -> String {
        match self {
            WatcherError::WatchLimitReached => {
                "This computer has run out of folder-watching slots, so AI Matrx is checking this \
                 folder on a timer instead of instantly. To restore instant updates, raise the \
                 Linux limit `fs.inotify.max_user_watches` (for example: \
                 `echo fs.inotify.max_user_watches=524288 | sudo tee \
                 /etc/sysctl.d/40-matrx.conf && sudo sysctl --system`)."
                    .to_string()
            }
            WatcherError::RootUnavailable(detail) => format!(
                "AI Matrx cannot watch this folder — it may be on a drive that is not connected. \
                 Reconnect the drive, or choose a different folder. ({detail})"
            ),
            WatcherError::Other(detail) => format!(
                "Instant updates are unavailable for this folder, so AI Matrx is checking it on a \
                 timer. Syncing continues. ({detail})"
            ),
        }
    }

    /// Whether the mapping should keep running on the timer alone.
    ///
    /// Every watcher failure is survivable, because the periodic rescan is the mechanism — which
    /// is the whole reason this module is allowed to fail at all.
    pub const fn survivable(&self) -> bool {
        !matches!(self, WatcherError::RootUnavailable(_))
    }
}

/// Classify a `notify` error into something with a remedy.
///
/// `MaxFilesWatch` is the inotify limit. It is the one error worth naming precisely, because it is
/// the one a user can actually fix, and because it is silent otherwise: the folder simply stops
/// updating instantly and nobody knows why.
pub fn classify(error: &notify::Error) -> WatcherError {
    match &error.kind {
        notify::ErrorKind::MaxFilesWatch => WatcherError::WatchLimitReached,
        notify::ErrorKind::PathNotFound => WatcherError::RootUnavailable(error.to_string()),
        notify::ErrorKind::Io(io) => match io.kind() {
            std::io::ErrorKind::NotFound => WatcherError::RootUnavailable(io.to_string()),
            // Linux reports the inotify limit as ENOSPC from `inotify_add_watch` — "no space left
            // on device" on a disk with plenty of room. Filing that under generic IO is how this
            // class stays invisible.
            _ if io.raw_os_error() == Some(28) => WatcherError::WatchLimitReached,
            _ => WatcherError::Other(io.to_string()),
        },
        _ => WatcherError::Other(error.to_string()),
    }
}

/// A running watcher over one mapping root.
///
/// Dropping it stops watching. The daemon holds one per running mapping.
pub struct FolderWatcher {
    _debouncer: Debouncer<notify::RecommendedWatcher, FileIdMap>,
    events: Receiver<Result<Vec<DebouncedEvent>, Vec<notify::Error>>>,
    root: PathBuf,
    /// The root with every symlink resolved.
    ///
    /// Backends report the REAL path, not the one they were handed: on macOS a watch on
    /// `/var/folders/…` comes back as `/private/var/folders/…`, and `/tmp` comes back as
    /// `/private/tmp`. Stripping the given prefix then fails on every single event and the watcher
    /// silently reports nothing — which is what this crate did until a test over a tempdir caught
    /// it. Any mapping root reachable through a symlink would have hit it in production.
    root_canonical: PathBuf,
}

impl std::fmt::Debug for FolderWatcher {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FolderWatcher")
            .field("root", &self.root)
            .finish_non_exhaustive()
    }
}

/// What a drain of the watcher found.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WatchBatch {
    /// Mapping-relative, NFC paths that may have changed, in order and without duplicates.
    ///
    /// "May". The scanner decides what actually changed; this only says where to look.
    pub paths: BTreeSet<String>,
    /// Errors the watcher reported while running — an exhausted watch limit can arrive here long
    /// after a successful start, as a tree grows.
    pub errors: Vec<WatcherError>,
    /// Whether the watcher said something happened that it could not describe, so the caller must
    /// fall back to a full walk. A kernel queue overflow arrives this way.
    pub needs_full_rescan: bool,
}

impl FolderWatcher {
    /// Start watching `root` recursively, debounced by `debounce_ms`.
    ///
    /// Fails loudly rather than degrading silently: the caller turns the error into the honest
    /// state and the remedy, and keeps the mapping running on the timer.
    pub fn start(root: &Path, debounce_ms: u64) -> Result<Self, WatcherError> {
        let (tx, events) = channel();
        // `tick_rate` None lets the debouncer choose; the timeout is the knob.
        let mut debouncer = new_debouncer(Duration::from_millis(debounce_ms), None, tx)
            .map_err(|e| classify(&e))?;
        debouncer
            .watch(root, RecursiveMode::Recursive)
            .map_err(|e| classify(&e))?;
        Ok(FolderWatcher {
            _debouncer: debouncer,
            events,
            root: root.to_path_buf(),
            root_canonical: std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf()),
        })
    }

    /// Take everything the watcher has queued, without blocking.
    pub fn drain(&self) -> WatchBatch {
        let mut batch = WatchBatch::default();
        while let Ok(message) = self.events.try_recv() {
            match message {
                Ok(events) => {
                    for event in events {
                        if matches!(event.kind, notify::EventKind::Other) {
                            // `Other` is how a backend says "I lost track" — a kernel queue
                            // overflow, an FSEvents history drop. The only honest answer is to
                            // stop trusting the watcher for this round and walk.
                            batch.needs_full_rescan = true;
                        }
                        for path in &event.paths {
                            if let Some(relative) = self.relative(path) {
                                batch.paths.insert(relative);
                            }
                        }
                    }
                }
                Err(errors) => {
                    for error in &errors {
                        let classified = classify(error);
                        // A watch limit reached while RUNNING is the common shape on Linux: the
                        // tree grew past the budget. It must reach the caller with its remedy,
                        // not be counted as noise.
                        if !batch.errors.contains(&classified) {
                            batch.errors.push(classified);
                        }
                    }
                    batch.needs_full_rescan = true;
                }
            }
        }
        batch
    }

    /// Block for up to `timeout` for the first batch, then drain the rest.
    ///
    /// For tests and for the daemon's idle loop; the daemon normally drains on its own tick.
    pub fn drain_blocking(&self, timeout: Duration) -> WatchBatch {
        let mut batch = WatchBatch::default();
        if let Ok(message) = self.events.recv_timeout(timeout) {
            match message {
                Ok(events) => {
                    for event in events {
                        if matches!(event.kind, notify::EventKind::Other) {
                            batch.needs_full_rescan = true;
                        }
                        for path in &event.paths {
                            if let Some(relative) = self.relative(path) {
                                batch.paths.insert(relative);
                            }
                        }
                    }
                }
                Err(errors) => {
                    for error in &errors {
                        let classified = classify(error);
                        if !batch.errors.contains(&classified) {
                            batch.errors.push(classified);
                        }
                    }
                    batch.needs_full_rescan = true;
                }
            }
        }
        let rest = self.drain();
        batch.paths.extend(rest.paths);
        for e in rest.errors {
            if !batch.errors.contains(&e) {
                batch.errors.push(e);
            }
        }
        batch.needs_full_rescan |= rest.needs_full_rescan;
        batch
    }

    /// The mapping-relative, NFC form of an absolute path the watcher reported.
    ///
    /// `None` for anything outside the root (a watcher can report the root itself) and for the
    /// mapping's own `.matrx-sync/` bookkeeping, whose staging writes would otherwise make every
    /// download look like a local change.
    fn relative(&self, path: &Path) -> Option<String> {
        // Try the root as given AND as resolved: the caller thinks in the path the user chose, the
        // backend answers in the path the kernel knows.
        let relative = path
            .strip_prefix(&self.root)
            .or_else(|_| path.strip_prefix(&self.root_canonical))
            .ok()?;
        let joined = relative
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/");
        if joined.is_empty() || joined == ".matrx-sync" || joined.starts_with(".matrx-sync/") {
            return None;
        }
        Some(crate::naming::nfc(&joined))
    }
}
