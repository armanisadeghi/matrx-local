//! The authorization server seam (§3.1, §5).
//!
//! The token endpoint supports exactly two grants — `authorization_code` and `refresh_token` —
//! and no client secret (public client). The response **may** carry a new refresh token, and the
//! documentation is explicit: *always update your stored refresh token when a new one is
//! provided.* S8's write-ahead rule is what honours it.
//!
//! Everything here is behind [`OAuthProvider`] so `FakeAuthServer` (§13) can drive every branch of
//! §5 without a network. **`FakeAuthServer` is a mock and is never cited as product evidence.**

use super::error::{CustodyError, Result};
use async_trait::async_trait;
use serde::Deserialize;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Mutex;
use std::time::Duration;

/// What a successful grant returns.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TokenResponse {
    /// The short-lived Supabase JWT. Never written to disk (§4).
    pub access_token: String,
    /// The refresh token to store, when the server rotated it. `None` means "keep the one you
    /// have" — the OAuth-server path is documented as *may* rotate.
    pub refresh_token: Option<String>,
    /// Seconds of life the server declared. Scheduling uses this relative value, never a wall
    /// clock (S9, S10).
    pub expires_in: i64,
}

/// The claims custody reads out of the access token. Only these two, and only for display and for
/// the keychain account name — never for scheduling (S9).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TokenIdentity {
    /// `sub` — the Supabase user id (uuid).
    pub user_id: String,
    /// `email`, when the token carries one.
    pub email: String,
}

/// Read `sub` and `email` out of a JWT payload without verifying the signature.
///
/// The daemon is not the verifier — the server is, on every call the token is used for. This
/// decode exists only to name the keychain account and to say "Sign back in as …".
pub fn identity_from_jwt(access_token: &str) -> Result<TokenIdentity> {
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use base64::Engine as _;

    let payload = access_token.split('.').nth(1).ok_or_else(|| {
        CustodyError::AmbiguousResponse {
            status: 200,
            detail: "the access token is not a JWT (no payload segment)".into(),
        }
    })?;
    let bytes = URL_SAFE_NO_PAD
        .decode(payload)
        .map_err(|e| CustodyError::AmbiguousResponse {
            status: 200,
            detail: format!("the access token's payload is not base64url: {e}"),
        })?;
    #[derive(Deserialize)]
    struct Claims {
        sub: Option<String>,
        email: Option<String>,
    }
    let claims: Claims =
        serde_json::from_slice(&bytes).map_err(|e| CustodyError::AmbiguousResponse {
            status: 200,
            detail: format!("the access token's payload is not JSON: {e}"),
        })?;
    Ok(TokenIdentity {
        user_id: claims.sub.ok_or_else(|| CustodyError::AmbiguousResponse {
            status: 200,
            detail: "the access token carries no `sub` claim".into(),
        })?,
        email: claims.email.unwrap_or_default(),
    })
}

/// The two grants, and nothing else.
#[async_trait]
pub trait OAuthProvider: Send + Sync + std::fmt::Debug {
    /// `grant_type=authorization_code` — `code`, `code_verifier`, `client_id`, `redirect_uri`.
    async fn exchange_code(
        &self,
        code: &str,
        code_verifier: &str,
        redirect_uri: &str,
    ) -> Result<TokenResponse>;

    /// `grant_type=refresh_token` — `refresh_token`, `client_id`. No client secret.
    async fn refresh(&self, refresh_token: &str) -> Result<TokenResponse>;
}

/// Which of Supabase's two token endpoints renewed this device's session.
///
/// A Supabase session has an origin: one created by the OAuth 2.1 authorization-code grant is
/// renewed at `/auth/v1/oauth/token`, and one created any other way — a password sign-in, the
/// pre-cutover app's own session, the dev harness door — is renewed at
/// `/auth/v1/token?grant_type=refresh_token`. Presenting a non-OAuth refresh token to the OAuth
/// endpoint is answered `400 Client authentication not allowed for non-OAuth session`, which is
/// how every adopted session died at its first refresh (FS-C5b finding C5b-1).
///
/// The daemon does not know a session's origin after a restart — the keychain holds a token, not
/// a provenance — so it probes: the first refresh tries a lane, and the other lane is tried when
/// the first refuses. The lane that worked is remembered for this process, so the probe costs at
/// most one extra request per daemon start.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefreshLane {
    /// `/auth/v1/oauth/token`, `client_id` included — an OAuth-created session.
    OAuth,
    /// `/auth/v1/token?grant_type=refresh_token`, `apikey` header, no `client_id`.
    Classic,
}

