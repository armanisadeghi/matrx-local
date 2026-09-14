//! The typed rows and trees the journal stores and the planner reads.
//!
//! Every struct here mirrors one table in `migrations/001_initial.sql` (SPEC-ENGINE §4). The
//! planner (`crate::planner`) reads [`LocalTree`], [`RemoteTree`] and [`SyncedTree`] and writes
//! nothing — invariant I6.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// A mapping's sync direction (`mappings.direction`, D6).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Direction {
    /// Both sides are authoritative; divergence becomes a conflict copy.
    TwoWay,
    /// Local is authority: remote edits are not applied; local deletes propagate only if
    /// `sync.upload_only_propagates_deletes`.
    UploadOnly,
    /// The cloud is authority: local edits are preserved and flagged, never overwritten.
    DownloadOnly,
}

impl Direction {
    /// The wire spelling stored in `mappings.direction`.
    pub const fn as_str(self) -> &'static str {
        match self {
            Direction::TwoWay => "two_way",
            Direction::UploadOnly => "upload_only",
            Direction::DownloadOnly => "download_only",
        }
    }

    /// Parse the wire spelling.
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "two_way" => Some(Direction::TwoWay),
            "upload_only" => Some(Direction::UploadOnly),
            "download_only" => Some(Direction::DownloadOnly),
            _ => None,
        }
    }
}

/// A `tree_local` row: what the scanner last saw on disk.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalNode {
    /// NFC-normalised path relative to the mapping root (invariant I8).
    pub path_nfc: String,
    /// Directory rows carry no content.
    pub is_dir: bool,
    /// Size in bytes; `None` for directories.
    pub size: Option<i64>,
    /// Modification time in nanoseconds. A hint only, never a decision (invariant I9).
    pub mtime_ns: Option<i64>,
    /// Volume identity — half of the rename-detection key.
    pub volume_id: Option<String>,
    /// inode (unix) / NTFS FileId, as text — the other half.
    pub file_id: Option<String>,
    /// SHA-256 hex. `None` means "not yet known", never "unchanged" (invariant I9).
    pub content_hash: Option<String>,
    /// RFC3339 timestamp of the scan that produced this row.
    pub scanned_at: Option<String>,
}

/// A `tree_remote` row: what the change feed last told us the cloud holds.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RemoteNode {
    /// NFC-normalised path relative to the mapping root.
    pub path_nfc: String,
    /// Directory rows carry no content.
    pub is_dir: bool,
    /// Size in bytes as the server reports it.
    pub size: Option<i64>,
    /// `files.files.id`.
    pub remote_file_id: Option<String>,
    /// The containing `files.folders.id`.
    pub remote_folder_id: Option<String>,
    /// The row version the 412 precondition is taken against (invariant I3).
    pub remote_version: Option<i64>,
    /// Server-side SHA-256.
    pub checksum: Option<String>,
    /// RFC3339 client modification time as recorded in the cloud.
    pub client_modified_at: Option<String>,
    /// `app_instances.id` of the device that wrote it (C13).
    pub origin_device_id: Option<String>,
    /// RFC3339 tombstone time; a tombstoned row is not a live remote node.
    pub deleted_at: Option<String>,
    /// RFC3339 timestamp of the feed page that produced this row.
    pub seen_at: Option<String>,
}

impl RemoteNode {
    /// Whether this row is a live remote node rather than a tombstone.
    pub fn is_live(&self) -> bool {
        self.deleted_at.is_none()
    }
}

/// A `tree_synced` row: the last state BOTH sides confirmed (invariant I1).
///
/// There is no public constructor path that writes one of these to the journal without both
/// confirmations; see [`crate::journal::Journal::confirm_op`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SyncedNode {
    /// NFC-normalised path relative to the mapping root.
    pub path_nfc: String,
    /// Directory rows carry no content.
    pub is_dir: bool,
    /// Size in bytes at the last successful sync.
    pub size: Option<i64>,
    /// Local mtime at the last successful sync.
    pub mtime_ns: Option<i64>,
    /// Volume identity at the last successful sync.
    pub volume_id: Option<String>,
    /// inode / FileId at the last successful sync.
    pub file_id: Option<String>,
    /// The hash THIS daemon computed after the write (invariant I2). Never `None` for files.
    pub content_hash: Option<String>,
    /// `files.files.id` as returned by the server.
    pub remote_file_id: String,
    /// The row version the server returned.
    pub remote_version: i64,
    /// The checksum the server returned (invariant I2). Never `None` for files.
    pub checksum: Option<String>,
    /// `download_only`: a local edit deliberately preserved instead of overwritten (D6).
    pub local_edit_flagged: bool,
    /// RFC3339 time of the confirming transaction.
    pub synced_at: String,
}

