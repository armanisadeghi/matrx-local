//! Reconnect backoff — "jittered backoff 1→60 s".
//!
//! Doubling, capped at 60 s, with ±20 % jitter so a thousand helpers that all lost the same
//! deploy do not come back in one synchronised wave. The jitter is applied to every attempt
//! including the first, and the result is always clamped into `[1 s, 60 s]` — a helper must never
//! hot-loop a dial, and must never wait longer than a minute to try again.
//!
//! The counter is reset by ONE thing: a session that got as far as HELLO_ACK. A socket that
//! connected and was immediately refused (a bad token, a paused device) is not progress, and
//! resetting on it would turn a refusal into a tight loop.

use std::time::Duration;

/// The cap.
pub const MAX_DELAY: Duration = Duration::from_secs(60);
/// The floor.
pub const MIN_DELAY: Duration = Duration::from_secs(1);

/// A reconnect schedule.
#[derive(Debug, Clone)]
pub struct Backoff {
    attempt: u32,
}

impl Default for Backoff {
    fn default() -> Self {
        Self::new()
    }
}

impl Backoff {
    /// A fresh schedule: the next delay is about a second.
    pub const fn new() -> Self {
        Backoff { attempt: 0 }
    }

    /// Forget the failures — called only when a session reached HELLO_ACK.
    pub fn reset(&mut self) {
        self.attempt = 0;
    }

    /// How many failures in a row this schedule has seen.
    pub const fn attempt(&self) -> u32 {
        self.attempt
    }

    /// The delay before the next attempt, and advance.
    pub fn next_delay(&mut self) -> Duration {
        let base = Self::base_for(self.attempt);
        self.attempt = self.attempt.saturating_add(1);
        jitter(base, rand::random::<f64>())
    }

    /// The un-jittered delay for an attempt index — the doubling itself, extracted so it can be
    /// asserted without randomness.
    pub fn base_for(attempt: u32) -> Duration {
        let seconds = 1u64.checked_shl(attempt.min(63)).unwrap_or(u64::MAX);
        Duration::from_secs(seconds).min(MAX_DELAY)
    }
}

/// Apply ±20 % jitter and clamp. `fraction` is in `[0, 1)`.
fn jitter(base: Duration, fraction: f64) -> Duration {
    let factor = 0.8 + 0.4 * fraction.clamp(0.0, 1.0);
    let scaled = base.as_secs_f64() * factor;
    Duration::from_secs_f64(scaled).clamp(MIN_DELAY, MAX_DELAY)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_base_doubles_from_one_second_and_stops_at_sixty() {
        assert_eq!(Backoff::base_for(0), Duration::from_secs(1));
        assert_eq!(Backoff::base_for(1), Duration::from_secs(2));
        assert_eq!(Backoff::base_for(2), Duration::from_secs(4));
        assert_eq!(Backoff::base_for(3), Duration::from_secs(8));
        assert_eq!(Backoff::base_for(4), Duration::from_secs(16));
        assert_eq!(Backoff::base_for(5), Duration::from_secs(32));
        assert_eq!(Backoff::base_for(6), MAX_DELAY);
        // …and never overflows, however long the server stays down.
        for attempt in [7u32, 31, 63, 64, 1000, u32::MAX] {
            assert_eq!(Backoff::base_for(attempt), MAX_DELAY, "attempt {attempt}");
        }
    }

    #[test]
    fn every_delay_stays_inside_one_second_to_one_minute() {
        let mut backoff = Backoff::new();
        for attempt in 0..200 {
            let delay = backoff.next_delay();
            assert!(
                delay >= MIN_DELAY && delay <= MAX_DELAY,
                "attempt {attempt} waited {delay:?}"
            );
        }
    }

    #[test]
    fn the_jitter_is_plus_or_minus_a_fifth_and_never_leaves_the_bounds() {
        // The extremes of the random draw, asserted without depending on the draw.
        assert_eq!(jitter(Duration::from_secs(10), 0.0), Duration::from_secs(8));
        assert_eq!(jitter(Duration::from_secs(10), 1.0), Duration::from_secs(12));
        assert_eq!(jitter(Duration::from_secs(10), 0.5), Duration::from_secs(10));
        // The floor holds even at the low end of the first attempt…
        assert_eq!(jitter(Duration::from_secs(1), 0.0), MIN_DELAY);
        // …and the cap holds at the high end of the last.
        assert_eq!(jitter(MAX_DELAY, 1.0), MAX_DELAY);
    }

    #[test]
    fn the_delay_grows_until_it_reaches_the_cap() {
        let mut backoff = Backoff::new();
        let first = backoff.next_delay();
        // Attempt 0 is at most 1.2 s and attempt 5 is at least 25.6 s — they cannot overlap, so
        // this is an assertion about growth rather than about one random draw.
        for _ in 0..4 {
            backoff.next_delay();
        }
        let sixth = backoff.next_delay();
        assert!(sixth > first, "{sixth:?} did not grow past {first:?}");
        assert!(sixth >= Duration::from_secs(25), "{sixth:?}");
    }

    #[test]
    fn a_session_that_reached_hello_ack_starts_the_schedule_over() {
        let mut backoff = Backoff::new();
        for _ in 0..10 {
            backoff.next_delay();
        }
        assert_eq!(backoff.attempt(), 10);
        backoff.reset();
        assert_eq!(backoff.attempt(), 0);
        assert!(backoff.next_delay() <= Duration::from_millis(1200));
    }
}
