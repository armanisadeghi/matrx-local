//! An in-memory mock of the cloud file service. **A mock.**
//!
//! It models the four behaviours the engine actually has to survive, and nothing else:
//!
//! * **the change feed with a cursor** — an append-only log of events, read forward from a cursor;
//! * **412 preconditions** — a write carrying a stale `expected_version` is refused, which is how
//!   two devices racing on one path are stopped from clobbering each other (invariant I3);
//! * **tombstones** — a delete is a row with `deleted_at`, never a missing row, and tombstones are
//!   never purged here (D23 puts the retention floor at 90 days);
//! * **stability lag** — an event is not visible to a feed read until the lag has passed, so a
//!   device that writes and immediately polls does *not* see its own write echo instantly. This is
//!   the property that makes naive "poll after write" engines converge in tests and diverge in
//!   production.
//!
//! It is not a model of the real server's schema, auth, quotas or rate limits. Green here proves
//! the planner and the executor's shape — never the product (SCOPE §6).

use crate::model::{RemoteNode, RemoteTree};
use std::collections::BTreeMap;

/// Why a server call failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ServerError {
    /// The `expected_version` did not match: someone else wrote first (HTTP 412).
    PreconditionFailed {
        /// The version the caller expected.
        expected: Option<i64>,
        /// The version the server actually holds.
        actual: Option<i64>,
    },
    /// An injected transient failure — a dropped connection, a 503, a timeout.
    Transient(&'static str),
    /// The path does not exist.
    NotFound,
}

/// One file as the server holds it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServerFile {
    /// `files.files.id`.
    pub id: String,
    /// The row version every precondition is taken against.
    pub version: i64,
    /// The server's SHA-256, or `None` for a folder or a tombstone.
    pub checksum: Option<String>,
    /// Whether it is a folder.
    pub is_dir: bool,
    /// Set when tombstoned. The row is kept; it is never removed.
    pub deleted_at: Option<i64>,
    /// Which device wrote it last.
    pub origin_device_id: String,
}

/// One entry in the change feed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedEvent {
    /// The cursor value. Strictly increasing.
    pub seq: i64,
    /// The path the event concerns.
    pub path: String,
    /// The file as it stood after the event.
    pub file: ServerFile,
    /// When it happened, in simulation ticks.
    pub at: i64,
}

/// The mock cloud.
#[derive(Debug, Clone, Default)]
pub struct MockServer {
    files: BTreeMap<String, ServerFile>,
    feed: Vec<FeedEvent>,
    next_seq: i64,
    next_id: u64,
    /// How many ticks an event stays invisible to a feed read.
    pub stability_lag: i64,
}

impl MockServer {
    /// A fresh cloud with the given stability lag.
    pub fn new(stability_lag: i64) -> Self {
        MockServer {
            stability_lag,
            ..MockServer::default()
        }
    }

    fn fresh_id(&mut self) -> String {
        self.next_id += 1;
        format!("file-{}", self.next_id)
    }

    fn append(&mut self, path: &str, file: ServerFile, now: i64) {
        self.next_seq += 1;
        let seq = self.next_seq;
        self.feed.push(FeedEvent {
            seq,
            path: path.to_string(),
            file: file.clone(),
            at: now,
        });
        self.files.insert(path.to_string(), file);
    }

    fn check_precondition(
        &self,
        path: &str,
        expected_version: Option<i64>,
    ) -> Result<(), ServerError> {
        let actual = self.files.get(path).filter(|f| f.deleted_at.is_none()).map(|f| f.version);
        match (expected_version, actual) {
            // No precondition offered: the caller believes nothing exists. If something live does,
            // that is a race and the write is refused.
            (None, None) => Ok(()),
            (None, Some(actual)) => Err(ServerError::PreconditionFailed {
                expected: None,
                actual: Some(actual),
            }),
            (Some(e), Some(a)) if e == a => Ok(()),
            (Some(e), a) => Err(ServerError::PreconditionFailed {
                expected: Some(e),
                actual: a,
            }),
        }
    }

    /// Write a file. Returns the new row, or a 412.
    pub fn put(
        &mut self,
        path: &str,
        checksum: &str,
        expected_version: Option<i64>,
        device: &str,
        now: i64,
    ) -> Result<ServerFile, ServerError> {
        self.check_precondition(path, expected_version)?;
        let id = match self.files.get(path) {
            Some(f) => f.id.clone(),
            None => self.fresh_id(),
        };
        let version = self.files.get(path).map(|f| f.version).unwrap_or(0) + 1;
        let file = ServerFile {
            id,
            version,
            checksum: Some(checksum.to_string()),
            is_dir: false,
            deleted_at: None,
            origin_device_id: device.to_string(),
        };
        self.append(path, file.clone(), now);
        Ok(file)
    }

