//! FS-L1 unit 4b — the production [`LocalIo`]: the real disk.
//!
//! Four things here are load-bearing, and each one is a failure class this engine's predecessor
//! shipped:
//!
//! 1. **Nothing partial ever appears at a user path** (invariant I4). A download is written to
//!    `<root>/.matrx-sync/tmp`, `fsync`ed, re-hashed, and only then `rename`d into place. A rename
//!    within one filesystem is atomic, so the user sees the old file or the new one and never a
//!    half-written one — not even if the machine loses power between the two.
//! 2. **The on-disk name is recovered from the filesystem, never from `path_nfc`** (invariant I8).
//!    `path_nfc` is a *key*. macOS hands back NFD names for the same characters the cloud stores
//!    as NFC, so composing a path by joining the key onto the root finds nothing for every name
//!    with an accent in it. [`RealLocalIo::resolve`] walks the tree segment by segment and matches
//!    by normalised form, and when two distinct on-disk names normalise to one key it refuses with
//!    [`ConflictKind::UnicodeCollision`] rather than picking one.
//! 3. **mtime is preserved** on a download, so the file the user sees carries the time it was last
//!    edited rather than the time it happened to arrive on this machine — and so the scanner's
//!    size/mtime fast path is not defeated on every synced file.
//! 4. **A local delete goes to the OS trash** when `sync.trash_local_deletes` says so. The
//!    executor never decides recoverability for itself; the knob arrives on the op.

use super::error::{ExecError, ExecResult};
use super::io::{LocalIo, LocalStat, Staged};
use crate::model::ConflictKind;
use crate::naming::nfc;
use std::path::{Path, PathBuf};

/// The real filesystem under one mapping root.
#[derive(Debug, Clone)]
pub struct RealLocalIo {
    root: PathBuf,
    volume_id: Option<String>,
    case_insensitive: bool,
}

impl RealLocalIo {
    /// A disk rooted at `root`.
    ///
    /// `case_insensitive` comes from the admission check's volume probe, not from the platform:
    /// macOS ships case-insensitive by default but case-sensitive volumes exist, and a Linux box
    /// can mount either. Guessing from `cfg!(target_os)` is how a case collision becomes a silent
    /// overwrite on the one machine that was formatted differently.
    pub fn new(root: impl Into<PathBuf>, case_insensitive: bool) -> Self {
        let root = root.into();
        let volume_id = volume_of(&root);
        RealLocalIo {
            root,
            volume_id,
            case_insensitive,
        }
    }

    /// `<root>/.matrx-sync/tmp` — where downloads land before they are verified (I4).
    pub fn staging_dir(&self) -> PathBuf {
        crate::scan::marker::staging_dir(&self.root)
    }

