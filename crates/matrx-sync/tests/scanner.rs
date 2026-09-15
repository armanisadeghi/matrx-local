//! FS-L1 unit 1 — the scanner, against a REAL filesystem.
//!
//! These tests create real files in a real tempdir and read them back with real `stat` calls. That
//! is deliberate: the two classes this unit exists to get right — identity surviving a rename, and
//! the fast path refusing to trust `(size, mtime)` — are both properties of an actual filesystem,
//! and a mock of one would be a mock of the answer.

use matrx_sync::model::{LocalNode, LocalTree};
use matrx_sync::scan::{
    scan_root, FastPath, MtimeGranularity, Observed, RehashReason, ScanOptions, SkipReason,
};

const HOUR_NS: i64 = 3_600_000_000_000;

fn now_ns() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("after 1970")
        .as_nanos() as i64
}

/// A scan whose clock is an hour ahead of every file, so nothing is inside the granularity window.
fn opts_settled() -> ScanOptions {
    ScanOptions::new(now_ns() + HOUR_NS, "2026-09-15T00:00:00Z")
}

#[test]
fn a_scan_records_identity_size_and_mtime_for_every_file() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::write(dir.path().join("a.txt"), b"hello").expect("write");
    std::fs::create_dir(dir.path().join("sub")).expect("mkdir");
    std::fs::write(dir.path().join("sub/b.txt"), b"world!").expect("write");

    let report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());

    let a = report.tree.get("a.txt").expect("a.txt is in the tree");
    assert!(!a.is_dir);
    assert_eq!(a.size, Some(5));
    assert!(a.mtime_ns.is_some_and(|m| m > 0), "a real mtime: {a:?}");
    assert!(
        a.volume_id.is_some() && a.file_id.is_some(),
        "identity must be read on this platform, or renames become re-uploads: {a:?}"
    );
    assert_eq!(
        a.content_hash, None,
        "the walk never hashes; I9 says an unhashed row is 'not yet known'"
    );

    let sub = report.tree.get("sub").expect("the directory is in the tree");
    assert!(sub.is_dir && sub.size.is_none());
    assert_eq!(report.tree.get("sub/b.txt").expect("nested file").size, Some(6));

    // Every file needs hashing on a first scan, and says why.
    assert_eq!(report.hash_backlog(), 2);
    assert_eq!(
        report.needs_hash.get("a.txt"),
        Some(&RehashReason::NotSeenBefore)
    );

    // The on-disk path is recorded, because `path_nfc` is not how bytes are found again (I8).
    assert_eq!(
        report.on_disk.get("sub/b.txt").cloned(),
        Some(dir.path().join("sub").join("b.txt"))
    );
}

#[test]
fn a_renamed_file_keeps_its_identity_which_is_what_makes_a_move_a_move() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::write(dir.path().join("before.txt"), b"same bytes").expect("write");

    let first = scan_root(dir.path(), &LocalTree::new(), &opts_settled());
    let before = first.tree.get("before.txt").cloned().expect("scanned");

    std::fs::rename(dir.path().join("before.txt"), dir.path().join("after.txt")).expect("rename");
    let second = scan_root(dir.path(), &first.tree, &opts_settled());
    let after = second.tree.get("after.txt").cloned().expect("scanned");

    assert_eq!(
        (&before.volume_id, &before.file_id),
        (&after.volume_id, &after.file_id),
        "a rename is a metadata operation: the inode does not change, and that identity is the \
         only thing that lets the planner call it a move instead of a delete plus a re-upload"
    );
    assert!(second.tree.get("before.txt").is_none());
}

