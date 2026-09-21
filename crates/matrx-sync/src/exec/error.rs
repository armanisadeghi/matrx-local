//! Every way an op can fail, as a **named honest state with a remedy** — never a panic, never a
//! silent skip (law 4).
//!
//! The executor is the first module in this crate that can fail because of the outside world: a
//! disk that filled, a permission that was revoked, a server that said 412. SPEC-ENGINE §3.6 is
//! explicit that a surface is *absent or honest*, so every failure here resolves to one of three
//! things and nothing else:
//!
//! * a **mapping state** from [`crate::states::HONEST_STATES`] (`permission_denied`,
//!   `suspended_disk_full`, `over_quota`, `offline`, `sign_in_needed`, …) — the user sees a
//!   sentence and a one-click remedy;
//! * a **re-plan** ([`ExecError::is_race`]) — the world moved under us, so the rest of the plan is
//!   thrown away and a new one is derived from refreshed trees. **A 412 is never retried blind.**
//! * a **deferral** ([`ExecError::retryable`]) — the op goes back on the queue with a backoff.
//!
//! Nothing in this enum widens `honest_states.json`. Every value [`ExecError::honest_state`]
//! returns is already in that artifact, and a test asserts it.

use crate::feed::FeedError;
use crate::SyncError;
use std::fmt;

/// What went wrong while applying one op.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum ExecError {
    /// The local file is gone. Between planning and acting is a real window; a file that vanishes
    /// in it is a change the next scan sees, not an error.
    Vanished {
        /// The mapping-relative path.
        path: String,
    },
    /// I3: the bytes on disk are not the pre-image the plan was made against.
    PreImageMismatch {
        /// The mapping-relative path.
        path: String,
        /// What the plan expected to find.
        expected: String,
        /// What is actually there, when it could be read.
        found: Option<String>,
    },
    /// A `Create` was planned for a path that already holds a *different* file. Writing it would
    /// destroy that file with no conflict copy and nothing on screen.
    UnexpectedLocalFile {
        /// The mapping-relative path.
        path: String,
    },
    /// The server refused on a precondition (SPEC-SERVER §3.2). Carries the whole 412 body,
    /// because that body **is** the engine's conflict input.
    PreconditionFailed(Box<Precondition>),
    /// The cloud row this op names is not there any more.
    RemoteGone {
        /// The mapping-relative path.
        path: String,
    },
    /// A name the destination refuses, or a `(created_by, file_path)` collision — SPEC-SERVER
    /// §4.2's `23505` → 409 `path_conflict`.
    PathConflict {
        /// The mapping-relative path.
        path: String,
        /// What the server said.
        detail: String,
    },
    /// The OS refused to read or write. macOS TCC, an ACL, a read-only volume.
    PermissionDenied {
        /// The mapping-relative path.
        path: String,
        /// What the OS said.
        detail: String,
    },
    /// The disk filled up mid-write. The staged file is removed; nothing partial reaches a user
    /// path (I4).
    DiskFull {
        /// What the OS said.
        detail: String,
    },
    /// The organization is out of storage (SPEC-ENGINE §3.1, HTTP 507).
    OverQuota {
        /// What the server said.
        detail: String,
    },
    /// The network, a timeout, a captive portal. Not a failure — a wait.
    Offline {
        /// What the transport said.
        detail: String,
    },
    /// The token was refused and the custodian could not replace it.
    SignInNeeded {
        /// What the server said.
        detail: String,
    },
    /// `X-Organization-Id` was missing or the caller is not a member of it.
    OrganizationRefused {
        /// What the server said.
        detail: String,
    },
    /// Downloaded bytes did not hash to the checksum the plan carried (I4). They are discarded;
    /// nothing is written into place.
    ChecksumMismatch {
        /// The mapping-relative path.
        path: String,
        /// What the plan said the bytes would hash to.
        expected: String,
        /// What they actually hashed to.
        found: String,
    },
    /// Two on-disk names normalise or case-fold to one tree key, so acting on the key would pick
    /// one of the user's files and destroy the other. Never a silent pick: it becomes a conflict
    /// row and the path waits for a human (invariant I8).
    NameCollision {
        /// The mapping-relative key in dispute.
        path: String,
        /// Which class — `case_collision` or `unicode_collision`.
        kind: crate::model::ConflictKind,
    },
    /// The direction forbids this op. **A defect, not a state**: the planner must never emit an
    /// op its own direction forbids, so this is reported loudly and the op is refused.
    DirectionRefused {
        /// The direction in force.
        direction: &'static str,
        /// The op the planner emitted.
        op: &'static str,
        /// The mapping-relative path.
        path: String,
    },
    /// The journal refused — a confirmation that did not satisfy invariant I1 or I2.
    Refused(String),
    /// Anything else the OS said.
    Io {
        /// The mapping-relative path, when there is one.
        path: String,
        /// What the OS said.
        detail: String,
    },
    /// The server answered with something this client cannot read, or a status with no meaning.
    Server {
        /// The HTTP status.
        code: u16,
        /// The first 200 bytes of the body.
        detail: String,
    },
}