    /// Resolve a tree key to the real path on disk (I8).
    ///
    /// Returns the path whether or not it exists: a create needs the path it *would* take. What it
    /// refuses is ambiguity — two on-disk names that normalise to one key, or (on a
    /// case-insensitive volume) an existing name that differs from the key only by case.
    fn resolve(&self, path_nfc: &str) -> ExecResult<PathBuf> {
        let mut current = self.root.clone();
        for segment in path_nfc.split('/').filter(|s| !s.is_empty()) {
            let literal = current.join(segment);
            if literal.symlink_metadata().is_ok() {
                // The literal spelling resolved — but on a case-insensitive volume that proves
                // nothing. APFS is case-insensitive by default, so `report.txt` happily opens the
                // user's `Report.txt`, and writing the cloud's copy through that path destroys
                // theirs. The on-disk spelling is read back and compared; this test cost one
                // `realpath` per segment and caught exactly that overwrite in the battery below.
                if self.case_insensitive {
                    if let Some(real) = on_disk_name(&literal) {
                        if nfc(&real) != segment {
                            return Err(ExecError::NameCollision {
                                path: path_nfc.to_string(),
                                kind: ConflictKind::CaseCollision,
                            });
                        }
                    }
                }
                current = literal;
                continue;
            }
            // Not there under the key's own spelling. Either it does not exist at all, or the
            // filesystem stores it in a different normal form (NFD on a normalisation-sensitive
            // volume) or a different case.
            let mut matches: Vec<PathBuf> = Vec::new();
            let mut case_only = false;
            if let Ok(entries) = std::fs::read_dir(&current) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let Some(name) = name.to_str() else { continue };
                    if nfc(name) == segment {
                        matches.push(entry.path());
                    } else if self.case_insensitive && nfc(name).eq_ignore_ascii_case(segment) {
                        case_only = true;
                    }
                }
            }
            match matches.len() {
                0 => {
                    if case_only {
                        // The volume cannot hold both spellings, and choosing one destroys the
                        // other's content. That is a conflict for a human, never a silent pick.
                        return Err(ExecError::NameCollision {
                            path: path_nfc.to_string(),
                            kind: ConflictKind::CaseCollision,
                        });
                    }
                    // It does not exist — which is a perfectly good answer for a create. The walk
                    // CONTINUES with the literal spelling rather than returning it, because the
                    // segments after a missing directory still belong to the path. Returning here
                    // resolved `Archive/2026/loose.txt` to `Archive`, and a rename then wrote the
                    // file over the directory's own name.
                    current = literal;
                }
                1 => current = matches.remove(0),
                _ => {
                    return Err(ExecError::NameCollision {
                        path: path_nfc.to_string(),
                        kind: ConflictKind::UnicodeCollision,
                    })
                }
            }
        }
        Ok(current)
    }

    fn stat_of(&self, real: &Path, path_nfc: &str, with_hash: bool) -> ExecResult<Option<LocalStat>> {
        let meta = match std::fs::symlink_metadata(real) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(classify(path_nfc, &e)),
        };
        let is_dir = meta.is_dir();
        let content_hash = if is_dir || !with_hash {
            None
        } else {
            match crate::scan::sha256_file(real) {
                Ok(h) => Some(h),
                Err(crate::scan::HashError::Vanished) => return Ok(None),
                Err(crate::scan::HashError::Locked(d)) => {
                    return Err(ExecError::PermissionDenied {
                        path: path_nfc.to_string(),
                        detail: d,
                    })
                }
                Err(crate::scan::HashError::Io(d)) => {
                    return Err(ExecError::Io {
                        path: path_nfc.to_string(),
                        detail: d,
                    })
                }
            }
        };
        let identity = identity_of(real, &meta);
        Ok(Some(LocalStat {
            is_dir,
            size: if is_dir { None } else { Some(meta.len() as i64) },
            mtime_ns: mtime_ns(&meta),
            volume_id: identity.0.or_else(|| self.volume_id.clone()),
            file_id: identity.1,
            content_hash,
        }))
    }
}

#[async_trait::async_trait]
impl LocalIo for RealLocalIo {
    fn root(&self) -> &Path {
        &self.root
    }

    async fn stat(&self, path_nfc: &str, with_hash: bool) -> ExecResult<Option<LocalStat>> {
        let me = self.clone();
        let path = path_nfc.to_string();
        blocking(move || {
            let real = me.resolve(&path)?;
            me.stat_of(&real, &path, with_hash)
        })
        .await
    }

    async fn mkdir(&self, path_nfc: &str) -> ExecResult<LocalStat> {
        let me = self.clone();
        let path = path_nfc.to_string();
        blocking(move || {
            let real = me.resolve(&path)?;
            std::fs::create_dir_all(&real).map_err(|e| classify(&path, &e))?;
            Ok(me.stat_of(&real, &path, false)?.unwrap_or_default())
        })
        .await
    }

    async fn remove(&self, path_nfc: &str, to_trash: bool) -> ExecResult<()> {
        let me = self.clone();
        let path = path_nfc.to_string();
        blocking(move || {
            let real = me.resolve(&path)?;
            if std::fs::symlink_metadata(&real).is_err() {
                return Ok(()); // already gone; a delete that found nothing did its job
            }
            if to_trash {
                // `sync.trash_local_deletes`. The one recoverable deletion the engine performs —
                // and the reason a mistaken propagated delete is retrievable by the user rather
                // than only from a ninety-day cloud tombstone.
                return trash::delete(&real).map_err(|e| ExecError::Io {
                    path: path.clone(),
                    detail: format!("could not move it to the trash: {e}"),
                });
            }
            let outcome = if real.is_dir() {
                std::fs::remove_dir_all(&real)
            } else {
                std::fs::remove_file(&real)
            };
            match outcome {
                Ok(()) => Ok(()),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
                Err(e) => Err(classify(&path, &e)),
            }
        })
        .await
    }

