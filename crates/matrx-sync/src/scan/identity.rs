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

/// Windows: `volume_serial_number` and `file_index` are `MetadataExt` methods behind the unstable
/// `windows_by_handle` feature, so they are read through `GetFileInformationByHandle` instead —
/// the API SPEC-ENGINE §5 names `windows-sys` for.
///
/// **Written, not run.** There is no Windows machine in this lane; this path is compiled only on
/// Windows and is unproven until the Windows leg of FS-V2 exercises it. It is written rather than
/// left as `None` because `None` would silently turn every Windows rename into a delete plus a
/// full re-upload — a real product defect disguised as graceful degradation.
#[cfg(windows)]
pub(crate) fn identity_of_path(path: &std::path::Path) -> FileIdentity {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };

    let Ok(file) = std::fs::File::open(path) else {
        return FileIdentity {
            volume_id: None,
            file_id: None,
        };
    };
    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    let ok = unsafe { GetFileInformationByHandle(file.as_raw_handle() as HANDLE, &mut info) };
    if ok == 0 {
        return FileIdentity {
            volume_id: None,
            file_id: None,
        };
    }
    let file_id = (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow);
    FileIdentity {
        volume_id: Some(info.dwVolumeSerialNumber.to_string()),
        file_id: Some(file_id.to_string()),
    }
}

#[cfg(windows)]
pub(crate) fn identity_of(_metadata: &Metadata) -> FileIdentity {
    // Windows needs the path, not the metadata; the walker calls `identity_of_path` directly.
    FileIdentity {
        volume_id: None,
        file_id: None,
    }
}