/// A tree keyed by `path_nfc`, ordered so every traversal is deterministic.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Tree<N> {
    nodes: BTreeMap<String, N>,
}

// Derived `Default` would demand `N: Default`, which no node type satisfies (there is no
// meaningful empty `SyncedNode`). An empty tree needs nothing of its element type.
impl<N> Default for Tree<N> {
    fn default() -> Self {
        Tree {
            nodes: BTreeMap::new(),
        }
    }
}

impl<N> Tree<N> {
    /// An empty tree.
    pub fn new() -> Self {
        Tree {
            nodes: BTreeMap::new(),
        }
    }

    /// The node at `path`, if any.
    pub fn get(&self, path: &str) -> Option<&N> {
        self.nodes.get(path)
    }

    /// Whether a node exists at `path`.
    pub fn contains(&self, path: &str) -> bool {
        self.nodes.contains_key(path)
    }

    /// Every node, in path order.
    pub fn iter(&self) -> impl Iterator<Item = (&String, &N)> {
        self.nodes.iter()
    }

    /// Every path, in order.
    pub fn paths(&self) -> impl Iterator<Item = &String> {
        self.nodes.keys()
    }

    /// How many nodes the tree holds.
    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    /// Whether the tree is empty.
    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    /// Insert or replace the node at `path`.
    pub fn insert(&mut self, path: impl Into<String>, node: N) {
        self.nodes.insert(path.into(), node);
    }

    /// Remove the node at `path`, returning it.
    pub fn remove(&mut self, path: &str) -> Option<N> {
        self.nodes.remove(path)
    }

    /// Mutable access to the node at `path`.
    pub fn get_mut(&mut self, path: &str) -> Option<&mut N> {
        self.nodes.get_mut(path)
    }
}

impl<N> FromIterator<(String, N)> for Tree<N> {
    fn from_iter<I: IntoIterator<Item = (String, N)>>(iter: I) -> Self {
        Tree {
            nodes: iter.into_iter().collect(),
        }
    }
}

/// The scanner's view of the disk.
pub type LocalTree = Tree<LocalNode>;
/// The feed's view of the cloud.
pub type RemoteTree = Tree<RemoteNode>;
/// The last double-confirmed state.
pub type SyncedTree = Tree<SyncedNode>;

/// `ops.kind` — the executor's vocabulary (SPEC-ENGINE §4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[allow(missing_docs)] // each variant is its own documentation; the spec's list is the contract
pub enum OpKind {
    UploadCreate,
    UploadUpdate,
    DownloadCreate,
    DownloadUpdate,
    MkdirLocal,
    MkdirRemote,
    RenameLocal,
    RenameRemote,
    MoveRemote,
    DeleteLocal,
    DeleteRemote,
    WriteConflictCopy,
    /// D6's preserve-and-flag, added by SPEC-ENGINE amendment 1 (2026-09-13, `6c226529`): the op a
    /// `download_only` mapping emits when the local file diverged. A journal write, not a
    /// transfer, but under the same double-confirmation discipline as every other terminal op.
    PreserveLocalEdit,
    IndexEnqueue,
    Unindex,
}

