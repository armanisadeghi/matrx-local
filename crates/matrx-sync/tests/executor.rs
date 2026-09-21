//! FS-L1 unit 4 — the executor battery.
//!
//! Everything here drives the REAL [`Executor`], the REAL [`Journal`] on a tempdir file, the REAL
//! scanner and the REAL planner. The two things that are faked are the seams the executor acts
//! through — a disk that can be told to fill up and a cloud that can be told to return 412 — and
//! they are faked because those are the two conditions this code exists to survive and neither can
//! be produced on demand against a real machine.
//!
//! Green here proves the executor's **decisions**. Product evidence is the live-service tier.

use chrono::{TimeZone, Utc};
use matrx_sync::custody::clock::TestClock;
use matrx_sync::exec::fakes::{FakeLocal, FakeRemote, Fault};
use matrx_sync::exec::{ExecContext, ExecError, ExecReport, Executor, LocalIo, NoProgress};
use matrx_sync::journal::Journal;
use matrx_sync::knobs::Knobs;
use matrx_sync::model::Direction;
use matrx_sync::planner::{plan, Plan, PlanContext};
use matrx_sync::scan::{hash_files, scan_root, ScanOptions};
use matrx_sync::states::{HonestState, Scope};
use matrx_sync::{model::MappingRow, HONEST_STATES};
use std::collections::BTreeSet;
use std::path::PathBuf;

const MAPPING: &str = "mapping-1";
const ORG: &str = "11111111-1111-1111-1111-111111111111";

struct Bed {
    dir: tempfile::TempDir,
    journal_path: PathBuf,
    local: FakeLocal,
    remote: FakeRemote,
    clock: TestClock,
    ctx: ExecContext,
}

fn bed(direction: Direction) -> Bed {
    let dir = tempfile::tempdir().expect("tempdir");
    let root = dir.path().join("root");
    std::fs::create_dir_all(&root).expect("root");
    let journal_path = dir.path().join("syncd.db");
    let mut ctx = ExecContext::new(MAPPING, ORG, "this-mac", direction);
    ctx.knobs = Knobs::default();
    Bed {
        local: FakeLocal::new(&root),
        remote: FakeRemote::new(),
        clock: TestClock::new(Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0).unwrap()),
        journal_path,
        ctx,
        dir,
    }
}

impl Bed {
    fn open_journal(&self) -> Journal {
        let journal = Journal::open(&self.journal_path).expect("journal");
        if journal.mapping(MAPPING).expect("mapping read").is_none() {
            journal
                .put_mapping(&MappingRow {
                    id: MAPPING.to_string(),
                    organization_id: ORG.to_string(),
                    cloud_kind: "org_root".to_string(),
                    cloud_folder_id: None,
                    local_root: self.local.root().display().to_string(),
                    local_root_volume_id: "fake-volume".to_string(),
                    direction: self.ctx.direction,
                    desired_state: "active".to_string(),
                    state: "pending".to_string(),
                    state_detail: None,
                    knobs: "{}".to_string(),
                    file_cursor: None,
                    folder_cursor: None,
                    cloud_row_version: None,
                    marker_uuid: "marker-1".to_string(),
                    created_at: Some("2026-09-21T12:00:00Z".to_string()),
                    updated_at: Some("2026-09-21T12:00:00Z".to_string()),
                    last_sync_at: None,
                    last_full_rescan_at: None,
                })
                .expect("put mapping");
        }
        journal
    }

    /// Walk the fake disk into `tree_local`, exactly as the daemon's scan step does.
    fn scan(&self, journal: &Journal) {
        let previous = journal.local_tree(MAPPING).expect("previous tree");
        let opts = ScanOptions::new(0, "2026-09-21T12:00:00Z");
        let mut report = scan_root(self.local.root(), &previous, &opts);
        let outcomes = hash_files(&report.hash_requests(), 2);
        report.apply_hashes(outcomes);
        for path in previous.paths().cloned().collect::<Vec<_>>() {
            if !report.tree.contains(&path) {
                journal.delete_local(MAPPING, &path).expect("prune local");
            }
        }
        for (_, node) in report.tree.iter() {
            journal.put_local(MAPPING, node).expect("put local");
        }
    }

    /// Put what the fake cloud holds into `tree_remote`, as a feed poll would.
    fn poll(&self, journal: &Journal, paths: &[&str]) {
        for path in paths {
            let node = self.remote.node_for(path);
            if let Some(node) = node {
                journal.put_remote(MAPPING, &node).expect("put remote");
            }
        }
    }

