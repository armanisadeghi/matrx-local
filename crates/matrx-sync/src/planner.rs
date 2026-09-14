//! FS-C3 — the pure planner (D2, SPEC-ENGINE §4.3).
//!
//! [`plan`] is a **function**: three trees, a direction, a resolved [`Knobs`] and a
//! [`PlanContext`] go in; a [`Plan`] comes out. It performs **no IO, reads no clock and draws no
//! randomness** — the device name and today's date, the only two values a conflict name needs,
//! arrive inside [`PlanContext`]. It writes nothing, not even to the journal (invariant I6).
//!
//! That purity is the whole point: it is what lets the property tests in
//! `tests/planner_properties.rs` drive hundreds of thousands of generated three-tree
//! configurations through it and assert SPEC-ENGINE §4.3's four invariants, and what lets the
//! FS-C4 harness replay a failing seed exactly.
//!
//! # What a plan is, and is not
//!
//! Every decision is **described**, never performed. A name that cannot exist on Windows, a
//! deletion large enough to trip the circuit breaker, a conflict — each comes back as a typed
//! item for the executor (or the user) to act on. The brief's "noop" is the empty plan:
//! [`Plan::is_empty`] is how a caller recognises the fixed point.

use crate::knobs::Knobs;
use crate::model::{
    ConflictKind, Direction, LocalNode, LocalTree, RemoteNode, RemoteTree, SyncedNode, SyncedTree,
};
use crate::naming::{self, NameVerdict};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// Whether an op creates something new or updates something that exists.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Change {
    /// The path does not exist on the destination side yet.
    Create,
    /// The path exists and its content is being replaced.
    Update,
}

/// Which side an op acts on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Side {
    /// The local filesystem.
    Local,
    /// The cloud.
    Remote,
}

/// Why the planner asked for the mapping to be suspended.
///
/// A suspension is a **plan item**: the planner never performs it, and a plan that carries one
/// carries nothing else, so no destructive op can slip out alongside it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum SuspendReason {
    /// The mass-delete circuit breaker tripped (D8).
    MassDelete {
        /// How many deletions this plan would have performed.
        planned_deletes: usize,
        /// How many paths the mapping currently has under management (`tree_synced`).
        tracked_items: usize,
        /// The `sync.mass_delete_percent` value in force.
        percent_threshold: u8,
        /// The `sync.mass_delete_count` value in force.
        count_threshold: u32,
    },
}

impl SuspendReason {
    /// The honest-state value the mapping row takes while suspended (SPEC-ENGINE §3.6 table c).
    pub const fn honest_state(&self) -> &'static str {
        match self {
            SuspendReason::MassDelete { .. } => "suspended_mass_delete",
        }
    }
}

