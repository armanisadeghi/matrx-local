//! Fault-injectable stand-ins for the disk and the cloud.
//!
//! **A test seam, and a mock.** Green here proves the *executor's decisions* — the order it acts
//! in, what it refuses, what it confirms, what it resumes — and is never evidence that the product
//! works. Product evidence is the live-service tier (FS-L1 units 5–6) and FS-V2 on real machines.
//!
//! It is `pub` for the same reason [`crate::custody::clock::TestClock`] is: the batteries live in
//! `tests/`, which cannot reach a `#[cfg(test)]` item. Nothing in the daemon constructs one.
//!
//! [`FakeLocal`] writes real files under a root the caller owns, because the executor's contract
//! with the disk is about ordering and pre-images, not about bytes. The **production**
//! implementation, with atomic writes, the OS trash, mtime preservation and collision handling,
//! is [`super::local`]; this one is deliberately the simplest thing that can hold a file, plus a
//! fault switch the real one must never have.

use super::error::{ExecError, ExecResult};
use super::io::{
    DeleteRequest, LocalIo, LocalStat, ProgressSink, RemoteIo, RemoteObject, RenameRequest,
    UploadRequest,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// One injected fault: the next call naming `path` fails with `error`, `times` times.
#[derive(Debug, Clone)]
pub struct Fault {
    /// The mapping-relative path the fault applies to. `"*"` matches every path.
    pub path: String,
    /// Which call it applies to: `stat`, `mkdir`, `copy`, `rename`, `remove`, `stage`, `commit`,
    /// `upload`, `download`, `tombstone`, `remote_rename`, `remote_mkdir`.
    pub call: String,
    /// What to fail with.
    pub error: ExecError,
    /// How many times, then it stops firing.
    pub times: usize,
}

#[derive(Debug, Default)]
struct Faults {
    queue: Mutex<Vec<Fault>>,
}

impl Faults {
    fn take(&self, call: &str, path: &str) -> Option<ExecError> {
        let mut q = self.queue.lock().expect("fault queue");
        for f in q.iter_mut() {
            if f.call == call && (f.path == "*" || f.path == path) && f.times > 0 {
                f.times -= 1;
                return Some(f.error.clone());
            }
        }
        None
    }

    fn add(&self, f: Fault) {
        self.queue.lock().expect("fault queue").push(f);
    }
}

/// A disk under `root`, with a fault switch.
#[derive(Debug)]
pub struct FakeLocal {
    root: PathBuf,
    faults: Faults,
    /// Paths sent to the trash rather than unlinked, so a test can assert recoverability.
    pub trashed: Mutex<Vec<String>>,
}

impl FakeLocal {
    /// A disk rooted at `root`, which the caller owns and cleans up.
    pub fn new(root: impl Into<PathBuf>) -> Self {
        let root = root.into();
        std::fs::create_dir_all(root.join(".matrx-sync").join("tmp")).ok();
        FakeLocal {
            root,
            faults: Faults::default(),
            trashed: Mutex::new(Vec::new()),
        }
    }

    /// Arm a fault.
    pub fn inject(&self, fault: Fault) {
        self.faults.add(fault);
    }

    /// Put a file there directly, as the user would.
    pub fn put(&self, path_nfc: &str, content: &str) {
        let real = self.real(path_nfc);
        if let Some(parent) = real.parent() {
            std::fs::create_dir_all(parent).expect("fake disk parent");
        }
        std::fs::write(real, content).expect("fake disk write");
    }

    /// Read a file back, as the user would.
    pub fn read(&self, path_nfc: &str) -> Option<String> {
        std::fs::read_to_string(self.real(path_nfc)).ok()
    }

    /// The SHA-256 of a string — what a test uses to build a plan's expected hashes.
    pub fn hash_of(content: &str) -> String {
        let mut h = Sha256::new();
        h.update(content.as_bytes());
        format!("{:x}", h.finalize())
    }

    fn real(&self, path_nfc: &str) -> PathBuf {
        self.root.join(path_nfc)
    }

    fn stat_of(path: &Path, with_hash: bool) -> ExecResult<Option<LocalStat>> {
        let meta = match std::fs::metadata(path) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => {
                return Err(ExecError::Io {
                    path: path.display().to_string(),
                    detail: e.to_string(),
                })
            }
        };
        let is_dir = meta.is_dir();
        let content_hash = if is_dir || !with_hash {
            None
        } else {
            Some(crate::scan::sha256_file(path).map_err(|e| ExecError::Io {
                path: path.display().to_string(),
                detail: format!("{e:?}"),
            })?)
        };
        Ok(Some(LocalStat {
            is_dir,
            size: if is_dir { None } else { Some(meta.len() as i64) },
            mtime_ns: meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_nanos() as i64),
            volume_id: Some("fake-volume".to_string()),
            file_id: Some(format!("{}", path.display())),
            content_hash,
        }))
    }
}

