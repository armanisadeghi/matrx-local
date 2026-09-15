//! Locked files: deferred and retried, never dropped and never guessed at.
//!
//! SCOPE §3.1 item 7 lists locked files beside symlinks and name collisions as *"named states with
//! remedies"* — *"deferred and retried"*. On Windows this is ordinary rather than exceptional:
//! Office holds a `.docx` open for as long as it is on screen, antivirus holds a file for the
//! moment after it is written, and a backup agent holds whatever it is walking. A sync engine that
//! treats "cannot open" as "cannot sync" loses those files silently; one that retries forever
//! spins.
//!
//! So a lock is a **schedule**, and the schedule is knobs (`sync.locked_file_retry_base_s` 5,
//! `_max_s` 900, `_giveup_h` 24). Everything here is pure: the caller passes the clock.

/// The retry schedule, from the three knobs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetryPolicy {
    /// `sync.locked_file_retry_base_s` — the first wait, in seconds.
    pub base_s: u64,
    /// `sync.locked_file_retry_max_s` — the ceiling on the wait, in seconds.
    pub max_s: u64,
    /// `sync.locked_file_retry_giveup_h` — how long the file may stay locked before the engine
    /// stops retrying and says so.
    pub giveup_h: u64,
}

impl Default for RetryPolicy {
    /// SPEC-ENGINE §2's defaults: 5 s · 900 s · 24 h.
    fn default() -> Self {
        RetryPolicy {
            base_s: 5,
            max_s: 900,
            giveup_h: 24,
        }
    }
}

/// What to do about a file that is still locked.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetryVerdict {
    /// Try again at this epoch second.
    RetryAt(i64),
    /// It has been locked past `giveup_h`. Stop retrying and make it a visible state with a
    /// remedy — "close the app that has this file open" — rather than retrying invisibly forever.
    GiveUp,
}

/// One file the scan could not read, and the state of its retrying.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Deferral {
    /// The tree key.
    pub path_nfc: String,
    /// Epoch second of the FIRST failure — the clock the give-up runs against, so a file that
    /// fails every five seconds for a day still gives up after a day rather than never.
    pub first_failure_at_s: i64,
    /// How many attempts have failed.
    pub attempts: u32,
    /// The OS's own words, for the remedy sentence.
    pub last_error: String,
}

impl Deferral {
    /// A first failure, right now.
    pub fn first(path_nfc: impl Into<String>, now_s: i64, error: impl Into<String>) -> Self {
        Deferral {
            path_nfc: path_nfc.into(),
            first_failure_at_s: now_s,
            attempts: 1,
            last_error: error.into(),
        }
    }

    /// Record another failure.
    pub fn failed_again(&mut self, error: impl Into<String>) {
        self.attempts = self.attempts.saturating_add(1);
        self.last_error = error.into();
    }
}

/// When to try again, or whether to stop.
///
/// The wait doubles from `base_s` and is capped at `max_s`: a file locked for a second should be
/// picked up almost at once, and one locked all afternoon should not be hammered. The give-up is
/// measured from the FIRST failure, not the last, so a file that keeps failing does eventually
/// stop rather than resetting its own deadline.
pub fn next_attempt(policy: &RetryPolicy, deferral: &Deferral, now_s: i64) -> RetryVerdict {
    let elapsed = now_s.saturating_sub(deferral.first_failure_at_s);
    let giveup_after = (policy.giveup_h as i64).saturating_mul(3_600);
    if elapsed >= giveup_after {
        return RetryVerdict::GiveUp;
    }
    // 2^(attempts-1), saturating rather than wrapping: a very long-lived deferral must not shift
    // its way back to a one-second wait.
    let exponent = deferral.attempts.saturating_sub(1).min(32);
    let wait = policy
        .base_s
        .saturating_mul(1u64 << exponent)
        .min(policy.max_s.max(policy.base_s));
    RetryVerdict::RetryAt(now_s.saturating_add(wait as i64))
}

/// The sentence a surface shows for a file that has given up.
///
/// Named here rather than in the UI because law 4 is that a state announces itself **with a
/// remedy**, and the remedy for a locked file is always the same: find the app holding it.
pub fn giveup_remedy(path_nfc: &str, last_error: &str) -> String {
    format!(
        "“{path_nfc}” has been in use by another program for too long to sync. Close the app that \
         has it open, then choose Sync now. ({last_error})"
    )
}
