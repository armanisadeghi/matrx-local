//! The journal (FS-C2, SPEC-ENGINE §4, D2, D18).
//!
//! One SQLite file per user per world. **The path is the caller's decision** — this library never
//! composes `~/.matrx`; the daemon knows which world it is in (Hard Rule 9) and passes an absolute
//! path to [`Journal::open`]. That is what lets the simulation harness (FS-C4) open the same schema
//! in memory with [`Journal::open_in_memory`].
//!
//! # The invariants, as code
//!
//! * **I1 — the synced tree is written only on double confirmation.** There is no public upsert
//!   into `tree_synced`. The one way in is [`Journal::confirm_op`], which requires an op that this
//!   caller holds a lease on, a [`LocalConfirmation`] (the file re-`stat`ed and re-hashed *after*
//!   the write) and a [`RemoteConfirmation`] (the server's `remote_version` + `checksum`), and does
//!   the synced write and the `done` transition in ONE transaction.
//! * **I2 — no invented hashes.** `confirm_op` refuses a file confirmation with no hash, and the
//!   table `CHECK` makes an optimistic write inexpressible even from raw SQL.
//! * **I5 — every op is idempotent under replay.** [`NewOp::idempotency_key`] is `UNIQUE`;
//!   [`Journal::enqueue_op`] returns the existing row's id instead of inserting a duplicate.
//! * **I6 — one writer per tree.** The scanner calls the `local_*` methods, the feed writer the
//!   `remote_*` methods, the executor `confirm_op`. The planner is handed trees by value and
//!   writes nothing.
//! * **I7 — per-mapping isolation.** Every queue method takes a `mapping_id` and touches no other.

mod confirm;
mod migrations;

pub use migrations::{max_version, Migration, MIGRATIONS};

use crate::model::{
    ConflictKind, ConflictRow, Direction, LocalNode, LocalTree, MappingRow, OpKind, OpRow, OpState,
    RemoteNode, RemoteTree, SyncedNode, SyncedTree,
};
use crate::{Result, SyncError};
use rusqlite::{params, Connection, OptionalExtension, Row};
use std::path::Path;

/// The local half of the double confirmation (I1): the file as it was re-`stat`ed and re-hashed
/// **after** the write completed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalConfirmation {
    /// Whether the confirmed node is a directory.
    pub is_dir: bool,
    /// Size in bytes, as re-`stat`ed.
    pub size: Option<i64>,
    /// Modification time, as re-`stat`ed.
    pub mtime_ns: Option<i64>,
    /// The volume the file sits on.
    pub volume_id: Option<String>,
    /// inode / FileId, as re-`stat`ed.
    pub file_id: Option<String>,
    /// The SHA-256 THIS daemon computed after the write. Required for files (I2).
    pub content_hash: Option<String>,
    /// `download_only`: this row holds a deliberately preserved local edit (D6).
    pub local_edit_flagged: bool,
}

/// The remote half of the double confirmation (I1): what the server's response carried.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RemoteConfirmation {
    /// `files.files.id` the server returned.
    pub remote_file_id: String,
    /// The row version the server returned.
    pub remote_version: i64,
    /// The SHA-256 the server returned. Required for files (I2).
    pub checksum: Option<String>,
}

/// An op to enqueue. `id` is assigned by the journal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewOp {
    /// The mapping whose queue it joins.
    pub mapping_id: String,
    /// Ordering within that queue.
    pub seq: i64,
    /// What the executor must do.
    pub kind: OpKind,
    /// The path it acts on.
    pub path_nfc: String,
    /// The destination, for renames and conflict copies.
    pub target_path_nfc: Option<String>,
    /// The cloud file it targets.
    pub remote_file_id: Option<String>,
    /// The 412 precondition's checksum (I3).
    pub expected_checksum: Option<String>,
    /// The 412 precondition's version (I3).
    pub expected_version: Option<i64>,
    /// The pre-image a destructive local act must match (I3).
    pub expected_local_hash: Option<String>,
    /// `sha256(mapping_id, kind, path_nfc, expected_version|expected_local_hash)` (I5).
    pub idempotency_key: String,
    /// RFC3339 creation time — injected, never read from a clock inside this crate.
    pub created_at: String,
}

/// The journal: one owned SQLite connection, migrated to this binary's schema.
#[derive(Debug)]
pub struct Journal {
    conn: Connection,
}

