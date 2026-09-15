//! Walking a mapping root into a [`crate::model::LocalTree`].
//!
//! The walk never follows a symlink and never hashes: hashing is the next unit's job, and this one
//! decides only *which* files need it (SCOPE §3.1 item 2's fast path). A file whose hash cannot be
//! carried forward comes back with `content_hash: None`, which invariant I9 defines as "not yet
//! known" — and which the planner answers with a `HashRequest` rather than a guess.

use crate::model::{LocalNode, LocalTree};
use crate::scan::fastpath::{decide, FastPath, MtimeGranularity, Observed, RehashReason};
use crate::scan::identity;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Why an entry was not put in the tree.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkipReason {
    /// A symlink, and `sync.follow_symlinks` is false (its default). SCOPE §3.1 item 7 requires
    /// these be "skipped **and reported**" — silence would be a file the user believes is syncing.
    Symlink,
    /// Not a file, a directory or a symlink: a socket, a fifo, a device node.
    NotAFileOrDirectory,
    /// The OS refused to read it (permissions, a TCC denial, a lock).
    Unreadable,
}

impl SkipReason {
    /// A short stable token for activity rows.
    pub const fn as_str(self) -> &'static str {
        match self {
            SkipReason::Symlink => "symlink",
            SkipReason::NotAFileOrDirectory => "not_a_file_or_directory",
            SkipReason::Unreadable => "unreadable",
        }
    }
}

/// One entry the scan deliberately did not sync, and why.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkippedEntry {
    /// The mapping-relative path.
    pub path: String,
    /// Why.
    pub reason: SkipReason,
    /// The OS's own words, when it had any.
    pub detail: Option<String>,
}

/// What the caller decides before a scan runs. Every one of these is a knob or a fact about the
/// volume — none of them is a constant inside the walk.
#[derive(Debug, Clone)]
pub struct ScanOptions {
    /// `sync.follow_symlinks` (default false). Following them is how a sync engine walks into an
    /// infinite loop or quietly copies somebody's whole home directory.
    pub follow_symlinks: bool,
    /// What this volume can promise about timestamps.
    pub granularity: MtimeGranularity,
    /// When this scan began, in nanoseconds — injected, because this crate reads no clock.
    pub scan_started_ns: i64,
    /// The RFC3339 stamp recorded on every row this scan writes.
    pub scanned_at: String,
    /// Directory names never descended into. The mapping's own state directory is always here.
    pub skip_dir_names: Vec<String>,
}

impl ScanOptions {
    /// The defaults SPEC-ENGINE §2 gives, for a modern volume.
    pub fn new(scan_started_ns: i64, scanned_at: impl Into<String>) -> Self {
        ScanOptions {
            follow_symlinks: false,
            granularity: MtimeGranularity::MODERN,
            scan_started_ns,
            scanned_at: scanned_at.into(),
            // `.matrx-sync/` holds the marker, the download staging area and the compiled ignore
            // rules. It is the mapping's own bookkeeping and is never synced (SPEC-ENGINE §2's
            // junk list names it).
            skip_dir_names: vec![".matrx-sync".to_string()],
        }
    }
}

/// What a scan found.
#[derive(Debug, Clone)]
pub struct ScanReport {
    /// The tree, ready for the journal and then the planner.
    pub tree: LocalTree,
    /// The real relative path of every entry, keyed by the tree's key.
    ///
    /// Invariant I8: *"The on-disk name is recovered from the filesystem, never from `path_nfc`."*
    /// Once the key is normalised, this map is how the executor finds the bytes again.
    pub on_disk: BTreeMap<String, PathBuf>,
    /// Entries deliberately not synced, each with its reason.
    pub skipped: Vec<SkippedEntry>,
    /// Why each file that needs hashing needs it — the fast path's refusals, in path order.
    pub needs_hash: BTreeMap<String, RehashReason>,
    /// Directories the walk could not read at all.
    pub unreadable_dirs: Vec<SkippedEntry>,
}

impl ScanReport {
    /// How many files this scan must hash before the planner can decide anything about them.
    pub fn hash_backlog(&self) -> usize {
        self.needs_hash.len()
    }
}