/// One typed operation. Nothing here has happened yet.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum PlanOp {
    /// I9: this local file's hash is not yet known, so no decision may be taken about it. The
    /// planner emits this instead of guessing from `(size, mtime)`.
    HashRequest {
        /// The mapping-relative path to hash.
        path: String,
    },
    /// Create a directory on disk.
    MkdirLocal {
        /// The mapping-relative path.
        path: String,
    },
    /// Create a folder in the cloud.
    MkdirRemote {
        /// The mapping-relative path.
        path: String,
    },
    /// Send local bytes to the cloud.
    Upload {
        /// The mapping-relative path.
        path: String,
        /// Create or update.
        change: Change,
        /// The 412 precondition's version (I3).
        expected_version: Option<i64>,
        /// The 412 precondition's checksum (I3).
        expected_checksum: Option<String>,
        /// The hash of the bytes being sent.
        local_hash: String,
    },
    /// Fetch cloud bytes to disk, via `.matrx-sync/tmp` and a checksum check (I4).
    Download {
        /// The mapping-relative path.
        path: String,
        /// Create or update.
        change: Change,
        /// The cloud file to fetch.
        remote_file_id: Option<String>,
        /// The version being fetched.
        remote_version: Option<i64>,
        /// The checksum the downloaded bytes must match before the rename into place (I4).
        checksum: String,
        /// The pre-image the file on disk must still match before being replaced (I3).
        expected_local_hash: Option<String>,
    },
    /// Remove a local file. `to_trash` carries `sync.trash_local_deletes` so the executor never
    /// decides recoverability for itself.
    DeleteLocalToTrash {
        /// The mapping-relative path.
        path: String,
        /// Whether it goes to the OS trash rather than being unlinked.
        to_trash: bool,
        /// The pre-image the file must still match (I3).
        expected_local_hash: Option<String>,
    },
    /// Tombstone a cloud file. Tombstones are retained ≥ 90 days by contract (D23).
    DeleteRemoteTombstone {
        /// The mapping-relative path.
        path: String,
        /// The cloud file to tombstone.
        remote_file_id: Option<String>,
        /// The 412 precondition's version (I3).
        expected_version: Option<i64>,
    },
    /// Move one path to another on one side, preserving identity instead of re-transferring bytes.
    Rename {
        /// Which side moves.
        side: Side,
        /// The old mapping-relative path.
        from: String,
        /// The new mapping-relative path.
        to: String,
    },
    /// Write the losing copy under its D7 name. Both copies then reach the cloud.
    ConflictCopy {
        /// The disputed path.
        path: String,
        /// Where the losing bytes go.
        copy_path: String,
        /// Which conflict class this is.
        kind: ConflictKind,
        /// The losing side's hash.
        local_hash: Option<String>,
    },
    /// `download_only`: keep the local bytes, mark the row, never overwrite (D6).
    FlagLocalEdit {
        /// The mapping-relative path.
        path: String,
        /// The preserved local hash.
        local_hash: Option<String>,
    },
    /// Open a conflict row that needs no copy — a name that cannot exist, a collision, or a
    /// delete-versus-modify race.
    RecordConflict {
        /// The disputed path.
        path: String,
        /// Which conflict class this is.
        kind: ConflictKind,
        /// The sentence the surface shows.
        detail: String,
    },
    /// Both sides already hold identical content that was never recorded as synced. The executor
    /// writes the `tree_synced` row from the two confirmations it already has (I1) — no transfer.
    RecordSynced {
        /// The mapping-relative path.
        path: String,
    },
    /// Both sides are gone; drop the bookkeeping row.
    ForgetSynced {
        /// The mapping-relative path.
        path: String,
    },
}

impl PlanOp {
    /// The path this op acts on.
    pub fn path(&self) -> &str {
        match self {
            PlanOp::HashRequest { path }
            | PlanOp::MkdirLocal { path }
            | PlanOp::MkdirRemote { path }
            | PlanOp::Upload { path, .. }
            | PlanOp::Download { path, .. }
            | PlanOp::DeleteLocalToTrash { path, .. }
            | PlanOp::DeleteRemoteTombstone { path, .. }
            | PlanOp::ConflictCopy { path, .. }
            | PlanOp::FlagLocalEdit { path, .. }
            | PlanOp::RecordConflict { path, .. }
            | PlanOp::RecordSynced { path }
            | PlanOp::ForgetSynced { path } => path,
            PlanOp::Rename { from, .. } => from,
        }
    }

    /// Whether this op destroys user-visible data on one side.
    pub fn is_destructive(&self) -> bool {
        matches!(
            self,
            PlanOp::DeleteLocalToTrash { .. } | PlanOp::DeleteRemoteTombstone { .. }
        )
    }
}

/// The values the planner would otherwise have to read from the world.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanContext {
    /// The device name a conflict copy is named after (D7).
    pub device_name: String,
    /// Today's date as `YYYY-MM-DD`, injected. The planner reads no clock.
    pub today: String,
    /// Paths carrying an unresolved `conflicts` row.
    ///
    /// A path awaiting the user's decision gets **no ops at all** — that is what the mapping state
    /// `needs_conflict_resolution` means, and it is what makes the planner reach a fixed point
    /// instead of re-proposing the same unresolvable work every round. The set is a value like
    /// every other input, so the planner stays pure.
    pub open_conflicts: BTreeSet<String>,
}

