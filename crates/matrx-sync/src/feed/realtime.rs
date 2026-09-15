//! The Realtime trigger: a small Phoenix-channel client over Supabase Realtime.
//!
//! **It is a trigger and nothing more** (D9). Postgres replica identity does not put the old row on
//! the wire, so Realtime can never carry a delete — an engine that treated it as truth would
//! resurrect every deleted file the moment a device reconnected. Its entire job is to make
//! [`super::apply`]'s feed read happen sooner than the poll would have.
//!
//! That is why this module is allowed to fail. When the socket will not stay up, the mapping falls
//! back to polling and **says so on screen** (`polling_fallback` — SCOPE §3.1 item 8: *"announced
//! on screen as 'live updates unavailable, checking every minute'"*). A sync engine that silently
//! degrades to a one-minute poll is a sync engine users describe as "sometimes slow for no reason".
//!
//! SPEC-ENGINE §5: there is **no maintained Supabase Realtime client for Rust**, so the protocol is
//! ours. It is small — join, heartbeat, `postgres_changes` — and the framing is pinned by tests so
//! a server-side change shows up as a failure rather than as silence.

use serde::{Deserialize, Serialize};

/// A Phoenix frame. Supabase Realtime speaks Phoenix v1's JSON protocol.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PhoenixFrame {
    /// The join reference — set on the join, echoed on its reply.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub join_ref: Option<String>,
    /// The message reference, for matching replies.
    #[serde(skip_serializing_if = "Option::is_none")]
    #[serde(rename = "ref")]
    pub message_ref: Option<String>,
    /// The channel topic, e.g. `realtime:public:files`.
    pub topic: String,
    /// The event name.
    pub event: String,
    /// The payload.
    pub payload: serde_json::Value,
}

/// Which table a subscription watches.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WatchedTable {
    /// `files.files`.
    Files,
    /// `files.folders`.
    Folders,
}

impl WatchedTable {
    /// The schema half of the `postgres_changes` filter.
    pub const fn schema(self) -> &'static str {
        "files"
    }

    /// The table half.
    pub const fn table(self) -> &'static str {
        match self {
            WatchedTable::Files => "files",
            WatchedTable::Folders => "folders",
        }
    }
}

/// The join frame for one table's channel.
///
/// The access token goes in the payload's `access_token`, which is also what a later
/// `access_token` event refreshes — SPEC-CUSTODY re-authenticates this socket on `session.changed`
/// rather than tearing it down, because a reconnect costs a full feed read.
pub fn join_frame(table: WatchedTable, access_token: &str, reference: &str) -> PhoenixFrame {
    PhoenixFrame {
        join_ref: Some(reference.to_string()),
        message_ref: Some(reference.to_string()),
        topic: format!("realtime:{}:{}", table.schema(), table.table()),
        event: "phx_join".to_string(),
        payload: serde_json::json!({
            "config": {
                "postgres_changes": [{
                    "event": "*",
                    "schema": table.schema(),
                    "table": table.table(),
                }],
                // We want the trigger, never the row: `presence` and `broadcast` are off, and
                // nothing here is read as data. The feed is the only truth.
                "presence": { "key": "" },
                "broadcast": { "self": false, "ack": false },
            },
            "access_token": access_token,
        }),
    }
}

/// The frame that hands the socket a fresh token without reconnecting.
pub fn access_token_frame(table: WatchedTable, access_token: &str, reference: &str) -> PhoenixFrame {
    PhoenixFrame {
        join_ref: None,
        message_ref: Some(reference.to_string()),
        topic: format!("realtime:{}:{}", table.schema(), table.table()),
        event: "access_token".to_string(),
        payload: serde_json::json!({ "access_token": access_token }),
    }
}

/// The heartbeat. Supabase closes a socket that stops sending one.
pub fn heartbeat_frame(reference: &str) -> PhoenixFrame {
    PhoenixFrame {
        join_ref: None,
        message_ref: Some(reference.to_string()),
        topic: "phoenix".to_string(),
        event: "heartbeat".to_string(),
        payload: serde_json::json!({}),
    }
}

/// What an inbound frame means to the engine.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Signal {
    /// The channel is joined and live updates are flowing.
    Joined,
    /// Something changed in the watched table. **The payload is deliberately discarded**: this is
    /// a trigger, and reading rows from it is how an engine learns to believe a stream that cannot
    /// carry deletes.
    ChangedGoRead,
    /// The server refused the join — usually an expired token.
    JoinRefused(String),
    /// A heartbeat reply. Silence beyond an interval is what detects a dead socket.
    HeartbeatAck,
    /// Anything else, kept so an unexpected frame is visible rather than swallowed.
    Other(String),
}

