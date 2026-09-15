# Migration fingerprints — one line per migration: `<name> <bytes> <fnv1a-64 hex>`.

A migration is **frozen** the moment it is committed: a journal that already applied it will never
see an edit, so an edit silently leaves those journals behind. That happened once — `003` was
amended in place to add a table, and any journal already at `schema_version = 3` never got it —
and every test opens a fresh journal, so the suite could not see it. `004` now carries that table
and `003` has been restored to its committed body.

`no_migration_file_changes_after_it_is_committed` compares this file with the embedded SQL and
fails on any difference. It is a **change detector, not a security control**: FNV-1a is not
cryptographic and is not trying to be, and nothing here defends against someone who edits this file
too. It defends against the edit nobody meant to make permanent.

Adding a migration: add its file, add it to `MIGRATIONS`, add its line here. Changing one that has
already shipped: don't — write a new one.

001_initial 6658 7ed465dd5af75bb6
002_i1_write_guard 1882 be2e7e04eec7856e
003_mass_delete_window 1552 eaffe0e5c5a5c004
004_window_item_count 1921 7f5126af10cdf546