#[test]
fn the_fast_path_carries_a_hash_forward_only_when_nothing_could_have_changed() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::write(dir.path().join("stable.txt"), b"unchanged").expect("write");

    // A first scan, then pretend it hashed the file.
    let opts = opts_settled();
    let first = scan_root(dir.path(), &LocalTree::new(), &opts);
    let mut hashed = first.tree.clone();
    let mut node = hashed.get("stable.txt").cloned().expect("scanned");
    node.content_hash = Some("sha-of-unchanged".to_string());
    hashed.insert("stable.txt".to_string(), node);

    // Nothing touched it: the hash is reused and the file is not in the backlog.
    let second = scan_root(dir.path(), &hashed, &opts_settled());
    assert_eq!(
        second.tree.get("stable.txt").expect("scanned").content_hash,
        Some("sha-of-unchanged".to_string())
    );
    assert_eq!(second.hash_backlog(), 0);

    // Rewriting it with DIFFERENT bytes changes the size, which is the unambiguous signal.
    std::fs::write(dir.path().join("stable.txt"), b"changed!!!!!").expect("rewrite");
    let third = scan_root(dir.path(), &hashed, &opts_settled());
    assert_eq!(
        third.tree.get("stable.txt").expect("scanned").content_hash,
        None
    );
    assert_eq!(
        third.needs_hash.get("stable.txt"),
        Some(&RehashReason::SizeChanged)
    );
}

#[test]
fn a_same_size_same_mtime_replacement_is_still_rehashed() {
    // The atomic-save shape: an editor writes a temp file and renames it over the original. Size
    // and mtime can match exactly; the inode cannot. Trusting (size, mtime) here loses the edit.
    let dir = tempfile::tempdir().expect("tempdir");
    let target = dir.path().join("doc.txt");
    std::fs::write(&target, b"aaaa").expect("write");

    let opts = opts_settled();
    let first = scan_root(dir.path(), &LocalTree::new(), &opts);
    let mut hashed = first.tree.clone();
    let mut node = hashed.get("doc.txt").cloned().expect("scanned");
    let original_mtime = node.mtime_ns;
    node.content_hash = Some("sha-of-aaaa".to_string());
    hashed.insert("doc.txt".to_string(), node);

    // Replace through a rename, then restore the original mtime so ONLY the inode differs.
    let temp = dir.path().join("doc.txt.tmp");
    std::fs::write(&temp, b"bbbb").expect("write temp");
    std::fs::rename(&temp, &target).expect("atomic replace");
    let replaced = scan_root(dir.path(), &LocalTree::new(), &opts).tree;
    let replaced = replaced.get("doc.txt").cloned().expect("scanned");
    assert_eq!(replaced.size, Some(4), "the same size, as an atomic save often is");

    // Feed the decision the original mtime alongside the new identity — the exact ambiguity.
    let previous = hashed.get("doc.txt").cloned().expect("previous");
    let verdict = matrx_sync::scan::fastpath::decide(
        Some(&previous),
        &Observed {
            size: 4,
            mtime_ns: original_mtime.expect("an mtime"),
            identity: matrx_sync::scan::FileIdentity {
                volume_id: replaced.volume_id.clone(),
                file_id: replaced.file_id.clone(),
            },
        },
        opts.scan_started_ns,
        MtimeGranularity::MODERN,
    );
    assert_eq!(
        verdict,
        FastPath::Rehash(RehashReason::IdentityChanged),
        "same path, same size, same mtime, different inode — something replaced the file"
    );
}

#[test]
fn a_file_written_inside_the_volumes_timestamp_granularity_is_never_trusted() {
    // FAT and exFAT round mtimes to two seconds, so a write in the same tick is invisible. The
    // refusal is what makes "any doubt hashes" true rather than aspirational.
    let previous = LocalNode {
        path_nfc: "x.txt".to_string(),
        is_dir: false,
        size: Some(10),
        mtime_ns: Some(1_000_000_000_000),
        volume_id: Some("vol".to_string()),
        file_id: Some("ino".to_string()),
        content_hash: Some("sha".to_string()),
        scanned_at: None,
    };
    let observed = Observed {
        size: 10,
        mtime_ns: 1_000_000_000_000,
        identity: matrx_sync::scan::FileIdentity {
            volume_id: Some("vol".to_string()),
            file_id: Some("ino".to_string()),
        },
    };

    // One second after the write, on a FAT volume: inside the two-second granularity.
    assert_eq!(
        matrx_sync::scan::fastpath::decide(
            Some(&previous),
            &observed,
            1_000_000_000_000 + 1_000_000_000,
            MtimeGranularity::FAT
        ),
        FastPath::Rehash(RehashReason::MtimeWithinGranularity)
    );
    // Three seconds after, the same volume can vouch for it.
    assert_eq!(
        matrx_sync::scan::fastpath::decide(
            Some(&previous),
            &observed,
            1_000_000_000_000 + 3_000_000_000,
            MtimeGranularity::FAT
        ),
        FastPath::Reuse("sha".to_string())
    );
    // A file whose mtime is in the future relative to the scan: the clock moved.
    assert_eq!(
        matrx_sync::scan::fastpath::decide(
            Some(&previous),
            &observed,
            1_000_000_000_000 - 1,
            MtimeGranularity::MODERN
        ),
        FastPath::Rehash(RehashReason::ClockWentBackwards)
    );
}

