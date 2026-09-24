//! FS-L1 unit 4c — the production [`RemoteIo`]: the live file service.
//!
//! Every route here was exercised against the live service as `admin@admin.com` on 2026-09-24
//! before this file was written, because three of them do not behave the way the spec's prose
//! suggests and a client built from prose alone would be wrong in each case:
//!
//! * `POST files.matrxserver.com/files/upload` (multipart) takes `expected_checksum` as a form
//!   field and answers `{file_id, file_path, size_bytes, checksum, …}` — **no version**. The
//!   version every later precondition is taken against is read back with `GET /files/{id}`.
//! * A 412 arrives as `{"detail": {…SPEC-SERVER §3.2 payload…}}`, wrapped by FastAPI, on upload
//!   and on `PATCH`. A replay under the same `X-Idempotency-Key` returns the stored 200.
//! * `PATCH /files/{id}` with `{"name"}` renames and with `{"folder": "<path>"}` moves, honouring
//!   `If-Match: W/"v<n>"` — and a rename does **not** bump `current_version`.
//! * Folders are created on the **platform host** (`server.app.matrxserver.com/folders`,
//!   `{folder_path}`, idempotent, missing parents created), not on the file service, which has
//!   no folder-create route.
//! * `DELETE /files/{id}` accepts **no precondition** of any kind. SPEC-SERVER §3.1 names three
//!   preconditioned routes and delete is not one of them. The version check is therefore made
//!   here, by reading the row first — which narrows the race to the gap between two requests but
//!   cannot close it. Recorded as a spec gap, not papered over.
//!
//! Every call carries `X-Organization-Id` — the mapping's own organization, never a default — and,
//! when the device is registered, `X-Matrx-Device-Id` so the feed's echo suppression works (C13).
//! The organization refusals come back as five codes; only `organization_unverifiable` is worth
//! retrying.

use super::error::{ExecError, ExecResult, Precondition};
use super::io::{DeleteRequest, ProgressSink, RemoteIo, RemoteObject, RenameRequest, UploadRequest};
use crate::knobs::TransferKnobs;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Semaphore;

/// Where the access token comes from. The custodian owns it and rotates it; this client asks for
/// it on every call and never keeps it.
#[async_trait::async_trait]
pub trait AccessToken: Send + Sync {
    /// A currently valid access token, or the state that stops syncing.
    async fn access_token(&self) -> ExecResult<String>;
}

/// A fixed token — for the live test tier, which signs in once per run.
#[derive(Debug, Clone)]
pub struct StaticToken(pub String);

#[async_trait::async_trait]
impl AccessToken for StaticToken {
    async fn access_token(&self) -> ExecResult<String> {
        Ok(self.0.clone())
    }
}

/// Where the two services live, and which mapping this client acts for.
#[derive(Debug, Clone)]
pub struct RemoteConfig {
    /// The file service — `https://files.matrxserver.com`. Injected, never compiled in.
    pub files_base_url: String,
    /// The platform host that owns folder creation — `https://server.app.matrxserver.com`.
    pub host_base_url: String,
    /// `X-Organization-Id`: the MAPPING's organization, always explicit (D12).
    pub organization_id: String,
    /// `X-Matrx-Device-Id` — `public.app_instances.id` (C13), when this device is registered.
    pub device_id: Option<String>,
    /// The mapping's cloud prefix: `""` for an organization root, `"Folder/Sub"` for a folder
    /// mapping. Mapping-relative keys are joined onto it.
    pub cloud_prefix: String,
}

/// Bytes moved, as the server meters them: request bodies up, response bodies down.
#[derive(Debug, Default)]
pub struct Meter {
    /// Bytes uploaded.
    pub up: AtomicU64,
    /// Bytes downloaded.
    pub down: AtomicU64,
}

/// The live file service, as the executor sees it.
#[derive(Debug, Clone)]
pub struct HttpRemote {
    config: RemoteConfig,
    http: reqwest::Client,
    token: Arc<dyn AccessTokenDebug>,
    permits: Arc<Semaphore>,
    /// What this client has moved, for the activity log and the progress surface.
    pub meter: Arc<Meter>,
}

/// [`AccessToken`] plus `Debug`, so the client stays printable without printing the token.
pub trait AccessTokenDebug: AccessToken + std::fmt::Debug {}
impl<T: AccessToken + std::fmt::Debug> AccessTokenDebug for T {}

