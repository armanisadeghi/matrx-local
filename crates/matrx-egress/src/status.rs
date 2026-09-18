//! The status document — the one place the helper says what it is doing.
//!
//! Exactly the shape the contract fixes:
//!
//! ```json
//! {"state":"connected","since":"…","device_name":"…","server":"…","streams_active":0,
//!  "streams_total":0,"bytes_relayed":0,"last_error":null,"remedy":null,"helper_version":"…"}
//! ```
//!
//! Three consumers read it and they must never disagree: the tray menu, `matrx-egress status`,
//! and the desktop engine's supervisor tailing `--status-file`. They all read THIS struct.
//!
//! Law 4 lives here: `last_error` never appears without `remedy`. A state is set through
//! [`StatusHandle::set_state`] (no error) or [`StatusHandle::set_error`] (a sentence AND what to
//! do about it) — there is no third way to move the state, so an error without a remedy cannot be
//! written.

use crate::paths;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

/// The five states the contract names.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    /// The socket is up and the server has acknowledged HELLO.
    Connected,
    /// Dialling, or waiting out a backoff before the next attempt.
    Connecting,
    /// Switched off — from the tray, from `matrx-egress pause`, or from the web app (close 4403).
    Paused,
    /// No token on this computer: it has never been connected, or it was removed.
    SignedOut,
    /// Something is wrong and the helper is saying so, with a remedy.
    Error,
}

/// The status document.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Status {
    /// What the helper is doing.
    pub state: State,
    /// RFC3339, when it entered that state.
    pub since: String,
    /// The computer's name as the account shows it, once the server has said it.
    pub device_name: Option<String>,
    /// The server this helper talks to.
    pub server: String,
    /// Streams open right now.
    pub streams_active: u64,
    /// Streams opened since this process started.
    pub streams_total: u64,
    /// Bytes relayed in both directions since this process started.
    pub bytes_relayed: u64,
    /// A plain sentence, or null.
    pub last_error: Option<String>,
    /// What to do about it. Never null when `last_error` is set.
    pub remedy: Option<String>,
    /// This binary's version.
    pub helper_version: String,
}

impl Status {
    /// The document as one line of JSON — what goes on stdout per state change.
    pub fn to_line(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|e| {
            // A struct of owned strings and integers cannot fail to serialise; if it somehow does,
            // the line still says something true rather than vanishing.
            format!("{{\"state\":\"error\",\"last_error\":\"could not render the status: {e}\"}}")
        })
    }

    /// The document as pretty JSON — what `matrx-egress status` prints and what the status file
    /// holds, because a human opens both.
    pub fn to_pretty(&self) -> String {
        serde_json::to_string_pretty(self).unwrap_or_else(|_| self.to_line())
    }

    /// The tray's title line, in plain English.
    ///
    /// `signed_out` and `error` are two different things and must never read the same: `signed_out`
    /// is "the account does not have this computer any more", `error` is "it is still yours and it
    /// is not working right now" — and the second one names the reason, because a title that says
    /// only "Not connected" leaves the person with nowhere to go. The whole sentence and its
    /// remedy are the lines underneath ([`crate::menu::detail_lines`]).
    pub fn title_line(&self) -> String {
        match self.state {
            State::Connected => match &self.device_name {
                Some(name) => format!("AI Matrx Home Connection — Connected as {name}"),
                None => "AI Matrx Home Connection — Connected".to_string(),
            },
            State::Connecting => "AI Matrx Home Connection — Connecting".to_string(),
            State::Paused => "AI Matrx Home Connection — Paused".to_string(),
            State::SignedOut => "AI Matrx Home Connection — Removed from your account".to_string(),
            State::Error => match self
                .last_error
                .as_deref()
                .and_then(crate::menu::short_reason)
            {
                Some(reason) => format!("AI Matrx Home Connection — Not connected: {reason}"),
                None => "AI Matrx Home Connection — Not connected".to_string(),
            },
        }
    }
}

/// Shared, cheap to clone through an `Arc`, safe to update from any task.
#[derive(Debug)]
pub struct StatusHandle {
    inner: Mutex<Status>,
    /// Where to write the document, when the caller asked for a status file.
    path: Option<PathBuf>,
    /// Engine mode prints one JSON line per state change on stdout.
    emit_stdout: bool,
    /// Bumped on every state change so the tray knows to redraw without diffing a document.
    revision: AtomicU64,
}

impl StatusHandle {
    /// A handle in `connecting`, which is what the helper is doing the moment it starts.
    pub fn new(server: String, path: Option<PathBuf>, emit_stdout: bool) -> Self {
        StatusHandle {
            inner: Mutex::new(Status {
                state: State::Connecting,
                since: now_rfc3339(),
                device_name: None,
                server,
                streams_active: 0,
                streams_total: 0,
                bytes_relayed: 0,
                last_error: None,
                remedy: None,
                helper_version: crate::VERSION.to_string(),
            }),
            path,
            emit_stdout,
            revision: AtomicU64::new(0),
        }
    }

