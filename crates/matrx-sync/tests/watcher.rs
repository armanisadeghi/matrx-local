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

// ------------------------------------- unit 2b: the real watcher, over a real directory

use matrx_sync::watch::{FolderWatcher, WatcherError};
use std::time::Duration;

/// The knob's floor (`sync.watcher_debounce_ms` range is 100–5000). Tests use the floor so they
/// finish; the shipped default is 750, because editors write a file three or four times a second.
const DEBOUNCE_MS: u64 = 100;

/// Drain until something arrives or the budget runs out. Watchers are asynchronous and the
/// alternative — one fixed sleep — is a test that is either slow or flaky, and usually both.
fn wait_for_paths(watcher: &FolderWatcher, budget: Duration) -> std::collections::BTreeSet<String> {
    let deadline = std::time::Instant::now() + budget;
    let mut paths = std::collections::BTreeSet::new();
    while std::time::Instant::now() < deadline {
        let batch = watcher.drain_blocking(Duration::from_millis(200));
        paths.extend(batch.paths);
        if !paths.is_empty() {
            // Give coalesced follow-ups a moment to land, then take whatever else is queued.
            std::thread::sleep(Duration::from_millis(DEBOUNCE_MS * 2));
            paths.extend(watcher.drain().paths);
            break;
        }
    }
    paths
}

#[test]
fn the_watcher_reports_a_real_write_as_a_mapping_relative_nfc_path() {
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::create_dir(dir.path().join("sub")).expect("mkdir");
    let watcher = FolderWatcher::start(dir.path(), DEBOUNCE_MS).expect("start a watcher");

    std::fs::write(dir.path().join("sub/note.txt"), b"hello").expect("write");

    let paths = wait_for_paths(&watcher, Duration::from_secs(10));
    assert!(
        paths.iter().any(|p| p == "sub/note.txt" || p == "sub"),
        "the write must be reported somewhere under the mapping: {paths:?}"
    );
    assert!(
        paths.iter().all(|p| !p.starts_with('/')),
        "paths are mapping-relative, the shape the journal and the cloud use: {paths:?}"
    );
}

#[test]
fn the_watchers_own_staging_directory_is_never_reported_as_a_local_change() {
    // Every download writes into `.matrx-sync/tmp` before it is verified and renamed into place
    // (I4). If those writes came back as local changes the engine would plan work in response to
    // its own downloads — a loop that looks exactly like a busy sync.
    let dir = tempfile::tempdir().expect("tempdir");
    matrx_sync::scan::write_marker(dir.path(), "uuid-1").expect("marker");
    let watcher = FolderWatcher::start(dir.path(), DEBOUNCE_MS).expect("start");

    std::fs::write(dir.path().join(".matrx-sync/tmp/half-a-download"), b"partial").expect("write");
    std::fs::write(dir.path().join("real.txt"), b"a real change").expect("write");

    let paths = wait_for_paths(&watcher, Duration::from_secs(10));
    assert!(
        paths.iter().all(|p| !p.starts_with(".matrx-sync")),
        "the mapping's own bookkeeping is not a user change: {paths:?}"
    );
    assert!(
        paths.iter().any(|p| p == "real.txt"),
        "and the real change still arrives: {paths:?}"
    );
}

#[test]
fn a_root_that_is_not_there_fails_loudly_with_a_remedy() {
    let dir = tempfile::tempdir().expect("tempdir");
    let missing = dir.path().join("never-existed");

    let error = FolderWatcher::start(&missing, DEBOUNCE_MS)
        .expect_err("watching a folder that is not there cannot silently succeed");
    assert!(
        matches!(error, WatcherError::RootUnavailable(_)),
        "got {error:?}"
    );
    assert_eq!(error.honest_state(), "root_missing");
    assert!(
        !error.survivable(),
        "a root that is not there is not something the timer can work around"
    );
    assert!(error.remedy().to_lowercase().contains("drive"));
}

#[test]
fn the_inotify_limit_is_a_named_state_with_the_sysctl_in_the_remedy() {
    // SCOPE §3.1 item 12 requires this be surfaced with "the exact remedy". It is the one watcher
    // failure a user can actually fix, and it is silent otherwise: the folder simply stops
    // updating instantly and nobody knows why.
    let exhausted = WatcherError::WatchLimitReached;
    assert_eq!(exhausted.honest_state(), "watcher_exhausted");
    assert!(
        exhausted.survivable(),
        "syncing continues on the timer — the watcher was only ever an optimisation"
    );
    let remedy = exhausted.remedy();
    assert!(
        remedy.contains("fs.inotify.max_user_watches"),
        "the sysctl name is the entire useful content of this message: {remedy}"
    );
    assert!(
        remedy.contains("timer"),
        "and it says what is happening meanwhile, so the state is not just a complaint: {remedy}"
    );

    // Linux reports the limit as ENOSPC from inotify_add_watch — "no space left on device" on a
    // disk with plenty of room. Filing that under generic IO is how this class stays invisible.
    let enospc = notify::Error::io(std::io::Error::from_raw_os_error(28));
    assert_eq!(
        matrx_sync::watch::classify(&enospc),
        WatcherError::WatchLimitReached
    );
    // And the kind `notify` raises by name maps the same way.
    let by_kind = notify::Error::new(notify::ErrorKind::MaxFilesWatch);
    assert_eq!(matrx_sync::watch::classify(&by_kind), WatcherError::WatchLimitReached);
}

#[test]
fn an_unclassifiable_watcher_failure_keeps_the_mapping_running_on_the_timer() {
    let other = matrx_sync::watch::classify(&notify::Error::generic("backend fell over"));
    assert_eq!(other, WatcherError::Other("backend fell over".to_string()));
    assert_eq!(
        other.honest_state(),
        "polling_fallback",
        "a watcher failure is never a reason to stop syncing: the periodic rescan is the mechanism"
    );
    assert!(other.survivable());
    assert!(other.remedy().contains("Syncing continues"));
}

#[test]
fn the_watcher_and_the_scanner_agree_on_what_a_path_is_called() {
    // The watcher names paths so the scanner can look them up. If the two disagreed about
    // normalisation or separators, every watched change would miss in the tree.
    let dir = tempfile::tempdir().expect("tempdir");
    std::fs::create_dir(dir.path().join("d")).expect("mkdir");
    let watcher = FolderWatcher::start(dir.path(), DEBOUNCE_MS).expect("start");
    std::fs::write(dir.path().join("d/cafe\u{301}.txt"), b"x").expect("write");

    let watched = wait_for_paths(&watcher, Duration::from_secs(10));
    let scanned = matrx_sync::scan::scan_root(
        dir.path(),
        &matrx_sync::model::LocalTree::new(),
        &opts_settled_for_watch(),
    );

    assert!(
        !watched.is_empty(),
        "the watcher saw nothing, so this test would pass without proving anything — which is \
         exactly how the symlinked-root bug hid: every event was silently discarded"
    );
    for path in watched.iter().filter(|p| p.ends_with(".txt")) {
        assert!(
            scanned.tree.get(path).is_some(),
            "the watcher reported {path:?}, which the scanner does not have: {:?}",
            scanned.tree.paths().collect::<Vec<_>>()
        );
    }
}

fn opts_settled_for_watch() -> matrx_sync::scan::ScanOptions {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("after 1970")
        .as_nanos() as i64;
    matrx_sync::scan::ScanOptions::new(now + 3_600_000_000_000, "2026-09-15T00:00:00Z")
}