#[test]
fn symlinks_are_skipped_and_reported_never_followed() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::write(dir.path().join("real.txt"), b"real").expect("write");
    #[cfg(unix)]
    std::os::unix::fs::symlink(dir.path().join("real.txt"), dir.path().join("link.txt"))
        .expect("symlink");
    #[cfg(windows)]
    std::os::windows::fs::symlink_file(dir.path().join("real.txt"), dir.path().join("link.txt"))
        .expect("symlink");

    let report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());

    assert!(
        report.tree.get("link.txt").is_none(),
        "a symlink is not a file to sync"
    );
    let skipped = report
        .skipped
        .iter()
        .find(|s| s.path == "link.txt")
        .expect("and it must be REPORTED — silence is a file the user thinks is syncing");
    assert_eq!(skipped.reason, SkipReason::Symlink);
    assert!(skipped.detail.is_some(), "the report names the target");
    assert!(report.tree.get("real.txt").is_some());
}

#[test]
fn a_symlinked_directory_is_not_descended_into() {
    // The loop that eats a home directory: a link back to an ancestor.
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::create_dir(dir.path().join("real")).expect("mkdir");
    std::fs::write(dir.path().join("real/inside.txt"), b"x").expect("write");
    #[cfg(unix)]
    std::os::unix::fs::symlink(dir.path(), dir.path().join("real/loop")).expect("symlink");
    #[cfg(windows)]
    std::os::windows::fs::symlink_dir(dir.path(), dir.path().join("real/loop")).expect("symlink");

    let report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());

    assert!(report.tree.get("real/inside.txt").is_some());
    assert!(
        report.tree.paths().all(|p| !p.contains("loop/")),
        "the walk followed a symlink into a loop: {:?}",
        report.tree.paths().collect::<Vec<_>>()
    );
    assert!(report.skipped.iter().any(|s| s.path == "real/loop"));
}

#[test]
fn the_mappings_own_state_directory_is_never_scanned() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::create_dir(dir.path().join(".matrx-sync")).expect("mkdir");
    std::fs::write(dir.path().join(".matrx-sync/marker"), b"uuid").expect("write");
    std::fs::create_dir(dir.path().join(".matrx-sync/tmp")).expect("mkdir");
    std::fs::write(dir.path().join(".matrx-sync/tmp/part"), b"half a download").expect("write");
    std::fs::write(dir.path().join("user.txt"), b"mine").expect("write");

    let report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());

    assert!(report.tree.get("user.txt").is_some());
    assert!(
        report.tree.paths().all(|p| !p.starts_with(".matrx-sync")),
        "the mapping's own bookkeeping — marker, download staging, compiled ignores — is never \
         synced: {:?}",
        report.tree.paths().collect::<Vec<_>>()
    );
}

#[test]
fn an_unreadable_directory_is_reported_rather_than_silently_empty() {
    // A TCC denial or a mode-000 directory must not look like "the folder is empty", which is the
    // difference between a visible state and a mass delete.
    let dir = tempfile::tempdir().expect("tempdir");
    let locked = dir.path().join("locked");
    std::fs::create_dir(&locked).expect("mkdir");
    std::fs::write(locked.join("secret.txt"), b"x").expect("write");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o000)).expect("chmod");
    }

    let report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());

    #[cfg(unix)]
    {
        assert!(
            report.tree.get("locked/secret.txt").is_none(),
            "we cannot read it, so we do not claim to know it"
        );
        assert!(
            report.unreadable_dirs.iter().any(|s| s.path == "locked"),
            "and the scan says so, with the OS's own words: {:?}",
            report.unreadable_dirs
        );
        // Leave the tempdir removable.
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    }
    assert!(report.tree.get("locked").is_some(), "the directory itself is known");
}