impl OpKind {
    /// The wire spelling stored in `ops.kind`.
    pub const fn as_str(self) -> &'static str {
        match self {
            OpKind::UploadCreate => "upload_create",
            OpKind::UploadUpdate => "upload_update",
            OpKind::DownloadCreate => "download_create",
            OpKind::DownloadUpdate => "download_update",
            OpKind::MkdirLocal => "mkdir_local",
            OpKind::MkdirRemote => "mkdir_remote",
            OpKind::RenameLocal => "rename_local",
            OpKind::RenameRemote => "rename_remote",
            OpKind::MoveRemote => "move_remote",
            OpKind::DeleteLocal => "delete_local",
            OpKind::DeleteRemote => "delete_remote",
            OpKind::WriteConflictCopy => "write_conflict_copy",
            OpKind::PreserveLocalEdit => "preserve_local_edit",
            OpKind::IndexEnqueue => "index_enqueue",
            OpKind::Unindex => "unindex",
        }
    }

    /// Parse the wire spelling.
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "upload_create" => OpKind::UploadCreate,
            "upload_update" => OpKind::UploadUpdate,
            "download_create" => OpKind::DownloadCreate,
            "download_update" => OpKind::DownloadUpdate,
            "mkdir_local" => OpKind::MkdirLocal,
            "mkdir_remote" => OpKind::MkdirRemote,
            "rename_local" => OpKind::RenameLocal,
            "rename_remote" => OpKind::RenameRemote,
            "move_remote" => OpKind::MoveRemote,
            "delete_local" => OpKind::DeleteLocal,
            "delete_remote" => OpKind::DeleteRemote,
            "write_conflict_copy" => OpKind::WriteConflictCopy,
            "preserve_local_edit" => OpKind::PreserveLocalEdit,
            "index_enqueue" => OpKind::IndexEnqueue,
            "unindex" => OpKind::Unindex,
            _ => return None,
        })
    }
}

/// `conflicts.kind` — the complete set (SPEC-ENGINE §4).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[allow(missing_docs)] // the spec's list is the contract
pub enum ConflictKind {
    BothModified,
    ModifyVsDelete,
    DeleteVsModify,
    CaseCollision,
    UnicodeCollision,
    WhitespaceCollision,
    IllegalName,
    PathTooLong,
    TypeChanged,
}

impl ConflictKind {
    /// The wire spelling stored in `conflicts.kind`.
    pub const fn as_str(self) -> &'static str {
        match self {
            ConflictKind::BothModified => "both_modified",
            ConflictKind::ModifyVsDelete => "modify_vs_delete",
            ConflictKind::DeleteVsModify => "delete_vs_modify",
            ConflictKind::CaseCollision => "case_collision",
            ConflictKind::UnicodeCollision => "unicode_collision",
            ConflictKind::WhitespaceCollision => "whitespace_collision",
            ConflictKind::IllegalName => "illegal_name",
            ConflictKind::PathTooLong => "path_too_long",
            ConflictKind::TypeChanged => "type_changed",
        }
    }

    /// Parse the wire spelling.
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "both_modified" => ConflictKind::BothModified,
            "modify_vs_delete" => ConflictKind::ModifyVsDelete,
            "delete_vs_modify" => ConflictKind::DeleteVsModify,
            "case_collision" => ConflictKind::CaseCollision,
            "unicode_collision" => ConflictKind::UnicodeCollision,
            "whitespace_collision" => ConflictKind::WhitespaceCollision,
            "illegal_name" => ConflictKind::IllegalName,
            "path_too_long" => ConflictKind::PathTooLong,
            "type_changed" => ConflictKind::TypeChanged,
            _ => return None,
        })
    }
}

/// `ops.state`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[allow(missing_docs)] // the spec's CHECK is the contract
pub enum OpState {
    Ready,
    Leased,
    Blocked,
    Failed,
    Done,
}

impl OpState {
    /// The wire spelling stored in `ops.state`.
    pub const fn as_str(self) -> &'static str {
        match self {
            OpState::Ready => "ready",
            OpState::Leased => "leased",
            OpState::Blocked => "blocked",
            OpState::Failed => "failed",
            OpState::Done => "done",
        }
    }

    /// Parse the wire spelling.
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "ready" => OpState::Ready,
            "leased" => OpState::Leased,
            "blocked" => OpState::Blocked,
            "failed" => OpState::Failed,
            "done" => OpState::Done,
            _ => return None,
        })
    }
}

