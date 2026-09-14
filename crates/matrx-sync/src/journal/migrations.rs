//! Forward-only migrations (SPEC-ENGINE §4.2, E11).
//!
//! Each migration is a numbered `.sql` file under `crates/matrx-sync/migrations/`, embedded with
//! `include_str!` so the binary that needs a schema always carries it. They are applied in
//! ascending order, one transaction each, and recorded in `schema_version`. **There are no
//! down-migrations** (no-legacy until go-live): a journal whose `schema_version` exceeds this
//! binary's maximum makes the daemon refuse to start, publish `daemon_older_than_journal` and
//! exit 0. It never downgrades a journal.
//!
//! Adding a migration is adding a file here and a line to [`MIGRATIONS`] — never an edit to an
//! existing file, which would leave every already-migrated journal behind.

/// One migration: its version, its human name, and its SQL.
#[derive(Debug, Clone, Copy)]
pub struct Migration {
    /// The `schema_version.version` this migration produces.
    pub version: i64,
    /// The file's name, for logs and errors.
    pub name: &'static str,
    /// The SQL, embedded at compile time.
    pub sql: &'static str,
}

/// Every migration this binary carries, in ascending version order.
pub const MIGRATIONS: &[Migration] = &[Migration {
    version: 1,
    name: "001_initial",
    sql: include_str!("../../migrations/001_initial.sql"),
}];

/// The highest schema version this binary can read.
pub fn max_version() -> i64 {
    MIGRATIONS.last().map(|m| m.version).unwrap_or(0)
}
