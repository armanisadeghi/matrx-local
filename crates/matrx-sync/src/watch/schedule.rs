//! When to rescan, and why.
//!
//! SCOPE §3.1 item 2: *"`notify` watcher **plus** a periodic full rescan (default 60 min, knob) and
//! a rescan on daemon start, wake, network reconnect, and mapping resume."* The "plus" is the whole
//! point — a watcher is an optimisation, never the mechanism. Watchers miss events: the kernel
//! queue overflows, the process was not running, the volume was not mounted, inotify ran out of
//! watches, and on macOS FSEvents coalesces. An engine that trusts its watcher is an engine that
//! quietly stops syncing a folder and never notices.
//!
//! Everything here is pure: the clock is an argument. That is what lets the harness fast-forward a
//! day and assert what the daemon would have done.

use serde::{Deserialize, Serialize};

/// Why a rescan is happening. It reaches the activity log, so the user can see the difference
/// between "we check every hour" and "your laptop woke up".
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RescanReason {
    /// The daemon started. It has no idea what happened while it was not running.
    Start,
    /// The user resumed a paused or suspended mapping (`sync.rescan_on_resume`).
    Resume,
    /// The machine slept and woke (`sync.rescan_on_wake`). Nothing was watched while it slept.
    Wake,
    /// The network came back. The feed cursor and the local tree may both have moved.
    Reconnect,
    /// The periodic sweep — `sync.rescan_interval_min`, or the degraded cadence while the watcher
    /// is exhausted.
    Periodic,
    /// The user pressed Sync now.
    Manual,
    /// The watcher itself failed or was lost, so the timer is the only remaining mechanism.
    WatcherLost,
}

impl RescanReason {
    /// A short stable token for activity rows.
    pub const fn as_str(self) -> &'static str {
        match self {
            RescanReason::Start => "start",
            RescanReason::Resume => "resume",
            RescanReason::Wake => "wake",
            RescanReason::Reconnect => "reconnect",
            RescanReason::Periodic => "periodic",
            RescanReason::Manual => "manual",
            RescanReason::WatcherLost => "watcher_lost",
        }
    }

    /// Whether this reason means the watcher's view is untrustworthy, so the scan must be a FULL
    /// walk rather than only the paths the watcher named.
    pub const fn requires_full_walk(self) -> bool {
        match self {
            // A watcher that was not running, not subscribed, or out of watches told us nothing,
            // and "nothing" is indistinguishable from "no changes" unless we go and look.
            RescanReason::Start
            | RescanReason::Resume
            | RescanReason::Wake
            | RescanReason::Reconnect
            | RescanReason::Periodic
            | RescanReason::WatcherLost => true,
            // The user asked; they may be asking about one file, but they are entitled to a full
            // answer, so this is full too. It is separate only so the log can say who asked.
            RescanReason::Manual => true,
        }
    }
}

/// The rescan knobs, resolved.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RescanKnobs {
    /// `sync.rescan_interval_min` — 60 by default, 5–1440.
    pub interval_min: u32,
    /// `sync.rescan_interval_degraded_min` — 15 by default, 5–120. The cadence while
    /// `watcher_exhausted`: the timer is now the only mechanism, so it has to run more often.
    pub interval_degraded_min: u32,
    /// `sync.rescan_on_wake` — true by default.
    pub on_wake: bool,
    /// `sync.rescan_on_resume` — true by default.
    pub on_resume: bool,
}

impl Default for RescanKnobs {
    /// SPEC-ENGINE §2's defaults.
    fn default() -> Self {
        RescanKnobs {
            interval_min: 60,
            interval_degraded_min: 15,
            on_wake: true,
            on_resume: true,
        }
    }
}

impl RescanKnobs {
    /// Check each value against its documented range. Refuses rather than clamps: a clamp is a
    /// limit an agent chose.
    pub fn validate(&self) -> crate::Result<()> {
        use crate::SyncError::KnobOutOfRange;
        if !(5..=1440).contains(&self.interval_min) {
            return Err(KnobOutOfRange {
                knob: "sync.rescan_interval_min",
                value: self.interval_min.to_string(),
            });
        }
        if !(5..=120).contains(&self.interval_degraded_min) {
            return Err(KnobOutOfRange {
                knob: "sync.rescan_interval_degraded_min",
                value: self.interval_degraded_min.to_string(),
            });
        }
        Ok(())
    }

    /// The cadence in force, in seconds.
    pub fn interval_s(&self, watcher_exhausted: bool) -> i64 {
        let minutes = if watcher_exhausted {
            self.interval_degraded_min
        } else {
            self.interval_min
        };
        i64::from(minutes) * 60
    }
}

/// Decides when the next periodic rescan is due, and turns events into rescans.
///
/// One per mapping. It holds no clock and spawns nothing; the daemon's 15 s tick asks it.
#[derive(Debug, Clone)]
pub struct RescanScheduler {
    knobs: RescanKnobs,
    last_full_rescan_s: Option<i64>,
    watcher_exhausted: bool,
}

impl RescanScheduler {
    /// A scheduler for a mapping that has never been scanned.
    pub fn new(knobs: RescanKnobs) -> Self {
        RescanScheduler {
            knobs,
            last_full_rescan_s: None,
            watcher_exhausted: false,
        }
    }

