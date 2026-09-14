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
/// reserves (`CON.txt`) and two paths inside one folder, so those classes are exercised by the
/// random generator rather than only by hand-written cases.
const PATHS: &[&str] = &["a.txt", "A.txt", "CON.txt", "d/c.txt", "d/e.txt", "f.txt"];

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
        for path in before.synced.paths() {
            if before.local.contains(path) || before.remote.contains(path) {
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
