//! The `/v1` control API (SPEC-ENGINE §3, C1/C5/C6/C7/C8).
//!
//! **Two transports, one API.** HTTP/1.1 + JSON over the per-user Unix socket (Windows: the named
//! pipe) **and** over one loopback TCP listener in the daemon band — both serve the identical
//! `/v1` routes with the identical auth. The Tauri host and the Python engine prefer the
//! socket/pipe; the webview is a browser context and uses the TCP listener under §3's
//! browser-origin allow-list.
//!
//! **What this daemon serves today is FS-C5's slice and nothing more**: `GET /v1/health`,
//! `GET /v1/version`, the five custody-owned auth routes (C1), `POST /v1/shutdown` and
//! `GET /v1/events`. Every other route in SPEC-ENGINE's table belongs to FS-L2a and **returns 404
//! with the error envelope — never a stub that answers with a plausible shape** (law 4).

mod cors;
mod routes;
mod server;

pub use server::{bind, serve, LocalEndpoint};

use matrx_sync::custody::Custodian;
use std::sync::Arc;

/// The daemon's protocol version. One integer, bumped only on a breaking control-API change; the
/// daemon serves N and N−1 (SPEC-ENGINE §1.6).
pub const PROTOCOL_VERSION: u32 = 1;

/// The lowest protocol version this daemon can still speak to.
pub const MIN_PROTOCOL_VERSION: u32 = 1;

/// Everything a request handler needs.
pub struct ApiState {
    /// The custodian — the device's only session holder.
    pub custodian: Custodian,
    /// The two scoped tokens minted at start (S17).
    pub tokens: crate::discovery::ScopedTokens,
    /// `live` or `dev`.
    pub world: matrx_sync::custody::World,
    /// The daemon build's version string.
    pub daemon_version: String,
    /// This binary's own path, for the §1.6 handshake.
    pub executable_path: String,
    /// The loopback listener's port, for the `Host` allow-list.
    pub tcp_port: Option<u16>,
    /// Raised by `POST /v1/shutdown`; the run loop watches it.
    ///
    /// A `watch` channel and **not** a `Notify`: a notification has to find a waiter, and the run
    /// loop does not become one until after it has bound its listeners, published its discovery
    /// file and started adopting a session — which is exactly the stretch in which a shutdown is
    /// most likely to arrive and most important to honour. A `watch` holds the request until
    /// somebody reads it, so the window does not exist rather than being narrow.
    pub shutdown: tokio::sync::watch::Sender<bool>,
    /// The teardown budget, in seconds (`sync.shutdown_budget_s`).
    pub shutdown_budget_s: u64,
}

/// A shared handle to the API state.
pub type SharedState = Arc<ApiState>;