    /// A copy of the document as it stands.
    pub fn snapshot(&self) -> Status {
        self.inner.lock().expect("status mutex").clone()
    }

    /// How many state changes have happened — what the tray compares against to know it must
    /// redraw without diffing a whole document.
    pub fn revision(&self) -> u64 {
        self.revision.load(Ordering::SeqCst)
    }

    /// Move to a state that is not an error. Clears any previous error and its remedy: a state
    /// that succeeded must not keep wearing the last failure's sentence.
    pub fn set_state(&self, state: State) {
        self.update(|status| {
            if status.state == state && status.last_error.is_none() {
                return false;
            }
            status.state = state;
            status.since = now_rfc3339();
            status.last_error = None;
            status.remedy = None;
            true
        });
    }

    /// Move to a state because something went wrong, saying what and what to do about it.
    ///
    /// `state` is a parameter because not every error is `Error`: a removed device is
    /// `signed_out` and a device switched off from the web is `paused`, and each still carries the
    /// sentence explaining itself.
    pub fn set_error(&self, state: State, message: impl Into<String>, remedy: impl Into<String>) {
        let message = message.into();
        let remedy = remedy.into();
        self.update(|status| {
            if status.state == state
                && status.last_error.as_deref() == Some(message.as_str())
                && status.remedy.as_deref() == Some(remedy.as_str())
            {
                return false;
            }
            status.state = state;
            status.since = now_rfc3339();
            status.last_error = Some(message.clone());
            status.remedy = Some(remedy.clone());
            true
        });
    }

    /// Record the name the account shows for this computer (from HELLO_ACK).
    pub fn set_device_name(&self, name: impl Into<String>) {
        let name = name.into();
        self.update(|status| {
            if status.device_name.as_deref() == Some(name.as_str()) {
                return false;
            }
            status.device_name = Some(name.clone());
            true
        });
    }

    /// A stream was opened. Counters do NOT wake the file writer: they move constantly, and the
    /// 5-second tick is what the contract asks of them.
    pub fn stream_opened(&self) {
        let mut status = self.inner.lock().expect("status mutex");
        status.streams_active = status.streams_active.saturating_add(1);
        status.streams_total = status.streams_total.saturating_add(1);
    }

    /// A stream ended.
    pub fn stream_closed(&self) {
        let mut status = self.inner.lock().expect("status mutex");
        status.streams_active = status.streams_active.saturating_sub(1);
    }

    /// Bytes moved in either direction.
    pub fn add_bytes(&self, n: u64) {
        let mut status = self.inner.lock().expect("status mutex");
        status.bytes_relayed = status.bytes_relayed.saturating_add(n);
    }

    /// Every stream is gone because the socket went — used when a session ends so the tray never
    /// shows streams that no longer exist.
    pub fn clear_active_streams(&self) {
        let mut status = self.inner.lock().expect("status mutex");
        status.streams_active = 0;
    }

    /// Write the document to the status file, if there is one.
    pub fn persist(&self) {
        let Some(path) = &self.path else { return };
        let snapshot = self.snapshot();
        if let Err(e) = paths::write_atomic(path, &snapshot.to_pretty(), false) {
            // The status file is how another process learns what this one is doing, so failing to
            // write it is itself worth a line — and it must not be a silent no-op (law 4).
            eprintln!(
                "[egress] could not write {}: {e}. Anything watching this file will show stale \
                 information until the next write succeeds.",
                path.display()
            );
        }
    }

    fn update(&self, mutate: impl FnOnce(&mut Status) -> bool) {
        let line = {
            let mut status = self.inner.lock().expect("status mutex");
            if !mutate(&mut status) {
                return;
            }
            status.to_line()
        };
        self.revision.fetch_add(1, Ordering::SeqCst);
        if self.emit_stdout {
            println!("{line}");
        }
        self.persist();
    }
}

