//! The HTTP half of the change feed.
//!
//! Verified against the live service on 2026-09-15 (`admin@admin.com`, read-only):
//!
//! * `GET https://files.matrxserver.com/files/sync/changes?limit=&cursor=` → `{files, next_cursor,
//!   has_more, server_time}`
//! * `GET …/files/sync/folders?…` → `{folders, next_cursor, has_more, server_time, truncated}`
//! * **Both require `X-Organization-Id` as well as the bearer token.** Without it the service
//!   answers `400 organization_required`: *"This request carried an identity but no organization …
//!   the server verifies your membership and never chooses one for you."* SPEC-SERVER §2.3 puts
//!   `organization_id` on the ENTRY but does not name the request header; it is the platform's
//!   every-write-carries-an-organization law reaching reads, and it is recorded here because a
//!   client built from the spec alone gets a 400 on its first call.
//!
//! The transport is a trait so the executor and the harness can drive this without a network, and
//! so the one place that speaks HTTP is one file.

use crate::feed::entry::{FilePage, FolderPage};
use std::time::Duration;

/// Why a feed read failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FeedError {
    /// The token was refused. The custodian re-authenticates and the caller retries.
    Unauthorised(String),
    /// The organization header was missing or the caller is not a member.
    OrganizationRefused(String),
    /// The cursor was not understood — SPEC-SERVER §2.4 promises a 400 on garbage. The cursor is
    /// dropped and the feed re-bootstraps from the beginning, which is slow and correct.
    CursorRejected(String),
    /// The service answered, but not with something this client can read.
    Malformed(String),
    /// The network, a timeout, a captive portal.
    Transport(String),
    /// Any other status.
    Status {
        /// The HTTP status.
        code: u16,
        /// What the body said.
        detail: String,
    },
}

impl FeedError {
    /// Whether waiting and trying again is the right response.
    pub const fn retryable(&self) -> bool {
        match self {
            FeedError::Transport(_) => true,
            // A 401 is retryable AFTER the custodian refreshes; the caller sequences that.
            FeedError::Unauthorised(_) => true,
            FeedError::Status { code, .. } => *code >= 500 || *code == 429,
            FeedError::OrganizationRefused(_)
            | FeedError::CursorRejected(_)
            | FeedError::Malformed(_) => false,
        }
    }

    /// The honest state a mapping takes while the feed cannot be read.
    ///
    /// Never a failure state for a transport problem: the feed being unreachable is `offline`, and
    /// syncing resumes by itself. Only a refusal the user must act on is anything else.
    pub const fn honest_state(&self) -> &'static str {
        match self {
            FeedError::Transport(_) | FeedError::Status { .. } => "offline",
            FeedError::Unauthorised(_) => "sign_in_needed",
            FeedError::OrganizationRefused(_) => "admission_revoked",
            FeedError::CursorRejected(_) | FeedError::Malformed(_) => "polling_fallback",
        }
    }
}

/// What the client needs to reach the service as a particular user in a particular organization.
#[derive(Debug, Clone)]
pub struct FeedAuth {
    /// The access token the custodian holds. Never stored here beyond the call.
    pub access_token: String,
    /// `X-Organization-Id`. The service refuses the call without it and never picks one for you.
    pub organization_id: String,
}

/// Where the service lives and how patient to be with it.
#[derive(Debug, Clone)]
pub struct FeedConfig {
    /// `https://files.matrxserver.com` in production; injected, never compiled in, so dev and live
    /// are a value rather than a build.
    pub base_url: String,
    /// Page size. SPEC-SERVER §2.3: 1–2000, default 500 — unchanged for folder sync.
    pub page_size: u32,
    /// Per-request timeout.
    pub timeout: Duration,
}

impl FeedConfig {
    /// The production service with the spec's default page size.
    pub fn production() -> Self {
        FeedConfig {
            base_url: "https://files.matrxserver.com".to_string(),
            page_size: 500,
            timeout: Duration::from_secs(30),
        }
    }
}

/// The feed, as the rest of the engine sees it.
///
/// A trait so the executor, the harness and the tests can all drive the same code path without a
/// network — and so the single place that speaks HTTP is [`HttpFeed`].
#[async_trait::async_trait]
pub trait FeedTransport: Send + Sync {
    /// One page of the file feed from `cursor` (or the beginning).
    async fn file_page(&self, auth: &FeedAuth, cursor: Option<&str>) -> Result<FilePage, FeedError>;
    /// One page of the folder feed from `cursor`.
    async fn folder_page(
        &self,
        auth: &FeedAuth,
        cursor: Option<&str>,
    ) -> Result<FolderPage, FeedError>;
}

/// The live service.
#[derive(Debug, Clone)]
pub struct HttpFeed {
    config: FeedConfig,
    http: reqwest::Client,
}

impl HttpFeed {
    /// A client for `config`.
    pub fn new(config: FeedConfig) -> Result<Self, FeedError> {
        let http = reqwest::Client::builder()
            .timeout(config.timeout)
            .build()
            .map_err(|e| FeedError::Transport(e.to_string()))?;
        Ok(HttpFeed { config, http })
    }

    async fn get<T: serde::de::DeserializeOwned>(
        &self,
        path: &str,
        auth: &FeedAuth,
        cursor: Option<&str>,
    ) -> Result<T, FeedError> {
        let mut url = format!(
            "{}{path}?limit={}",
            self.config.base_url.trim_end_matches('/'),
            self.config.page_size
        );
        if let Some(cursor) = cursor {
            url.push_str(&format!("&cursor={}", urlencoding::encode(cursor)));
        }
        let response = self
            .http
            .get(&url)
            .header("Authorization", format!("Bearer {}", auth.access_token))
            .header("X-Organization-Id", &auth.organization_id)
            .send()
            .await
            .map_err(|e| FeedError::Transport(e.to_string()))?;

        let status = response.status();
        let body = response
            .text()
            .await
            .map_err(|e| FeedError::Transport(e.to_string()))?;

        if !status.is_success() {
            return Err(classify_status(status.as_u16(), &body));
        }
        serde_json::from_str::<T>(&body).map_err(|e| {
            // Truncated: a feed page can be megabytes, and an error message is not a place to put
            // one — or to put a user's file names.
            FeedError::Malformed(format!("{e} (first 200 bytes: {})", &body[..body.len().min(200)]))
        })
    }
}

/// Turn a non-2xx into something with a remedy.
pub fn classify_status(code: u16, body: &str) -> FeedError {
    let lower = body.to_lowercase();
    match code {
        401 | 403 => FeedError::Unauthorised(truncate(body)),
        400 if lower.contains("organization_required") || lower.contains("organization") => {
            FeedError::OrganizationRefused(truncate(body))
        }
        400 if lower.contains("cursor") => FeedError::CursorRejected(truncate(body)),
        _ => FeedError::Status {
            code,
            detail: truncate(body),
        },
    }
}

fn truncate(body: &str) -> String {
    body.chars().take(200).collect()
}

#[async_trait::async_trait]
impl FeedTransport for HttpFeed {
    async fn file_page(&self, auth: &FeedAuth, cursor: Option<&str>) -> Result<FilePage, FeedError> {
        self.get("/files/sync/changes", auth, cursor).await
    }

    async fn folder_page(
        &self,
        auth: &FeedAuth,
        cursor: Option<&str>,
    ) -> Result<FolderPage, FeedError> {
        self.get("/files/sync/folders", auth, cursor).await
    }
}