#[test]
fn two_scans_of_an_unchanged_tree_are_identical() {
    let dir = tempfile::tempdir().expect("tempdir");
    for i in 0..20 {
        std::fs::create_dir_all(dir.path().join(format!("d{}", i % 4))).expect("mkdir");
        std::fs::write(dir.path().join(format!("d{}/f{i}.txt", i % 4)), format!("body {i}"))
            .expect("write");
    }
    let opts = opts_settled();
    let a = scan_root(dir.path(), &LocalTree::new(), &opts);
    let b = scan_root(dir.path(), &LocalTree::new(), &opts);
    assert_eq!(a.tree, b.tree, "the walk must be deterministic to be diffable");
    assert_eq!(a.on_disk, b.on_disk);
    assert_eq!(a.tree.len(), 24, "20 files and 4 directories");
}

// ------------------------------------------------- unit 1b: hashing, NFC, the marker

#[test]
fn hashing_produces_the_servers_sha256_and_folds_back_into_the_tree() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::write(dir.path().join("a.txt"), b"abc").expect("write");

    let mut report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());
    assert_eq!(report.hash_backlog(), 1);

    let outcomes = matrx_sync::scan::hash_files(&report.hash_requests(), 4);
    report.apply_hashes(outcomes);

    assert_eq!(
        report.tree.get("a.txt").expect("scanned").content_hash.as_deref(),
        // The canonical SHA-256 of "abc" — if this ever changes, we are not speaking the server's
        // language any more, and every checksum comparison against the cloud is broken.
        Some("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    );
    assert_eq!(report.hash_backlog(), 0);
    assert!(report.failed_hashes.is_empty());
}

#[test]
fn hashing_is_streamed_and_order_stable_under_concurrency() {
    let dir = tempfile::tempdir().expect("tempdir");
    // Bigger than the 1 MiB read buffer, so the streaming path is actually exercised rather than
    // one lucky read.
    let big = vec![b'x'; 3 * 1024 * 1024 + 7];
    std::fs::write(dir.path().join("big.bin"), &big).expect("write");
    for i in 0..25 {
        std::fs::write(dir.path().join(format!("f{i:02}.txt")), format!("body {i}")).expect("write");
    }

    let report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());
    let requests = report.hash_requests();
    assert_eq!(requests.len(), 26);

    let one = matrx_sync::scan::hash_files(&requests, 1);
    let many = matrx_sync::scan::hash_files(&requests, 8);
    assert_eq!(
        one, many,
        "results must come back in request order whatever order the threads finished in, or two \
         scans of one tree are not comparable"
    );
    let paths: Vec<&str> = many.iter().map(|o| o.path_nfc.as_str()).collect();
    let mut sorted = paths.clone();
    sorted.sort_unstable();
    assert_eq!(paths, sorted);

    // The big file hashes to the same value as a single-shot hash of its bytes.
    let expected = {
        use sha2::{Digest, Sha256};
        let mut h = Sha256::new();
        h.update(&big);
        format!("{:x}", h.finalize())
    };
    let got = many
        .iter()
        .find(|o| o.path_nfc == "big.bin")
        .and_then(|o| o.result.clone().ok());
    assert_eq!(got.as_deref(), Some(expected.as_str()));
}

#[test]
fn the_default_hash_concurrency_leaves_the_machine_usable() {
    let n = matrx_sync::scan::hash_concurrency_default();
    assert!(n >= 2, "never below two, or a single-core VM cannot overlap IO");
    let cores = std::thread::available_parallelism().map(|c| c.get()).unwrap_or(2);
    assert!(
        n <= cores.max(2),
        "half the cores, so a first sync of a large tree does not make the machine feel broken"
    );
}

#[test]
fn a_file_that_vanishes_between_the_walk_and_the_hash_leaves_the_tree() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::write(dir.path().join("fleeting.txt"), b"here for now").expect("write");
    let mut report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());

    std::fs::remove_file(dir.path().join("fleeting.txt")).expect("remove");
    let outcomes = matrx_sync::scan::hash_files(&report.hash_requests(), 2);
    assert!(matches!(
        outcomes[0].result,
        Err(matrx_sync::scan::HashError::Vanished)
    ));
    report.apply_hashes(outcomes);

    assert!(
        report.tree.get("fleeting.txt").is_none(),
        "it is not 'not yet known', it is gone — leaving it would have the planner ask for its \
         hash forever"
    );
    assert_eq!(report.hash_backlog(), 0);
    assert!(report.failed_hashes.is_empty(), "a vanished file is not a failure");
}