impl Default for PlanContext {
    fn default() -> Self {
        PlanContext {
            device_name: "this device".to_string(),
            today: "1970-01-01".to_string(),
            open_conflicts: BTreeSet::new(),
        }
    }
}

/// The planner's answer.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Plan {
    /// The operations to perform, in path order. Empty is the fixed point.
    pub ops: Vec<PlanOp>,
    /// Set when the circuit breaker tripped. When set, `ops` is empty.
    pub suspended: Option<SuspendReason>,
}

impl Plan {
    /// Whether there is nothing to do — the brief's "noop".
    pub fn is_empty(&self) -> bool {
        self.ops.is_empty() && self.suspended.is_none()
    }
}

/// Plan one mapping's next round of work.
///
/// # The mass-delete circuit breaker (D8)
///
/// The registry gives the breaker two knobs, `sync.mass_delete_percent` (50) and
/// `sync.mass_delete_count` (1000), and does not state how they combine. They are combined with
/// **AND**: a plan trips the breaker when its deletions are both a large enough *fraction* of what
/// the mapping manages and a large enough *absolute number*. That is the only combinator under
/// which both defaults are individually coherent — a pure OR on the percentage would suspend a
/// two-file folder for one deletion, which no champion does. **This combinator is not stated by the
/// frozen spec and is reported as a gap; it is a one-line change if the amendment rules otherwise.**
///
/// A tripped plan carries the [`SuspendReason`] **and nothing else**, so no destructive op can be
/// executed alongside a suspension.
pub fn plan(
    local: &LocalTree,
    remote: &RemoteTree,
    synced: &SyncedTree,
    direction: Direction,
    knobs: &Knobs,
    ctx: &PlanContext,
) -> Plan {
    let mut ops: Vec<PlanOp> = Vec::new();

    // Collisions first: two distinct paths that fold to one name cannot both exist on a
    // case-insensitive volume, so neither is acted on until the user resolves it.
    let mut colliding: BTreeSet<String> = BTreeSet::new();
    let mut by_key: BTreeMap<String, Vec<&str>> = BTreeMap::new();
    for p in local.paths() {
        by_key
            .entry(naming::collision_key(p))
            .or_default()
            .push(p.as_str());
    }
    for (p, n) in remote.iter() {
        if n.is_live() {
            let e = by_key.entry(naming::collision_key(p)).or_default();
            if !e.contains(&p.as_str()) {
                e.push(p.as_str());
            }
        }
    }
    for (_, paths) in by_key.iter().filter(|(_, v)| v.len() > 1) {
        let first = paths[0];
        // Every member of the group is quarantined, so every member carries a conflict row. A
        // group where only the second path was reported left the first one silently frozen —
        // invisible to the user and, in the harness, unconverged. Found by
        // `converges_per_direction`; see TESTING.md.
        for member in paths.iter() {
            colliding.insert((*member).to_string());
            if ctx.open_conflicts.contains(*member) {
                continue;
            }
            let against = if *member == first { paths[1] } else { first };
            ops.push(PlanOp::RecordConflict {
                path: (*member).to_string(),
                kind: naming::collision_kind(member, against),
                detail: format!(
                    "“{member}” and “{against}” cannot both exist on a case-insensitive volume; \
                     rename one of them to continue"
                ),
            });
        }
    }

    // Rename detection: a local path that is new, whose (volume, inode) identity belongs to a
    // synced row whose old path has disappeared locally, with identical content, is a move — not
    // a delete plus an upload.
    let renames = detect_renames(local, remote, synced, direction, &colliding);
    for (from, to) in &renames {
        ops.push(PlanOp::Rename {
            side: Side::Remote,
            from: from.clone(),
            to: to.clone(),
        });
    }
    let renamed_from: BTreeSet<&str> = renames.iter().map(|(f, _)| f.as_str()).collect();
    let renamed_to: BTreeSet<&str> = renames.iter().map(|(_, t)| t.as_str()).collect();

    let mut paths: BTreeSet<&str> = BTreeSet::new();
    paths.extend(local.paths().map(String::as_str));
    paths.extend(remote.paths().map(String::as_str));
    paths.extend(synced.paths().map(String::as_str));

    for path in paths {
        if ctx.open_conflicts.contains(path)
            || colliding.contains(path)
            || renamed_from.contains(path)
            || renamed_to.contains(path)
        {
            continue;
        }
        let l = local.get(path);
        let r = remote.get(path).filter(|n| n.is_live());
        let s = synced.get(path);

        // I9: an unhashed local file is "not yet known", never "unchanged". No op may be emitted
        // for it; the planner asks for the hash instead.
        if let Some(l) = l {
            if !l.is_dir && l.content_hash.is_none() {
                ops.push(PlanOp::HashRequest {
                    path: path.to_string(),
                });
                continue;
            }
        }
        // The same rule on the remote side: a live remote file the feed has not given a checksum
        // for is not yet a value, so no decision is taken about it this round.
        if let Some(r) = r {
            if !r.is_dir && r.checksum.is_none() {
                continue;
            }
        }
        // A path that is a directory on one side and a file on the other is never resolved
        // silently.
        if let (Some(l), Some(r)) = (l, r) {
            if l.is_dir != r.is_dir {
                ops.push(PlanOp::RecordConflict {
                    path: path.to_string(),
                    kind: ConflictKind::TypeChanged,
                    detail: format!(
                        "“{path}” is a {} here and a {} in the cloud",
                        kind_word(l.is_dir),
                        kind_word(r.is_dir)
                    ),
                });
                continue;
            }
        }

        let is_dir = l.map(|n| n.is_dir).or(r.map(|n| n.is_dir)).or(s.map(|n| n.is_dir));
        let decided = match (direction, is_dir) {
            (_, None) => Vec::new(),
            (Direction::TwoWay, Some(true)) => decide_two_way_dir(path, l, r, s, knobs),
            (Direction::TwoWay, Some(false)) => decide_two_way_file(path, l, r, s, knobs, ctx),
            (Direction::UploadOnly, Some(dir)) => decide_upload_only(path, l, r, s, dir, knobs),
            (Direction::DownloadOnly, Some(dir)) => decide_download_only(path, l, r, s, dir, knobs),
        };
        ops.extend(decided);
    }

    let planned_deletes = ops.iter().filter(|o| o.is_destructive()).count();
    let tracked_items = synced.len();
    if trips_breaker(planned_deletes, tracked_items, knobs) {
        return Plan {
            ops: Vec::new(),
            suspended: Some(SuspendReason::MassDelete {
                planned_deletes,
                tracked_items,
                percent_threshold: knobs.mass_delete_percent,
                count_threshold: knobs.mass_delete_count,
            }),
        };
    }

    Plan {
        ops,
        suspended: None,
    }
}

