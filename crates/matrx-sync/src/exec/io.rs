//! The two seams the executor acts through.
//!
//! [`LocalIo`] is the disk and [`RemoteIo`] is the cloud. Both are traits for the same reason the
//! feed transport is: **the executor's decisions must be testable without a disk that can fill up
//! or a server that must be asked nicely to return 412.** The real implementations live in
//! [`super::local`] and [`super::remote`]; the fakes the fault battery drives live in
//! [`super::fakes`].
//!
//! Two rules hold across the seam:
//!
//! * **Bytes are never found through `path_nfc`** (invariant I8). A `path_nfc` is a *key*; the
//!   implementation composes the real on-disk path from the mapping root, and a NFD-on-disk name
//!   that NFC-normalises to the same key is resolved by the implementation, never guessed here.
//! * **Nothing partial ever appears at a user path** (invariant I4). A download goes to
//!   `.matrx-sync/tmp` through [`LocalIo::stage`] and reaches its real name only through
//!   [`LocalIo::commit_staged`], which re-hashes the bytes and refuses unless they match.

use super::error::ExecResult;
use std::path::{Path, PathBuf};

/// What the disk says about one node, after the act that produced it.
///
/// This is the raw material of the local half of the double confirmation (I1): the executor
/// re-`stat`s and re-hashes **after** the write and confirms from that, never from what it
/// intended to write.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct LocalStat {
    /// Whether it is a directory.
    pub is_dir: bool,
    /// Size in bytes; `None` for a directory.
    pub size: Option<i64>,
    /// Modification time in nanoseconds.
    pub mtime_ns: Option<i64>,
    /// The volume it sits on — half of the rename-detection key.
    pub volume_id: Option<String>,
    /// inode / NTFS FileId, as text — the other half.
    pub file_id: Option<String>,
    /// The SHA-256 THIS daemon computed, when the caller asked for it. Never carried forward from
    /// a previous scan (invariant I9).
    pub content_hash: Option<String>,
}

/// A file being assembled under `.matrx-sync/tmp`, not yet at any user-visible path (I4).
///
/// It is deliberately not `Copy` and not cloneable: it is consumed by
/// [`LocalIo::commit_staged`] or [`LocalIo::discard_staged`], so a code path that forgets both is
/// a compile-time unused-variable warning rather than a file left in the staging directory.
#[derive(Debug, PartialEq, Eq)]
pub struct Staged {
    /// Where the bytes are being written. Inside `.matrx-sync/tmp`, always.
    pub temp_path: PathBuf,
    /// The mapping-relative key the bytes are destined for.
    pub path_nfc: String,
}

/// What the cloud says about one object.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RemoteObject {
    /// `files.files.id`, or `files.folders.id` for a directory.
    pub file_id: String,
    /// The containing folder.
    pub folder_id: Option<String>,
    /// `files.files.current_version` — the feed's `version`, which every precondition is taken
    /// against (I3). **Never the platform row `version` column** (SPEC-SERVER §3.2).
    pub version: i64,
    /// The server-side SHA-256. `None` for a directory.
    pub checksum: Option<String>,
    /// Size in bytes as the server stored it.
    pub size: Option<i64>,
    /// Whether this object is a folder.
    pub is_dir: bool,
}

/// One upload, with its precondition and its idempotency key.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UploadRequest {
    /// The mapping-relative key.
    pub path_nfc: String,
    /// Where the bytes actually are, on this machine.
    pub source: PathBuf,
    /// The SHA-256 of those bytes, computed by this daemon before the send.
    pub content_hash: String,
    /// Size in bytes.
    pub size: i64,
    /// SPEC-SERVER §3.1: the SHA-256 the client believes the server holds at this path, or the
    /// literal `"absent"` for "I believe no alive row exists here". **Always sent** (S8).
    pub expected_checksum: String,
    /// The cloud file being replaced, when this is an update.
    pub remote_file_id: Option<String>,
    /// `sha256(mapping_id || file_path || content_sha256 || attempt_epoch)` — stable across
    /// process restarts, so a `kill -9` mid-upload resumes instead of duplicating
    /// (SPEC-SERVER §3.3 item 4).
    pub idempotency_key: String,
    /// The local filesystem mtime this client observed, RFC3339 UTC —
    /// `X-Matrx-Client-Modified-At` (SPEC-SERVER §2.2).
    pub client_modified_at: Option<String>,
    /// Bytes already sent, for a resumed transfer.
    pub resume_offset: Option<i64>,
    /// The TUS upload URL a previous attempt obtained.
    pub resume_url: Option<String>,
}

