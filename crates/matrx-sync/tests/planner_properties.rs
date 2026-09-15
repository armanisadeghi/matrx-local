//! FS-C3 property tests — SPEC-ENGINE §4.3's four CanopyCheck invariants, per direction.
//!
//! Random three-tree configurations are driven through the planner to a fixed point and the
//! invariants are read off the resulting trees. `proptest` shrinks every failure to a minimal
//! counter-example and writes the failing seed to
//! `tests/planner_properties.proptest-regressions`, which is checked in, so a fixed bug can never
//! silently come back.
//!
//! Case count: 10,000 per property by default (CI mode). Override with `PROPTEST_CASES`; the
//! 100,000-case local soak is recorded in `TESTING.md`.
//!
//! Where a spec clause carries its own stated exception — `upload_only` does not create
//! remote-only paths locally, `download_only` never overwrites a flagged local edit — the
//! assertion honours the exception. Every exemption is named in `exempt_paths` and explained in
//! `TESTING.md`; nothing is exempted to make a test pass.

use matrx_sync::model::{Direction, LocalNode, RemoteNode, SyncedNode};
use matrx_sync::planner::{plan, PlanContext};
use matrx_sync::sim::World;
use matrx_sync::Knobs;
use proptest::prelude::*;
use std::collections::BTreeSet;
use std::panic::{catch_unwind, AssertUnwindSafe};

/// The path pool. It deliberately contains a case collision (`a.txt` / `A.txt`), a name Windows
/// reserves (`CON.txt`), a path inside a folder, an **NFC/NFD twin pair** (`café.txt` spelled both
/// ways — one filesystem entry on APFS and NTFS), and a **conflict-copy-shaped name** for the
/// device and date this harness plans with, so a second conflict on `x.txt` lands on a name that
/// is already taken. The last two were added after independent verification found both classes
/// structurally unreachable by the old pool (F1, F5).
const PATHS: &[&str] = &[
    "a.txt",
    "A.txt",
    "CON.txt",
    "d/c.txt",
    "x.txt",
    "x (conflicted copy from device-a 2026-09-13).txt",
    "cafe\u{301}.txt",
    "caf\u{e9}.txt",
];

/// Content ids. Small on purpose: collisions between generated contents are the interesting cases.
const CONTENTS: &[&str] = &["c0", "c1", "c2"];

#[derive(Debug, Clone)]
struct Cell {
    local: Option<usize>,
    remote: Option<usize>,
    synced: Option<(usize, usize)>,
    unhashed: bool,
    flagged: bool,
}

fn cell_strategy() -> impl Strategy<Value = Cell> {
    (
        prop::option::of(0..CONTENTS.len()),
        prop::option::of(0..CONTENTS.len()),
        prop::option::of((0..CONTENTS.len(), 0..CONTENTS.len())),
        any::<bool>(),
        any::<bool>(),
    )
        .prop_map(|(local, remote, synced, unhashed, flagged)| Cell {
            local,
            remote,
            synced,
            unhashed,
            flagged,
        })
}

fn scenario_strategy() -> impl Strategy<Value = (Vec<Cell>, Direction)> {
    (
        prop::collection::vec(cell_strategy(), PATHS.len()),
        prop_oneof![
            Just(Direction::TwoWay),
            Just(Direction::UploadOnly),
            Just(Direction::DownloadOnly),
        ],
    )
}

fn build(cells: &[Cell]) -> World {
    let mut w = World::new();
    for (i, (path, cell)) in PATHS.iter().zip(cells).enumerate() {
        let inode = format!("inode-{i}");
        if let Some(c) = cell.local {
            let hash = CONTENTS[c].to_string();
            w.local.insert(
                (*path).to_string(),
                LocalNode {
                    path_nfc: (*path).to_string(),
                    is_dir: false,
                    size: Some(10 + i as i64),
                    mtime_ns: Some(1_000 + i as i64),
                    volume_id: Some("vol-local".to_string()),
                    file_id: Some(inode.clone()),
                    // I9: an unhashed row is "not yet known". The harness satisfies the planner's
                    // hash request rather than letting it guess from (size, mtime).
                    content_hash: if cell.unhashed { None } else { Some(hash.clone()) },
                    scanned_at: Some("2026-09-13T00:00:00Z".to_string()),
                },
            );
            if cell.unhashed {
                w.unhashed.insert((*path).to_string(), hash);
            }
        }
        if let Some(c) = cell.remote {
            w.remote.insert(
                (*path).to_string(),
                RemoteNode {
                    path_nfc: (*path).to_string(),
                    is_dir: false,
                    size: Some(10 + i as i64),
                    remote_file_id: Some(format!("file-{i}")),
                    remote_folder_id: None,
                    remote_version: Some(1),
                    checksum: Some(CONTENTS[c].to_string()),
                    client_modified_at: Some("2026-09-13T00:00:00Z".to_string()),
                    origin_device_id: Some("device-b".to_string()),
                    deleted_at: None,
                    seen_at: Some("2026-09-13T00:00:00Z".to_string()),
                },
            );
        }
        if let Some((lc, rc)) = cell.synced {
            // A synced row records ONE state both sides confirmed, so its local hash and its
            // server checksum are the SAME bytes' SHA-256 (invariants I1/I2). They may differ
            // only on a `local_edit_flagged` row, which is exactly a preserved local edit (D6).
            // Generating them independently manufactured rows no correct daemon can write, and
            // the planner was being asked to converge an impossible past.
            let rc = if cell.flagged { rc } else { lc };
            w.synced.insert(
                (*path).to_string(),
                SyncedNode {
                    path_nfc: (*path).to_string(),
                    is_dir: false,
                    size: Some(10 + i as i64),
                    mtime_ns: Some(1_000 + i as i64),
                    volume_id: Some("vol-local".to_string()),
                    file_id: Some(inode),
                    content_hash: Some(CONTENTS[lc].to_string()),
                    remote_file_id: format!("file-{i}"),
                    remote_version: 1,
                    checksum: Some(CONTENTS[rc].to_string()),
                    local_edit_flagged: cell.flagged,
                    synced_at: "2026-09-13T00:00:00Z".to_string(),
                },
            );
        }
    }
    w.next_id = 100;
    w
}

struct RunOutcome {
    world: World,
    rounds: usize,
    bound: usize,
    suspended: bool,
}

