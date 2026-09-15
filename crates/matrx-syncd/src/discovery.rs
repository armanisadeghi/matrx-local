//! `syncd.json` and `syncd.token` (C5, S17, SPEC-ENGINE §1.9).
//!
//! One discovery shape, owned by SPEC-ENGINE, carrying **no credential material at all** — no
//! token and no token path. The two scoped tokens live in the single two-line `syncd.token` and
//! nowhere else.

use crate::paths::{write_private, Paths};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use rand::TryRngCore;
use serde::{Deserialize, Serialize};
use std::io;
use std::path::Path;

/// The discovery file, exactly C5's shape. Written atomically, mode 0600.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Discovery {
    /// Always `1` for this shape.
    pub version: u32,
    /// `live` or `dev` — the one dev/live word, never a `dev` boolean.
    pub world: String,
    /// This process.
    pub pid: u32,
    /// The Unix socket path, or the Windows pipe name.
    pub socket_path: String,
    /// The loopback API listener's port, or `null` while the listener is down.
    pub tcp_port: Option<u16>,
    /// The daemon build's version string.
    pub daemon_version: String,
    /// RFC3339.
    pub started_at: String,
}

impl Discovery {
    /// Read the discovery file at `path`, if one is there and parses.
    pub fn read(path: &Path) -> Option<Self> {
        serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
    }

    /// Write it atomically, owner-only.
    pub fn write(&self, path: &Path) -> io::Result<()> {
        let json = serde_json::to_string(self).map_err(io::Error::other)?;
        write_private(path, &(json + "\n"))
    }

    /// Remove the discovery file — but **only if its `pid` is ours** (SPEC-ENGINE §1.3).
    ///
    /// A daemon that crashed and was replaced must not have its successor's file deleted by the
    /// corpse's shutdown path.
    pub fn remove_if_ours(path: &Path) -> io::Result<()> {
        match Discovery::read(path) {
            Some(d) if d.pid == std::process::id() => std::fs::remove_file(path),
            _ => Ok(()),
        }
    }
}

/// The two scoped tokens (S17). One file, two lines: line 1 `control`, line 2 `read`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScopedTokens {
    /// Line 1. The Tauri host, the Python engine, the CLI. Authorises every route. **It never
    /// enters JavaScript** — the webview asks through a Tauri IPC command and Rust makes the call.
    pub control: String,
    /// Line 2. Handed to the webview at window creation. Authorises the five read routes only;
    /// every mutating route rejects it with `403 {error:{code:"forbidden_scope"}}`.
    pub read: String,
}

impl ScopedTokens {
    /// Mint two fresh 256-bit tokens from the OS CSPRNG.
    ///
    /// Minted at every start: a token that outlived the process that issued it would authorise a
    /// caller against a daemon that never granted it.
    pub fn mint() -> io::Result<Self> {
        Ok(ScopedTokens {
            control: random_token()?,
            read: random_token()?,
        })
    }

    /// Write the one two-line file, mode 0600.
    pub fn write(&self, path: &Path) -> io::Result<()> {
        write_private(path, &format!("{}\n{}\n", self.control, self.read))
    }

    /// Read the file back — the ONE parser for the two-line shape on the Rust side, used by
    /// `--print-read-token` and by every test that authenticates against a running daemon.
    pub fn read(path: &Path) -> io::Result<Self> {
        let text = std::fs::read_to_string(path)?;
        let mut lines = text.lines();
        let control = lines.next().unwrap_or_default().trim().to_string();
        let read = lines.next().unwrap_or_default().trim().to_string();
        if control.is_empty() || read.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "{} does not hold two tokens; it must be exactly two lines — the control \
                     scope then the read scope",
                    path.display()
                ),
            ));
        }
        Ok(ScopedTokens { control, read })
    }

    /// Which scope a presented bearer token carries, if any.
    ///
    /// Compared in constant time so a timing oracle cannot walk the token out of the daemon.
    pub fn scope_of(&self, presented: &str) -> Option<Scope> {
        if constant_time_eq(presented, &self.control) {
            Some(Scope::Control)
        } else if constant_time_eq(presented, &self.read) {
            Some(Scope::Read)
        } else {
            None
        }
    }
}

/// The two scopes (S17).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scope {
    /// Authorises every route.
    Control,
    /// Authorises `GET /v1/token`, `/v1/session`, `/v1/status`, `/v1/version` and `/v1/events`
    /// only.
    Read,
}

fn random_token() -> io::Result<String> {
    let mut buf = [0u8; 32];
    rand::rngs::OsRng
        .try_fill_bytes(&mut buf)
        .map_err(io::Error::other)?;
    Ok(URL_SAFE_NO_PAD.encode(buf))
}

