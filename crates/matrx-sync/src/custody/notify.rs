//! One OS notification per state entry (S18).
//!
//! §13 proof 1's whole point is that the app can be quit entirely — so the tray and the app are
//! invisible exactly when the session dies unattended, and every champion notifies here. If the
//! Tauri host is running it notifies; if it is not, **the daemon notifies itself**.
//!
//! Fires on entering `sign_in_needed` and `credential_store_unavailable` only, once per state
//! entry, deduplicated by `(state, since)` and never repeated by the retry loop. A notifier that
//! refuses is a logged line and nothing more — the state is still in the journal, the tray and the
//! app (law 4: it announces itself, it does not fail the daemon).

use super::error::{CustodyError, Result};
use std::sync::Mutex;

/// The seam. One method, because one message shape is all S18 has.
pub trait Notifier: Send + Sync + std::fmt::Debug {
    /// Post one notification.
    fn post(&self, title: &str, body: &str) -> Result<()>;
}

/// The real notifier: `mac-notification-sys` on macOS (it posts from our own signed bundle, where
/// an `osascript` shell-out would inherit no bundle identity, show the Script Editor's icon and
/// fail silently under a hardened runtime); `notify-rust` elsewhere — libnotify/D-Bus on Linux,
/// a WinRT toast on Windows.
#[derive(Debug)]
pub struct OsNotifier {
    /// Shown as the notification's source. Also the bundle identity on macOS.
    app_name: String,
}

impl OsNotifier {
    /// A notifier that posts as `app_name`.
    pub fn new(app_name: impl Into<String>) -> Self {
        OsNotifier {
            app_name: app_name.into(),
        }
    }
}

#[cfg(target_os = "macos")]
impl Notifier for OsNotifier {
    fn post(&self, title: &str, body: &str) -> Result<()> {
        // Best effort: the bundle identifier is only settable once per process, and a failure to
        // set it means the notification carries the default identity, not that it cannot be sent.
        let _ = mac_notification_sys::set_application("com.aimatrx.desktop.syncd");
        mac_notification_sys::send_notification(
            &self.app_name,
            Some(title),
            body,
            Some(&mac_notification_sys::Notification::new()),
        )
        .map(|_| ())
        .map_err(|e| CustodyError::Notification {
            cause: e.to_string(),
        })
    }
}

#[cfg(not(target_os = "macos"))]
impl Notifier for OsNotifier {
    fn post(&self, title: &str, body: &str) -> Result<()> {
        notify_rust::Notification::new()
            .appname(&self.app_name)
            .summary(title)
            .body(body)
            .show()
            .map(|_| ())
            .map_err(|e| CustodyError::Notification {
                cause: e.to_string(),
            })
    }
}

/// A notifier that records instead of posting. Used by the battery, and by the daemon on a
/// headless host where posting would fail on every call.
#[derive(Debug, Default)]
pub struct RecordingNotifier {
    posted: Mutex<Vec<(String, String)>>,
}

impl RecordingNotifier {
    /// An empty recorder.
    pub fn new() -> Self {
        Self::default()
    }

    /// `(title, body)` for every notification posted, in order.
    pub fn posted(&self) -> Vec<(String, String)> {
        self.posted.lock().expect("recording notifier").clone()
    }
}

impl Notifier for RecordingNotifier {
    fn post(&self, title: &str, body: &str) -> Result<()> {
        self.posted
            .lock()
            .expect("recording notifier")
            .push((title.to_string(), body.to_string()));
        Ok(())
    }
}
