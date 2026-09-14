# Testing `matrx-sync`

```bash
cargo test -p matrx-sync                                    # 10,000 cases per property (CI mode)
PROPTEST_CASES=100000 cargo test -p matrx-sync --release    # the local soak
cargo clippy -p matrx-sync --all-targets -- -D warnings
```

No test opens a network connection. The only real filesystem touched is a tempdir for the journal
file in `tests/journal.rs`; everything else is in memory.

## The property harness (FS-C3)

`tests/planner_properties.rs` generates random three-tree configurations over a fixed path pool —
which deliberately contains a case collision (`a.txt` / `A.txt`), a name Windows reserves
(`CON.txt`) and two paths inside one folder — picks a direction, and drives the planner to a fixed
point through the executor model in `src/sim/world.rs`. It then reads SPEC-ENGINE §4.3's four
invariants off the resulting trees.

`proptest` shrinks every failure to a minimal counter-example and appends its seed to
`tests/planner_properties.proptest-regressions`, **which is checked in**: those seeds run first on
every future invocation, so a fixed bug cannot silently come back.

### What the generator guarantees, and why

A `tree_synced` row records ONE state both sides confirmed, so its `content_hash` (the local
SHA-256) and its `checksum` (the server's) are the same bytes' hash — invariants I1 and I2 make any
other row unwritable. They differ only on a `local_edit_flagged` row, which is exactly a preserved
local edit (D6). The first version of the generator drew the two independently and so manufactured
a past no correct daemon could have produced; the planner was then asked to converge an impossible
history. That was a **harness** defect, not a planner defect, and is recorded here so nobody
"fixes" the planner against it later.

### Reading the spec's clauses exactly, including their own exceptions

Two clauses of §4.3 carry stated exceptions in their own sentences, and the assertions honour them
rather than asserting past them:

* `upload_only` — "Remote-only paths are **not** created locally", and local deletes propagate
  **iff** `sync.upload_only_propagates_deletes`. Paths in those two classes are exempt from the
  path-equivalence clause.
* `download_only` — rows with `local_edit_flagged = 1` "keep their local bytes … and are never
  overwritten". Those paths are exempt from the content-equivalence clause. A file the cloud never
  held is likewise not the cloud's to delete.

Two further exemptions are structural, not textual:

* A path with an **unresolved conflict row** is `needs_conflict_resolution`: the planner proposes
  nothing for it by design, which is what makes the fixed point reachable. This is why
  `PlanContext` carries `open_conflicts` — a value like every other input, so the planner stays
  pure.
* **Directory rows** are excluded from every content clause, as §4.3 states.

### The two escalated gaps were ruled — SPEC-ENGINE amendment 1

**Both escalations were accepted and landed as a dated amendment to the frozen spec**
(`common-docs` `6c226529`, 2026-09-13, "SPEC-ENGINE amendment 1"):

* **§4.3 data preservation** is restated as *"no content is lost that the direction did not
  authorise losing"*, asserted per `(path, side)` occurrence against the **live** sides only, with
  `tree_synced` never counted as a content holder and the contradictory "Deletion case" sentence
  deleted. That is what this harness already asserted.
* **§2** states that the mass-delete breaker's two knobs combine with **AND**, with the
  one-file-of-two example as the reason. That is what `planner::plan` already implements.
* **§4** additionally gains an `ops.kind` value `preserve_local_edit` for D6's flagged row, and
  I6 now names `PlanContext.open_conflicts` as a planner input with the reason no fixed point
  exists without it. Both match the existing code; the op kind is now carried as a kind rather
  than a boolean (see finding F2 below).

The reasoning that produced them is kept below, because it is the evidence behind the amendment.

### The clause as originally frozen, and why it could not be asserted literally

§4.3's data-preservation row says *"every content hash present in any tree at t0 is reachable at
the end"*. Read literally, with `tree_synced` counted as a tree, **no ordinary update can satisfy
it**: when a user edits a file, the previous content — which `tree_synced` recorded — is gone from
all three trees at the end, by design. The same is true of every propagated deletion, which the
clause's own "Deletion case" sentence *requires* to end in none of the three trees.

The property therefore asserts the guarantee the clause is reaching for: **no content is lost that
the direction did not authorise losing.** `tree_synced` is bookkeeping of a past sync, not an
extant copy, so the measurement is taken against the live sides, and losses are computed **per
occurrence** — one (path, side) pair — so a content id that also lives at an untouched path is
still required to survive. Authorised losses are exactly:

| Direction | Authorised |
|---|---|
| `two_way` | the loser of an ordinary **one-sided** update, and a propagated deletion. A both-modified case is **never** authorised — D7 keeps both copies. |
| `upload_only` | the cloud's divergent bytes (local is authority, D6; the cloud keeps its own trash, outside these three trees) and a propagated deletion when the knob allows it. |
| `download_only` | the local bytes **only** when they were unchanged since the last sync. A local edit is flagged and preserved, never authorised. |

This reading was escalated rather than taken quietly, and **amendment 1 landed it**.

A second gap: SPEC-ENGINE §2 originally gave the mass-delete breaker two knobs,
`sync.mass_delete_percent` (50) and `sync.mass_delete_count` (1000), and never said how they
combine. They are combined with **AND** — see `planner::plan`'s documentation. **Amendment 1
states this in §2.**

## Findings from independent verification (2026-09-13)

An adversarial re-verify by a seat with **zero authorship** — `common-docs`
`projects/folder-sync/verification/FS-C2-C4-verify.md`, verdict **PASS_WITH_FINDINGS** at
`8d71e4b22` — confirmed the DDL byte-for-byte, the planner's purity by grep, the honest-state
artifact's staleness test proven red, and two of the five claimed defect fixes proven
failing-then-passing by reverting them. It also found **nine** things this lane had missed. Every
one is fixed below as a class, and every fix carries a test **proven red before it went green**.

| # | Severity | What it was | Fix |
|---|---|---|---|
| **F1** | HIGH, **data loss** | Two conflicts on one path, on one day, from one device rendered the **same** copy name, and the second overwrote the first — destroying the very thing a conflict copy exists to protect. The verifier reproduced it: `{c1, c2, c9}` became `{c1, c2}`. | `naming::unique_conflict_copy_path` keeps D7's shape and appends ` (2)`, ` (3)` … while the name is taken in any of the three trees or already claimed by this plan. Pure: the planner already holds every path as an input. **The property generator could not reach the class** — its path pool contained no copy-shaped name — so the pool now carries one, and the verifier's exact case is `a_second_conflict_the_same_day_does_not_destroy_the_first_copy`. |
| **F2** | MEDIUM, guard bypass | `local_edit_flagged: true` was an **exemption** from `confirm_op`'s hash-≠-checksum refusal, which made the boolean a skeleton key: anything that could set it could record a row that was never true. | The two doors are disjoint. `confirm_op` refuses the flag outright; `preserve_local_edit` takes an **op id** and refuses any op whose kind is not `preserve_local_edit` — the value amendment 1 added to `ops.kind` — under the same lease and double-confirmation discipline. |
| **F3** | MEDIUM, a false claim | `connection()`'s doc comment said no write could smuggle a `tree_synced` row past `confirm_op` because the handle is `&self`. False — `Connection::execute` takes `&self` — and the verifier fabricated a row to prove it. | Migration `002` puts `BEFORE INSERT / UPDATE / DELETE` triggers on `tree_synced` that abort unless a one-row `synced_write_guard` flag is raised, and only the three confirmation methods raise it, inside the very transaction as the write they authorise. A crash rolls the flag back with everything else. I1 is now enforced by the **database**. |
| **F4** | MEDIUM, weakened assertion | `reachable_content` counted `tree_synced` as a content holder while `live_content` correctly did not, so content surviving only as a journal row passed as preserved — the rule applied to one side of the comparison and not the other. | `tree_synced` is no longer read there. Amendment 1 states the rule outright. All scenarios and the soaks were re-run after. |
| **F5** | MEDIUM, unreachable spec state | `unicode_collision` was defined, in the spec's `conflicts.kind` enum, and **emitted by nothing**: `collision_key` case-folded but never normalised. NFC/NFD twins are one filesystem entry on APFS and NTFS, so the executor would have clobbered one with the other in silence — exactly what I8 says must never happen. | `unicode-normalization` (SPEC-ENGINE §5) added; `collision_key` NFC-normalises, and a group whose members differ only after normalisation is named `UnicodeCollision`. The twin pair is now in the property pool. |
| **F6** | LOW | An `upload_only` directory gone from **both** sides emitted a tombstone with no `remote_file_id` and no precondition — an op no real executor can address, which would sit `failed` on that mapping's queue forever. A wildcard arm matched before the specific one. | Arms reordered; `two_way` and `upload_only` now agree on `ForgetSynced`. |
| **F7** | LOW, coverage hole | D7's "both copies reach the cloud" held in practice but was asserted nowhere: the convergence clause exempts conflict-copy paths. | The property now asserts every conflict copy ends present in all three trees for `two_way`. |
| **F8** | LOW, below the champion bar | A case-only rename (`a.txt` → `A.txt`) became a permanent conflict on both spellings — safe, and one of the most common things a Mac user does to a filename. Dropbox performs it. | Renames are detected **before** the collision pass, so identity decides: a path whose `(volume, inode)` matches the synced row with unchanged content is a move, and a colliding group that is exactly one rename's two spellings is not a dispute. |
| **F9** | TRIVIAL | A stale comment in `Cargo.toml` about a workspace bump that had already landed. | Deleted. |

The verifier also recorded two things this lane should not paper over: **the tombstone-retention
scenario is inexpressible here** (the mock never purges tombstones, by design), so a device absent
longer than the 90-day floor is unverified anywhere — that belongs to FS-C6/FS-V2 — and the soak
timings quoted below are this Mac's, on this day, and should not be read as a benchmark.

## Defects the harness found

Both were found by the property tests at a few hundred cases, before any large run — which is the
method working.

### 1. `download_only` overwrote a preserved local edit on the round after flagging it

**Severity: data loss.** Found by `preserves_data` (`DownloadOnly`; seed
`e9be34a0…`-class, minimal case: one path, local `c1`, remote `c0`, no synced row).

The first round correctly emitted `FlagLocalEdit`, which writes a `tree_synced` row carrying the
*local* hash and `local_edit_flagged = 1`. On the **next** round the branch that decides whether
the local file changed compared it against that synced row — which now held the user's own hash —
so `local_changed` came back **false**, and the planner fell through to `Download{Update}`,
overwriting the user's bytes with the cloud's. The flag was written and then immediately made
meaningless.

Fix: the flag is checked **before** any change computation, so a flagged row is never overwritten
on any round (`planner.rs`, `decide_download_only`). The regression seed is checked in.

This is exactly the class D6 exists to prevent, and exactly the class a "does a two-device sync
work?" smoke test would have missed: it only appears on the second round.

### 2. A case collision froze one of the two paths without telling anyone

**Severity: silent stall** (law 4 — nothing fails silently). Found by `converges_per_direction`
(`DownloadOnly`; minimal case: `a.txt` and `A.txt` both present in the cloud, neither on disk).

The collision detector quarantined **both** members of a colliding group — correctly, since neither
can be created on a case-insensitive volume — but recorded a `conflicts` row for only the second
one. The first path was therefore frozen forever with no conflict row, no state, and nothing on
screen: a file that simply never synced and never explained why.

Fix: every member of a colliding group carries its own conflict row naming the path it collides
with (`planner.rs`, the collision pass).

### 3. `Download{Create}` overwrote a file the scanner had not yet seen — **data loss**

**Severity: data loss, silent.** Found by
`two_devices_converge_with_the_cloud_under_interleaving_failures_and_crashes` (seed 7, step 18).

A device that polls the change feed before it walks the disk plans `Download{Create}` for a path it
believes is empty. If the user already has a *different* file there, the executor wrote straight
over it — no conflict copy, no conflict row, nothing on screen. The plan was correct for the trees
it was given; the executor was trusting a tree it had not refreshed.

Fix (`sim/device.rs`): a download whose `expected_local_hash` is `None` — a create — must find
**nothing** at the path. The pre-image for a create is absence, and I3 applies to it like any other
destructive write. Finding something else aborts the op, rescans and re-plans.

### 4. A synced row assembled from two different moments — **data loss**

**Severity: data loss.** Same run, and the reason the fix for (3) alone was not enough.

`RecordSynced` is emitted when the local hash and the server checksum already agree. Between the
plan and its execution another device can write, and the executor was reading the local hash from
the plan and the checksum from the server *as it now stood*, recording a `tree_synced` row that was
never true. The next plan read that row as "local unchanged, remote changed" and downloaded over
the user's file.

Fixed as a **class**, in the journal rather than in the executor: `Journal::confirm_op` now refuses
a file confirmation whose local hash and server checksum differ. A synced row records ONE state
both sides confirmed, so for a file those two values are the same bytes' SHA-256 — they may differ
only on a `local_edit_flagged` row, which has its own method (`preserve_local_edit`). Two tests in
`tests/journal.rs` hold the guard. The executor's own re-check is the second layer, not the fix.

### 5. Conflict resolution livelocked (two separate causes) — **stall**

**Severity: silent stall.** Found by the same test.

*Cause one.* The harness's executor re-planned after every single operation. A conflict resolution
is several ops; re-planning after the first re-derives the identical first op forever, because
nothing has changed yet. This is precisely why SPEC-ENGINE §4 gives the journal an `ops` **queue**:
a plan is enqueued whole and drained in order. The harness now does that
(`sim/device.rs::pending`), and a race throws the remainder away and re-plans.

*Cause two, in the planner.* The conflict plan bolted the losing copy's upload on with
`expected_version: None`. Whenever that copy already existed in the cloud — a crash between the
upload and its confirmation, or the other device having written it — the upload drew a 412, the
plan was discarded, and the identical plan came back. Fixed by not bolting it on at all: once the
copy exists on disk it is an ordinary local-only path, and the next round's normal per-path logic
uploads it with a real precondition read from the remote tree.

### Not found

Nothing else, by the harnesses. Independent verification found nine more; they are the table above,
and finding them is what the verifier seat is for. The 100,000-case soak (`PROPTEST_CASES=100000`, release, all four properties, three
directions) passed with no failures after those two fixes — run on this Mac on 2026-09-13, 24.8 s
wall. That is a statement about the **planner**, and about the model of an executor in
`src/sim/`. It is not product evidence: the harness is a mock and says so (SCOPE §6, D2).

## The simulation harness (FS-C4)

`tests/simulation.rs` runs two simulated devices against one mock cloud from a fixed, checked-in
set of seeds (`SIM_SEEDS=n` adds `n` more for a soak). Each device has an in-memory disk with real
inode identity and an OS-trash stand-in, and a **real** SQLite journal in a tempdir — so "the
device crashes and resumes from its journal" means the handle is dropped and the file reopened,
with any unconfirmed lease released and every uncommitted plan gone.

The mock cloud (`src/sim/server.rs`) models the four behaviours the engine has to survive and
nothing else: a **feed with a cursor**, **412 preconditions**, **tombstones** that are never purged
(D23 puts the floor at 90 days), and a **stability lag**, so a device that writes and immediately
polls does not see its own echo — the property that lets a naive engine pass a test and fail in
production.

The scheduler picks a device and an action each step from the seed, which is what reorders
requests: device A's upload lands between device B's plan and device B's write. It injects
transient failures, crashes and clock jumps at configurable rates.

Named scenarios, each asserting convergence across both devices **and** the cloud plus no data
loss:

| Test | What it drives |
|---|---|
| `two_devices_converge_with_the_cloud_under_interleaving_failures_and_crashes` | the general case, including two devices creating the same path with different content |
| `the_delete_vs_modify_race_never_destroys_either_version` | one device deletes a path while the other edits it; neither byte stream may be destroyed |
| `a_rename_storm_converges_without_losing_content` | six simultaneous moves across both devices |
| `a_crashing_device_resumes_from_its_journal` | a crash every fifth step; the test fails if the hazard never fired, so it cannot pass vacuously |
| `a_precondition_failure_is_survived_and_neither_version_is_lost` | a deliberate 412 |
| `the_feed_withholds_events_until_they_are_stable` | the stability lag is real |
| `a_deletion_leaves_a_tombstone_in_the_feed` | a delete is a tombstone row, never a missing one |

Every failure prints the seed; re-running with that seed replays the run exactly, because the PRNG
is hand-rolled (`src/sim/rng.rs`) rather than taken from a crate whose algorithm could change.

Two of the harness's five defects (3 and 5) originally rested only on the general interleaving test
at fixed seeds. Independent verification called that weaker evidence than a case that can be
reverted in isolation, and it was right: both now have dedicated regression tests
(`a_download_create_never_overwrites_a_file_the_scanner_has_not_seen`,
`a_conflict_whose_copy_already_exists_in_the_cloud_still_settles`), each proven red by reverting
its fix.

Soak runs on this Mac, 2026-09-13, after the whole fix round:

| Run | Result |
|---|---|
| `cargo test -p matrx-sync` | **59 passing, 0 failing** |
| `PROPTEST_CASES=200000 … --release --test planner_properties` | 12/12 green, 13.3 s |
| `PROPTEST_CASES=1000000 … --release --test planner_properties` | 12/12 green, 62.7 s |
| `SIM_SEEDS=200 … --release --test simulation` | 10/10 green, 210 seeds, 59.4 s |

**The harness is a mock and its README says so.** It proves the planner and the executor's shape.
Product evidence is FS-V2, on real machines with real files.