impl HttpRemote {
    /// A client for one mapping. `knobs` supplies the request timeout and the transfer
    /// concurrency bound — both registry values, never constants here.
    pub fn new(
        config: RemoteConfig,
        token: Arc<dyn AccessTokenDebug>,
        knobs: &TransferKnobs,
    ) -> ExecResult<Self> {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(knobs.request_timeout_s.max(1)))
            .build()
            .map_err(|e| ExecError::Offline {
                detail: e.to_string(),
            })?;
        Ok(HttpRemote {
            config,
            http,
            token,
            permits: Arc::new(Semaphore::new(knobs.transfer_concurrency.max(1) as usize)),
            meter: Arc::new(Meter::default()),
        })
    }

    /// Share one transfer bound across several mappings' clients (`sync.transfer_concurrency` is
    /// a DEVICE knob, so every mapping on the device draws from one pool).
    pub fn with_shared_permits(mut self, permits: Arc<Semaphore>) -> Self {
        self.permits = permits;
        self
    }

    /// The cloud path for a mapping-relative key.
    pub fn cloud_path(&self, path_nfc: &str) -> String {
        let prefix = self.config.cloud_prefix.trim_matches('/');
        let key = path_nfc.trim_matches('/');
        match (prefix.is_empty(), key.is_empty()) {
            (true, _) => key.to_string(),
            (false, true) => prefix.to_string(),
            (false, false) => format!("{prefix}/{key}"),
        }
    }

    fn files(&self, path: &str) -> String {
        format!("{}{path}", self.config.files_base_url.trim_end_matches('/'))
    }

    async fn authed(&self, rb: reqwest::RequestBuilder) -> ExecResult<reqwest::RequestBuilder> {
        let token = self.token.access_token().await?;
        let mut rb = rb
            .header("Authorization", format!("Bearer {token}"))
            .header("X-Organization-Id", &self.config.organization_id);
        if let Some(device) = &self.config.device_id {
            rb = rb.header("X-Matrx-Device-Id", device);
        }
        Ok(rb)
    }

    async fn send(&self, rb: reqwest::RequestBuilder, path_nfc: &str) -> ExecResult<(u16, String)> {
        let _permit = self.permits.acquire().await.map_err(|e| ExecError::Io {
            path: path_nfc.to_string(),
            detail: format!("the transfer pool closed: {e}"),
        })?;
        let response = rb.send().await.map_err(|e| ExecError::Offline {
            detail: e.to_string(),
        })?;
        let status = response.status().as_u16();
        let body = response.text().await.map_err(|e| ExecError::Offline {
            detail: e.to_string(),
        })?;
        self.meter
            .down
            .fetch_add(body.len() as u64, Ordering::Relaxed);
        Ok((status, body))
    }

    /// `GET /files/{id}` — the authoritative row, used to confirm what every write produced.
    pub async fn record(&self, file_id: &str, path_nfc: &str) -> ExecResult<Option<FileRow>> {
        let rb = self.authed(self.http.get(self.files(&format!("/files/{file_id}")))).await?;
        let (status, body) = self.send(rb, path_nfc).await?;
        if status == 404 {
            return Ok(None);
        }
        if !(200..300).contains(&status) {
            return Err(classify(status, &body, path_nfc));
        }
        serde_json::from_str::<FileRow>(&body)
            .map(Some)
            .map_err(|e| ExecError::Server {
                code: status,
                detail: format!("unreadable file record: {e}"),
            })
    }

    async fn confirm_row(&self, file_id: &str, path_nfc: &str) -> ExecResult<RemoteObject> {
        let Some(row) = self.record(file_id, path_nfc).await? else {
            return Err(ExecError::RemoteGone {
                path: path_nfc.to_string(),
            });
        };
        Ok(RemoteObject {
            file_id: row.id,
            folder_id: row.parent_folder_id,
            version: row.current_version.unwrap_or(1),
            checksum: row.checksum,
            size: row.size_bytes,
            is_dir: false,
        })
    }

    async fn patch(
        &self,
        file_id: &str,
        body: serde_json::Value,
        if_match: Option<String>,
        idempotency_key: &str,
        path_nfc: &str,
    ) -> ExecResult<()> {
        let mut rb = self
            .http
            .patch(self.files(&format!("/files/{file_id}")))
            .header("X-Idempotency-Key", idempotency_key)
            .json(&body);
        if let Some(m) = if_match {
            rb = rb.header("If-Match", m);
        }
        let rb = self.authed(rb).await?;
        let (status, text) = self.send(rb, path_nfc).await?;
        if !(200..300).contains(&status) {
            return Err(classify(status, &text, path_nfc));
        }
        // SPEC-SERVER §4.3 forbids 200-with-errors, but the combined-op envelope still carries an
        // `errors` array; a non-empty one is a failure, never a success to believe.
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(errors) = v.get("errors").and_then(|e| e.as_array()) {
                if !errors.is_empty() {
                    return Err(ExecError::Server {
                        code: status,
                        detail: format!("the server reported errors: {}", truncate(&text)),
                    });
                }
            }
        }
        Ok(())
    }
}