/// The body of a 412 (SPEC-SERVER §3.2, exact).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Precondition {
    /// The cloud file that refused.
    pub file_id: Option<String>,
    /// The checksum the server actually holds now.
    pub current_checksum: Option<String>,
    /// The version the server actually holds now.
    pub current_version: Option<i64>,
    /// The size the server actually holds now.
    pub current_size_bytes: Option<i64>,
    /// When the server last changed it.
    pub current_updated_at: Option<String>,
    /// Which device wrote what is there now (C13) — how "this was my own earlier write" is told
    /// apart from "another device beat me".
    pub origin_device_id: Option<String>,
    /// What this client sent.
    pub expected_checksum: Option<String>,
}

impl ExecError {
    /// A short stable token written to `ops.error_code` and to activity rows.
    pub const fn code(&self) -> &'static str {
        match self {
            ExecError::Vanished { .. } => "vanished",
            ExecError::PreImageMismatch { .. } => "pre_image_mismatch",
            ExecError::UnexpectedLocalFile { .. } => "unexpected_local_file",
            ExecError::PreconditionFailed(_) => "precondition_failed",
            ExecError::RemoteGone { .. } => "remote_gone",
            ExecError::PathConflict { .. } => "path_conflict",
            ExecError::PermissionDenied { .. } => "permission_denied",
            ExecError::DiskFull { .. } => "disk_full",
            ExecError::OverQuota { .. } => "over_quota",
            ExecError::Offline { .. } => "offline",
            ExecError::SignInNeeded { .. } => "sign_in_needed",
            ExecError::OrganizationRefused { .. } => "organization_refused",
            ExecError::ChecksumMismatch { .. } => "checksum_mismatch",
            ExecError::NameCollision { .. } => "name_collision",
            ExecError::DirectionRefused { .. } => "direction_refused",
            ExecError::Refused(_) => "journal_refused",
            ExecError::Io { .. } => "io_error",
            ExecError::Server { .. } => "server_error",
        }
    }

    /// Whether the world moved under the plan, so the **rest of the plan is discarded and a new
    /// one derived** from refreshed trees.
    ///
    /// This is the one answer to a 412. Retrying the same bytes against the same precondition
    /// loses identically by contract (SPEC-SERVER §3.3 item 3), and retrying *without* the
    /// precondition is how the other device's bytes disappear.
    pub const fn is_race(&self) -> bool {
        matches!(
            self,
            ExecError::PreconditionFailed(_)
                | ExecError::PreImageMismatch { .. }
                | ExecError::UnexpectedLocalFile { .. }
                | ExecError::RemoteGone { .. }
                | ExecError::Vanished { .. }
        )
    }

    /// Whether waiting and trying the SAME op again is the right response.
    pub const fn retryable(&self) -> bool {
        match self {
            ExecError::Offline { .. } | ExecError::ChecksumMismatch { .. } => true,
            ExecError::Server { code, .. } => *code >= 500 || *code == 429,
            // A 401 is retryable only after the custodian refreshes; the daemon sequences that,
            // so from the executor's seat it is a state, not a retry.
            _ => false,
        }
    }

    /// The honest state this failure puts the **mapping** into, or `None` when it is an ordinary
    /// transient the user never needs to see.
    ///
    /// Every value returned here is in `contracts/honest_states.json` with the `mapping` scope;
    /// `tests/executor.rs` asserts it, so this function cannot invent a state.
    pub const fn honest_state(&self) -> Option<&'static str> {
        match self {
            ExecError::PermissionDenied { .. } => Some("permission_denied"),
            ExecError::DiskFull { .. } => Some("suspended_disk_full"),
            ExecError::OverQuota { .. } => Some("over_quota"),
            ExecError::Offline { .. } => Some("offline"),
            ExecError::SignInNeeded { .. } => Some("sign_in_needed"),
            ExecError::OrganizationRefused { .. } => Some("admission_revoked"),
            _ => None,
        }
    }

    /// Whether this failure stops the mapping until a human acts.
    pub const fn suspends(&self) -> bool {
        matches!(
            self,
            ExecError::PermissionDenied { .. }
                | ExecError::DiskFull { .. }
                | ExecError::OverQuota { .. }
                | ExecError::SignInNeeded { .. }
                | ExecError::OrganizationRefused { .. }
        )
    }

    /// Build the 412 case from a server body, keeping whatever the body actually carried.
    pub fn precondition_from_json(body: &str) -> ExecError {
        let parsed: serde_json::Value = serde_json::from_str(body).unwrap_or(serde_json::Value::Null);
        let text = |k: &str| {
            parsed
                .get(k)
                .and_then(serde_json::Value::as_str)
                .map(str::to_string)
        };
        ExecError::PreconditionFailed(Box::new(Precondition {
            file_id: text("file_id"),
            current_checksum: text("current_checksum"),
            current_version: parsed.get("current_version").and_then(serde_json::Value::as_i64),
            current_size_bytes: parsed
                .get("current_size_bytes")
                .and_then(serde_json::Value::as_i64),
            current_updated_at: text("current_updated_at"),
            origin_device_id: text("origin_device_id"),
            expected_checksum: text("expected_checksum"),
        }))
    }
}

