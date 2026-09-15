//! FS-C4 — the simulation harness's own tests.
//!
//! **Everything here runs against a MOCK.** The filesystem is in memory, the server is in memory,
//! and the clock is an integer. Green here proves the planner and the executor's shape. It is
//! never evidence that the product works on a real machine (SCOPE §6, D2); that is FS-V2's job.
//!
//! The journals, though, are real: each simulated device keeps a genuine SQLite journal in a
//! tempdir, so "the device crashes and resumes from its journal" means exactly that.
//!
//! Every failure prints the seed. Re-run with the same seed to replay the run exactly.

use matrx_sync::model::Direction;
use matrx_sync::sim::{Hazards, Simulation};
use matrx_sync::Knobs;

/// The seeds every scenario runs.
///
/// A fixed, checked-in set comes first: a test whose inputs change per run cannot be replayed by a
/// colleague reading the failure.
///
/// `SIM_SEEDS` adds more, and takes an **explicit range** so a soak can reach seeds nobody has
/// used. Hostile re-verification pointed out that the old bare-count form always extended from the
/// same offset, so a `SIM_SEEDS=100` run re-treads a `SIM_SEEDS=200` run's ground and a verifier
/// wanting genuinely fresh seeds had to write a throwaway harness. Three forms:
///
/// | `SIM_SEEDS` | Seeds added |
/// |---|---|
/// | `100` | `1_000_000 ..= 1_000_099` — the legacy form, kept so old commands still work |
/// | `7000000..7000100` | exactly that half-open range |
/// | `7000000+100` | 100 seeds from 7,000,000 |
fn seeds() -> Vec<u64> {
    let mut out: Vec<u64> = vec![1, 2, 3, 7, 11, 42, 101, 1_009, 65_537, 999_331];
    out.extend(extra_seeds());
    out
}

/// Parse `SIM_SEEDS` into the range it names. Unparseable input is a loud panic, not a silent
/// empty range: a soak that quietly ran zero extra seeds would report green for nothing.
fn extra_seeds() -> Vec<u64> {
    let Ok(spec) = std::env::var("SIM_SEEDS") else {
        return Vec::new();
    };
    let spec = spec.trim();
    if spec.is_empty() {
        return Vec::new();
    }
    const DEFAULT_OFFSET: u64 = 1_000_000;

    let Some(seeds) = parse_seed_spec(spec, DEFAULT_OFFSET) else {
        panic!(
            "SIM_SEEDS={spec:?} is not a count, a START..END range, or a START+COUNT offset; \
             refusing to run a soak that silently adds no seeds"
        );
    };
    seeds
}

/// The `SIM_SEEDS` grammar, as a pure function so it can be tested without touching the
/// process-wide environment that every other test in this binary reads concurrently.
fn parse_seed_spec(spec: &str, default_offset: u64) -> Option<Vec<u64>> {
    let (start, count) = if let Some((start, end)) = spec.split_once("..") {
        match (start.trim().parse::<u64>(), end.trim().parse::<u64>()) {
            (Ok(s), Ok(e)) if e >= s => (s, e - s),
            _ => return None,
        }
    } else if let Some((start, count)) = spec.split_once('+') {
        match (start.trim().parse::<u64>(), count.trim().parse::<u64>()) {
            (Ok(s), Ok(n)) => (s, n),
            _ => return None,
        }
    } else {
        (default_offset, spec.parse::<u64>().ok()?)
    };
    Some((start..start.saturating_add(count)).collect())
}

/// Seed one cloud file and one file on each device, then let the scheduler interleave everything.
fn seed_content(sim: &mut Simulation) {
    sim.devices[0].fs.write("shared.txt", "c-a0", 1);
    sim.devices[0].fs.write("only-a.txt", "c-a1", 1);
    sim.devices[0].fs.mkdir("folder", 1);
    sim.devices[0].fs.write("folder/deep.txt", "c-a2", 1);
    sim.devices[1].fs.write("only-b.txt", "c-b0", 1);
    // Both devices independently create the SAME path with DIFFERENT content — the first-run
    // collision every real fleet hits, and the one that produces a conflict copy.
    sim.devices[0].fs.write("both.txt", "c-a3", 1);
    sim.devices[1].fs.write("both.txt", "c-b1", 1);
}

