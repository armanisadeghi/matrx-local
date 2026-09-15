//! The three-tree world and the executor model that applies a [`Plan`] to it.
//!
//! **This is a mock.** It models what a correct executor would do — not what the real one does.
//! Green here proves the *planner*, never the product (SCOPE §6).
//!
//! Content is modelled by content id (the SHA-256 hex a real file would hash to), never by bytes:
//! the invariants are all statements about content identity, and modelling bytes would only add
//! noise. Every write mirrors invariant I1 — a `tree_synced` row appears only where both sides
//! were confirmed by a completed op.

use crate::model::{ConflictKind, LocalNode, LocalTree, RemoteNode, RemoteTree, SyncedNode, SyncedTree};
use crate::planner::{Plan, PlanOp, Side, SuspendReason};
use std::collections::{BTreeMap, BTreeSet};

/// A conflict the model recorded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordedConflict {
    /// The disputed path.
    pub path: String,
    /// Which class.
    pub kind: ConflictKind,
    /// Where the losing copy went, when one was written.
    pub conflict_copy_path: Option<String>,
    /// Whether the plan resolved it on the spot (a conflict copy) or it awaits the user.
    pub resolved: bool,
}

/// One mapping's three trees plus the bookkeeping the model needs.
#[derive(Debug, Clone, Default)]
pub struct World {
    /// What is on disk.
    pub local: LocalTree,
    /// What the cloud holds.
    pub remote: RemoteTree,
    /// What both sides last confirmed.
    pub synced: SyncedTree,
    /// Conflicts recorded so far.
    pub conflicts: Vec<RecordedConflict>,
    /// Hashes the scanner has not computed yet, keyed by path (invariant I9).
    pub unhashed: BTreeMap<String, String>,
    /// Monotonic counter behind every generated identity — deterministic, never random.
    pub next_id: u64,
    /// Wall-clock stand-in, incremented per applied op. Deterministic.
    pub tick: u64,
}

impl World {
    /// An empty world.
    pub fn new() -> Self {
        World::default()
    }

    /// The paths whose conflict row is still open — what [`crate::PlanContext::open_conflicts`]
    /// is built from.
    pub fn open_conflicts(&self) -> BTreeSet<String> {
        self.conflicts
            .iter()
            .filter(|c| !c.resolved)
            .map(|c| c.path.clone())
            .collect()
    }

    fn fresh(&mut self, prefix: &str) -> String {
        self.next_id += 1;
        format!("{prefix}-{}", self.next_id)
    }

    fn stamp(&mut self) -> String {
        self.tick += 1;
        format!("2026-09-13T00:00:{:02}Z", self.tick % 60)
    }

    /// Apply every op of a plan, in order, as a correct executor would.
    ///
    /// A suspended plan applies nothing — that is the circuit breaker working.
    pub fn apply(&mut self, plan: &Plan) {
        if plan.suspended.is_some() {
            return;
        }
        for op in &plan.ops {
            self.apply_op(op);
        }
    }