fn kind_word(is_dir: bool) -> &'static str {
    if is_dir {
        "folder"
    } else {
        "file"
    }
}

/// See [`plan`]'s documentation for why the two knobs are combined with AND.
fn trips_breaker(planned_deletes: usize, tracked_items: usize, knobs: &Knobs) -> bool {
    if planned_deletes == 0 || tracked_items == 0 {
        return false;
    }
    let over_count = planned_deletes >= knobs.mass_delete_count as usize;
    let over_percent =
        planned_deletes.saturating_mul(100) >= tracked_items * knobs.mass_delete_percent as usize;
    over_count && over_percent
}

/// A local file that is new at `to`, absent at `from`, carries the identity `tree_synced` recorded
/// for `from`, and holds the same content, is a move.
fn detect_renames(
    local: &LocalTree,
    remote: &RemoteTree,
    synced: &SyncedTree,
    direction: Direction,
    colliding: &BTreeSet<String>,
) -> Vec<(String, String)> {
    if direction == Direction::DownloadOnly {
        // The cloud is authority; a local move is a local edit, not an instruction to the cloud.
        return Vec::new();
    }
    let mut by_identity: BTreeMap<(String, String), &SyncedNode> = BTreeMap::new();
    for (_, n) in synced.iter() {
        if n.is_dir {
            continue;
        }
        if let (Some(v), Some(f)) = (&n.volume_id, &n.file_id) {
            by_identity.insert((v.clone(), f.clone()), n);
        }
    }
    let mut out = Vec::new();
    let mut consumed: BTreeSet<String> = BTreeSet::new();
    for (path, l) in local.iter() {
        if l.is_dir || synced.contains(path) || colliding.contains(path) {
            continue;
        }
        let (Some(v), Some(f)) = (&l.volume_id, &l.file_id) else {
            continue;
        };
        let Some(old) = by_identity.get(&(v.clone(), f.clone())) else {
            continue;
        };
        if local.contains(&old.path_nfc) || consumed.contains(&old.path_nfc) {
            continue; // the old path still exists: this is a copy, not a move
        }
        if colliding.contains(&old.path_nfc) {
            continue;
        }
        if l.content_hash.is_none() || l.content_hash != old.content_hash {
            continue; // content changed as well: handled as a delete plus a create
        }
        if remote.get(path).is_some_and(|n| n.is_live()) {
            continue; // something already occupies the destination in the cloud
        }
        consumed.insert(old.path_nfc.clone());
        out.push((old.path_nfc.clone(), path.clone()));
    }
    out
}