#[test]
fn paths_are_nfc_whatever_the_filesystem_hands_back() {
    let dir = tempfile::tempdir().expect("tempdir");
    // "café" written DECOMPOSED (e + U+0301), the spelling macOS hands out.
    let nfd = "cafe\u{301}.txt";
    let nfc = "caf\u{e9}.txt";
    std::fs::write(dir.path().join(nfd), b"x").expect("write");

    let report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());

    assert!(
        report.tree.get(nfc).is_some(),
        "the journal is NFC everywhere (I8); the cloud stores NFC: {:?}",
        report.tree.paths().collect::<Vec<_>>()
    );
    // And the REAL name is kept, because bytes are never found through `path_nfc`.
    let on_disk = report.on_disk.get(nfc).expect("the real path is recorded");
    assert!(on_disk.exists(), "the recorded path must actually open: {on_disk:?}");
}

#[test]
fn two_names_that_normalise_to_one_key_are_reported_not_silently_dropped() {
    let dir = tempfile::tempdir().expect("tempdir");
    let nfd = dir.path().join("cafe\u{301}.txt");
    let nfc = dir.path().join("caf\u{e9}.txt");
    std::fs::write(&nfd, b"decomposed").expect("write");
    if std::fs::write(&nfc, b"precomposed").is_err() || !both_exist(&nfd, &nfc) {
        // APFS is normalisation-INSENSITIVE: the second write lands on the first file, so this
        // class cannot be produced here. It is produced on ext4 and NTFS, which are
        // normalisation-preserving, and that is where this assertion matters.
        return;
    }

    let report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());

    assert_eq!(report.tree.len(), 1, "one key can hold one node");
    assert_eq!(
        report.normalisation_collisions.len(),
        1,
        "and the other must be REPORTED — a file that disappears from the scan with nothing said \
         is exactly the silence I8 forbids"
    );
}

fn both_exist(a: &std::path::Path, b: &std::path::Path) -> bool {
    let (Ok(ma), Ok(mb)) = (std::fs::metadata(a), std::fs::metadata(b)) else {
        return false;
    };
    ma.len() != mb.len()
}

#[test]
fn the_marker_tells_an_unmounted_drive_from_an_emptied_folder() {
    let dir = tempfile::tempdir().expect("tempdir");
    let uuid = "11111111-2222-3333-4444-555555555555";

    // Before admission there is no marker: an empty directory is NOT this mapping's folder.
    assert_eq!(
        matrx_sync::scan::check_marker(dir.path(), uuid),
        matrx_sync::scan::MarkerState::Missing
    );
    assert_eq!(
        matrx_sync::scan::check_marker(dir.path(), uuid).suspension_state(),
        Some("suspended_marker_missing"),
        "an unmounted volume must suspend the mapping, never empty the cloud"
    );

    matrx_sync::scan::write_marker(dir.path(), uuid).expect("write marker");
    assert!(matrx_sync::scan::check_marker(dir.path(), uuid).is_present());
    assert!(
        dir.path().join(".matrx-sync/tmp").is_dir(),
        "the staging directory is created with it: downloads land there before they are verified"
    );

    // A folder carrying ANOTHER mapping's marker — copied from another machine, or two mappings
    // pointed at one folder — is not this mapping's either, and says which.
    let foreign = matrx_sync::scan::check_marker(dir.path(), "99999999-9999-9999-9999-999999999999");
    match foreign {
        matrx_sync::scan::MarkerState::Foreign { ref found } => assert_eq!(found, uuid),
        other => panic!("expected Foreign, got {other:?}"),
    }
    assert_eq!(foreign.suspension_state(), Some("suspended_marker_missing"));

    // And the marker never syncs: it lives in the directory the scan skips.
    let report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());
    assert!(report.tree.paths().all(|p| !p.starts_with(".matrx-sync")));
}

// ---------------------------------- unit 1c: the symlink knob, and locked-file deferral

