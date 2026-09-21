//! The custody error type — every failure is typed and carries a sentence (law 4).
//!
//! Nothing in this module returns a bare string or swallows a cause. A `CustodyError` that
//! reaches a control-API caller is rendered into SPEC-ENGINE §3.1's envelope by
//! [`CustodyError::code`], [`CustodyError::message`] and [`CustodyError::remedy`]; a
//! `CustodyError` that reaches a log line is rendered by `Display`.

use std::fmt;

/// Everything credential custody can fail with.
#[derive(Debug)]
#[non_exhaustive]
pub enum CustodyError {
    /// The OS credential store refused to open, read or write (S7). Never falls back to disk.
    CredentialStore {
        /// What was being attempted, in the imperative ("read the keychain item").
        operation: &'static str,
        /// The underlying store's own words.
        cause: String,
    },
    /// A network or transport failure talking to the authorization server. Retryable (S5 backoff).
    Transport {
        /// Which endpoint.
        endpoint: &'static str,
        /// The underlying words.
        cause: String,
    },
    /// The authorization server refused terminally — `invalid_grant` and friends. NOT retryable.
    GrantRefused {
        /// The server's `error` field.
        error: String,
        /// The server's `error_description`, when it sent one.
        description: Option<String>,
    },
    /// The authorization server was REACHED and refused this device's session with a 4xx that is
    /// not one of the named grant refusals — for example GoTrue's
    /// `Client authentication not allowed for non-OAuth session`.
    ///
    /// A server that answered is not an absent network (law 4): this is never rendered as
    /// "check this computer's internet connection", and it is not retried forever. It is only
    /// raised after **every** token endpoint this device can use has refused the same token.
    AuthServerRefused {
        /// HTTP status observed.
        status: u16,
        /// The server's own words, or the closest thing it sent.
        detail: String,
    },
    /// The authorization server answered with something that is not a token response — a
    /// captive-portal HTML 200, a proxy 407, a 5xx. Retryable, never treated as a revocation.
    AmbiguousResponse {
        /// HTTP status observed.
        status: u16,
        /// What made it unusable, in one sentence.
        detail: String,
    },
    /// `POST /v1/sign-in/callback` carried a `state` this daemon never issued, or one that expired
    /// (S5's 10-minute TTL, S14's cross-world case).
    UnknownTransaction,
    /// A route that needs a session was called while there is none.
    NotSignedIn,
    /// The fixed OAuth loopback port is held by another process (S21's named failure).
    LoopbackPortUnavailable {
        /// The port that could not be bound.
        port: u16,
        /// The OS's words.
        cause: String,
    },
    /// The journal refused. Wraps the crate's own error.
    Journal(crate::SyncError),
    /// A cloud state write (S16) could not be made. Never fails the daemon: the caller records
    /// `cloud_state_write_pending` and carries on.
    CloudStateWrite {
        /// Why the write could not land.
        detail: String,
    },
    /// The OS notification subsystem refused (S18). Never fails the daemon.
    Notification {
        /// The notifier's own words.
        cause: String,
    },
    /// The dev-world-only test-harness session door was called on a daemon that is NOT in the
    /// dev world (MXL-D-091's guard).
    ///
    /// The door exists so an automated browser harness can put `admin@admin.com` in front of
    /// every screen without the product regaining a password field. A live-world daemon holds a
    /// real person's session, so the door is not merely unused there — it is refused, and the
    /// refusal names the world it was asked in.
    HarnessDoorNotInDevWorld {
        /// The world the daemon is actually running in.
        world: &'static str,
    },
    /// A value the daemon was configured with is unusable.
    Configuration {
        /// The setting's name.
        setting: &'static str,
        /// Why it is unusable.
        detail: String,
    },
}