/// Drive the planner to a fixed point. Returns `Err` with the panic message if any generated input
/// made the planner panic (SPEC-ENGINE §4.3, "No panic").
fn run(mut world: World, direction: Direction, knobs: &Knobs) -> Result<RunOutcome, String> {
    let distinct_paths: BTreeSet<String> = world
        .local
        .paths()
        .chain(world.remote.paths())
        .chain(world.synced.paths())
        .cloned()
        .collect();
    // SPEC-ENGINE §4.3: a fixed point in ≤ N rounds, N = 4 × distinct paths.
    let bound = 4 * distinct_paths.len().max(1);
    let mut rounds = 0usize;
    loop {
        let ctx = PlanContext {
            device_name: "device-a".to_string(),
            today: "2026-09-13".to_string(),
            // H1: every run of the loop is one rolling window, so what this world has already
            // deleted counts against the plan being built.
            recent_deletions: world.executed_deletions,
            open_conflicts: world.open_conflicts(),
        };
        let local = world.local.clone();
        let remote = world.remote.clone();
        let synced = world.synced.clone();
        let p = catch_unwind(AssertUnwindSafe(|| {
            plan(&local, &remote, &synced, direction, knobs, &ctx)
        }))
        .map_err(|e| {
            let msg = e
                .downcast_ref::<&str>()
                .map(|s| (*s).to_string())
                .or_else(|| e.downcast_ref::<String>().cloned())
                .unwrap_or_else(|| "non-string panic payload".to_string());
            format!("the planner panicked: {msg}")
        })?;
        if p.suspended.is_some() {
            // A suspended mapping is a terminal state, not a loop: the circuit breaker held and
            // the plan carried nothing else.
            assert!(p.ops.is_empty(), "a suspended plan must carry no ops");
            return Ok(RunOutcome {
                world,
                rounds,
                bound,
                suspended: true,
            });
        }
        // G1, as a property over EVERY plan of every run: the losing bytes must be set aside
        // before they are replaced, under a name a filesystem can actually create. A plan that
        // downloads over a locally-changed file without a validly-named conflict copy in the same
        // plan is data loss the moment it executes on a real volume — and a mock filesystem with
        // no name limits can never notice.
        no_unprotected_overwrite(&local, &remote, &synced, &p, knobs)?;
        // H1, as a property over every run: no SEQUENCE of plans inside one window may delete more
        // than the thresholds allow without a suspension. Counting one plan at a time let 38 of 40
        // files through in two instalments.
        if p.suspended.is_none() {
            let would_delete = world.executed_deletions
                + p.ops.iter().filter(|o| o.is_destructive()).count();
            let denominator = world.synced.len() + world.executed_deletions;
            if breaker_should_trip(would_delete, denominator, knobs) {
                return Err(format!(
                    "{would_delete} cumulative deletions of {denominator} tracked were planned \
                     across this window with no suspension"
                ));
            }
        }
        if p.is_empty() {
            return Ok(RunOutcome {
                world,
                rounds,
                bound,
                suspended: false,
            });
        }
        if rounds > bound {
            return Ok(RunOutcome {
                world,
                rounds,
                bound,
                suspended: false,
            });
        }
        world.apply(&p);
        rounds += 1;
    }
}

/// The breaker's rule, restated independently of the planner so the property is a real check and
/// not the implementation compared with itself.
fn breaker_should_trip(deleted: usize, tracked: usize, knobs: &Knobs) -> bool {
    if deleted == 0 || tracked == 0 {
        return false;
    }
    let absolute = deleted >= knobs.mass_delete_count as usize;
    let proportional = deleted * 100 >= tracked * knobs.mass_delete_percent as usize
        && deleted >= knobs.mass_delete_min_count as usize;
    absolute || proportional
}

/// Every `Download` that replaces locally-changed bytes must be accompanied, in the SAME plan, by
/// a `ConflictCopy` for that path whose name passes the crate's own name guard.
fn no_unprotected_overwrite(
    local: &matrx_sync::LocalTree,
    remote: &matrx_sync::RemoteTree,
    synced: &matrx_sync::SyncedTree,
    p: &matrx_sync::Plan,
    knobs: &Knobs,
) -> Result<(), String> {
    for op in &p.ops {
        let matrx_sync::PlanOp::Download { path, .. } = op else {
            continue;
        };
        let l = local.get(path);
        let locally_changed = match (l, synced.get(path), remote.get(path)) {
            (Some(l), Some(s), _) => l.content_hash != s.content_hash,
            (Some(l), None, Some(r)) => l.content_hash != r.checksum,
            _ => false,
        };
        if !locally_changed {
            continue;
        }
        let copy = p.ops.iter().find_map(|o| match o {
            matrx_sync::PlanOp::ConflictCopy {
                path: cp,
                copy_path,
                ..
            } if cp == path => Some(copy_path),
            _ => None,
        });
        match copy {
            None => {
                return Err(format!(
                    "{path}: the plan downloads over locally-changed bytes with no conflict copy"
                ))
            }
            Some(copy_path) => {
                if matrx_sync::naming::check_name(copy_path, knobs)
                    != matrx_sync::naming::NameVerdict::Ok
                {
                    return Err(format!(
                        "{path}: the conflict copy is planned at a name no filesystem can create: \
                         {copy_path}"
                    ));
                }
            }
        }
    }
    Ok(())
}

/// The contents the direction's own rules authorise losing, computed per **occurrence**.
///
/// SPEC-ENGINE §4.3's data-preservation row is read against the **live** sides: `tree_synced` is
/// bookkeeping of a past sync, not an extant copy — its own "Deletion case" sentence requires a
/// both-sides-deleted path to end in none of the three trees, so a propagated deletion removes
/// content by design, and so does every ordinary update (the side that did not change loses its
/// previous bytes). What the property asserts is the real guarantee: **no content is lost that the
/// direction did not authorise losing.**
///
/// It is computed per occurrence — one (path, side) pair — so a content id that also lives at some
/// untouched path is still required to survive. Nothing is exempted by coincidence.
///
/// Authorised per direction:
/// * `two_way` — the loser of an ordinary one-sided update, and a propagated deletion. A
///   both-modified case is NOT authorised: D7 keeps both copies.
/// * `upload_only` — the cloud's divergent bytes (local is authority, D6; the cloud keeps them in
///   its own trash, which is outside these three trees), and a propagated deletion when the knob
///   allows it.
/// * `download_only` — the local bytes only when they were unchanged since the last sync. A local
///   edit is flagged and preserved (D6), never authorised.
fn authorised_losses(before: &World, direction: Direction, knobs: &Knobs) -> BTreeSet<String> {
    let mut occurrences: Vec<(String, bool, String)> = Vec::new(); // (path, is_local, content)
    let mut at_risk: BTreeSet<(String, bool)> = BTreeSet::new();

    let mut paths: BTreeSet<String> = BTreeSet::new();
    paths.extend(before.local.paths().cloned());
    paths.extend(before.remote.paths().cloned());
    paths.extend(before.synced.paths().cloned());

    for path in &paths {
        let l = before.local.get(path);
        let r = before.remote.get(path).filter(|n| n.is_live());
        let s = before.synced.get(path);
        let local_hash = l.and_then(|n| {
            n.content_hash
                .clone()
                .or_else(|| before.unhashed.get(path).cloned())
        });
        let remote_hash = r.and_then(|n| n.checksum.clone());
        if let Some(h) = &local_hash {
            occurrences.push((path.clone(), true, h.clone()));
        }
        if let Some(h) = &remote_hash {
            occurrences.push((path.clone(), false, h.clone()));
        }
        let local_changed = s.is_some_and(|s| local_hash != s.content_hash);
        let remote_changed = s.is_some_and(|s| remote_hash != s.checksum);
        match direction {
            Direction::TwoWay => {
                if s.is_some() {
                    if remote_changed && !local_changed && l.is_some() {
                        at_risk.insert((path.clone(), true));
                    }
                    if local_changed && !remote_changed && r.is_some() {
                        at_risk.insert((path.clone(), false));
                    }
                    if r.is_none() && !local_changed && l.is_some() {
                        at_risk.insert((path.clone(), true));
                    }
                    if l.is_none() && !remote_changed && r.is_some() {
                        at_risk.insert((path.clone(), false));
                    }
                }
            }
            Direction::UploadOnly => {
                if r.is_some() && l.is_some() && local_hash != remote_hash {
                    at_risk.insert((path.clone(), false));
                }
                if l.is_none() && s.is_some() && r.is_some() && knobs.upload_only_propagates_deletes
                {
                    at_risk.insert((path.clone(), false));
                }
            }
            Direction::DownloadOnly => {
                let flagged = s.is_some_and(|s| s.local_edit_flagged);
                let local_edit = match s {
                    Some(s) => local_hash != s.content_hash,
                    None => l.is_some() && r.is_some() && local_hash != remote_hash,
                };
                if l.is_some() && !flagged && !local_edit {
                    if r.is_some() && local_hash != remote_hash {
                        at_risk.insert((path.clone(), true));
                    }
                    if r.is_none() && s.is_some() {
                        at_risk.insert((path.clone(), true));
                    }
                }
            }
        }
    }

    let mut authorised: BTreeSet<String> = BTreeSet::new();
    let contents: BTreeSet<String> = occurrences.iter().map(|(_, _, c)| c.clone()).collect();
    for c in contents {
        let all_at_risk = occurrences
            .iter()
            .filter(|(_, _, oc)| *oc == c)
            .all(|(p, is_local, _)| at_risk.contains(&(p.clone(), *is_local)));
        if all_at_risk {
            authorised.insert(c);
        }
    }
    authorised
}

