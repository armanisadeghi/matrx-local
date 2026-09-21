//! The executor: a [`Plan`] applied to the world, op by op, under the journal's discipline.
//!
//! # What makes this safe rather than merely working
//!
//! * **Enqueue, then act, then confirm — in that order, always.** Every terminal op is written to
//!   the journal's `ops` queue with its idempotency key *before* anything happens in the world,
//!   leased, performed, and only then confirmed. A crash at any point leaves a row that says
//!   exactly how far it got.
//! * **The synced tree is written ONLY through [`Journal::confirm_op`],
//!   [`Journal::confirm_delete_op`] and [`Journal::preserve_local_edit`]** — the three doors
//!   `journal::confirm` guards with a private token. This module holds no fourth door and adds
//!   none; a write from here that bypassed them would not compile.
//! * **Resumability is re-planning, never replay.** Nothing persists a plan. A restart re-derives
//!   one from the journal's three trees, and an op that already completed is `done` under its
//!   idempotency key, so [`Journal::lease_op`] hands it back untouched and the executor skips it.
//!   That is how "crash between ops" costs nothing and duplicates nothing (I5).
//! * **Every failure is a named state with a remedy** ([`ExecError`]) — never a panic, never a
//!   silent skip. The three shapes are: re-plan (a 412 and its siblings), defer with backoff, or
//!   halt the mapping in an honest state the user can act on.
//! * **A 412 is never retried blind.** It discards the rest of the plan and asks for a new one.

use super::error::{ExecError, ExecResult};
use super::io::{
    DeleteRequest, LocalIo, LocalStat, ProgressSink, RemoteIo, RemoteObject, RenameRequest,
    UploadRequest,
};
use super::order;
use crate::custody::clock::{rfc3339, Clock};
use crate::journal::{Journal, LocalConfirmation, NewOp, RemoteConfirmation};
use crate::knobs::Knobs;
use crate::model::{ConflictKind, ConflictRow, Direction, LocalNode, OpKind, OpState, RemoteNode};
use crate::planner::{Change, Plan, PlanOp, Side};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

/// Everything about this mapping the executor cannot read from the world.
#[derive(Debug, Clone)]
pub struct ExecContext {
    /// The mapping being synced.
    pub mapping_id: String,
    /// Always explicit, never inherited (D12). Every cloud call carries it as
    /// `X-Organization-Id`.
    pub organization_id: String,
    /// The device name a conflict copy is named after (D7).
    pub device_name: String,
    /// `public.app_instances.id` (C13) — what the server stamps as `origin_device_id`.
    pub device_id: Option<String>,
    /// Who holds the op leases. One daemon, one owner.
    pub lease_owner: String,
    /// The mapping's direction (D6).
    pub direction: Direction,
    /// The resolved knobs.
    pub knobs: Knobs,
    /// `sync.lease_ttl_s`.
    pub lease_ttl_s: i64,
    /// `sync.locked_file_retry_base_s` — the first backoff step.
    pub retry_base_s: i64,
    /// `sync.locked_file_retry_max_s` — the backoff ceiling.
    pub retry_max_s: i64,
}

impl ExecContext {
    /// A context for `mapping_id` with the frozen G1 knob defaults.
    pub fn new(
        mapping_id: impl Into<String>,
        organization_id: impl Into<String>,
        device_name: impl Into<String>,
        direction: Direction,
    ) -> Self {
        let device_name = device_name.into();
        ExecContext {
            mapping_id: mapping_id.into(),
            organization_id: organization_id.into(),
            lease_owner: device_name.clone(),
            device_name,
            device_id: None,
            direction,
            knobs: Knobs::default(),
            lease_ttl_s: 900,
            retry_base_s: 5,
            retry_max_s: 900,
        }
    }
}

/// What one op did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OpOutcome {
    /// It happened and was confirmed.
    Applied,
    /// A previous run already completed it; nothing was done twice (I5).
    AlreadyDone,
    /// There was nothing to do — the reason is named, never shrugged at.
    NothingToDo(&'static str),
    /// The world moved. The rest of the plan is discarded and a new one derived.
    Raced {
        /// The stable token written to `ops.error_code`.
        code: &'static str,
        /// The sentence behind it.
        detail: String,
    },
    /// Back on the queue with a backoff.
    Deferred {
        /// The stable token.
        code: &'static str,
        /// The sentence.
        detail: String,
        /// When it may be tried again.
        retry_at: Option<String>,
    },
    /// The mapping stops until a human acts.
    Halted {
        /// The honest state the mapping takes.
        state: &'static str,
        /// The stable token.
        code: &'static str,
        /// The sentence.
        detail: String,
    },
}

/// Something the daemon turns into an SSE event (C9) or an `activity` row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecEvent {
    /// An `activity.appended` row.
    Activity {
        /// The stable activity code.
        code: &'static str,
        /// `info` | `warn` | `error`.
        level: &'static str,
        /// The mapping-relative path.
        path: String,
        /// The sentence a surface shows.
        message: String,
        /// Bytes moved, when this was a transfer.
        bytes: Option<i64>,
    },
    /// A `mapping.state_changed`.
    StateChanged {
        /// The honest state.
        state: &'static str,
        /// The sentence behind it.
        detail: Option<String>,
    },
    /// A `conflict.changed`.
    ConflictChanged {
        /// The disputed path.
        path: String,
        /// Which class.
        kind: ConflictKind,
    },
}

/// What a whole plan did.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ExecReport {
    /// Ops applied and confirmed.
    pub applied: usize,
    /// Ops a previous run had already completed.
    pub already_done: usize,
    /// Ops skipped because an earlier op on their ancestor failed.
    pub skipped: usize,
    /// Ops deferred with a backoff, as `(path, code)`.
    pub deferred: Vec<(String, &'static str)>,
    /// Whether the caller must re-plan before doing anything else.
    pub replan: bool,
    /// The honest state the mapping ends in.
    pub state: &'static str,
    /// The sentence behind it.
    pub state_detail: Option<String>,
    /// Everything the daemon should emit.
    pub events: Vec<ExecEvent>,
}

