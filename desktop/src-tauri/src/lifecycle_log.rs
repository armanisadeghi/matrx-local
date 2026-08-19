//! Durable lifecycle log for Rust-side process-control events.
//!
//! Every place Rust starts, stops, signals, or sweeps the engine sidecar (or
//! observes it die) MUST call [`log`] so the event survives in a file the
//! user/agent can read after the fact. `println!`/`eprintln!` are NOT durable:
//! a packaged .app launched from Finder has no captured stdout, which is why
//! the 2026-07-11/12 mid-session engine deaths were unattributable — roughly
//! a dozen SIGTERM-style shutdowns reached the engine and not one line
//! anywhere recorded which Rust path (if any) sent them.
//!
//! Destination: `<platform log dir>/lifecycle.log`, the same directory the
//! Python engine writes `system.log` to, so one directory tells the whole
//! story:
//!   macOS   → ~/Library/Logs/MatrxLocal/lifecycle.log
//!   Windows → %LOCALAPPDATA%\MatrxLocal\logs\lifecycle.log
//!   Linux   → ~/.local/state/matrx-local/logs/lifecycle.log (XDG_STATE_HOME;
//!             note the lowercase slug — Linux uses APP_NAME_SLUG)
//! (Mirrors `_platform_log_dir()` in app/config.py — keep the two in sync.
//! In dev/non-frozen runs the Python engine logs to <repo>/system/logs
//! instead, so "beside system.log" holds for packaged builds — the ones
//! this forensic log exists for.)
//!
//! The file rotates once at 5 MB (lifecycle events are rare; this cap exists
//! only as a runaway guard). Writes are best-effort append-with-timestamp;
//! logging must never block or fail a shutdown path.

use std::io::Write;

const MAX_BYTES: u64 = 5 * 1024 * 1024;

fn log_dir() -> Option<std::path::PathBuf> {
    #[cfg(target_os = "macos")]
    {
        std::env::var_os("HOME")
            .map(|h| std::path::PathBuf::from(h).join("Library").join("Logs").join("MatrxLocal"))
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("LOCALAPPDATA")
            .map(|base| std::path::PathBuf::from(base).join("MatrxLocal").join("logs"))
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let state_home = std::env::var_os("XDG_STATE_HOME")
            .map(std::path::PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .map(|h| std::path::PathBuf::from(h).join(".local").join("state"))
            })?;
        // config.py uses the lowercase APP_NAME_SLUG on Linux.
        Some(state_home.join("matrx-local").join("logs"))
    }
}

/// Append one timestamped line to lifecycle.log AND echo it to stderr.
///
/// `event` should say WHAT happened, WHO did it, and to WHICH pid, e.g.
/// `[stop_sidecar] sending SIGTERM to engine pid 1234 (frontend command)`.
pub fn log(event: &str) {
    // Human-readable UTC timestamp without a chrono dependency.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let line = format!("{} [unix:{}] {}\n", format_utc(now), now, event);

    eprintln!("[lifecycle] {}", event);

    let Some(dir) = log_dir() else { return };
    let path = dir.join("lifecycle.log");
    let _ = std::fs::create_dir_all(&dir);

    // One-shot rotation guard.
    if let Ok(meta) = std::fs::metadata(&path) {
        if meta.len() > MAX_BYTES {
            let _ = std::fs::rename(&path, dir.join("lifecycle.log.1"));
        }
    }

    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = f.write_all(line.as_bytes());
    }
}

/// Append one timestamped engine STDERR line to engine-stderr.log.
///
/// The engine's stderr carries what system.log cannot: Python tracebacks from
/// import-time failures, SystemExit messages, and PyInstaller bootstrap
/// errors — everything a crashed boot prints before (or instead of) its
/// logging setup. Until 2026-08-19 those lines lived only in the 200-line
/// in-memory SidecarLogs ring buffer and the app's stdout (nowhere, for a
/// Finder launch), which made the recurring "engine exited code=1 ~35s after
/// spawn, nothing in system.log" boots undiagnosable after the fact. Same
/// destination directory and best-effort semantics as lifecycle.log.
pub fn engine_stderr(line: &str) {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let entry = format!("{} {}\n", format_utc(now), line);

    let Some(dir) = log_dir() else { return };
    let path = dir.join("engine-stderr.log");
    let _ = std::fs::create_dir_all(&dir);

    if let Ok(meta) = std::fs::metadata(&path) {
        if meta.len() > MAX_BYTES {
            let _ = std::fs::rename(&path, dir.join("engine-stderr.log.1"));
        }
    }

    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = f.write_all(entry.as_bytes());
    }
}

/// Seconds-since-epoch → "YYYY-MM-DD HH:MM:SSZ" (civil-from-days algorithm,
/// avoids pulling in chrono for one format call).
fn format_utc(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // Howard Hinnant's civil_from_days.
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if mo <= 2 { y + 1 } else { y };
    format!("{:04}-{:02}-{:02} {:02}:{:02}:{:02}Z", y, mo, d, h, m, s)
}
