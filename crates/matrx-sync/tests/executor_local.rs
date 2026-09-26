//! FS-L1 unit 4b — the production [`RealLocalIo`] against a real disk.
//!
//! Nothing here is faked. Every test writes real files into a real tempdir and asserts on what the
//! filesystem actually holds afterwards, because the four things this module exists for — atomic
//! writes, NFC/NFD resolution, preserved mtimes and the OS trash — are all statements about a real
//! filesystem that an in-memory model would answer correctly by construction.

use matrx_sync::exec::local::RealLocalIo;
use matrx_sync::exec::{ExecError, LocalIo};
use matrx_sync::model::ConflictKind;
use sha2::{Digest, Sha256};
use std::path::Path;

fn hash_of(s: &str) -> String {
    let mut h = Sha256::new();
    h.update(s.as_bytes());
    format!("{:x}", h.finalize())
}

fn io(dir: &Path, case_insensitive: bool) -> RealLocalIo {
    std::fs::create_dir_all(dir.join(".matrx-sync").join("tmp")).expect("staging");
    RealLocalIo::new(dir, case_insensitive)
}

#[tokio::test]
async fn a_download_reaches_its_name_only_after_the_bytes_hash_to_what_was_promised() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);

    let staged = local.stage("report.txt").await.expect("stage");
    assert!(
        staged.temp_path.starts_with(dir.path().join(".matrx-sync").join("tmp")),
        "staging must live inside the mapping's own state directory, not beside the user's files"
    );
    std::fs::write(&staged.temp_path, b"the real bytes").expect("write staged");
    let stat = local
        .commit_staged(staged, &hash_of("the real bytes"), None)
        .await
        .expect("commit");

    assert_eq!(
        std::fs::read_to_string(dir.path().join("report.txt")).expect("read"),
        "the real bytes"
    );
    assert_eq!(stat.content_hash.as_deref(), Some(hash_of("the real bytes").as_str()));
    assert!(
        std::fs::read_dir(dir.path().join(".matrx-sync").join("tmp"))
            .expect("staging dir")
            .next()
            .is_none(),
        "the staging directory is empty once the file is in place"
    );
}

#[tokio::test]
async fn bytes_that_do_not_match_are_discarded_and_never_wear_a_user_visible_name() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    std::fs::write(dir.path().join("report.txt"), b"what the user already has").expect("seed");

    let staged = local.stage("report.txt").await.expect("stage");
    std::fs::write(&staged.temp_path, b"corrupted in flight").expect("write staged");
    let refused = local
        .commit_staged(staged, &hash_of("the real bytes"), None)
        .await;

    assert!(matches!(refused, Err(ExecError::ChecksumMismatch { .. })), "{refused:?}");
    assert_eq!(
        std::fs::read_to_string(dir.path().join("report.txt")).expect("read"),
        "what the user already has",
        "the file that was already there is untouched"
    );
    assert!(
        std::fs::read_dir(dir.path().join(".matrx-sync").join("tmp"))
            .expect("staging dir")
            .next()
            .is_none(),
        "and the bad bytes are gone rather than left to be retried into place"
    );
}

#[tokio::test]
async fn the_modification_time_the_cloud_recorded_survives_the_download() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    // 2021-03-04T05:06:07Z, well before this test runs, so "it kept the old time" and "it took
    // now" cannot be confused.
    let when_ns: i64 = 1_614_834_367 * 1_000_000_000;

    let staged = local.stage("old.txt").await.expect("stage");
    std::fs::write(&staged.temp_path, b"aged").expect("write staged");
    let stat = local
        .commit_staged(staged, &hash_of("aged"), Some(when_ns))
        .await
        .expect("commit");

    let seconds = stat.mtime_ns.expect("mtime") / 1_000_000_000;
    assert_eq!(
        seconds,
        when_ns / 1_000_000_000,
        "the mtime the cloud recorded was replaced by the arrival time"
    );
}

#[tokio::test]
async fn a_file_the_filesystem_spells_differently_is_still_found_by_its_key() {
    use unicode_normalization::UnicodeNormalization;
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);

    // The cloud's key is NFC; macOS commonly hands the same characters back as NFD. Joining the
    // key onto the root finds nothing, which is why I8 exists.
    let nfc_key: String = "café/résumé.txt".nfc().collect();
    let nfd_dir: String = "café".nfd().collect();
    let nfd_file: String = "résumé.txt".nfd().collect();
    std::fs::create_dir_all(dir.path().join(&nfd_dir)).expect("mkdir nfd");
    std::fs::write(dir.path().join(&nfd_dir).join(&nfd_file), b"accented").expect("write nfd");

    let stat = local.stat(&nfc_key, true).await.expect("stat");
    let stat = stat.expect("the NFD file is found through its NFC key");
    assert_eq!(stat.content_hash.as_deref(), Some(hash_of("accented").as_str()));

    let source = local.source_of(&nfc_key).await.expect("source");
    assert_eq!(
        std::fs::read_to_string(&source).expect("read through the resolved path"),
        "accented"
    );
}

