//! The cloud reflection of the session state (§8b, S16, C14).
//!
//! `public.app_instances` gains **nothing** — no lane owns adding a session column to it. Instead
//! the daemon writes, **on the still-valid access token**, into every one of its own
//! `files.sync_mappings` rows: `state`, `state_reason`, `state_changed_at`. `desired_state` is the
//! user's own choice (C4) and is never touched.
//!
//! **Branch B is a first-class path, not an error handler.** The table may not exist yet on the
//! live server until the server lane's migration 032 lands; a write that cannot be made records
//! `cloud_state_write_pending` in the journal and the browser falls back to
//! `app_instances.last_seen` staleness. Nothing pretends the write happened, and the daemon never
//! fails because of it.

use super::error::{CustodyError, Result};
use super::session::SessionState;
use async_trait::async_trait;
use std::sync::Mutex;
use std::time::Duration;

/// What one attempt at §8b's write actually achieved. Every variant is reported honestly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CloudWriteOutcome {
    /// The write landed on this many of the device's mapping rows.
    Written {
        /// How many rows carry the new state.
        rows: u64,
    },
    /// This device has no `app_instances.id` yet, so it owns no mapping rows. Not a failure —
    /// there is nothing to write, and the journal says so.
    NoDeviceRegistered,
    /// S16 branch B: the table or the RPC is absent on this server build. The caller records
    /// `cloud_state_write_pending`.
    TablePending {
        /// The server's own words, for the log line.
        detail: String,
    },
}

/// The one seam between custody and `files.sync_mappings`.
#[async_trait]
pub trait CloudStateWriter: Send + Sync + std::fmt::Debug {
    /// Write `state` + `state_reason` + `state_changed_at` to every non-deleted mapping row whose
    /// `device_id` is `device_id`, authenticated as `access_token`.
    async fn write_mapping_state(
        &self,
        access_token: &str,
        device_id: &str,
        state: SessionState,
        state_reason: &str,
        changed_at: &str,
    ) -> Result<CloudWriteOutcome>;
}

/// The real writer: PostgREST against the `files` schema.
#[derive(Debug)]
pub struct PostgrestCloudStateWriter {
    endpoint: String,
    publishable_key: String,
    http: reqwest::Client,
}

impl PostgrestCloudStateWriter {
    /// Build a writer against `supabase_url` using the anon/publishable key as the `apikey`.
    pub fn new(supabase_url: &str, publishable_key: &str) -> Result<Self> {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(20))
            .build()
            .map_err(|e| CustodyError::Configuration {
                setting: "http client",
                detail: e.to_string(),
            })?;
        Ok(PostgrestCloudStateWriter {
            endpoint: format!(
                "{}/rest/v1/sync_mappings",
                supabase_url.trim_end_matches('/')
            ),
            publishable_key: publishable_key.to_string(),
            http,
        })
    }
}

#[async_trait]
impl CloudStateWriter for PostgrestCloudStateWriter {
    async fn write_mapping_state(
        &self,
        access_token: &str,
        device_id: &str,
        state: SessionState,
        state_reason: &str,
        changed_at: &str,
    ) -> Result<CloudWriteOutcome> {
        let url = format!(
            "{}?device_id=eq.{}&deleted_at=is.null",
            self.endpoint,
            urlencoding::encode(device_id)
        );
        let body = serde_json::json!({
            "state": state.as_str(),
            "state_reason": state_reason,
            "state_changed_at": changed_at,
        });

        let response = self
            .http
            .patch(&url)
            .header("apikey", &self.publishable_key)
            .header(reqwest::header::AUTHORIZATION, format!("Bearer {access_token}"))
            // `files` is not the default exposed schema; PostgREST selects it by header.
            .header("Content-Profile", "files")
            .header("Accept-Profile", "files")
            .header("Prefer", "return=representation")
            .json(&body)
            .send()
            .await
            .map_err(|e| CustodyError::CloudStateWrite {
                detail: format!("the request did not reach the server: {e}"),
            })?;

        let status = response.status();
        let text = response.text().await.unwrap_or_default();

        if status.is_success() {
            let rows = serde_json::from_str::<serde_json::Value>(&text)
                .ok()
                .and_then(|v| v.as_array().map(|a| a.len() as u64))
                .unwrap_or(0);
            return Ok(CloudWriteOutcome::Written { rows });
        }

        // S16 branch B: the table (or its exposure) is not there yet. PostgREST says so with
        // PGRST205/PGRST106 and a 404; a 404 on this route can mean nothing else.
        if status.as_u16() == 404 || text.contains("PGRST205") || text.contains("PGRST106") {
            return Ok(CloudWriteOutcome::TablePending {
                detail: format!("HTTP {status}: {}", text.trim()),
            });
        }

        Err(CustodyError::CloudStateWrite {
            detail: format!("HTTP {status}: {}", text.trim()),
        })
    }
}

/// A scripted writer for the §13 battery. **A mock; never product evidence.**
#[derive(Debug, Default)]
pub struct FakeCloudStateWriter {
    calls: Mutex<Vec<(String, String, String)>>,
    outcome: Mutex<Option<CloudWriteOutcome>>,
}

impl FakeCloudStateWriter {
    /// A fake that reports one row written.
    pub fn new() -> Self {
        Self::default()
    }

    /// Script the next (and every subsequent) outcome.
    pub fn set_outcome(&self, outcome: CloudWriteOutcome) {
        *self.outcome.lock().expect("fake cloud writer") = Some(outcome);
    }

    /// `(device_id, state, reason)` for every call made, in order.
    pub fn calls(&self) -> Vec<(String, String, String)> {
        self.calls.lock().expect("fake cloud writer").clone()
    }
}

#[async_trait]
impl CloudStateWriter for FakeCloudStateWriter {
    async fn write_mapping_state(
        &self,
        _access_token: &str,
        device_id: &str,
        state: SessionState,
        state_reason: &str,
        _changed_at: &str,
    ) -> Result<CloudWriteOutcome> {
        self.calls.lock().expect("fake cloud writer").push((
            device_id.to_string(),
            state.as_str().to_string(),
            state_reason.to_string(),
        ));
        Ok(self
            .outcome
            .lock()
            .expect("fake cloud writer")
            .clone()
            .unwrap_or(CloudWriteOutcome::Written { rows: 1 }))
    }
}