// --------------------------------------------------------------------- two_way

fn decide_two_way_dir(
    path: &str,
    l: Option<&LocalNode>,
    r: Option<&RemoteNode>,
    s: Option<&SyncedNode>,
    knobs: &Knobs,
) -> Vec<PlanOp> {
    match (l.is_some(), r.is_some(), s.is_some()) {
        (true, false, false) => create_remote_dir(path, knobs),
        (false, true, false) => create_local_dir(path, knobs),
        (true, true, false) => vec![PlanOp::RecordSynced {
            path: path.to_string(),
        }],
        (true, true, true) => Vec::new(),
        (false, true, true) => vec![PlanOp::DeleteRemoteTombstone {
            path: path.to_string(),
            remote_file_id: r.and_then(|n| n.remote_file_id.clone()),
            expected_version: r.and_then(|n| n.remote_version),
        }],
        (true, false, true) => vec![PlanOp::DeleteLocalToTrash {
            path: path.to_string(),
            to_trash: knobs.trash_local_deletes,
            expected_local_hash: None,
        }],
        (false, false, true) => vec![PlanOp::ForgetSynced {
            path: path.to_string(),
        }],
        (false, false, false) => Vec::new(),
    }
}

fn decide_two_way_file(
    path: &str,
    l: Option<&LocalNode>,
    r: Option<&RemoteNode>,
    s: Option<&SyncedNode>,
    knobs: &Knobs,
    ctx: &PlanContext,
) -> Vec<PlanOp> {
    match (l, r, s) {
        (None, None, None) => Vec::new(),
        (None, None, Some(_)) => vec![PlanOp::ForgetSynced {
            path: path.to_string(),
        }],
        // A creation on one side only.
        (Some(l), None, None) => upload(path, l, None, Change::Create, knobs),
        (None, Some(r), None) => download(path, r, None, Change::Create, knobs),
        // Both sides hold it, nothing recorded: adopt if identical, conflict if not.
        (Some(l), Some(r), None) => {
            if l.content_hash == r.checksum {
                vec![PlanOp::RecordSynced {
                    path: path.to_string(),
                }]
            } else {
                conflict_copy(path, l, r, knobs, ctx)
            }
        }
        // Tracked on both sides.
        (Some(l), Some(r), Some(s)) => {
            let local_changed = l.content_hash != s.content_hash;
            let remote_changed = r.checksum != s.checksum;
            match (local_changed, remote_changed) {
                (false, false) => Vec::new(),
                (true, false) => upload(path, l, Some(r), Change::Update, knobs),
                (false, true) => download(path, r, Some(s), Change::Update, knobs),
                (true, true) => {
                    if l.content_hash == r.checksum {
                        vec![PlanOp::RecordSynced {
                            path: path.to_string(),
                        }]
                    } else {
                        conflict_copy(path, l, r, knobs, ctx)
                    }
                }
            }
        }
        // Deleted locally.
        (None, Some(r), Some(s)) => {
            if r.checksum == s.checksum {
                vec![PlanOp::DeleteRemoteTombstone {
                    path: path.to_string(),
                    remote_file_id: r.remote_file_id.clone(),
                    expected_version: r.remote_version,
                }]
            } else {
                // delete-versus-modify: the remote bytes are newer than what we deleted. Restore
                // them rather than destroying them, and say so.
                let mut ops = download(path, r, None, Change::Create, knobs);
                ops.push(PlanOp::RecordConflict {
                    path: path.to_string(),
                    kind: ConflictKind::DeleteVsModify,
                    detail: format!(
                        "“{path}” was deleted here but changed in the cloud; the cloud copy was \
                         restored"
                    ),
                });
                ops
            }
        }
        // Deleted in the cloud.
        (Some(l), None, Some(s)) => {
            if l.content_hash == s.content_hash {
                vec![PlanOp::DeleteLocalToTrash {
                    path: path.to_string(),
                    to_trash: knobs.trash_local_deletes,
                    expected_local_hash: s.content_hash.clone(),
                }]
            } else {
                let mut ops = upload(path, l, None, Change::Create, knobs);
                ops.push(PlanOp::RecordConflict {
                    path: path.to_string(),
                    kind: ConflictKind::ModifyVsDelete,
                    detail: format!(
                        "“{path}” was deleted in the cloud but changed here; the local copy was \
                         kept and uploaded"
                    ),
                });
                ops
            }
        }
    }
}