#[async_trait::async_trait]
impl LocalIo for FakeLocal {
    fn root(&self) -> &Path {
        &self.root
    }

    async fn stat(&self, path_nfc: &str, with_hash: bool) -> ExecResult<Option<LocalStat>> {
        if let Some(e) = self.faults.take("stat", path_nfc) {
            return Err(e);
        }
        FakeLocal::stat_of(&self.real(path_nfc), with_hash)
    }

    async fn mkdir(&self, path_nfc: &str) -> ExecResult<LocalStat> {
        if let Some(e) = self.faults.take("mkdir", path_nfc) {
            return Err(e);
        }
        let real = self.real(path_nfc);
        std::fs::create_dir_all(&real).map_err(|e| ExecError::Io {
            path: path_nfc.to_string(),
            detail: e.to_string(),
        })?;
        Ok(FakeLocal::stat_of(&real, false)?.unwrap_or_default())
    }

    async fn remove(&self, path_nfc: &str, to_trash: bool) -> ExecResult<()> {
        if let Some(e) = self.faults.take("remove", path_nfc) {
            return Err(e);
        }
        let real = self.real(path_nfc);
        if to_trash {
            self.trashed
                .lock()
                .expect("trash")
                .push(path_nfc.to_string());
        }
        let outcome = if real.is_dir() {
            std::fs::remove_dir_all(&real)
        } else {
            std::fs::remove_file(&real)
        };
        match outcome {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(ExecError::Io {
                path: path_nfc.to_string(),
                detail: e.to_string(),
            }),
        }
    }

    async fn rename(&self, from: &str, to: &str) -> ExecResult<LocalStat> {
        if let Some(e) = self.faults.take("rename", from) {
            return Err(e);
        }
        let dst = self.real(to);
        if let Some(parent) = dst.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::rename(self.real(from), &dst).map_err(|e| ExecError::Io {
            path: from.to_string(),
            detail: e.to_string(),
        })?;
        Ok(FakeLocal::stat_of(&dst, true)?.unwrap_or_default())
    }

    async fn copy(&self, from: &str, to: &str) -> ExecResult<LocalStat> {
        if let Some(e) = self.faults.take("copy", from) {
            return Err(e);
        }
        let dst = self.real(to);
        if let Some(parent) = dst.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::copy(self.real(from), &dst).map_err(|e| ExecError::Io {
            path: from.to_string(),
            detail: e.to_string(),
        })?;
        Ok(FakeLocal::stat_of(&dst, true)?.unwrap_or_default())
    }

    async fn stage(&self, path_nfc: &str) -> ExecResult<super::io::Staged> {
        if let Some(e) = self.faults.take("stage", path_nfc) {
            return Err(e);
        }
        let dir = self.root.join(".matrx-sync").join("tmp");
        std::fs::create_dir_all(&dir).map_err(|e| ExecError::Io {
            path: path_nfc.to_string(),
            detail: e.to_string(),
        })?;
        let mut h = Sha256::new();
        h.update(path_nfc.as_bytes());
        Ok(super::io::Staged {
            temp_path: dir.join(format!("{:x}", h.finalize())),
            path_nfc: path_nfc.to_string(),
        })
    }

