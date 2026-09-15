//! File identity: the pair that survives a rename.
//!
//! SCOPE §3.1 item 3: *"File identity = (volume, inode/FileId) + size + mtime fast path … Move and
//! rename are metadata operations, detected by identity first, hash second."* The planner's
//! `detect_renames` reads exactly this pair out of `tree_local` and `tree_synced`; without it a
//! move becomes a delete plus a full re-upload of the bytes.

use std::fs::Metadata;

/// What the filesystem calls this file, independently of where it currently sits.
///
/// `None` on either half means the platform would not tell us. The planner treats a missing
/// identity as "cannot prove this is a move" and falls back to delete-plus-create, which is safe
/// but re-transfers the bytes — so a platform that cannot answer is a real cost, not a shrug.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileIdentity {
    /// The volume the file lives on. A file that moves between volumes is not the same file.
    pub volume_id: Option<String>,
    /// inode (unix) / NTFS FileId (Windows), as text.
    pub file_id: Option<String>,
}

impl FileIdentity {
    /// Whether both halves are known — the only case in which identity may decide anything.
    pub fn is_complete(&self) -> bool {
        self.volume_id.is_some() && self.file_id.is_some()
    }
}

#[cfg(unix)]
pub(crate) fn identity_of(metadata: &Metadata) -> FileIdentity {
    use std::os::unix::fs::MetadataExt;
    FileIdentity {
        volume_id: Some(metadata.dev().to_string()),
        file_id: Some(metadata.ino().to_string()),
    }
}

/// Windows exposes the volume serial and file index through stable `MetadataExt` accessors. Keep
/// this path in safe Rust: the crate forbids unsafe code, and the standard library already owns
/// the platform-specific handle work.
#[cfg(windows)]
pub(crate) fn identity_of_path(path: &std::path::Path) -> FileIdentity {
    let Ok(metadata) = std::fs::metadata(path) else {
        return FileIdentity {
            volume_id: None,
            file_id: None,
        };
    };
    identity_of(&metadata)
}

#[cfg(windows)]
pub(crate) fn identity_of(metadata: &Metadata) -> FileIdentity {
    use std::os::windows::fs::MetadataExt;
    FileIdentity {
        volume_id: metadata.volume_serial_number().map(|value| value.to_string()),
        file_id: metadata.file_index().map(|value| value.to_string()),
    }
}