/// RFC3339 with second precision, in UTC — the same spelling `matrx-syncd` writes.
pub fn now_rfc3339() -> String {
    chrono::Utc::now()
        .format("%Y-%m-%dT%H:%M:%SZ")
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn handle() -> StatusHandle {
        StatusHandle::new("https://server.app.matrxserver.com".into(), None, false)
    }

    #[test]
    fn a_fresh_handle_is_connecting_and_carries_every_contract_key() {
        let status = handle().snapshot();
        assert_eq!(status.state, State::Connecting);
        let json: serde_json::Value =
            serde_json::from_str(&status.to_line()).expect("valid json");
        for key in [
            "state",
            "since",
            "device_name",
            "server",
            "streams_active",
            "streams_total",
            "bytes_relayed",
            "last_error",
            "remedy",
            "helper_version",
        ] {
            assert!(json.get(key).is_some(), "the status has no {key}");
        }
        assert_eq!(json["state"], "connecting");
        assert!(json["last_error"].is_null());
        assert!(json["remedy"].is_null());
        assert_eq!(json["helper_version"], crate::VERSION);
    }

    #[test]
    fn every_state_spells_itself_the_way_the_contract_does() {
        let handle = handle();
        for (state, spelling) in [
            (State::Connected, "connected"),
            (State::Connecting, "connecting"),
            (State::Paused, "paused"),
            (State::SignedOut, "signed_out"),
            (State::Error, "error"),
        ] {
            handle.set_state(state);
            let json: serde_json::Value =
                serde_json::from_str(&handle.snapshot().to_line()).expect("json");
            assert_eq!(json["state"], spelling);
        }
    }

    #[test]
    fn an_error_can_never_be_written_without_a_remedy() {
        let handle = handle();
        handle.set_error(State::Error, "the server refused the connection", "try again");
        let status = handle.snapshot();
        assert!(status.last_error.is_some());
        assert!(status.remedy.is_some());
    }

    #[test]
    fn recovering_clears_the_previous_failures_sentence() {
        let handle = handle();
        handle.set_error(State::Error, "no network", "check the connection");
        handle.set_state(State::Connected);
        let status = handle.snapshot();
        assert_eq!(status.state, State::Connected);
        assert_eq!(status.last_error, None, "a connected helper wore an error");
        assert_eq!(status.remedy, None);
    }

    #[test]
    fn a_state_change_bumps_the_revision_and_an_identical_one_does_not() {
        let handle = handle();
        handle.set_state(State::Connected);
        let first = handle.revision();
        assert!(first > 0);
        handle.set_state(State::Connected);
        assert_eq!(handle.revision(), first, "an unchanged state redrew the tray");
        handle.set_state(State::Paused);
        assert_eq!(handle.revision(), first + 1);
    }

    #[test]
    fn counters_move_without_redrawing_and_never_go_below_zero() {
        let handle = handle();
        handle.set_state(State::Connected);
        let revision = handle.revision();
        handle.stream_opened();
        handle.stream_opened();
        handle.add_bytes(4096);
        handle.stream_closed();
        handle.stream_closed();
        handle.stream_closed(); // one more close than open — a bug elsewhere must not panic here
        let status = handle.snapshot();
        assert_eq!(status.streams_total, 2);
        assert_eq!(status.streams_active, 0);
        assert_eq!(status.bytes_relayed, 4096);
        assert_eq!(handle.revision(), revision, "a counter woke the tray");
    }

    #[test]
    fn the_status_file_is_written_on_every_state_change() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("status.json");
        let handle = StatusHandle::new("https://example.test".into(), Some(path.clone()), false);
        handle.set_state(State::Connected);
        let written: Status =
            serde_json::from_str(&std::fs::read_to_string(&path).expect("read")).expect("parse");
        assert_eq!(written.state, State::Connected);

        handle.set_error(
            State::Paused,
            "Paused from the web",
            "Turn it back on in AI Matrx.",
        );
        let written: Status =
            serde_json::from_str(&std::fs::read_to_string(&path).expect("read")).expect("parse");
        assert_eq!(written.state, State::Paused);
        assert_eq!(written.last_error.as_deref(), Some("Paused from the web"));
    }

    #[test]
    fn the_title_line_is_plain_english_and_names_the_computer() {
        let handle = handle();
        handle.set_device_name("Arman's MacBook Pro");
        handle.set_state(State::Connected);
        assert_eq!(
            handle.snapshot().title_line(),
            "AI Matrx Home Connection — Connected as Arman's MacBook Pro"
        );
        handle.set_state(State::Paused);
        assert_eq!(
            handle.snapshot().title_line(),
            "AI Matrx Home Connection — Paused"
        );
        handle.set_state(State::SignedOut);
        assert_eq!(
            handle.snapshot().title_line(),
            "AI Matrx Home Connection — Removed from your account"
        );
        handle.set_error(
            State::Error,
            "AI Matrx could not be reached from here",
            "Check this computer's internet connection.",
        );
        assert_eq!(
            handle.snapshot().title_line(),
            "AI Matrx Home Connection — Not connected: AI Matrx could not be reached from here"
        );
        // An error with no sentence still gets a title, never an empty one.
        handle.set_state(State::Connected);
        handle.set_error(State::Error, "", "Try again.");
        assert_eq!(
            handle.snapshot().title_line(),
            "AI Matrx Home Connection — Not connected"
        );
        for state in [State::Connected, State::Paused, State::SignedOut, State::Error] {
            handle.set_state(state);
            let line = handle.snapshot().title_line();
            for jargon in ["egress", "proxy", "residential", "IP", "socket", "relay"] {
                assert!(!line.contains(jargon), "{state:?} title says {jargon:?}: {line}");
            }
        }
    }
}