impl Journal {
    /// Open (creating if absent) the journal at `path`, apply the connection pragmas and migrate
    /// forward.
    ///
    /// The caller owns the path decision. The daemon passes
    /// `<matrx home for this world>/syncd.db`; it is never `matrx.db`, which the Python engine owns
    /// — a shared connection is the tearing class discovery A measured.
    ///
    /// # Errors
    /// [`SyncError::JournalNewerThanBinary`] when the file was written by a newer build; the daemon
    /// answers that by publishing `daemon_older_than_journal` and exiting 0 (§4.2).
    pub fn open(path: &Path) -> Result<Self> {
        let conn = Connection::open(path)?;
        // journal_mode returns a row, so it cannot go through execute_batch.
        let mode: String = conn.query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0))?;
        if !mode.eq_ignore_ascii_case("wal") {
            return Err(SyncError::Decode(format!(
                "journal_mode is {mode}, not WAL; the journal file may be on a filesystem that \
                 cannot support it"
            )));
        }
        Self::finish_open(conn)
    }

    /// Open an in-memory journal carrying the identical schema.
    ///
    /// This is what the FS-C4 simulation harness and every unit test use. WAL is meaningless for an
    /// in-memory database and is the one pragma that differs.
    pub fn open_in_memory() -> Result<Self> {
        Self::finish_open(Connection::open_in_memory()?)
    }

    fn finish_open(conn: Connection) -> Result<Self> {
        conn.execute_batch(
            "PRAGMA synchronous=FULL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA busy_timeout=5000;\
             PRAGMA wal_autocheckpoint=512;",
        )?;
        let mut j = Journal { conn };
        j.migrate()?;
        Ok(j)
    }

    /// The schema version currently recorded, or 0 for a fresh file.
    pub fn schema_version(&self) -> Result<i64> {
        let has_table: bool = self
            .conn
            .query_row(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'",
                [],
                |r| r.get::<_, i64>(0),
            )
            .optional()?
            .is_some();
        if !has_table {
            return Ok(0);
        }
        Ok(self
            .conn
            .query_row("SELECT COALESCE(MAX(version), 0) FROM schema_version", [], |r| r.get(0))?)
    }

    /// Apply every migration this binary carries that the file has not seen, in order, one
    /// transaction each. Forward-only.
    fn migrate(&mut self) -> Result<()> {
        let current = self.schema_version()?;
        let supported = max_version();
        if current > supported {
            return Err(SyncError::JournalNewerThanBinary {
                found: current,
                supported,
            });
        }
        for m in MIGRATIONS.iter().filter(|m| m.version > current) {
            let tx = self.conn.transaction()?;
            tx.execute_batch(m.sql)?;
            // The timestamp is SQLite's own clock, evaluated by the database: this crate reads
            // no host clock anywhere, so the planner's purity rule holds for the whole library.
            tx.execute(
                "INSERT INTO schema_version (version, applied_at)
                 VALUES (?1, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                params![m.version],
            )?;
            tx.commit()?;
        }
        Ok(())
    }

    /// Borrow the connection for reads the typed API does not cover.
    ///
    /// A write to `tree_synced` through this handle **fails** — migration `002` puts
    /// `BEFORE INSERT / UPDATE / DELETE` triggers on the table that abort unless a one-row flag is
    /// raised, and the only code allowed to raise it is [`crate::journal`]'s confirmation module,
    /// inside the very transaction that performs the write it authorises. I1 is therefore enforced
    /// by the database, not by convention in this crate. The allowlist is enforced too: a test
    /// greps the crate and fails if the flag is named anywhere else.
    ///
    /// Its threat model — enforcement against mistake, not against a caller that means it — is
    /// stated in that module's own documentation.
    ///
    /// The earlier version of this comment claimed `&self` was enough because no transaction could
    /// be opened through it. That was false — `rusqlite::Connection::execute` takes `&self` — and
    /// independent verification fabricated a row to prove it (F3).
    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    // ---------------------------------------------------------------- mappings

    /// Insert or replace a mapping row.
    pub fn put_mapping(&self, m: &MappingRow) -> Result<()> {
        self.conn.execute(
            "INSERT OR REPLACE INTO mappings (id, organization_id, cloud_kind, cloud_folder_id,
                 local_root, local_root_volume_id, direction, desired_state, state, state_detail,
                 knobs, file_cursor, folder_cursor, cloud_row_version, marker_uuid,
                 created_at, updated_at, last_sync_at, last_full_rescan_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19)",
            params![
                m.id,
                m.organization_id,
                m.cloud_kind,
                m.cloud_folder_id,
                m.local_root,
                m.local_root_volume_id,
                m.direction.as_str(),
                m.desired_state,
                m.state,
                m.state_detail,
                m.knobs,
                m.file_cursor,
                m.folder_cursor,
                m.cloud_row_version,
                m.marker_uuid,
                m.created_at,
                m.updated_at,
                m.last_sync_at,
                m.last_full_rescan_at,
            ],
        )?;
        Ok(())
    }

    /// Read a mapping row.
    pub fn mapping(&self, mapping_id: &str) -> Result<Option<MappingRow>> {
        let row = self
            .conn
            .query_row(
                "SELECT id, organization_id, cloud_kind, cloud_folder_id, local_root,
                        local_root_volume_id, direction, desired_state, state, state_detail, knobs,
                        file_cursor, folder_cursor, cloud_row_version, marker_uuid, created_at,
                        updated_at, last_sync_at, last_full_rescan_at
                 FROM mappings WHERE id = ?1",
                params![mapping_id],
                mapping_from_row,
            )
            .optional()?;
        row.transpose()
    }

    // ------------------------------------------------------------- local tree

    /// Insert or replace a `tree_local` row. Written only by the scanner (I6).
    pub fn put_local(&self, mapping_id: &str, n: &LocalNode) -> Result<()> {
        self.conn.execute(
            "INSERT OR REPLACE INTO tree_local
               (mapping_id, path_nfc, is_dir, size, mtime_ns, volume_id, file_id, content_hash, scanned_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            params![
                mapping_id,
                n.path_nfc,
                n.is_dir as i64,
                n.size,
                n.mtime_ns,
                n.volume_id,
                n.file_id,
                n.content_hash,
                n.scanned_at
            ],
        )?;
        Ok(())
    }

    /// Delete a `tree_local` row.
    pub fn delete_local(&self, mapping_id: &str, path_nfc: &str) -> Result<()> {
        self.conn.execute(
            "DELETE FROM tree_local WHERE mapping_id = ?1 AND path_nfc = ?2",
            params![mapping_id, path_nfc],
        )?;
        Ok(())
    }

    /// The whole local tree for a mapping, ready for the planner.
    pub fn local_tree(&self, mapping_id: &str) -> Result<LocalTree> {
        let mut st = self.conn.prepare(
            "SELECT path_nfc, is_dir, size, mtime_ns, volume_id, file_id, content_hash, scanned_at
             FROM tree_local WHERE mapping_id = ?1 ORDER BY path_nfc",
        )?;
        let rows = st.query_map(params![mapping_id], local_from_row)?;
        let mut tree = LocalTree::new();
        for r in rows {
            let n = r??;
            tree.insert(n.path_nfc.clone(), n);
        }
        Ok(tree)
    }

    // ------------------------------------------------------------ remote tree

    /// Insert or replace a `tree_remote` row. Written only by the feed/Realtime writer (I6).
    pub fn put_remote(&self, mapping_id: &str, n: &RemoteNode) -> Result<()> {
        self.conn.execute(
            "INSERT OR REPLACE INTO tree_remote
               (mapping_id, path_nfc, is_dir, size, remote_file_id, remote_folder_id, remote_version,
                checksum, client_modified_at, origin_device_id, deleted_at, seen_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12)",
            params![
                mapping_id,
                n.path_nfc,
                n.is_dir as i64,
                n.size,
                n.remote_file_id,
                n.remote_folder_id,
                n.remote_version,
                n.checksum,
                n.client_modified_at,
                n.origin_device_id,
                n.deleted_at,
                n.seen_at
            ],
        )?;
        Ok(())
    }

    /// Delete a `tree_remote` row outright. A tombstone is a row with `deleted_at` set, not a
    /// missing row — use [`Journal::put_remote`] for that.
    pub fn delete_remote(&self, mapping_id: &str, path_nfc: &str) -> Result<()> {
        self.conn.execute(
            "DELETE FROM tree_remote WHERE mapping_id = ?1 AND path_nfc = ?2",
            params![mapping_id, path_nfc],
        )?;
        Ok(())
    }

    /// The whole remote tree for a mapping, tombstones included.
    pub fn remote_tree(&self, mapping_id: &str) -> Result<RemoteTree> {
        let mut st = self.conn.prepare(
            "SELECT path_nfc, is_dir, size, remote_file_id, remote_folder_id, remote_version,
                    checksum, client_modified_at, origin_device_id, deleted_at, seen_at
             FROM tree_remote WHERE mapping_id = ?1 ORDER BY path_nfc",
        )?;
        let rows = st.query_map(params![mapping_id], remote_from_row)?;
        let mut tree = RemoteTree::new();
        for r in rows {
            let n = r??;
            tree.insert(n.path_nfc.clone(), n);
        }
        Ok(tree)
    }

    // ------------------------------------------------------------ synced tree

    /// The whole synced tree for a mapping.
    pub fn synced_tree(&self, mapping_id: &str) -> Result<SyncedTree> {
        let mut st = self.conn.prepare(
            "SELECT path_nfc, is_dir, size, mtime_ns, volume_id, file_id, content_hash,
                    remote_file_id, remote_version, checksum, local_edit_flagged, synced_at
             FROM tree_synced WHERE mapping_id = ?1 ORDER BY path_nfc",
        )?;
        let rows = st.query_map(params![mapping_id], synced_from_row)?;
        let mut tree = SyncedTree::new();
        for r in rows {
            let n = r??;
            tree.insert(n.path_nfc.clone(), n);
        }
        Ok(tree)
    }

    // ------------------------------------------------------------- op queue

    /// Enqueue an op, idempotently (I5).
    ///
    /// A replay after a crash re-finds the row by `idempotency_key` and returns its id; a duplicate
    /// plan is therefore a no-op insert, not a second transfer.
    pub fn enqueue_op(&self, op: &NewOp) -> Result<i64> {
        if let Some(id) = self
            .conn
            .query_row(
                "SELECT id FROM ops WHERE idempotency_key = ?1",
                params![op.idempotency_key],
                |r| r.get::<_, i64>(0),
            )
            .optional()?
        {
            return Ok(id);
        }
        self.conn.execute(
            "INSERT INTO ops (mapping_id, seq, kind, path_nfc, target_path_nfc, remote_file_id,
                 expected_checksum, expected_version, expected_local_hash, state, attempts,
                 idempotency_key, created_at, updated_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,'ready',0,?10,?11,?11)",
            params![
                op.mapping_id,
                op.seq,
                op.kind.as_str(),
                op.path_nfc,
                op.target_path_nfc,
                op.remote_file_id,
                op.expected_checksum,
                op.expected_version,
                op.expected_local_hash,
                op.idempotency_key,
                op.created_at,
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Take a lease on the next ready op of ONE mapping's queue (I7).
    ///
    /// `now` and `lease_expires_at` are injected RFC3339 strings — this crate reads no clock.
    pub fn lease_next_op(
        &self,
        mapping_id: &str,
        owner: &str,
        now: &str,
        lease_expires_at: &str,
    ) -> Result<Option<OpRow>> {
        let id: Option<i64> = self
            .conn
            .query_row(
                "SELECT id FROM ops
                 WHERE mapping_id = ?1 AND state = 'ready'
                   AND (next_attempt_at IS NULL OR next_attempt_at <= ?2)
                 ORDER BY seq, id LIMIT 1",
                params![mapping_id, now],
                |r| r.get(0),
            )
            .optional()?;
        let Some(id) = id else { return Ok(None) };
        self.conn.execute(
            "UPDATE ops SET state='leased', lease_owner=?2, lease_expires_at=?3, attempts=attempts+1,
                            updated_at=?4
             WHERE id = ?1",
            params![id, owner, lease_expires_at, now],
        )?;
        self.op(id)
    }

    /// Take a lease on ONE named op, whatever its position in the queue.
    ///
    /// [`Journal::lease_next_op`] is the queue drainer; this is what the executor uses when it is
    /// applying a plan whose ops it already knows, and what makes an enqueue-then-act sequence
    /// idempotent: an op that is already `done` is returned unchanged and **not** re-leased, so a
    /// replay after a crash re-finds the completed work instead of doing it twice (I5).
    ///
    /// Returns the row as it now stands. A caller must check `state`: `leased` by `owner` means
    /// the lease was taken, `done` means there is nothing left to do.
    pub fn lease_op(
        &self,
        id: i64,
        owner: &str,
        now: &str,
        lease_expires_at: &str,
    ) -> Result<Option<OpRow>> {
        self.conn.execute(
            "UPDATE ops SET state='leased', lease_owner=?2, lease_expires_at=?3,
                            attempts=attempts+1, updated_at=?4
             WHERE id = ?1 AND state IN ('ready','leased','failed')",
            params![id, owner, lease_expires_at, now],
        )?;
        self.op(id)
    }

    /// Retire a leased op that has **no `tree_synced` effect at all**.
    ///
    /// Two ops in the vocabulary change the world without changing the synced tree: a conflict
    /// copy (D7 — the copy is a brand-new local file with no cloud counterpart yet) and a recorded
    /// conflict. Marking them `failed` to get them out of the queue would be a lie on the surface
    /// the user reads, and confirming them through [`Journal::confirm_op`] is impossible because
    /// there is nothing to confirm.
    ///
    /// This door writes `ops` and nothing else. It cannot reach `tree_synced`: that table is
    /// reachable only through the private `GuardRaised` token in `journal::confirm`, which this
    /// function has no way to obtain.
    pub fn retire_op(&self, id: i64, owner: &str, note: &str, now: &str) -> Result<()> {
        let changed = self.conn.execute(
            "UPDATE ops SET state='done', lease_owner=NULL, lease_expires_at=NULL,
                            error_code=NULL, error_detail=?3, updated_at=?4
             WHERE id = ?1 AND state='leased' AND lease_owner = ?2",
            params![id, owner, note, now],
        )?;
        if changed == 0 {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} is not leased by {owner:?}; it cannot be retired"
            )));
        }
        Ok(())
    }

    /// Release every lease this owner holds on a mapping, back to `ready`.
    ///
    /// Called on start-up: a lease that did not survive a crash must not keep its op out of the
    /// queue until the TTL expires. The op keeps its `expected_*` pre-image, so the retake is the
    /// same op against the same precondition (I3, I5).
    pub fn release_leases(&self, mapping_id: &str, now: &str) -> Result<usize> {
        Ok(self.conn.execute(
            "UPDATE ops SET state='ready', lease_owner=NULL, lease_expires_at=NULL, updated_at=?2
             WHERE mapping_id = ?1 AND state = 'leased'",
            params![mapping_id, now],
        )?)
    }

    /// Write the daemon-owned honest state of a mapping row (C3, C4).
    ///
    /// `desired_state` is the user's field and is never touched here.
    pub fn set_mapping_state(
        &self,
        mapping_id: &str,
        state: &str,
        detail: Option<&str>,
        now: &str,
    ) -> Result<()> {
        let legal = matches!(
            crate::states::HonestState::get(state),
            Some(s) if s.allows(crate::states::Scope::Mapping)
        );
        if !legal {
            return Err(SyncError::Decode(format!(
                "'{state}' is not a mapping-scoped honest state; the vocabulary is \
                 contracts/honest_states.json and nothing may invent a value (C3)"
            )));
        }
        self.conn.execute(
            "UPDATE mappings SET state = ?2, state_detail = ?3, updated_at = ?4 WHERE id = ?1",
            params![mapping_id, state, detail, now],
        )?;
        Ok(())
    }

    /// The highest `seq` this mapping's queue has used, so a new plan appends after it.
    pub fn max_op_seq(&self, mapping_id: &str) -> Result<i64> {
        Ok(self.conn.query_row(
            "SELECT COALESCE(MAX(seq), 0) FROM ops WHERE mapping_id = ?1",
            params![mapping_id],
            |r| r.get(0),
        )?)
    }

    /// Read one op row.
    pub fn op(&self, id: i64) -> Result<Option<OpRow>> {
        let row = self
            .conn
            .query_row(
                "SELECT id, mapping_id, seq, kind, path_nfc, target_path_nfc, remote_file_id,
                        expected_checksum, expected_version, expected_local_hash, state, attempts,
                        next_attempt_at, lease_owner, lease_expires_at, idempotency_key,
                        transfer_offset, transfer_upload_url, error_code, error_detail,
                        created_at, updated_at
                 FROM ops WHERE id = ?1",
                params![id],
                op_from_row,
            )
            .optional()?;
        row.transpose()
    }

    /// Every op of one mapping in a given state, in queue order.
    pub fn ops_in_state(&self, mapping_id: &str, state: OpState) -> Result<Vec<OpRow>> {
        let mut st = self.conn.prepare(
            "SELECT id, mapping_id, seq, kind, path_nfc, target_path_nfc, remote_file_id,
                    expected_checksum, expected_version, expected_local_hash, state, attempts,
                    next_attempt_at, lease_owner, lease_expires_at, idempotency_key,
                    transfer_offset, transfer_upload_url, error_code, error_detail,
                    created_at, updated_at
             FROM ops WHERE mapping_id = ?1 AND state = ?2 ORDER BY seq, id",
        )?;
        let rows = st.query_map(params![mapping_id, state.as_str()], op_from_row)?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r??);
        }
        Ok(out)
    }

    /// Record a failure and schedule a retry. Never touches `tree_synced`.
    pub fn fail_op(
        &self,
        id: i64,
        error_code: &str,
        error_detail: &str,
        next_attempt_at: Option<&str>,
        now: &str,
    ) -> Result<()> {
        self.conn.execute(
            "UPDATE ops SET state = CASE WHEN ?4 IS NULL THEN 'failed' ELSE 'ready' END,
                            error_code=?2, error_detail=?3, next_attempt_at=?4,
                            lease_owner=NULL, lease_expires_at=NULL, updated_at=?5
             WHERE id = ?1",
            params![id, error_code, error_detail, next_attempt_at, now],
        )?;
        Ok(())
    }

    /// Record resumable-transfer progress so a crash resumes instead of restarting.
    pub fn record_transfer_progress(
        &self,
        id: i64,
        offset: i64,
        upload_url: Option<&str>,
        now: &str,
    ) -> Result<()> {
        self.conn.execute(
            "UPDATE ops SET transfer_offset=?2, transfer_upload_url=COALESCE(?3, transfer_upload_url),
                            updated_at=?4
             WHERE id = ?1",
            params![id, offset, upload_url, now],
        )?;
        Ok(())
    }

    // --------------------------------------------- the mass-delete rolling window

    /// How many deletions this mapping executed at or after `since` (H1).
    ///
    /// This is what [`crate::PlanContext::recent_deletions`] is filled from. Timestamps are
    /// RFC3339 UTC, whose lexicographic order **is** chronological order, so the comparison is a
    /// string comparison and the daemon computes `since` by subtracting
    /// `sync.mass_delete_window_hours` from now.
    ///
    /// The planner never calls this: it is pure, and a window is state.
    pub fn deletions_since(&self, mapping_id: &str, since: &str) -> Result<usize> {
        let n: i64 = self.conn.query_row(
            "SELECT count(*) FROM mass_delete_window WHERE mapping_id = ?1 AND at >= ?2",
            params![mapping_id, since],
            |r| r.get(0),
        )?;
        Ok(n.max(0) as usize)
    }

    /// The mapping's item count when the deletion window opened, if a window is open and has not
    /// aged out past `since`.
    ///
    /// This is [`crate::PlanContext::window_item_count`] — the percentage arm's **frozen**
    /// denominator (SPEC-ENGINE §2 amendment 3). `None` means no window is open, and the planner
    /// then falls back to what the mapping holds now, which is the same number when no deletions
    /// have happened.
    pub fn window_item_count(&self, mapping_id: &str, since: &str) -> Result<Option<usize>> {
        let row: Option<i64> = self
            .conn
            .query_row(
                "SELECT item_count FROM mass_delete_window_open
                 WHERE mapping_id = ?1 AND opened_at >= ?2",
                params![mapping_id, since],
                |r| r.get(0),
            )
            .optional()?;
        Ok(row.map(|n| n.max(0) as usize))
    }

    /// Forget this mapping's deletion window.
    ///
    /// Called when the user **resumes a suspended mapping** — they have looked at what was about
    /// to happen and said to go on, so the window that stopped it must not stop it again on the
    /// next plan. It is also how a legitimate large cleanup completes: suspend, ask, resume.
    ///
    /// It touches only the breaker's own memory; the `activity` log the user reads is untouched.
    pub fn clear_deletion_window(&self, mapping_id: &str) -> Result<usize> {
        self.conn.execute(
            "DELETE FROM mass_delete_window_open WHERE mapping_id = ?1",
            params![mapping_id],
        )?;
        let n = self.conn.execute(
            "DELETE FROM mass_delete_window WHERE mapping_id = ?1",
            params![mapping_id],
        )?;
        Ok(n)
    }

    /// Drop window rows older than `before`, so the table cannot grow without bound.
    ///
    /// Separate from [`Journal::clear_deletion_window`] on purpose: this is housekeeping outside
    /// the window, that one is the user's decision inside it.
    pub fn prune_deletion_window(&self, before: &str) -> Result<usize> {
        // A window whose opening has aged out is no window: its frozen denominator must go with
        // it, or a stale count would divide a fresh window's deletions.
        self.conn.execute(
            "DELETE FROM mass_delete_window_open WHERE opened_at < ?1",
            params![before],
        )?;
        let n = self.conn.execute(
            "DELETE FROM mass_delete_window WHERE at < ?1",
            params![before],
        )?;
        Ok(n)
    }

    // ------------------------------------------------------------- conflicts

    /// Record a conflict.
    pub fn put_conflict(&self, c: &ConflictRow) -> Result<()> {
        self.conn.execute(
            "INSERT OR REPLACE INTO conflicts (id, mapping_id, path_nfc, kind, local_hash,
                 remote_file_id, remote_checksum, conflict_copy_path, detected_at, resolved_at,
                 resolution)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)",
            params![
                c.id,
                c.mapping_id,
                c.path_nfc,
                c.kind.as_str(),
                c.local_hash,
                c.remote_file_id,
                c.remote_checksum,
                c.conflict_copy_path,
                c.detected_at,
                c.resolved_at,
                c.resolution
            ],
        )?;
        Ok(())
    }

    /// Every unresolved conflict of one mapping.
    pub fn open_conflicts(&self, mapping_id: &str) -> Result<Vec<ConflictRow>> {
        let mut st = self.conn.prepare(
            "SELECT id, mapping_id, path_nfc, kind, local_hash, remote_file_id, remote_checksum,
                    conflict_copy_path, detected_at, resolved_at, resolution
             FROM conflicts WHERE mapping_id = ?1 AND resolved_at IS NULL ORDER BY detected_at, id",
        )?;
        let rows = st.query_map(params![mapping_id], conflict_from_row)?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r??);
        }
        Ok(out)
    }

    // -------------------------------------------------------- device settings

    /// Write one device-scoped knob value with its source (`user`, `org`, `default`).
    pub fn put_device_setting(
        &self,
        key: &str,
        value: &str,
        source: &str,
        updated_at: &str,
    ) -> Result<()> {
        self.conn.execute(
            "INSERT OR REPLACE INTO device_settings (key, value, source, updated_at)
             VALUES (?1,?2,?3,?4)",
            params![key, value, source, updated_at],
        )?;
        Ok(())
    }

    /// Read one device-scoped knob value.
    pub fn device_setting(&self, key: &str) -> Result<Option<String>> {
        Ok(self
            .conn
            .query_row(
                "SELECT value FROM device_settings WHERE key = ?1",
                params![key],
                |r| r.get(0),
            )
            .optional()?)
    }
}

