//! The local control surface — how `matrx-egress status|pause|resume|sign-out` reaches the
//! running standalone instance.
//!
//! The same shape `matrx-syncd` uses: a loopback listener on an OS-chosen port, and a private
//! file naming the port and a token minted fresh at every start
//! (`<home>/egress/control.json`, mode 0600, deleted on a clean stop). A file that outlives the
//! process that minted it authorises nothing, so leaving it behind would only put a live-looking
//! credential on disk.
//!
//! Four routes, all requiring `Authorization: Bearer <token>`:
//!
//! | Route | Does |
//! |---|---|
//! | `GET /status` | the status document |
//! | `POST /pause` | switch this computer off, here and on the account |
//! | `POST /resume` | switch it back on |
//! | `POST /sign-out` | remove this computer from the account and forget its token |

use crate::paths::{self, Paths};
use crate::status::StatusHandle;
use crate::supervisor::Command;
use http_body_util::Full;
use hyper::body::Bytes;
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use rand::Rng;
use serde::{Deserialize, Serialize};
use std::io;
use std::net::{Ipv4Addr, SocketAddr};
use std::sync::Arc;
use std::time::Duration;
use tokio::net::TcpListener;
use tokio::sync::mpsc::UnboundedSender;

/// What `control.json` holds. No secret other than the control token itself, and no path to one.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ControlFile {
    /// The shape version. Only `1` exists.
    pub version: u8,
    /// `live` or `dev`.
    pub world: String,
    /// The running helper's process id.
    pub pid: u32,
    /// The loopback port it is answering on.
    pub port: u16,
    /// The token every request must carry.
    pub token: String,
    /// RFC3339.
    pub started_at: String,
}

impl ControlFile {
    /// Read the file, or `None` when no helper has published one.
    pub fn read(path: &std::path::Path) -> io::Result<Option<Self>> {
        match std::fs::read_to_string(path) {
            Ok(text) => serde_json::from_str(&text)
                .map(Some)
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e)),
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e),
        }
    }

    /// Where this helper is answering.
    pub fn base_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }
}

/// A 256-bit token, hex, minted fresh at every start.
pub fn mint_token() -> String {
    let bytes: [u8; 32] = rand::rng().random();
    hex::encode(bytes)
}

/// The bound control listener.
pub struct ControlServer {
    listener: TcpListener,
    /// The port the OS gave us.
    pub port: u16,
    /// The token clients must present.
    pub token: String,
}

impl ControlServer {
    /// Bind loopback on an OS-chosen port. Binds `127.0.0.1` only: this surface is never reachable
    /// from another machine.
    pub async fn bind() -> io::Result<Self> {
        let listener = TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, 0))).await?;
        let port = listener.local_addr()?.port();
        Ok(ControlServer {
            listener,
            port,
            token: mint_token(),
        })
    }

    /// Publish `control.json` so the CLI can find this instance.
    pub fn publish(&self, paths: &Paths) -> io::Result<()> {
        let file = ControlFile {
            version: 1,
            world: paths.world.as_str().to_string(),
            pid: std::process::id(),
            port: self.port,
            token: self.token.clone(),
            started_at: crate::status::now_rfc3339(),
        };
        let text = serde_json::to_string_pretty(&file)
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        paths::write_atomic(&paths.control, &text, true)
    }

    /// Serve until the task is dropped.
    pub fn serve(
        self,
        status: Arc<StatusHandle>,
        commands: UnboundedSender<Command>,
    ) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let token = Arc::new(self.token);
            loop {
                let Ok((stream, _)) = self.listener.accept().await else {
                    return;
                };
                let token = Arc::clone(&token);
                let status = Arc::clone(&status);
                let commands = commands.clone();
                tokio::spawn(async move {
                    let service = service_fn(move |request: Request<hyper::body::Incoming>| {
                        let token = Arc::clone(&token);
                        let status = Arc::clone(&status);
                        let commands = commands.clone();
                        async move { Ok::<_, std::convert::Infallible>(handle(request, &token, &status, &commands)) }
                    });
                    let _ = http1::Builder::new()
                        .serve_connection(TokioIo::new(stream), service)
                        .await;
                });
            }
        })
    }
}

fn json_response(status: StatusCode, body: String) -> Response<Full<Bytes>> {
    Response::builder()
        .status(status)
        .header("content-type", "application/json")
        .body(Full::new(Bytes::from(body)))
        .unwrap_or_else(|_| Response::new(Full::new(Bytes::from("{}"))))
}