#[test]
fn two_devices_converge_with_the_cloud_under_interleaving_failures_and_crashes() {
    for seed in seeds() {
        let dir = tempfile::tempdir().expect("tempdir");
        let mut sim = Simulation::new(seed, dir.path(), Direction::TwoWay, Knobs::default(), 2)
            .expect("build the simulation");
        seed_content(&mut sim);
        let before = sim.reachable_contents();

        let report = sim.run_and_settle(400, 24).expect("run");
        assert!(
            report.settled,
            "seed {seed}: the fleet never reached quiet — {report:?}"
        );

        let disagreements = sim.disagreements().expect("read state");
        assert!(
            disagreements.is_empty(),
            "seed {seed}: devices and cloud disagree: {disagreements:#?}\nreport {report:?}"
        );

        let after = sim.reachable_contents();
        for content in &before {
            assert!(
                after.contains(content),
                "seed {seed}: content {content} was lost\nreport {report:?}"
            );
        }
    }
}

/// The named delete-versus-modify race: one device deletes a path while the other changes it.
///
/// The requirement is not that a particular side wins — it is that **neither byte stream is
/// destroyed** and the user is told.
#[test]
fn the_delete_vs_modify_race_never_destroys_either_version() {
    for seed in seeds() {
        let dir = tempfile::tempdir().expect("tempdir");
        let mut sim = Simulation::new(seed, dir.path(), Direction::TwoWay, Knobs::default(), 2)
            .expect("build");
        // Both devices start from one agreed file.
        sim.devices[0].fs.write("race.txt", "c-original", 1);
        sim.run_and_settle(80, 16).expect("initial sync");
        assert_eq!(
            sim.devices[1].fs.content("race.txt"),
            Some("c-original"),
            "seed {seed}: the file never reached the second device"
        );

        // Now the race: A deletes, B edits, and the scheduler interleaves the two.
        sim.devices[0].fs.remove("race.txt", true);
        sim.devices[1].fs.write("race.txt", "c-edited", sim.clock + 1);

        let report = sim.run_and_settle(200, 24).expect("race");
        assert!(report.settled, "seed {seed}: never settled — {report:?}");

        let reachable = sim.reachable_contents();
        assert!(
            reachable.contains("c-edited"),
            "seed {seed}: the edit was destroyed by the delete — {report:?}"
        );
        // The version that was deleted is still recoverable from the OS trash, which is what
        // `sync.trash_local_deletes` is for. Deleting is allowed to remove it from the sync set;
        // it is not allowed to shred it.
        assert!(
            reachable.contains("c-original")
                || sim.devices.iter().any(|d| !d.fs.trash.is_empty()),
            "seed {seed}: the deleted version left no recoverable trace — {report:?}"
        );
        let disagreements = sim.disagreements().expect("read state");
        assert!(
            disagreements.is_empty(),
            "seed {seed}: after the race the fleet disagrees: {disagreements:#?}"
        );
    }
}

/// The named rename storm: a burst of moves on both devices at once.
///
/// The point is that a move is carried as a rename — identity preserved — and that a storm of them
/// still converges without losing a byte.
#[test]
fn a_rename_storm_converges_without_losing_content() {
    for seed in seeds() {
        let dir = tempfile::tempdir().expect("tempdir");
        let mut sim = Simulation::new(seed, dir.path(), Direction::TwoWay, Knobs::default(), 1)
            .expect("build");
        for i in 0..6 {
            sim.devices[0]
                .fs
                .write(&format!("n{i}.txt"), &format!("c{i}"), 1);
        }
        sim.run_and_settle(120, 16).expect("initial sync");
        let before = sim.reachable_contents();

        // The storm: every path moves, on alternating devices, with no pause between moves.
        for i in 0..6 {
            let d = i % sim.devices.len();
            sim.devices[d]
                .fs
                .rename(&format!("n{i}.txt"), &format!("moved-{i}.txt"));
        }

        let report = sim.run_and_settle(300, 32).expect("storm");
        assert!(report.settled, "seed {seed}: never settled — {report:?}");

        let after = sim.reachable_contents();
        for content in &before {
            assert!(
                after.contains(content),
                "seed {seed}: content {content} lost in the rename storm — {report:?}"
            );
        }
        let disagreements = sim.disagreements().expect("read state");
        assert!(
            disagreements.is_empty(),
            "seed {seed}: after the storm the fleet disagrees: {disagreements:#?}"
        );
    }
}