impl CustodyError {
    /// The SPEC-ENGINE §3.1 `error.code` this failure is rendered as.
    pub fn code(&self) -> &'static str {
        match self {
            CustodyError::CredentialStore { .. } => "credential_store_unavailable",
            CustodyError::Transport { .. } | CustodyError::AmbiguousResponse { .. } => "offline",
            CustodyError::GrantRefused { .. } | CustodyError::AuthServerRefused { .. } => {
                "sign_in_needed"
            }
            CustodyError::UnknownTransaction => "unknown_transaction",
            CustodyError::NotSignedIn => "signed_out",
            CustodyError::LoopbackPortUnavailable { .. } => "loopback_port_unavailable",
            CustodyError::Journal(_) => "internal",
            CustodyError::CloudStateWrite { .. } => "internal",
            CustodyError::Notification { .. } => "internal",
            CustodyError::HarnessDoorNotInDevWorld { .. } => "harness_door_not_in_dev_world",
            CustodyError::Configuration { .. } => "internal",
        }
    }

    /// The user-facing sentence. No surface invents its own wording for a state we named.
    pub fn message(&self) -> String {
        self.to_string()
    }

    /// The one-click remedy sentence that travels with the message.
    pub fn remedy(&self) -> &'static str {
        match self {
            // The remedy must name THIS machine's situation. Telling a Mac user to install
            // gnome-keyring is a sentence that helps nobody, and law 4 asks for a remedy, not a
            // paragraph that happens to contain one. Observed live: a macOS keychain prompt
            // answered with the Linux keyring sentence.
            CustodyError::CredentialStore { .. } => {
                if cfg!(target_os = "macos") {
                    "macOS is asking permission for AI Matrx Sync to use your keychain, and a \
                     background service has no window to ask in. Open AI Matrx and sign in again — \
                     the prompt appears while the app is in front, and allowing it once is enough."
                } else if cfg!(target_os = "windows") {
                    "Windows Credential Manager refused. Sign in to Windows as the account that \
                     installed AI Matrx, then open AI Matrx and sign in again."
                } else {
                    "Install and unlock a system keyring — gnome-keyring or kwallet — or this \
                     device will need to sign in again after every restart."
                }
            }
            CustodyError::Transport { .. } | CustodyError::AmbiguousResponse { .. } => {
                "Check this computer's internet connection; sync retries on its own."
            }
            CustodyError::GrantRefused { .. } | CustodyError::AuthServerRefused { .. } => {
                "Sign in again on this computer."
            }
            CustodyError::UnknownTransaction => {
                "That sign-in link belongs to a different copy of AI Matrx (or has expired) — \
                 start sign-in again from the app you want to sign in to."
            }
            CustodyError::NotSignedIn => "Sign in on this computer.",
            CustodyError::LoopbackPortUnavailable { .. } => {
                "Quit whatever is holding that port, or sign in from the installed app, which uses \
                 the aimatrx:// link instead."
            }
            CustodyError::Journal(_) => "Restart sync; if it keeps happening, reinstall AI Matrx.",
            CustodyError::CloudStateWrite { .. } => {
                "Nothing to do here — the browser falls back to showing this device as silent."
            }
            CustodyError::Notification { .. } => {
                "Nothing to do here — the state is still shown in the app and the tray."
            }
            CustodyError::HarnessDoorNotInDevWorld { .. } => {
                "Nothing to do — this is a test-harness route and it only exists in the dev world. \
                 Run the harness against a source daemon started with `--world dev`."
            }
            CustodyError::Configuration { .. } => {
                "Reinstall AI Matrx; this build was packaged without a value it needs."
            }
        }
    }

    /// Whether a caller should back off and try again, rather than treat the session as lost.
    pub fn retryable(&self) -> bool {
        matches!(
            self,
            CustodyError::Transport { .. }
                | CustodyError::AmbiguousResponse { .. }
                | CustodyError::CloudStateWrite { .. }
        )
    }
}

impl fmt::Display for CustodyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CustodyError::CredentialStore { operation, cause } => write!(
                f,
                "the system credential store refused to {operation}: {cause}"
            ),
            CustodyError::Transport { endpoint, cause } => {
                write!(f, "could not reach {endpoint}: {cause}")
            }
            CustodyError::GrantRefused { error, description } => match description {
                Some(d) => write!(f, "the sign-in was refused ({error}): {d}"),
                None => write!(f, "the sign-in was refused ({error})"),
            },
            CustodyError::AuthServerRefused { status, detail } => write!(
                f,
                "the sign-in service refused this device's session (HTTP {status}): {detail}"
            ),
            CustodyError::AmbiguousResponse { status, detail } => write!(
                f,
                "the sign-in server answered {status} with something that is not a token: {detail}"
            ),
            CustodyError::UnknownTransaction => {
                write!(f, "no sign-in is in progress for that link on this computer")
            }
            CustodyError::NotSignedIn => write!(f, "this computer is not signed in"),
            CustodyError::LoopbackPortUnavailable { port, cause } => write!(
                f,
                "the sign-in callback port {port} is already in use: {cause}"
            ),
            CustodyError::Journal(e) => write!(f, "the sync journal refused: {e}"),
            CustodyError::CloudStateWrite { detail } => {
                write!(f, "this device's sync rows could not be updated: {detail}")
            }
            CustodyError::Notification { cause } => {
                write!(f, "the system notification could not be posted: {cause}")
            }
            CustodyError::HarnessDoorNotInDevWorld { world } => write!(
                f,
                "the test-harness session door was asked of a {world}-world daemon; it exists in \
                 the dev world only"
            ),
            CustodyError::Configuration { setting, detail } => {
                write!(f, "the setting {setting} is unusable: {detail}")
            }
        }
    }
}

impl std::error::Error for CustodyError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            CustodyError::Journal(e) => Some(e),
            _ => None,
        }
    }
}

impl From<crate::SyncError> for CustodyError {
    fn from(e: crate::SyncError) -> Self {
        CustodyError::Journal(e)
    }
}

/// The custody result alias.
pub type Result<T> = std::result::Result<T, CustodyError>;
