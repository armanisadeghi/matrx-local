-- 003_mass_delete_window — the mass-delete circuit breaker's ROLLING WINDOW.
--
-- Third-seat hostile re-verification (verification/FS-C2-C4-reverify-2.md, H1) wiped 38 of 40
-- files with no suspension by splitting the deletion across two consecutive plans: the breaker
-- counted the plan in front of it and remembered nothing. That is not exotic — the daemon plans on
-- a `sync.watcher_debounce_ms` timer and the scanner walks a large tree incrementally, so an
-- `rm -rf`, a drive unmounting under the sync root, or ransomware working alphabetically all reach
-- the planner as a STREAM of small deletions. The breaker exists for exactly that scenario.
--
-- So a deletion is recorded here the moment it is CONFIRMED — in the same transaction that removes
-- the `tree_synced` row, so a crash cannot lose it — and the count survives the restart an
-- `rm -rf` frequently causes. The planner is pure, so it never reads this table: the daemon counts
-- the window and passes the number in.
--
-- The rows are the breaker's memory, not the user's activity log; `activity` keeps that, and
-- resuming a suspended mapping clears this table without touching it.

CREATE TABLE mass_delete_window (
  id         INTEGER PRIMARY KEY,
  mapping_id TEXT NOT NULL,
  at         TEXT NOT NULL,   -- RFC3339 UTC: lexicographic order IS chronological order
  kind       TEXT NOT NULL,   -- the ops.kind that executed (delete_local | delete_remote)
  path_nfc   TEXT NOT NULL
);
CREATE INDEX ix_mass_delete_window ON mass_delete_window (mapping_id, at);
