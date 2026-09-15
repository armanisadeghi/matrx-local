//! The three — and only three — doors into `tree_synced` (invariant I1).
//!
//! **This file is the whole allowlist, and the compiler enforces it.** Every write to
//! `tree_synced` goes through [`GuardRaised::write`], which needs a `GuardRaised` — a struct
//! private to this module whose only constructor is private to this module. A fourth door is a
//! **compile error**, not a test finding.
//!
//! That is the primary enforcement as of the third hostile pass (H2). Before it, the allowlist was
//! a substring grep over the crate, and the grep was defeated in two lines: a new `src/` file
//! spelling the flag as `concat!("synced_write", "_", "guard")` fabricated a flagged row and CI
//! stayed green. A grep catches a developer who writes the name, not one who writes a wrapper. A
//! type cannot be reached by string concatenation. The grep survives as a clearly-labelled
//! **secondary** check, widened to `tree_synced` writes, which does catch that probe.
//!
//! The history this replaces: migration `002`'s claim that "only the three confirmation methods
//! raise the flag" was a convention claim of exactly the kind that got the old `connection()`
//! comment condemned (G4).
//!
//! # Threat model
//!
//! The guard defends the invariant against **mistake**, across every accidental and cross-process
//! route: a plain `INSERT`, an `UPDATE` or `DELETE` of a legitimately confirmed row, a second
//! `rusqlite::Connection` on the same file, and an `ATTACH` from an unrelated connection are all
//! refused by `RAISE(ABORT)` — a verifier ran all four.
//!
//! It does **not** defend against a caller that means it. Code in this process can
//! `UPDATE synced_write_guard SET active = 1` or `DROP TRIGGER` through
//! [`super::Journal::connection`] and then write whatever it likes; the DDL change even persists.
//! A co-located process with the journal file open can do the same. That is out of the threat
//! model and is stated rather than papered over: this is enforcement against error, not against
//! intent. What changed with H2 is that a mistake now has to get past the **type system** first,
//! not a string search.

use super::{Journal, LocalConfirmation, RemoteConfirmation};
use crate::model::{OpKind, OpState};
use crate::{Result, SyncError};
use rusqlite::{params, OptionalExtension};