// ------------------------------------------------------------------ row decoding

type RowResult<T> = rusqlite::Result<Result<T>>;

fn mapping_from_row(r: &Row<'_>) -> RowResult<MappingRow> {
    let dir_text: String = r.get(6)?;
    let Some(direction) = Direction::parse(&dir_text) else {
        return Ok(Err(SyncError::Decode(format!(
            "mappings.direction '{dir_text}' is not a direction"
        ))));
    };
    Ok(Ok(MappingRow {
        id: r.get(0)?,
        organization_id: r.get(1)?,
        cloud_kind: r.get(2)?,
        cloud_folder_id: r.get(3)?,
        local_root: r.get(4)?,
        local_root_volume_id: r.get(5)?,
        direction,
        desired_state: r.get(7)?,
        state: r.get(8)?,
        state_detail: r.get(9)?,
        knobs: r.get(10)?,
        file_cursor: r.get(11)?,
        folder_cursor: r.get(12)?,
        cloud_row_version: r.get(13)?,
        marker_uuid: r.get(14)?,
        created_at: r.get(15)?,
        updated_at: r.get(16)?,
        last_sync_at: r.get(17)?,
        last_full_rescan_at: r.get(18)?,
    }))
}

fn local_from_row(r: &Row<'_>) -> RowResult<LocalNode> {
    Ok(Ok(LocalNode {
        path_nfc: r.get(0)?,
        is_dir: r.get::<_, i64>(1)? != 0,
        size: r.get(2)?,
        mtime_ns: r.get(3)?,
        volume_id: r.get(4)?,
        file_id: r.get(5)?,
        content_hash: r.get(6)?,
        scanned_at: r.get(7)?,
    }))
}