    /// Create a folder. Idempotent.
    pub fn mkdir(&mut self, path: &str, device: &str, now: i64) -> ServerFile {
        if let Some(f) = self.files.get(path).filter(|f| f.deleted_at.is_none()) {
            return f.clone();
        }
        let id = self.fresh_id();
        let version = self.files.get(path).map(|f| f.version).unwrap_or(0) + 1;
        let file = ServerFile {
            id,
            version,
            checksum: None,
            is_dir: true,
            deleted_at: None,
            origin_device_id: device.to_string(),
        };
        self.append(path, file.clone(), now);
        file
    }

    /// Tombstone a file. The row stays; only `deleted_at` is set (D23).
    pub fn delete(
        &mut self,
        path: &str,
        expected_version: Option<i64>,
        device: &str,
        now: i64,
    ) -> Result<ServerFile, ServerError> {
        if !self.files.contains_key(path) {
            return Err(ServerError::NotFound);
        }
        self.check_precondition(path, expected_version)?;
        let existing = self.files.get(path).expect("checked").clone();
        let file = ServerFile {
            version: existing.version + 1,
            checksum: None,
            deleted_at: Some(now),
            origin_device_id: device.to_string(),
            ..existing
        };
        self.append(path, file.clone(), now);
        Ok(file)
    }

    /// Move a file, keeping its id — the server-side rename primitive the planner's `Rename`
    /// depends on.
    pub fn rename(
        &mut self,
        from: &str,
        to: &str,
        expected_version: Option<i64>,
        device: &str,
        now: i64,
    ) -> Result<ServerFile, ServerError> {
        self.check_precondition(from, expected_version)?;
        let Some(existing) = self.files.get(from).cloned() else {
            return Err(ServerError::NotFound);
        };
        if self
            .files
            .get(to)
            .is_some_and(|f| f.deleted_at.is_none())
        {
            return Err(ServerError::PreconditionFailed {
                expected: None,
                actual: self.files.get(to).map(|f| f.version),
            });
        }
        let tombstone = ServerFile {
            version: existing.version + 1,
            checksum: None,
            deleted_at: Some(now),
            origin_device_id: device.to_string(),
            ..existing.clone()
        };
        self.append(from, tombstone, now);
        let moved = ServerFile {
            version: existing.version + 2,
            deleted_at: None,
            origin_device_id: device.to_string(),
            ..existing
        };
        self.append(to, moved.clone(), now);
        Ok(moved)
    }

    /// Read the feed forward from `cursor`, honouring the stability lag.
    ///
    /// Returns the events and the new cursor. An event newer than `now - stability_lag` is not
    /// returned and does **not** advance the cursor past itself.
    pub fn feed_since(&self, cursor: i64, now: i64) -> (Vec<FeedEvent>, i64) {
        let mut out = Vec::new();
        let mut new_cursor = cursor;
        for e in self.feed.iter().filter(|e| e.seq > cursor) {
            if e.at + self.stability_lag > now {
                break; // and everything after it stays unread: the feed is ordered
            }
            new_cursor = e.seq;
            out.push(e.clone());
        }
        (out, new_cursor)
    }

    /// The live (non-tombstoned) file at `path`.
    pub fn live(&self, path: &str) -> Option<&ServerFile> {
        self.files.get(path).filter(|f| f.deleted_at.is_none())
    }

    /// Every live path with its content id, for a convergence assertion.
    pub fn live_contents(&self) -> BTreeMap<String, Option<String>> {
        self.files
            .iter()
            .filter(|(_, f)| f.deleted_at.is_none())
            .map(|(p, f)| (p.clone(), f.checksum.clone()))
            .collect()
    }

    /// The whole cloud as a [`RemoteTree`] — what a full rescan would produce.
    pub fn full_tree(&self) -> RemoteTree {
        self.files
            .iter()
            .map(|(path, f)| (path.clone(), node_from(path, f)))
            .collect()
    }

    /// How many events the feed holds. Tombstones included; they are never purged.
    pub fn feed_len(&self) -> usize {
        self.feed.len()
    }
}

/// Turn a feed event's file into the `tree_remote` row a device records.
pub fn node_from(path: &str, f: &ServerFile) -> RemoteNode {
    RemoteNode {
        path_nfc: path.to_string(),
        is_dir: f.is_dir,
        size: f.checksum.as_ref().map(|c| c.len() as i64),
        remote_file_id: Some(f.id.clone()),
        remote_folder_id: None,
        remote_version: Some(f.version),
        checksum: f.checksum.clone(),
        client_modified_at: None,
        origin_device_id: Some(f.origin_device_id.clone()),
        deleted_at: f.deleted_at.map(|t| format!("tick-{t}")),
        seen_at: None,
    }
}