impl Journal {
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
        // NOTE: `local.is_dir` is a caller-supplied boolean and is NOT trusted. It is checked
        // against the op's own kind below, after the row is read, and the content checks are
        // driven by the kind. Nothing here may depend on the boolean alone.
        // The flag is NOT an exemption here. Independent verification (F2) showed that treating
        // it as one turned it into a skeleton key: any caller that set `local_edit_flagged: true`
        // on an ordinary op walked straight past the check below. The two doors are disjoint —
        // a flagged row is written ONLY by [`Journal::preserve_local_edit`], which SPEC-ENGINE
        // amendment 1 (2026-09-13, `6c226529`) gives its own `ops.kind` value.
        if local.local_edit_flagged {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} carries local_edit_flagged; a flagged row is written only by \
                 preserve_local_edit, on an op of kind preserve_local_edit (I1, amendment 1)"
            )));
        }
        let tx = self.conn.transaction()?;
        let found: Option<(String, String, String, String, Option<String>)> = tx
            .query_row(
                "SELECT mapping_id, path_nfc, kind, state, lease_owner FROM ops WHERE id = ?1",
                params![id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
            )
            .optional()?;
        let Some((mapping_id, path_nfc, kind, state, lease_owner)) = found else {
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

        // G3, from hostile re-verification: `is_dir: true` walked past the hash-≠-checksum
        // refusal, because that check read the caller's boolean. F2's own reasoning condemned
        // exactly this shape — "a boolean was a skeleton key" — and left this one open. The row
        // type is now DERIVED from the op's kind, and the caller's boolean must agree with it.
        let Some(kind) = OpKind::parse(&kind) else {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} has an unknown kind '{kind}'"
            )));
        };
        let is_dir = matches!(kind, OpKind::MkdirLocal | OpKind::MkdirRemote);
        if local.is_dir != is_dir {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} is a {} op but its confirmation says is_dir={}; the row type comes from \
                 the op, never from the caller (I1)",
                kind.as_str(),
                local.is_dir
            )));
        }
        if is_dir {
            // A directory row carries no content by construction — the table CHECK says so, and a
            // confirmation that offers content is confused about what it just did.
            if local.content_hash.is_some() || remote.checksum.is_some() {
                return Err(SyncError::SyncedWriteRefused(format!(
                    "op {id} confirms a directory carrying content"
                )));
            }
        } else {
            if local.content_hash.is_none() {
                return Err(SyncError::SyncedWriteRefused(format!(
                    "op {id} confirms a file with no locally computed hash (I2)"
                )));
            }
            if remote.checksum.is_none() {
                return Err(SyncError::SyncedWriteRefused(format!(
                    "op {id} confirms a file with no server checksum (I2)"
                )));
            }
            // I1, sharpened: a synced row records ONE state both sides confirmed, so for a file
            // the hash this daemon computed and the checksum the server returned are the SAME
            // bytes' SHA-256. The one exception is the flagged row, which has its own door.
            if local.content_hash != remote.checksum {
                return Err(SyncError::SyncedWriteRefused(format!(
                    "op {id} confirms {:?} locally and {:?} remotely; a synced row records one \
                     state both sides confirmed, never two observations taken at different \
                     moments (I1)",
                    local.content_hash, remote.checksum
                )));
            }
        }
        let guard = GuardRaised::raise(&tx, "confirm_op")?;
        guard.write(
            "INSERT OR REPLACE INTO tree_synced
               (mapping_id, path_nfc, is_dir, size, mtime_ns, volume_id, file_id, content_hash,
                remote_file_id, remote_version, checksum, local_edit_flagged, synced_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13)",
            params![
                mapping_id,
                path_nfc,
                is_dir as i64,
                local.size,
                local.mtime_ns,
                local.volume_id,
                local.file_id,
                local.content_hash,
                remote.remote_file_id,
                remote.remote_version,
                remote.checksum,
                0_i64, // never flagged here — the flagged row has its own door
                synced_at,
            ],
        )?;
        guard.release()?;
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
        let found: Option<(String, String, String, String, Option<String>)> = tx
            .query_row(
                "SELECT mapping_id, path_nfc, kind, state, lease_owner FROM ops WHERE id = ?1",
                params![id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
            )
            .optional()?;
        let Some((mapping_id, path_nfc, kind, state, lease_owner)) = found else {
            return Err(SyncError::SyncedWriteRefused(format!("op {id} does not exist")));
        };
        if state != OpState::Leased.as_str() || lease_owner.as_deref() != Some(owner) {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} is not leased by {owner:?} (state '{state}')"
            )));
        }
        let Some(kind) = OpKind::parse(&kind) else {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} has an unknown kind '{kind}'"
            )));
        };
        let guard = GuardRaised::raise(&tx, "confirm_delete_op")?;
        guard.write(
            "DELETE FROM tree_synced WHERE mapping_id = ?1 AND path_nfc = ?2",
            params![mapping_id, path_nfc],
        )?;
        guard.release()?;
        // H1: the breaker's rolling window is recorded in the SAME transaction that removes the
        // synced row, so a crash between the delete and its accounting is impossible and the count
        // survives the restart an `rm -rf` frequently causes. Only the two ops that actually
        // destroy a user-visible copy count; `unindex` and the like are bookkeeping.
        if matches!(kind, OpKind::DeleteLocal | OpKind::DeleteRemote) {
            tx.execute(
                "INSERT INTO mass_delete_window (mapping_id, at, kind, path_nfc)
                 VALUES (?1, ?2, ?3, ?4)",
                params![mapping_id, now, kind.as_str(), path_nfc],
            )?;
        }
        tx.execute(
            "UPDATE ops SET state='done', lease_owner=NULL, lease_expires_at=NULL, updated_at=?2
             WHERE id = ?1",
            params![id, now],
        )?;
        tx.commit()?;
        Ok(())
    }

    /// D6's preserve-and-flag — the `preserve_local_edit` op (SPEC-ENGINE amendment 1,
    /// 2026-09-13, `6c226529`).
    ///
    /// It is the **one** door through which a `tree_synced` row may carry
    /// `content_hash != checksum`, and it is under the same double-confirmation discipline as
    /// every other terminal op (I1): the op must exist, be `leased` by `owner`, and be of kind
    /// `preserve_local_edit`; the local hash must be one this daemon computed and the checksum one
    /// the server returned; and the row and the `done` transition happen in ONE transaction.
    ///
    /// The kind is checked rather than a boolean trusted, because a boolean was a skeleton key
    /// (F3's sibling finding, F2): anything that could set a flag could write a row that was never
    /// true.
    pub fn preserve_local_edit(
        &mut self,
        id: i64,
        owner: &str,
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
                "op {id} preserves a local edit without both the local hash and the server \
                 checksum (I2)"
            )));
        }
        let tx = self.conn.transaction()?;
        let found: Option<(String, String, String, String, Option<String>)> = tx
            .query_row(
                "SELECT mapping_id, path_nfc, kind, state, lease_owner FROM ops WHERE id = ?1",
                params![id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
            )
            .optional()?;
        let Some((mapping_id, path_nfc, kind, state, lease_owner)) = found else {
            return Err(SyncError::SyncedWriteRefused(format!("op {id} does not exist")));
        };
        if kind != OpKind::PreserveLocalEdit.as_str() {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} is of kind '{kind}'; a flagged row is written only on an op of kind \
                 'preserve_local_edit' (amendment 1)"
            )));
        }
        if state != OpState::Leased.as_str() || lease_owner.as_deref() != Some(owner) {
            return Err(SyncError::SyncedWriteRefused(format!(
                "op {id} is not leased by {owner:?} (state '{state}')"
            )));
        }
        let guard = GuardRaised::raise(&tx, "preserve_local_edit")?;
        guard.write(
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
        guard.release()?;
        tx.execute(
            "UPDATE ops SET state='done', lease_owner=NULL, lease_expires_at=NULL, updated_at=?2
             WHERE id = ?1",
            params![id, at],
        )?;
        tx.commit()?;
        Ok(())
    }

}

