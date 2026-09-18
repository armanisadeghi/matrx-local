//! The helper's HTTP half: pairing, and the two device routes the tray drives.
//!
//! Four calls, all against `<server>/egress/*`:
//!
//! | Call | Route | Credential |
//! |---|---|---|
//! | [`Api::start_pairing`] | `POST /egress/pairings` | none (the route is anonymous) |
//! | [`Api::poll_pairing`] | `GET /egress/pairings/{id}` | `Bearer <pairing_secret>` |
//! | [`Api::set_enabled`] | `PATCH /egress/devices/{id}` | `Bearer <device_token>` |
//! | [`Api::remove_device`] | `DELETE /egress/devices/{id}` | `Bearer <device_token>` |
//!
//! **A gap the contract leaves, named rather than hidden:** the contract marks
//! `PATCH`/`DELETE /egress/devices/{id}` "user JWT", and the helper holds a DEVICE token, not a
//! user's session. It presents the device token for its own row, which is the only credential a
//! headless helper can have. If the gateway does not accept it, `pause` and `sign-out` still take
//! effect on THIS computer immediately (the relay stops and the keychain item goes) and the status
//! carries the refusal with the remedy "turn it off in AI Matrx on the web" — the helper never
//! reports a change it did not make.

use crate::config::Server;
use crate::identity::Registration;
use serde::{Deserialize, Serialize};
use std::fmt;
use std::time::Duration;

/// How long any one call may take before it is a failure with a sentence.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(20);

/// What `POST /egress/pairings` answers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PairingStart {
    /// The pairing row's id.
    pub pairing_id: String,
    /// The secret that authorises polling for this pairing. Never printed.
    pub pairing_secret: String,
    /// `ABCD-1234` — what the person types on the web page.
    pub user_code: String,
    /// The page to open in the browser.
    pub connect_url: String,
    /// RFC3339.
    pub expires_at: String,
    /// How often to poll, in seconds.
    #[serde(default = "default_poll_seconds")]
    pub poll_seconds: u64,
}

fn default_poll_seconds() -> u64 {
    3
}

/// What `GET /egress/pairings/{id}` answers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PairingPoll {
    /// `pending` / `approved` / `denied` / `expired`.
    pub status: String,
    /// Present exactly once, when the status first reads `approved`.
    #[serde(default)]
    pub device_id: Option<String>,
    /// Present with `device_id`.
    #[serde(default)]
    pub display_name: Option<String>,
    /// Present with `device_id`. The one time this value ever crosses the network to us.
    #[serde(default)]
    pub device_token: Option<String>,
}

/// An HTTP failure, in words a person can act on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiError {
    /// What was being attempted.
    pub what: String,
    /// The HTTP status, when there was a response at all.
    pub status: Option<u16>,
    /// The server's own message, or the transport error.
    pub detail: String,
    /// What the user should do.
    pub remedy: String,
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.status {
            Some(status) => write!(f, "{} — the server answered {status}: {}", self.what, self.detail),
            None => write!(f, "{} — {}", self.what, self.detail),
        }
    }
}

impl std::error::Error for ApiError {}

/// The HTTP client. One per process: it holds a connection pool.
#[derive(Debug, Clone)]
pub struct Api {
    client: reqwest::Client,
    server: Server,
}

impl Api {
    /// Build a client for one server.
    pub fn new(server: Server) -> Result<Self, ApiError> {
        let client = reqwest::Client::builder()
            .timeout(REQUEST_TIMEOUT)
            .user_agent(format!("matrx-egress/{}", crate::VERSION))
            .build()
            .map_err(|e| ApiError {
                what: "could not start the connection to AI Matrx".into(),
                status: None,
                detail: e.to_string(),
                remedy: "Restart the AI Matrx Home Connection.".into(),
            })?;
        Ok(Api { client, server })
    }

    /// Which server this client talks to.
    pub fn server(&self) -> &Server {
        &self.server
    }

