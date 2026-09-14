//! The device's session truth — the journal's `session_state` row (§8a, C10).
//!
//! One row, surviving restart. **That is what makes it a state and not a toast.** The table is
//! part of the journal DDL (SPEC-ENGINE §4, migration `001`); this module is its only reader and
//! writer, and it reaches the table through [`Journal::connection`] rather than growing the
//! journal module — the journal owns the schema, custody owns this row's meaning.
//!
//! [`Journal::connection`]: crate::journal::Journal::connection

use super::error::Result;
use super::world::World;
use crate::journal::Journal;
use rusqlite::{params, OptionalExtension};
use serde::Serialize;
use std::sync::{Arc, Mutex};

/// The five session values of the ONE honest-state enum (C3, SPEC-ENGINE §3.6 table a).
///
/// Custody writes only values from that table and never invents one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionState {
    /// The healthy session value — a positive state, so no surface infers health from the absence
    /// of a fault (A5).
    SignedIn,
    /// Terminal session loss. One spelling everywhere (C2).
    SignInNeeded,
    /// A deliberate sign-out. Explicit, never disguised as `paused` (C3).
    SignedOut,
    /// The OS keychain refused (S7).
    CredentialStoreUnavailable,
    /// Retrying; the remedy shows `next_attempt_at`.
    Offline,
}

impl SessionState {
    /// The wire spelling, identical to the value written to the journal and to
    /// `files.sync_mappings.state`.
    pub const fn as_str(self) -> &'static str {
        match self {
            SessionState::SignedIn => "signed_in",
            SessionState::SignInNeeded => "sign_in_needed",
            SessionState::SignedOut => "signed_out",
            SessionState::CredentialStoreUnavailable => "credential_store_unavailable",
            SessionState::Offline => "offline",
        }
    }

    /// Parse a stored spelling. `None` for anything that is not one of the five.
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "signed_in" => Some(SessionState::SignedIn),
            "sign_in_needed" => Some(SessionState::SignInNeeded),
            "signed_out" => Some(SessionState::SignedOut),
            "credential_store_unavailable" => Some(SessionState::CredentialStoreUnavailable),
            "offline" => Some(SessionState::Offline),
            _ => None,
        }
    }

    /// Whether entering this state posts an OS notification (S18): `sign_in_needed` and
    /// `credential_store_unavailable` only, once per state entry.
    pub const fn notifies(self) -> bool {
        matches!(
            self,
            SessionState::SignInNeeded | SessionState::CredentialStoreUnavailable
        )
    }
}

/// The `session_state` row, column for column (§8a).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SessionRow {
    /// The honest state.
    pub state: SessionState,
    /// The remedy sentence every surface shows verbatim.
    pub state_reason: Option<String>,
    /// RFC3339 — when this state was entered.
    pub since: String,
    /// Supabase user id (uuid) = the keychain account.
    pub user_id: Option<String>,
    /// For "Sign back in as …"; never part of an item name.
    pub email: Option<String>,
    /// `live` or `dev`.
    pub world: World,
    /// RFC3339; scheduling uses monotonic time (S10), not this.
    pub access_token_expires_at: Option<String>,
    /// RFC3339 of the last successful rotation.
    pub last_refresh_at: Option<String>,
    /// RFC3339 of the last rotation attempt, successful or not.
    pub last_attempt_at: Option<String>,
    /// What the `offline` state shows the user.
    pub next_attempt_at: Option<String>,
    /// Consecutive failures behind the current backoff.
    pub failure_count: i64,
    /// S16 branch B: the cloud write could not be made.
    pub cloud_state_write_pending: bool,
    /// RFC3339 of the last successful cloud state write.
    pub cloud_state_written_at: Option<String>,
    /// S18 dedup key, half one.
    pub notified_state: Option<String>,
    /// S18 dedup key, half two.
    pub notified_since: Option<String>,
    /// RFC3339 of the last write to this row.
    pub updated_at: String,
}

