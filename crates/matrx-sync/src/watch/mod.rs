//! FS-L1 unit 2 — local change detection.
//!
//! A watcher is an **optimisation**, never the mechanism. Watchers miss things: the kernel queue
//! overflows, the process was not running, the volume was not mounted, inotify runs out of watches,
//! FSEvents coalesces. So the contract (SCOPE §3.1 item 2) is a watcher **plus** a periodic full
//! rescan **plus** a rescan on start, wake, reconnect and resume — and the timer alone is enough to
//! be correct, just slower.
//!
//! | Submodule | What it owns |
//! |---|---|
//! | [`schedule`] | When to rescan and why: the knobs, the periodic cadence, and the monotonic-gap sleep detector. Pure — the clock is an argument. |

pub mod schedule;

pub use schedule::{RescanKnobs, RescanReason, RescanScheduler, WakeDetector, WatchEvent};