impl ExecReport {
    fn empty() -> Self {
        ExecReport {
            state: "idle",
            ..ExecReport::default()
        }
    }
}

/// The op-by-op executor.
///
/// It borrows everything it acts through, so one is built per pass and nothing it touches outlives
/// the pass.
pub struct Executor<'a> {
    journal: &'a mut Journal,
    local: &'a dyn LocalIo,
    remote: &'a dyn RemoteIo,
    clock: &'a dyn Clock,
    progress: &'a dyn ProgressSink,
    ctx: ExecContext,
    seq: i64,
}

/// The result of asking for a lease on an op.
enum Lease {
    /// Held, with the op's row id and the `created_at` the idempotency key is anchored to.
    Held { id: i64, created_at: String },
    /// A previous run already finished it.
    AlreadyDone,
}

impl<'a> Executor<'a> {
    /// Build an executor for one pass over one mapping.
    pub fn new(
        journal: &'a mut Journal,
        local: &'a dyn LocalIo,
        remote: &'a dyn RemoteIo,
        clock: &'a dyn Clock,
        progress: &'a dyn ProgressSink,
        ctx: ExecContext,
    ) -> ExecResult<Self> {
        let seq = journal.max_op_seq(&ctx.mapping_id)?;
        Ok(Executor {
            journal,
            local,
            remote,
            clock,
            progress,
            ctx,
            seq,
        })
    }

    fn now(&self) -> String {
        rfc3339(self.clock.now_wall())
    }

    fn lease_deadline(&self) -> String {
        rfc3339(self.clock.now_wall() + chrono::Duration::seconds(self.ctx.lease_ttl_s))
    }

    fn backoff(&self, attempts: i64) -> String {
        // Exponential from `sync.locked_file_retry_base_s`, capped at `_max_s`. Both are knobs;
        // neither is a constant in a decision (law 6).
        let step = self
            .ctx
            .retry_base_s
            .saturating_mul(1_i64 << attempts.clamp(0, 16).min(16))
            .min(self.ctx.retry_max_s.max(self.ctx.retry_base_s));
        rfc3339(self.clock.now_wall() + chrono::Duration::seconds(step))
    }

    /// Apply a whole plan, in dependency order.
    ///
    /// A suspended plan applies **nothing** — that is the circuit breaker working, and it is why
    /// the planner puts a suspension and ops in mutually exclusive positions.
    pub async fn execute(&mut self, plan: &Plan) -> ExecResult<ExecReport> {
        let mut report = ExecReport::empty();
        if let Some(reason) = &plan.suspended {
            let state = reason.honest_state();
            let detail = format!(
                "sync stopped before deleting anything: {:?}",
                match reason {
                    crate::planner::SuspendReason::MassDelete { planned_deletes, .. } =>
                        format!("{planned_deletes} deletions were about to be applied"),
                }
            );
            self.journal.set_mapping_state(
                &self.ctx.mapping_id,
                state,
                Some(&detail),
                &self.now(),
            )?;
            report.state = state;
            report.state_detail = Some(detail.clone());
            report.events.push(ExecEvent::StateChanged {
                state,
                detail: Some(detail),
            });
            return Ok(report);
        }
        if plan.ops.is_empty() {
            self.journal
                .set_mapping_state(&self.ctx.mapping_id, "idle", None, &self.now())?;
            return Ok(report);
        }

        self.journal
            .set_mapping_state(&self.ctx.mapping_id, "syncing", None, &self.now())?;
        report.state = "syncing";

        let ordered = order::order(&plan.ops);
        // Paths whose descendants must be skipped because the act they depend on failed.
        let mut blocked: BTreeSet<String> = BTreeSet::new();

        for op in ordered {
            let path = op.path().to_string();
            if blocked.iter().any(|a| order::is_under(&path, a)) {
                report.skipped += 1;
                continue;
            }
            let outcome = match self.direction_permits(&op) {
                Ok(()) => self.execute_op(op).await,
                Err(e) => Err(e),
            };
            let outcome = match outcome {
                Ok(o) => o,
                Err(e) => self.record_failure(&path, &e)?,
            };
            match outcome {
                OpOutcome::Applied => {
                    report.applied += 1;
                }
                OpOutcome::AlreadyDone => report.already_done += 1,
                OpOutcome::NothingToDo(_) => report.skipped += 1,
                OpOutcome::Raced { code, detail } => {
                    report.replan = true;
                    report.events.push(ExecEvent::Activity {
                        code: "replan",
                        level: "info",
                        path: path.clone(),
                        message: detail,
                        bytes: None,
                    });
                    report.deferred.push((path, code));
                    // The rest of the plan was derived from a world that no longer exists.
                    break;
                }
                OpOutcome::Deferred { code, detail, .. } => {
                    report.deferred.push((path.clone(), code));
                    report.events.push(ExecEvent::Activity {
                        code: "op_deferred",
                        level: "warn",
                        path: path.clone(),
                        message: detail,
                        bytes: None,
                    });
                    blocked.insert(path);
                }
                OpOutcome::Halted {
                    state,
                    code,
                    detail,
                } => {
                    self.journal.set_mapping_state(
                        &self.ctx.mapping_id,
                        state,
                        Some(&detail),
                        &self.now(),
                    )?;
                    report.state = state;
                    report.state_detail = Some(detail.clone());
                    report.deferred.push((path.clone(), code));
                    report.events.push(ExecEvent::StateChanged {
                        state,
                        detail: Some(detail.clone()),
                    });
                    report.events.push(ExecEvent::Activity {
                        code: "sync_halted",
                        level: "error",
                        path,
                        message: detail,
                        bytes: None,
                    });
                    return Ok(report);
                }
            }
        }

        if report.state == "syncing" {
            let open = self.journal.open_conflicts(&self.ctx.mapping_id)?;
            let state = if !open.is_empty() {
                "needs_conflict_resolution"
            } else if report.replan || !report.deferred.is_empty() {
                "syncing"
            } else {
                "idle"
            };
            self.journal
                .set_mapping_state(&self.ctx.mapping_id, state, None, &self.now())?;
            report.state = state;
            report.events.push(ExecEvent::StateChanged {
                state,
                detail: None,
            });
        }
        Ok(report)
    }