impl SessionRow {
    /// The row a journal with no session has ever held: signed out, in this world, as of `now`.
    pub fn signed_out(world: World, now: &str) -> Self {
        SessionRow {
            state: SessionState::SignedOut,
            state_reason: Some(
                "Sign in on this computer to start syncing your folders.".to_string(),
            ),
            since: now.to_string(),
            user_id: None,
            email: None,
            world,
            access_token_expires_at: None,
            last_refresh_at: None,
            last_attempt_at: None,
            next_attempt_at: None,
            failure_count: 0,
            cloud_state_write_pending: false,
            cloud_state_written_at: None,
            notified_state: None,
            notified_since: None,
            updated_at: now.to_string(),
        }
    }

    /// Whether S18's dedup key says this state entry has already been notified.
    pub fn already_notified(&self) -> bool {
        self.notified_state.as_deref() == Some(self.state.as_str())
            && self.notified_since.as_deref() == Some(self.since.as_str())
    }
}

/// The one reader and writer of `session_state`.
#[derive(Debug, Clone)]
pub struct SessionStore {
    journal: Arc<Mutex<Journal>>,
}

impl SessionStore {
    /// Wrap a journal. The mutex is the journal's own single-connection rule (SPEC-ENGINE §4):
    /// `rusqlite::Connection` is `Send` but not `Sync`, so one lock guards every use.
    pub fn new(journal: Arc<Mutex<Journal>>) -> Self {
        SessionStore { journal }
    }

    /// Read the row, or `None` when this journal has never held a session.
    pub fn read(&self) -> Result<Option<SessionRow>> {
        let journal = self.journal.lock().expect("journal mutex");
        let row = journal
            .connection()
            .query_row(
                "SELECT state, state_reason, since, user_id, email, world,
                        access_token_expires_at, last_refresh_at, last_attempt_at,
                        next_attempt_at, failure_count, cloud_state_write_pending,
                        cloud_state_written_at, notified_state, notified_since, updated_at
                   FROM session_state WHERE id = 1",
                [],
                |r| {
                    let state: String = r.get(0)?;
                    let world: String = r.get(5)?;
                    Ok((state, world, r.get::<_, Option<String>>(1)?, r.get::<_, String>(2)?,
                        r.get::<_, Option<String>>(3)?, r.get::<_, Option<String>>(4)?,
                        r.get::<_, Option<String>>(6)?, r.get::<_, Option<String>>(7)?,
                        r.get::<_, Option<String>>(8)?, r.get::<_, Option<String>>(9)?,
                        r.get::<_, i64>(10)?, r.get::<_, i64>(11)?,
                        r.get::<_, Option<String>>(12)?, r.get::<_, Option<String>>(13)?,
                        r.get::<_, Option<String>>(14)?, r.get::<_, String>(15)?))
                },
            )
            .optional()
            .map_err(crate::SyncError::from)?;
        drop(journal);

        let Some(t) = row else { return Ok(None) };
        let state = SessionState::parse(&t.0).ok_or_else(|| {
            crate::SyncError::Decode(format!(
                "session_state.state holds {:?}, which is not one of the five session values of \
                 the honest-state enum",
                t.0
            ))
        })?;
        let world = World::parse(&t.1).ok_or_else(|| {
            crate::SyncError::Decode(format!("session_state.world holds {:?}", t.1))
        })?;
        Ok(Some(SessionRow {
            state,
            state_reason: t.2,
            since: t.3,
            user_id: t.4,
            email: t.5,
            world,
            access_token_expires_at: t.6,
            last_refresh_at: t.7,
            last_attempt_at: t.8,
            next_attempt_at: t.9,
            failure_count: t.10,
            cloud_state_write_pending: t.11 != 0,
            cloud_state_written_at: t.12,
            notified_state: t.13,
            notified_since: t.14,
            updated_at: t.15,
        }))
    }