// ----------------------------------------------------------------- upload_only

fn decide_upload_only(
    path: &str,
    l: Option<&LocalNode>,
    r: Option<&RemoteNode>,
    s: Option<&SyncedNode>,
    is_dir: bool,
    knobs: &Knobs,
) -> Vec<PlanOp> {
    if is_dir {
        return match (l.is_some(), r.is_some(), s.is_some()) {
            (true, false, _) => create_remote_dir(path, knobs),
            (false, _, true) if knobs.upload_only_propagates_deletes => {
                vec![PlanOp::DeleteRemoteTombstone {
                    path: path.to_string(),
                    remote_file_id: r.and_then(|n| n.remote_file_id.clone()),
                    expected_version: r.and_then(|n| n.remote_version),
                }]
            }
            (false, false, true) => vec![PlanOp::ForgetSynced {
                path: path.to_string(),
            }],
            _ => Vec::new(),
        };
    }
    match (l, r, s) {
        // Local is authority: whatever the cloud holds, the local bytes win on the next push.
        (Some(l), None, _) => upload(path, l, None, Change::Create, knobs),
        (Some(l), Some(r), _) => {
            if l.content_hash == r.checksum {
                if s.is_some_and(|s| s.content_hash == l.content_hash) {
                    Vec::new()
                } else {
                    vec![PlanOp::RecordSynced {
                        path: path.to_string(),
                    }]
                }
            } else {
                upload(path, l, Some(r), Change::Update, knobs)
            }
        }
        // Gone locally, tracked: propagate the delete only if the knob says so (D6).
        (None, Some(r), Some(_)) => {
            if knobs.upload_only_propagates_deletes {
                vec![PlanOp::DeleteRemoteTombstone {
                    path: path.to_string(),
                    remote_file_id: r.remote_file_id.clone(),
                    expected_version: r.remote_version,
                }]
            } else {
                Vec::new()
            }
        }
        (None, None, Some(_)) => vec![PlanOp::ForgetSynced {
            path: path.to_string(),
        }],
        // Remote-only and never synced: not created locally (D6). Left standing, deliberately.
        (None, Some(_), None) => Vec::new(),
        (None, None, None) => Vec::new(),
    }
}

