//! FS-C2 unit tests: migrations, and the synced-tree write guard (invariant I1).
//!
//! No real filesystem beyond a tempdir for the journal file, and no network.

use matrx_sync::journal::{Journal, LocalConfirmation, NewOp, RemoteConfirmation};
use matrx_sync::model::{Direction, LocalNode, MappingRow, OpKind, OpState, RemoteNode};
use matrx_sync::SyncError;

const MAPPING: &str = "11111111-1111-1111-1111-111111111111";

fn mapping_row() -> MappingRow {
    MappingRow {
        id: MAPPING.to_string(),
        organization_id: "22222222-2222-2222-2222-222222222222".to_string(),
        cloud_kind: "org_root".to_string(),
        cloud_folder_id: None,
        local_root: "/Users/someone/Matrx".to_string(),
        local_root_volume_id: "vol-1".to_string(),
        direction: Direction::TwoWay,
        desired_state: "active".to_string(),
        state: "idle".to_string(),
        state_detail: None,
        knobs: "{}".to_string(),
        file_cursor: None,
        folder_cursor: None,
        cloud_row_version: None,
        marker_uuid: "33333333-3333-3333-3333-333333333333".to_string(),
        created_at: Some("2026-09-13T00:00:00Z".to_string()),
        updated_at: Some("2026-09-13T00:00:00Z".to_string()),
        last_sync_at: None,
        last_full_rescan_at: None,
    }
}

fn upload_op() -> NewOp {
    NewOp {
        mapping_id: MAPPING.to_string(),
        seq: 1,
        kind: OpKind::UploadCreate,
        path_nfc: "notes/a.md".to_string(),
        target_path_nfc: None,
        remote_file_id: None,
        expected_checksum: None,
        expected_version: None,
        expected_local_hash: None,
        idempotency_key: "key-upload-a".to_string(),
        created_at: "2026-09-13T00:00:00Z".to_string(),
    }
}

fn local_confirmation() -> LocalConfirmation {
    LocalConfirmation {
        is_dir: false,
        size: Some(12),
        mtime_ns: Some(1_700_000_000_000_000_000),
        volume_id: Some("vol-1".to_string()),
        file_id: Some("inode-9".to_string()),
        content_hash: Some("a".repeat(64)),
        local_edit_flagged: false,
    }
}

fn remote_confirmation() -> RemoteConfirmation {
    RemoteConfirmation {
        remote_file_id: "file-1".to_string(),
        remote_version: 7,
        checksum: Some("a".repeat(64)),
    }
}

// ------------------------------------------------------------------ migrations

#[test]
fn a_fresh_journal_migrates_to_the_binarys_max_version() {
    let j = Journal::open_in_memory().expect("open");
    assert_eq!(j.schema_version().expect("version"), matrx_sync::journal::max_version());
    assert!(matrx_sync::journal::max_version() >= 1);
}

#[test]
fn migrations_are_idempotent_across_reopens_of_the_same_file() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("syncd.db");

    let first = Journal::open(&path).expect("first open");
    let v1 = first.schema_version().expect("version");
    first.put_mapping(&mapping_row()).expect("write a mapping");
    drop(first);

    let second = Journal::open(&path).expect("second open");
    assert_eq!(second.schema_version().expect("version"), v1);
    assert!(
        second.mapping(MAPPING).expect("read").is_some(),
        "reopening must not re-run 001 and wipe the data"
    );

    // Exactly one schema_version row per migration, never one per open.
    let rows: i64 = second
        .connection()
        .query_row("SELECT count(*) FROM schema_version", [], |r| r.get(0))
        .expect("count");
    assert_eq!(rows, matrx_sync::journal::max_version());
}

#[test]
fn every_migration_declares_a_strictly_increasing_version() {
    let mut previous = 0;
    for m in matrx_sync::journal::MIGRATIONS {
        assert!(
            m.version > previous,
            "migration {} does not increase the version",
            m.name
        );
        assert!(!m.sql.trim().is_empty(), "{} is empty", m.name);
        previous = m.version;
    }
}

#[test]
fn a_journal_from_a_newer_binary_is_refused_never_downgraded() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("syncd.db");
    {
        let j = Journal::open(&path).expect("open");
        j.connection()
            .execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?1, '2099-01-01T00:00:00Z')",
                [matrx_sync::journal::max_version() + 5],
            )
            .expect("plant a future version");
    }
    match Journal::open(&path) {
        Err(SyncError::JournalNewerThanBinary { found, supported }) => {
            assert_eq!(found, supported + 5);
        }
        other => panic!("expected JournalNewerThanBinary, got {other:?}"),
    }
}