impl RefreshLane {
    const fn other(self) -> Self {
        match self {
            RefreshLane::OAuth => RefreshLane::Classic,
            RefreshLane::Classic => RefreshLane::OAuth,
        }
    }

    const fn as_u8(self) -> u8 {
        match self {
            RefreshLane::OAuth => 1,
            RefreshLane::Classic => 2,
        }
    }

    const fn from_u8(v: u8) -> Option<Self> {
        match v {
            1 => Some(RefreshLane::OAuth),
            2 => Some(RefreshLane::Classic),
            _ => None,
        }
    }
}

/// The real Supabase OAuth 2.1 server.
#[derive(Debug)]
pub struct SupabaseOAuth {
    token_url: String,
    classic_token_url: String,
    client_id: String,
    publishable_key: String,
    http: reqwest::Client,
    /// The lane that last renewed a token in this process; 0 = not yet known.
    lane: AtomicU8,
}

impl SupabaseOAuth {
    /// Build a provider against `supabase_url` for the registered public client `client_id`.
    ///
    /// `publishable_key` is the `apikey` header the non-OAuth token endpoint requires. It is not
    /// a secret (it is the anon key every client already ships).
    pub fn new(supabase_url: &str, client_id: &str, publishable_key: &str) -> Result<Self> {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
            .map_err(|e| CustodyError::Configuration {
                setting: "http client",
                detail: e.to_string(),
            })?;
        let base = supabase_url.trim_end_matches('/');
        Ok(SupabaseOAuth {
            token_url: format!("{base}/auth/v1/oauth/token"),
            classic_token_url: format!("{base}/auth/v1/token?grant_type=refresh_token"),
            client_id: client_id.to_string(),
            publishable_key: publishable_key.to_string(),
            http,
            lane: AtomicU8::new(0),
        })
    }

    /// The lane remembered for this process, if one has worked yet.
    pub fn remembered_lane(&self) -> Option<RefreshLane> {
        RefreshLane::from_u8(self.lane.load(Ordering::Relaxed))
    }

    fn remember(&self, lane: RefreshLane) {
        self.lane.store(lane.as_u8(), Ordering::Relaxed);
    }

    /// One refresh attempt down one lane.
    async fn refresh_on(&self, lane: RefreshLane, refresh_token: &str) -> Result<TokenResponse> {
        match lane {
            RefreshLane::OAuth => {
                self.post(vec![
                    ("grant_type", "refresh_token".into()),
                    ("client_id", self.client_id.clone()),
                    ("refresh_token", refresh_token.to_string()),
                ])
                .await
            }
            RefreshLane::Classic => {
                self.interpret(
                    self.http
                        .post(&self.classic_token_url)
                        .header("apikey", &self.publishable_key)
                        .header(
                            reqwest::header::AUTHORIZATION,
                            format!("Bearer {}", self.publishable_key),
                        )
                        .json(&serde_json::json!({ "refresh_token": refresh_token })),
                )
                .await
            }
        }
    }

    async fn post(&self, form: Vec<(&str, String)>) -> Result<TokenResponse> {
        self.interpret(self.http.post(&self.token_url).form(&form))
            .await
    }

