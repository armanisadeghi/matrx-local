//! The size + mtime fast path — and every reason it must refuse to take it.
//!
//! SCOPE §3.1 item 2: *"The size+mtime fast path is a hint, never truth: any doubt hashes."*
//! Invariant I9 says the same from the other side: `(size, mtime_ns)` may only **skip** work, never
//! decide that two differing files are the same, and a `content_hash IS NULL` row is "not yet
//! known", never "unchanged".
//!
//! So this module answers one question — *may the previous scan's hash be carried forward?* — and
//! every "no" carries its reason, because a fast path whose refusals are invisible is a fast path
//! nobody can debug when it is wrong.

use crate::model::LocalNode;
use crate::scan::identity::FileIdentity;

/// Why a file must be hashed rather than trusted.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RehashReason {
    /// The previous scan had never seen this path.
    NotSeenBefore,
    /// It had seen it but had not hashed it yet.
    NoPreviousHash,
    /// The size differs — the one unambiguous signal.
    SizeChanged,
    /// The modification time differs.
    MtimeChanged,
    /// Same path, same size, same mtime — but a different inode. Something replaced the file, and
    /// a replacement that preserves size and mtime is exactly what an editor's atomic save looks
    /// like.
    IdentityChanged,
    /// Identity is not fully known on this platform, so it cannot vouch for anything.
    IdentityUnknown,
    /// The mtime is inside the volume's timestamp granularity of the last scan, so a write that
    /// landed in the same tick would be invisible. FAT and exFAT round to two seconds.
    MtimeWithinGranularity,
    /// The mtime is in the future relative to the scan, or the previous scan's clock was ahead of
    /// this one: a clock that moved backwards makes every comparison meaningless.
    ClockWentBackwards,
}

impl RehashReason {
    /// A short stable token for logs and activity rows.
    pub const fn as_str(self) -> &'static str {
        match self {
            RehashReason::NotSeenBefore => "not_seen_before",
            RehashReason::NoPreviousHash => "no_previous_hash",
            RehashReason::SizeChanged => "size_changed",
            RehashReason::MtimeChanged => "mtime_changed",
            RehashReason::IdentityChanged => "identity_changed",
            RehashReason::IdentityUnknown => "identity_unknown",
            RehashReason::MtimeWithinGranularity => "mtime_within_granularity",
            RehashReason::ClockWentBackwards => "clock_went_backwards",
        }
    }
}

/// The fast path's answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FastPath {
    /// The previous hash still describes these bytes.
    Reuse(String),
    /// It does not, or cannot be shown to. The file must be hashed.
    Rehash(RehashReason),
}

/// What the volume can promise about timestamps.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MtimeGranularity(pub i64);

impl MtimeGranularity {
    /// APFS, ext4, NTFS and friends: nanosecond or 100 ns timestamps. One millisecond is a
    /// deliberately conservative floor — being wrong here means re-hashing, which is slow; being
    /// wrong the other way means missing a write, which is data loss.
    pub const MODERN: MtimeGranularity = MtimeGranularity(1_000_000);
    /// FAT and exFAT round modification times to **two seconds**, so two different files written
    /// inside one tick are indistinguishable by mtime.
    pub const FAT: MtimeGranularity = MtimeGranularity(2_000_000_000);
}

/// Everything the decision needs, so the decision itself is a pure function that can be tested
/// without a filesystem.
#[derive(Debug, Clone)]
pub struct Observed {
    /// Size in bytes as just `stat`ed.
    pub size: i64,
    /// Modification time in nanoseconds as just `stat`ed.
    pub mtime_ns: i64,
    /// Identity as just read.
    pub identity: FileIdentity,
}

/// May this scan carry the previous scan's hash forward?
///
/// `scan_started_ns` is when THIS scan began; `granularity` is what the volume can promise. Both
/// are arguments rather than reads, so the rule is testable and so the caller decides the clock.
pub fn decide(
    previous: Option<&LocalNode>,
    observed: &Observed,
    scan_started_ns: i64,
    granularity: MtimeGranularity,
) -> FastPath {
    let Some(previous) = previous else {
        return FastPath::Rehash(RehashReason::NotSeenBefore);
    };
    let Some(hash) = previous.content_hash.as_ref() else {
        return FastPath::Rehash(RehashReason::NoPreviousHash);
    };
    if previous.size != Some(observed.size) {
        return FastPath::Rehash(RehashReason::SizeChanged);
    }
    if previous.mtime_ns != Some(observed.mtime_ns) {
        return FastPath::Rehash(RehashReason::MtimeChanged);
    }
    if !observed.identity.is_complete() {
        return FastPath::Rehash(RehashReason::IdentityUnknown);
    }
    if previous.volume_id != observed.identity.volume_id
        || previous.file_id != observed.identity.file_id
    {
        return FastPath::Rehash(RehashReason::IdentityChanged);
    }
    // A write that lands in the same timestamp tick as the scan is invisible to mtime, so a file
    // whose mtime is too close to now cannot be trusted until it settles.
    if observed.mtime_ns > scan_started_ns {
        return FastPath::Rehash(RehashReason::ClockWentBackwards);
    }
    if scan_started_ns.saturating_sub(observed.mtime_ns) < granularity.0 {
        return FastPath::Rehash(RehashReason::MtimeWithinGranularity);
    }
    FastPath::Reuse(hash.clone())
}
