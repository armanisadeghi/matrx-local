//! FS-L1 unit 2 — local change detection.
//!
//! The scheduler and the wake detector are pure, so these run without a clock, a thread or a
//! filesystem. That is the point: the behaviour that matters — what the daemon does when a laptop
//! has been shut for a weekend — is otherwise only reachable by waiting for a weekend.

use matrx_sync::watch::{RescanKnobs, RescanReason, RescanScheduler, WakeDetector, WatchEvent};

#[test]
fn a_mapping_that_has_never_been_scanned_is_due_immediately() {
    let s = RescanScheduler::new(RescanKnobs::default());
    assert!(
        s.periodic_due(0),
        "the daemon has just started and has no idea what happened while it was off; waiting an \
         hour to find out is not a sync engine"
    );
    assert_eq!(s.next_due_s(), None);
}

#[test]
fn the_periodic_cadence_is_the_knob_and_tightens_when_the_watcher_is_gone() {
    let knobs = RescanKnobs::default();
    assert_eq!((knobs.interval_min, knobs.interval_degraded_min), (60, 15));

    let mut s = RescanScheduler::new(knobs);
    s.note_rescan(1_000);
    assert_eq!(s.next_due_s(), Some(1_000 + 3_600));
    assert!(!s.periodic_due(1_000 + 3_599));
    assert!(s.periodic_due(1_000 + 3_600));

    // With the watcher exhausted the timer is the ONLY mechanism left, so it has to run more often.
    s.set_watcher_exhausted(true);
    assert_eq!(s.next_due_s(), Some(1_000 + 900));
    assert!(s.periodic_due(1_000 + 900));
}

#[test]
fn out_of_range_rescan_knobs_are_refused_not_clamped() {
    for bad in [
        RescanKnobs { interval_min: 4, ..RescanKnobs::default() },
        RescanKnobs { interval_min: 1441, ..RescanKnobs::default() },
        RescanKnobs { interval_degraded_min: 4, ..RescanKnobs::default() },
        RescanKnobs { interval_degraded_min: 121, ..RescanKnobs::default() },
    ] {
        assert!(bad.validate().is_err(), "{bad:?} must be refused");
    }
    assert!(RescanKnobs::default().validate().is_ok());
}

#[test]
fn every_event_that_can_hide_a_change_forces_a_full_walk() {
    let s = RescanScheduler::new(RescanKnobs::default());
    for (event, expected) in [
        (WatchEvent::DaemonStarted, RescanReason::Start),
        (WatchEvent::MappingResumed, RescanReason::Resume),
        (WatchEvent::MachineWoke, RescanReason::Wake),
        (WatchEvent::NetworkReturned, RescanReason::Reconnect),
        (WatchEvent::UserAskedToSyncNow, RescanReason::Manual),
        (WatchEvent::WatcherLost, RescanReason::WatcherLost),
    ] {
        let reason = s.on_event(event).expect("the default knobs allow all of these");
        assert_eq!(reason, expected);
        assert!(
            reason.requires_full_walk(),
            "{reason:?} means the watcher told us nothing, and 'nothing' is indistinguishable \
             from 'no changes' unless we go and look"
        );
    }
}

#[test]
fn the_wake_and_resume_knobs_can_switch_their_rescans_off_and_say_so() {
    let s = RescanScheduler::new(RescanKnobs {
        on_wake: false,
        on_resume: false,
        ..RescanKnobs::default()
    });
    assert_eq!(s.on_event(WatchEvent::MachineWoke), None);
    assert_eq!(s.on_event(WatchEvent::MappingResumed), None);
    // But nothing else is silenced by those knobs.
    assert_eq!(s.on_event(WatchEvent::DaemonStarted), Some(RescanReason::Start));
    assert_eq!(
        s.on_event(WatchEvent::NetworkReturned),
        Some(RescanReason::Reconnect)
    );
}

#[test]
fn sleep_is_detected_by_the_gap_between_the_two_clocks() {
    // The 15 s tick: both clocks advance together while the machine is awake.
    let mut d = WakeDetector::new(1_000, 500);
    assert_eq!(d.observe(1_015, 515), None);
    assert_eq!(d.observe(1_030, 530), None);

    // A weekend shut: the wall clock advanced two days, CLOCK_MONOTONIC did not advance across
    // sleep (which is why it is CLOCK_MONOTONIC and never CLOCK_BOOTTIME — BOOTTIME would tick
    // through the sleep and see nothing).
    let slept = d
        .observe(1_030 + 172_800, 545)
        .expect("two days of wall clock against 15 s of monotonic is sleep");
    assert!(
        (slept - 172_785).abs() < 2,
        "the gap is roughly the time asleep: {slept}"
    );

    // And it does not fire again on the next ordinary tick.
    assert_eq!(d.observe(1_030 + 172_815, 560), None);
}

#[test]
fn a_backwards_wall_clock_is_not_mistaken_for_sleep() {
    // NTP correcting a fast clock, or a user setting the date. The wall clock moves BACKWARDS
    // relative to monotonic time; treating that as a wake would rescan every mapping on the
    // machine for no reason.
    let mut d = WakeDetector::new(1_000_000, 500);
    assert_eq!(
        d.observe(1_000_000 - 3_600, 515),
        None,
        "an hour backwards is not an hour asleep"
    );
    // The detector recovers: the next honest tick is quiet, and a real sleep after it still fires.
    assert_eq!(d.observe(1_000_000 - 3_585, 530), None);
    assert!(d.observe(1_000_000 - 3_585 + 7_200, 545).is_some());
}

#[test]
fn a_gap_just_under_the_threshold_is_not_a_wake() {
    let mut d = WakeDetector::new(0, 0);
    assert_eq!(
        d.observe(WakeDetector::DEFAULT_THRESHOLD_S, 0),
        None,
        "exactly the threshold is not over it — a busy machine whose tick ran late is not a wake"
    );
    let mut d = WakeDetector::new(0, 0);
    assert_eq!(
        d.observe(WakeDetector::DEFAULT_THRESHOLD_S + 1, 0),
        Some(WakeDetector::DEFAULT_THRESHOLD_S + 1)
    );
}

#[test]
fn a_scheduler_resumed_from_the_journal_does_not_forget_its_last_rescan() {
    // The daemon restarts. Without the journal's `last_full_rescan_at` every restart would trigger
    // a full walk of every mapping, which on a large tree is how a sync engine becomes the reason
    // a laptop is hot.
    let mut s = RescanScheduler::resumed(RescanKnobs::default(), Some(10_000));
    assert_eq!(s.next_due_s(), Some(10_000 + 3_600));
    assert!(!s.periodic_due(10_100));

    // Start still forces one — the restart itself is a gap in what was watched.
    assert_eq!(s.on_event(WatchEvent::DaemonStarted), Some(RescanReason::Start));
    s.note_rescan(10_100);
    assert_eq!(s.next_due_s(), Some(10_100 + 3_600));
}