/// Paths no convergence clause applies to, with the reason each is excluded.
///
/// Every entry is a stated exception of the spec itself, never a convenience.
fn exempt_paths(before: &World, after: &World, direction: Direction) -> BTreeSet<String> {
    let mut out: BTreeSet<String> = after
        // A path awaiting the user's decision is `needs_conflict_resolution`; the planner proposes
        // nothing for it by design.
        .conflicts
        .iter()
        .filter(|c| !c.resolved)
        .map(|c| c.path.clone())
        .collect();
    // Conflict copies are new paths the run created; they are asserted separately by data
    // preservation.
    for c in &after.conflicts {
        if let Some(p) = &c.conflict_copy_path {
            out.insert(p.clone());
        }
    }
    // D6, stated in SPEC-ENGINE §4.3's own convergence row: a preserved local edit keeps its
    // bytes and is never overwritten.
    for (p, n) in after.synced.iter() {
        if n.local_edit_flagged {
            out.insert(p.clone());
        }
    }
    match direction {
        Direction::TwoWay => {}
        Direction::UploadOnly => {
            // "Remote-only paths are not created locally" — the clause's own sentence.
            for p in before.remote.paths() {
                if !before.local.contains(p) && !before.synced.contains(p) {
                    out.insert(p.clone());
                }
            }
        }
        Direction::DownloadOnly => {
            // The cloud is authority, but a file it never held is not the cloud's to delete.
            for p in before.local.paths() {
                if !before.remote.contains(p) && !before.synced.contains(p) {
                    out.insert(p.clone());
                }
            }
        }
    }
    out
}

fn knobs_for(direction: Direction) -> Knobs {
    // The defaults of SPEC-ENGINE §2, unchanged. `upload_only`'s delete knob is exercised in both
    // positions by `upload_only_respects_the_delete_knob`.
    let _ = direction;
    Knobs::default()
}

proptest! {
    #![proptest_config(ProptestConfig {
        cases: cases(),
        max_shrink_iters: 4096,
        .. ProptestConfig::default()
    })]

    /// **Termination** (all directions) and **No panic** (all directions).
    #[test]
    fn terminates_without_panicking((cells, direction) in scenario_strategy()) {
        let knobs = knobs_for(direction);
        let outcome = run(build(&cells), direction, &knobs)
            .map_err(TestCaseError::fail)?;
        prop_assert!(
            outcome.rounds <= outcome.bound,
            "no fixed point in {} rounds (bound {}) for {:?} / {:?}",
            outcome.rounds, outcome.bound, direction, cells
        );
        // And the fixed point really is one: replanning changes nothing.
        let ctx = PlanContext {
            device_name: "device-a".to_string(),
            today: "2026-09-13".to_string(),
            recent_deletions: outcome.world.executed_deletions,
            open_conflicts: outcome.world.open_conflicts(),
        };
        let again = plan(
            &outcome.world.local, &outcome.world.remote, &outcome.world.synced,
            direction, &knobs, &ctx,
        );
        prop_assert!(
            again.is_empty() || again.suspended.is_some() || outcome.suspended,
            "the fixed point was not stable: {:?}", again.ops
        );
    }

    /// **Convergence**, per direction.
    #[test]
    fn converges_per_direction((cells, direction) in scenario_strategy()) {
        let knobs = knobs_for(direction);
        let before = build(&cells);
        let outcome = run(before.clone(), direction, &knobs)
            .map_err(TestCaseError::fail)?;
        if outcome.suspended || outcome.rounds > outcome.bound {
            return Ok(()); // termination is the other property's subject
        }
        let after = &outcome.world;
        let exempt = exempt_paths(&before, after, direction);

        let mut paths: BTreeSet<String> = BTreeSet::new();
        paths.extend(after.local.paths().cloned());
        paths.extend(after.remote.paths().cloned());
        paths.extend(after.synced.paths().cloned());

        for path in &paths {
            if exempt.contains(path) {
                continue;
            }
            let l = after.local.get(path);
            let r = after.remote.get(path).filter(|n| n.is_live());
            let s = after.synced.get(path);
            if l.is_some_and(|n| n.is_dir) || r.is_some_and(|n| n.is_dir) {
                continue; // directory rows are excluded from every content clause
            }
            match direction {
                Direction::TwoWay => {
                    let present = [l.is_some(), r.is_some(), s.is_some()];
                    prop_assert!(
                        present.iter().all(|p| *p) || present.iter().all(|p| !*p),
                        "two_way: {path} is present in some trees but not all: {present:?}\n\
                         cells {cells:?}"
                    );
                    if let (Some(l), Some(r), Some(s)) = (l, r, s) {
                        prop_assert_eq!(
                            (&l.content_hash, &r.checksum),
                            (&s.content_hash, &s.checksum),
                            "two_way: {} did not converge on content", path
                        );
                        prop_assert_eq!(
                            &l.content_hash, &r.checksum,
                            "two_way: {} local and remote content differ", path
                        );
                    }
                }
                Direction::UploadOnly => {
                    if let Some(l) = l {
                        prop_assert!(
                            r.is_some(),
                            "upload_only: {path} exists locally but not in the cloud\ncells {cells:?}"
                        );
                        prop_assert_eq!(
                            &l.content_hash, &r.expect("checked").checksum,
                            "upload_only: {} did not push the local content", path
                        );
                        prop_assert!(
                            s.is_some_and(|s| s.content_hash == l.content_hash),
                            "upload_only: {path} has no agreeing synced row"
                        );
                    } else if s.is_some() {
                        prop_assert!(
                            !knobs.upload_only_propagates_deletes,
                            "upload_only: {path} is gone locally and still tracked while deletes \
                             propagate"
                        );
                    }
                }
                Direction::DownloadOnly => {
                    if let Some(r) = r {
                        prop_assert!(
                            l.is_some(),
                            "download_only: {path} is in the cloud but not on disk\ncells {cells:?}"
                        );
                        prop_assert_eq!(
                            &l.expect("checked").content_hash, &r.checksum,
                            "download_only: {} did not take the cloud content", path
                        );
                    } else if let Some(s) = s {
                        prop_assert!(
                            s.local_edit_flagged,
                            "download_only: {path} is gone from the cloud and still tracked"
                        );
                    }
                }
            }
        }

        // D7: "both copies reach the cloud". The generic clause exempts conflict-copy paths, so
        // without this nothing in the suite would catch a regression that left a copy local-only.
        // Independent verification named the hole (F7).
        if direction == Direction::TwoWay {
            for c in &after.conflicts {
                let Some(copy) = &c.conflict_copy_path else {
                    continue;
                };
                prop_assert!(
                    after.local.contains(copy)
                        && after.remote.contains(copy)
                        && after.synced.contains(copy),
                    "two_way: the conflict copy {copy} did not reach all three trees\ncells {cells:?}"
                );
            }
        }
    }

    /// **Data preservation** (all directions).
    #[test]
    fn preserves_data((cells, direction) in scenario_strategy()) {
        let knobs = knobs_for(direction);
        let before = build(&cells);
        let live_at_t0 = before.live_content();
        let outcome = run(before.clone(), direction, &knobs)
            .map_err(TestCaseError::fail)?;
        if outcome.suspended || outcome.rounds > outcome.bound {
            return Ok(());
        }
        let after = &outcome.world;
        let reachable = after.reachable_content();

        // No content that a live side held at t0 is ever lost: it is reachable at its own path, at
        // a conflict copy, or on a flagged row.
        let authorised = authorised_losses(&before, direction, &knobs);
        for content in live_at_t0.difference(&authorised) {
            prop_assert!(
                reachable.contains(content),
                "content {content} present at t0 is unreachable at the end\n\
                 direction {direction:?}\ncells {cells:?}"
            );
        }

        // Deletion case: a path present at t0 only in tree_synced (deleted on both sides) must end
        // in none of the three.
        let copies_written: BTreeSet<String> = after
            .conflicts
            .iter()
            .filter_map(|c| c.conflict_copy_path.clone())
            .collect();
        for path in before.synced.paths() {
            if before.local.contains(path) || before.remote.contains(path) {
                continue;
            }
            if copies_written.contains(path) {
                // The run wrote a NEW conflict copy at this name. A path re-created by a
                // legitimate new copy is not the deleted row surviving.
                continue;
            }
            prop_assert!(
                !after.local.contains(path)
                    && !after.remote.contains(path)
                    && !after.synced.contains(path),
                "{path} was deleted on both sides but survives\ncells {cells:?}"
            );
        }

        // Creation case: a path present in exactly one live tree and not in tree_synced ends
        // present in all three, subject to the direction's own stated exceptions.
        let exempt = exempt_paths(&before, after, direction);
        for path in PATHS {
            let p = (*path).to_string();
            if exempt.contains(&p) || before.synced.contains(&p) {
                continue;
            }
            let only_local = before.local.contains(&p) && !before.remote.contains(&p);
            let only_remote = before.remote.contains(&p) && !before.local.contains(&p);
            if !(only_local || only_remote) {
                continue;
            }
            let creates_locally = matches!(direction, Direction::TwoWay | Direction::DownloadOnly);
            let creates_remotely = matches!(direction, Direction::TwoWay | Direction::UploadOnly);
            if (only_local && !creates_remotely) || (only_remote && !creates_locally) {
                continue;
            }
            prop_assert!(
                after.local.contains(&p) && after.remote.contains(&p) && after.synced.contains(&p),
                "{p} was a creation and did not reach all three trees\n\
                 direction {direction:?}\ncells {cells:?}"
            );
        }
    }
}