fn handle(
    request: Request<hyper::body::Incoming>,
    token: &str,
    status: &Arc<StatusHandle>,
    commands: &UnboundedSender<Command>,
) -> Response<Full<Bytes>> {
    let presented = request
        .headers()
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .unwrap_or_default();
    if !constant_time_eq(presented.as_bytes(), token.as_bytes()) {
        return json_response(
            StatusCode::UNAUTHORIZED,
            serde_json::json!({
                "error": "unauthorized",
                "message": "that request did not carry this computer's control token",
                "remedy": "Run the command again from the same account that started the AI Matrx \
                           Home Connection."
            })
            .to_string(),
        );
    }

    let path = request.uri().path().to_string();
    match (request.method().as_str(), path.as_str()) {
        ("GET", "/status") => json_response(StatusCode::OK, status.snapshot().to_pretty()),
        ("POST", "/pause") => accept(commands, Command::Pause),
        ("POST", "/resume") => accept(commands, Command::Resume),
        ("POST", "/sign-out") => accept(commands, Command::SignOut),
        _ => json_response(
            StatusCode::NOT_FOUND,
            serde_json::json!({
                "error": "not_found",
                "message": format!("this version of the Home Connection does not answer {path}"),
                "remedy": "Update the AI Matrx Home Connection."
            })
            .to_string(),
        ),
    }
}

fn accept(commands: &UnboundedSender<Command>, command: Command) -> Response<Full<Bytes>> {
    match commands.send(command) {
        Ok(()) => json_response(
            StatusCode::ACCEPTED,
            serde_json::json!({ "accepted": true }).to_string(),
        ),
        Err(_) => json_response(
            StatusCode::SERVICE_UNAVAILABLE,
            serde_json::json!({
                "error": "stopping",
                "message": "the AI Matrx Home Connection is shutting down and did not act on that",
                "remedy": "Start it again and run the command once more."
            })
            .to_string(),
        ),
    }
}

/// Compare two secrets without leaking their common prefix through timing.
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut difference = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        difference |= x ^ y;
    }
    difference == 0
}

/// The client half: talk to the running instance named by `control.json`.
#[derive(Debug)]
pub struct ControlClient {
    file: ControlFile,
    client: reqwest::Client,
}

/// Why the CLI could not reach a running helper.
#[derive(Debug)]
pub enum ControlError {
    /// No `control.json`, so nothing is running in this world.
    NotRunning,
    /// It is there but did not answer.
    Unreachable(String),
    /// It answered with a refusal.
    Refused {
        /// The HTTP status.
        status: u16,
        /// The body, as it came.
        body: String,
    },
}

impl std::fmt::Display for ControlError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ControlError::NotRunning => write!(
                f,
                "the AI Matrx Home Connection is not running on this computer.\n\
                 Start it (open AI Matrx Home Connection from your Applications folder, or run \
                 `matrx-egress`) and try again."
            ),
            ControlError::Unreachable(cause) => write!(
                f,
                "the AI Matrx Home Connection did not answer: {cause}.\n\
                 It may be starting up or shutting down — try again in a few seconds."
            ),
            ControlError::Refused { status, body } => {
                write!(f, "the AI Matrx Home Connection answered {status}: {body}")
            }
        }
    }
}

impl std::error::Error for ControlError {}

