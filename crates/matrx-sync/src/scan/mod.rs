//! FS-L1 unit 1 — the real-filesystem scanner.
//!
//! This is the first module in the crate that touches a real disk. Everything it produces is a
//! [`crate::model::LocalTree`], which is what the pure planner reads, so the boundary stays exactly
//! where invariant I6 puts it: **the scanner writes `tree_local` and nothing else.**
//!
//! | Submodule | What it owns |
//! |---|---|
//! | [`identity`] | (volume, inode/FileId) — the pair that survives a rename (SCOPE item 3). |
//! | [`fastpath`] | Whether the previous scan's hash may be carried forward, and every reason it may not (SCOPE item 2, invariant I9). |
//! | [`walk`] | Walking a mapping root into a tree, without following symlinks, with NFC keys. |
//! | [`hash`] | Whole-file SHA-256 with bounded concurrency — the server's checksum, not a faster one. |
//! | [`marker`] | The `.matrx-sync/marker` file (D8): the cheapest answer to "is this my folder, or an unmounted drive?" |

pub mod fastpath;
pub mod hash;
pub mod identity;
pub mod marker;
pub mod walk;

pub use fastpath::{FastPath, MtimeGranularity, Observed, RehashReason};
pub use hash::{hash_concurrency_default, hash_files, sha256_file, HashError, HashOutcome, HashRequest};
pub use identity::FileIdentity;
pub use marker::{check_marker, write_marker, MarkerState};
pub use walk::{
    scan_root, NormalisationCollision, ScanOptions, ScanReport, SkipReason, SkippedEntry,
};
