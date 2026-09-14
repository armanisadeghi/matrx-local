//! The clock seam (S9, S10).
//!
//! Scheduling uses the **monotonic** clock and the *relative* `expires_in`, so a wrong wall clock
//! cannot postpone a refresh; the wall clock is used only to write RFC3339 timestamps and to
//! detect a jump. Both are behind this trait so the tests in `tests/custody_*.rs` can fast-forward
//! deterministically instead of sleeping.

use chrono::{DateTime, Utc};
use std::time::{Duration, Instant};

/// Wall time and monotonic time, injected.
pub trait Clock: Send + Sync + std::fmt::Debug {
    /// Wall-clock now, for timestamps written to the journal and to the cloud.
    fn now_wall(&self) -> DateTime<Utc>;
    /// Monotonic now, as a duration since an arbitrary fixed origin. Never compared across
    /// processes; only differences are meaningful.
    fn now_mono(&self) -> Duration;
}

/// The real clock.
#[derive(Debug)]
pub struct SystemClock {
    origin: Instant,
}

impl SystemClock {
    /// A clock whose monotonic origin is this call.
    pub fn new() -> Self {
        SystemClock {
            origin: Instant::now(),
        }
    }
}

impl Default for SystemClock {
    fn default() -> Self {
        Self::new()
    }
}

impl Clock for SystemClock {
    fn now_wall(&self) -> DateTime<Utc> {
        Utc::now()
    }

    fn now_mono(&self) -> Duration {
        self.origin.elapsed()
    }
}

/// Render an instant the way every custody timestamp is written: RFC3339, UTC, second precision.
pub fn rfc3339(at: DateTime<Utc>) -> String {
    at.to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}

/// A clock the caller drives (S9, S10, §13's "deterministic clock").
///
/// **A test seam.** It is public because the §13 battery lives in `tests/`, which cannot reach
/// `#[cfg(test)]` items; it is never constructed by the daemon.
#[derive(Debug)]
pub struct TestClock {
    wall: std::sync::Mutex<DateTime<Utc>>,
    mono: std::sync::Mutex<Duration>,
}

impl TestClock {
    /// A clock starting at `wall` with a monotonic reading of zero.
    pub fn new(wall: DateTime<Utc>) -> Self {
        TestClock {
            wall: std::sync::Mutex::new(wall),
            mono: std::sync::Mutex::new(Duration::ZERO),
        }
    }

    /// Advance both clocks together — ordinary time passing.
    pub fn advance(&self, by: Duration) {
        *self.mono.lock().expect("test clock") += by;
        *self.wall.lock().expect("test clock") +=
            chrono::Duration::from_std(by).expect("test clock advance");
    }

    /// Move the WALL clock only — a machine whose clock was corrected, or a sleep that the
    /// monotonic clock did not see. This is what S10 must survive.
    pub fn jump_wall(&self, by: chrono::Duration) {
        *self.wall.lock().expect("test clock") += by;
    }
}

impl Clock for TestClock {
    fn now_wall(&self) -> DateTime<Utc> {
        *self.wall.lock().expect("test clock")
    }

    fn now_mono(&self) -> Duration {
        *self.mono.lock().expect("test clock")
    }
}