fn cases() -> u32 {
    std::env::var("PROPTEST_CASES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(10_000)
}

// ------------------------------------------------------- hand-written cases

/// SPEC-ENGINE §2's worked examples for the breaker, as amended (amendment 2, 2026-09-13):
///
/// > suspend when `deleted_count >= sync.mass_delete_count` OR
/// > (`deleted_percent >= sync.mass_delete_percent` AND `deleted_count >= sync.mass_delete_min_count`)
///
/// Each row is the spec's own worked example, at the spec's own defaults.
#[test]
fn the_breakers_worked_examples_match_the_amended_rule() {
    // (tracked, deleted, expect_suspend, why)
    let cases: &[(usize, usize, bool, &str)] = &[
        (30, 22, true, "73% clears 50% and 22 clears the floor of 20"),
        (30, 16, false, "53% clears 50% but 16 is under the floor of 20"),
        (2, 1, false, "50% clears the percentage arm but 1 is far under the floor"),
        (100_000, 1_200, true, "1.2% fails the percentage arm; 1,200 clears the absolute 1,000"),
        (12, 12, false, "100% clears 50% but 12 is under the floor — the re-verify's case"),
    ];
    for (tracked, deleted, expect, why) in cases {
        let w = deletion_world(*tracked, *deleted);
        let p = plan(
            &w.local,
            &w.remote,
            &w.synced,
            Direction::TwoWay,
            &Knobs::default(),
            &PlanContext::default(),
        );
        assert_eq!(
            p.suspended.is_some(),
            *expect,
            "{tracked} tracked, {deleted} deleted: expected suspend={expect} ({why});              plan had {} ops",
            p.ops.len()
        );
        if *expect {
            assert!(p.ops.is_empty(), "a suspended plan carries nothing else");
        }
    }

    // The floor is a knob, not a constant: the twelve-file case suspends once an org lowers it,
    // which is the spec's own note on why it is a knob.
    let w = deletion_world(12, 12);
    let lowered = Knobs {
        mass_delete_min_count: 10,
        ..Knobs::default()
    };
    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &lowered,
        &PlanContext::default(),
    );
    let reason = p
        .suspended
        .expect("12 of 12 suspends once the floor is lowered to 10");
    assert_eq!(reason.honest_state(), "suspended_mass_delete");
}

/// A world of `tracked` synced files, `deleted` of which are gone from disk.
fn deletion_world(tracked: usize, deleted: usize) -> World {
    let mut w = World::new();
    for i in 0..tracked {
        let path = format!("f{i}.txt");
        w.remote.insert(
            path.clone(),
            RemoteNode {
                path_nfc: path.clone(),
                is_dir: false,
                size: Some(1),
                remote_file_id: Some(format!("file-{i}")),
                remote_folder_id: None,
                remote_version: Some(1),
                checksum: Some("c0".to_string()),
                client_modified_at: None,
                origin_device_id: None,
                deleted_at: None,
                seen_at: None,
            },
        );
        w.synced.insert(
            path.clone(),
            SyncedNode {
                path_nfc: path.clone(),
                is_dir: false,
                size: Some(1),
                mtime_ns: Some(1),
                volume_id: Some("vol-local".to_string()),
                file_id: Some(format!("inode-{i}")),
                content_hash: Some("c0".to_string()),
                remote_file_id: format!("file-{i}"),
                remote_version: 1,
                checksum: Some("c0".to_string()),
                local_edit_flagged: false,
                synced_at: "t".to_string(),
            },
        );
        if i >= deleted {
            w.local.insert(
                path.clone(),
                LocalNode {
                    path_nfc: path.clone(),
                    is_dir: false,
                    size: Some(1),
                    mtime_ns: Some(1),
                    volume_id: Some("vol-local".to_string()),
                    file_id: Some(format!("inode-{i}")),
                    content_hash: Some("c0".to_string()),
                    scanned_at: None,
                },
            );
        }
    }
    w.next_id = 9_000;
    w
}