    /// D6, enforced a second time at the point of action.
    ///
    /// The planner already honours the direction; this is the guard that turns a planner defect
    /// into a loud refusal instead of a `download_only` mapping quietly uploading.
    fn direction_permits(&self, op: &PlanOp) -> ExecResult<()> {
        let refuse = |op_name: &'static str| {
            Err(ExecError::DirectionRefused {
                direction: self.ctx.direction.as_str(),
                op: op_name,
                path: op.path().to_string(),
            })
        };
        match (self.ctx.direction, op) {
            (Direction::DownloadOnly, PlanOp::Upload { .. }) => refuse("upload"),
            (Direction::DownloadOnly, PlanOp::MkdirRemote { .. }) => refuse("mkdir_remote"),
            (Direction::DownloadOnly, PlanOp::DeleteRemoteTombstone { .. }) => {
                refuse("delete_remote")
            }
            (
                Direction::DownloadOnly,
                PlanOp::Rename {
                    side: Side::Remote, ..
                },
            ) => refuse("rename_remote"),
            (Direction::UploadOnly, PlanOp::Download { .. }) => refuse("download"),
            (Direction::UploadOnly, PlanOp::MkdirLocal { .. }) => refuse("mkdir_local"),
            (Direction::UploadOnly, PlanOp::DeleteLocalToTrash { .. }) => refuse("delete_local"),
            (
                Direction::UploadOnly,
                PlanOp::Rename {
                    side: Side::Local, ..
                },
            ) => refuse("rename_local"),
            _ => Ok(()),
        }
    }

    fn record_failure(&mut self, path: &str, e: &ExecError) -> ExecResult<OpOutcome> {
        let now = self.now();
        if let Some(state) = e.honest_state() {
            if e.suspends() {
                return Ok(OpOutcome::Halted {
                    state,
                    code: e.code(),
                    detail: e.to_string(),
                });
            }
        }
        if e.is_race() {
            return Ok(OpOutcome::Raced {
                code: e.code(),
                detail: e.to_string(),
            });
        }
        let retry_at = if e.retryable() { Some(self.backoff(0)) } else { None };
        let _ = (path, &now);
        Ok(OpOutcome::Deferred {
            code: e.code(),
            detail: e.to_string(),
            retry_at,
        })
    }

    // ------------------------------------------------------------------ the queue

    fn next_seq(&mut self) -> i64 {
        self.seq += 1;
        self.seq
    }

    /// `sha256(mapping_id, kind, path_nfc, expected_version|expected_local_hash)` — invariant I5.
    fn op_key(
        &self,
        kind: OpKind,
        path: &str,
        expected_version: Option<i64>,
        expected_local_hash: Option<&str>,
    ) -> String {
        let mut h = Sha256::new();
        h.update(self.ctx.mapping_id.as_bytes());
        h.update([0]);
        h.update(kind.as_str().as_bytes());
        h.update([0]);
        h.update(path.as_bytes());
        h.update([0]);
        h.update(
            expected_version
                .map(|v| v.to_string())
                .unwrap_or_default()
                .as_bytes(),
        );
        h.update([0]);
        h.update(expected_local_hash.unwrap_or_default().as_bytes());
        format!("{:x}", h.finalize())
    }

    /// The key the SERVER dedupes on: `sha256(mapping_id || file_path || content_sha256 ||
    /// attempt_epoch)` (SPEC-SERVER §3.3 item 4).
    ///
    /// `attempt_epoch` is the op row's own `created_at`, which is **persisted** — that is what
    /// makes a `kill -9` mid-upload resume under the same key instead of creating a second file.
    fn request_key(&self, path: &str, content_hash: &str, attempt_epoch: &str) -> String {
        let mut h = Sha256::new();
        h.update(self.ctx.mapping_id.as_bytes());
        h.update([0]);
        h.update(path.as_bytes());
        h.update([0]);
        h.update(content_hash.as_bytes());
        h.update([0]);
        h.update(attempt_epoch.as_bytes());
        format!("{:x}", h.finalize())
    }

    #[allow(clippy::too_many_arguments)]
    fn enqueue_and_lease(
        &mut self,
        kind: OpKind,
        path: &str,
        target: Option<&str>,
        remote_file_id: Option<&str>,
        expected_version: Option<i64>,
        expected_checksum: Option<String>,
        expected_local_hash: Option<String>,
    ) -> ExecResult<Lease> {
        let now = self.now();
        let key = self.op_key(kind, path, expected_version, expected_local_hash.as_deref());
        let seq = self.next_seq();
        let id = self.journal.enqueue_op(&NewOp {
            mapping_id: self.ctx.mapping_id.clone(),
            seq,
            kind,
            path_nfc: path.to_string(),
            target_path_nfc: target.map(str::to_string),
            remote_file_id: remote_file_id.map(str::to_string),
            expected_checksum,
            expected_version,
            expected_local_hash,
            idempotency_key: key,
            created_at: now.clone(),
        })?;
        let deadline = self.lease_deadline();
        let row = self.journal.lease_op(id, &self.ctx.lease_owner, &now, &deadline)?;
        let Some(row) = row else {
            return Err(ExecError::Refused(format!("op {id} vanished after enqueue")));
        };
        if row.state == OpState::Done {
            return Ok(Lease::AlreadyDone);
        }
        if row.state != OpState::Leased || row.lease_owner.as_deref() != Some(&self.ctx.lease_owner)
        {
            return Err(ExecError::Refused(format!(
                "op {id} is '{}' held by {:?}; this daemon could not take the lease",
                row.state.as_str(),
                row.lease_owner
            )));
        }
        Ok(Lease::Held {
            id,
            created_at: row.created_at.unwrap_or(now),
        })
    }

    fn fail(&mut self, id: i64, e: &ExecError, attempts: i64) -> ExecResult<()> {
        let now = self.now();
        let retry_at = if e.retryable() || e.is_race() {
            Some(self.backoff(attempts))
        } else {
            None
        };
        self.journal.fail_op(
            id,
            e.code(),
            &e.to_string(),
            retry_at.as_deref(),
            &now,
        )?;
        Ok(())
    }

    // ------------------------------------------------------------------ one op

    async fn execute_op(&mut self, op: PlanOp) -> ExecResult<OpOutcome> {
        match op {
            PlanOp::HashRequest { path } => self.do_hash_request(&path).await,
            PlanOp::MkdirLocal { path } => self.do_mkdir_local(&path).await,
            PlanOp::MkdirRemote { path } => self.do_mkdir_remote(&path).await,
            PlanOp::Upload {
                path,
                change,
                expected_version,
                expected_checksum,
                local_hash,
            } => {
                self.do_upload(&path, change, expected_version, expected_checksum, &local_hash)
                    .await
            }
            PlanOp::Download {
                path,
                change,
                remote_file_id,
                remote_version,
                checksum,
                expected_local_hash,
            } => {
                self.do_download(
                    &path,
                    change,
                    remote_file_id,
                    remote_version,
                    &checksum,
                    expected_local_hash,
                )
                .await
            }
            PlanOp::DeleteLocalToTrash {
                path,
                to_trash,
                expected_local_hash,
            } => {
                self.do_delete_local(&path, to_trash, expected_local_hash)
                    .await
            }
            PlanOp::DeleteRemoteTombstone {
                path,
                remote_file_id,
                expected_version,
            } => {
                self.do_delete_remote(&path, remote_file_id, expected_version)
                    .await
            }
            PlanOp::Rename {
                side,
                from,
                to,
                expected_version,
                expected_checksum,
            } => {
                self.do_rename(side, &from, &to, expected_version, expected_checksum)
                    .await
            }
            PlanOp::ConflictCopy {
                path,
                copy_path,
                kind,
                local_hash,
            } => {
                self.do_conflict_copy(&path, &copy_path, kind, local_hash)
                    .await
            }
            PlanOp::FlagLocalEdit { path, local_hash } => {
                self.do_flag_local_edit(&path, local_hash).await
            }
            PlanOp::RecordConflict { path, kind, detail } => {
                self.do_record_conflict(&path, kind, &detail)
            }
            PlanOp::RecordSynced { path } => self.do_record_synced(&path).await,
            PlanOp::ForgetSynced { path } => self.do_forget_synced(&path),
        }
    }

    async fn do_hash_request(&mut self, path: &str) -> ExecResult<OpOutcome> {
        // A hash request is not a terminal op: it changes nothing in the world and writes only
        // `tree_local`, which is the scanner's own table. It therefore takes no queue row.
        let Some(stat) = self.local.stat(path, true).await? else {
            self.journal.delete_local(&self.ctx.mapping_id, path)?;
            return Ok(OpOutcome::NothingToDo("the file is gone"));
        };
        let node = self.local_node(path, &stat);
        self.journal.put_local(&self.ctx.mapping_id, &node)?;
        Ok(OpOutcome::Applied)
    }

    fn local_node(&self, path: &str, stat: &LocalStat) -> LocalNode {
        LocalNode {
            path_nfc: path.to_string(),
            is_dir: stat.is_dir,
            size: stat.size,
            mtime_ns: stat.mtime_ns,
            volume_id: stat.volume_id.clone(),
            file_id: stat.file_id.clone(),
            content_hash: stat.content_hash.clone(),
            scanned_at: Some(self.now()),
        }
    }

    fn remote_node(&self, path: &str, obj: &RemoteObject) -> RemoteNode {
        RemoteNode {
            path_nfc: path.to_string(),
            is_dir: obj.is_dir,
            size: obj.size,
            remote_file_id: Some(obj.file_id.clone()),
            remote_folder_id: obj.folder_id.clone(),
            remote_version: Some(obj.version),
            checksum: obj.checksum.clone(),
            client_modified_at: None,
            origin_device_id: self.ctx.device_id.clone(),
            deleted_at: None,
            seen_at: Some(self.now()),
        }
    }

    async fn do_mkdir_local(&mut self, path: &str) -> ExecResult<OpOutcome> {
        let stat = self.local.mkdir(path).await?;
        let node = self.local_node(path, &stat);
        self.journal.put_local(&self.ctx.mapping_id, &node)?;
        // A directory's synced row needs the cloud folder's identity. When the feed has not shown
        // it yet there is nothing to confirm against, and the next plan emits `RecordSynced`.
        let remote = self
            .journal
            .remote_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let Some(remote) = remote.filter(RemoteNode::is_live) else {
            return Ok(OpOutcome::Applied);
        };
        let Some(remote_file_id) = remote.remote_file_id.clone() else {
            return Ok(OpOutcome::Applied);
        };
        let lease = self.enqueue_and_lease(
            OpKind::MkdirLocal,
            path,
            None,
            Some(&remote_file_id),
            remote.remote_version,
            None,
            None,
        )?;
        let Lease::Held { id, .. } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        self.confirm(
            id,
            path,
            true,
            None,
            &remote_file_id,
            remote.remote_version.unwrap_or(1),
            None,
        )?;
        Ok(OpOutcome::Applied)
    }

    async fn do_mkdir_remote(&mut self, path: &str) -> ExecResult<OpOutcome> {
        let obj = match self.remote.mkdir(path).await {
            Ok(o) => o,
            Err(e) => return Err(e),
        };
        let node = self.remote_node(path, &obj);
        self.journal.put_remote(&self.ctx.mapping_id, &node)?;
        let lease = self.enqueue_and_lease(
            OpKind::MkdirRemote,
            path,
            None,
            Some(&obj.file_id),
            Some(obj.version),
            None,
            None,
        )?;
        let Lease::Held { id, .. } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        // The local side of a remote mkdir is the directory that is already there.
        let local = self.local.stat(path, false).await?;
        if local.is_none() {
            // Nothing to confirm against yet; the synced row waits for the local half.
            self.journal.fail_op(
                id,
                "awaiting_local",
                "the cloud folder exists; the local directory has not been seen yet",
                Some(&self.backoff(0)),
                &self.now(),
            )?;
            return Ok(OpOutcome::NothingToDo("the local directory is not there yet"));
        }
        self.confirm(id, path, true, None, &obj.file_id, obj.version, None)?;
        Ok(OpOutcome::Applied)
    }

    async fn do_upload(
        &mut self,
        path: &str,
        change: Change,
        expected_version: Option<i64>,
        expected_checksum: Option<String>,
        local_hash: &str,
    ) -> ExecResult<OpOutcome> {
        let kind = match change {
            Change::Create => OpKind::UploadCreate,
            Change::Update => OpKind::UploadUpdate,
        };
        let remote_file_id = self
            .journal
            .remote_tree(&self.ctx.mapping_id)?
            .get(path)
            .and_then(|n| n.remote_file_id.clone());
        let lease = self.enqueue_and_lease(
            kind,
            path,
            None,
            remote_file_id.as_deref(),
            expected_version,
            expected_checksum.clone(),
            Some(local_hash.to_string()),
        )?;
        let Lease::Held { id, created_at } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        let attempts = self.journal.op(id)?.map(|r| r.attempts).unwrap_or(1);

        // I3: the bytes being sent are re-read from disk and re-hashed. The plan's `local_hash`
        // came from the scan, and the file may have been written again since.
        let stat = match self.local.stat(path, true).await {
            Ok(Some(s)) => s,
            Ok(None) => {
                let e = ExecError::Vanished {
                    path: path.to_string(),
                };
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
            Err(e) => {
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
        };
        if stat.content_hash.as_deref() != Some(local_hash) {
            let e = ExecError::PreImageMismatch {
                path: path.to_string(),
                expected: local_hash.to_string(),
                found: stat.content_hash.clone(),
            };
            // The disk is now known to hold something else; record it so the next plan sees it.
            let node = self.local_node(path, &stat);
            self.journal.put_local(&self.ctx.mapping_id, &node)?;
            self.fail(id, &e, attempts)?;
            return Err(e);
        }

        let source = match self.local.source_of(path).await {
            Ok(s) => s,
            Err(e) => {
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
        };
        let request = UploadRequest {
            path_nfc: path.to_string(),
            source,
            content_hash: local_hash.to_string(),
            size: stat.size.unwrap_or(0),
            // S8: the daemon ALWAYS sends a precondition. "absent" is the honest spelling of
            // "I believe no alive row exists at this path".
            expected_checksum: expected_checksum.unwrap_or_else(|| "absent".to_string()),
            remote_file_id,
            idempotency_key: self.request_key(path, local_hash, &created_at),
            client_modified_at: stat.mtime_ns.map(rfc3339_from_ns),
            resume_offset: self.journal.op(id)?.and_then(|r| r.transfer_offset),
            resume_url: self.journal.op(id)?.and_then(|r| r.transfer_upload_url),
        };
        let obj = match self.remote.upload(&request, self.progress).await {
            Ok(o) => o,
            Err(e) => {
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
        };
        let node = self.remote_node(path, &obj);
        self.journal.put_remote(&self.ctx.mapping_id, &node)?;
        self.confirm(
            id,
            path,
            false,
            Some(local_hash.to_string()),
            &obj.file_id,
            obj.version,
            obj.checksum.clone(),
        )?;
        Ok(OpOutcome::Applied)
    }

    async fn do_download(
        &mut self,
        path: &str,
        change: Change,
        remote_file_id: Option<String>,
        remote_version: Option<i64>,
        checksum: &str,
        expected_local_hash: Option<String>,
    ) -> ExecResult<OpOutcome> {
        let kind = match change {
            Change::Create => OpKind::DownloadCreate,
            Change::Update => OpKind::DownloadUpdate,
        };
        let remote_row = self
            .journal
            .remote_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let remote_file_id = remote_file_id
            .or_else(|| remote_row.as_ref().and_then(|n| n.remote_file_id.clone()));
        let Some(remote_file_id) = remote_file_id else {
            return Err(ExecError::RemoteGone {
                path: path.to_string(),
            });
        };
        let lease = self.enqueue_and_lease(
            kind,
            path,
            None,
            Some(&remote_file_id),
            remote_version,
            Some(checksum.to_string()),
            expected_local_hash.clone(),
        )?;
        let Lease::Held { id, .. } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        let attempts = self.journal.op(id)?.map(|r| r.attempts).unwrap_or(1);

        // I3, both halves. For an UPDATE the pre-image is the hash the plan was made against; for
        // a CREATE the pre-image is "nothing is there". A plan derived from a stale scan proposes
        // a create at a path the user already put a different file at, and writing it would
        // destroy that file with no conflict copy and nothing on screen.
        let on_disk = match self.local.stat(path, true).await {
            Ok(s) => s,
            Err(e) => {
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
        };
        let found = on_disk.as_ref().and_then(|s| s.content_hash.clone());
        let mismatch = match (&expected_local_hash, &on_disk) {
            (Some(expected), _) if found.as_deref() != Some(expected.as_str()) => {
                Some(ExecError::PreImageMismatch {
                    path: path.to_string(),
                    expected: expected.clone(),
                    found: found.clone(),
                })
            }
            (None, Some(_)) if found.as_deref() != Some(checksum) => {
                Some(ExecError::UnexpectedLocalFile {
                    path: path.to_string(),
                })
            }
            _ => None,
        };
        if let Some(e) = mismatch {
            if let Some(stat) = &on_disk {
                let node = self.local_node(path, stat);
                self.journal.put_local(&self.ctx.mapping_id, &node)?;
            }
            self.fail(id, &e, attempts)?;
            return Err(e);
        }

        // I4: the bytes land in `.matrx-sync/tmp` and reach their real name only after they hash
        // to what the plan said they would.
        let staged = match self.local.stage(path).await {
            Ok(s) => s,
            Err(e) => {
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
        };
        let temp = staged.temp_path.clone();
        if let Err(e) = self
            .remote
            .download(&remote_file_id, path, &temp, self.progress)
            .await
        {
            self.local.discard_staged(staged).await;
            self.fail(id, &e, attempts)?;
            return Err(e);
        }
        let stat = match self.local.commit_staged(staged, checksum, None).await {
            Ok(s) => s,
            Err(e) => {
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
        };
        let node = self.local_node(path, &stat);
        self.journal.put_local(&self.ctx.mapping_id, &node)?;

        // I1: the remote half of the confirmation is what the cloud actually holds, as the feed
        // last reported it. If the feed has moved past the version we fetched, the two halves come
        // from different moments and the row would be a lie.
        let Some(remote_row) = remote_row.filter(RemoteNode::is_live) else {
            let e = ExecError::RemoteGone {
                path: path.to_string(),
            };
            self.fail(id, &e, attempts)?;
            return Err(e);
        };
        if remote_row.checksum.as_deref() != Some(checksum) {
            let e = ExecError::PreconditionFailed(Box::new(super::error::Precondition {
                file_id: Some(remote_file_id.clone()),
                current_checksum: remote_row.checksum.clone(),
                current_version: remote_row.remote_version,
                expected_checksum: Some(checksum.to_string()),
                ..Default::default()
            }));
            self.fail(id, &e, attempts)?;
            return Err(e);
        }
        self.confirm(
            id,
            path,
            false,
            Some(checksum.to_string()),
            &remote_file_id,
            remote_row.remote_version.unwrap_or(1),
            remote_row.checksum.clone(),
        )?;
        Ok(OpOutcome::Applied)
    }

    async fn do_delete_local(
        &mut self,
        path: &str,
        to_trash: bool,
        expected_local_hash: Option<String>,
    ) -> ExecResult<OpOutcome> {
        let lease = self.enqueue_and_lease(
            OpKind::DeleteLocal,
            path,
            None,
            None,
            None,
            None,
            expected_local_hash.clone(),
        )?;
        let Lease::Held { id, .. } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        let attempts = self.journal.op(id)?.map(|r| r.attempts).unwrap_or(1);
        let on_disk = self.local.stat(path, expected_local_hash.is_some()).await?;
        if let Some(expected) = &expected_local_hash {
            let found = on_disk.as_ref().and_then(|s| s.content_hash.clone());
            if found.as_deref() != Some(expected.as_str()) {
                let e = ExecError::PreImageMismatch {
                    path: path.to_string(),
                    expected: expected.clone(),
                    found,
                };
                if let Some(stat) = &on_disk {
                    let node = self.local_node(path, stat);
                    self.journal.put_local(&self.ctx.mapping_id, &node)?;
                }
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
        }
        // The cloud copy must already be gone, or this is not a propagated delete — it is us
        // about to destroy a file the cloud still holds live. That is a re-plan, not a delete.
        let remote_live = self
            .journal
            .remote_tree(&self.ctx.mapping_id)?
            .get(path)
            .is_some_and(RemoteNode::is_live);
        if remote_live {
            let e = ExecError::PreImageMismatch {
                path: path.to_string(),
                expected: "the cloud copy to be gone".to_string(),
                found: Some("the cloud still holds it".to_string()),
            };
            self.fail(id, &e, attempts)?;
            return Err(e);
        }
        if on_disk.is_some() {
            if let Err(e) = self.local.remove(path, to_trash).await {
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
        }
        self.journal.delete_local(&self.ctx.mapping_id, path)?;
        let now = self.now();
        let owner = self.ctx.lease_owner.clone();
        self.journal
            .confirm_delete_op(id, &owner, true, true, &now)?;
        Ok(OpOutcome::Applied)
    }

    async fn do_delete_remote(
        &mut self,
        path: &str,
        remote_file_id: Option<String>,
        expected_version: Option<i64>,
    ) -> ExecResult<OpOutcome> {
        let remote_row = self
            .journal
            .remote_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let remote_file_id = remote_file_id
            .or_else(|| remote_row.as_ref().and_then(|n| n.remote_file_id.clone()));
        let lease = self.enqueue_and_lease(
            OpKind::DeleteRemote,
            path,
            None,
            remote_file_id.as_deref(),
            expected_version,
            None,
            None,
        )?;
        let Lease::Held { id, created_at } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        let attempts = self.journal.op(id)?.map(|r| r.attempts).unwrap_or(1);
        // Already gone from the cloud is success, not an error: D23's tombstone is a row, and a
        // row that is not there was never ours to remove.
        if let Some(remote_file_id) = remote_file_id {
            let request = DeleteRequest {
                remote_file_id: remote_file_id.clone(),
                path_nfc: path.to_string(),
                expected_version,
                idempotency_key: self.request_key(path, "tombstone", &created_at),
            };
            match self.remote.tombstone(&request).await {
                Ok(()) | Err(ExecError::RemoteGone { .. }) => {}
                Err(e) => {
                    self.fail(id, &e, attempts)?;
                    return Err(e);
                }
            }
            if let Some(mut row) = remote_row {
                row.deleted_at = Some(self.now());
                row.checksum = None;
                self.journal.put_remote(&self.ctx.mapping_id, &row)?;
            }
        }
        let local_absent = self.local.stat(path, false).await?.is_none();
        if !local_absent {
            let e = ExecError::PreImageMismatch {
                path: path.to_string(),
                expected: "the local copy to be gone".to_string(),
                found: Some("it is still on disk".to_string()),
            };
            self.fail(id, &e, attempts)?;
            return Err(e);
        }
        let now = self.now();
        let owner = self.ctx.lease_owner.clone();
        self.journal
            .confirm_delete_op(id, &owner, true, true, &now)?;
        Ok(OpOutcome::Applied)
    }

    async fn do_rename(
        &mut self,
        side: Side,
        from: &str,
        to: &str,
        expected_version: Option<i64>,
        expected_checksum: Option<String>,
    ) -> ExecResult<OpOutcome> {
        match side {
            Side::Local => {
                let lease = self.enqueue_and_lease(
                    OpKind::RenameLocal,
                    from,
                    Some(to),
                    None,
                    expected_version,
                    expected_checksum,
                    None,
                )?;
                let Lease::Held { id, .. } = lease else {
                    return Ok(OpOutcome::AlreadyDone);
                };
                let attempts = self.journal.op(id)?.map(|r| r.attempts).unwrap_or(1);
                let stat = match self.local.rename(from, to).await {
                    Ok(s) => s,
                    Err(e) => {
                        self.fail(id, &e, attempts)?;
                        return Err(e);
                    }
                };
                self.journal.delete_local(&self.ctx.mapping_id, from)?;
                let node = self.local_node(to, &stat);
                self.journal.put_local(&self.ctx.mapping_id, &node)?;
                // The synced row at the OLD path is retired here; the row at the new path is
                // written by the `RecordSynced` the next plan emits, once both sides agree there.
                let now = self.now();
                let owner = self.ctx.lease_owner.clone();
                self.journal
                    .confirm_delete_op(id, &owner, true, true, &now)?;
                Ok(OpOutcome::Applied)
            }
            Side::Remote => {
                let remote_row = self
                    .journal
                    .remote_tree(&self.ctx.mapping_id)?
                    .get(from)
                    .cloned();
                let Some(remote_file_id) =
                    remote_row.as_ref().and_then(|n| n.remote_file_id.clone())
                else {
                    return Err(ExecError::RemoteGone {
                        path: from.to_string(),
                    });
                };
                let lease = self.enqueue_and_lease(
                    OpKind::RenameRemote,
                    from,
                    Some(to),
                    Some(&remote_file_id),
                    expected_version,
                    expected_checksum.clone(),
                    None,
                )?;
                let Lease::Held { id, created_at } = lease else {
                    return Ok(OpOutcome::AlreadyDone);
                };
                let attempts = self.journal.op(id)?.map(|r| r.attempts).unwrap_or(1);
                let request = RenameRequest {
                    remote_file_id: remote_file_id.clone(),
                    from: from.to_string(),
                    to: to.to_string(),
                    // G5: the op's OWN precondition, taken when the plan was made — never the
                    // server's current version, which would make the precondition a formality
                    // that always passes.
                    expected_version,
                    expected_checksum,
                    idempotency_key: self.request_key(from, to, &created_at),
                };
                let obj = match self.remote.rename(&request).await {
                    Ok(o) => o,
                    Err(e) => {
                        self.fail(id, &e, attempts)?;
                        return Err(e);
                    }
                };
                self.journal.delete_remote(&self.ctx.mapping_id, from)?;
                let node = self.remote_node(to, &obj);
                self.journal.put_remote(&self.ctx.mapping_id, &node)?;
                let now = self.now();
                let owner = self.ctx.lease_owner.clone();
                self.journal
                    .confirm_delete_op(id, &owner, true, true, &now)?;
                Ok(OpOutcome::Applied)
            }
        }
    }

    async fn do_conflict_copy(
        &mut self,
        path: &str,
        copy_path: &str,
        kind: ConflictKind,
        local_hash: Option<String>,
    ) -> ExecResult<OpOutcome> {
        let lease = self.enqueue_and_lease(
            OpKind::WriteConflictCopy,
            path,
            Some(copy_path),
            None,
            None,
            None,
            local_hash.clone(),
        )?;
        let Lease::Held { id, .. } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        let attempts = self.journal.op(id)?.map(|r| r.attempts).unwrap_or(1);
        let stat = match self.local.copy(path, copy_path).await {
            Ok(s) => s,
            Err(e) => {
                self.fail(id, &e, attempts)?;
                return Err(e);
            }
        };
        let node = self.local_node(copy_path, &stat);
        self.journal.put_local(&self.ctx.mapping_id, &node)?;
        let remote = self
            .journal
            .remote_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let now = self.now();
        self.journal.put_conflict(&ConflictRow {
            id: format!("{}:{path}:{}", self.ctx.mapping_id, kind.as_str()),
            mapping_id: self.ctx.mapping_id.clone(),
            path_nfc: path.to_string(),
            kind,
            local_hash,
            remote_file_id: remote.as_ref().and_then(|n| n.remote_file_id.clone()),
            remote_checksum: remote.as_ref().and_then(|n| n.checksum.clone()),
            conflict_copy_path: Some(copy_path.to_string()),
            detected_at: now.clone(),
            // D7 resolves this class on the spot: both copies reach the cloud, so the user is
            // TOLD, not asked.
            resolved_at: Some(now.clone()),
            resolution: Some("both_kept".to_string()),
        })?;
        // The copy is a plain local file now; there is no synced row for it until it is uploaded,
        // which the next plan does. The queue row is retired through the door that writes `ops`
        // and cannot reach `tree_synced` — not marked `failed`, which would be a lie on the
        // surface the user reads.
        let owner = self.ctx.lease_owner.clone();
        self.journal
            .retire_op(id, &owner, "a conflict copy was written (D7)", &now)?;
        Ok(OpOutcome::Applied)
    }

    async fn do_flag_local_edit(
        &mut self,
        path: &str,
        local_hash: Option<String>,
    ) -> ExecResult<OpOutcome> {
        let local = self
            .journal
            .local_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let remote = self
            .journal
            .remote_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let (Some(local), Some(remote)) = (local, remote) else {
            return Ok(OpOutcome::NothingToDo(
                "one of the two sides is not in the journal yet",
            ));
        };
        let Some(remote_file_id) = remote.remote_file_id.clone() else {
            return Ok(OpOutcome::NothingToDo("the cloud row has no id"));
        };
        let checksum = remote.checksum.clone().or_else(|| {
            self.journal
                .synced_tree(&self.ctx.mapping_id)
                .ok()
                .and_then(|t| t.get(path).and_then(|n| n.checksum.clone()))
        });
        if checksum.is_none() {
            return Ok(OpOutcome::NothingToDo("the cloud row carries no checksum"));
        }
        let content_hash = local_hash.or_else(|| local.content_hash.clone());
        if content_hash.is_none() {
            return Ok(OpOutcome::NothingToDo("the local hash is not known yet"));
        }
        let lease = self.enqueue_and_lease(
            OpKind::PreserveLocalEdit,
            path,
            None,
            Some(&remote_file_id),
            remote.remote_version,
            remote.checksum.clone(),
            content_hash.clone(),
        )?;
        let Lease::Held { id, .. } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        let now = self.now();
        let owner = self.ctx.lease_owner.clone();
        self.journal.preserve_local_edit(
            id,
            &owner,
            &LocalConfirmation {
                is_dir: false,
                size: local.size,
                mtime_ns: local.mtime_ns,
                volume_id: local.volume_id.clone(),
                file_id: local.file_id.clone(),
                content_hash,
                local_edit_flagged: true,
            },
            &RemoteConfirmation {
                remote_file_id,
                remote_version: remote.remote_version.unwrap_or(1),
                checksum,
            },
            &now,
        )?;
        Ok(OpOutcome::Applied)
    }

    fn do_record_conflict(
        &mut self,
        path: &str,
        kind: ConflictKind,
        detail: &str,
    ) -> ExecResult<OpOutcome> {
        let now = self.now();
        let remote = self
            .journal
            .remote_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let local = self
            .journal
            .local_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        self.journal.put_conflict(&ConflictRow {
            id: format!("{}:{path}:{}", self.ctx.mapping_id, kind.as_str()),
            mapping_id: self.ctx.mapping_id.clone(),
            path_nfc: path.to_string(),
            kind,
            local_hash: local.and_then(|n| n.content_hash),
            remote_file_id: remote.as_ref().and_then(|n| n.remote_file_id.clone()),
            remote_checksum: remote.as_ref().and_then(|n| n.checksum.clone()),
            conflict_copy_path: None,
            detected_at: now,
            // Unresolved on purpose: a name that cannot exist, a collision or a
            // delete-versus-modify race needs a human. The planner proposes nothing further for
            // the path until it is resolved, which is what makes the fixed point reachable.
            resolved_at: None,
            resolution: Some(detail.to_string()),
        })?;
        Ok(OpOutcome::Applied)
    }

    async fn do_record_synced(&mut self, path: &str) -> ExecResult<OpOutcome> {
        let local = self
            .journal
            .local_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let remote = self
            .journal
            .remote_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let (Some(local), Some(remote)) = (local, remote.filter(RemoteNode::is_live)) else {
            return Ok(OpOutcome::NothingToDo("one of the two sides is gone"));
        };
        let Some(remote_file_id) = remote.remote_file_id.clone() else {
            return Ok(OpOutcome::NothingToDo("the cloud row has no id"));
        };
        if local.is_dir != remote.is_dir {
            return Ok(OpOutcome::NothingToDo(
                "the two sides disagree about whether this is a folder",
            ));
        }
        if !local.is_dir {
            // The two sides agreed when the plan was made. Between then and now another device
            // may have written, so the agreement is re-checked against what is on disk NOW — a
            // synced row is never assembled from two moments (I1).
            let on_disk = self.local.stat(path, true).await?;
            let found = on_disk.as_ref().and_then(|s| s.content_hash.clone());
            if found.is_none() || found != remote.checksum {
                return Err(ExecError::PreImageMismatch {
                    path: path.to_string(),
                    expected: remote.checksum.clone().unwrap_or_default(),
                    found,
                });
            }
        }
        let kind = if local.is_dir {
            OpKind::MkdirRemote
        } else {
            OpKind::UploadUpdate
        };
        let lease = self.enqueue_and_lease(
            kind,
            path,
            None,
            Some(&remote_file_id),
            remote.remote_version,
            remote.checksum.clone(),
            local.content_hash.clone(),
        )?;
        let Lease::Held { id, .. } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        self.confirm(
            id,
            path,
            local.is_dir,
            local.content_hash.clone(),
            &remote_file_id,
            remote.remote_version.unwrap_or(1),
            remote.checksum.clone(),
        )?;
        Ok(OpOutcome::Applied)
    }

    fn do_forget_synced(&mut self, path: &str) -> ExecResult<OpOutcome> {
        let lease =
            self.enqueue_and_lease(OpKind::Unindex, path, None, None, None, None, None)?;
        let Lease::Held { id, .. } = lease else {
            return Ok(OpOutcome::AlreadyDone);
        };
        let now = self.now();
        let owner = self.ctx.lease_owner.clone();
        self.journal
            .confirm_delete_op(id, &owner, true, true, &now)?;
        Ok(OpOutcome::Applied)
    }

    /// The only place this module reaches `tree_synced`, and it reaches it through
    /// [`Journal::confirm_op`] — the guarded door. There is no other write here, by construction.
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
    ) -> ExecResult<()> {
        let local = self
            .journal
            .local_tree(&self.ctx.mapping_id)?
            .get(path)
            .cloned();
        let owner = self.ctx.lease_owner.clone();
        let now = self.now();
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
            &now,
        )?;
        Ok(())
    }
}

/// A filesystem mtime in nanoseconds since the epoch, as the RFC3339 the cloud stores in
/// `client_modified_at` (SPEC-SERVER §2.2).
fn rfc3339_from_ns(ns: i64) -> String {
    let secs = ns.div_euclid(1_000_000_000);
    chrono::DateTime::from_timestamp(secs, 0)
        .map(rfc3339)
        .unwrap_or_else(|| "1970-01-01T00:00:00Z".to_string())
}