    fn apply_op(&mut self, op: &PlanOp) {
        match op {
            PlanOp::HashRequest { path } => {
                if let Some(hash) = self.unhashed.remove(path) {
                    if let Some(n) = self.local.get_mut(path) {
                        n.content_hash = Some(hash);
                    }
                }
            }
            PlanOp::MkdirLocal { path } => {
                let volume = "vol-local".to_string();
                let file_id = self.fresh("inode");
                let at = self.stamp();
                self.local.insert(
                    path.clone(),
                    LocalNode {
                        path_nfc: path.clone(),
                        is_dir: true,
                        size: None,
                        mtime_ns: Some(self.tick as i64),
                        volume_id: Some(volume),
                        file_id: Some(file_id),
                        content_hash: None,
                        scanned_at: Some(at),
                    },
                );
                self.record_synced_from_trees(path);
            }
            PlanOp::MkdirRemote { path } => {
                let id = self.fresh("folder");
                let at = self.stamp();
                self.remote.insert(
                    path.clone(),
                    RemoteNode {
                        path_nfc: path.clone(),
                        is_dir: true,
                        size: None,
                        remote_file_id: Some(id.clone()),
                        remote_folder_id: Some(id),
                        remote_version: Some(1),
                        checksum: None,
                        client_modified_at: Some(at.clone()),
                        origin_device_id: Some("device-a".to_string()),
                        deleted_at: None,
                        seen_at: Some(at),
                    },
                );
                self.record_synced_from_trees(path);
            }
            PlanOp::Upload {
                path, local_hash, ..
            } => {
                let existing = self.remote.get(path).cloned();
                let id = match existing.as_ref().and_then(|n| n.remote_file_id.clone()) {
                    Some(id) => id,
                    None => self.fresh("file"),
                };
                let version = existing.as_ref().and_then(|n| n.remote_version).unwrap_or(0) + 1;
                let size = self.local.get(path).and_then(|n| n.size);
                let at = self.stamp();
                self.remote.insert(
                    path.clone(),
                    RemoteNode {
                        path_nfc: path.clone(),
                        is_dir: false,
                        size,
                        remote_file_id: Some(id),
                        remote_folder_id: None,
                        remote_version: Some(version),
                        checksum: Some(local_hash.clone()),
                        client_modified_at: Some(at.clone()),
                        origin_device_id: Some("device-a".to_string()),
                        deleted_at: None,
                        seen_at: Some(at),
                    },
                );
                self.record_synced_from_trees(path);
            }
            PlanOp::Download { path, checksum, .. } => {
                let identity = match self.local.get(path) {
                    Some(n) => (n.volume_id.clone(), n.file_id.clone()),
                    None => (Some("vol-local".to_string()), Some(self.fresh("inode"))),
                };
                let at = self.stamp();
                self.local.insert(
                    path.clone(),
                    LocalNode {
                        path_nfc: path.clone(),
                        is_dir: false,
                        size: self.remote.get(path).and_then(|n| n.size),
                        mtime_ns: Some(self.tick as i64),
                        volume_id: identity.0,
                        file_id: identity.1,
                        content_hash: Some(checksum.clone()),
                        scanned_at: Some(at),
                    },
                );
                self.unhashed.remove(path);
                self.record_synced_from_trees(path);
            }
            PlanOp::DeleteLocalToTrash { path, .. } => {
                self.local.remove(path);
                self.unhashed.remove(path);
                self.synced.remove(path);
            }
            PlanOp::DeleteRemoteTombstone { path, .. } => {
                if let Some(n) = self.remote.get_mut(path) {
                    let at = format!("2026-09-13T00:01:{:02}Z", self.tick % 60);
                    n.deleted_at = Some(at);
                    n.checksum = None;
                }
                self.synced.remove(path);
            }
            PlanOp::Rename {
                side,
                from,
                to,
                expected_version,
                expected_checksum,
            } => {
                // G5: the precondition is checked here too, so the model cannot apply a rename
                // over a source another device has moved on.
                let source_ok = self.remote.get(from).is_some_and(|n| {
                    n.is_live()
                        && n.remote_version == *expected_version
                        && n.checksum == *expected_checksum
                });
                if !source_ok {
                    return;
                }
                match side {
                    Side::Remote => {
                        if let Some(mut n) = self.remote.remove(from) {
                            n.path_nfc = to.clone();
                            n.remote_version = Some(n.remote_version.unwrap_or(0) + 1);
                            self.remote.insert(to.clone(), n);
                        }
                    }
                    Side::Local => {
                        if let Some(mut n) = self.local.remove(from) {
                            n.path_nfc = to.clone();
                            self.local.insert(to.clone(), n);
                        }
                    }
                }
                self.synced.remove(from);
                self.record_synced_from_trees(to);
            }
            PlanOp::ConflictCopy {
                path,
                copy_path,
                kind,
                local_hash,
            } => {
                if let Some(mut n) = self.local.get(path).cloned() {
                    n.path_nfc = copy_path.clone();
                    n.file_id = Some(self.fresh("inode"));
                    n.content_hash = local_hash.clone();
                    self.local.insert(copy_path.clone(), n);
                }
                self.conflicts.push(RecordedConflict {
                    path: path.clone(),
                    kind: *kind,
                    conflict_copy_path: Some(copy_path.clone()),
                    // The plan itself carries the upload of the copy and the download of the
                    // winner, so this class is resolved on the spot — D7's "both copies reach the
                    // cloud" is not a question put to the user.
                    resolved: true,
                });
            }
            PlanOp::FlagLocalEdit { path, local_hash } => {
                let checksum = self
                    .remote
                    .get(path)
                    .and_then(|n| n.checksum.clone())
                    .or_else(|| self.synced.get(path).and_then(|n| n.checksum.clone()));
                let remote_file_id = self
                    .remote
                    .get(path)
                    .and_then(|n| n.remote_file_id.clone())
                    .or_else(|| self.synced.get(path).map(|n| n.remote_file_id.clone()))
                    .unwrap_or_else(|| self.fresh("file"));
                let remote_version = self
                    .remote
                    .get(path)
                    .and_then(|n| n.remote_version)
                    .or_else(|| self.synced.get(path).map(|n| n.remote_version))
                    .unwrap_or(1);
                let at = self.stamp();
                let local = self.local.get(path).cloned();
                self.synced.insert(
                    path.clone(),
                    SyncedNode {
                        path_nfc: path.clone(),
                        is_dir: false,
                        size: local.as_ref().and_then(|n| n.size),
                        mtime_ns: local.as_ref().and_then(|n| n.mtime_ns),
                        volume_id: local.as_ref().and_then(|n| n.volume_id.clone()),
                        file_id: local.as_ref().and_then(|n| n.file_id.clone()),
                        content_hash: local_hash.clone(),
                        remote_file_id,
                        remote_version,
                        checksum,
                        local_edit_flagged: true,
                        synced_at: at,
                    },
                );
            }
            PlanOp::RecordConflict { path, kind, .. } => {
                if !self
                    .conflicts
                    .iter()
                    .any(|c| c.path == *path && c.kind == *kind && !c.resolved)
                {
                    self.conflicts.push(RecordedConflict {
                        path: path.clone(),
                        kind: *kind,
                        conflict_copy_path: None,
                        // Everything in this class needs a human: a name that cannot exist, a
                        // collision, a delete-versus-modify race. The planner proposes nothing
                        // further for the path until it is resolved.
                        resolved: false,
                    });
                }
            }
            PlanOp::RecordSynced { path } => self.record_synced_from_trees(path),
            PlanOp::ForgetSynced { path } => {
                self.synced.remove(path);
            }
        }
    }