/// The circuit breaker is a plan item, never an act (D8).
#[test]
fn the_mass_delete_breaker_suspends_instead_of_deleting() {
    let knobs = Knobs {
        mass_delete_count: 10,
        mass_delete_percent: 50,
        ..Knobs::default()
    };
    let mut w = World::new();
    // 20 tracked files, every one of them gone from disk.
    for i in 0..20 {
        let path = format!("f{i}.txt");
        w.remote.insert(
            path.clone(),
            RemoteNode {
                path_nfc: path.clone(),
                is_dir: false,
                size: Some(1),
                remote_file_id: Some(format!("file-{i}")),
                remote_folder_id: None,
                remote_version: Some(1),
                checksum: Some("c0".to_string()),
                client_modified_at: None,
                origin_device_id: None,
                deleted_at: None,
                seen_at: None,
            },
        );
        w.synced.insert(
            path.clone(),
            SyncedNode {
                path_nfc: path.clone(),
                is_dir: false,
                size: Some(1),
                mtime_ns: Some(1),
                volume_id: Some("vol-local".to_string()),
                file_id: Some(format!("inode-{i}")),
                content_hash: Some("c0".to_string()),
                remote_file_id: format!("file-{i}"),
                remote_version: 1,
                checksum: Some("c0".to_string()),
                local_edit_flagged: false,
                synced_at: "2026-09-13T00:00:00Z".to_string(),
            },
        );
    }
    let ctx = PlanContext::default();
    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &knobs,
        &ctx,
    );
    let reason = p.suspended.expect("the breaker must trip on 20/20 deletions");
    assert_eq!(reason.honest_state(), "suspended_mass_delete");
    assert!(p.ops.is_empty(), "a suspended plan carries nothing else");

    // And it does not trip on an ordinary deletion.
    let mut small = w.clone();
    for i in 1..20 {
        let path = format!("f{i}.txt");
        small.local.insert(
            path.clone(),
            LocalNode {
                path_nfc: path.clone(),
                is_dir: false,
                size: Some(1),
                mtime_ns: Some(1),
                volume_id: Some("vol-local".to_string()),
                file_id: Some(format!("inode-{i}")),
                content_hash: Some("c0".to_string()),
                scanned_at: None,
            },
        );
    }
    let p = plan(
        &small.local,
        &small.remote,
        &small.synced,
        Direction::TwoWay,
        &knobs,
        &ctx,
    );
    assert!(
        p.suspended.is_none(),
        "one deletion out of twenty is not a mass delete"
    );
}

/// D6: `upload_only` propagates local deletes only when the knob says so.
#[test]
fn upload_only_respects_the_delete_knob() {
    let mut w = World::new();
    w.remote.insert(
        "gone.txt".to_string(),
        RemoteNode {
            path_nfc: "gone.txt".to_string(),
            is_dir: false,
            size: Some(1),
            remote_file_id: Some("file-1".to_string()),
            remote_folder_id: None,
            remote_version: Some(3),
            checksum: Some("c0".to_string()),
            client_modified_at: None,
            origin_device_id: None,
            deleted_at: None,
            seen_at: None,
        },
    );
    w.synced.insert(
        "gone.txt".to_string(),
        SyncedNode {
            path_nfc: "gone.txt".to_string(),
            is_dir: false,
            size: Some(1),
            mtime_ns: Some(1),
            volume_id: Some("vol-local".to_string()),
            file_id: Some("inode-1".to_string()),
            content_hash: Some("c0".to_string()),
            remote_file_id: "file-1".to_string(),
            remote_version: 3,
            checksum: Some("c0".to_string()),
            local_edit_flagged: false,
            synced_at: "2026-09-13T00:00:00Z".to_string(),
        },
    );
    let ctx = PlanContext::default();

    let on = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::UploadOnly,
        &Knobs::default(),
        &ctx,
    );
    assert!(on.ops.iter().any(|o| o.is_destructive()), "{:?}", on.ops);

    let off = Knobs {
        upload_only_propagates_deletes: false,
        ..Knobs::default()
    };
    let p = plan(&w.local, &w.remote, &w.synced, Direction::UploadOnly, &off, &ctx);
    assert!(
        !p.ops.iter().any(|o| o.is_destructive()),
        "the knob is off; nothing may be deleted: {:?}",
        p.ops
    );
}

/// A rename keeps identity instead of re-transferring bytes.
#[test]
fn a_local_move_becomes_a_remote_rename() {
    let mut w = World::new();
    w.local.insert(
        "new.txt".to_string(),
        LocalNode {
            path_nfc: "new.txt".to_string(),
            is_dir: false,
            size: Some(1),
            mtime_ns: Some(1),
            volume_id: Some("vol-local".to_string()),
            file_id: Some("inode-7".to_string()),
            content_hash: Some("c0".to_string()),
            scanned_at: None,
        },
    );
    w.remote.insert(
        "old.txt".to_string(),
        RemoteNode {
            path_nfc: "old.txt".to_string(),
            is_dir: false,
            size: Some(1),
            remote_file_id: Some("file-7".to_string()),
            remote_folder_id: None,
            remote_version: Some(1),
            checksum: Some("c0".to_string()),
            client_modified_at: None,
            origin_device_id: None,
            deleted_at: None,
            seen_at: None,
        },
    );
    w.synced.insert(
        "old.txt".to_string(),
        SyncedNode {
            path_nfc: "old.txt".to_string(),
            is_dir: false,
            size: Some(1),
            mtime_ns: Some(1),
            volume_id: Some("vol-local".to_string()),
            file_id: Some("inode-7".to_string()),
            content_hash: Some("c0".to_string()),
            remote_file_id: "file-7".to_string(),
            remote_version: 1,
            checksum: Some("c0".to_string()),
            local_edit_flagged: false,
            synced_at: "2026-09-13T00:00:00Z".to_string(),
        },
    );
    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &Knobs::default(),
        &PlanContext::default(),
    );
    assert!(
        p.ops.iter().any(|o| matches!(
            o,
            matrx_sync::PlanOp::Rename { from, to, .. } if from == "old.txt" && to == "new.txt"
        )),
        "a move must be a rename, not an upload plus a delete: {:?}",
        p.ops
    );
    assert!(
        !p.ops.iter().any(|o| o.is_destructive()),
        "a rename destroys nothing: {:?}",
        p.ops
    );
}

/// The planner reads no clock: the same inputs give the same plan, byte for byte.
#[test]
fn the_planner_is_deterministic() {
    let cells: Vec<Cell> = (0..PATHS.len())
        .map(|i| Cell {
            local: Some(i % CONTENTS.len()),
            remote: Some((i + 1) % CONTENTS.len()),
            synced: Some((i % CONTENTS.len(), i % CONTENTS.len())),
            unhashed: i % 3 == 0,
            flagged: false,
        })
        .collect();
    let w = build(&cells);
    let ctx = PlanContext {
        device_name: "device-a".to_string(),
        today: "2026-09-13".to_string(),
        recent_deletions: 0,
        open_conflicts: BTreeSet::new(),
    };
    let a = plan(&w.local, &w.remote, &w.synced, Direction::TwoWay, &Knobs::default(), &ctx);
    let b = plan(&w.local, &w.remote, &w.synced, Direction::TwoWay, &Knobs::default(), &ctx);
    assert_eq!(a, b);
}