/// The subset of `GET /files/{id}` the executor confirms from.
#[derive(Debug, Clone, serde::Deserialize)]
pub struct FileRow {
    /// `files.files.id`.
    pub id: String,
    /// The full cloud path.
    pub file_path: String,
    /// Server SHA-256.
    #[serde(default)]
    pub checksum: Option<String>,
    /// `files.files.current_version` — the feed's `version`.
    #[serde(default)]
    pub current_version: Option<i64>,
    /// Size in bytes.
    #[serde(default)]
    pub size_bytes: Option<i64>,
    /// The containing folder.
    #[serde(default)]
    pub parent_folder_id: Option<String>,
    /// Set on a tombstone.
    #[serde(default)]
    pub deleted_at: Option<String>,
}

#[async_trait::async_trait]
impl RemoteIo for HttpRemote {
    async fn mkdir(&self, path_nfc: &str) -> ExecResult<RemoteObject> {
        let url = format!(
            "{}/folders",
            self.config.host_base_url.trim_end_matches('/')
        );
        let rb = self
            .http
            .post(url)
            .json(&serde_json::json!({ "folder_path": self.cloud_path(path_nfc) }));
        let rb = self.authed(rb).await?;
        let (status, body) = self.send(rb, path_nfc).await?;
        if !(200..300).contains(&status) {
            return Err(classify(status, &body, path_nfc));
        }
        let v: serde_json::Value = serde_json::from_str(&body).map_err(|e| ExecError::Server {
            code: status,
            detail: format!("unreadable folder record: {e}"),
        })?;
        let Some(id) = v.get("id").and_then(|x| x.as_str()) else {
            return Err(ExecError::Server {
                code: status,
                detail: format!("the folder record carries no id: {}", truncate(&body)),
            });
        };
        Ok(RemoteObject {
            file_id: id.to_string(),
            folder_id: v.get("parent_id").and_then(|x| x.as_str()).map(str::to_string),
            version: 1,
            checksum: None,
            size: None,
            is_dir: true,
        })
    }

    async fn upload(
        &self,
        request: &UploadRequest,
        progress: &dyn ProgressSink,
    ) -> ExecResult<RemoteObject> {
        let file = tokio::fs::File::open(&request.source)
            .await
            .map_err(|e| match e.kind() {
                std::io::ErrorKind::NotFound => ExecError::Vanished {
                    path: request.path_nfc.clone(),
                },
                std::io::ErrorKind::PermissionDenied => ExecError::PermissionDenied {
                    path: request.path_nfc.clone(),
                    detail: e.to_string(),
                },
                _ => ExecError::Io {
                    path: request.path_nfc.clone(),
                    detail: e.to_string(),
                },
            })?;
        let size = request.size.max(0) as u64;
        let leaf = request
            .path_nfc
            .rsplit('/')
            .next()
            .unwrap_or("file")
            .to_string();
        let part = reqwest::multipart::Part::stream_with_length(reqwest::Body::from(file), size)
            .file_name(leaf);
        let form = reqwest::multipart::Form::new()
            .part("file", part)
            .text("file_path", self.cloud_path(&request.path_nfc))
            // S8: the daemon ALWAYS sends one; the engine test fails if a sync write omits it.
            .text("expected_checksum", request.expected_checksum.clone());
        let mut rb = self
            .http
            .post(self.files("/files/upload"))
            .header("X-Idempotency-Key", &request.idempotency_key)
            .multipart(form);
        if let Some(at) = &request.client_modified_at {
            rb = rb.header("X-Matrx-Client-Modified-At", at);
        }
        let rb = self.authed(rb).await?;
        let (status, body) = self.send(rb, &request.path_nfc).await?;
        if !(200..300).contains(&status) {
            return Err(classify(status, &body, &request.path_nfc));
        }
        self.meter.up.fetch_add(size, Ordering::Relaxed);
        progress.bytes(&request.path_nfc, size, Some(size));
        let v: serde_json::Value = serde_json::from_str(&body).map_err(|e| ExecError::Server {
            code: status,
            detail: format!("unreadable upload response: {e}"),
        })?;
        let Some(file_id) = v.get("file_id").and_then(|x| x.as_str()) else {
            return Err(ExecError::Server {
                code: status,
                detail: format!("the upload response carries no file_id: {}", truncate(&body)),
            });
        };
        // The upload answer has no version, and I1 needs the one the server holds. Read it back.
        let object = self.confirm_row(file_id, &request.path_nfc).await?;
        // I2: the checksum the server computed must be the hash of the bytes this daemon sent.
        if object.checksum.as_deref() != Some(request.content_hash.as_str()) {
            return Err(ExecError::ChecksumMismatch {
                path: request.path_nfc.clone(),
                expected: request.content_hash.clone(),
                found: object.checksum.clone().unwrap_or_default(),
            });
        }
        Ok(object)
    }