/// A device that crashes mid-run resumes from its journal rather than starting over — and the
/// replay is idempotent (I5).
#[test]
fn a_crashing_device_resumes_from_its_journal() {
    for seed in [5u64, 13, 77, 2_027] {
        let dir = tempfile::tempdir().expect("tempdir");
        let mut sim = Simulation::new(seed, dir.path(), Direction::TwoWay, Knobs::default(), 2)
            .expect("build");
        sim.hazards = Hazards {
            transient_per_mille: 150,
            crash_per_mille: 200, // one step in five
            time_jump_per_mille: 200,
        };
        seed_content(&mut sim);
        let before = sim.reachable_contents();

        let report = sim.run_and_settle(400, 32).expect("run");
        assert!(
            report.crashes > 0,
            "seed {seed}: the crash hazard never fired, so this test proved nothing"
        );
        assert!(report.settled, "seed {seed}: never settled — {report:?}");

        let after = sim.reachable_contents();
        for content in &before {
            assert!(
                after.contains(content),
                "seed {seed}: content {content} lost across a crash — {report:?}"
            );
        }
        assert!(
            sim.disagreements().expect("read state").is_empty(),
            "seed {seed}: divergence after crashes — {report:?}"
        );
    }
}

/// The stability lag is real: a device that writes and immediately polls does not see its own
/// write echoed back. An engine that depended on that echo would converge here and diverge in
/// production, so the harness refuses to hide it.
#[test]
fn the_feed_withholds_events_until_they_are_stable() {
    let dir = tempfile::tempdir().expect("tempdir");
    let mut sim =
        Simulation::new(1, dir.path(), Direction::TwoWay, Knobs::default(), 5).expect("build");
    sim.devices[0].fs.write("lagged.txt", "c0", 1);
    sim.devices[0].scan(1).expect("scan");
    sim.devices[0]
        .execute_one(&mut sim.server, 1, None)
        .expect("upload");

    let immediate = sim.devices[1].poll_feed(&sim.server, 2).expect("poll");
    assert_eq!(immediate, 0, "the feed must withhold an event inside the lag");

    let later = sim.devices[1].poll_feed(&sim.server, 20).expect("poll");
    assert!(later > 0, "the event must appear once the lag has passed");
}

/// Tombstones are never purged: a deletion stays readable in the feed (D23's retention floor is
/// 90 days, so nothing here may drop one).
#[test]
fn a_deletion_leaves_a_tombstone_in_the_feed() {
    let dir = tempfile::tempdir().expect("tempdir");
    let mut sim =
        Simulation::new(1, dir.path(), Direction::TwoWay, Knobs::default(), 0).expect("build");
    sim.devices[0].fs.write("doomed.txt", "c0", 1);
    sim.run_and_settle(80, 16).expect("sync");
    let before = sim.server.feed_len();

    sim.devices[0].fs.remove("doomed.txt", true);
    sim.run_and_settle(120, 16).expect("delete");

    assert!(
        sim.server.feed_len() > before,
        "the delete must append a tombstone event"
    );
    assert!(
        sim.server.live("doomed.txt").is_none(),
        "the row must be tombstoned, not live"
    );
    assert!(
        sim.server.full_tree().get("doomed.txt").is_some(),
        "the tombstone row itself must survive — a delete is never a missing row"
    );
}