impl ControlClient {
    /// Find the running helper for this world.
    pub fn connect(paths: &Paths) -> Result<Self, ControlError> {
        let file = ControlFile::read(&paths.control)
            .map_err(|e| ControlError::Unreachable(e.to_string()))?
            .ok_or(ControlError::NotRunning)?;
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|e| ControlError::Unreachable(e.to_string()))?;
        Ok(ControlClient { file, client })
    }

    /// `GET /status`.
    pub async fn status(&self) -> Result<String, ControlError> {
        self.call(reqwest::Method::GET, "/status").await
    }

    /// One of the three verbs.
    pub async fn command(&self, path: &str) -> Result<String, ControlError> {
        self.call(reqwest::Method::POST, path).await
    }

    async fn call(&self, method: reqwest::Method, path: &str) -> Result<String, ControlError> {
        let response = self
            .client
            .request(method, format!("{}{path}", self.file.base_url()))
            .bearer_auth(&self.file.token)
            .send()
            .await
            .map_err(|e| ControlError::Unreachable(e.to_string()))?;
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        if status.is_success() {
            Ok(body)
        } else {
            Err(ControlError::Refused {
                status: status.as_u16(),
                body,
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::status::State;

    async fn running() -> (
        ControlFile,
        Arc<StatusHandle>,
        tokio::sync::mpsc::UnboundedReceiver<Command>,
        tempfile::TempDir,
    ) {
        let dir = tempfile::tempdir().expect("tempdir");
        let paths = Paths {
            world: crate::world::World::Dev,
            home: dir.path().to_path_buf(),
            dir: dir.path().to_path_buf(),
            status: dir.path().join("status.json"),
            control: dir.path().join("control.json"),
        };
        let status = Arc::new(StatusHandle::new("https://example.test".into(), None, false));
        status.set_state(State::Connected);
        let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
        let server = ControlServer::bind().await.expect("bind");
        server.publish(&paths).expect("publish");
        let file = ControlFile::read(&paths.control)
            .expect("read")
            .expect("published");
        server.serve(Arc::clone(&status), tx);
        (file, status, rx, dir)
    }

    #[tokio::test]
    async fn the_control_file_names_the_port_and_a_fresh_token() {
        let (file, _status, _rx, _dir) = running().await;
        assert_eq!(file.version, 1);
        assert_eq!(file.world, "dev");
        assert_eq!(file.pid, std::process::id());
        assert!(file.port > 0);
        assert_eq!(file.token.len(), 64, "a 256-bit token in hex");
        assert!(file.base_url().starts_with("http://127.0.0.1:"));
    }

    #[tokio::test]
    async fn status_needs_the_token_and_returns_the_document() {
        let (file, _status, _rx, _dir) = running().await;
        let client = reqwest::Client::new();

        let refused = client
            .get(format!("{}/status", file.base_url()))
            .send()
            .await
            .expect("request");
        assert_eq!(refused.status(), 401);
        let body = refused.text().await.expect("body");
        assert!(body.contains("control token"), "{body}");

        let allowed = client
            .get(format!("{}/status", file.base_url()))
            .bearer_auth(&file.token)
            .send()
            .await
            .expect("request");
        assert_eq!(allowed.status(), 200);
        let document: serde_json::Value = allowed.json().await.expect("json");
        assert_eq!(document["state"], "connected");
    }

    #[tokio::test]
    async fn a_wrong_token_of_the_right_length_is_still_refused() {
        let (file, _status, _rx, _dir) = running().await;
        let wrong = "0".repeat(file.token.len());
        let response = reqwest::Client::new()
            .post(format!("{}/pause", file.base_url()))
            .bearer_auth(wrong)
            .send()
            .await
            .expect("request");
        assert_eq!(response.status(), 401);
    }

    #[tokio::test]
    async fn the_three_verbs_reach_the_supervisor_and_an_unknown_route_says_so() {
        let (file, _status, mut rx, _dir) = running().await;
        let client = reqwest::Client::new();
        for (path, expected) in [
            ("/pause", Command::Pause),
            ("/resume", Command::Resume),
            ("/sign-out", Command::SignOut),
        ] {
            let response = client
                .post(format!("{}{path}", file.base_url()))
                .bearer_auth(&file.token)
                .send()
                .await
                .expect("request");
            assert_eq!(response.status(), 202, "{path}");
            assert_eq!(rx.recv().await.expect("command"), expected);
        }

        let unknown = client
            .post(format!("{}/definitely-not-a-route", file.base_url()))
            .bearer_auth(&file.token)
            .send()
            .await
            .expect("request");
        assert_eq!(unknown.status(), 404);
        let body = unknown.text().await.expect("body");
        assert!(body.contains("does not answer"), "{body}");
    }

    #[tokio::test]
    async fn the_client_says_plainly_when_nothing_is_running() {
        let dir = tempfile::tempdir().expect("tempdir");
        let paths = Paths {
            world: crate::world::World::Dev,
            home: dir.path().to_path_buf(),
            dir: dir.path().to_path_buf(),
            status: dir.path().join("status.json"),
            control: dir.path().join("control.json"),
        };
        let error = ControlClient::connect(&paths).expect_err("nothing running");
        assert!(matches!(error, ControlError::NotRunning));
        let sentence = error.to_string();
        assert!(sentence.contains("not running"), "{sentence}");
        assert!(sentence.contains("Start it"), "{sentence}");
    }

    #[test]
    fn two_tokens_are_never_the_same() {
        let a = mint_token();
        let b = mint_token();
        assert_ne!(a, b);
        assert_eq!(a.len(), 64);
    }

    #[test]
    fn the_token_comparison_is_length_safe() {
        assert!(constant_time_eq(b"abc", b"abc"));
        assert!(!constant_time_eq(b"abc", b"abd"));
        assert!(!constant_time_eq(b"abc", b"ab"));
        assert!(!constant_time_eq(b"", b"a"));
        assert!(constant_time_eq(b"", b""));
    }
}