    async fn commit_staged(
        &self,
        staged: super::io::Staged,
        checksum: &str,
        _mtime_ns: Option<i64>,
    ) -> ExecResult<LocalStat> {
        if let Some(e) = self.faults.take("commit", &staged.path_nfc) {
            std::fs::remove_file(&staged.temp_path).ok();
            return Err(e);
        }
        let found =
            crate::scan::sha256_file(&staged.temp_path).map_err(|e| ExecError::Io {
                path: staged.path_nfc.clone(),
                detail: format!("{e:?}"),
            })?;
        if found != checksum {
            std::fs::remove_file(&staged.temp_path).ok();
            return Err(ExecError::ChecksumMismatch {
                path: staged.path_nfc.clone(),
                expected: checksum.to_string(),
                found,
            });
        }
        let dst = self.real(&staged.path_nfc);
        if let Some(parent) = dst.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::rename(&staged.temp_path, &dst).map_err(|e| ExecError::Io {
            path: staged.path_nfc.clone(),
            detail: e.to_string(),
        })?;
        Ok(FakeLocal::stat_of(&dst, true)?.unwrap_or_default())
    }

    async fn discard_staged(&self, staged: super::io::Staged) {
        std::fs::remove_file(&staged.temp_path).ok();
    }

    async fn source_of(&self, path_nfc: &str) -> ExecResult<PathBuf> {
        Ok(self.real(path_nfc))
    }
}

/// One object in the fake cloud.
#[derive(Debug, Clone)]
struct FakeObject {
    file_id: String,
    version: i64,
    checksum: Option<String>,
    size: Option<i64>,
    is_dir: bool,
    content: String,
    deleted: bool,
}

/// A cloud that enforces the preconditions and the idempotent-replay contract of SPEC-SERVER §3.
#[derive(Debug, Default)]
pub struct FakeRemote {
    objects: Mutex<BTreeMap<String, FakeObject>>,
    replays: Mutex<BTreeMap<String, String>>,
    faults: Faults,
    next_id: Mutex<u64>,
    /// Every upload the executor performed, in order — how a test proves a resumed plan did not
    /// send the same bytes twice.
    pub uploads: Mutex<Vec<String>>,
}

impl FakeRemote {
    /// An empty cloud.
    pub fn new() -> Self {
        FakeRemote::default()
    }

    /// Arm a fault.
    pub fn inject(&self, fault: Fault) {
        self.faults.add(fault);
    }

    /// Put an object there directly, as another device would.
    pub fn put(&self, path_nfc: &str, content: &str) -> RemoteObject {
        let mut objects = self.objects.lock().expect("fake cloud");
        let id = objects
            .get(path_nfc)
            .map(|o| o.file_id.clone())
            .unwrap_or_else(|| self.fresh("file"));
        let version = objects.get(path_nfc).map(|o| o.version).unwrap_or(0) + 1;
        let object = FakeObject {
            file_id: id,
            version,
            checksum: Some(FakeLocal::hash_of(content)),
            size: Some(content.len() as i64),
            is_dir: false,
            content: content.to_string(),
            deleted: false,
        };
        objects.insert(path_nfc.to_string(), object.clone());
        as_remote(&object)
    }

    /// Put a folder there directly, as another device or the browser would.
    pub fn put_dir(&self, path_nfc: &str) -> RemoteObject {
        let mut objects = self.objects.lock().expect("fake cloud");
        let object = FakeObject {
            file_id: self.fresh("folder"),
            version: 1,
            checksum: None,
            size: None,
            is_dir: true,
            content: String::new(),
            deleted: false,
        };
        objects.insert(path_nfc.to_string(), object.clone());
        as_remote(&object)
    }

    /// What the cloud holds at `path_nfc`, if it is alive.
    pub fn live(&self, path_nfc: &str) -> Option<String> {
        let objects = self.objects.lock().expect("fake cloud");
        objects
            .get(path_nfc)
            .filter(|o| !o.deleted)
            .map(|o| o.content.clone())
    }