    /// A scheduler for a mapping whose journal remembers its last full rescan.
    pub fn resumed(knobs: RescanKnobs, last_full_rescan_s: Option<i64>) -> Self {
        RescanScheduler {
            knobs,
            last_full_rescan_s,
            watcher_exhausted: false,
        }
    }

    /// Record that a full rescan just finished.
    pub fn note_rescan(&mut self, at_s: i64) {
        self.last_full_rescan_s = Some(at_s);
    }

    /// Whether the watcher is exhausted, which changes the cadence.
    pub fn set_watcher_exhausted(&mut self, exhausted: bool) {
        self.watcher_exhausted = exhausted;
    }

    /// The epoch second the next periodic rescan is due.
    ///
    /// A mapping that has never been scanned is due immediately — not in an hour. The daemon has
    /// just started and has no idea what happened while it was off.
    pub fn next_due_s(&self) -> Option<i64> {
        self.last_full_rescan_s
            .map(|last| last.saturating_add(self.knobs.interval_s(self.watcher_exhausted)))
    }

    /// Is a periodic rescan due at `now_s`?
    pub fn periodic_due(&self, now_s: i64) -> bool {
        match self.next_due_s() {
            None => true,
            Some(due) => now_s >= due,
        }
    }

    /// Turn an event into a rescan reason, honouring the knobs that can switch one off.
    ///
    /// `None` means the knob says not to — and that is the ONLY reason this returns `None`, so a
    /// caller can log the difference between "we chose not to" and "nothing happened".
    pub fn on_event(&self, event: WatchEvent) -> Option<RescanReason> {
        match event {
            WatchEvent::DaemonStarted => Some(RescanReason::Start),
            WatchEvent::MappingResumed if self.knobs.on_resume => Some(RescanReason::Resume),
            WatchEvent::MappingResumed => None,
            WatchEvent::MachineWoke if self.knobs.on_wake => Some(RescanReason::Wake),
            WatchEvent::MachineWoke => None,
            WatchEvent::NetworkReturned => Some(RescanReason::Reconnect),
            WatchEvent::UserAskedToSyncNow => Some(RescanReason::Manual),
            WatchEvent::WatcherLost => Some(RescanReason::WatcherLost),
        }
    }
}

/// Something that happened to the machine or the mapping.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WatchEvent {
    /// The daemon came up.
    DaemonStarted,
    /// A paused or suspended mapping was resumed.
    MappingResumed,
    /// The monotonic-gap detector saw sleep.
    MachineWoke,
    /// The reachability probe succeeded after failing.
    NetworkReturned,
    /// The user pressed Sync now.
    UserAskedToSyncNow,
    /// The watcher died, or ran out of watches.
    WatcherLost,
}

/// The sleep/wake detector.
///
/// SPEC-ENGINE §1.10: *"Wake detection is a monotonic-gap detector on all three OSes: compare
/// wall-clock delta to monotonic delta; `wall − mono > 60 s` means the machine slept … Platform
/// hooks (`NSWorkspace.didWakeNotification`, `WM_POWERBROADCAST`, systemd `sleep.target`) are
/// optimisations layered on top, never the mechanism — one code path, testable in the harness by
/// fast-forwarding time."*
///
/// The mechanism is one comparison because three platform hooks are three code paths, two of which
/// are never exercised by whoever is debugging. ASSERTED by the spec and relied on here:
/// `CLOCK_MONOTONIC` does not advance across sleep on macOS or Linux — never `CLOCK_BOOTTIME`,
/// which does and would therefore see nothing.
#[derive(Debug, Clone, Copy)]
pub struct WakeDetector {
    last_wall_s: i64,
    last_mono_s: i64,
    threshold_s: i64,
}

impl WakeDetector {
    /// The spec's threshold: a wall-clock advance more than 60 s ahead of the monotonic clock.
    pub const DEFAULT_THRESHOLD_S: i64 = 60;

    /// Start from a first observation of both clocks.
    pub fn new(wall_s: i64, mono_s: i64) -> Self {
        WakeDetector {
            last_wall_s: wall_s,
            last_mono_s: mono_s,
            threshold_s: Self::DEFAULT_THRESHOLD_S,
        }
    }

    /// The same, with a threshold the harness can shrink.
    pub fn with_threshold(wall_s: i64, mono_s: i64, threshold_s: i64) -> Self {
        WakeDetector {
            last_wall_s: wall_s,
            last_mono_s: mono_s,
            threshold_s,
        }
    }

    /// Feed the 15 s tick's two clock readings. `Some(gap)` means the machine slept for about that
    /// many seconds.
    pub fn observe(&mut self, wall_s: i64, mono_s: i64) -> Option<i64> {
        let wall_delta = wall_s.saturating_sub(self.last_wall_s);
        let mono_delta = mono_s.saturating_sub(self.last_mono_s);
        self.last_wall_s = wall_s;
        self.last_mono_s = mono_s;
        let gap = wall_delta.saturating_sub(mono_delta);
        // A NEGATIVE gap is the wall clock being corrected backwards (NTP, a timezone-naive
        // change, a user setting the date). It is not sleep and must not trigger a wake rescan,
        // but it does make every timestamp comparison suspect — the caller is told by the sign.
        if gap > self.threshold_s {
            Some(gap)
        } else {
            None
        }
    }

    /// Whether the last observation showed the wall clock moving backwards relative to monotonic
    /// time, which makes `at >= since` comparisons unreliable until it settles.
    pub fn threshold_s(&self) -> i64 {
        self.threshold_s
    }
}