// ------------------------------------------------ the synced-tree write guard (I1)

#[test]
fn there_is_no_synced_row_until_an_op_is_confirmed() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let id = j.enqueue_op(&upload_op()).expect("enqueue");

    // Enqueued is not confirmed.
    assert!(j.synced_tree(MAPPING).expect("tree").is_empty());

    // Neither is a plan, a 2xx, or anything short of a lease this caller holds.
    let err = j
        .confirm_op(
            id,
            "executor-1",
            &local_confirmation(),
            &remote_confirmation(),
            "2026-09-13T00:00:05Z",
        )
        .expect_err("an un-leased op may not write the synced tree");
    assert!(
        matches!(err, SyncError::SyncedWriteRefused(_)),
        "got {err:?}"
    );
    assert!(j.synced_tree(MAPPING).expect("tree").is_empty());
}

#[test]
fn confirming_a_leased_op_writes_the_synced_row_and_marks_it_done() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let id = j.enqueue_op(&upload_op()).expect("enqueue");
    let leased = j
        .lease_next_op(MAPPING, "executor-1", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("an op was ready");
    assert_eq!(leased.id, id);
    assert_eq!(leased.state, OpState::Leased);

    j.confirm_op(
        id,
        "executor-1",
        &local_confirmation(),
        &remote_confirmation(),
        "2026-09-13T00:00:05Z",
    )
    .expect("both sides confirmed");

    let synced = j.synced_tree(MAPPING).expect("tree");
    let node = synced.get("notes/a.md").expect("the synced row exists");
    assert_eq!(node.content_hash.as_deref(), Some("a".repeat(64).as_str()));
    assert_eq!(node.checksum.as_deref(), Some("a".repeat(64).as_str()));
    assert_eq!(node.remote_version, 7);
    assert_eq!(
        j.op(id).expect("op").expect("exists").state,
        OpState::Done,
        "the synced write and the done transition are one transaction"
    );
}

#[test]
fn a_different_owner_cannot_confirm_someone_elses_lease() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let id = j.enqueue_op(&upload_op()).expect("enqueue");
    j.lease_next_op(MAPPING, "executor-1", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");

    let err = j
        .confirm_op(
            id,
            "executor-2",
            &local_confirmation(),
            &remote_confirmation(),
            "2026-09-13T00:00:05Z",
        )
        .expect_err("a foreign owner may not confirm");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");
    assert!(j.synced_tree(MAPPING).expect("tree").is_empty());
}

#[test]
fn a_file_confirmation_without_a_local_hash_is_refused_i2() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let id = j.enqueue_op(&upload_op()).expect("enqueue");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");

    let mut local = local_confirmation();
    local.content_hash = None;
    let err = j
        .confirm_op(id, "x", &local, &remote_confirmation(), "2026-09-13T00:00:05Z")
        .expect_err("no invented hashes");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");

    let mut remote = remote_confirmation();
    remote.checksum = None;
    let err = j
        .confirm_op(id, "x", &local_confirmation(), &remote, "2026-09-13T00:00:05Z")
        .expect_err("no invented checksums");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");
    assert!(j.synced_tree(MAPPING).expect("tree").is_empty());
}

#[test]
fn the_table_check_refuses_an_optimistic_file_row_even_from_raw_sql() {
    // I2 is enforced twice: once by confirm_op, and once by the schema, so a future code path
    // that reached for the connection could still not express an optimistic write.
    let j = Journal::open_in_memory().expect("open");
    let err = j.connection().execute(
        "INSERT INTO tree_synced (mapping_id, path_nfc, is_dir, remote_file_id, remote_version,
                                  synced_at)
         VALUES ('m', 'p', 0, 'f', 1, '2026-09-13T00:00:00Z')",
        [],
    );
    assert!(err.is_err(), "the CHECK must refuse a file row with no hashes");
}