/// A name Windows cannot hold becomes a conflict row, never a silent rename.
#[test]
fn an_illegal_name_is_reported_not_renamed() {
    let mut w = World::new();
    w.local.insert(
        "CON.txt".to_string(),
        LocalNode {
            path_nfc: "CON.txt".to_string(),
            is_dir: false,
            size: Some(1),
            mtime_ns: Some(1),
            volume_id: Some("vol-local".to_string()),
            file_id: Some("inode-1".to_string()),
            content_hash: Some("c0".to_string()),
            scanned_at: None,
        },
    );
    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &Knobs::default(),
        &PlanContext::default(),
    );
    let kinds: Vec<_> = p
        .ops
        .iter()
        .filter_map(|o| match o {
            matrx_sync::PlanOp::RecordConflict { kind, .. } => Some(*kind),
            _ => None,
        })
        .collect();
    assert_eq!(kinds, vec![matrx_sync::model::ConflictKind::IllegalName]);
    assert!(
        !p.ops
            .iter()
            .any(|o| matches!(o, matrx_sync::PlanOp::Upload { .. })),
        "an unsendable name is not sent: {:?}",
        p.ops
    );
}

// ------------------------------------------- regressions from independent verification

/// One row of a hand-written world: the path, and its content id on each of the three trees.
type Row<'a> = (&'a str, Option<&'a str>, Option<&'a str>, Option<&'a str>);

/// Build a world from explicit (path, local, remote, synced) rows.
fn world_of(rows: &[Row<'_>]) -> World {
    let mut w = World::new();
    for (i, (path, local, remote, synced)) in rows.iter().enumerate() {
        if let Some(h) = local {
            w.local.insert(
                (*path).to_string(),
                LocalNode {
                    path_nfc: (*path).to_string(),
                    is_dir: false,
                    size: Some(1),
                    mtime_ns: Some(1),
                    volume_id: Some("vol-local".to_string()),
                    file_id: Some(format!("inode-{i}")),
                    content_hash: Some((*h).to_string()),
                    scanned_at: None,
                },
            );
        }
        if let Some(c) = remote {
            w.remote.insert(
                (*path).to_string(),
                RemoteNode {
                    path_nfc: (*path).to_string(),
                    is_dir: false,
                    size: Some(1),
                    remote_file_id: Some(format!("file-{i}")),
                    remote_folder_id: None,
                    remote_version: Some(1),
                    checksum: Some((*c).to_string()),
                    client_modified_at: None,
                    origin_device_id: None,
                    deleted_at: None,
                    seen_at: None,
                },
            );
        }
        if let Some(h) = synced {
            w.synced.insert(
                (*path).to_string(),
                SyncedNode {
                    path_nfc: (*path).to_string(),
                    is_dir: false,
                    size: Some(1),
                    mtime_ns: Some(1),
                    volume_id: Some("vol-local".to_string()),
                    file_id: Some(format!("inode-{i}")),
                    content_hash: Some((*h).to_string()),
                    remote_file_id: format!("file-{i}"),
                    remote_version: 1,
                    checksum: Some((*h).to_string()),
                    local_edit_flagged: false,
                    synced_at: "t".to_string(),
                },
            );
        }
    }
    w.next_id = 500;
    w
}

fn drive(world: &mut World, direction: Direction, knobs: &Knobs, rounds: usize) {
    for _ in 0..rounds {
        let ctx = PlanContext {
            device_name: "device-a".to_string(),
            today: "2026-09-13".to_string(),
            // H1: every run of the loop is one rolling window, so what this world has already
            // deleted counts against the plan being built.
            recent_deletions: world.executed_deletions,
            open_conflicts: world.open_conflicts(),
        };
        let p = plan(
            &world.local,
            &world.remote,
            &world.synced,
            direction,
            knobs,
            &ctx,
        );
        if p.is_empty() || p.suspended.is_some() {
            return;
        }
        world.apply(&p);
    }
}

/// F1, from independent verification — **data loss**. The verifier's exact case: a conflict copy
/// from earlier today already holds `c9`, and `x.txt` goes into conflict again. Without a
/// uniquifier the new copy rendered the same name, overwrote `c9`, and then pushed the overwrite
/// to the cloud with a valid precondition. `before = {c1, c2, c9}` became `after = {c1, c2}`.
#[test]
fn a_second_conflict_the_same_day_does_not_destroy_the_first_copy() {
    let copy = "x (conflicted copy from device-a 2026-09-13).txt";
    let mut w = world_of(&[
        ("x.txt", Some("c1"), Some("c2"), None),
        (copy, Some("c9"), Some("c9"), Some("c9")),
    ]);
    let before = w.live_content();
    assert!(before.contains("c9"));

    drive(&mut w, Direction::TwoWay, &Knobs::default(), 16);

    let after = w.reachable_content();
    for c in ["c1", "c2", "c9"] {
        assert!(
            after.contains(c),
            "{c} was destroyed by the second conflict; reachable = {after:?}"
        );
    }
    assert!(
        w.local.contains(copy) && w.local.get(copy).expect("row").content_hash.as_deref() == Some("c9"),
        "the earlier copy must still hold its own bytes"
    );
}

/// F5, from independent verification. NFC/NFD twins are one filesystem entry on APFS and NTFS;
/// I8 requires a `unicode_collision`, "never a silent rename".
#[test]
fn nfc_and_nfd_twins_become_a_unicode_collision_not_a_silent_clobber() {
    let mut w = world_of(&[
        ("cafe\u{301}.txt", Some("c1"), None, None),
        ("caf\u{e9}.txt", None, Some("c2"), None),
    ]);
    let ctx = PlanContext {
        device_name: "device-a".to_string(),
        today: "2026-09-13".to_string(),
        recent_deletions: 0,
        open_conflicts: BTreeSet::new(),
    };
    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &Knobs::default(),
        &ctx,
    );
    let kinds: Vec<_> = p
        .ops
        .iter()
        .filter_map(|o| match o {
            matrx_sync::PlanOp::RecordConflict { kind, .. } => Some(*kind),
            _ => None,
        })
        .collect();
    assert_eq!(
        kinds,
        vec![
            matrx_sync::model::ConflictKind::UnicodeCollision,
            matrx_sync::model::ConflictKind::UnicodeCollision
        ],
        "both spellings must be reported, as the spec's named kind: {:?}",
        p.ops
    );
    assert!(
        !p.ops
            .iter()
            .any(|o| matches!(o, matrx_sync::PlanOp::Upload { .. } | matrx_sync::PlanOp::Download { .. })),
        "neither spelling may be transferred while they collide: {:?}",
        p.ops
    );
    drive(&mut w, Direction::TwoWay, &Knobs::default(), 16);
    assert!(w.reachable_content().contains("c1") && w.reachable_content().contains("c2"));
}

/// F6, from independent verification. An `upload_only` directory gone from both sides must drop
/// its bookkeeping, not emit a tombstone with no target — an op no real executor can address,
/// which would sit `failed` on the mapping's queue forever.
#[test]
fn an_upload_only_directory_gone_from_both_sides_forgets_rather_than_tombstoning() {
    let mut w = World::new();
    w.synced.insert(
        "d".to_string(),
        SyncedNode {
            path_nfc: "d".to_string(),
            is_dir: true,
            size: None,
            mtime_ns: None,
            volume_id: Some("vol-local".to_string()),
            file_id: Some("inode-1".to_string()),
            content_hash: None,
            remote_file_id: "folder-1".to_string(),
            remote_version: 1,
            checksum: None,
            local_edit_flagged: false,
            synced_at: "t".to_string(),
        },
    );
    for direction in [Direction::UploadOnly, Direction::TwoWay] {
        let p = plan(
            &w.local,
            &w.remote,
            &w.synced,
            direction,
            &Knobs::default(),
            &PlanContext::default(),
        );
        assert_eq!(
            p.ops,
            vec![matrx_sync::PlanOp::ForgetSynced {
                path: "d".to_string()
            }],
            "{direction:?} must forget, not tombstone: {:?}",
            p.ops
        );
    }
}

