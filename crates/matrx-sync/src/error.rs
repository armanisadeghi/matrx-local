//! The crate's error type.

use std::fmt;

/// Everything this crate can fail with.
#[derive(Debug)]
#[non_exhaustive]
pub enum SyncError {
    /// SQLite refused, or the journal file could not be opened.
    Sqlite(rusqlite::Error),
    /// The journal's `schema_version` is newer than this binary knows how to read.
    /// The daemon publishes `daemon_older_than_journal` and exits 0 (SPEC-ENGINE §4.2, E11).
    JournalNewerThanBinary {
        /// The version found in the journal.
        found: i64,
        /// The highest version this binary carries a migration for.
        supported: i64,
    },
    /// A write to `tree_synced` was attempted without both sides' confirmation (invariant I1),
    /// or against an op that is not leased by the caller.
    SyncedWriteRefused(String),
    /// A row could not be decoded into its typed struct.
    Decode(String),
    /// A value outside a knob's documented range (SPEC-ENGINE §2).
    KnobOutOfRange {
        /// The knob's registry name.
        knob: &'static str,
        /// What the caller asked for, rendered.
        value: String,
    },
}

impl fmt::Display for SyncError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SyncError::Sqlite(e) => write!(f, "journal database error: {e}"),
            SyncError::JournalNewerThanBinary { found, supported } => write!(
                f,
                "journal schema version {found} is newer than this binary's {supported}; \
                 update AI Matrx (daemon_older_than_journal)"
            ),
            SyncError::SyncedWriteRefused(why) => {
                write!(f, "refused to write the synced tree: {why}")
            }
            SyncError::Decode(what) => write!(f, "could not decode a journal row: {what}"),
            SyncError::KnobOutOfRange { knob, value } => {
                write!(f, "knob {knob} is out of its documented range: {value}")
            }
        }
    }
}

impl std::error::Error for SyncError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            SyncError::Sqlite(e) => Some(e),
            _ => None,
        }
    }
}

impl From<rusqlite::Error> for SyncError {
    fn from(e: rusqlite::Error) -> Self {
        SyncError::Sqlite(e)
    }
}

/// This crate's result alias.
pub type Result<T> = std::result::Result<T, SyncError>;