#[test]
fn a_half_confirmed_delete_leaves_the_synced_row_standing() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let id = j.enqueue_op(&upload_op()).expect("enqueue");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");
    j.confirm_op(
        id,
        "x",
        &local_confirmation(),
        &remote_confirmation(),
        "2026-09-13T00:00:05Z",
    )
    .expect("confirm");

    let mut delete = upload_op();
    delete.kind = OpKind::DeleteRemote;
    delete.seq = 2;
    delete.idempotency_key = "key-delete-a".to_string();
    let del_id = j.enqueue_op(&delete).expect("enqueue delete");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:01:00Z", "2026-09-13T00:16:00Z")
        .expect("lease")
        .expect("ready");

    let err = j
        .confirm_delete_op(del_id, "x", true, false, "2026-09-13T00:01:05Z")
        .expect_err("one side is not both sides");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");
    assert!(j.synced_tree(MAPPING).expect("tree").get("notes/a.md").is_some());

    j.confirm_delete_op(del_id, "x", true, true, "2026-09-13T00:01:06Z")
        .expect("both sides confirmed");
    assert!(j.synced_tree(MAPPING).expect("tree").get("notes/a.md").is_none());
}

// ------------------------------------------------------ idempotency and queues

#[test]
fn enqueueing_the_same_idempotency_key_twice_is_one_op_i5() {
    let j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let a = j.enqueue_op(&upload_op()).expect("first");
    let b = j.enqueue_op(&upload_op()).expect("replay after a crash");
    assert_eq!(a, b, "a replayed plan re-finds the row, it does not duplicate it");
    assert_eq!(j.ops_in_state(MAPPING, OpState::Ready).expect("ready").len(), 1);
}

#[test]
fn a_leased_op_blocks_only_its_own_mappings_queue_i7() {
    let j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let mut other_mapping = mapping_row();
    other_mapping.id = "44444444-4444-4444-4444-444444444444".to_string();
    j.put_mapping(&other_mapping).expect("second mapping");

    j.enqueue_op(&upload_op()).expect("first queue");
    let mut other = upload_op();
    other.mapping_id = other_mapping.id.clone();
    other.idempotency_key = "key-upload-other".to_string();
    j.enqueue_op(&other).expect("second queue");

    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");

    let second = j
        .lease_next_op(&other_mapping.id, "y", "2026-09-13T00:00:02Z", "2026-09-13T00:15:02Z")
        .expect("lease")
        .expect("the other mapping is untouched");
    assert_eq!(second.mapping_id, other_mapping.id);
}

// ---------------------------------------------------------- trees round-trip

#[test]
fn local_and_remote_rows_round_trip_through_their_typed_structs() {
    let j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");

    let local = LocalNode {
        path_nfc: "notes/a.md".to_string(),
        is_dir: false,
        size: Some(12),
        mtime_ns: Some(5),
        volume_id: Some("vol-1".to_string()),
        file_id: Some("inode-9".to_string()),
        content_hash: None, // "not yet known", never "unchanged" (I9)
        scanned_at: Some("2026-09-13T00:00:00Z".to_string()),
    };
    j.put_local(MAPPING, &local).expect("write local");
    assert_eq!(j.local_tree(MAPPING).expect("tree").get("notes/a.md"), Some(&local));

    let remote = RemoteNode {
        path_nfc: "notes/a.md".to_string(),
        is_dir: false,
        size: Some(12),
        remote_file_id: Some("file-1".to_string()),
        remote_folder_id: Some("folder-1".to_string()),
        remote_version: Some(7),
        checksum: Some("b".repeat(64)),
        client_modified_at: Some("2026-09-13T00:00:00Z".to_string()),
        origin_device_id: Some("55555555-5555-5555-5555-555555555555".to_string()),
        deleted_at: None,
        seen_at: Some("2026-09-13T00:00:01Z".to_string()),
    };
    j.put_remote(MAPPING, &remote).expect("write remote");
    let back = j.remote_tree(MAPPING).expect("tree");
    assert_eq!(back.get("notes/a.md"), Some(&remote));
    assert!(back.get("notes/a.md").expect("row").is_live());
}

/// Enqueue and lease one `preserve_local_edit` op, returning its id.
fn leased_preserve_op(j: &mut Journal, key: &str) -> i64 {
    let mut op = upload_op();
    op.kind = OpKind::PreserveLocalEdit;
    op.seq = 9;
    op.idempotency_key = key.to_string();
    let id = j.enqueue_op(&op).expect("enqueue");
    j.connection()
        .execute(
            "UPDATE ops SET state='leased', lease_owner='x' WHERE id = ?1",
            [id],
        )
        .expect("lease");
    id
}

