-- 004_window_item_count — the mass-delete window's FROZEN DENOMINATOR.
--
-- SPEC-ENGINE §2 amendment 3: "`deleted_percent` = that count over the mapping's item count at the
-- START of the window, not at the moment of the plan … so a shrinking mapping cannot dilute its own
-- percentage." Reading `tree_synced` at plan time gets this wrong in BOTH directions. A fourth seat
-- reproduced the second: 40 files, 19 deleted, then 200 files ADDED and synced, then 19 more
-- deleted — the live count is 221, so 38 deletions read as 15.8% instead of 95% and the wipe
-- propagated with no suspension. A mapping receiving a download, an import or a restore while a
-- local `rm -rf` walks the tree is exactly that shape.
--
-- The count is recorded once, by the deletion that OPENS the window, inside that deletion's own
-- transaction, from the `tree_synced` count taken BEFORE its row is removed.
--
-- WHY THIS IS A SEPARATE MIGRATION. It first shipped as an in-place edit to `003`, which was wrong:
-- a journal already at `schema_version = 3` never re-runs 003, so it never got the table and the
-- first breaker read failed with `no such table: mass_delete_window_open`. Every test opens a fresh
-- journal, so the whole suite was blind to it — the exact shape of "migrations are forward-only".
-- `003` has been restored to the body it was committed with, this file carries the addition, and
-- `migrations/FINGERPRINTS.md` plus `no_migration_file_changes_after_it_is_committed` make a repeat a test
-- failure rather than a discovery.
--
-- IF NOT EXISTS because a journal that was migrated while 003 carried the amendment already has it.

CREATE TABLE IF NOT EXISTS mass_delete_window_open (
  mapping_id TEXT PRIMARY KEY,
  opened_at  TEXT NOT NULL,   -- RFC3339 UTC, same ordering rule as mass_delete_window.at
  item_count INTEGER NOT NULL -- tree_synced rows for this mapping when the window opened
);