    fn plan(&self, journal: &Journal) -> Plan {
        let ctx = PlanContext {
            device_name: self.ctx.device_name.clone(),
            today: "2026-09-21".to_string(),
            recent_deletions: journal
                .deletions_since(MAPPING, "1970-01-01T00:00:00Z")
                .expect("window"),
            window_item_count: journal
                .window_item_count(MAPPING, "1970-01-01T00:00:00Z")
                .expect("denominator"),
            open_conflicts: journal
                .open_conflicts(MAPPING)
                .expect("conflicts")
                .into_iter()
                .map(|c| c.path_nfc)
                .collect::<BTreeSet<_>>(),
        };
        plan(
            &journal.local_tree(MAPPING).expect("local"),
            &journal.remote_tree(MAPPING).expect("remote"),
            &journal.synced_tree(MAPPING).expect("synced"),
            self.ctx.direction,
            &self.ctx.knobs,
            &ctx,
        )
    }

    async fn run(&self, journal: &mut Journal, plan: &Plan) -> ExecReport {
        let mut exec = Executor::new(
            journal,
            &self.local,
            &self.remote,
            &self.clock,
            &NoProgress,
            self.ctx.clone(),
        )
        .expect("executor");
        exec.execute(plan).await.expect("execute")
    }

    /// Scan, poll, plan, execute — one pass, as the daemon's loop does it.
    async fn pass(&self, journal: &mut Journal, remote_paths: &[&str]) -> ExecReport {
        self.scan(journal);
        self.poll(journal, remote_paths);
        let p = self.plan(journal);
        self.run(journal, &p).await
    }

    fn keep(&self) -> &tempfile::TempDir {
        &self.dir
    }
}

// ---------------------------------------------------------------- the happy paths

#[tokio::test]
async fn a_local_file_reaches_the_cloud_and_is_double_confirmed() {
    let bed = bed(Direction::TwoWay);
    bed.local.put("notes.md", "hello");
    let mut journal = bed.open_journal();

    let report = bed.pass(&mut journal, &["notes.md"]).await;
    assert!(report.applied >= 1, "{report:?}");
    assert_eq!(bed.remote.live("notes.md").as_deref(), Some("hello"));

    let synced = journal.synced_tree(MAPPING).expect("synced");
    let row = synced.get("notes.md").expect("a synced row was written");
    assert_eq!(row.content_hash, row.checksum, "I1: one state both sides confirmed");
    assert_eq!(row.content_hash.as_deref(), Some(FakeLocal::hash_of("hello").as_str()));
    let _ = bed.keep();
}

#[tokio::test]
async fn a_cloud_file_reaches_the_disk_through_staging_and_is_double_confirmed() {
    let bed = bed(Direction::TwoWay);
    bed.remote.put("from-cloud.txt", "cloud bytes");
    let mut journal = bed.open_journal();

    let report = bed.pass(&mut journal, &["from-cloud.txt"]).await;
    assert!(report.applied >= 1, "{report:?}");
    assert_eq!(bed.local.read("from-cloud.txt").as_deref(), Some("cloud bytes"));
    assert!(
        journal
            .synced_tree(MAPPING)
            .expect("synced")
            .contains("from-cloud.txt"),
        "the download was confirmed"
    );
    let _ = bed.keep();
}

#[tokio::test]
async fn the_plan_reaches_a_fixed_point_and_the_mapping_says_idle() {
    let bed = bed(Direction::TwoWay);
    bed.local.put("a.txt", "one");
    bed.remote.put("b.txt", "two");
    let mut journal = bed.open_journal();

    for _ in 0..4 {
        bed.pass(&mut journal, &["a.txt", "b.txt"]).await;
    }
    bed.scan(&journal);
    bed.poll(&journal, &["a.txt", "b.txt"]);
    let settled = bed.plan(&journal);
    assert!(settled.is_empty(), "the engine did not settle: {settled:?}");
    assert_eq!(
        journal.mapping(MAPPING).expect("mapping").expect("row").state,
        "idle"
    );
    let _ = bed.keep();
}

// ---------------------------------------------------------------- races