/// F8, from independent verification. A case-only rename is one of the most common things a Mac
/// user does to a filename, and Dropbox performs it. Identity decides: a path whose
/// `(volume, inode)` matches the synced row, with unchanged content, is a move — not a permanent
/// conflict between the two spellings of one name.
#[test]
fn a_case_only_rename_is_a_rename_not_a_permanent_conflict() {
    let mut w = world_of(&[("a.txt", None, Some("c1"), Some("c1"))]);
    // The same inode now appears under the new spelling; the old spelling is gone from disk.
    w.local.insert(
        "A.txt".to_string(),
        LocalNode {
            path_nfc: "A.txt".to_string(),
            is_dir: false,
            size: Some(1),
            mtime_ns: Some(2),
            volume_id: Some("vol-local".to_string()),
            file_id: Some("inode-0".to_string()),
            content_hash: Some("c1".to_string()),
            scanned_at: None,
        },
    );
    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &Knobs::default(),
        &PlanContext::default(),
    );
    assert!(
        p.ops.iter().any(|o| matches!(
            o,
            matrx_sync::PlanOp::Rename { from, to, .. } if from == "a.txt" && to == "A.txt"
        )),
        "a case-only move must be a rename: {:?}",
        p.ops
    );
    assert!(
        !p.ops
            .iter()
            .any(|o| matches!(o, matrx_sync::PlanOp::RecordConflict { .. })),
        "and not a conflict the user has to resolve by hand: {:?}",
        p.ops
    );
    drive(&mut w, Direction::TwoWay, &Knobs::default(), 16);
    assert!(w.reachable_content().contains("c1"));
}

// ------------------------------- regressions from hostile re-verification (round 2)

/// G1, from hostile re-verification — **data loss**. The re-verifier's exact input: a 244-character
/// legal path in conflict. The template adds forty-odd characters, so the copy name overran
/// `sync.max_segment_chars` — and the plan carried the `Download` that replaces the local bytes
/// anyway. On a real volume the copy write fails and the replacement succeeds; the mock filesystem,
/// having no name limits, saw nothing.
#[test]
fn a_conflict_copy_is_never_planned_at_a_name_no_filesystem_can_create() {
    let knobs = Knobs::default();
    let long = format!("{}.txt", "s".repeat(240));
    let mut w = world_of(&[(long.as_str(), Some("c1"), Some("c2"), None)]);

    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &knobs,
        &PlanContext {
            device_name: "device-a".to_string(),
            today: "2026-09-13".to_string(),
            recent_deletions: 0,
            open_conflicts: BTreeSet::new(),
        },
    );

    for op in &p.ops {
        if let matrx_sync::PlanOp::ConflictCopy { copy_path, .. } = op {
            assert_eq!(
                matrx_sync::naming::check_name(copy_path, &knobs),
                matrx_sync::naming::NameVerdict::Ok,
                "the copy is planned at an uncreatable name: {copy_path}"
            );
        }
    }
    assert!(
        no_unprotected_overwrite(&w.local, &w.remote, &w.synced, &p, &knobs).is_ok(),
        "the local bytes must not be replaced without a validly-named copy: {:?}",
        p.ops
    );

    drive(&mut w, Direction::TwoWay, &knobs, 16);
    let after = w.reachable_content();
    assert!(
        after.contains("c1") && after.contains("c2"),
        "both versions must survive; reachable = {after:?}"
    );
}

/// G1's other half: when NO length of stem can be named, the path is quarantined with a named
/// conflict and the replacement is not planned at all.
#[test]
fn an_unnameable_conflict_copy_blocks_the_download_instead_of_accompanying_it() {
    // A knob tight enough that the template cannot fit at any stem length. Knobs are knobs.
    let knobs = Knobs {
        max_segment_chars: 12,
        ..Knobs::default()
    };
    let w = world_of(&[("x.txt", Some("c1"), Some("c2"), None)]);
    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &knobs,
        &PlanContext::default(),
    );
    assert!(
        !p.ops
            .iter()
            .any(|o| matches!(o, matrx_sync::PlanOp::Download { .. })),
        "nothing may replace the local bytes while they cannot be set aside: {:?}",
        p.ops
    );
    assert!(
        p.ops
            .iter()
            .any(|o| matches!(o, matrx_sync::PlanOp::RecordConflict { .. })),
        "and the user must be told why: {:?}",
        p.ops
    );
}

/// G2, from hostile re-verification — **data loss**. An earlier copy differing only in CASE. The
/// uniquifier's `taken` was byte-exact while every other name comparison in the crate folds, so
/// the new copy landed on the old one on any case-insensitive volume.
#[test]
fn a_conflict_copy_does_not_land_on_an_earlier_one_that_differs_only_in_case() {
    let knobs = Knobs::default();
    let existing = "x (Conflicted Copy From device-a 2026-09-13).txt";
    let mut w = world_of(&[
        ("x.txt", Some("c1"), Some("c2"), None),
        (existing, Some("c9"), Some("c9"), Some("c9")),
    ]);

    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &knobs,
        &PlanContext {
            device_name: "device-a".to_string(),
            today: "2026-09-13".to_string(),
            recent_deletions: 0,
            open_conflicts: BTreeSet::new(),
        },
    );
    for op in &p.ops {
        if let matrx_sync::PlanOp::ConflictCopy { copy_path, .. } = op {
            assert_ne!(
                matrx_sync::naming::collision_key(copy_path),
                matrx_sync::naming::collision_key(existing),
                "the new copy folds onto the existing one: {copy_path}"
            );
        }
    }

    drive(&mut w, Direction::TwoWay, &knobs, 16);
    let after = w.reachable_content();
    for c in ["c1", "c2", "c9"] {
        assert!(after.contains(c), "{c} was lost; reachable = {after:?}");
    }
}