    /// The `tree_remote` row a feed poll would produce for `path_nfc`.
    ///
    /// The real daemon builds these from feed pages (`crate::feed::apply`); a test that only needs
    /// the cloud's current truth gets it here instead of hand-writing rows.
    pub fn node_for(&self, path_nfc: &str) -> Option<crate::model::RemoteNode> {
        let objects = self.objects.lock().expect("fake cloud");
        let o = objects.get(path_nfc)?;
        Some(crate::model::RemoteNode {
            path_nfc: path_nfc.to_string(),
            is_dir: o.is_dir,
            size: o.size,
            remote_file_id: Some(o.file_id.clone()),
            remote_folder_id: None,
            remote_version: Some(o.version),
            checksum: o.checksum.clone(),
            client_modified_at: None,
            origin_device_id: None,
            deleted_at: if o.deleted {
                Some("2026-09-21T12:00:00Z".to_string())
            } else {
                None
            },
            seen_at: Some("2026-09-21T12:00:00Z".to_string()),
        })
    }

    /// Whether `path_nfc` carries a tombstone.
    pub fn tombstoned(&self, path_nfc: &str) -> bool {
        let objects = self.objects.lock().expect("fake cloud");
        objects.get(path_nfc).is_some_and(|o| o.deleted)
    }

    fn fresh(&self, prefix: &str) -> String {
        let mut n = self.next_id.lock().expect("fake cloud ids");
        *n += 1;
        format!("{prefix}-{n}")
    }
}

fn as_remote(o: &FakeObject) -> RemoteObject {
    RemoteObject {
        file_id: o.file_id.clone(),
        folder_id: None,
        version: o.version,
        checksum: o.checksum.clone(),
        size: o.size,
        is_dir: o.is_dir,
    }
}

#[async_trait::async_trait]
impl RemoteIo for FakeRemote {
    async fn mkdir(&self, path_nfc: &str) -> ExecResult<RemoteObject> {
        if let Some(e) = self.faults.take("remote_mkdir", path_nfc) {
            return Err(e);
        }
        let mut objects = self.objects.lock().expect("fake cloud");
        if let Some(existing) = objects.get(path_nfc).filter(|o| o.is_dir && !o.deleted) {
            return Ok(as_remote(existing));
        }
        let object = FakeObject {
            file_id: self.fresh("folder"),
            version: 1,
            checksum: None,
            size: None,
            is_dir: true,
            content: String::new(),
            deleted: false,
        };
        objects.insert(path_nfc.to_string(), object.clone());
        Ok(as_remote(&object))
    }

    async fn upload(
        &self,
        request: &UploadRequest,
        progress: &dyn ProgressSink,
    ) -> ExecResult<RemoteObject> {
        if let Some(e) = self.faults.take("upload", &request.path_nfc) {
            return Err(e);
        }
        // SPEC-SERVER §3.3 item 1: a replay with the same key and the same precondition returns
        // the stored original response, even if the row has since moved on. It never re-uploads.
        if let Some(stored) = self
            .replays
            .lock()
            .expect("fake cloud")
            .get(&request.idempotency_key)
            .cloned()
        {
            let objects = self.objects.lock().expect("fake cloud");
            if let Some(o) = objects.get(&stored) {
                return Ok(as_remote(o));
            }
        }
        let content = std::fs::read_to_string(&request.source).map_err(|e| ExecError::Io {
            path: request.path_nfc.clone(),
            detail: e.to_string(),
        })?;
        let mut objects = self.objects.lock().expect("fake cloud");
        let current = objects.get(&request.path_nfc).filter(|o| !o.deleted);
        let holds = match current {
            Some(o) => o.checksum.clone().unwrap_or_default(),
            None => "absent".to_string(),
        };
        if request.expected_checksum != holds {
            return Err(ExecError::PreconditionFailed(Box::new(super::error::Precondition {
                file_id: current.map(|o| o.file_id.clone()),
                current_checksum: current.and_then(|o| o.checksum.clone()),
                current_version: current.map(|o| o.version),
                current_size_bytes: current.and_then(|o| o.size),
                expected_checksum: Some(request.expected_checksum.clone()),
                ..Default::default()
            })));
        }
        let object = FakeObject {
            file_id: current
                .map(|o| o.file_id.clone())
                .unwrap_or_else(|| self.fresh("file")),
            version: current.map(|o| o.version).unwrap_or(0) + 1,
            checksum: Some(FakeLocal::hash_of(&content)),
            size: Some(content.len() as i64),
            is_dir: false,
            content,
            deleted: false,
        };
        objects.insert(request.path_nfc.clone(), object.clone());
        self.replays
            .lock()
            .expect("fake cloud")
            .insert(request.idempotency_key.clone(), request.path_nfc.clone());
        self.uploads
            .lock()
            .expect("fake cloud")
            .push(request.path_nfc.clone());
        progress.bytes(
            &request.path_nfc,
            request.size.max(0) as u64,
            Some(request.size.max(0) as u64),
        );
        Ok(as_remote(&object))
    }