#[tokio::test]
async fn a_412_discards_the_rest_of_the_plan_and_is_never_retried_blind() {
    let bed = bed(Direction::TwoWay);
    bed.local.put("raced.txt", "mine");
    let mut journal = bed.open_journal();
    bed.scan(&journal);
    // The cloud already holds a DIFFERENT file at that path, which our tree_remote has not seen.
    bed.remote.put("raced.txt", "theirs");
    let p = bed.plan(&journal);
    let report = bed.run(&mut journal, &p).await;

    assert!(report.replan, "a precondition failure must ask for a new plan");
    assert_eq!(
        bed.remote.live("raced.txt").as_deref(),
        Some("theirs"),
        "the other device's bytes were not overwritten"
    );
    assert_eq!(
        bed.remote.uploads.lock().expect("uploads").len(),
        0,
        "nothing was sent twice, and nothing was sent without its precondition"
    );
    let op = journal
        .ops_in_state(MAPPING, matrx_sync::model::OpState::Ready)
        .expect("ops")
        .into_iter()
        .chain(
            journal
                .ops_in_state(MAPPING, matrx_sync::model::OpState::Failed)
                .expect("ops"),
        )
        .find(|o| o.path_nfc == "raced.txt")
        .expect("the refused op is still in the queue with its error");
    assert_eq!(op.error_code.as_deref(), Some("precondition_failed"));
    let _ = bed.keep();
}

#[tokio::test]
async fn a_download_never_clobbers_a_local_file_the_plan_did_not_know_about() {
    let bed = bed(Direction::TwoWay);
    bed.remote.put("surprise.txt", "cloud");
    let mut journal = bed.open_journal();
    bed.poll(&journal, &["surprise.txt"]);
    let p = bed.plan(&journal);
    // Between the plan and the act, the user drops a different file at that exact path.
    bed.local.put("surprise.txt", "the user's own work");
    let report = bed.run(&mut journal, &p).await;

    assert!(report.replan, "{report:?}");
    assert_eq!(
        bed.local.read("surprise.txt").as_deref(),
        Some("the user's own work"),
        "I3: a create is not a licence to overwrite"
    );
    let _ = bed.keep();
}

// ---------------------------------------------------------------- resumability

#[tokio::test]
async fn a_crash_between_ops_resumes_without_uploading_anything_twice() {
    let bed = bed(Direction::TwoWay);
    for i in 0..6 {
        bed.local.put(&format!("f{i}.txt"), &format!("body {i}"));
    }
    let mut journal = bed.open_journal();
    bed.scan(&journal);
    let p = bed.plan(&journal);
    assert!(p.ops.len() >= 6, "{p:?}");

    // The crash: the third upload never answers, and the process dies. `times: 1` so only one op
    // is hit; the executor defers it and stops descending into its path.
    bed.local.inject(Fault {
        path: "f3.txt".to_string(),
        call: "stat".to_string(),
        error: ExecError::Offline {
            detail: "the machine lost power mid-plan".to_string(),
        },
        times: 1,
    });
    let first = bed.run(&mut journal, &p).await;
    assert!(first.applied >= 1, "{first:?}");

    // The process comes back: the journal handle is dropped and the file reopened, leases are
    // released, and a NEW plan is derived from the trees — nothing replays a stored plan.
    drop(journal);
    let mut journal = bed.open_journal();
    journal
        .release_leases(MAPPING, "2026-09-21T12:05:00Z")
        .expect("release");
    for _ in 0..3 {
        let paths: Vec<String> = (0..6).map(|i| format!("f{i}.txt")).collect();
        let refs: Vec<&str> = paths.iter().map(String::as_str).collect();
        bed.pass(&mut journal, &refs).await;
    }

    for i in 0..6 {
        assert_eq!(
            bed.remote.live(&format!("f{i}.txt")).as_deref(),
            Some(format!("body {i}").as_str()),
            "f{i} did not converge"
        );
    }
    let uploads = bed.remote.uploads.lock().expect("uploads").clone();
    let mut unique: Vec<String> = uploads.clone();
    unique.sort();
    unique.dedup();
    assert_eq!(
        uploads.len(),
        unique.len(),
        "a resumed plan uploaded the same path twice: {uploads:?}"
    );
    let _ = bed.keep();
}

#[tokio::test]
async fn replaying_an_identical_plan_does_the_work_once() {
    let bed = bed(Direction::TwoWay);
    bed.local.put("once.txt", "body");
    let mut journal = bed.open_journal();
    bed.scan(&journal);
    let p = bed.plan(&journal);

    let first = bed.run(&mut journal, &p).await;
    assert_eq!(first.applied, p.ops.len());
    // The SAME plan again — the shape a crash between "plan" and "record that the plan ran"
    // produces. Every op is already `done` under its idempotency key (I5).
    let second = bed.run(&mut journal, &p).await;
    assert_eq!(second.applied, 0, "{second:?}");
    assert_eq!(second.already_done, p.ops.len(), "{second:?}");
    assert_eq!(bed.remote.uploads.lock().expect("uploads").len(), 1);
    let _ = bed.keep();
}