#[tokio::test]
async fn two_spellings_of_one_name_become_a_conflict_rather_than_a_silent_pick() {
    use unicode_normalization::UnicodeNormalization;
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);

    let nfc_name: String = "é.txt".nfc().collect();
    let nfd_name: String = "é.txt".nfd().collect();
    std::fs::write(dir.path().join(&nfd_name), b"one").expect("nfd");
    std::fs::write(dir.path().join(&nfc_name), b"two").ok();
    let both_spellings = std::fs::read_dir(dir.path())
        .expect("list")
        .flatten()
        .filter(|e| {
            e.file_name()
                .to_str()
                .is_some_and(|n| n == nfc_name || n == nfd_name)
        })
        .count();
    if both_spellings < 2 {
        // APFS is normalisation-INSENSITIVE: the second write opened the first file. The class is
        // physically unreachable on this volume, which is said out loud rather than passed
        // silently. It is reachable on ext4, and the resolver's refusal is the same code path the
        // case-collision test above exercises.
        eprintln!("this volume folds NFC and NFD into one name; the collision class cannot arise here");
        return;
    }
    // Both exist and both normalise to the same key. Acting on the key would destroy one of them.
    let refused = local.stat(&nfc_name, false).await;
    assert!(
        matches!(
            refused,
            Err(ExecError::NameCollision {
                kind: ConflictKind::UnicodeCollision,
                ..
            })
        ),
        "{refused:?}"
    );
}

#[cfg(unix)]
#[tokio::test]
async fn two_spellings_of_the_same_inode_are_one_file_not_a_collision() {
    use unicode_normalization::UnicodeNormalization;
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    let nfc_name: String = "é.txt".nfc().collect();
    let nfd_name: String = "é.txt".nfd().collect();
    std::fs::write(dir.path().join(&nfd_name), b"one").expect("nfd");
    if std::fs::hard_link(dir.path().join(&nfd_name), dir.path().join(&nfc_name)).is_err() {
        // A normalization-insensitive volume cannot hold both spellings.
        return;
    }

    let stat = local.stat(&nfc_name, false).await.expect("same inode is unambiguous");
    assert!(stat.is_some());
}

#[cfg(windows)]
#[tokio::test]
async fn two_unicode_spellings_of_links_to_one_target_still_collide() {
    use unicode_normalization::UnicodeNormalization;
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    let nfc_name: String = "é.txt".nfc().collect();
    let nfd_name: String = "é.txt".nfd().collect();
    let target = dir.path().join("target.txt");
    std::fs::write(&target, b"shared target").expect("target");
    if std::os::windows::fs::symlink_file(&target, dir.path().join(&nfd_name)).is_err()
        || std::os::windows::fs::symlink_file(&target, dir.path().join(&nfc_name)).is_err()
    {
        // Symlink creation needs Developer Mode or an elevated token on some hosts;
        // a filesystem that folds the spellings cannot represent this collision.
        return;
    }
    let result = local.stat(&nfc_name, false).await;
    assert!(matches!(
        result,
        Err(ExecError::NameCollision {
            kind: ConflictKind::UnicodeCollision,
            ..
        })
    ));
}

#[tokio::test]
async fn on_a_case_insensitive_volume_a_case_only_difference_is_a_conflict() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), true);
    std::fs::write(dir.path().join("Report.txt"), b"theirs").expect("seed");

    // The cloud holds `report.txt`; the disk holds `Report.txt`. On a volume that cannot hold
    // both, writing the cloud's copy overwrites the user's — so it is a conflict, not a write.
    let refused = local.stat("report.txt", false).await;
    assert!(
        matches!(
            refused,
            Err(ExecError::NameCollision {
                kind: ConflictKind::CaseCollision,
                ..
            })
        ),
        "{refused:?}"
    );
    assert_eq!(
        std::fs::read_to_string(dir.path().join("Report.txt")).expect("read"),
        "theirs"
    );
}