    async fn interpret(&self, request: reqwest::RequestBuilder) -> Result<TokenResponse> {
        let response = request
            .send()
            .await
            .map_err(|e| CustodyError::Transport {
                endpoint: "the AI Matrx sign-in service",
                cause: e.to_string(),
            })?;

        let status = response.status();
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let body = response.text().await.map_err(|e| CustodyError::Transport {
            endpoint: "the AI Matrx sign-in service",
            cause: e.to_string(),
        })?;

        // A captive-portal 200 carrying HTML, or a proxy 407, is ambiguous — retryable, never a
        // revocation (§5). Content type is checked before the status so an HTML 200 cannot be
        // mistaken for a token.
        if !content_type.contains("json") {
            return Err(CustodyError::AmbiguousResponse {
                status: status.as_u16(),
                detail: format!(
                    "the response content type was {}, not JSON — this is usually a captive \
                     portal or a proxy, not a sign-in failure",
                    if content_type.is_empty() {
                        "absent"
                    } else {
                        &content_type
                    }
                ),
            });
        }

        if status.is_success() {
            #[derive(Deserialize)]
            struct Ok_ {
                access_token: String,
                refresh_token: Option<String>,
                expires_in: Option<i64>,
            }
            let ok: Ok_ =
                serde_json::from_str(&body).map_err(|e| CustodyError::AmbiguousResponse {
                    status: status.as_u16(),
                    detail: format!("the token response could not be read: {e}"),
                })?;
            if ok.access_token.is_empty() {
                return Err(CustodyError::AmbiguousResponse {
                    status: status.as_u16(),
                    detail: "the token response carried an empty access token".into(),
                });
            }
            return Ok(TokenResponse {
                access_token: ok.access_token,
                refresh_token: ok.refresh_token.filter(|t| !t.is_empty()),
                // Supabase's documented default is 3600 s; a response without the field is still
                // usable, and a conservative floor is safer than assuming an hour.
                expires_in: ok.expires_in.unwrap_or(3600),
            });
        }

        #[derive(Deserialize)]
        struct Err_ {
            error: Option<String>,
            error_description: Option<String>,
            // Supabase OAuth server errors use this pair rather than OAuth's
            // `error`/`error_description` for an exhausted rotating refresh token.
            error_code: Option<String>,
            msg: Option<String>,
        }
        let parsed: Err_ = serde_json::from_str(&body).unwrap_or(Err_ {
            error: None,
            error_description: None,
            error_code: None,
            msg: None,
        });
        let code = parsed
            .error
            .or(parsed.error_code)
            .unwrap_or_else(|| status.as_u16().to_string());

        // 400/401 known grant refusals are terminal: revoked, reused, or the user revoked the
        // grant from the web. Supabase reports rotating-token exhaustion as `error_code`, not
        // the standard OAuth `error`.
        let terminal = (status.as_u16() == 400 || status.as_u16() == 401)
            && matches!(
                code.as_str(),
                "invalid_grant"
                    | "invalid_request"
                    | "unauthorized_client"
                    | "refresh_token_not_found"
                    | "refresh_token_already_used"
                    | "session_expired"
            );
        if terminal {
            return Err(CustodyError::GrantRefused {
                error: code,
                description: parsed.error_description.or(parsed.msg),
            });
        }

        let detail = parsed
            .error_description
            .or(parsed.msg)
            .unwrap_or_else(|| format!("the sign-in service answered {code}"));

        // A server that ANSWERED is not an absent network (law 4). An unrecognised 400/401/403
        // from a reachable auth server is a refusal of this device's session, not "check your
        // internet" repeated forever — the caller only surfaces it after every lane has refused.
        // 408, 429 and 5xx stay ambiguous and retryable: they are "try again", not "you are out".
        if matches!(status.as_u16(), 400 | 401 | 403) {
            return Err(CustodyError::AuthServerRefused {
                status: status.as_u16(),
                detail,
            });
        }
        Err(CustodyError::AmbiguousResponse {
            status: status.as_u16(),
            detail,
        })
    }
}

#[async_trait]
impl OAuthProvider for SupabaseOAuth {
    async fn exchange_code(
        &self,
        code: &str,
        code_verifier: &str,
        redirect_uri: &str,
    ) -> Result<TokenResponse> {
        self.post(vec![
            ("grant_type", "authorization_code".into()),
            ("client_id", self.client_id.clone()),
            ("code", code.to_string()),
            ("code_verifier", code_verifier.to_string()),
            ("redirect_uri", redirect_uri.to_string()),
        ])
        .await
    }