// --------------------------------------------------------------- download_only

fn decide_download_only(
    path: &str,
    l: Option<&LocalNode>,
    r: Option<&RemoteNode>,
    s: Option<&SyncedNode>,
    is_dir: bool,
    knobs: &Knobs,
) -> Vec<PlanOp> {
    if is_dir {
        return match (l.is_some(), r.is_some(), s.is_some()) {
            (false, true, _) => create_local_dir(path, knobs),
            (true, false, true) => vec![PlanOp::DeleteLocalToTrash {
                path: path.to_string(),
                to_trash: knobs.trash_local_deletes,
                expected_local_hash: None,
            }],
            (false, false, true) => vec![PlanOp::ForgetSynced {
                path: path.to_string(),
            }],
            _ => Vec::new(),
        };
    }
    match (l, r, s) {
        (None, Some(r), _) => download(path, r, None, Change::Create, knobs),
        (Some(l), Some(r), s) => {
            if l.content_hash == r.checksum {
                // The two sides agree. Any flag that was standing is answered by the agreement.
                if s.is_some_and(|s| s.content_hash == l.content_hash && !s.local_edit_flagged) {
                    Vec::new()
                } else {
                    vec![PlanOp::RecordSynced {
                        path: path.to_string(),
                    }]
                }
            } else if s.is_some_and(|s| s.local_edit_flagged) {
                // D6: a preserved local edit is NEVER overwritten — not on the round it is
                // flagged, and not on any later round. Once flagged, `local_changed` computed
                // against the synced row goes false (the flag recorded the local hash), so a
                // test that only looked at `local_changed` would download over the user's bytes
                // on the very next round. Found by `preserves_data`; see TESTING.md.
                Vec::new()
            } else if l.content_hash != s.map_or(r.checksum.clone(), |s| s.content_hash.clone()) {
                vec![PlanOp::FlagLocalEdit {
                    path: path.to_string(),
                    local_hash: l.content_hash.clone(),
                }]
            } else {
                download(path, r, s, Change::Update, knobs)
            }
        }
        // Gone from the cloud: remove the local copy unless it carries a preserved local edit.
        (Some(l), None, Some(s)) => {
            if s.local_edit_flagged {
                Vec::new()
            } else if l.content_hash == s.content_hash {
                vec![PlanOp::DeleteLocalToTrash {
                    path: path.to_string(),
                    to_trash: knobs.trash_local_deletes,
                    expected_local_hash: s.content_hash.clone(),
                }]
            } else {
                vec![PlanOp::FlagLocalEdit {
                    path: path.to_string(),
                    local_hash: l.content_hash.clone(),
                }]
            }
        }
        (None, None, Some(_)) => vec![PlanOp::ForgetSynced {
            path: path.to_string(),
        }],
        // A purely local file the cloud never had: the cloud is authority, but it did not ask for
        // this file to be deleted either. Left alone, deliberately (D6).
        (Some(_), None, None) => Vec::new(),
        (None, None, None) => Vec::new(),
    }
}

// ------------------------------------------------------------------- emitters

fn create_remote_dir(path: &str, knobs: &Knobs) -> Vec<PlanOp> {
    match name_guard(path, knobs) {
        Some(op) => vec![op],
        None => vec![PlanOp::MkdirRemote {
            path: path.to_string(),
        }],
    }
}

fn create_local_dir(path: &str, knobs: &Knobs) -> Vec<PlanOp> {
    match name_guard(path, knobs) {
        Some(op) => vec![op],
        None => vec![PlanOp::MkdirLocal {
            path: path.to_string(),
        }],
    }
}

