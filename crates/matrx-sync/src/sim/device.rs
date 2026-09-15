//! One simulated device: a mock disk, a **real** journal, and an executor.
//!
//! The journal is the genuine [`Journal`] on a tempdir file, so a simulated crash is a real one:
//! the in-memory handle is dropped and the device comes back by reopening the same file. Anything
//! the device had not committed is gone, exactly as it would be.

use super::fs::MemFs;
use super::server::{node_from, MockServer, ServerError};
use crate::journal::{Journal, LocalConfirmation, NewOp, RemoteConfirmation};
use crate::knobs::Knobs;
use crate::model::{Direction, MappingRow, OpKind};
use crate::planner::{plan, Change, Plan, PlanOp, Side, SuspendReason};
use crate::Result;
use std::collections::{BTreeSet, VecDeque};
use std::path::PathBuf;

/// A comparable timestamp for a simulation tick.
///
/// The journal compares timestamps as strings — RFC3339 UTC's lexicographic order is its
/// chronological order — so the harness's stamps must be ordered too. `tick-9` sorts after
/// `tick-10`, which would have made the rolling window count the wrong rows; zero-padding fixes it.
fn stamp(tick: i64) -> String {
    format!("t{tick:012}")
}

/// What happened during one executed op — enough for a test to assert on, nothing more.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StepOutcome {
    /// The op completed.
    Done,
    /// The server refused on a precondition; the device will re-read the feed and re-plan.
    Raced,
    /// An injected transient failure. The op stays in the queue.
    Failed(&'static str),
    /// Nothing to do.
    Idle,
}

/// One simulated device.
pub struct Device {
    /// The device's name — also its `origin_device_id` and its conflict-copy name (D7).
    pub name: String,
    /// The mock disk.
    pub fs: MemFs,
    /// Where the journal file lives. Reopened on restart.
    pub journal_path: PathBuf,
    journal: Journal,
    /// The mapping this device syncs.
    pub mapping_id: String,
    /// Its direction.
    pub direction: Direction,
    /// Its resolved knobs.
    pub knobs: Knobs,
    /// The feed cursor, persisted in the journal's `mappings` row on every advance.
    pub cursor: i64,
    /// Whether the scanner hashes during the scan or leaves it for a `HashRequest` (I9).
    pub hash_on_scan: bool,
    /// The last suspension the planner asked for, if any.
    pub suspended: Option<SuspendReason>,
    /// The most recent tick this device acted on, so `current_plan` can measure the window.
    pub last_tick: i64,
    /// The plan currently being drained.
    ///
    /// A plan is enqueued **whole** and drained in order — which is precisely why SPEC-ENGINE §4
    /// gives the journal an `ops` queue instead of having the executor re-plan after every single
    /// operation. Re-planning per op livelocks: a conflict resolution is three ops (write the
    /// copy, upload it, take the winner), and re-planning after the first one re-derives the same
    /// first one forever, because nothing has changed yet. The harness found that; see TESTING.md.
    pending: VecDeque<PlanOp>,
    seq: i64,
}