    /// Ask for a pairing code for this computer.
    pub async fn start_pairing(
        &self,
        registration: &Registration,
    ) -> Result<PairingStart, ApiError> {
        let response = self
            .client
            .post(self.server.egress_url("pairings"))
            .json(&serde_json::json!({ "registration": registration }))
            .send()
            .await
            .map_err(|e| self.transport_error("could not ask AI Matrx to connect this computer", e))?;
        self.read_json(response, "could not ask AI Matrx to connect this computer")
            .await
    }

    /// Ask whether the person approved it yet.
    pub async fn poll_pairing(
        &self,
        pairing_id: &str,
        pairing_secret: &str,
    ) -> Result<PairingPoll, ApiError> {
        let response = self
            .client
            .get(self.server.egress_url(&format!("pairings/{pairing_id}")))
            .bearer_auth(pairing_secret)
            .send()
            .await
            .map_err(|e| self.transport_error("could not check whether this computer was approved", e))?;
        self.read_json(response, "could not check whether this computer was approved")
            .await
    }

    /// Turn this computer's home connection on or off for the whole account.
    pub async fn set_enabled(
        &self,
        device_id: &str,
        device_token: &str,
        enabled: bool,
    ) -> Result<(), ApiError> {
        let what = if enabled {
            "could not turn this computer's home connection back on"
        } else {
            "could not pause this computer's home connection"
        };
        let response = self
            .client
            .patch(self.server.egress_url(&format!("devices/{device_id}")))
            .bearer_auth(device_token)
            .json(&serde_json::json!({ "enabled": enabled }))
            .send()
            .await
            .map_err(|e| self.transport_error(what, e))?;
        self.read_empty(response, what).await
    }

    /// Remove this computer from the account.
    pub async fn remove_device(
        &self,
        device_id: &str,
        device_token: &str,
    ) -> Result<(), ApiError> {
        let what = "could not remove this computer from your AI Matrx account";
        let response = self
            .client
            .delete(self.server.egress_url(&format!("devices/{device_id}")))
            .bearer_auth(device_token)
            .send()
            .await
            .map_err(|e| self.transport_error(what, e))?;
        self.read_empty(response, what).await
    }

    fn transport_error(&self, what: &str, e: reqwest::Error) -> ApiError {
        ApiError {
            what: what.to_string(),
            status: None,
            detail: e.to_string(),
            remedy: format!(
                "Check this computer's internet connection and try again. It was trying to reach \
                 {}.",
                self.server
            ),
        }
    }

    async fn read_json<T: serde::de::DeserializeOwned>(
        &self,
        response: reqwest::Response,
        what: &str,
    ) -> Result<T, ApiError> {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        if !status.is_success() {
            return Err(self.status_error(what, status.as_u16(), &body));
        }
        serde_json::from_str(&body).map_err(|e| ApiError {
            what: what.to_string(),
            status: Some(status.as_u16()),
            detail: format!("the server's answer was not what this version expects: {e}"),
            remedy: "Update the AI Matrx Home Connection and try again.".into(),
        })
    }

    async fn read_empty(&self, response: reqwest::Response, what: &str) -> Result<(), ApiError> {
        let status = response.status();
        if status.is_success() {
            return Ok(());
        }
        let body = response.text().await.unwrap_or_default();
        Err(self.status_error(what, status.as_u16(), &body))
    }

    fn status_error(&self, what: &str, status: u16, body: &str) -> ApiError {
        // A server that says something useful gets quoted; one that says nothing does not get a
        // fabricated explanation.
        let detail = extract_message(body).unwrap_or_else(|| {
            if body.trim().is_empty() {
                "it did not say why".to_string()
            } else {
                body.chars().take(300).collect()
            }
        });
        let remedy = match status {
            401 | 403 => "This computer is no longer connected to your account. Open AI Matrx and \
                          connect it again."
                .to_string(),
            404 => "This computer is not on your account any more. Open AI Matrx and connect it \
                    again."
                .to_string(),
            408 | 429 => "AI Matrx is busy. It will try again on its own in a minute.".to_string(),
            500..=599 => "AI Matrx had a problem at its end. It will try again on its own in a \
                          minute."
                .to_string(),
            _ => "Open AI Matrx on the web and check this computer's home connection.".to_string(),
        };
        ApiError {
            what: what.to_string(),
            status: Some(status),
            detail,
            remedy,
        }
    }
}

