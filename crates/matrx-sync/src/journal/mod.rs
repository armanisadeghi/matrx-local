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
    /// Deliberately **not** `&mut`: a caller cannot open a transaction through it, so no write path
    /// can smuggle a `tree_synced` upsert past [`Journal::confirm_op`] (I1).
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

    // ------------------------------------------- THE synced-tree write guard

    /// **The only way a `tree_synced` row is ever written or removed (I1).**
    ///
    /// Marks op `id` `done` and applies its synced-tree effect in ONE transaction. It refuses
    /// unless:
    ///
    /// * the op exists and is `leased` by `owner` — a plan, an enqueue or a bare 2xx is not enough;
    /// * for a file (`is_dir = false`) the local confirmation carries a hash this daemon computed
    ///   **and** the remote confirmation carries the server's checksum (I2) — enforced here and,
    ///   independently, by the table's `CHECK`.
    ///
    /// `synced_at` is injected; this crate reads no clock.
    pub fn confirm_op(
        &mut self,
        id: i64,
        owner: &str,
        local: &LocalConfirmation,
        remote: &RemoteConfirmation,
        synced_at: &str,
    ) -> Result<()> {
        if !local.is_dir && local.content_hash.is_none() {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} confirms a file with no locally computed hash (I2)"
            )));
        }
        if !local.is_dir && remote.checksum.is_none() {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} confirms a file with no server checksum (I2)"
            )));
        }
        // I1, sharpened: a synced row records ONE state both sides confirmed, so for a file the
        // hash this daemon computed and the checksum the server returned are the SAME bytes'
        // SHA-256. They may differ only on a `local_edit_flagged` row, which is D6's deliberately
        // preserved local edit and is written through `preserve_local_edit`.
        //
        // Without this, an executor that read the local hash and the server checksum at two
        // DIFFERENT moments — which is what happens whenever another device writes in between —
        // records a row that was never true, and the next plan reads it as "local unchanged,
        // remote changed" and downloads over the user's file. The FS-C4 harness found exactly
        // that; see TESTING.md.
        if !local.is_dir
            && !local.local_edit_flagged
            && local.content_hash != remote.checksum
        {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} confirms {:?} locally and {:?} remotely; a synced row records one state \
                 both sides confirmed, never two observations taken at different moments (I1)",
                local.content_hash, remote.checksum
            )));
        }
        let tx = self.conn.transaction()?;
        let found: Option<(String, String, String, Option<String>)> = tx
            .query_row(
                "SELECT mapping_id, path_nfc, state, lease_owner FROM ops WHERE id = ?1",
                params![id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .optional()?;
        let Some((mapping_id, path_nfc, state, lease_owner)) = found else {
            return Err(SyncError::SyncedWriteRefused(format!("op {id} does not exist")));
        };
        if state != OpState::Leased.as_str() {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} is '{state}', not 'leased'; a synced row is written only inside the \
                 transaction that completes a leased op (I1)"
            )));
        }
        if lease_owner.as_deref() != Some(owner) {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} is leased by {lease_owner:?}, not by {owner:?}"
            )));
        }
        tx.execute(
            "INSERT OR REPLACE INTO tree_synced
               (mapping_id, path_nfc, is_dir, size, mtime_ns, volume_id, file_id, content_hash,
                remote_file_id, remote_version, checksum, local_edit_flagged, synced_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13)",
            params![
                mapping_id,
                path_nfc,
                local.is_dir as i64,
                local.size,
                local.mtime_ns,
                local.volume_id,
                local.file_id,
                local.content_hash,
                remote.remote_file_id,
                remote.remote_version,
                remote.checksum,
                local.local_edit_flagged as i64,
                synced_at,
            ],
        )?;
        tx.execute(
            "UPDATE ops SET state='done', lease_owner=NULL, lease_expires_at=NULL, updated_at=?2
             WHERE id = ?1",
            params![id, synced_at],
        )?;
        tx.commit()?;
        Ok(())
    }

    /// The deletion half of the same guard: mark a leased delete op `done` and drop its
    /// `tree_synced` row, in ONE transaction.
    ///
    /// `local_absent` and `remote_absent` are the two confirmations — the executor asserts that the
    /// file is gone from the disk and that the server accepted the tombstone. Both must be true; a
    /// half-confirmed delete leaves the synced row standing so the next plan re-derives the work.
    pub fn confirm_delete_op(
        &mut self,
        id: i64,
        owner: &str,
        local_absent: bool,
        remote_absent: bool,
        now: &str,
    ) -> Result<()> {
        if !(local_absent && remote_absent) {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} deletes a synced row with local_absent={local_absent}, \
                 remote_absent={remote_absent}; both sides must confirm (I1)"
            )));
        }
        let tx = self.conn.transaction()?;
        let found: Option<(String, String, String, Option<String>)> = tx
            .query_row(
                "SELECT mapping_id, path_nfc, state, lease_owner FROM ops WHERE id = ?1",
                params![id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .optional()?;
        let Some((mapping_id, path_nfc, state, lease_owner)) = found else {
            return Err(SyncError::SyncedWriteRefused(format!("op {id} does not exist")));
        };
        if state != OpState::Leased.as_str() || lease_owner.as_deref() != Some(owner) {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} is not leased by {owner:?} (state '{state}')"
            )));
        }
        tx.execute(
            "DELETE FROM tree_synced WHERE mapping_id = ?1 AND path_nfc = ?2",
            params![mapping_id, path_nfc],
        )?;
        tx.execute(
            "UPDATE ops SET state='done', lease_owner=NULL, lease_expires_at=NULL, updated_at=?2
             WHERE id = ?1",
            params![id, now],
        )?;
        tx.commit()?;
        Ok(())
    }

    /// D6's preserve-and-flag, written under the same double-confirmation discipline as
    /// [`Journal::confirm_op`].
    ///
    /// **Spec gap, escalated.** `ops.kind` in SPEC-ENGINE §4 has no value that means "a local edit
    /// was preserved and flagged", so there is no op for this transition to complete — yet D6 and
    /// the `download_only` convergence clause both require the row. This method therefore takes
    /// the two confirmations directly and writes the row in one transaction: the local hash THIS
    /// daemon computed and the server checksum the feed returned (I2 holds), with
    /// `local_edit_flagged = 1`. It refuses anything less.
    pub fn preserve_local_edit(
        &mut self,
        mapping_id: &str,
        path_nfc: &str,
        local: &LocalConfirmation,
        remote: &RemoteConfirmation,
        at: &str,
    ) -> Result<()> {
        if local.is_dir {
            return Err(SyncError::SyncedWriteRefused(
                "a directory cannot carry a preserved local edit".to_string(),
            ));
        }
        if !local.local_edit_flagged {
            return Err(SyncError::SyncedWriteRefused(
                "preserve_local_edit was called without the flag set".to_string(),
            ));
        }
        if local.content_hash.is_none() || remote.checksum.is_none() {
            return Err(SyncError::SyncedWriteRefused(format!(
                "preserving the local edit at {path_nfc} needs both the local hash and the server \
                 checksum (I2)"
            )));
        }
        self.conn.execute(
            "INSERT OR REPLACE INTO tree_synced
               (mapping_id, path_nfc, is_dir, size, mtime_ns, volume_id, file_id, content_hash,
                remote_file_id, remote_version, checksum, local_edit_flagged, synced_at)
             VALUES (?1,?2,0,?3,?4,?5,?6,?7,?8,?9,?10,1,?11)",
            params![
                mapping_id,
                path_nfc,
                local.size,
                local.mtime_ns,
                local.volume_id,
                local.file_id,
                local.content_hash,
                remote.remote_file_id,
                remote.remote_version,
                remote.checksum,
                at,
            ],
        )?;
        Ok(())
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
