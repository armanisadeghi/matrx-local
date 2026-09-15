//! The control API, proven against a **real daemon** (SPEC-ENGINE §3, C1/C5/C7, SPEC-CUSTODY S17).
//!
//! These tests start the actual `matrx-syncd` binary in a private `MATRX_HOME_DIR` and talk to it
//! over its own loopback listener. Nothing here is mocked, because the things worth proving —
//! which routes a scope reaches, that an unserved route answers with the error envelope, that the
//! `Host` guard refuses a rebound name, that a shutdown arriving during start is honoured, that a
//! graceful stop leaves no credential behind — are all properties of the running process and of
//! nothing smaller. A unit test over the route table could only restate its own match arms.
//!
//! **They bind the dev daemon band.** A dev daemon already running on this machine makes them
//! fail loudly rather than silently pass, which is the correct outcome: two daemons in one world
//! is precisely what the clobber rule refuses.

use std::io::Read as _;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

const BINARY: &str = env!("CARGO_BIN_EXE_matrx-syncd");

struct Daemon {
    child: Child,
    home: tempfile::TempDir,
    port: u16,
    control: String,
    read: String,
}

impl Daemon {
    fn start() -> Option<Self> {
        let home = tempfile::tempdir().expect("tempdir");
        let child = Command::new(BINARY)
            .env("MATRX_HOME_DIR", home.path())
            .arg("--world")
            .arg("dev")
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn matrx-syncd");
        let mut daemon = Daemon { child, home, port: 0, control: String::new(), read: String::new() };
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline {
            if let Some((port, control, read)) = daemon.published() {
                daemon.port = port;
                daemon.control = control;
                daemon.read = read;
                return Some(daemon);
            }
            if matches!(daemon.child.try_wait(), Ok(Some(_))) {
                let mut stderr = String::new();
                if let Some(mut pipe) = daemon.child.stderr.take() {
                    let _ = pipe.read_to_string(&mut stderr);
                }
                panic!("matrx-syncd exited before publishing: {stderr}");
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        None
    }

    fn published(&self) -> Option<(u16, String, String)> {
        let json: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(self.discovery()).ok()?).ok()?;
        let port = json.get("tcp_port")?.as_u64()? as u16;
        let text = std::fs::read_to_string(self.token_file()).ok()?;
        let mut lines = text.lines();
        let control = lines.next()?.trim().to_string();
        let read = lines.next()?.trim().to_string();
        (!control.is_empty() && !read.is_empty()).then_some((port, control, read))
    }

    fn discovery(&self) -> PathBuf {
        self.home.path().join("syncd.json")
    }

    fn token_file(&self) -> PathBuf {
        self.home.path().join("syncd.token")
    }

    fn socket(&self) -> PathBuf {
        self.home.path().join("run").join("syncd.sock")
    }

    fn request(
        &self,
        method: &str,
        path: &str,
        token: Option<&str>,
        client_header: bool,
        host: Option<&str>,
        origin: Option<&str>,
    ) -> (u16, String) {
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .expect("client");
        let mut req = client.request(
            method.parse().expect("method"),
            format!("http://127.0.0.1:{}{path}", self.port),
        );
        if let Some(token) = token {
            req = req.header("Authorization", format!("Bearer {token}"));
        }
        if client_header {
            req = req.header("X-Matrx-Client", "cli");
        }
        req = req.header(
            "Host",
            host.map(str::to_string).unwrap_or_else(|| format!("127.0.0.1:{}", self.port)),
        );
        if let Some(origin) = origin {
            req = req.header("Origin", origin);
        }
        if method == "POST" {
            req = req.header("Content-Type", "application/json").body("{}");
        }
        let response = req.send().expect("request reached the daemon");
        (response.status().as_u16(), response.text().unwrap_or_default())
    }

    fn get(&self, path: &str, token: &str) -> (u16, String) {
        self.request("GET", path, Some(token), true, None, None)
    }

    fn post(&self, path: &str, token: &str) -> (u16, String) {
        self.request("POST", path, Some(token), true, None, None)
    }

    fn wait_for_exit(&mut self, budget: Duration) -> bool {
        let deadline = Instant::now() + budget;
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return true;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        false
    }
}

impl Drop for Daemon {
    fn drop(&mut self) {
        // A failed test must not leave a daemon holding the band for the next one. The polite verb
        // first; the kill is a last resort a TEST may use on a process it started itself, and
        // never something the product does (rule 9).
        if matches!(self.child.try_wait(), Ok(None)) {
            let control = self.control.clone();
            let _ = self.request("POST", "/v1/shutdown", Some(&control), true, None, None);
            if !self.wait_for_exit(Duration::from_secs(10)) {
                let _ = self.child.kill();
            }
        }
    }
}

fn error_code(body: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_str(body).ok()?;
    Some(value.get("error")?.get("code")?.as_str()?.to_string())
}

fn require_daemon() -> Daemon {
    Daemon::start().expect(
        "matrx-syncd did not publish within 20s. If a dev daemon is already running on this \
         machine, stop it first — two daemons in one world is what the clobber rule refuses.",
    )
}

#[test]
fn the_read_scope_reaches_the_five_read_routes_and_nothing_else() {
    // S17/§12: the sentence the scope split exists to make true — a compromised webview can obtain
    // short-lived tokens and read status, and cannot sign the device out or stop it.
    let daemon = require_daemon();

    for path in ["/v1/version", "/v1/session", "/v1/token"] {
        let (status, body) = daemon.get(path, &daemon.read);
        assert_ne!(status, 403, "the read scope must reach {path}: {body}");
        assert_ne!(error_code(&body).as_deref(), Some("forbidden_scope"));
    }

    for path in ["/v1/sign-in", "/v1/sign-in/callback", "/v1/sign-out", "/v1/shutdown"] {
        let (status, body) = daemon.post(path, &daemon.read);
        assert_eq!(status, 403, "the read scope must NOT reach {path}: {body}");
        assert_eq!(error_code(&body).as_deref(), Some("forbidden_scope"));
    }

    // The refused shutdown really was refused.
    assert_eq!(daemon.get("/v1/version", &daemon.control).0, 200);
}

#[test]
fn auth_and_the_client_header_are_required_on_every_route_but_health() {
    let daemon = require_daemon();

    let (status, body) = daemon.request("GET", "/v1/health", None, false, None, None);
    assert_eq!(status, 200, "health is the one unauthenticated route: {body}");

    let (status, body) = daemon.request("GET", "/v1/session", None, true, None, None);
    assert_eq!(status, 401);
    assert_eq!(error_code(&body).as_deref(), Some("unauthorized"));

    assert_eq!(
        daemon.request("GET", "/v1/session", Some("not-a-token"), true, None, None).0,
        401
    );

    let (status, body) = daemon.request("GET", "/v1/session", Some(&daemon.read), false, None, None);
    assert_eq!(status, 400, "X-Matrx-Client is required: {body}");
    assert_eq!(error_code(&body).as_deref(), Some("bad_request"));
}

#[test]
fn an_unserved_route_answers_404_with_the_error_envelope_never_a_stub() {
    let daemon = require_daemon();
    for path in ["/v1/status", "/v1/mappings", "/v1/conflicts", "/v1/activity", "/health", "/v2/session"] {
        let (status, body) = daemon.get(path, &daemon.control);
        assert_eq!(status, 404, "{path} answered {status}: {body}");
        let value: serde_json::Value = serde_json::from_str(&body).expect("envelope");
        let error = value.get("error").expect("error object");
        for field in ["code", "message", "remedy", "retryable", "details"] {
            assert!(error.get(field).is_some(), "{path} envelope lacks {field}");
        }
    }
}

#[test]
fn the_host_guard_refuses_a_rebound_name_and_the_origin_allow_list_holds() {
    let daemon = require_daemon();

    let (status, body) =
        daemon.request("GET", "/v1/session", Some(&daemon.read), true, Some("evil.example:1"), None);
    assert_eq!(status, 403, "{body}");
    assert_eq!(error_code(&body).as_deref(), Some("forbidden_origin"));

    let (status, body) = daemon.request(
        "GET", "/v1/session", Some(&daemon.read), true, None, Some("https://evil.example"),
    );
    assert_eq!(status, 403, "{body}");
    assert_eq!(error_code(&body).as_deref(), Some("forbidden_origin"));

    for origin in ["tauri://localhost", "http://tauri.localhost", "http://localhost:1420"] {
        let (status, body) =
            daemon.request("GET", "/v1/session", Some(&daemon.read), true, None, Some(origin));
        assert_eq!(status, 200, "{origin} was refused: {body}");
    }
}

#[test]
fn a_shutdown_arriving_during_start_is_honoured() {
    // Defect 6: `notify_waiters` wakes only waiters that already exist, so a shutdown arriving
    // before the run loop reached its `select!` — exactly the window in which the daemon is
    // adopting its session and most likely to be stuck — was answered `202 accepted` and then
    // silently dropped. Fired as early as the socket allows, repeatedly, because the window is
    // small and a flaky guard is not a guard.
    for attempt in 0..8 {
        let mut daemon = require_daemon();
        let control = daemon.control.clone();
        let (status, body) = daemon.post("/v1/shutdown", &control);
        assert_eq!(status, 202, "attempt {attempt}: {body}");
        assert!(
            daemon.wait_for_exit(Duration::from_secs(25)),
            "attempt {attempt}: answered 202 and did not stop — a promise it did not keep",
        );
    }
}

#[test]
fn a_graceful_stop_leaves_no_discovery_file_and_no_tokens_behind() {
    // The two scoped tokens are minted fresh at every start, so a token file that outlives the
    // process authorises nothing and is only a live-looking credential sitting on disk.
    let mut daemon = require_daemon();
    assert!(daemon.discovery().exists());
    assert!(daemon.token_file().exists());

    let control = daemon.control.clone();
    assert_eq!(daemon.post("/v1/shutdown", &control).0, 202);
    assert!(daemon.wait_for_exit(Duration::from_secs(25)), "it did not stop");

    assert!(!daemon.discovery().exists(), "syncd.json outlived the daemon");
    assert!(!daemon.token_file().exists(), "syncd.token outlived the daemon that minted it");
    assert!(!daemon.socket().exists(), "the socket outlived the daemon");
}

#[test]
fn the_sign_in_callback_page_does_not_claim_a_sign_in_that_has_not_happened() {
    // Defect 3: the loopback page is written BEFORE the token exchange runs, and it used to say
    // "You are signed in to AI Matrx" — which it said, for real, on a run where the sign-in was in
    // fact being lost. It must report only that the browser's part is over.
    let daemon = require_daemon();

    let (status, body) = daemon.post("/v1/sign-in", &daemon.control);
    assert_eq!(status, 200, "sign-in could not start: {body}");
    let start: serde_json::Value = serde_json::from_str(&body).expect("sign-in payload");
    assert_eq!(start["redirect_kind"], "loopback", "the dev world signs in over loopback (S3)");
    let redirect = start["redirect_uri"].as_str().expect("redirect_uri");

    // A state the daemon will not match, so nothing is exchanged and no network call is made —
    // the page is written either way, which is exactly the point.
    let page = reqwest::blocking::Client::new()
        .get(format!("{redirect}?code=not-a-real-code&state=not-a-real-state"))
        .timeout(Duration::from_secs(10))
        .send()
        .expect("the daemon's loopback listener answered")
        .text()
        .unwrap_or_default();

    assert!(
        !page.to_lowercase().contains("you are signed in"),
        "the page claims a sign-in that has not been attempted yet: {page}"
    );
    assert!(
        page.contains("received your sign-in"),
        "the page should say the sign-in was received, not that it succeeded: {page}"
    );
}
