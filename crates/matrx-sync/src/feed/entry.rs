//! The change feed's wire shape — typed from the LIVE service, not from prose.
//!
//! Verified against `https://files.matrxserver.com/files/sync/changes` on 2026-09-15 as
//! `admin@admin.com`: the entry carries all three of SPEC-SERVER §2.3's v2 fields
//! (`origin_device_id`, `client_modified_at`, `organization_id`) alongside the v1 set.

use serde::{Deserialize, Serialize};

/// One file as the feed reports it.
///
/// Unknown fields are ignored rather than rejected: the server may add a field before this client
/// knows about it, and refusing to parse the feed would stop syncing over a field nobody reads.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileEntry {
    /// `files.files.id`.
    pub file_id: String,
    /// The full cloud path.
    pub file_path: String,
    /// The leaf name.
    pub file_name: Option<String>,
    /// The containing folder.
    pub folder_id: Option<String>,
    /// Server-side SHA-256. NULL on a tombstone, and — per SPEC-SERVER §5 — never NULL for a sync
    /// upload.
    pub checksum: Option<String>,
    /// Size in bytes.
    pub size_bytes: Option<i64>,
    /// The row version every precondition is taken against (I3).
    pub version: Option<i64>,
    /// RFC3339. Set means this row is a TOMBSTONE, not a live file.
    pub deleted_at: Option<String>,
    /// RFC3339 keyset half.
    pub updated_at: Option<String>,
    /// RFC3339.
    pub created_at: Option<String>,
    /// `app_instances.id` of the device that wrote it — what echo suppression reads (C13).
    pub origin_device_id: Option<String>,
    /// What the user's clock said when they changed it, as distinct from when the server stored it.
    pub client_modified_at: Option<String>,
    /// The organization the row belongs to. NOT optional on the entry (SPEC-SERVER §2.3), so the
    /// engine can stamp writes explicitly rather than letting a default choose.
    pub organization_id: Option<String>,
    /// MIME type, when the server has one.
    pub mime_type: Option<String>,
}

impl FileEntry {
    /// Whether this entry is a tombstone rather than a live file.
    ///
    /// A tombstone is a ROW, never a missing row — that is what lets a laptop shut for a month
    /// converge instead of resurrecting deletes (D23, ninety days by contract).
    pub fn is_tombstone(&self) -> bool {
        self.deleted_at.is_some()
    }
}

/// One folder as the feed reports it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FolderEntry {
    /// `files.folders.id`.
    pub folder_id: String,
    /// The full cloud path.
    pub folder_path: String,
    /// The leaf name.
    pub folder_name: Option<String>,
    /// The parent folder.
    pub parent_id: Option<String>,
    /// RFC3339. Set means a tombstone.
    pub deleted_at: Option<String>,
    /// RFC3339 keyset half.
    pub updated_at: Option<String>,
    /// RFC3339.
    pub created_at: Option<String>,
    /// Whether the server considers this a protected system folder (D16: `is_system` means
    /// "protected" and is never repurposed as "hidden").
    pub is_system: Option<bool>,
}

impl FolderEntry {
    /// Whether this entry is a tombstone.
    pub fn is_tombstone(&self) -> bool {
        self.deleted_at.is_some()
    }
}

/// One page of the file feed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FilePage {
    /// The entries, in keyset order.
    #[serde(default)]
    pub files: Vec<FileEntry>,
    /// The cursor to pass next. `None` means this page ended at the stability cutoff.
    #[serde(default)]
    pub next_cursor: Option<String>,
    /// Whether more pages are waiting right now.
    #[serde(default)]
    pub has_more: bool,
    /// The server's clock when it answered — the only clock the engine trusts for feed ordering.
    #[serde(default)]
    pub server_time: Option<String>,
}

/// One page of the folder feed. Same contract, verbatim (SPEC-SERVER §2.4).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FolderPage {
    /// The entries, in keyset order.
    #[serde(default)]
    pub folders: Vec<FolderEntry>,
    /// The cursor to pass next.
    #[serde(default)]
    pub next_cursor: Option<String>,
    /// Whether more pages are waiting.
    #[serde(default)]
    pub has_more: bool,
    /// The server's clock when it answered.
    #[serde(default)]
    pub server_time: Option<String>,
    /// Legacy. SPEC-SERVER §2.4: it stays and is now always `false`, and is removed only once every
    /// client stops reading it — so this client reads it and never depends on it.
    #[serde(default)]
    pub truncated: bool,
}
