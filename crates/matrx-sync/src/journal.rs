//! Placeholder for the sync journal.
//!
//! The journal is the per-user SQLite store holding three trees per mapping
//! (local / remote / last-synced), the operation log, and the per-mapping
//! queues. It is **register item FS-C2** and is deliberately unimplemented
//! here: FS-C1 is scaffolding only, and FS-C3 rules that no transfer code may
//! exist before the pure planner's property tests are green.
//!
//! Spec: `common-docs/projects/folder-sync/SCOPE.md` §4-§5,
//! `DECISIONS.md` D2 and D18.