/// `upload_only` and `download_only` runs converge to their own contracts, not to `two_way`'s.
#[test]
fn one_way_directions_converge_to_their_own_contract() {
    for (direction, seed) in [(Direction::UploadOnly, 3u64), (Direction::DownloadOnly, 4)] {
        let dir = tempfile::tempdir().expect("tempdir");
        let mut sim =
            Simulation::new(seed, dir.path(), direction, Knobs::default(), 1).expect("build");
        sim.devices[0].fs.write("a.txt", "c0", 1);
        let before = sim.reachable_contents();
        let report = sim.run_and_settle(200, 24).expect("run");
        assert!(
            report.settled,
            "{direction:?} seed {seed}: never settled — {report:?}"
        );
        let after = sim.reachable_contents();
        for c in &before {
            assert!(
                after.contains(c),
                "{direction:?} seed {seed}: content {c} lost — {report:?}"
            );
        }
    }
}

/// A 412 is survived, not clobbered: two devices write the same path in the same tick, one of them
/// loses the precondition, and neither version is destroyed.
#[test]
fn a_precondition_failure_is_survived_and_neither_version_is_lost() {
    let dir = tempfile::tempdir().expect("tempdir");
    let mut sim =
        Simulation::new(9, dir.path(), Direction::TwoWay, Knobs::default(), 0).expect("build");

    // Both devices believe the path is free and both try to create it, with no feed poll between
    // them. Exactly one can win.
    sim.devices[0].fs.write("contested.txt", "c-a", 1);
    sim.devices[1].fs.write("contested.txt", "c-b", 1);
    sim.devices[0].scan(1).expect("scan a");
    sim.devices[1].scan(1).expect("scan b");
    let first = sim.devices[0]
        .execute_one(&mut sim.server, 1, None)
        .expect("a writes");
    let second = sim.devices[1]
        .execute_one(&mut sim.server, 1, None)
        .expect("b writes");
    assert_eq!(first, matrx_sync::sim::StepOutcome::Done);
    assert_eq!(
        second,
        matrx_sync::sim::StepOutcome::Raced,
        "the second writer must be refused on the precondition, not allowed to clobber"
    );

    let report = sim.run_and_settle(200, 24).expect("settle");
    assert!(report.settled, "never settled — {report:?}");
    let reachable = sim.reachable_contents();
    assert!(
        reachable.contains("c-a") && reachable.contains("c-b"),
        "both versions must survive the race — {report:?}, reachable {reachable:?}"
    );
    assert!(
        sim.disagreements().expect("read state").is_empty(),
        "the fleet must still agree afterwards — {report:?}"
    );
}

// --------------------------------- dedicated regressions for TESTING.md defects 3 and 5
//
// Independent verification noted that both rested only on the general interleaving test at fixed
// seeds, which is weaker evidence than a case that can be reverted in isolation. These two can.

/// Defect 3 — **data loss**. A device that polls the change feed before it walks the disk plans
/// `Download{Create}` for a path it believes is empty. If the user already has a *different* file
/// there, the write destroyed it: no conflict copy, no conflict row, nothing on screen.
///
/// The pre-image for a create is **absence**, and I3 applies to it like any other destructive
/// write.
#[test]
fn a_download_create_never_overwrites_a_file_the_scanner_has_not_seen() {
    let dir = tempfile::tempdir().expect("tempdir");
    let mut sim =
        Simulation::new(21, dir.path(), Direction::TwoWay, Knobs::default(), 0).expect("build");

    // Device A publishes `contested.txt` = c-a.
    sim.devices[0].fs.write("contested.txt", "c-a", 1);
    sim.devices[0].scan(1).expect("scan a");
    sim.devices[0]
        .execute_one(&mut sim.server, 1, None)
        .expect("upload");

    // Device B already has a DIFFERENT file at that path — and polls the feed before scanning,
    // so its `tree_local` is empty and the plan believes the path is free.
    sim.devices[1].fs.write("contested.txt", "c-b", 2);
    sim.devices[1].poll_feed(&sim.server, 10).expect("poll");
    let plan = sim.devices[1].current_plan().expect("plan");
    assert!(
        plan.ops.iter().any(|o| matches!(
            o,
            matrx_sync::PlanOp::Download { change: matrx_sync::planner::Change::Create, .. }
        )),
        "the setup must actually produce a Download{{Create}}, or this proves nothing: {:?}",
        plan.ops
    );

    let outcome = sim.devices[1]
        .execute_one(&mut sim.server, 10, None)
        .expect("execute");
    assert_eq!(
        outcome,
        matrx_sync::sim::StepOutcome::Raced,
        "the create must abort on finding something there, not write over it"
    );
    assert_eq!(
        sim.devices[1].fs.content("contested.txt"),
        Some("c-b"),
        "the user's file must be untouched"
    );

    // And the fleet still converges, with both versions kept.
    let report = sim.run_and_settle(200, 24).expect("settle");
    assert!(report.settled, "never settled — {report:?}");
    let reachable = sim.reachable_contents();
    assert!(
        reachable.contains("c-a") && reachable.contains("c-b"),
        "both versions must survive — reachable {reachable:?}"
    );
}