    async fn rename(&self, from: &str, to: &str) -> ExecResult<LocalStat> {
        let me = self.clone();
        let (from, to) = (from.to_string(), to.to_string());
        blocking(move || {
            let src = me.resolve(&from)?;
            let dst = me.resolve(&to)?;
            if let Some(parent) = dst.parent() {
                std::fs::create_dir_all(parent).map_err(|e| classify(&to, &e))?;
            }
            std::fs::rename(&src, &dst).map_err(|e| classify(&from, &e))?;
            Ok(me.stat_of(&dst, &to, true)?.unwrap_or_default())
        })
        .await
    }

    async fn copy(&self, from: &str, to: &str) -> ExecResult<LocalStat> {
        let me = self.clone();
        let (from, to) = (from.to_string(), to.to_string());
        blocking(move || {
            let src = me.resolve(&from)?;
            let dst = me.resolve(&to)?;
            if let Some(parent) = dst.parent() {
                std::fs::create_dir_all(parent).map_err(|e| classify(&to, &e))?;
            }
            // A conflict copy is written through the staging directory for the same reason a
            // download is: a copy interrupted half way would otherwise leave a truncated file
            // wearing the name that is supposed to hold the user's rescued bytes (D7).
            let temp = me.staging_dir().join(format!(
                "copy-{:x}",
                {
                    use sha2::{Digest, Sha256};
                    let mut h = Sha256::new();
                    h.update(to.as_bytes());
                    h.finalize()
                }
            ));
            std::fs::create_dir_all(me.staging_dir()).map_err(|e| classify(&to, &e))?;
            std::fs::copy(&src, &temp).map_err(|e| classify(&from, &e))?;
            if let Ok(meta) = std::fs::metadata(&src) {
                preserve_mtime(&temp, mtime_ns(&meta));
            }
            std::fs::rename(&temp, &dst).map_err(|e| classify(&to, &e))?;
            Ok(me.stat_of(&dst, &to, true)?.unwrap_or_default())
        })
        .await
    }

    async fn stage(&self, path_nfc: &str) -> ExecResult<Staged> {
        let me = self.clone();
        let path = path_nfc.to_string();
        blocking(move || {
            let dir = me.staging_dir();
            std::fs::create_dir_all(&dir).map_err(|e| classify(&path, &e))?;
            use sha2::{Digest, Sha256};
            let mut h = Sha256::new();
            h.update(path.as_bytes());
            Ok(Staged {
                temp_path: dir.join(format!("dl-{:x}", h.finalize())),
                path_nfc: path,
            })
        })
        .await
    }

    async fn commit_staged(
        &self,
        staged: Staged,
        checksum: &str,
        mtime_ns_value: Option<i64>,
    ) -> ExecResult<LocalStat> {
        let me = self.clone();
        let checksum = checksum.to_string();
        blocking(move || {
            let Staged {
                temp_path,
                path_nfc,
            } = staged;
            // I4, the whole of it: hash what is actually on disk, refuse unless it is what the
            // plan said it would be, and only then let it wear a user-visible name.
            let found = match crate::scan::sha256_file(&temp_path) {
                Ok(h) => h,
                Err(e) => {
                    std::fs::remove_file(&temp_path).ok();
                    return Err(ExecError::Io {
                        path: path_nfc,
                        detail: format!("{e:?}"),
                    });
                }
            };
            if found != checksum {
                std::fs::remove_file(&temp_path).ok();
                return Err(ExecError::ChecksumMismatch {
                    path: path_nfc,
                    expected: checksum,
                    found,
                });
            }
            // fsync before the rename: a rename is atomic with respect to the directory entry,
            // but without the flush the entry can point at a file whose bytes never reached the
            // platter. That is the shape of "the file is there and it is empty after a crash".
            if let Ok(file) = std::fs::File::open(&temp_path) {
                file.sync_all().ok();
            }
            preserve_mtime(&temp_path, mtime_ns_value);
            let dst = match me.resolve(&path_nfc) {
                Ok(d) => d,
                Err(e) => {
                    std::fs::remove_file(&temp_path).ok();
                    return Err(e);
                }
            };
            if let Some(parent) = dst.parent() {
                if let Err(e) = std::fs::create_dir_all(parent) {
                    std::fs::remove_file(&temp_path).ok();
                    return Err(classify(&path_nfc, &e));
                }
            }
            if let Err(e) = std::fs::rename(&temp_path, &dst) {
                std::fs::remove_file(&temp_path).ok();
                return Err(classify(&path_nfc, &e));
            }
            Ok(me.stat_of(&dst, &path_nfc, true)?.unwrap_or_default())
        })
        .await
    }