/// Walk `root` into a tree, reusing `previous`'s hashes wherever the fast path allows.
///
/// The walk is breadth-first and deterministic: entries are sorted, so two scans of an unchanged
/// tree produce byte-identical output and a diff of two reports is meaningful.
pub fn scan_root(root: &Path, previous: &LocalTree, opts: &ScanOptions) -> ScanReport {
    let mut report = ScanReport {
        tree: LocalTree::new(),
        on_disk: BTreeMap::new(),
        skipped: Vec::new(),
        needs_hash: BTreeMap::new(),
        unreadable_dirs: Vec::new(),
    };
    let mut queue: Vec<PathBuf> = vec![root.to_path_buf()];

    while let Some(dir) = queue.pop() {
        let relative_dir = relative(root, &dir);
        let entries = match std::fs::read_dir(&dir) {
            Ok(entries) => entries,
            Err(e) => {
                report.unreadable_dirs.push(SkippedEntry {
                    path: relative_dir,
                    reason: SkipReason::Unreadable,
                    detail: Some(e.to_string()),
                });
                continue;
            }
        };
        let mut names: Vec<PathBuf> = Vec::new();
        for entry in entries {
            match entry {
                Ok(e) => names.push(e.path()),
                Err(e) => report.unreadable_dirs.push(SkippedEntry {
                    path: relative_dir.clone(),
                    reason: SkipReason::Unreadable,
                    detail: Some(e.to_string()),
                }),
            }
        }
        names.sort();

        for path in names {
            let rel = relative(root, &path);
            if rel.is_empty() {
                continue;
            }
            // `symlink_metadata` does not follow: a symlink must be SEEN as a symlink before any
            // decision about it, or the decision is about its target.
            let meta = match std::fs::symlink_metadata(&path) {
                Ok(m) => m,
                Err(e) => {
                    report.skipped.push(SkippedEntry {
                        path: rel,
                        reason: SkipReason::Unreadable,
                        detail: Some(e.to_string()),
                    });
                    continue;
                }
            };

            if meta.file_type().is_symlink() && !opts.follow_symlinks {
                report.skipped.push(SkippedEntry {
                    path: rel,
                    reason: SkipReason::Symlink,
                    detail: std::fs::read_link(&path)
                        .ok()
                        .map(|t| t.to_string_lossy().into_owned()),
                });
                continue;
            }

            if meta.is_dir() {
                let name = path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default();
                if opts.skip_dir_names.contains(&name) {
                    continue;
                }
                report.tree.insert(
                    rel.clone(),
                    LocalNode {
                        path_nfc: rel.clone(),
                        is_dir: true,
                        size: None,
                        mtime_ns: None,
                        volume_id: None,
                        file_id: None,
                        content_hash: None,
                        scanned_at: Some(opts.scanned_at.clone()),
                    },
                );
                report.on_disk.insert(rel, path.clone());
                queue.push(path);
                continue;
            }

            if !meta.is_file() {
                report.skipped.push(SkippedEntry {
                    path: rel,
                    reason: SkipReason::NotAFileOrDirectory,
                    detail: None,
                });
                continue;
            }

            let identity = file_identity(&path, &meta);
            let observed = Observed {
                size: meta.len() as i64,
                mtime_ns: mtime_ns(&meta),
                identity: identity.clone(),
            };
            let content_hash = match decide(
                previous.get(&rel),
                &observed,
                opts.scan_started_ns,
                opts.granularity,
            ) {
                FastPath::Reuse(hash) => Some(hash),
                FastPath::Rehash(reason) => {
                    report.needs_hash.insert(rel.clone(), reason);
                    None
                }
            };

            report.tree.insert(
                rel.clone(),
                LocalNode {
                    path_nfc: rel.clone(),
                    is_dir: false,
                    size: Some(observed.size),
                    mtime_ns: Some(observed.mtime_ns),
                    volume_id: identity.volume_id,
                    file_id: identity.file_id,
                    content_hash,
                    scanned_at: Some(opts.scanned_at.clone()),
                },
            );
            report.on_disk.insert(rel, path);
        }
    }
    report
}

#[cfg(windows)]
fn file_identity(path: &Path, _meta: &std::fs::Metadata) -> crate::scan::FileIdentity {
    identity::identity_of_path(path)
}

#[cfg(not(windows))]
fn file_identity(_path: &Path, meta: &std::fs::Metadata) -> crate::scan::FileIdentity {
    identity::identity_of(meta)
}

/// The path relative to the mapping root, with `/` separators on every platform — the shape
/// `path_nfc` has in the journal and in the cloud.
fn relative(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("/")
}

/// Modification time in nanoseconds since the unix epoch.
///
/// A time before the epoch yields a negative number rather than being clamped: a file dated 1969 is
/// strange, and hiding it behind a 0 would make two such files look identical to the fast path.
fn mtime_ns(meta: &std::fs::Metadata) -> i64 {
    let Ok(modified) = meta.modified() else {
        return 0;
    };
    match modified.duration_since(std::time::UNIX_EPOCH) {
        Ok(d) => d.as_nanos().min(i64::MAX as u128) as i64,
        Err(e) => -(e.duration().as_nanos().min(i64::MAX as u128) as i64),
    }
}