#[test]
fn a_synced_row_may_not_be_assembled_from_two_different_moments() {
    // The guard that closed the FS-C4 harness's second data-loss finding: an executor that read
    // the local hash and the server checksum at two different moments recorded a row that was
    // never true, and the next plan read it as "local unchanged, remote changed" and downloaded
    // over the user's file.
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let id = j.enqueue_op(&upload_op()).expect("enqueue");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");

    let mut remote = remote_confirmation();
    remote.checksum = Some("b".repeat(64)); // a different file than the local confirmation's
    let err = j
        .confirm_op(id, "x", &local_confirmation(), &remote, "2026-09-13T00:00:05Z")
        .expect_err("two moments are not one confirmation");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");
    assert!(j.synced_tree(MAPPING).expect("tree").is_empty());

    // The same two hashes ARE allowed on a preserved local edit — that is exactly what D6's flag
    // means — and that path is an op of kind `preserve_local_edit`, never `confirm_op`.
    let preserve = leased_preserve_op(&mut j, "key-preserve");
    let mut local = local_confirmation();
    local.local_edit_flagged = true;
    j.preserve_local_edit(preserve, "x", &local, &remote, "2026-09-13T00:00:06Z")
        .expect("a preserved local edit may differ from the cloud");
    let row = j
        .synced_tree(MAPPING)
        .expect("tree")
        .get("notes/a.md")
        .cloned()
        .expect("row");
    assert!(row.local_edit_flagged);
    assert_ne!(row.content_hash, row.checksum);
}

/// F2, from independent verification: the flag was a skeleton key. Setting
/// `local_edit_flagged: true` on an ORDINARY op walked past the hash-≠-checksum refusal and wrote
/// a row that was never true. `confirm_op` now refuses the flag outright; the two doors are
/// disjoint.
#[test]
fn the_local_edit_flag_is_not_a_skeleton_key_through_confirm_op() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let id = j.enqueue_op(&upload_op()).expect("enqueue");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");

    let mut local = local_confirmation();
    local.local_edit_flagged = true;
    let mut remote = remote_confirmation();
    remote.checksum = Some("b".repeat(64));

    let err = j
        .confirm_op(id, "x", &local, &remote, "2026-09-13T00:00:05Z")
        .expect_err("the flag must not open confirm_op's door");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");
    assert!(
        j.synced_tree(MAPPING).expect("tree").is_empty(),
        "no row may be written through the flag"
    );

    // Even with matching hashes, the flag is refused here: a flagged row has ONE door.
    let err = j
        .confirm_op(id, "x", &local, &remote_confirmation(), "2026-09-13T00:00:05Z")
        .expect_err("still refused");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");
}

/// A flagged row is written only on an op that declares itself one (amendment 1).
#[test]
fn preserve_local_edit_requires_its_own_op_kind() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let wrong = j.enqueue_op(&upload_op()).expect("enqueue");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");
    let mut local = local_confirmation();
    local.local_edit_flagged = true;
    let mut remote = remote_confirmation();
    remote.checksum = Some("b".repeat(64));
    let err = j
        .preserve_local_edit(wrong, "x", &local, &remote, "t")
        .expect_err("an upload op may not become a flagged row");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");
    assert!(j.synced_tree(MAPPING).expect("tree").is_empty());
}

/// F3, from independent verification: raw SQL through `connection()` fabricated a `tree_synced`
/// row — no op, no lease, no hash anybody computed. I1 is now enforced by the database.
#[test]
fn raw_sql_cannot_fabricate_a_synced_row() {
    let j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let fabricate = j.connection().execute(
        "INSERT INTO tree_synced (mapping_id, path_nfc, is_dir, content_hash, remote_file_id,
                                  remote_version, checksum, synced_at)
         VALUES (?1, 'fabricated.txt', 0, 'INVENTED', 'file-x', 7, 'ALSO_INVENTED', 't')",
        [MAPPING],
    );
    assert!(
        fabricate.is_err(),
        "the trigger must refuse a row no confirmation authorised"
    );
    assert!(j.synced_tree(MAPPING).expect("tree").is_empty());

    // Updates and deletes are guarded too, so a fabricated row cannot be smuggled in by editing
    // or removing a legitimate one either. (A trigger fires per row, so the table needs a real
    // row first — an empty table would make these pass vacuously.)
    let mut j = j;
    let id = j.enqueue_op(&upload_op()).expect("enqueue");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");
    j.confirm_op(
        id,
        "x",
        &local_confirmation(),
        &remote_confirmation(),
        "2026-09-13T00:00:05Z",
    )
    .expect("a legitimate row");
    assert_eq!(j.synced_tree(MAPPING).expect("tree").len(), 1);

    assert!(j
        .connection()
        .execute("UPDATE tree_synced SET content_hash = 'X'", [])
        .is_err());
    assert!(j
        .connection()
        .execute("DELETE FROM tree_synced", [])
        .is_err());
    let row = j
        .synced_tree(MAPPING)
        .expect("tree")
        .get("notes/a.md")
        .cloned()
        .expect("still there, untouched");
    assert_eq!(row.content_hash.as_deref(), Some("a".repeat(64).as_str()));
}