    async fn refresh(&self, refresh_token: &str) -> Result<TokenResponse> {
        // Probe, then remember. A session's origin is not recorded anywhere that survives a
        // restart, so the daemon asks the server rather than assuming — and never lets one
        // endpoint's "that is not my kind of session" end a renewable session (C5b-1).
        let first = self.remembered_lane().unwrap_or(RefreshLane::OAuth);
        let first_error = match self.refresh_on(first, refresh_token).await {
            Ok(response) => {
                self.remember(first);
                return Ok(response);
            }
            // The network is down. Trying the other lane can only produce the same failure with a
            // different URL in it, and a retryable state is already the honest answer.
            Err(e @ CustodyError::Transport { .. }) => return Err(e),
            Err(e) => e,
        };

        let second = first.other();
        match self.refresh_on(second, refresh_token).await {
            Ok(response) => {
                self.remember(second);
                Ok(response)
            }
            Err(second_error) => Err(both_lanes_refused(first_error, second_error)),
        }
    }
}

/// Both token endpoints refused the same token: pick the answer that tells the user the most.
///
/// A named grant refusal is the most informative thing either lane can say; after that, a
/// reachable server's refusal beats a transport failure on the second URL.
fn both_lanes_refused(first: CustodyError, second: CustodyError) -> CustodyError {
    if matches!(second, CustodyError::GrantRefused { .. }) {
        return second;
    }
    if matches!(first, CustodyError::GrantRefused { .. }) {
        return first;
    }
    if matches!(second, CustodyError::Transport { .. }) {
        return first;
    }
    second
}

/// `FakeAuthServer` (§13): a scripted stand-in for `/auth/v1/oauth/{authorize,token}`.
///
/// **It is a mock. It proves the state machine only** and is never cited as product evidence
/// (SCOPE §6 attestation rule).
#[derive(Debug)]
pub struct FakeAuthServer {
    /// Scripted answers, consumed in order. When the queue empties the last answer repeats.
    script: Mutex<VecDeque<std::result::Result<TokenResponse, FakeFailure>>>,
    last: Mutex<Option<std::result::Result<TokenResponse, FakeFailure>>>,
    /// Every refresh token this fake was ever presented, in order — the reuse assertion.
    presented: Mutex<Vec<String>>,
}

/// A scripted failure shape.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FakeFailure {
    /// The terminal path.
    InvalidGrant,
    /// Supabase's rotating-token exhaustion response (`error_code`).
    RefreshTokenNotFound,
    /// A 5xx or a 429 — retryable.
    ServerError(u16),
    /// A captive-portal 200 carrying HTML.
    CaptivePortal,
    /// The network was unreachable.
    Offline,
    /// A reachable auth server refused this device's session with an unrecognised 4xx — GoTrue's
    /// `Client authentication not allowed for non-OAuth session` is the observed one (C5b-1).
    SessionRefused,
}

impl FakeFailure {
    fn into_error(self) -> CustodyError {
        match self {
            FakeFailure::InvalidGrant => CustodyError::GrantRefused {
                error: "invalid_grant".into(),
                description: Some("the refresh token has been revoked".into()),
            },
            FakeFailure::RefreshTokenNotFound => CustodyError::GrantRefused {
                error: "refresh_token_not_found".into(),
                description: Some("Invalid Refresh Token: Refresh Token Not Found".into()),
            },
            FakeFailure::ServerError(status) => CustodyError::AmbiguousResponse {
                status,
                detail: "the sign-in service is unavailable".into(),
            },
            FakeFailure::CaptivePortal => CustodyError::AmbiguousResponse {
                status: 200,
                detail: "the response content type was text/html, not JSON".into(),
            },
            FakeFailure::SessionRefused => CustodyError::AuthServerRefused {
                status: 400,
                detail: "Client authentication not allowed for non-OAuth session".into(),
            },
            FakeFailure::Offline => CustodyError::Transport {
                endpoint: "the AI Matrx sign-in service",
                cause: "no route to host".into(),
            },
        }
    }
}