fn remote_from_row(r: &Row<'_>) -> RowResult<RemoteNode> {
    Ok(Ok(RemoteNode {
        path_nfc: r.get(0)?,
        is_dir: r.get::<_, i64>(1)? != 0,
        size: r.get(2)?,
        remote_file_id: r.get(3)?,
        remote_folder_id: r.get(4)?,
        remote_version: r.get(5)?,
        checksum: r.get(6)?,
        client_modified_at: r.get(7)?,
        origin_device_id: r.get(8)?,
        deleted_at: r.get(9)?,
        seen_at: r.get(10)?,
    }))
}

fn synced_from_row(r: &Row<'_>) -> RowResult<SyncedNode> {
    Ok(Ok(SyncedNode {
        path_nfc: r.get(0)?,
        is_dir: r.get::<_, i64>(1)? != 0,
        size: r.get(2)?,
        mtime_ns: r.get(3)?,
        volume_id: r.get(4)?,
        file_id: r.get(5)?,
        content_hash: r.get(6)?,
        remote_file_id: r.get(7)?,
        remote_version: r.get(8)?,
        checksum: r.get(9)?,
        local_edit_flagged: r.get::<_, i64>(10)? != 0,
        synced_at: r.get(11)?,
    }))
}

fn op_from_row(r: &Row<'_>) -> RowResult<OpRow> {
    let kind_text: String = r.get(3)?;
    let Some(kind) = OpKind::parse(&kind_text) else {
        return Ok(Err(SyncError::Decode(format!(
            "ops.kind '{kind_text}' is not an op kind"
        ))));
    };
    let state_text: String = r.get(10)?;
    let Some(state) = OpState::parse(&state_text) else {
        return Ok(Err(SyncError::Decode(format!(
            "ops.state '{state_text}' is not an op state"
        ))));
    };
    Ok(Ok(OpRow {
        id: r.get(0)?,
        mapping_id: r.get(1)?,
        seq: r.get(2)?,
        kind,
        path_nfc: r.get(4)?,
        target_path_nfc: r.get(5)?,
        remote_file_id: r.get(6)?,
        expected_checksum: r.get(7)?,
        expected_version: r.get(8)?,
        expected_local_hash: r.get(9)?,
        state,
        attempts: r.get(11)?,
        next_attempt_at: r.get(12)?,
        lease_owner: r.get(13)?,
        lease_expires_at: r.get(14)?,
        idempotency_key: r.get(15)?,
        transfer_offset: r.get(16)?,
        transfer_upload_url: r.get(17)?,
        error_code: r.get(18)?,
        error_detail: r.get(19)?,
        created_at: r.get(20)?,
        updated_at: r.get(21)?,
    }))
}

fn conflict_from_row(r: &Row<'_>) -> RowResult<ConflictRow> {
    let kind_text: String = r.get(3)?;
    let Some(kind) = ConflictKind::parse(&kind_text) else {
        return Ok(Err(SyncError::Decode(format!(
            "conflicts.kind '{kind_text}' is not a conflict kind"
        ))));
    };
    Ok(Ok(ConflictRow {
        id: r.get(0)?,
        mapping_id: r.get(1)?,
        path_nfc: r.get(2)?,
        kind,
        local_hash: r.get(4)?,
        remote_file_id: r.get(5)?,
        remote_checksum: r.get(6)?,
        conflict_copy_path: r.get(7)?,
        detected_at: r.get(8)?,
        resolved_at: r.get(9)?,
        resolution: r.get(10)?,
    }))
}