fn upload(
    path: &str,
    l: &LocalNode,
    r: Option<&RemoteNode>,
    change: Change,
    knobs: &Knobs,
) -> Vec<PlanOp> {
    if let Some(op) = name_guard(path, knobs) {
        return vec![op];
    }
    let Some(local_hash) = l.content_hash.clone() else {
        // Unreachable in `plan`, which emits a HashRequest first; kept honest rather than
        // unwrapped, because a future caller may reach this function directly.
        return vec![PlanOp::HashRequest {
            path: path.to_string(),
        }];
    };
    vec![PlanOp::Upload {
        path: path.to_string(),
        change,
        expected_version: r.and_then(|n| n.remote_version),
        expected_checksum: r.and_then(|n| n.checksum.clone()),
        local_hash,
    }]
}

fn download(
    path: &str,
    r: &RemoteNode,
    s: Option<&SyncedNode>,
    change: Change,
    knobs: &Knobs,
) -> Vec<PlanOp> {
    if let Some(op) = name_guard(path, knobs) {
        return vec![op];
    }
    let Some(checksum) = r.checksum.clone() else {
        // The caller filters these out; a checksum-less remote row is "not yet known".
        return Vec::new();
    };
    vec![PlanOp::Download {
        path: path.to_string(),
        change,
        remote_file_id: r.remote_file_id.clone(),
        remote_version: r.remote_version,
        checksum,
        expected_local_hash: s.and_then(|n| n.content_hash.clone()),
    }]
}

fn conflict_copy(
    path: &str,
    l: &LocalNode,
    r: &RemoteNode,
    knobs: &Knobs,
    ctx: &PlanContext,
) -> Vec<PlanOp> {
    let copy_path = naming::conflict_copy_path(path, &ctx.device_name, &ctx.today, knobs);
    let mut ops = vec![PlanOp::ConflictCopy {
        path: path.to_string(),
        copy_path: copy_path.clone(),
        kind: ConflictKind::BothModified,
        local_hash: l.content_hash.clone(),
    }];
    // D7: both copies reach the cloud — but the copy's upload is NOT bolted onto this plan. Once
    // the copy exists on disk it is an ordinary local-only path, and the next round's normal
    // per-path logic uploads it with a real precondition taken from the remote tree. Emitting it
    // here with `expected_version: None` livelocked whenever the copy already existed in the
    // cloud (a crash between the upload and its confirmation, or the other device having written
    // it): every attempt drew a 412, the plan was thrown away, and the identical plan came back.
    // The FS-C4 harness found that; see TESTING.md.
    let _ = &copy_path;
    // The winner replaces the local file, whose bytes are now safe in the copy. I3 still applies:
    // the replacement carries the pre-image it expects to find, so if the file changed again
    // between planning and execution the executor aborts instead of overwriting something it
    // never saw.
    if let Some(checksum) = r.checksum.clone() {
        ops.push(PlanOp::Download {
            path: path.to_string(),
            change: Change::Update,
            remote_file_id: r.remote_file_id.clone(),
            remote_version: r.remote_version,
            checksum,
            expected_local_hash: l.content_hash.clone(),
        });
    }
    ops
}

fn name_guard(path: &str, knobs: &Knobs) -> Option<PlanOp> {
    match naming::check_name(path, knobs) {
        NameVerdict::Ok => None,
        NameVerdict::TooLong(kind) => Some(PlanOp::RecordConflict {
            path: path.to_string(),
            kind,
            detail: format!(
                "“{path}” is longer than this account allows ({} characters per path, {} per name)",
                knobs.max_path_chars, knobs.max_segment_chars
            ),
        }),
        NameVerdict::Illegal => Some(PlanOp::RecordConflict {
            path: path.to_string(),
            kind: ConflictKind::IllegalName,
            detail: format!("“{path}” cannot exist on Windows; rename it to continue"),
        }),
    }
}