// ---------------------------------------------------------------- honest states

#[tokio::test]
async fn a_full_disk_suspends_the_mapping_with_a_state_the_user_can_act_on() {
    let bed = bed(Direction::TwoWay);
    bed.remote.put("big.bin", "a lot of bytes");
    let mut journal = bed.open_journal();
    bed.poll(&journal, &["big.bin"]);
    bed.local.inject(Fault {
        path: "*".to_string(),
        call: "commit".to_string(),
        error: ExecError::DiskFull {
            detail: "No space left on device".to_string(),
        },
        times: 5,
    });
    let p = bed.plan(&journal);
    let report = bed.run(&mut journal, &p).await;

    assert_eq!(report.state, "suspended_disk_full");
    assert_eq!(
        journal.mapping(MAPPING).expect("mapping").expect("row").state,
        "suspended_disk_full"
    );
    assert!(
        bed.local.read("big.bin").is_none(),
        "I4: nothing partial reached the user's path"
    );
    let _ = bed.keep();
}

#[tokio::test]
async fn a_revoked_permission_names_itself_rather_than_failing_silently() {
    let bed = bed(Direction::TwoWay);
    bed.local.put("locked.txt", "body");
    let mut journal = bed.open_journal();
    bed.scan(&journal);
    bed.local.inject(Fault {
        path: "*".to_string(),
        call: "stat".to_string(),
        error: ExecError::PermissionDenied {
            path: "locked.txt".to_string(),
            detail: "Operation not permitted".to_string(),
        },
        times: 5,
    });
    let p = bed.plan(&journal);
    let report = bed.run(&mut journal, &p).await;
    assert_eq!(report.state, "permission_denied");
    assert!(report.state_detail.is_some(), "the state carries a sentence");
    let _ = bed.keep();
}

#[tokio::test]
async fn being_over_quota_is_a_state_and_stops_the_mapping() {
    let bed = bed(Direction::TwoWay);
    bed.local.put("x.txt", "body");
    let mut journal = bed.open_journal();
    bed.scan(&journal);
    bed.remote.inject(Fault {
        path: "*".to_string(),
        call: "upload".to_string(),
        error: ExecError::OverQuota {
            detail: "your organization is out of storage".to_string(),
        },
        times: 5,
    });
    let p = bed.plan(&journal);
    let report = bed.run(&mut journal, &p).await;
    assert_eq!(report.state, "over_quota");
    let _ = bed.keep();
}

#[test]
fn every_state_the_executor_can_reach_is_in_the_published_artifact() {
    // The executor may not invent a state (C3). This walks every variant the enum can produce and
    // asserts the value is in `contracts/honest_states.json` with the `mapping` scope.
    let candidates = [
        ExecError::PermissionDenied {
            path: "p".into(),
            detail: "d".into(),
        },
        ExecError::DiskFull { detail: "d".into() },
        ExecError::OverQuota { detail: "d".into() },
        ExecError::Offline { detail: "d".into() },
        ExecError::SignInNeeded { detail: "d".into() },
        ExecError::OrganizationRefused { detail: "d".into() },
    ];
    for e in candidates {
        let state = e.honest_state().expect("this variant names a state");
        let published = HonestState::get(state).expect("the state is published");
        assert!(
            published.allows(Scope::Mapping),
            "{state} is not mapping-scoped"
        );
        assert!(
            HONEST_STATES.iter().any(|s| s.value == state),
            "{state} is not in the artifact"
        );
    }
}

#[tokio::test]
async fn a_state_outside_the_artifact_cannot_be_written_to_a_mapping_row() {
    let bed = bed(Direction::TwoWay);
    let journal = bed.open_journal();
    let refused = journal.set_mapping_state(MAPPING, "everything_is_fine", None, "2026-09-21T12:00:00Z");
    assert!(refused.is_err(), "a made-up state must be refused (C3)");
    let _ = bed.keep();
}

// ---------------------------------------------------------------- directions

#[tokio::test]
async fn a_download_only_mapping_never_writes_the_cloud_from_local_bytes() {
    let bed = bed(Direction::DownloadOnly);
    bed.local.put("mine.txt", "local only");
    let mut journal = bed.open_journal();
    for _ in 0..3 {
        bed.pass(&mut journal, &["mine.txt"]).await;
    }
    assert!(
        bed.remote.live("mine.txt").is_none(),
        "download_only uploaded a local file"
    );
    assert_eq!(
        bed.local.read("mine.txt").as_deref(),
        Some("local only"),
        "and it did not touch the local bytes either"
    );
    let _ = bed.keep();
}