#[test]
fn following_symlinks_is_opt_in_and_still_cannot_loop() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::create_dir(dir.path().join("real")).expect("mkdir");
    std::fs::write(dir.path().join("real/inside.txt"), b"x").expect("write");
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(dir.path().join("real"), dir.path().join("linked")).expect("ln");
        // And a link back to the root, the shape that eats a disk.
        std::os::unix::fs::symlink(dir.path(), dir.path().join("real/up")).expect("ln");
    }
    #[cfg(not(unix))]
    return;

    let mut opts = opts_settled();
    opts.follow_symlinks = true;
    let report = scan_root(dir.path(), &LocalTree::new(), &opts);

    assert!(
        report.tree.get("linked/inside.txt").is_some(),
        "with the knob on, the link's target is synced: {:?}",
        report.tree.paths().collect::<Vec<_>>()
    );
    assert!(
        report.tree.len() < 50,
        "the walk must terminate, not follow the cycle until the disk fills: {} entries",
        report.tree.len()
    );
    assert!(
        report
            .skipped
            .iter()
            .any(|s| s.reason == matrx_sync::scan::SkipReason::DirectoryCycle),
        "and the cycle is reported by identity, not guessed at from names: {:?}",
        report.skipped
    );
}

#[test]
fn a_locked_file_backs_off_and_eventually_gives_up_with_a_remedy() {
    use matrx_sync::scan::{next_attempt, Deferral, RetryPolicy, RetryVerdict};
    let policy = RetryPolicy::default();
    assert_eq!((policy.base_s, policy.max_s, policy.giveup_h), (5, 900, 24));

    let start = 1_700_000_000_i64;
    let mut deferral = Deferral::first("report.docx", start, "os error 32");

    // The first retry is soon: a file locked for a second should be picked up almost at once.
    assert_eq!(next_attempt(&policy, &deferral, start), RetryVerdict::RetryAt(start + 5));

    // The wait doubles and is capped, so an afternoon-long lock is not hammered.
    let mut waits = Vec::new();
    for _ in 0..12 {
        deferral.failed_again("os error 32");
        if let RetryVerdict::RetryAt(at) = next_attempt(&policy, &deferral, start) {
            waits.push(at - start);
        }
    }
    assert!(waits.windows(2).all(|w| w[1] >= w[0]), "monotonic: {waits:?}");
    assert_eq!(*waits.last().expect("some waits"), 900, "capped at max_s");

    // The give-up clock runs from the FIRST failure, not the last, so a file that keeps failing
    // does stop rather than resetting its own deadline every time.
    assert_eq!(
        next_attempt(&policy, &deferral, start + 24 * 3600 - 1),
        RetryVerdict::RetryAt(start + 24 * 3600 - 1 + 900)
    );
    assert_eq!(
        next_attempt(&policy, &deferral, start + 24 * 3600),
        RetryVerdict::GiveUp
    );

    // And giving up is a state with a remedy, not a silence.
    let remedy = matrx_sync::scan::giveup_remedy(&deferral.path_nfc, &deferral.last_error);
    assert!(remedy.contains("report.docx") && remedy.to_lowercase().contains("close the app"));
}

#[test]
fn a_file_the_os_refuses_is_deferred_with_its_reason_not_hashed_as_empty() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("private.txt");
    std::fs::write(&path, b"secret").expect("write");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o000)).expect("chmod");
    }

    // Probe rather than guess: root, and some filesystems, read a mode-000 file happily, and then
    // the assertions below would be testing nothing.
    #[cfg(unix)]
    let enforced = std::fs::File::open(&path).is_err();
    #[cfg(not(unix))]
    let enforced = false;

    let mut report = scan_root(dir.path(), &LocalTree::new(), &opts_settled());
    let outcomes = matrx_sync::scan::hash_files(&report.hash_requests(), 2);
    report.apply_hashes(outcomes);

    #[cfg(unix)]
    if enforced {
        let node = report.tree.get("private.txt").expect("still known to exist");
        assert_eq!(
            node.content_hash, None,
            "an unreadable file is 'not yet known' — never hashed as if it were empty, which \
             would make every unreadable file look like the same file"
        );
        assert!(
            report
                .failed_hashes
                .iter()
                .any(|(p, e)| p == "private.txt" && matches!(e, matrx_sync::scan::HashError::Locked(_))),
            "and it is recorded with the OS's own words: {:?}",
            report.failed_hashes
        );
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).expect("chmod");
    }
}

