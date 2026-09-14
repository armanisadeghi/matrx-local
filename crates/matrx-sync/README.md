# `matrx-sync` — the folder-sync engine

One crate, two packagings (folder-sync `DECISIONS.md` D1): the `matrx-syncd` daemon and the
Tauri app compile this same source. It holds **no IO of its own** — no network, no host clock,
no `~/.matrx` path composition. Everything the outside world supplies arrives as a value.

Contracts, frozen at G1 in `common-docs/projects/folder-sync/specs/`:

| Contract | Where |
|---|---|
| Journal DDL, invariants I1–I9, the four CanopyCheck invariants | `SPEC-ENGINE.md` §4, §4.1, §4.3 |
| Knob registry with layers and defaults | `SPEC-ENGINE.md` §2 |
| The ONE honest-state enum and its generated artifact | `SPEC-ENGINE.md` §3.6, ruling E16 |
| Cross-spec rulings C1–C14 | `CONTRACT-RULINGS.md` |

A builder who finds a contract wrong reports it. Nobody edits a frozen spec in passing.

## Module map

| Module | Register item | What it owns |
|---|---|---|
| `model` | — | The typed rows and trees every other module speaks in: `LocalNode`, `RemoteNode`, `SyncedNode`, `Tree<N>`, `Direction`, `OpKind`, `ConflictKind`, `OpState`, `OpRow`, `ConflictRow`, `MappingRow`. |
| `states` | FS-C2 | The ONE honest-state enum (ruling C3) with its scopes, and `artifact_json()`, which renders `contracts/honest_states.json` (ruling E16). |
| `knobs` | FS-C2 | The knob registry as a `Knobs` struct the caller passes in, with the frozen §2 defaults and range validation. No limit is ever a constant inside a decision. |
| `journal` | FS-C2 | The SQLite journal: migrations, the three trees, the per-mapping op queues, conflicts — and the synced-tree write guard. |
| `error` | — | `SyncError` / `Result`. |

## The journal

`Journal::open(path)` — **the caller decides the path.** The daemon knows which world it is in
(matrx-local Hard Rule 9: live owns `~/.matrx`, every source run owns `~/.matrx-dev`) and passes
an absolute path to `syncd.db`; this library composes none. `Journal::open_in_memory()` opens the
identical schema for tests and for the FS-C4 harness.

Connection pragmas, per `SPEC-ENGINE.md` §4: `journal_mode=WAL` (file only), `synchronous=FULL`,
`foreign_keys=ON`, `busy_timeout=5000`, `wal_autocheckpoint=512`.

### Migrations

`migrations/NNN_name.sql`, embedded with `include_str!`, listed in `journal::MIGRATIONS`, applied
forward-only in ascending order, one transaction each, recorded in `schema_version`. **There are no
down-migrations.** A journal whose version exceeds this binary's maximum returns
`SyncError::JournalNewerThanBinary`; the daemon answers by publishing `daemon_older_than_journal`
and exiting 0. It never downgrades a journal.

Adding a schema change means adding a **new** file and a new `MIGRATIONS` entry — never editing an
existing one, which would leave every already-migrated journal behind.

### The synced-tree write guard (invariant I1)

There is **no public upsert into `tree_synced`.** The only ways in are:

* `Journal::confirm_op` — requires an op this caller holds the lease on, a `LocalConfirmation`
  (the file re-`stat`ed and re-hashed *after* the write) and a `RemoteConfirmation` (the server's
  `remote_version` + `checksum`); it writes the synced row and marks the op `done` in ONE
  transaction.
* `Journal::confirm_delete_op` — the deletion half, requiring both sides to confirm absence.

`confirm_op` refuses a file confirmation with no locally computed hash or no server checksum (I2),
and the table's own `CHECK (is_dir = 1 OR (content_hash IS NOT NULL AND checksum IS NOT NULL))`
refuses it a second time, so even a future code path reaching for `connection()` cannot express an
optimistic write. `connection()` is deliberately `&self`, not `&mut self`: no transaction can be
opened through it.

This is the notes-engine lesson as code — a `last_synced_hash` that lands NULL produced 14
"conflicts" against nothing (`SPEC-ENGINE.md` §4.1).

## Contracts emitted

`contracts/honest_states.json` (ruling E16) is generated from the `states` enum and **checked in**.
Three consumers read it: SPEC-ENGINE §3.6's three tables, SPEC-SERVER's generated
`files.sync_mappings.state` CHECK, and SPEC-SERVER's divergence test. It is kept honest by
`tests/honest_states_artifact.rs`, which fails when the file on disk is not exactly what the enum
renders. Regenerate with:

```bash
UPDATE_CONTRACTS=1 cargo test -p matrx-sync --test honest_states_artifact
```

## Building and testing

```bash
cargo test -p matrx-sync
cargo clippy -p matrx-sync --all-targets -- -D warnings
cargo doc -p matrx-sync --no-deps
```

No test opens a network connection or touches a real filesystem, except a tempdir for the journal
file in the migration tests.

`rusqlite` is inherited from the workspace, never pinned here: the Tauri app also depends on it and
two versions of `libsqlite3-sys` (`links = "sqlite3"`) cannot coexist in one workspace.