    async fn download(
        &self,
        remote_file_id: &str,
        path_nfc: &str,
        into: &Path,
        progress: &dyn ProgressSink,
    ) -> ExecResult<u64> {
        use tokio::io::AsyncWriteExt;
        let rb = self.authed(
            self.http
                .get(self.files(&format!("/files/{remote_file_id}/download"))),
        )
        .await?;
        let _permit = self.permits.acquire().await.map_err(|e| ExecError::Io {
            path: path_nfc.to_string(),
            detail: format!("the transfer pool closed: {e}"),
        })?;
        let mut response = rb.send().await.map_err(|e| ExecError::Offline {
            detail: e.to_string(),
        })?;
        let status = response.status().as_u16();
        if !(200..300).contains(&status) {
            let body = response.text().await.unwrap_or_default();
            return Err(classify(status, &body, path_nfc));
        }
        let total = response.content_length();
        let mut out = tokio::fs::File::create(into).await.map_err(|e| io_error(path_nfc, &e))?;
        let mut moved: u64 = 0;
        loop {
            let chunk = response.chunk().await.map_err(|e| ExecError::Offline {
                detail: e.to_string(),
            })?;
            let Some(chunk) = chunk else { break };
            out.write_all(&chunk).await.map_err(|e| io_error(path_nfc, &e))?;
            moved += chunk.len() as u64;
            progress.bytes(path_nfc, moved, total);
        }
        out.flush().await.map_err(|e| io_error(path_nfc, &e))?;
        self.meter.down.fetch_add(moved, Ordering::Relaxed);
        Ok(moved)
    }

    async fn rename(&self, request: &RenameRequest) -> ExecResult<RemoteObject> {
        let (from_parent, from_leaf) = crate::naming::split_parent(&request.from);
        let (to_parent, to_leaf) = crate::naming::split_parent(&request.to);
        // G5: the op's own precondition. A version names what was read; a checksum names the
        // bytes when no version is known.
        let if_match = match (&request.expected_version, &request.expected_checksum) {
            (Some(v), _) => Some(format!("W/\"v{v}\"")),
            (None, Some(c)) => Some(format!("\"{c}\"")),
            (None, None) => None,
        };
        if from_parent != to_parent {
            self.patch(
                &request.remote_file_id,
                serde_json::json!({ "folder": self.cloud_path(to_parent) }),
                if_match.clone(),
                &format!("{}:move", request.idempotency_key),
                &request.from,
            )
            .await?;
        }
        if from_leaf != to_leaf {
            self.patch(
                &request.remote_file_id,
                serde_json::json!({ "name": to_leaf }),
                if_match,
                &format!("{}:name", request.idempotency_key),
                &request.from,
            )
            .await?;
        }
        let Some(row) = self.record(&request.remote_file_id, &request.to).await? else {
            return Err(ExecError::RemoteGone {
                path: request.to.clone(),
            });
        };
        // SPEC-SERVER §4.3 forbade 200-with-errors because a rename that "succeeded" while the
        // row stayed put is how 22 rows drifted. Believe the row, not the status.
        if row.file_path != self.cloud_path(&request.to) {
            return Err(ExecError::Server {
                code: 200,
                detail: format!(
                    "the server accepted the move but the file sits at {} rather than {}",
                    row.file_path,
                    self.cloud_path(&request.to)
                ),
            });
        }
        let object = RemoteObject {
            file_id: row.id,
            folder_id: row.parent_folder_id,
            version: row.current_version.unwrap_or(1),
            checksum: row.checksum,
            size: row.size_bytes,
            is_dir: false,
        };
        Ok(object)
    }