/// G5, from hostile re-verification — **silent permanent divergence**. The re-verifier's exact
/// case: `a.txt` is synced at `c1` version 1; this device moves it to `A.txt` (same volume+inode,
/// content unchanged) while another device edits `a.txt` to `c2`, version 2.
///
/// `detect_renames` checked local identity, local content and that the destination was free — but
/// never that the SOURCE in the cloud was still what the synced row recorded, and `PlanOp::Rename`
/// carried no precondition for anything downstream to catch. The plan was one op with no conflict;
/// driving it to a fixed point left the device on `c1`, the cloud on `c2`, no further op ever
/// planned, and nothing said.
#[test]
fn a_rename_that_races_a_remote_edit_is_a_conflict_not_a_rename() {
    let mut w = World::new();
    // The cloud has moved on: version 2, content c2.
    w.remote.insert(
        "a.txt".to_string(),
        RemoteNode {
            path_nfc: "a.txt".to_string(),
            is_dir: false,
            size: Some(1),
            remote_file_id: Some("file-0".to_string()),
            remote_folder_id: None,
            remote_version: Some(2),
            checksum: Some("c2".to_string()),
            client_modified_at: None,
            origin_device_id: Some("device-b".to_string()),
            deleted_at: None,
            seen_at: None,
        },
    );
    // The synced row still records version 1 / c1 …
    w.synced.insert(
        "a.txt".to_string(),
        SyncedNode {
            path_nfc: "a.txt".to_string(),
            is_dir: false,
            size: Some(1),
            mtime_ns: Some(1),
            volume_id: Some("vol-local".to_string()),
            file_id: Some("inode-0".to_string()),
            content_hash: Some("c1".to_string()),
            remote_file_id: "file-0".to_string(),
            remote_version: 1,
            checksum: Some("c1".to_string()),
            local_edit_flagged: false,
            synced_at: "t".to_string(),
        },
    );
    // … and locally the file moved, unchanged, to A.txt.
    w.local.insert(
        "A.txt".to_string(),
        LocalNode {
            path_nfc: "A.txt".to_string(),
            is_dir: false,
            size: Some(1),
            mtime_ns: Some(2),
            volume_id: Some("vol-local".to_string()),
            file_id: Some("inode-0".to_string()),
            content_hash: Some("c1".to_string()),
            scanned_at: None,
        },
    );
    w.next_id = 700;

    let knobs = Knobs::default();
    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &knobs,
        &PlanContext::default(),
    );
    assert!(
        !p.ops
            .iter()
            .any(|o| matches!(o, matrx_sync::PlanOp::Rename { .. })),
        "a move whose source changed under it is not a move: {:?}",
        p.ops
    );

    drive(&mut w, Direction::TwoWay, &knobs, 24);

    // Neither byte stream is lost …
    let after = w.reachable_content();
    assert!(
        after.contains("c1") && after.contains("c2"),
        "both versions must survive; reachable = {after:?}"
    );
    // … the user is told …
    assert!(
        !w.conflicts.is_empty(),
        "the race must be reported, not settled silently"
    );
    // … and the fleet is not left permanently split with nothing planned.
    for (path, s) in w.synced.iter() {
        if s.local_edit_flagged || s.is_dir {
            continue;
        }
        assert_eq!(
            s.content_hash, s.checksum,
            "{path}: the model wrote a synced row confirm_op would refuse"
        );
    }
}

/// Every rename the planner emits carries its precondition — the property, not just the case.
#[test]
fn every_planned_rename_carries_its_precondition() {
    let mut w = world_of(&[("old.txt", None, Some("c1"), Some("c1"))]);
    w.local.insert(
        "new.txt".to_string(),
        LocalNode {
            path_nfc: "new.txt".to_string(),
            is_dir: false,
            size: Some(1),
            mtime_ns: Some(2),
            volume_id: Some("vol-local".to_string()),
            file_id: Some("inode-0".to_string()),
            content_hash: Some("c1".to_string()),
            scanned_at: None,
        },
    );
    let p = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &Knobs::default(),
        &PlanContext::default(),
    );
    let renames: Vec<_> = p
        .ops
        .iter()
        .filter_map(|o| match o {
            matrx_sync::PlanOp::Rename {
                expected_version,
                expected_checksum,
                ..
            } => Some((*expected_version, expected_checksum.clone())),
            _ => None,
        })
        .collect();
    assert_eq!(renames.len(), 1, "the move must be a rename: {:?}", p.ops);
    assert_eq!(
        renames[0],
        (Some(1), Some("c1".to_string())),
        "the rename must carry the source's version and checksum"
    );
}

/// H1, from the third hostile pass — **data loss**. The re-verifier's exact case: forty synced
/// files, nineteen deleted in one plan (47.5%, below the percentage arm; 19, below the floor of
/// 20), applied; then nineteen of the twenty-one survivors deleted in the next. Counting one plan
/// at a time, neither round trips either arm, and **38 of 40 files propagate to the cloud with no
/// suspension and no user-visible event of any kind**.
///
/// This is not exotic: the daemon plans on a `sync.watcher_debounce_ms` timer and the scanner walks
/// a large tree incrementally, so an `rm -rf`, a drive unmounting under the sync root, or
/// ransomware working alphabetically all reach the planner as a stream of small deletions. That
/// stream is the scenario the breaker exists for.
#[test]
fn a_wipe_split_across_two_plans_still_trips_the_breaker() {
    let knobs = Knobs::default();
    let mut w = deletion_world(40, 19);

    // Round one: nineteen gone. Below both arms on its own, so it is allowed — and executed.
    let ctx = PlanContext {
        recent_deletions: w.executed_deletions,
        ..PlanContext::default()
    };
    let first = plan(&w.local, &w.remote, &w.synced, Direction::TwoWay, &knobs, &ctx);
    assert!(
        first.suspended.is_none(),
        "nineteen of forty is below both arms and must not suspend on its own"
    );
    assert_eq!(
        first.ops.iter().filter(|o| o.is_destructive()).count(),
        19,
        "{:?}",
        first.ops
    );
    w.apply(&first);
    assert_eq!(w.executed_deletions, 19);

    // Round two: nineteen of the twenty-one survivors disappear.
    let survivors: Vec<String> = w.local.paths().cloned().collect();
    for path in survivors.iter().take(19) {
        w.local.remove(path);
    }
    let ctx = PlanContext {
        recent_deletions: w.executed_deletions,
        ..PlanContext::default()
    };
    let second = plan(&w.local, &w.remote, &w.synced, Direction::TwoWay, &knobs, &ctx);

    let reason = second
        .suspended
        .clone()
        .expect("38 of 40 across one window is a mass delete, however it was split");
    assert_eq!(reason.honest_state(), "suspended_mass_delete");
    assert!(
        second.ops.is_empty(),
        "a suspended plan carries nothing else: {:?}",
        second.ops
    );
    match reason {
        matrx_sync::planner::SuspendReason::MassDelete {
            recent_deletions,
            window_hours,
            ..
        } => {
            assert_eq!(recent_deletions, 19, "the surface must be able to say why");
            assert_eq!(window_hours, 24);
        }
    }

    // And the cloud still holds the 21 the first round left, because round two executed nothing.
    w.apply(&second);
    assert_eq!(
        w.remote.iter().filter(|(_, n)| n.is_live()).count(),
        21,
        "the suspension must stop the second instalment"
    );
}

/// Resuming a suspended mapping forgets the window — otherwise the user's "yes, go on" would be
/// refused again on the very next plan, and a legitimate large cleanup could never complete.
#[test]
fn resuming_a_suspended_mapping_resets_the_window() {
    let knobs = Knobs::default();
    let mut w = deletion_world(40, 19);
    let first = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &knobs,
        &PlanContext::default(),
    );
    w.apply(&first);

    let survivors: Vec<String> = w.local.paths().cloned().collect();
    for path in survivors.iter().take(19) {
        w.local.remove(path);
    }
    let suspended = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &knobs,
        &PlanContext {
            recent_deletions: w.executed_deletions,
            ..PlanContext::default()
        },
    );
    assert!(suspended.suspended.is_some());

    // The user looks at it and says go on. The window is cleared; the same work now proceeds.
    w.executed_deletions = 0;
    let resumed = plan(
        &w.local,
        &w.remote,
        &w.synced,
        Direction::TwoWay,
        &knobs,
        &PlanContext {
            recent_deletions: 0,
            ..PlanContext::default()
        },
    );
    assert!(
        resumed.suspended.is_none(),
        "after a resume the same deletions must go through"
    );
    assert!(resumed.ops.iter().any(|o| o.is_destructive()));
}