#[tokio::test]
async fn a_case_only_difference_is_not_a_conflict_on_a_volume_that_can_hold_both() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    std::fs::write(dir.path().join("Report.txt"), b"theirs").expect("seed");
    // `case_insensitive` is a fact about the VOLUME, from the admission probe — not a guess from
    // the platform. On a case-sensitive volume the two names are two files and neither is at risk.
    let answer = local.stat("report.txt", false).await;
    assert!(
        !matches!(answer, Err(ExecError::NameCollision { .. })),
        "the resolver must trust the volume probe, not second-guess it: {answer:?}"
    );
}

#[tokio::test]
async fn an_unlinked_delete_removes_the_file_and_a_missing_file_is_not_an_error() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    std::fs::write(dir.path().join("gone.txt"), b"x").expect("seed");

    local.remove("gone.txt", false).await.expect("remove");
    assert!(!dir.path().join("gone.txt").exists());
    // A delete that found nothing did its job: the file is absent, which is what was asked for.
    local
        .remove("gone.txt", false)
        .await
        .expect("removing an absent file is success, not an error");
}

#[cfg(target_os = "macos")]
#[tokio::test]
async fn a_trashed_delete_is_recoverable_by_the_user() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    // A name nothing else on this machine can own, so the assertion below can only be about this
    // test's own file and the cleanup cannot touch anything of the user's.
    let name = format!(
        "matrx-sync-trash-probe-{}.txt",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos()
    );
    std::fs::write(dir.path().join(&name), b"recoverable").expect("seed");

    local
        .remove(&name, true)
        .await
        .expect("sync.trash_local_deletes promises the OS trash");
    assert!(!dir.path().join(&name).exists(), "it left the sync folder");

    let trashed = std::path::PathBuf::from(std::env::var("HOME").expect("HOME"))
        .join(".Trash")
        .join(&name);
    assert!(
        trashed.exists(),
        "a propagated delete must be retrievable from the trash, not only from a cloud tombstone"
    );
    std::fs::remove_file(&trashed).expect("this test cleans up after itself");
}

#[tokio::test]
async fn a_conflict_copy_appears_whole_or_not_at_all() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    std::fs::write(dir.path().join("plan.md"), b"the local version").expect("seed");

    let stat = local
        .copy("plan.md", "plan (conflicted copy from this-mac 2026-09-21).md")
        .await
        .expect("copy");
    assert_eq!(
        stat.content_hash.as_deref(),
        Some(hash_of("the local version").as_str())
    );
    assert_eq!(
        std::fs::read_to_string(
            dir.path().join("plan (conflicted copy from this-mac 2026-09-21).md")
        )
        .expect("read copy"),
        "the local version"
    );
    assert_eq!(
        std::fs::read_to_string(dir.path().join("plan.md")).expect("read original"),
        "the local version",
        "the original is untouched"
    );
}

#[tokio::test]
async fn a_rename_creates_the_destinations_parents() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    std::fs::write(dir.path().join("loose.txt"), b"body").expect("seed");

    local
        .rename("loose.txt", "Archive/2026/loose.txt")
        .await
        .expect("rename");
    assert_eq!(
        std::fs::read_to_string(dir.path().join("Archive/2026/loose.txt")).expect("read"),
        "body"
    );
    assert!(!dir.path().join("loose.txt").exists());
}

#[tokio::test]
async fn file_identity_is_reported_so_a_move_is_a_move_and_not_a_re_upload() {
    let dir = tempfile::tempdir().expect("tempdir");
    let local = io(dir.path(), false);
    std::fs::write(dir.path().join("a.txt"), b"body").expect("seed");

    let before = local.stat("a.txt", false).await.expect("stat").expect("there");
    local.rename("a.txt", "b.txt").await.expect("rename");
    let after = local.stat("b.txt", false).await.expect("stat").expect("there");

    assert!(before.file_id.is_some() && before.volume_id.is_some());
    assert_eq!(
        (before.volume_id, before.file_id),
        (after.volume_id, after.file_id),
        "identity survives a rename, which is what makes a move a metadata operation"
    );
}

// ---------------------------------------------------------------- through the executor

/// The production disk, driven by the real executor, the real journal and the real planner — the
/// only seam left faked is the cloud. This is what proves `RealLocalIo` plugs into the thing it
/// was written for, rather than only satisfying its own unit tests.
mod through_the_executor {
    use super::*;
    use chrono::{TimeZone, Utc};
    use matrx_sync::custody::clock::TestClock;
    use matrx_sync::exec::fakes::FakeRemote;
    use matrx_sync::exec::{ExecContext, Executor, NoProgress};
    use matrx_sync::journal::Journal;
    use matrx_sync::model::{Direction, MappingRow};
    use matrx_sync::planner::{plan, PlanContext};
    use matrx_sync::scan::{hash_files, scan_root, ScanOptions};
    use std::collections::BTreeSet;