#[test]
fn preserve_local_edit_refuses_anything_less_than_both_sides() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let id = leased_preserve_op(&mut j, "key-preserve-2");
    let mut local = local_confirmation();
    local.local_edit_flagged = true;
    let mut remote = remote_confirmation();
    remote.checksum = None;
    assert!(j
        .preserve_local_edit(id, "x", &local, &remote, "2026-09-13T00:00:00Z")
        .is_err());

    let mut unflagged = local_confirmation();
    unflagged.local_edit_flagged = false;
    assert!(j
        .preserve_local_edit(id, "x", &unflagged, &remote_confirmation(), "t")
        .is_err());
    assert!(j.synced_tree(MAPPING).expect("tree").is_empty());
}

/// G3, from hostile re-verification — guard bypass. F2 closed the `local_edit_flagged` door and
/// left `is_dir` open: a `download_update` op confirmed with `is_dir: true` wrote a row assembled
/// from two different moments, plus a type mismatch the next plan reads as a directory where a
/// file is. `is_dir` was a caller-supplied boolean — the exact shape F2's own reasoning condemned.
#[test]
fn is_dir_cannot_be_used_to_walk_past_the_hash_checksum_refusal() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let mut op = upload_op();
    op.kind = OpKind::DownloadUpdate;
    let id = j.enqueue_op(&op).expect("enqueue");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");

    let mut local = local_confirmation();
    local.is_dir = true;
    local.content_hash = Some("LOCAL_ONE_MOMENT".to_string());
    let mut remote = remote_confirmation();
    remote.checksum = Some("REMOTE_ANOTHER_MOMENT".to_string());

    let err = j
        .confirm_op(id, "x", &local, &remote, "2026-09-13T00:00:05Z")
        .expect_err("a download op is not a directory, whatever the caller says");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");
    assert!(j.synced_tree(MAPPING).expect("tree").is_empty());
}

/// The row type comes from the op, so a directory op confirmed as a file is refused too — and a
/// directory confirmation that carries content is refused, because a directory row has none.
#[test]
fn the_row_type_comes_from_the_op_kind_not_from_the_caller() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");

    let mut mkdir = upload_op();
    mkdir.kind = OpKind::MkdirRemote;
    mkdir.path_nfc = "folder".to_string();
    mkdir.idempotency_key = "key-mkdir".to_string();
    let id = j.enqueue_op(&mkdir).expect("enqueue");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");

    // Claiming it is a file is refused …
    let err = j
        .confirm_op(
            id,
            "x",
            &local_confirmation(),
            &remote_confirmation(),
            "2026-09-13T00:00:05Z",
        )
        .expect_err("a mkdir op is not a file");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");

    // … and so is a directory confirmation carrying content.
    let mut with_content = local_confirmation();
    with_content.is_dir = true;
    let err = j
        .confirm_op(id, "x", &with_content, &remote_confirmation(), "t")
        .expect_err("a directory row carries no content");
    assert!(matches!(err, SyncError::SyncedWriteRefused(_)), "got {err:?}");

    // The honest confirmation succeeds.
    let dir = LocalConfirmation {
        is_dir: true,
        size: None,
        mtime_ns: Some(1),
        volume_id: Some("vol-1".to_string()),
        file_id: Some("inode-dir".to_string()),
        content_hash: None,
        local_edit_flagged: false,
    };
    let remote = RemoteConfirmation {
        remote_file_id: "folder-1".to_string(),
        remote_version: 1,
        checksum: None,
    };
    j.confirm_op(id, "x", &dir, &remote, "2026-09-13T00:00:06Z")
        .expect("an honest directory confirmation");
    let row = j
        .synced_tree(MAPPING)
        .expect("tree")
        .get("folder")
        .cloned()
        .expect("row");
    assert!(row.is_dir && row.content_hash.is_none() && row.checksum.is_none());
}