fn constant_time_eq(a: &str, b: &str) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.bytes().zip(b.bytes()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// The clobber rule, mirroring the engine's (SPEC-ENGINE §1.9, `preflight.py`).
///
/// A start refuses to overwrite a `syncd.json` whose `pid` is alive **and** whose endpoint answers
/// `GET /v1/health`. One daemon per user per world. It logs loudly and exits 0 — never a silent
/// second daemon, and never a kill.
pub enum ClobberCheck {
    /// No live daemon is publishing; this process may claim the file.
    Free,
    /// A live daemon already holds this world.
    Occupied {
        /// Its pid, for the log line.
        pid: u32,
        /// Where it is listening, for the log line.
        endpoint: String,
    },
}

/// Decide whether this process may publish `syncd.json`.
pub async fn clobber_check(paths: &Paths) -> ClobberCheck {
    let Some(existing) = Discovery::read(&paths.discovery) else {
        return ClobberCheck::Free;
    };
    if existing.pid == std::process::id() {
        return ClobberCheck::Free;
    }
    if !pid_is_alive(existing.pid) {
        return ClobberCheck::Free;
    }
    // A live pid is not enough: a pid is reused, and a hung process is not a serving one. The
    // endpoint must actually answer.
    let Some(port) = existing.tcp_port else {
        return ClobberCheck::Occupied {
            pid: existing.pid,
            endpoint: existing.socket_path.clone(),
        };
    };
    let answered = tokio::time::timeout(
        std::time::Duration::from_secs(2),
        reqwest::Client::new()
            .get(format!("http://127.0.0.1:{port}/v1/health"))
            .header("Host", format!("127.0.0.1:{port}"))
            .send(),
    )
    .await
    .ok()
    .and_then(|r| r.ok())
    .map(|r| r.status().is_success())
    .unwrap_or(false);

    if answered {
        ClobberCheck::Occupied {
            pid: existing.pid,
            endpoint: format!("127.0.0.1:{port}"),
        }
    } else {
        ClobberCheck::Free
    }
}

#[cfg(unix)]
fn pid_is_alive(pid: u32) -> bool {
    // `kill(pid, 0)` asks the kernel whether the process exists and we may signal it. It sends
    // nothing: rule 9 — nobody kills the daemon, and this code does not become the exception.
    // SAFETY: `kill` with signal 0 has no effect beyond the existence check, and both arguments
    // are plain integers.
    unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
}

#[cfg(windows)]
fn pid_is_alive(pid: u32) -> bool {
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    // SAFETY: OpenProcess takes plain integers and returns a handle we close immediately. It
    // opens for QUERY only — the daemon is never signalled or terminated (rule 9, D19).
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() || handle == INVALID_HANDLE_VALUE {
            return false;
        }
        CloseHandle(handle);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_token_file_is_two_lines_and_round_trips() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("syncd.token");
        let tokens = ScopedTokens::mint().expect("mint");
        tokens.write(&path).expect("write");

        let text = std::fs::read_to_string(&path).expect("read");
        assert_eq!(text.lines().count(), 2, "exactly two lines (C5)");
        assert_eq!(ScopedTokens::read(&path).expect("parse"), tokens);
    }

    #[test]
    fn the_two_tokens_are_never_equal_and_carry_distinct_scopes() {
        let t = ScopedTokens::mint().expect("mint");
        assert_ne!(t.control, t.read);
        assert_eq!(t.scope_of(&t.control), Some(Scope::Control));
        assert_eq!(t.scope_of(&t.read), Some(Scope::Read));
        assert_eq!(t.scope_of("anything else"), None);
        assert_eq!(t.scope_of(""), None);
    }

    #[test]
    fn a_half_written_token_file_is_refused_rather_than_half_trusted() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("syncd.token");
        std::fs::write(&path, "only-one-line\n").expect("write");
        assert!(ScopedTokens::read(&path).is_err());
    }

    #[test]
    fn the_discovery_file_carries_no_credential_material() {
        let d = Discovery {
            version: 1,
            world: "dev".into(),
            pid: 4711,
            socket_path: "/Users/u/.matrx-dev/run/syncd.sock".into(),
            tcp_port: Some(22263),
            daemon_version: "0.1.0".into(),
            started_at: "2026-09-13T18:00:00Z".into(),
        };
        let json = serde_json::to_string(&d).expect("json");
        // C5: no token, and no token path, ever appears in this file.
        assert!(!json.contains("token"));
        assert!(!json.contains("syncd.token"));
        // Round trip, so a reader written against the shape keeps working.
        assert_eq!(serde_json::from_str::<Discovery>(&json).unwrap(), d);
    }

    #[test]
    fn the_discovery_file_is_removed_only_by_the_process_that_wrote_it() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("syncd.json");
        let mut d = Discovery {
            version: 1,
            world: "dev".into(),
            pid: std::process::id() + 1,
            socket_path: "x".into(),
            tcp_port: None,
            daemon_version: "0.1.0".into(),
            started_at: "t".into(),
        };
        d.write(&path).expect("write");
        Discovery::remove_if_ours(&path).expect("no-op");
        assert!(path.exists(), "another daemon's file is left alone");

        d.pid = std::process::id();
        d.write(&path).expect("write");
        Discovery::remove_if_ours(&path).expect("remove");
        assert!(!path.exists());
    }
}
