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
-- resuming a suspended mapping clears these tables without touching it.
--
-- AMENDED IN PLACE on 2026-09-15 to add `mass_delete_window_open`. That is normally forbidden —
-- migrations are forward-only and an edit leaves every already-migrated journal behind — and it is
-- acceptable here only because this migration has shipped to NO user: there is no daemon yet, the
-- only journals carrying it are tempdirs and in-memory databases created by this crate's own
-- tests, and matrx-local is pre-launch. The migration test's expected version is bumped with it.
-- The next change to this schema is migration 004.

CREATE TABLE mass_delete_window (
  id         INTEGER PRIMARY KEY,
  mapping_id TEXT NOT NULL,
  at         TEXT NOT NULL,   -- RFC3339 UTC: lexicographic order IS chronological order
  kind       TEXT NOT NULL,   -- the ops.kind that executed (delete_local | delete_remote)
  path_nfc   TEXT NOT NULL
);
CREATE INDEX ix_mass_delete_window ON mass_delete_window (mapping_id, at);

-- The window's FROZEN DENOMINATOR (SPEC-ENGINE §2 amendment 3: "`deleted_percent` = that count over
-- the mapping's item count at the START of the window, not at the moment of the plan … so a
-- shrinking mapping cannot dilute its own percentage").
--
-- Reading `tree_synced` at plan time gets this wrong in both directions. A fourth seat reproduced
-- the second one: 40 files, 19 deleted, then 200 files ADDED and synced, then 19 more deleted —
-- the live count is 221, so 38 deletions read as 15.8% instead of 95% and the wipe propagated with
-- no suspension. A mapping receiving a download, an import or a restore while a local `rm -rf`
-- walks the tree is exactly that shape.
--
-- So the count is recorded once, when the first deletion OPENS the window, inside the same
-- transaction as that deletion — and it is the count BEFORE that deletion's row is removed.
CREATE TABLE mass_delete_window_open (
  mapping_id TEXT PRIMARY KEY,
  opened_at  TEXT NOT NULL,   -- RFC3339 UTC, same ordering rule as mass_delete_window.at
  item_count INTEGER NOT NULL -- tree_synced rows for this mapping when the window opened
);
