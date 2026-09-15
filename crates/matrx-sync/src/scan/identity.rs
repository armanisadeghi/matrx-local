//! File identity: the pair that survives a rename.
//!
//! SCOPE §3.1 item 3: *"File identity = (volume, inode/FileId) + size + mtime fast path … Move and
//! rename are metadata operations, detected by identity first, hash second."* The planner's
//! `detect_renames` reads exactly this pair out of `tree_local` and `tree_synced`; without it a
//! move becomes a delete plus a full re-upload of the bytes.

// Only the unix reader takes a `Metadata`; Windows identity comes from the path (below).
#[cfg(unix)]
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

/// Windows file identity is read through the `file-id` crate. It owns the platform handle work,
/// keeping this crate's `forbid(unsafe_code)` guarantee intact while still returning the volume
/// and NTFS identity pair required for rename detection.
#[cfg(windows)]
pub(crate) fn identity_of_path(path: &std::path::Path) -> FileIdentity {
    let Ok(identity) = file_id::get_file_id(path) else {
        return FileIdentity {
            volume_id: None,
            file_id: None,
        };
    };

    match identity {
        file_id::FileId::LowRes {
            volume_serial_number,
            file_index,
        } => FileIdentity {
            volume_id: Some(volume_serial_number.to_string()),
            file_id: Some(file_index.to_string()),
        },
        file_id::FileId::HighRes {
            volume_serial_number,
            file_id,
        } => FileIdentity {
            volume_id: Some(volume_serial_number.to_string()),
            file_id: Some(file_id.to_string()),
        },
        file_id::FileId::Inode { .. } => FileIdentity {
            volume_id: None,
            file_id: None,
        },
    }
}

// There is deliberately NO `identity_of(&Metadata)` on Windows. `std::fs::Metadata` carries no
// volume serial or file index there, so the only honest answer would be `(None, None)` — and a
// stub that always says "this platform cannot tell us" would make the planner fall back to
// delete-plus-reupload on every Windows rename while looking like a real implementation. The
// Windows walker calls `identity_of_path` instead (see `scan::walk::file_identity`), because file
// identity requires opening the path. A missing function is a compile error at the call site; a
// lying stub is a silent 100% re-transfer.