/// An `ops` row.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OpRow {
    /// `ops.id`, assigned by SQLite.
    pub id: i64,
    /// The mapping whose queue this op belongs to (invariant I7).
    pub mapping_id: String,
    /// Ordering within the mapping's queue.
    pub seq: i64,
    /// What the executor must do.
    pub kind: OpKind,
    /// The path the op acts on.
    pub path_nfc: String,
    /// The destination path for renames and conflict copies.
    pub target_path_nfc: Option<String>,
    /// The cloud file this op targets.
    pub remote_file_id: Option<String>,
    /// The 412 precondition's checksum (invariant I3).
    pub expected_checksum: Option<String>,
    /// The 412 precondition's version (invariant I3).
    pub expected_version: Option<i64>,
    /// The pre-image a destructive local act must match (invariant I3).
    pub expected_local_hash: Option<String>,
    /// Where the op is in its lifecycle.
    pub state: OpState,
    /// How many times it has been attempted.
    pub attempts: i64,
    /// RFC3339 backoff deadline.
    pub next_attempt_at: Option<String>,
    /// Who holds the lease.
    pub lease_owner: Option<String>,
    /// RFC3339 lease expiry.
    pub lease_expires_at: Option<String>,
    /// `sha256(mapping_id, kind, path_nfc, expected_version|expected_local_hash)` (invariant I5).
    pub idempotency_key: String,
    /// Resumable-transfer state: bytes already sent.
    pub transfer_offset: Option<i64>,
    /// Resumable-transfer state: the TUS upload URL.
    pub transfer_upload_url: Option<String>,
    /// The last failure's code.
    pub error_code: Option<String>,
    /// The last failure's detail.
    pub error_detail: Option<String>,
    /// RFC3339 creation time.
    pub created_at: Option<String>,
    /// RFC3339 last update.
    pub updated_at: Option<String>,
}

/// A `conflicts` row.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConflictRow {
    /// `conflicts.id`, assigned by the caller.
    pub id: String,
    /// The mapping the conflict belongs to.
    pub mapping_id: String,
    /// The path in dispute.
    pub path_nfc: String,
    /// Which conflict class this is.
    pub kind: ConflictKind,
    /// The local side's hash, when there is one.
    pub local_hash: Option<String>,
    /// The remote side's file id, when there is one.
    pub remote_file_id: Option<String>,
    /// The remote side's checksum, when there is one.
    pub remote_checksum: Option<String>,
    /// Where the losing copy was written, when a copy was written (D7).
    pub conflict_copy_path: Option<String>,
    /// RFC3339 detection time.
    pub detected_at: String,
    /// RFC3339 resolution time.
    pub resolved_at: Option<String>,
    /// How it was resolved.
    pub resolution: Option<String>,
}

/// A `mappings` row.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MappingRow {
    /// `files.sync_mappings.id`.
    pub id: String,
    /// Always explicit, never inherited (D12).
    pub organization_id: String,
    /// `org_root` or `folder`.
    pub cloud_kind: String,
    /// The cloud folder, when `cloud_kind = 'folder'`.
    pub cloud_folder_id: Option<String>,
    /// The absolute local root.
    pub local_root: String,
    /// The volume the root lives on.
    pub local_root_volume_id: String,
    /// The mapping's direction.
    pub direction: Direction,
    /// User-written (C4): `active` | `paused` | `removed`.
    pub desired_state: String,
    /// Daemon-written, from the honest-state enum's `mapping` scope (C3).
    pub state: String,
    /// The sentence behind the state.
    pub state_detail: Option<String>,
    /// Per-mapping knobs as JSON (C11).
    pub knobs: String,
    /// The file feed cursor.
    pub file_cursor: Option<String>,
    /// The folder feed cursor.
    pub folder_cursor: Option<String>,
    /// The cloud row version the daemon last wrote against.
    pub cloud_row_version: Option<i64>,
    /// The contents of `.matrx-sync/marker` (D8).
    pub marker_uuid: String,
    /// RFC3339 creation time.
    pub created_at: Option<String>,
    /// RFC3339 last update.
    pub updated_at: Option<String>,
    /// RFC3339 last successful sync.
    pub last_sync_at: Option<String>,
    /// RFC3339 last full rescan.
    pub last_full_rescan_at: Option<String>,
}