/// Defect 5 — **silent stall**. Conflict resolution livelocked when the losing copy already
/// existed in the cloud (a crash between its upload and its confirmation, or the other device
/// having written it): the bolted-on upload carried no precondition, drew a 412 every time, the
/// plan was discarded, and the identical plan came back.
///
/// The copy is now an ordinary local-only path that the next round uploads with a real
/// precondition, so the run reaches quiet.
#[test]
fn a_conflict_whose_copy_already_exists_in_the_cloud_still_settles() {
    let dir = tempfile::tempdir().expect("tempdir");
    let mut sim =
        Simulation::new(22, dir.path(), Direction::TwoWay, Knobs::default(), 0).expect("build");

    // Put the conflict copy's exact name in the cloud first, from the device that will lose.
    let copy = "c (conflicted copy from device-a 2026-09-13).txt";
    sim.devices[0].fs.write(copy, "c-old", 1);
    sim.run_and_settle(80, 16).expect("publish the copy");
    assert!(
        sim.server.live(copy).is_some(),
        "the copy name must already be taken in the cloud"
    );

    // Now force a conflict on `c.txt` from that same device, on the same day.
    sim.devices[0].fs.write("c.txt", "c-a", sim.clock + 1);
    sim.devices[1].fs.write("c.txt", "c-b", sim.clock + 1);
    let before = sim.reachable_contents();

    let report = sim.run_and_settle(200, 24).expect("run");
    assert!(
        report.settled,
        "the conflict livelocked instead of settling — {report:?}"
    );
    let after = sim.reachable_contents();
    for c in &before {
        assert!(after.contains(c), "content {c} lost — {report:?}");
    }
    assert!(
        sim.disagreements().expect("read state").is_empty(),
        "the fleet must agree afterwards — {report:?}"
    );
}