    async fn download(
        &self,
        remote_file_id: &str,
        path_nfc: &str,
        into: &Path,
        progress: &dyn ProgressSink,
    ) -> ExecResult<u64> {
        if let Some(e) = self.faults.take("download", path_nfc) {
            return Err(e);
        }
        let objects = self.objects.lock().expect("fake cloud");
        let Some(object) = objects
            .values()
            .find(|o| o.file_id == remote_file_id && !o.deleted)
        else {
            return Err(ExecError::RemoteGone {
                path: path_nfc.to_string(),
            });
        };
        std::fs::write(into, object.content.as_bytes()).map_err(|e| ExecError::Io {
            path: path_nfc.to_string(),
            detail: e.to_string(),
        })?;
        let n = object.content.len() as u64;
        progress.bytes(path_nfc, n, Some(n));
        Ok(n)
    }

    async fn rename(&self, request: &RenameRequest) -> ExecResult<RemoteObject> {
        if let Some(e) = self.faults.take("remote_rename", &request.from) {
            return Err(e);
        }
        let mut objects = self.objects.lock().expect("fake cloud");
        let Some(object) = objects.get(&request.from).filter(|o| !o.deleted).cloned() else {
            return Err(ExecError::RemoteGone {
                path: request.from.clone(),
            });
        };
        if request.expected_version.is_some_and(|v| v != object.version) {
            return Err(ExecError::PreconditionFailed(Box::new(super::error::Precondition {
                file_id: Some(object.file_id.clone()),
                current_checksum: object.checksum.clone(),
                current_version: Some(object.version),
                ..Default::default()
            })));
        }
        if objects.get(&request.to).is_some_and(|o| !o.deleted) {
            return Err(ExecError::PathConflict {
                path: request.to.clone(),
                detail: "the cloud already holds a live row at that path".to_string(),
            });
        }
        objects.remove(&request.from);
        // SPEC-SERVER §4.2: `file_id` never changes; `updated_at` moves, so the change rides the
        // feed.
        let moved = FakeObject {
            version: object.version + 1,
            ..object
        };
        objects.insert(request.to.clone(), moved.clone());
        Ok(as_remote(&moved))
    }

    async fn tombstone(&self, request: &DeleteRequest) -> ExecResult<()> {
        if let Some(e) = self.faults.take("tombstone", &request.path_nfc) {
            return Err(e);
        }
        let mut objects = self.objects.lock().expect("fake cloud");
        let Some(object) = objects.get_mut(&request.path_nfc) else {
            return Err(ExecError::RemoteGone {
                path: request.path_nfc.clone(),
            });
        };
        if request.expected_version.is_some_and(|v| v != object.version) {
            let snapshot = object.clone();
            return Err(ExecError::PreconditionFailed(Box::new(super::error::Precondition {
                file_id: Some(snapshot.file_id),
                current_checksum: snapshot.checksum,
                current_version: Some(snapshot.version),
                ..Default::default()
            })));
        }
        object.deleted = true;
        object.checksum = None;
        object.version += 1;
        Ok(())
    }
}
