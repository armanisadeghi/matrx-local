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

### The one clause that could not be asserted as literally written — ESCALATED

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

This reading is recorded as a **spec gap for amendment**, not as a licence taken quietly.

A second gap: SPEC-ENGINE §2 gives the mass-delete breaker two knobs,
`sync.mass_delete_percent` (50) and `sync.mass_delete_count` (1000), and never says how they
combine. They are combined with **AND** — see `planner::plan`'s documentation for why that is the
only combinator under which both defaults are individually coherent. Also escalated.

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

### Not found

Nothing else. The 100,000-case soak (`PROPTEST_CASES=100000`, release, all four properties, three
directions) passed with no failures after those two fixes — run on this Mac on 2026-09-13, 24.8 s
wall. That is a statement about the **planner**, and about the model of an executor in
`src/sim/`. It is not product evidence: the harness is a mock and says so (SCOPE §6, D2).