impl Device {
    /// Bring a device up against `journal_path`, creating or reopening its journal.
    pub fn open(
        name: &str,
        journal_path: PathBuf,
        mapping_id: &str,
        direction: Direction,
        knobs: Knobs,
    ) -> Result<Self> {
        let journal = Journal::open(&journal_path)?;
        let existing = journal.mapping(mapping_id)?;
        let cursor = existing
            .as_ref()
            .and_then(|m| m.file_cursor.as_ref())
            .and_then(|c| c.parse().ok())
            .unwrap_or(0);
        if existing.is_none() {
            journal.put_mapping(&MappingRow {
                id: mapping_id.to_string(),
                organization_id: "org-1".to_string(),
                cloud_kind: "org_root".to_string(),
                cloud_folder_id: None,
                local_root: format!("/mock/{name}"),
                local_root_volume_id: format!("vol-{name}"),
                direction,
                desired_state: "active".to_string(),
                state: "idle".to_string(),
                state_detail: None,
                knobs: "{}".to_string(),
                file_cursor: Some("0".to_string()),
                folder_cursor: None,
                cloud_row_version: None,
                marker_uuid: format!("marker-{name}"),
                created_at: Some(stamp(0)),
                updated_at: Some(stamp(0)),
                last_sync_at: None,
                last_full_rescan_at: None,
            })?;
        }
        let seq = journal
            .connection()
            .query_row(
                "SELECT COALESCE(MAX(seq), 0) FROM ops WHERE mapping_id = ?1",
                rusqlite::params![mapping_id],
                |r| r.get::<_, i64>(0),
            )
            .unwrap_or(0);
        Ok(Device {
            name: name.to_string(),
            fs: MemFs::new(format!("vol-{name}")),
            journal_path,
            journal,
            mapping_id: mapping_id.to_string(),
            direction,
            knobs,
            cursor,
            hash_on_scan: true,
            suspended: None,
            last_tick: 0,
            pending: VecDeque::new(),
            seq,
        })
    }

    /// Crash and come back: drop the journal handle and reopen the same file.
    ///
    /// The mock disk survives (a crash does not wipe the user's files) and the journal comes back
    /// exactly as it was committed. Any op that was leased but not confirmed is still `leased`
    /// with its `expected_*` pre-image intact, so the replay is idempotent (I5).
    pub fn crash_and_restart(&mut self) -> Result<()> {
        let path = self.journal_path.clone();
        self.journal = Journal::open(&path)?;
        // A plan held only in memory does not survive a crash. It is re-derived from the journal,
        // which is the point of persisting the trees rather than the plan.
        self.pending.clear();
        self.cursor = self
            .journal
            .mapping(&self.mapping_id)?
            .and_then(|m| m.file_cursor)
            .and_then(|c| c.parse().ok())
            .unwrap_or(0);
        // A lease that did not survive the crash is released so the work can be retaken.
        self.journal.connection().execute(
            "UPDATE ops SET state='ready', lease_owner=NULL, lease_expires_at=NULL
             WHERE mapping_id = ?1 AND state = 'leased'",
            rusqlite::params![self.mapping_id],
        )?;
        Ok(())
    }

    /// Scan the disk into `tree_local`.
    pub fn scan(&mut self, now: i64) -> Result<()> {
        let seen = self.fs.scan(self.hash_on_scan, now);
        let known: Vec<String> = self
            .journal
            .local_tree(&self.mapping_id)?
            .paths()
            .cloned()
            .collect();
        for path in known {
            if !seen.contains(&path) {
                self.journal.delete_local(&self.mapping_id, &path)?;
            }
        }
        for (_, n) in seen.iter() {
            self.journal.put_local(&self.mapping_id, n)?;
        }
        Ok(())
    }

    /// Read the change feed forward and write `tree_remote`.
    pub fn poll_feed(&mut self, server: &MockServer, now: i64) -> Result<usize> {
        let (events, cursor) = server.feed_since(self.cursor, now);
        let count = events.len();
        for e in &events {
            self.journal
                .put_remote(&self.mapping_id, &node_from(&e.path, &e.file))?;
        }
        if cursor != self.cursor {
            self.cursor = cursor;
            self.journal.connection().execute(
                "UPDATE mappings SET file_cursor = ?2 WHERE id = ?1",
                rusqlite::params![self.mapping_id, cursor.to_string()],
            )?;
        }
        Ok(count)
    }

    /// Force a full rescan of the cloud — what the daemon does when a 412 tells it its view is
    /// stale, and what a `polling_fallback` mapping does on its timer.
    pub fn refresh_remote(&mut self, server: &MockServer) -> Result<()> {
        for (_, n) in server.full_tree().iter() {
            self.journal.put_remote(&self.mapping_id, n)?;
        }
        Ok(())
    }

    /// The start of the breaker's rolling window, as a comparable timestamp (H1).
    fn window_start(&self, now: i64) -> String {
        // One simulation tick models one second.
        let span = i64::from(self.knobs.mass_delete_window_hours) * 3_600;
        stamp(now.saturating_sub(span))
    }