/// **Secondary, belt-and-braces.** The PRIMARY enforcement of invariant I1's single door is the
/// type system: `tree_synced` is written only through `GuardRaised::write`, and `GuardRaised` is
/// private to `src/journal/confirm.rs` with a private constructor, so a fourth door does not
/// compile. `the_guard_token_is_not_exported` pins that.
///
/// This test is the second layer. The third hostile pass (H2) defeated its earlier form — a
/// literal grep for the flag's name — with `concat!("synced_write", "_", "guard")` in a new `src/`
/// file, so it now flags any file outside the allowlist that **writes `tree_synced`** at all,
/// which is what that probe actually did. It is still a substring search and can still be worked
/// around by someone who means to; it is here to catch a fourth door arriving by accident, and the
/// compiler is what catches the rest.
#[test]
fn nothing_outside_the_allowlist_writes_the_synced_tree_or_its_guard() {
    use std::path::{Path, PathBuf};

    const ALLOWED: &[&str] = &[
        "src/journal/confirm.rs",
        "src/journal/mod.rs", // reads only: local_tree/remote_tree/synced_tree SELECTs
        "migrations/001_initial.sql",
        "migrations/002_i1_write_guard.sql",
        "tests/journal.rs",
    ];
    /// Writing shapes. A SELECT over `tree_synced` is fine; these are not.
    const WRITE_SHAPES: &[&str] = &[
        "INSERT INTO tree_synced",
        "INSERT OR REPLACE INTO tree_synced",
        "UPDATE tree_synced",
        "DELETE FROM tree_synced",
        "synced_write_guard",
    ];

    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(&path, out);
            } else if path
                .extension()
                .is_some_and(|e| e == "rs" || e == "sql")
            {
                out.push(path);
            }
        }
    }

    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut files = Vec::new();
    for sub in ["src", "migrations", "tests"] {
        walk(&root.join(sub), &mut files);
    }
    assert!(files.len() > 5, "the walk found almost nothing; it is broken");

    let mut offenders = Vec::new();
    for file in &files {
        let Ok(text) = std::fs::read_to_string(file) else {
            continue;
        };
        // Normalise whitespace so a wrapped SQL literal is still recognised.
        let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
        if !WRITE_SHAPES.iter().any(|shape| flat.contains(shape)) {
            continue;
        }
        let relative = file
            .strip_prefix(&root)
            .expect("under the crate root")
            .to_string_lossy()
            .replace('\\', "/");
        if !ALLOWED.contains(&relative.as_str()) {
            offenders.push(relative);
        }
    }

    assert!(
        offenders.is_empty(),
        "these files write tree_synced or its guard from outside the allowlist ({ALLOWED:?}): \
         {offenders:?}. The synced tree has exactly three doors and they all live in \
         src/journal/confirm.rs."
    );
}

/// H2: the guard token is **not exported**, so no other module can raise the guard — the property
/// the grep above only approximates.
#[test]
fn the_guard_token_is_not_exported() {
    let confirm = std::fs::read_to_string(
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/journal/confirm.rs"),
    )
    .expect("read confirm.rs");

    assert!(
        confirm.contains("struct GuardRaised"),
        "the guard token has been renamed or removed; this test and the module doc need updating"
    );
    for forbidden in [
        "pub struct GuardRaised",
        "pub(crate) struct GuardRaised",
        "pub(super) struct GuardRaised",
        "pub fn raise(",
        "pub(crate) fn raise(",
        "pub(super) fn raise(",
    ] {
        assert!(
            !confirm.contains(forbidden),
            "`{forbidden}` would let another module raise the tree_synced write guard. The token \
             and its constructor are private to src/journal/confirm.rs on purpose: that privacy is \
             what makes a fourth door a compile error instead of a test finding."
        );
    }

    // And the module itself is private, so even a `pub` item inside it would not escape the crate.
    let journal = std::fs::read_to_string(
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/journal/mod.rs"),
    )
    .expect("read journal/mod.rs");
    assert!(
        journal.contains("mod confirm;") && !journal.contains("pub mod confirm;"),
        "src/journal/confirm.rs must stay a private module"
    );
}

