//! The mapping's marker file (D8).
//!
//! SCOPE §3.1 item 6: *"folder marker file so an unmounted or unreadable root suspends the mapping
//! instead of emptying the cloud."* It is the cheapest possible answer to the most expensive
//! question a sync engine asks — *is this empty directory my folder, or is it a mount point whose
//! drive is not plugged in?* Without it, an unmounted volume reads as "the user deleted
//! everything", and the engine dutifully propagates that.
//!
//! The file is `<root>/.matrx-sync/marker` and its contents are the mapping's `marker_uuid`.
//! `.matrx-sync/` is never scanned, so the marker never syncs anywhere.

use std::path::{Path, PathBuf};

/// What the marker says about this root.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MarkerState {
    /// The marker is there and is this mapping's.
    Present,
    /// The directory is readable but the marker is not there: an unmounted volume, a restored
    /// backup, a folder the user replaced. Never "the user deleted everything".
    Missing,
    /// A marker is there but belongs to a DIFFERENT mapping — two mappings pointed at one folder,
    /// or a folder copied from another machine.
    Foreign {
        /// What the marker actually says.
        found: String,
    },
    /// The marker could not be read for some other reason.
    Unreadable(String),
}

impl MarkerState {
    /// Whether syncing may proceed.
    pub fn is_present(&self) -> bool {
        matches!(self, MarkerState::Present)
    }

    /// The honest state a mapping takes when this marker is not usable (SPEC-ENGINE §3.6 table c).
    pub const fn suspension_state(&self) -> Option<&'static str> {
        match self {
            MarkerState::Present => None,
            _ => Some("suspended_marker_missing"),
        }
    }
}

/// `<root>/.matrx-sync`.
pub fn state_dir(root: &Path) -> PathBuf {
    root.join(".matrx-sync")
}

/// `<root>/.matrx-sync/marker`.
pub fn marker_path(root: &Path) -> PathBuf {
    state_dir(root).join("marker")
}

/// `<root>/.matrx-sync/tmp` — where downloads land before they are verified and renamed into
/// place (invariant I4: nothing partial ever appears at a user path).
pub fn staging_dir(root: &Path) -> PathBuf {
    state_dir(root).join("tmp")
}

/// Write the marker, creating `.matrx-sync/` and the staging directory.
///
/// Called when a mapping is admitted, not on every scan: a scan that creates the marker it is about
/// to check would answer its own question.
pub fn write_marker(root: &Path, marker_uuid: &str) -> std::io::Result<()> {
    std::fs::create_dir_all(staging_dir(root))?;
    std::fs::write(marker_path(root), marker_uuid.as_bytes())
}

/// Read the marker and compare it with what this mapping expects.
pub fn check_marker(root: &Path, expected_uuid: &str) -> MarkerState {
    match std::fs::read_to_string(marker_path(root)) {
        Ok(found) => {
            let found = found.trim();
            if found == expected_uuid {
                MarkerState::Present
            } else {
                MarkerState::Foreign {
                    found: found.to_string(),
                }
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => MarkerState::Missing,
        Err(e) => MarkerState::Unreadable(e.to_string()),
    }
}