/// Read an inbound frame.
pub fn interpret(frame: &PhoenixFrame) -> Signal {
    match frame.event.as_str() {
        "phx_reply" => {
            let status = frame.payload.get("status").and_then(|s| s.as_str());
            match (frame.topic.as_str(), status) {
                ("phoenix", _) => Signal::HeartbeatAck,
                (_, Some("ok")) => Signal::Joined,
                (_, Some(_)) => Signal::JoinRefused(
                    frame
                        .payload
                        .get("response")
                        .map(|r| r.to_string())
                        .unwrap_or_else(|| "refused".to_string()),
                ),
                (_, None) => Signal::Other("reply with no status".to_string()),
            }
        }
        "postgres_changes" => Signal::ChangedGoRead,
        "phx_error" | "phx_close" => Signal::JoinRefused(frame.event.clone()),
        other => Signal::Other(other.to_string()),
    }
}

/// Whether live updates are working, and what the user is told.
///
/// The state is a value, not a feeling: the daemon reports it, the tray shows it, and the browser's
/// device list shows it. "Live" and "polling" are both honest; "quietly polling" is not.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LiveState {
    /// Not started yet.
    Connecting,
    /// Joined; the poll is a safety net running at its normal cadence.
    Live,
    /// The socket will not stay up. The mapping polls, and the surface says so.
    PollingFallback,
}

impl LiveState {
    /// The honest state written to `sync_mappings.state`, or `None` when nothing is wrong.
    ///
    /// `Connecting` deliberately publishes nothing: a state that flickers on every reconnect is
    /// noise, and the poll is already covering it.
    pub const fn honest_state(self) -> Option<&'static str> {
        match self {
            LiveState::Connecting | LiveState::Live => None,
            LiveState::PollingFallback => Some("polling_fallback"),
        }
    }

    /// The sentence the surface shows. SCOPE §3.1 item 8 names it almost verbatim.
    pub fn message(self, poll_interval_s: u32) -> Option<String> {
        match self {
            LiveState::Connecting | LiveState::Live => None,
            LiveState::PollingFallback => Some(format!(
                "Live updates are unavailable, so AI Matrx is checking for changes every {}. \
                 Everything still syncs — it may just take a little longer to notice a change \
                 somebody else made.",
                humanise(poll_interval_s)
            )),
        }
    }
}

/// A cadence in the words a person would use. SCOPE §3.1 item 8's own sentence is "checking every
/// minute", not "every 60 seconds" — the whole-minute cases come FIRST, or the default knob value
/// reads as machine output.
fn humanise(seconds: u32) -> String {
    match seconds {
        60 => "minute".to_string(),
        s if s % 60 == 0 && s >= 60 => format!("{} minutes", s / 60),
        1 => "second".to_string(),
        s => format!("{s} seconds"),
    }
}

/// Tracks whether the socket is healthy enough to be believed, and when to give up on it.
///
/// Pure: the caller passes the clock and the events, so "what does a laptop on hotel wifi see?" is
/// a test rather than a trip.
#[derive(Debug, Clone)]
pub struct LiveTracker {
    state: LiveState,
    consecutive_failures: u32,
    /// How many consecutive failures before the surface is told. One reconnect is not news.
    tolerance: u32,
    last_signal_s: Option<i64>,
}

impl LiveTracker {
    /// Two failures before announcing: a single dropped socket on a laptop lid-close is ordinary,
    /// and a state that appears every time is a state users learn to ignore.
    pub const DEFAULT_TOLERANCE: u32 = 2;

    /// A tracker that has not connected yet.
    pub fn new() -> Self {
        LiveTracker {
            state: LiveState::Connecting,
            consecutive_failures: 0,
            tolerance: Self::DEFAULT_TOLERANCE,
            last_signal_s: None,
        }
    }

    /// The same, with a tolerance the caller chooses.
    pub fn with_tolerance(tolerance: u32) -> Self {
        LiveTracker {
            tolerance,
            ..LiveTracker::new()
        }
    }

    /// What the surface should show.
    pub fn state(&self) -> LiveState {
        self.state
    }

    /// The socket joined.
    pub fn note_joined(&mut self, now_s: i64) {
        self.consecutive_failures = 0;
        self.last_signal_s = Some(now_s);
        self.state = LiveState::Live;
    }

    /// Any inbound frame — proof the socket is alive.
    pub fn note_signal(&mut self, now_s: i64) {
        self.last_signal_s = Some(now_s);
    }

    /// The socket failed, refused, or closed.
    pub fn note_failure(&mut self, _now_s: i64) {
        self.consecutive_failures = self.consecutive_failures.saturating_add(1);
        if self.consecutive_failures >= self.tolerance {
            self.state = LiveState::PollingFallback;
        }
    }

    /// Nothing has arrived for `silence_limit_s` — a socket that is open but dead, which is the
    /// failure mode a plain "is it connected?" check never catches.
    pub fn note_tick(&mut self, now_s: i64, silence_limit_s: i64) {
        if self.state != LiveState::Live {
            return;
        }
        if let Some(last) = self.last_signal_s {
            if now_s.saturating_sub(last) > silence_limit_s {
                self.consecutive_failures = self.consecutive_failures.saturating_add(1);
                if self.consecutive_failures >= self.tolerance {
                    self.state = LiveState::PollingFallback;
                }
            }
        }
    }
}

impl Default for LiveTracker {
    fn default() -> Self {
        Self::new()
    }
}