/// **Proof, in the type system, that the `tree_synced` write guard is raised.**
///
/// The struct is private to this module and [`GuardRaised::raise`] is its ONLY constructor, so no
/// other module in the crate can produce one — and every write to `tree_synced` in this crate goes
/// through [`GuardRaised::write`], which needs one. A fourth door into the synced tree is therefore
/// a **compile error**, not a test finding.
///
/// That replaces the substring-grep allowlist as the primary enforcement. The third hostile pass
/// (H2) defeated the grep in two lines — `concat!("synced_write", "_", "guard")` in a new `src/`
/// file fabricated a flagged row and CI stayed green — because a grep catches a developer who
/// writes the name, not one who writes a wrapper. A type cannot be reached by string concatenation.
///
/// It still does not defend against raw SQL through [`super::Journal::connection`]; that remains
/// out of the threat model, stated at the top of this file.
struct GuardRaised<'a> {
    tx: &'a rusqlite::Transaction<'a>,
}

impl<'a> GuardRaised<'a> {
    /// Raise the guard for the rest of this transaction (migration `002`).
    ///
    /// Private, and private on purpose: this is the whole allowlist.
    fn raise(tx: &'a rusqlite::Transaction<'a>, door: &str) -> Result<Self> {
        tx.execute(
            "UPDATE synced_write_guard SET active = 1, door = ?1 WHERE id = 1",
            params![door],
        )?;
        Ok(GuardRaised { tx })
    }

    /// Write `tree_synced` under the raised guard. Requires `&self`, so it requires the proof.
    fn write(&self, sql: &str, p: impl rusqlite::Params) -> Result<usize> {
        Ok(self.tx.execute(sql, p)?)
    }

    /// Lower it again, so nothing later in the same transaction writes unauthorised.
    ///
    /// Takes `self` by value: the proof is consumed, so no write can follow the release.
    fn release(self) -> Result<()> {
        self.tx.execute(
            "UPDATE synced_write_guard SET active = 0, door = NULL WHERE id = 1",
            [],
        )?;
        Ok(())
    }
}