/// One rename or move, with its precondition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RenameRequest {
    /// The cloud file to move. Its `file_id` never changes (SPEC-SERVER §4.2).
    pub remote_file_id: String,
    /// The old mapping-relative key.
    pub from: String,
    /// The new mapping-relative key.
    pub to: String,
    /// The version the source must still be at (I3).
    pub expected_version: Option<i64>,
    /// The checksum the source must still carry (I3).
    pub expected_checksum: Option<String>,
    /// The idempotency key for the replay contract.
    pub idempotency_key: String,
}

/// One tombstone, with its precondition. Tombstones are retained ≥ 90 days by contract (D23).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeleteRequest {
    /// The cloud file to tombstone.
    pub remote_file_id: String,
    /// The mapping-relative key, for the error message.
    pub path_nfc: String,
    /// The version it must still be at (I3).
    pub expected_version: Option<i64>,
    /// The idempotency key for the replay contract.
    pub idempotency_key: String,
}

/// Progress of one transfer, reported so `mapping.progress` (C9) can be emitted without the
/// transport knowing what an event is.
pub trait ProgressSink: Send + Sync {
    /// `bytes` of `total` moved for `path_nfc`. Called often; the daemon throttles to 4 Hz.
    fn bytes(&self, path_nfc: &str, moved: u64, total: Option<u64>);
    /// A resumable transfer reached `offset`, at `upload_url`. Persisted so a crash resumes.
    fn checkpoint(&self, path_nfc: &str, offset: i64, upload_url: Option<&str>);
}

/// A sink that drops everything — the default when nobody is watching.
#[derive(Debug, Default, Clone, Copy)]
pub struct NoProgress;

impl ProgressSink for NoProgress {
    fn bytes(&self, _path_nfc: &str, _moved: u64, _total: Option<u64>) {}
    fn checkpoint(&self, _path_nfc: &str, _offset: i64, _upload_url: Option<&str>) {}
}

/// The disk, as the executor sees it.
#[async_trait::async_trait]
pub trait LocalIo: Send + Sync {
    /// The mapping root. Used for messages and for composing the staging directory.
    fn root(&self) -> &Path;

    /// `stat` one node, optionally hashing it. `None` means it is not there.
    async fn stat(&self, path_nfc: &str, with_hash: bool) -> ExecResult<Option<LocalStat>>;

    /// Create a directory and every missing parent.
    async fn mkdir(&self, path_nfc: &str) -> ExecResult<LocalStat>;

    /// Remove a file, to the OS trash when `to_trash` — the executor never decides recoverability
    /// for itself; `sync.trash_local_deletes` arrives on the op.
    async fn remove(&self, path_nfc: &str, to_trash: bool) -> ExecResult<()>;

    /// Move one path to another, creating the destination's parents.
    async fn rename(&self, from: &str, to: &str) -> ExecResult<LocalStat>;

    /// Copy one path to another — how a conflict copy is written (D7).
    async fn copy(&self, from: &str, to: &str) -> ExecResult<LocalStat>;

    /// Open a staging file for `path_nfc` under `.matrx-sync/tmp` (I4).
    async fn stage(&self, path_nfc: &str) -> ExecResult<Staged>;

    /// Hash the staged bytes, **refuse unless they match `checksum`**, preserve `mtime_ns` when
    /// given, and rename into place. The returned stat is the local half of the confirmation.
    async fn commit_staged(
        &self,
        staged: Staged,
        checksum: &str,
        mtime_ns: Option<i64>,
    ) -> ExecResult<LocalStat>;

    /// Throw staged bytes away. Called on every failure path, so a failed download leaves nothing.
    async fn discard_staged(&self, staged: Staged);

    /// Where the bytes of `path_nfc` actually live, for the uploader to read (I8).
    async fn source_of(&self, path_nfc: &str) -> ExecResult<PathBuf>;
}

/// The cloud, as the executor sees it.
#[async_trait::async_trait]
pub trait RemoteIo: Send + Sync {
    /// Create a cloud folder for `path_nfc`, and every missing parent.
    async fn mkdir(&self, path_nfc: &str) -> ExecResult<RemoteObject>;

    /// Send bytes, with the precondition and the idempotency key the request carries.
    async fn upload(
        &self,
        request: &UploadRequest,
        progress: &dyn ProgressSink,
    ) -> ExecResult<RemoteObject>;

    /// Fetch `remote_file_id` into `into`, which is always a staging path.
    async fn download(
        &self,
        remote_file_id: &str,
        path_nfc: &str,
        into: &Path,
        progress: &dyn ProgressSink,
    ) -> ExecResult<u64>;

    /// Rename or move, through `public.rename_file` / `public.move_file` — never a
    /// delete-and-recreate, so versions, shares, RAG jobs and grants survive (SPEC-SERVER §4.2).
    async fn rename(&self, request: &RenameRequest) -> ExecResult<RemoteObject>;

    /// Tombstone, with its precondition (D23).
    async fn tombstone(&self, request: &DeleteRequest) -> ExecResult<()>;
}