    /// Write the synced row from the two sides as they now stand — the model's `confirm_op`.
    ///
    /// It refuses exactly what [`crate::journal::Journal::confirm_op`] refuses: a file row with no
    /// locally computed hash or no server checksum (I2).
    fn record_synced_from_trees(&mut self, path: &str) {
        let (Some(l), Some(r)) = (self.local.get(path).cloned(), self.remote.get(path).cloned())
        else {
            return;
        };
        if !r.is_live() || l.is_dir != r.is_dir {
            return;
        }
        if !l.is_dir && (l.content_hash.is_none() || r.checksum.is_none()) {
            return;
        }
        // G5, the second half: the model must never write a `tree_synced` row that
        // `Journal::confirm_op` would refuse. It manufactured exactly one — a rename's row with
        // `content_hash != checksum` and no flag — and the property harness then explored a state
        // the product cannot reach and called it converged. A real daemon would have got
        // `SyncedWriteRefused` and left the op stuck.
        if !l.is_dir && l.content_hash != r.checksum {
            return;
        }
        let Some(remote_file_id) = r.remote_file_id.clone() else {
            return;
        };
        let at = self.stamp();
        self.synced.insert(
            path.to_string(),
            SyncedNode {
                path_nfc: path.to_string(),
                is_dir: l.is_dir,
                size: l.size,
                mtime_ns: l.mtime_ns,
                volume_id: l.volume_id.clone(),
                file_id: l.file_id.clone(),
                content_hash: l.content_hash.clone(),
                remote_file_id,
                remote_version: r.remote_version.unwrap_or(1),
                checksum: r.checksum.clone(),
                local_edit_flagged: false,
                synced_at: at,
            },
        );
    }

    /// Every content id reachable from any of the three trees, plus conflict copies.
    pub fn reachable_content(&self) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        for (_, n) in self.local.iter() {
            if let Some(h) = &n.content_hash {
                out.insert(h.clone());
            }
        }
        for (p, h) in &self.unhashed {
            if self.local.contains(p) {
                out.insert(h.clone());
            }
        }
        for (_, n) in self.remote.iter() {
            if n.is_live() {
                if let Some(c) = &n.checksum {
                    out.insert(c.clone());
                }
            }
        }
        // `tree_synced` is deliberately NOT read here. It is bookkeeping of a past sync, not an
        // extant copy — the same rule `live_content` applies at t0, and SPEC-ENGINE §4.3 as
        // amended (2026-09-13, `6c226529`) states it outright: "tree_synced is bookkeeping of a
        // past sync, not an extant copy, and is never counted as a content holder". Counting it
        // here applied the rule to one side of the comparison and not the other, so content that
        // survived only as a journal row passed as preserved. Independent verification found it
        // (F4).
        //
        // A `local_edit_flagged` row's bytes are a surviving occurrence — and they are on disk,
        // in `tree_local`, which is already counted above.
        out
    }

    /// Every content id the *live* sides held at the start of a run — the set data preservation is
    /// measured against.
    pub fn live_content(&self) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        for (p, n) in self.local.iter() {
            if let Some(h) = n.content_hash.clone().or_else(|| self.unhashed.get(p).cloned()) {
                out.insert(h);
            }
        }
        for (_, n) in self.remote.iter() {
            if n.is_live() {
                if let Some(c) = &n.checksum {
                    out.insert(c.clone());
                }
            }
        }
        out
    }

    /// The suspension a plan asked for, if any — so a test can assert the breaker tripped.
    pub fn suspension(plan: &Plan) -> Option<&SuspendReason> {
        plan.suspended.as_ref()
    }
}