/// Pull a human sentence out of an error body, whichever of the two shapes the server used.
fn extract_message(body: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_str(body).ok()?;
    for pointer in ["/error/message", "/message", "/detail", "/error"] {
        if let Some(text) = value.pointer(pointer).and_then(|v| v.as_str()) {
            if !text.is_empty() {
                return Some(text.to_string());
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio::sync::Mutex;

    /// A one-file HTTP/1.1 server that answers with canned responses and records what it was
    /// asked. It is a MOCK: it proves this client's request shape and its reading of answers, and
    /// is never cited as evidence that the real gateway behaves this way.
    struct FakeServer {
        base: String,
        seen: Arc<Mutex<Vec<String>>>,
    }

    impl FakeServer {
        /// `responses` are `(status line, body)`. The Content-Length is computed here, because a
        /// hand-written one is a fixture bug waiting to look like a client bug.
        async fn start(responses: Vec<(&'static str, &'static str)>) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
            let port = listener.local_addr().expect("addr").port();
            let seen = Arc::new(Mutex::new(Vec::new()));
            let recorder = Arc::clone(&seen);
            tokio::spawn(async move {
                for (status_line, body) in responses {
                    let Ok((mut stream, _)) = listener.accept().await else {
                        return;
                    };
                    let mut buffer = vec![0u8; 8192];
                    let n = stream.read(&mut buffer).await.unwrap_or(0);
                    recorder
                        .lock()
                        .await
                        .push(String::from_utf8_lossy(&buffer[..n]).into_owned());
                    let response = format!(
                        "HTTP/1.1 {status_line}\r\nContent-Type: application/json\r\n\
                         Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
                        body.len()
                    );
                    let _ = stream.write_all(response.as_bytes()).await;
                    let _ = stream.flush().await;
                    let _ = stream.shutdown().await;
                }
            });
            FakeServer {
                base: format!("http://127.0.0.1:{port}"),
                seen,
            }
        }

        fn api(&self) -> Api {
            Api::new(Server::parse(&self.base).expect("server")).expect("client")
        }

        async fn request(&self, index: usize) -> String {
            self.seen.lock().await[index].clone()
        }
    }

    const PAIRING_STARTED: (&str, &str) = (
        "200 OK",
        r#"{"pairing_id":"p-1","pairing_secret":"s-1","user_code":"ABCD-2345","connect_url":"https://aimatrx.com/connect-computer?code=ABCD-2345","expires_at":"2026-09-18T00:15:00Z","poll_seconds":3}"#,
    );

    #[tokio::test]
    async fn starting_a_pairing_posts_the_registration_and_reads_the_code() {
        let server = FakeServer::start(vec![PAIRING_STARTED]).await;
        let api = server.api();
        let registration = Registration::for_this_computer(Some("The kitchen Mac"));
        let started = api.start_pairing(&registration).await.expect("pairing");

        assert_eq!(started.user_code, "ABCD-2345");
        assert_eq!(started.poll_seconds, 3);
        assert!(started.connect_url.contains("connect-computer"));

        let request = server.request(0).await;
        assert!(request.starts_with("POST /egress/pairings "), "{request}");
        assert!(request.contains("The kitchen Mac"), "{request}");
        assert!(request.contains("\"client_kind\":\"helper\""), "{request}");
        // The anonymous route is called anonymously.
        assert!(!request.to_lowercase().contains("authorization:"), "{request}");
    }

    #[tokio::test]
    async fn polling_carries_the_pairing_secret_and_reads_the_token_once() {
        let approved = (
            "200 OK",
            r#"{"status":"approved","device_id":"d-1","display_name":"The kitchen Mac","device_token":"mxe_d-1_secret"}"#,
        );
        let server = FakeServer::start(vec![approved]).await;
        let poll = server
            .api()
            .poll_pairing("p-1", "s-1")
            .await
            .expect("poll");
        assert_eq!(poll.status, "approved");
        assert_eq!(poll.device_token.as_deref(), Some("mxe_d-1_secret"));

        let request = server.request(0).await;
        assert!(request.starts_with("GET /egress/pairings/p-1 "), "{request}");
        assert!(request.contains("authorization: Bearer s-1"), "{request}");
    }

    #[tokio::test]
    async fn a_pending_poll_has_no_device_and_that_is_not_an_error() {
        let pending = ("200 OK", r#"{"status":"pending"}"#);
        let server = FakeServer::start(vec![pending]).await;
        let poll = server.api().poll_pairing("p-1", "s-1").await.expect("poll");
        assert_eq!(poll.status, "pending");
        assert_eq!(poll.device_token, None);
        assert_eq!(poll.device_id, None);
    }

    #[tokio::test]
    async fn pausing_patches_the_device_row_with_the_device_token() {
        let ok = ("200 OK", "");
        let server = FakeServer::start(vec![ok, ok]).await;
        let api = server.api();
        api.set_enabled("d-1", "mxe_d-1_secret", false)
            .await
            .expect("pause");
        let request = server.request(0).await;
        assert!(request.starts_with("PATCH /egress/devices/d-1 "), "{request}");
        assert!(request.contains("authorization: Bearer mxe_d-1_secret"), "{request}");
        assert!(request.contains("\"enabled\":false"), "{request}");

        api.set_enabled("d-1", "mxe_d-1_secret", true)
            .await
            .expect("resume");
        assert!(server.request(1).await.contains("\"enabled\":true"));
    }

    #[tokio::test]
    async fn removing_this_computer_deletes_the_device_row() {
        let server = FakeServer::start(vec![("204 No Content", "")]).await;
        server
            .api()
            .remove_device("d-1", "mxe_d-1_secret")
            .await
            .expect("remove");
        let request = server.request(0).await;
        assert!(request.starts_with("DELETE /egress/devices/d-1 "), "{request}");
        assert!(request.contains("authorization: Bearer mxe_d-1_secret"), "{request}");
    }

    #[tokio::test]
    async fn a_refusal_keeps_the_servers_own_sentence_and_adds_a_remedy() {
        let refused = (
            "401 Unauthorized",
            r#"{"error":{"message":"this computer is not on the account"}}"#,
        );
        let server = FakeServer::start(vec![refused]).await;
        let error = server
            .api()
            .set_enabled("d-1", "bad", false)
            .await
            .expect_err("refused");
        assert_eq!(error.status, Some(401));
        assert_eq!(error.detail, "this computer is not on the account");
        assert!(error.remedy.contains("connect it again"), "{}", error.remedy);
        assert!(error.to_string().contains("401"));
    }

    #[tokio::test]
    async fn a_silent_refusal_is_not_given_a_made_up_explanation() {
        let server = FakeServer::start(vec![("500 Internal Server Error", "")]).await;
        let error = server
            .api()
            .remove_device("d-1", "t")
            .await
            .expect_err("refused");
        assert_eq!(error.detail, "it did not say why");
        assert!(error.remedy.contains("try again"), "{}", error.remedy);
    }

    #[tokio::test]
    async fn a_server_that_cannot_be_reached_says_which_address_it_tried() {
        // Port 1 on loopback: nothing listens there, and the connection is refused at once.
        let api = Api::new(Server::parse("http://127.0.0.1:1").expect("server")).expect("client");
        let error = api
            .poll_pairing("p-1", "s-1")
            .await
            .expect_err("unreachable");
        assert_eq!(error.status, None);
        assert!(error.remedy.contains("127.0.0.1:1"), "{}", error.remedy);
    }

    #[test]
    fn a_message_is_found_in_either_error_shape() {
        assert_eq!(
            extract_message(r#"{"error":{"message":"one"}}"#).as_deref(),
            Some("one")
        );
        assert_eq!(extract_message(r#"{"message":"two"}"#).as_deref(), Some("two"));
        assert_eq!(extract_message(r#"{"detail":"three"}"#).as_deref(), Some("three"));
        assert_eq!(extract_message(r#"{"error":"four"}"#).as_deref(), Some("four"));
        assert_eq!(extract_message("not json"), None);
        assert_eq!(extract_message(r#"{"error":{"message":""}}"#), None);
    }
}