    const MAPPING: &str = "mapping-real";
    const ORG: &str = "11111111-1111-1111-1111-111111111111";

    fn mapping_row(root: &Path, direction: Direction) -> MappingRow {
        MappingRow {
            id: MAPPING.to_string(),
            organization_id: ORG.to_string(),
            cloud_kind: "org_root".to_string(),
            cloud_folder_id: None,
            local_root: root.display().to_string(),
            local_root_volume_id: "v".to_string(),
            direction,
            desired_state: "active".to_string(),
            state: "pending".to_string(),
            state_detail: None,
            knobs: "{}".to_string(),
            file_cursor: None,
            folder_cursor: None,
            cloud_row_version: None,
            marker_uuid: "marker-real".to_string(),
            created_at: Some("2026-09-21T12:00:00Z".to_string()),
            updated_at: Some("2026-09-21T12:00:00Z".to_string()),
            last_sync_at: None,
            last_full_rescan_at: None,
        }
    }

    fn scan_into(journal: &Journal, root: &Path) {
        let previous = journal.local_tree(MAPPING).expect("previous");
        let opts = ScanOptions::new(0, "2026-09-21T12:00:00Z");
        let mut report = scan_root(root, &previous, &opts);
        report.apply_hashes(hash_files(&report.hash_requests(), 2));
        for path in previous.paths().cloned().collect::<Vec<_>>() {
            if !report.tree.contains(&path) {
                journal.delete_local(MAPPING, &path).expect("prune");
            }
        }
        for (_, node) in report.tree.iter() {
            journal.put_local(MAPPING, node).expect("put local");
        }
    }

    #[tokio::test]
    async fn a_round_trip_over_the_production_disk_converges_and_settles() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path().join("root");
        std::fs::create_dir_all(root.join(".matrx-sync").join("tmp")).expect("root");
        let local = RealLocalIo::new(&root, cfg!(target_os = "macos"));
        let remote = FakeRemote::new();
        let clock = TestClock::new(Utc.with_ymd_and_hms(2026, 9, 21, 12, 0, 0).unwrap());
        let ctx = ExecContext::new(MAPPING, ORG, "this-mac", Direction::TwoWay);

        std::fs::write(root.join("mine.txt"), b"local bytes").expect("seed local");
        remote.put("theirs.txt", "cloud bytes");

        let mut journal = Journal::open(&dir.path().join("syncd.db")).expect("journal");
        journal
            .put_mapping(&mapping_row(&root, Direction::TwoWay))
            .expect("mapping");

        for _ in 0..4 {
            scan_into(&journal, &root);
            for path in ["mine.txt", "theirs.txt"] {
                if let Some(node) = remote.node_for(path) {
                    journal.put_remote(MAPPING, &node).expect("put remote");
                }
            }
            let ctx_plan = PlanContext {
                device_name: "this-mac".to_string(),
                today: "2026-09-21".to_string(),
                recent_deletions: 0,
                window_item_count: None,
                open_conflicts: BTreeSet::new(),
            };
            let p = plan(
                &journal.local_tree(MAPPING).expect("local"),
                &journal.remote_tree(MAPPING).expect("remote"),
                &journal.synced_tree(MAPPING).expect("synced"),
                Direction::TwoWay,
                &Default::default(),
                &ctx_plan,
            );
            let mut exec = Executor::new(
                &mut journal,
                &local,
                &remote,
                &clock,
                &NoProgress,
                ctx.clone(),
            )
            .expect("executor");
            exec.execute(&p).await.expect("execute");
        }

        assert_eq!(
            remote.live("mine.txt").as_deref(),
            Some("local bytes"),
            "the local file did not reach the cloud"
        );
        assert_eq!(
            std::fs::read_to_string(root.join("theirs.txt")).expect("read"),
            "cloud bytes",
            "the cloud file did not reach the disk"
        );
        assert!(
            std::fs::read_dir(root.join(".matrx-sync").join("tmp"))
                .expect("staging")
                .next()
                .is_none(),
            "staging is empty when the work is done"
        );
        assert_eq!(
            journal.mapping(MAPPING).expect("mapping").expect("row").state,
            "idle"
        );
    }
}