    async fn tombstone(&self, request: &DeleteRequest) -> ExecResult<()> {
        // SPEC GAP: `DELETE /files/{id}` accepts no precondition. The version is checked here by
        // reading first; the race between the read and the delete is narrowed, not closed.
        let Some(row) = self.record(&request.remote_file_id, &request.path_nfc).await? else {
            return Err(ExecError::RemoteGone {
                path: request.path_nfc.clone(),
            });
        };
        if let Some(expected) = request.expected_version {
            if row.current_version != Some(expected) {
                return Err(ExecError::PreconditionFailed(Box::new(Precondition {
                    file_id: Some(row.id),
                    current_checksum: row.checksum,
                    current_version: row.current_version,
                    current_size_bytes: row.size_bytes,
                    ..Default::default()
                })));
            }
        }
        let rb = self
            .http
            .delete(self.files(&format!("/files/{}", request.remote_file_id)))
            .header("X-Idempotency-Key", &request.idempotency_key);
        let rb = self.authed(rb).await?;
        let (status, body) = self.send(rb, &request.path_nfc).await?;
        if status == 404 {
            return Err(ExecError::RemoteGone {
                path: request.path_nfc.clone(),
            });
        }
        if !(200..300).contains(&status) {
            return Err(classify(status, &body, &request.path_nfc));
        }
        Ok(())
    }
}

fn io_error(path: &str, e: &std::io::Error) -> ExecError {
    if e.kind() == std::io::ErrorKind::StorageFull || e.raw_os_error() == Some(28) {
        return ExecError::DiskFull {
            detail: e.to_string(),
        };
    }
    ExecError::Io {
        path: path.to_string(),
        detail: e.to_string(),
    }
}

fn truncate(s: &str) -> String {
    s.chars().take(200).collect()
}

/// Turn a non-2xx from the live service into a named state.
///
/// Public so the classification — the part of this file most likely to be wrong in a way that
/// matters — is tested without a network.
pub fn classify(status: u16, body: &str, path_nfc: &str) -> ExecError {
    let parsed: serde_json::Value = serde_json::from_str(body).unwrap_or(serde_json::Value::Null);
    // FastAPI wraps a dict detail as `{"detail": {...}}`; the organization gate answers flat.
    let inner = parsed.get("detail").filter(|d| d.is_object()).unwrap_or(&parsed);
    let code = inner
        .get("code")
        .or_else(|| inner.get("error"))
        .and_then(|c| c.as_str())
        .unwrap_or("");
    if status == 412 || code == "precondition_failed" {
        let text = serde_json::to_string(inner).unwrap_or_default();
        return ExecError::precondition_from_json(&text);
    }
    if code == "organization_unverifiable" {
        // The one organization code worth retrying: membership could not be checked right now.
        return ExecError::Server {
            code: 503,
            detail: truncate(body),
        };
    }
    if code.starts_with("organization_") {
        return ExecError::OrganizationRefused {
            detail: truncate(body),
        };
    }
    match status {
        401 => ExecError::SignInNeeded {
            detail: truncate(body),
        },
        403 => ExecError::PermissionDenied {
            path: path_nfc.to_string(),
            detail: truncate(body),
        },
        404 => ExecError::RemoteGone {
            path: path_nfc.to_string(),
        },
        409 if code == "idempotency_key_reuse" => ExecError::Refused(format!(
            "the server saw one idempotency key used for two different requests — a client \
             defect, never retried: {}",
            truncate(body)
        )),
        409 => ExecError::PathConflict {
            path: path_nfc.to_string(),
            detail: truncate(body),
        },
        413 | 507 => ExecError::OverQuota {
            detail: truncate(body),
        },
        _ if code.contains("quota") => ExecError::OverQuota {
            detail: truncate(body),
        },
        _ => ExecError::Server {
            code: status,
            detail: truncate(body),
        },
    }
}