    /// Write the row. There is exactly one, and the table's `CHECK (id = 1)` enforces it.
    pub fn write(&self, row: &SessionRow) -> Result<()> {
        let journal = self.journal.lock().expect("journal mutex");
        journal
            .connection()
            .execute(
                "INSERT INTO session_state (id, state, state_reason, since, user_id, email, world,
                     access_token_expires_at, last_refresh_at, last_attempt_at, next_attempt_at,
                     failure_count, cloud_state_write_pending, cloud_state_written_at,
                     notified_state, notified_since, updated_at)
                 VALUES (1, ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)
                 ON CONFLICT(id) DO UPDATE SET
                     state=excluded.state, state_reason=excluded.state_reason,
                     since=excluded.since, user_id=excluded.user_id, email=excluded.email,
                     world=excluded.world,
                     access_token_expires_at=excluded.access_token_expires_at,
                     last_refresh_at=excluded.last_refresh_at,
                     last_attempt_at=excluded.last_attempt_at,
                     next_attempt_at=excluded.next_attempt_at,
                     failure_count=excluded.failure_count,
                     cloud_state_write_pending=excluded.cloud_state_write_pending,
                     cloud_state_written_at=excluded.cloud_state_written_at,
                     notified_state=excluded.notified_state,
                     notified_since=excluded.notified_since,
                     updated_at=excluded.updated_at",
                params![
                    row.state.as_str(),
                    row.state_reason,
                    row.since,
                    row.user_id,
                    row.email,
                    row.world.as_str(),
                    row.access_token_expires_at,
                    row.last_refresh_at,
                    row.last_attempt_at,
                    row.next_attempt_at,
                    row.failure_count,
                    i64::from(row.cloud_state_write_pending),
                    row.cloud_state_written_at,
                    row.notified_state,
                    row.notified_since,
                    row.updated_at,
                ],
            )
            .map_err(crate::SyncError::from)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> SessionStore {
        SessionStore::new(Arc::new(Mutex::new(
            Journal::open_in_memory().expect("journal"),
        )))
    }

    #[test]
    fn a_fresh_journal_holds_no_session() {
        assert_eq!(store().read().expect("read"), None);
    }

    #[test]
    fn the_row_survives_a_write_and_read_intact() {
        let s = store();
        let mut row = SessionRow::signed_out(World::Dev, "2026-09-13T00:00:00Z");
        row.state = SessionState::SignInNeeded;
        row.user_id = Some("u-1".into());
        row.email = Some("admin@admin.com".into());
        row.failure_count = 3;
        row.cloud_state_write_pending = true;
        s.write(&row).expect("write");
        assert_eq!(s.read().expect("read"), Some(row));
    }

    #[test]
    fn a_second_write_replaces_the_one_row_rather_than_adding_one() {
        let s = store();
        s.write(&SessionRow::signed_out(World::Dev, "t0")).expect("w1");
        let mut second = SessionRow::signed_out(World::Dev, "t1");
        second.state = SessionState::SignedIn;
        s.write(&second).expect("w2");
        let count: i64 = {
            let j = s.journal.lock().unwrap();
            j.connection()
                .query_row("SELECT COUNT(*) FROM session_state", [], |r| r.get(0))
                .unwrap()
        };
        assert_eq!(count, 1);
        assert_eq!(s.read().unwrap().unwrap().state, SessionState::SignedIn);
    }

    #[test]
    fn the_five_spellings_are_the_enums_and_nothing_else() {
        for value in [
            "signed_in",
            "sign_in_needed",
            "signed_out",
            "credential_store_unavailable",
            "offline",
        ] {
            assert_eq!(SessionState::parse(value).map(SessionState::as_str), Some(value));
        }
        assert_eq!(SessionState::parse("paused"), None);
        assert_eq!(SessionState::parse("healthy"), None);
    }

    #[test]
    fn only_the_two_terminal_states_notify() {
        assert!(SessionState::SignInNeeded.notifies());
        assert!(SessionState::CredentialStoreUnavailable.notifies());
        assert!(!SessionState::SignedIn.notifies());
        assert!(!SessionState::SignedOut.notifies());
        assert!(!SessionState::Offline.notifies());
    }
}