    async fn discard_staged(&self, staged: Staged) {
        let _ = tokio::task::spawn_blocking(move || {
            std::fs::remove_file(&staged.temp_path).ok();
        })
        .await;
    }

    async fn source_of(&self, path_nfc: &str) -> ExecResult<PathBuf> {
        let me = self.clone();
        let path = path_nfc.to_string();
        blocking(move || me.resolve(&path)).await
    }
}

/// Run blocking filesystem work off the async runtime's threads.
async fn blocking<T, F>(f: F) -> ExecResult<T>
where
    T: Send + 'static,
    F: FnOnce() -> ExecResult<T> + Send + 'static,
{
    match tokio::task::spawn_blocking(f).await {
        Ok(r) => r,
        Err(e) => Err(ExecError::Io {
            path: String::new(),
            detail: format!("a filesystem task did not finish: {e}"),
        }),
    }
}

/// Turn an OS error into a named state. A full disk and a refused permission are the two the user
/// must be told about by name; everything else is `io_error` with the OS's own words.
fn classify(path: &str, e: &std::io::Error) -> ExecError {
    // `ErrorKind::StorageFull` is stable; the raw code is checked too because some filesystems
    // report ENOSPC through paths that do not map to it.
    let full = e.kind() == std::io::ErrorKind::StorageFull
        || matches!(e.raw_os_error(), Some(28) | Some(112));
    if full {
        return ExecError::DiskFull {
            detail: e.to_string(),
        };
    }
    match e.kind() {
        std::io::ErrorKind::PermissionDenied => ExecError::PermissionDenied {
            path: path.to_string(),
            detail: e.to_string(),
        },
        std::io::ErrorKind::NotFound => ExecError::Vanished {
            path: path.to_string(),
        },
        _ => ExecError::Io {
            path: path.to_string(),
            detail: e.to_string(),
        },
    }
}

/// The spelling the filesystem actually stores for `path`, as distinct from the spelling the
/// caller used to open it.
///
/// `realpath` answers this in one syscall on every platform we ship, and it answers it for exactly
/// the last component, which is the one a case collision can differ in. Listing the parent
/// directory would answer it too and costs O(entries) per path on a tree with thousands of files
/// in one folder.
fn on_disk_name(path: &Path) -> Option<String> {
    std::fs::canonicalize(path)
        .ok()?
        .file_name()?
        .to_str()
        .map(str::to_string)
}

fn mtime_ns(meta: &std::fs::Metadata) -> Option<i64> {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos() as i64)
}

fn preserve_mtime(path: &Path, ns: Option<i64>) {
    let Some(ns) = ns else { return };
    let seconds = ns.div_euclid(1_000_000_000);
    let nanos = ns.rem_euclid(1_000_000_000) as u32;
    let stamp = filetime::FileTime::from_unix_time(seconds, nanos);
    // Best effort by design: a volume that cannot set times (some network mounts) must not fail
    // the download that already succeeded. The cost is one extra hash on the next scan.
    filetime::set_file_mtime(path, stamp).ok();
}

#[cfg(unix)]
fn identity_of(_path: &Path, meta: &std::fs::Metadata) -> (Option<String>, Option<String>) {
    use std::os::unix::fs::MetadataExt;
    (Some(meta.dev().to_string()), Some(meta.ino().to_string()))
}

#[cfg(windows)]
fn identity_of(path: &Path, _meta: &std::fs::Metadata) -> (Option<String>, Option<String>) {
    // Windows file identity requires opening the path; `Metadata` does not carry it. The scanner
    // makes the same call for the same reason (`scan::identity`).
    let id = crate::scan::identity::identity_of_path(path);
    (id.volume_id, id.file_id)
}

#[cfg(unix)]
fn volume_of(root: &Path) -> Option<String> {
    use std::os::unix::fs::MetadataExt;
    std::fs::metadata(root).ok().map(|m| m.dev().to_string())
}

#[cfg(windows)]
fn volume_of(root: &Path) -> Option<String> {
    crate::scan::identity::identity_of_path(root).volume_id
}
