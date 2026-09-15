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