#[tokio::test]
async fn an_upload_only_mapping_never_writes_the_disk_from_cloud_bytes() {
    let bed = bed(Direction::UploadOnly);
    bed.remote.put("theirs.txt", "cloud only");
    let mut journal = bed.open_journal();
    for _ in 0..3 {
        bed.pass(&mut journal, &["theirs.txt"]).await;
    }
    assert!(
        bed.local.read("theirs.txt").is_none(),
        "upload_only wrote a cloud file to disk"
    );
    let _ = bed.keep();
}

#[tokio::test]
async fn the_executor_refuses_an_op_its_direction_forbids_even_if_the_planner_emits_one() {
    use matrx_sync::planner::{Change, PlanOp};
    let bed = bed(Direction::DownloadOnly);
    bed.local.put("smuggled.txt", "bytes");
    let mut journal = bed.open_journal();
    bed.scan(&journal);
    // A hand-built plan standing in for a planner defect: the guard must be the executor's, not
    // an assumption about the planner's correctness.
    let forbidden = Plan {
        ops: vec![PlanOp::Upload {
            path: "smuggled.txt".to_string(),
            change: Change::Create,
            expected_version: None,
            expected_checksum: None,
            local_hash: FakeLocal::hash_of("bytes"),
        }],
        suspended: None,
    };
    let report = bed.run(&mut journal, &forbidden).await;
    assert!(bed.remote.live("smuggled.txt").is_none());
    assert!(
        report.deferred.iter().any(|(_, c)| *c == "direction_refused"),
        "the refusal must be recorded, not swallowed: {report:?}"
    );
    let _ = bed.keep();
}

// ---------------------------------------------------------------- the breaker

#[tokio::test]
async fn a_suspended_plan_applies_nothing_at_all() {
    use matrx_sync::planner::SuspendReason;
    let bed = bed(Direction::TwoWay);
    bed.local.put("keep.txt", "still here");
    let mut journal = bed.open_journal();
    bed.scan(&journal);
    let suspended = Plan {
        ops: Vec::new(),
        suspended: Some(SuspendReason::MassDelete {
            planned_deletes: 40,
            tracked_items: 40,
            percent_threshold: 50,
            count_threshold: 1000,
            min_count_threshold: 20,
            recent_deletions: 0,
            window_hours: 24,
        }),
    };
    let report = bed.run(&mut journal, &suspended).await;
    assert_eq!(report.state, "suspended_mass_delete");
    assert_eq!(report.applied, 0);
    assert_eq!(bed.local.read("keep.txt").as_deref(), Some("still here"));
    assert_eq!(
        journal.mapping(MAPPING).expect("mapping").expect("row").state,
        "suspended_mass_delete"
    );
    let _ = bed.keep();
}

// ---------------------------------------------------------------- integrity

#[tokio::test]
async fn bytes_that_do_not_hash_to_what_the_plan_expected_never_reach_the_user_path() {
    let bed = bed(Direction::TwoWay);
    bed.remote.put("corrupt.txt", "good bytes");
    let mut journal = bed.open_journal();
    bed.poll(&journal, &["corrupt.txt"]);
    let p = bed.plan(&journal);
    // The cloud hands back different bytes than the feed advertised.
    bed.remote.put("corrupt.txt", "TAMPERED");
    let report = bed.run(&mut journal, &p).await;

    assert!(bed.local.read("corrupt.txt").is_none(), "{report:?}");
    assert!(
        !journal
            .synced_tree(MAPPING)
            .expect("synced")
            .contains("corrupt.txt"),
        "nothing was confirmed"
    );
    let _ = bed.keep();
}

#[tokio::test]
async fn a_deferred_directory_stops_its_own_children_rather_than_failing_twice() {
    let bed = bed(Direction::TwoWay);
    bed.remote.put_dir("Notes");
    bed.remote.put("Notes/a.md", "a");
    bed.remote.put("Notes/b.md", "b");
    let mut journal = bed.open_journal();
    bed.poll(&journal, &["Notes", "Notes/a.md", "Notes/b.md"]);
    bed.local.inject(Fault {
        path: "Notes".to_string(),
        call: "mkdir".to_string(),
        error: ExecError::Io {
            path: "Notes".to_string(),
            detail: "the directory could not be created".to_string(),
        },
        times: 1,
    });
    let p = bed.plan(&journal);
    let report = bed.run(&mut journal, &p).await;
    assert!(
        report.skipped > 0 || report.deferred.iter().any(|(p, _)| p == "Notes"),
        "{report:?}"
    );
    let _ = bed.keep();
}