/// H1: the breaker's rolling window is durable, because the wipe it exists to stop frequently
/// takes the process down with it.
#[test]
fn the_deletion_window_is_recorded_on_confirmation_and_survives_a_restart() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("syncd.db");
    {
        let mut j = Journal::open(&path).expect("open");
        j.put_mapping(&mapping_row()).expect("mapping");

        // A deletion is recorded only when it is CONFIRMED — never on enqueue.
        let mut del = upload_op();
        del.kind = OpKind::DeleteRemote;
        del.idempotency_key = "key-del-1".to_string();
        let id = j.enqueue_op(&del).expect("enqueue");
        assert_eq!(
            j.deletions_since(MAPPING, "2000-01-01T00:00:00Z").expect("count"),
            0,
            "an enqueued deletion has not happened yet"
        );

        j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
            .expect("lease")
            .expect("ready");
        j.confirm_delete_op(id, "x", true, true, "2026-09-13T12:00:00Z")
            .expect("confirm");
        assert_eq!(
            j.deletions_since(MAPPING, "2026-09-13T00:00:00Z").expect("count"),
            1
        );
        // The window is a window: anything before it does not count.
        assert_eq!(
            j.deletions_since(MAPPING, "2026-09-14T00:00:00Z").expect("count"),
            0
        );
    }

    // The restart an `rm -rf` frequently causes must not erase the breaker's memory.
    let j = Journal::open(&path).expect("reopen");
    assert_eq!(
        j.deletions_since(MAPPING, "2026-09-13T00:00:00Z").expect("count"),
        1,
        "the window must survive a restart"
    );

    // Resuming a suspended mapping forgets it, so the user's "go on" is not refused again.
    assert_eq!(j.clear_deletion_window(MAPPING).expect("clear"), 1);
    assert_eq!(
        j.deletions_since(MAPPING, "2026-09-13T00:00:00Z").expect("count"),
        0
    );
}

/// A non-destructive op does not count against the window, and the window is per mapping (I7).
#[test]
fn only_real_deletions_count_and_only_against_their_own_mapping() {
    let mut j = Journal::open_in_memory().expect("open");
    j.put_mapping(&mapping_row()).expect("mapping");
    let mut other = mapping_row();
    other.id = "44444444-4444-4444-4444-444444444444".to_string();
    j.put_mapping(&other).expect("second mapping");

    // `unindex` removes a synced row but destroys no user-visible copy.
    let mut bookkeeping = upload_op();
    bookkeeping.kind = OpKind::Unindex;
    bookkeeping.idempotency_key = "key-unindex".to_string();
    let id = j.enqueue_op(&bookkeeping).expect("enqueue");
    j.lease_next_op(MAPPING, "x", "2026-09-13T00:00:01Z", "2026-09-13T00:15:01Z")
        .expect("lease")
        .expect("ready");
    j.confirm_delete_op(id, "x", true, true, "2026-09-13T12:00:00Z")
        .expect("confirm");
    assert_eq!(
        j.deletions_since(MAPPING, "2000-01-01T00:00:00Z").expect("count"),
        0,
        "bookkeeping is not a deletion"
    );

    // A real deletion on the OTHER mapping does not count against this one.
    let mut del = upload_op();
    del.mapping_id = other.id.clone();
    del.kind = OpKind::DeleteLocal;
    del.idempotency_key = "key-del-other".to_string();
    let id = j.enqueue_op(&del).expect("enqueue");
    j.lease_next_op(&other.id, "y", "2026-09-13T00:00:02Z", "2026-09-13T00:15:02Z")
        .expect("lease")
        .expect("ready");
    j.confirm_delete_op(id, "y", true, true, "2026-09-13T12:00:01Z")
        .expect("confirm");
    assert_eq!(
        j.deletions_since(MAPPING, "2000-01-01T00:00:00Z").expect("count"),
        0
    );
    assert_eq!(
        j.deletions_since(&other.id, "2000-01-01T00:00:00Z").expect("count"),
        1
    );

    // Housekeeping outside the window is not the user's decision inside it.
    assert_eq!(j.prune_deletion_window("2026-09-14T00:00:00Z").expect("prune"), 1);
    assert_eq!(
        j.deletions_since(&other.id, "2000-01-01T00:00:00Z").expect("count"),
        0
    );
}
