-- 002_synced_write_guard — invariant I1 enforced by the DATABASE, not by convention.
--
-- Independent verification (verification/FS-C2-C4-verify.md, F3) proved that
-- `Journal::connection()` could fabricate a `tree_synced` row with raw SQL: `execute` takes
-- `&self` and needs no transaction, and the table's own CHECK only forbids NULL hashes, not
-- invented ones. The doc comment claiming otherwise was false.
--
-- The guard is a one-row flag that ONLY the confirmation API raises, inside the same transaction
-- as the write it authorises. Three triggers refuse every write to `tree_synced` while the flag is
-- down. A crash mid-transaction rolls the flag back with everything else, so it can never be left
-- raised.

CREATE TABLE synced_write_guard (
  id     INTEGER PRIMARY KEY CHECK (id = 1),
  active INTEGER NOT NULL DEFAULT 0,
  -- Which confirmation door raised it, for the error message and for debugging.
  door   TEXT
);
INSERT INTO synced_write_guard (id, active, door) VALUES (1, 0, NULL);

CREATE TRIGGER tree_synced_insert_guard BEFORE INSERT ON tree_synced
WHEN (SELECT active FROM synced_write_guard WHERE id = 1) = 0
BEGIN
  SELECT RAISE(ABORT,
    'tree_synced is written only through a confirmed op (I1): use Journal::confirm_op, confirm_delete_op or preserve_local_edit');
END;

CREATE TRIGGER tree_synced_update_guard BEFORE UPDATE ON tree_synced
WHEN (SELECT active FROM synced_write_guard WHERE id = 1) = 0
BEGIN
  SELECT RAISE(ABORT,
    'tree_synced is written only through a confirmed op (I1): use Journal::confirm_op, confirm_delete_op or preserve_local_edit');
END;

CREATE TRIGGER tree_synced_delete_guard BEFORE DELETE ON tree_synced
WHEN (SELECT active FROM synced_write_guard WHERE id = 1) = 0
BEGIN
  SELECT RAISE(ABORT,
    'tree_synced rows are removed only through a confirmed delete op (I1): use Journal::confirm_delete_op');
END;