    /// The current plan, from the journal's own three trees.
    pub fn current_plan(&self) -> Result<Plan> {
        self.current_plan_at(self.last_tick)
    }

    /// The current plan, with the rolling window measured as of `now`.
    pub fn current_plan_at(&self, now: i64) -> Result<Plan> {
        let ctx = crate::planner::PlanContext {
            device_name: self.name.clone(),
            today: "2026-09-13".to_string(),
            // H1: the breaker counts the window, not the plan. The daemon — not the pure
            // planner — reads the journal for it.
            recent_deletions: self
                .journal
                .deletions_since(&self.mapping_id, &self.window_start(now))?,
            open_conflicts: self
                .journal
                .open_conflicts(&self.mapping_id)?
                .into_iter()
                .map(|c| c.path_nfc)
                .collect::<BTreeSet<_>>(),
        };
        Ok(plan(
            &self.journal.local_tree(&self.mapping_id)?,
            &self.journal.remote_tree(&self.mapping_id)?,
            &self.journal.synced_tree(&self.mapping_id)?,
            self.direction,
            &self.knobs,
            &ctx,
        ))
    }

    /// Execute **one** operation of the current plan against the server and the disk.
    ///
    /// One at a time on purpose: the scheduler interleaves devices between calls, which is how
    /// requests get reordered and how two devices end up racing on one path.
    pub fn execute_one(
        &mut self,
        server: &mut MockServer,
        now: i64,
        inject: Option<&'static str>,
    ) -> Result<StepOutcome> {
        self.last_tick = now;
        if self.pending.is_empty() {
            let p = self.current_plan_at(now)?;
            if let Some(reason) = p.suspended {
                self.suspended = Some(reason);
                return Ok(StepOutcome::Idle);
            }
            self.suspended = None;
            self.pending.extend(p.ops);
        }
        let Some(op) = self.pending.pop_front() else {
            return Ok(StepOutcome::Idle);
        };
        if let Some(why) = inject {
            // A transient failure happens BEFORE anything is committed, which is the only place a
            // failure is harmless. The op goes back on the queue.
            self.pending.push_front(op);
            return Ok(StepOutcome::Failed(why));
        }
        let outcome = self.perform(op, server, now)?;
        if outcome == StepOutcome::Raced {
            // The world moved under us: throw the rest of the plan away and derive a new one from
            // the refreshed trees.
            self.pending.clear();
        }
        Ok(outcome)
    }

    fn next_seq(&mut self) -> i64 {
        self.seq += 1;
        self.seq
    }

    #[allow(clippy::too_many_arguments)]
    fn enqueue_and_lease(
        &mut self,
        kind: OpKind,
        path: &str,
        target: Option<&str>,
        expected_version: Option<i64>,
        expected_checksum: Option<String>,
        expected_local_hash: Option<String>,
        now: i64,
    ) -> Result<i64> {
        let seq = self.next_seq();
        let key = format!(
            "{}|{}|{}|{}|{}",
            self.mapping_id,
            kind.as_str(),
            path,
            expected_version.map(|v| v.to_string()).unwrap_or_default(),
            expected_local_hash.clone().unwrap_or_default()
        );
        let id = self.journal.enqueue_op(&NewOp {
            mapping_id: self.mapping_id.clone(),
            seq,
            kind,
            path_nfc: path.to_string(),
            target_path_nfc: target.map(str::to_string),
            remote_file_id: None,
            expected_checksum,
            expected_version,
            expected_local_hash,
            idempotency_key: key,
            created_at: stamp(now),
        })?;
        // The op may already be `done` from an earlier identical plan (I5) — leasing it again is
        // the replay path, and it simply finds nothing ready.
        self.journal.connection().execute(
            "UPDATE ops SET state='leased', lease_owner=?2, lease_expires_at=?3
             WHERE id = ?1 AND state IN ('ready','leased')",
            rusqlite::params![id, self.name, stamp(now + 900)],
        )?;
        Ok(id)
    }