/// The mass-delete circuit breaker, end to end — a permanent named scenario.
///
/// Hostile re-verification wrote this because no simulation scenario seeded a bulk delete, which
/// is the hazard that matters most for data loss: an `rm -rf`, a drive that unmounted under the
/// sync root, ransomware. The two knobs guarding it were asserted only as pure planner units, and
/// nothing proved the breaker stops a real fleet mid-flight and leaves the cloud whole.
///
/// Twelve files synced, then all twelve gone from one device at once. Both halves of the ruled
/// rule are asserted, because both are the rule (SPEC-ENGINE §2, amendment 2):
///
/// * with the floor lowered so the percentage arm bites, the mapping **suspends** and the cloud
///   keeps every file;
/// * at the shipped defaults a twelve-file folder is **not** protected — 100% clears the
///   percentage arm but 12 is under the floor of 20 — so the deletion propagates. That is the
///   ruled behaviour, not a defect, and it is asserted so nobody later mistakes it for one. It is
///   also why the floor is a knob.
#[test]
fn the_mass_delete_breaker_stops_a_real_fleet_and_leaves_the_cloud_whole() {
    for seed in seeds().into_iter().take(12) {
        // (a) The breaker bites once the folder is inside the knobs' reach.
        let dir = tempfile::tempdir().expect("tempdir");
        let guarded = Knobs {
            // Its minimum legal value — knobs are knobs.
            mass_delete_count: 10,
            mass_delete_min_count: 10,
            ..Knobs::default()
        };
        let mut sim =
            Simulation::new(seed, dir.path(), Direction::TwoWay, guarded, 1).expect("build");
        for i in 0..12 {
            sim.devices[0]
                .fs
                .write(&format!("doc{i}.txt"), &format!("c{i}"), 1);
        }
        sim.run_and_settle(200, 24).expect("initial sync");
        assert_eq!(
            sim.server.live_contents().len(),
            12,
            "seed {seed}: the twelve files must reach the cloud first"
        );

        for i in 0..12 {
            sim.devices[0].fs.remove(&format!("doc{i}.txt"), true);
        }
        let report = sim.run_and_settle(200, 24).expect("the wipe");

        assert!(
            sim.devices[0].suspended.is_some(),
            "seed {seed}: twelve of twelve deleted and the breaker did not trip — {report:?}"
        );
        assert_eq!(
            sim.server.live_contents().len(),
            12,
            "seed {seed}: the cloud was emptied despite the suspension — {report:?}"
        );
        for i in 0..12 {
            assert!(
                sim.reachable_contents().contains(&format!("c{i}")),
                "seed {seed}: content c{i} lost — {report:?}"
            );
        }

        // (b) At the shipped defaults the same wipe propagates, because 12 is under the floor of
        // 20. This is SPEC-ENGINE §2's own worked example and its own reason for the knob.
        let dir = tempfile::tempdir().expect("tempdir");
        let mut sim = Simulation::new(seed, dir.path(), Direction::TwoWay, Knobs::default(), 1)
            .expect("build");
        for i in 0..12 {
            sim.devices[0]
                .fs
                .write(&format!("doc{i}.txt"), &format!("c{i}"), 1);
        }
        sim.run_and_settle(200, 24).expect("initial sync");
        for i in 0..12 {
            sim.devices[0].fs.remove(&format!("doc{i}.txt"), true);
        }
        sim.run_and_settle(200, 24).expect("the wipe");
        assert!(
            sim.devices[0].suspended.is_none(),
            "seed {seed}: a twelve-file folder is below the default floor and must NOT suspend"
        );
        assert!(
            sim.server.live_contents().is_empty(),
            "seed {seed}: the deletion must propagate at defaults"
        );
        // Nothing is shredded even then: the local deletes went to the OS trash.
        assert_eq!(
            sim.devices[0].fs.trash.len(),
            12,
            "seed {seed}: every propagated delete must still be recoverable locally"
        );
    }
}

/// `SIM_SEEDS` parsing itself. A soak that silently adds no seeds reports green for nothing, and
/// the re-verifier had to write a throwaway harness because the old form could not reach a fresh
/// range at all.
#[test]
fn sim_seeds_takes_a_count_a_range_or_an_offset() {
    // The legacy bare count, from the default offset.
    assert_eq!(
        parse_seed_spec("3", 1_000_000),
        Some(vec![1_000_000, 1_000_001, 1_000_002])
    );
    // An explicit half-open range — genuinely fresh seeds, reachable at last.
    assert_eq!(
        parse_seed_spec("7000000..7000003", 1_000_000),
        Some(vec![7_000_000, 7_000_001, 7_000_002])
    );
    // A start plus a count.
    assert_eq!(
        parse_seed_spec("7000000+2", 1_000_000),
        Some(vec![7_000_000, 7_000_001])
    );
    // Whitespace is tolerated; nonsense is not swallowed.
    assert_eq!(parse_seed_spec(" 1 .. 3 ", 0), Some(vec![1, 2]));
    assert_eq!(parse_seed_spec("nonsense", 0), None);
    assert_eq!(parse_seed_spec("5..1", 0), None, "a reversed range is a typo");
    assert_eq!(parse_seed_spec("1..x", 0), None);

    // And the checked-in set always runs, whatever the environment says.
    let base = seeds();
    assert!(base.contains(&1) && base.contains(&999_331), "{base:?}");
}