impl FakeAuthServer {
    /// A fake with an empty script; every call fails until one is pushed.
    pub fn new() -> Self {
        FakeAuthServer {
            script: Mutex::new(VecDeque::new()),
            last: Mutex::new(None),
            presented: Mutex::new(Vec::new()),
        }
    }

    /// Queue one successful answer.
    pub fn push_ok(&self, access_token: &str, refresh_token: Option<&str>, expires_in: i64) {
        self.script
            .lock()
            .expect("fake auth script")
            .push_back(Ok(TokenResponse {
                access_token: access_token.to_string(),
                refresh_token: refresh_token.map(str::to_string),
                expires_in,
            }));
    }

    /// Queue one failure.
    pub fn push_err(&self, failure: FakeFailure) {
        self.script
            .lock()
            .expect("fake auth script")
            .push_back(Err(failure));
    }

    /// Every refresh token presented so far, in order. A value appearing twice is the reuse
    /// Supabase's session path treats as session termination (§5).
    pub fn presented_refresh_tokens(&self) -> Vec<String> {
        self.presented.lock().expect("fake auth script").clone()
    }

    fn next(&self) -> Result<TokenResponse> {
        let mut script = self.script.lock().expect("fake auth script");
        let answer = script.pop_front().or_else(|| {
            self.last
                .lock()
                .expect("fake auth script")
                .clone()
        });
        drop(script);
        match answer {
            Some(a) => {
                *self.last.lock().expect("fake auth script") = Some(a.clone());
                a.map_err(FakeFailure::into_error)
            }
            None => Err(CustodyError::AmbiguousResponse {
                status: 500,
                detail: "FakeAuthServer has no scripted answer left".into(),
            }),
        }
    }
}

impl Default for FakeAuthServer {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl OAuthProvider for FakeAuthServer {
    async fn exchange_code(
        &self,
        _code: &str,
        _code_verifier: &str,
        _redirect_uri: &str,
    ) -> Result<TokenResponse> {
        self.next()
    }

    async fn refresh(&self, refresh_token: &str) -> Result<TokenResponse> {
        self.presented
            .lock()
            .expect("fake auth script")
            .push(refresh_token.to_string());
        self.next()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use base64::Engine as _;

    fn jwt(payload: &str) -> String {
        format!("header.{}.sig", URL_SAFE_NO_PAD.encode(payload))
    }

    #[test]
    fn identity_reads_sub_and_email() {
        let t = jwt(r#"{"sub":"11111111-2222-3333-4444-555555555555","email":"a@b.c"}"#);
        let id = identity_from_jwt(&t).expect("identity");
        assert_eq!(id.user_id, "11111111-2222-3333-4444-555555555555");
        assert_eq!(id.email, "a@b.c");
    }

    #[test]
    fn a_token_without_a_sub_is_refused_rather_than_guessed() {
        let t = jwt(r#"{"email":"a@b.c"}"#);
        assert!(matches!(
            identity_from_jwt(&t),
            Err(CustodyError::AmbiguousResponse { .. })
        ));
    }

    #[test]
    fn a_token_that_is_not_a_jwt_is_refused() {
        assert!(identity_from_jwt("not-a-jwt").is_err());
    }

    #[tokio::test]
    async fn supabase_refresh_token_not_found_is_terminal() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind test server");
        let address = listener.local_addr().expect("test server address");
        tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.expect("accept request");
            let mut request = [0_u8; 4096];
            let _ = socket.read(&mut request).await.expect("read request");
            let body = r#"{"code":400,"error_code":"refresh_token_not_found","msg":"Invalid Refresh Token: Refresh Token Not Found"}"#;
            let response = format!(
                "HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len(),
            );
            socket
                .write_all(response.as_bytes())
                .await
                .expect("write response");
        });

        let provider = SupabaseOAuth::new(&format!("http://{address}"), "test-client", "anon-key")
            .expect("provider");
        let error = provider.refresh("exhausted-token").await.expect_err("terminal refusal");
        assert!(matches!(
            error,
            CustodyError::GrantRefused { ref error, .. } if error == "refresh_token_not_found"
        ));
    }
}