impl fmt::Display for ExecError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ExecError::Vanished { path } => write!(f, "{path} is no longer on disk"),
            ExecError::PreImageMismatch { path, expected, found } => write!(
                f,
                "{path} changed under us: expected {expected}, found {found:?}"
            ),
            ExecError::UnexpectedLocalFile { path } => {
                write!(f, "a different file already exists at {path}")
            }
            ExecError::PreconditionFailed(p) => write!(
                f,
                "the cloud copy changed since we read it (now version {:?}, checksum {:?})",
                p.current_version, p.current_checksum
            ),
            ExecError::RemoteGone { path } => write!(f, "the cloud copy of {path} is gone"),
            ExecError::PathConflict { path, detail } => {
                write!(f, "the cloud already holds something at {path}: {detail}")
            }
            ExecError::PermissionDenied { path, detail } => {
                write!(f, "not allowed to touch {path}: {detail}")
            }
            ExecError::DiskFull { detail } => write!(f, "the disk is full: {detail}"),
            ExecError::OverQuota { detail } => write!(f, "cloud storage is full: {detail}"),
            ExecError::Offline { detail } => write!(f, "cannot reach the file service: {detail}"),
            ExecError::SignInNeeded { detail } => write!(f, "sign-in is needed: {detail}"),
            ExecError::OrganizationRefused { detail } => {
                write!(f, "this organization refused the request: {detail}")
            }
            ExecError::ChecksumMismatch { path, expected, found } => write!(
                f,
                "the bytes downloaded for {path} hashed to {found}, not {expected}; they were \
                 discarded"
            ),
            ExecError::NameCollision { path, kind } => write!(
                f,
                "two files on this disk both claim the name {path} ({}); choosing one would \
                 destroy the other",
                kind.as_str()
            ),
            ExecError::DirectionRefused { direction, op, path } => write!(
                f,
                "a {direction} mapping refused a {op} on {path}; the planner must never emit it"
            ),
            ExecError::Refused(why) => write!(f, "the journal refused: {why}"),
            ExecError::Io { path, detail } => write!(f, "{path}: {detail}"),
            ExecError::Server { code, detail } => write!(f, "the server answered {code}: {detail}"),
        }
    }
}

impl std::error::Error for ExecError {}

impl From<SyncError> for ExecError {
    fn from(e: SyncError) -> Self {
        match e {
            SyncError::SyncedWriteRefused(why) => ExecError::Refused(why),
            other => ExecError::Refused(other.to_string()),
        }
    }
}

impl From<FeedError> for ExecError {
    fn from(e: FeedError) -> Self {
        match e {
            FeedError::Transport(d) => ExecError::Offline { detail: d },
            FeedError::Unauthorised(d) => ExecError::SignInNeeded { detail: d },
            FeedError::OrganizationRefused(d) => ExecError::OrganizationRefused { detail: d },
            FeedError::CursorRejected(d) | FeedError::Malformed(d) => ExecError::Server {
                code: 400,
                detail: d,
            },
            FeedError::Status { code, detail } => ExecError::Server { code, detail },
        }
    }
}

/// The executor's result alias.
pub type ExecResult<T> = std::result::Result<T, ExecError>;