    fn perform(
        &mut self,
        op: PlanOp,
        server: &mut MockServer,
        now: i64,
    ) -> Result<StepOutcome> {
        match op {
            PlanOp::HashRequest { path } => {
                let hash = self.fs.content(&path).map(str::to_string);
                if let Some(mut n) = self.journal.local_tree(&self.mapping_id)?.get(&path).cloned() {
                    n.content_hash = hash;
                    self.journal.put_local(&self.mapping_id, &n)?;
                }
                Ok(StepOutcome::Done)
            }
            PlanOp::MkdirLocal { path } => {
                self.fs.mkdir(&path, now);
                self.scan(now)?;
                let file = server.live(&path).cloned();
                if let Some(f) = file {
                    let id = self.enqueue_and_lease(
                        OpKind::MkdirLocal,
                        &path,
                        None,
                        Some(f.version),
                        None,
                        None,
                        now,
                    )?;
                    self.confirm(id, &path, true, None, &f.id, f.version, None, now)?;
                }
                Ok(StepOutcome::Done)
            }
            PlanOp::MkdirRemote { path } => {
                let f = server.mkdir(&path, &self.name, now);
                self.journal
                    .put_remote(&self.mapping_id, &node_from(&path, &f))?;
                let id = self.enqueue_and_lease(
                    OpKind::MkdirRemote,
                    &path,
                    None,
                    Some(f.version),
                    None,
                    None,
                    now,
                )?;
                self.confirm(id, &path, true, None, &f.id, f.version, None, now)?;
                Ok(StepOutcome::Done)
            }
            PlanOp::Upload {
                path,
                change,
                expected_version,
                expected_checksum,
                local_hash,
            } => {
                let kind = match change {
                    Change::Create => OpKind::UploadCreate,
                    Change::Update => OpKind::UploadUpdate,
                };
                let id = self.enqueue_and_lease(
                    kind,
                    &path,
                    None,
                    expected_version,
                    expected_checksum,
                    Some(local_hash.clone()),
                    now,
                )?;
                match server.put(&path, &local_hash, expected_version, &self.name, now) {
                    Ok(f) => {
                        self.journal
                            .put_remote(&self.mapping_id, &node_from(&path, &f))?;
                        self.confirm(
                            id,
                            &path,
                            false,
                            Some(local_hash),
                            &f.id,
                            f.version,
                            f.checksum.clone(),
                            now,
                        )?;
                        Ok(StepOutcome::Done)
                    }
                    Err(ServerError::PreconditionFailed { .. }) => {
                        self.journal.fail_op(
                            id,
                            "precondition_failed",
                            "another device wrote first",
                            Some(&stamp(now)),
                            &stamp(now),
                        )?;
                        self.refresh_remote(server)?;
                        Ok(StepOutcome::Raced)
                    }
                    Err(e) => {
                        self.journal.fail_op(
                            id,
                            "server_error",
                            &format!("{e:?}"),
                            Some(&stamp(now)),
                            &stamp(now),
                        )?;
                        Ok(StepOutcome::Failed("server error"))
                    }
                }
            }
            PlanOp::Download {
                path,
                change,
                remote_version,
                checksum,
                expected_local_hash,
                ..
            } => {
                let kind = match change {
                    Change::Create => OpKind::DownloadCreate,
                    Change::Update => OpKind::DownloadUpdate,
                };
                let id = self.enqueue_and_lease(
                    kind,
                    &path,
                    None,
                    remote_version,
                    Some(checksum.clone()),
                    expected_local_hash.clone(),
                    now,
                )?;
                // I3: a destructive local write happens only against a matching pre-image — and
                // for a CREATE the pre-image is "nothing is there". A plan derived from a stale
                // scan (the feed polled before the disk was walked) proposes Download{Create} for
                // a path the user already has a different file at; writing it would destroy that
                // file with no conflict copy and nothing on screen. The FS-C4 harness found
                // exactly that; see TESTING.md.
                let on_disk = self.fs.content(&path).map(str::to_string);
                if expected_local_hash.is_none()
                    && on_disk.is_some()
                    && on_disk.as_deref() != Some(checksum.as_str())
                {
                    self.journal.fail_op(
                        id,
                        "unexpected_local_file",
                        "a different file already exists at this path",
                        Some(&stamp(now)),
                        &stamp(now),
                    )?;
                    self.scan(now)?;
                    return Ok(StepOutcome::Raced);
                }
                if let Some(expected) = &expected_local_hash {
                    if on_disk.as_deref() != Some(expected.as_str()) {
                        self.journal.fail_op(
                            id,
                            "pre_image_mismatch",
                            "the file on disk changed under us",
                            Some(&stamp(now)),
                            &stamp(now),
                        )?;
                        self.scan(now)?;
                        return Ok(StepOutcome::Raced);
                    }
                }
                // I4: nothing partial ever appears at a user path — the mock writes atomically.
                self.fs.write(&path, &checksum, now);
                self.scan(now)?;
                let Some(f) = server.live(&path).cloned() else {
                    self.journal.fail_op(
                        id,
                        "gone",
                        "the cloud row disappeared mid-download",
                        Some(&stamp(now)),
                        &stamp(now),
                    )?;
                    return Ok(StepOutcome::Raced);
                };
                self.confirm(
                    id,
                    &path,
                    false,
                    Some(checksum),
                    &f.id,
                    f.version,
                    f.checksum.clone(),
                    now,
                )?;
                Ok(StepOutcome::Done)
            }
            PlanOp::DeleteLocalToTrash {
                path,
                to_trash,
                expected_local_hash,
            } => {
                let id = self.enqueue_and_lease(
                    OpKind::DeleteLocal,
                    &path,
                    None,
                    None,
                    None,
                    expected_local_hash.clone(),
                    now,
                )?;
                let on_disk = self.fs.content(&path).map(str::to_string);
                if let Some(expected) = &expected_local_hash {
                    if on_disk.as_deref() != Some(expected.as_str()) {
                        self.journal.fail_op(
                            id,
                            "pre_image_mismatch",
                            "the file changed before we deleted it",
                            Some(&stamp(now)),
                            &stamp(now),
                        )?;
                        self.scan(now)?;
                        return Ok(StepOutcome::Raced);
                    }
                }
                self.fs.remove(&path, to_trash);
                self.scan(now)?;
                self.journal
                    .confirm_delete_op(id, &self.name.clone(), true, true, &stamp(now))?;
                Ok(StepOutcome::Done)
            }
            PlanOp::DeleteRemoteTombstone {
                path,
                expected_version,
                ..
            } => {
                let id = self.enqueue_and_lease(
                    OpKind::DeleteRemote,
                    &path,
                    None,
                    expected_version,
                    None,
                    None,
                    now,
                )?;
                match server.delete(&path, expected_version, &self.name, now) {
                    Ok(f) => {
                        self.journal
                            .put_remote(&self.mapping_id, &node_from(&path, &f))?;
                        self.journal.confirm_delete_op(
                            id,
                            &self.name.clone(),
                            true,
                            true,
                            &stamp(now),
                        )?;
                        Ok(StepOutcome::Done)
                    }
                    Err(ServerError::NotFound) => {
                        self.journal.confirm_delete_op(
                            id,
                            &self.name.clone(),
                            true,
                            true,
                            &stamp(now),
                        )?;
                        Ok(StepOutcome::Done)
                    }
                    Err(_) => {
                        self.journal.fail_op(
                            id,
                            "precondition_failed",
                            "another device wrote first",
                            Some(&stamp(now)),
                            &stamp(now),
                        )?;
                        self.refresh_remote(server)?;
                        Ok(StepOutcome::Raced)
                    }
                }
            }
            PlanOp::Rename {
                side,
                from,
                to,
                expected_version,
                ..
            } => match side {
                Side::Remote => {
                    // G5: the op's OWN precondition, taken when the plan was made — never the
                    // server's current version, which would make the precondition a formality
                    // that always passes.
                    let version = expected_version;
                    let id = self.enqueue_and_lease(
                        OpKind::RenameRemote,
                        &from,
                        Some(&to),
                        version,
                        None,
                        None,
                        now,
                    )?;
                    match server.rename(&from, &to, version, &self.name, now) {
                        Ok(f) => {
                            self.refresh_remote(server)?;
                            let local_hash = self.fs.content(&to).map(str::to_string);
                            self.journal.confirm_delete_op(
                                id,
                                &self.name.clone(),
                                true,
                                true,
                                &stamp(now),
                            )?;
                            let id2 = self.enqueue_and_lease(
                                OpKind::MoveRemote,
                                &to,
                                None,
                                Some(f.version),
                                None,
                                local_hash.clone(),
                                now,
                            )?;
                            self.confirm(
                                id2,
                                &to,
                                false,
                                local_hash,
                                &f.id,
                                f.version,
                                f.checksum.clone(),
                                now,
                            )?;
                            Ok(StepOutcome::Done)
                        }
                        Err(_) => {
                            self.journal.fail_op(
                                id,
                                "precondition_failed",
                                "the rename raced",
                                Some(&stamp(now)),
                                &stamp(now),
                            )?;
                            self.refresh_remote(server)?;
                            Ok(StepOutcome::Raced)
                        }
                    }
                }
                Side::Local => {
                    self.fs.rename(&from, &to);
                    self.scan(now)?;
                    Ok(StepOutcome::Done)
                }
            },
            PlanOp::ConflictCopy {
                path,
                copy_path,
                kind,
                local_hash,
            } => {
                self.fs.copy(&path, &copy_path, now);
                self.scan(now)?;
                self.journal.put_conflict(&crate::model::ConflictRow {
                    id: format!("{}-{}-{}", self.name, path, now),
                    mapping_id: self.mapping_id.clone(),
                    path_nfc: path.clone(),
                    kind,
                    local_hash,
                    remote_file_id: server.live(&path).map(|f| f.id.clone()),
                    remote_checksum: server.live(&path).and_then(|f| f.checksum.clone()),
                    conflict_copy_path: Some(copy_path),
                    detected_at: stamp(now),
                    // D7 resolves this class on the spot: both copies reach the cloud, so the
                    // user is told, not asked.
                    resolved_at: Some(stamp(now)),
                    resolution: Some("both_kept".to_string()),
                })?;
                Ok(StepOutcome::Done)
            }
            PlanOp::FlagLocalEdit { path, local_hash } => {
                let local = self
                    .journal
                    .local_tree(&self.mapping_id)?
                    .get(&path)
                    .cloned();
                let remote = self
                    .journal
                    .remote_tree(&self.mapping_id)?
                    .get(&path)
                    .cloned();
                let (Some(local), Some(remote)) = (local, remote) else {
                    return Ok(StepOutcome::Idle);
                };
                let Some(remote_file_id) = remote.remote_file_id.clone() else {
                    return Ok(StepOutcome::Idle);
                };
                let checksum = remote.checksum.clone().or_else(|| {
                    self.journal
                        .synced_tree(&self.mapping_id)
                        .ok()
                        .and_then(|t| t.get(&path).and_then(|n| n.checksum.clone()))
                });
                if checksum.is_none() {
                    return Ok(StepOutcome::Idle);
                }
                // Amendment 1 gives this transition its own op kind, so it goes through the
                // queue like every other terminal op rather than being a bare journal write.
                let op_id = self.enqueue_and_lease(
                    OpKind::PreserveLocalEdit,
                    &path,
                    None,
                    remote.remote_version,
                    remote.checksum.clone(),
                    local.content_hash.clone(),
                    now,
                )?;
                let owner = self.name.clone();
                self.journal.preserve_local_edit(
                    op_id,
                    &owner,
                    &LocalConfirmation {
                        is_dir: false,
                        size: local.size,
                        mtime_ns: local.mtime_ns,
                        volume_id: local.volume_id.clone(),
                        file_id: local.file_id.clone(),
                        content_hash: local_hash.or(local.content_hash),
                        local_edit_flagged: true,
                    },
                    &RemoteConfirmation {
                        remote_file_id,
                        remote_version: remote.remote_version.unwrap_or(1),
                        checksum,
                    },
                    &stamp(now),
                )?;
                Ok(StepOutcome::Done)
            }
            PlanOp::RecordConflict { path, kind, detail } => {
                self.journal.put_conflict(&crate::model::ConflictRow {
                    id: format!("{}-{}-{}", self.name, path, kind.as_str()),
                    mapping_id: self.mapping_id.clone(),
                    path_nfc: path,
                    kind,
                    local_hash: None,
                    remote_file_id: None,
                    remote_checksum: None,
                    conflict_copy_path: None,
                    detected_at: stamp(now),
                    resolved_at: None,
                    resolution: Some(detail),
                })?;
                Ok(StepOutcome::Done)
            }
            PlanOp::RecordSynced { path } => {
                let local = self.journal.local_tree(&self.mapping_id)?.get(&path).cloned();
                let Some(f) = server.live(&path).cloned() else {
                    return Ok(StepOutcome::Idle);
                };
                let Some(local) = local else {
                    return Ok(StepOutcome::Idle);
                };
                // The two sides agreed when the plan was made. Between then and now another
                // device may have written, so the agreement is re-checked against the server as
                // it stands — a synced row is never assembled from two moments.
                if !local.is_dir && local.content_hash != f.checksum {
                    self.refresh_remote(server)?;
                    return Ok(StepOutcome::Raced);
                }
                let id = self.enqueue_and_lease(
                    if local.is_dir {
                        OpKind::MkdirRemote
                    } else {
                        OpKind::UploadUpdate
                    },
                    &path,
                    None,
                    Some(f.version),
                    f.checksum.clone(),
                    local.content_hash.clone(),
                    now,
                )?;
                self.confirm(
                    id,
                    &path,
                    local.is_dir,
                    local.content_hash.clone(),
                    &f.id,
                    f.version,
                    f.checksum.clone(),
                    now,
                )?;
                Ok(StepOutcome::Done)
            }
            PlanOp::ForgetSynced { path } => {
                let id = self.enqueue_and_lease(
                    OpKind::Unindex,
                    &path,
                    None,
                    None,
                    None,
                    None,
                    now,
                )?;
                self.journal
                    .confirm_delete_op(id, &self.name.clone(), true, true, &stamp(now))?;
                Ok(StepOutcome::Done)
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn confirm(
        &mut self,
        op_id: i64,
        path: &str,
        is_dir: bool,
        local_hash: Option<String>,
        remote_file_id: &str,
        remote_version: i64,
        checksum: Option<String>,
        now: i64,
    ) -> Result<()> {
        let local = self.journal.local_tree(&self.mapping_id)?.get(path).cloned();
        let owner = self.name.clone();
        self.journal.confirm_op(
            op_id,
            &owner,
            &LocalConfirmation {
                is_dir,
                size: local.as_ref().and_then(|n| n.size),
                mtime_ns: local.as_ref().and_then(|n| n.mtime_ns),
                volume_id: local.as_ref().and_then(|n| n.volume_id.clone()),
                file_id: local.as_ref().and_then(|n| n.file_id.clone()),
                content_hash: if is_dir { None } else { local_hash },
                local_edit_flagged: false,
            },
            &RemoteConfirmation {
                remote_file_id: remote_file_id.to_string(),
                remote_version,
                checksum: if is_dir { None } else { checksum },
            },
            &stamp(now),
        )
    }

    /// Read-only access to the journal, for assertions.
    pub fn journal(&self) -> &Journal {
        &self.journal
    }

    /// Resume a suspended mapping, as the user's one-click remedy does: the window that stopped
    /// the work is forgotten, so the same deletions are not refused again (H1).
    pub fn resume_after_suspension(&mut self) -> Result<()> {
        self.journal.clear_deletion_window(&self.mapping_id)?;
        self.suspended = None;
        self.pending.clear();
        Ok(())
    }
}
