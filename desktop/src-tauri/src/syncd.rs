//! The app's side of the sync daemon (FS-C5b, SPEC-CUSTODY §10, SPEC-ENGINE §1.2).
//!
//! Three jobs, and nothing else:
//!
//! 1. **Make sure `matrx-syncd` is running** when the app launches — §1.2's ladder, of which this
//!    build implements step 3, the detached first-run spawn. Steps 1 and 2 (adopt a running
//!    daemon, or ask the supervisor to start it) and the login-item registration are FS-L2a's.
//! 2. **Hold the `control` token.** It authorises sign-in, sign-out and shutdown, and
//!    SPEC-CUSTODY S17 is explicit that **it never enters JavaScript**. The webview asks through
//!    the commands below and Rust makes the call; the webview is handed only the `read` token.
//! 3. **Forward the OAuth deep link to the daemon**, which holds the PKCE verifier. The webview
//!    never sees a code.
//!
//! **The daemon is not supervised by this process** and must not be. SPEC-ENGINE rule 10 puts it
//! outside the app's process tree, and rule 9 forbids anything here from killing it by name — so
//! it is deliberately absent from `EngineSupervisorState`, from `ENGINE_PATTERN`, and from every
//! sweep. The one spawn below uses `setsid` (POSIX) / `DETACHED_PROCESS |
//! CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB` (Windows) precisely so that killing the
//! app can never cascade into it.

use serde::Serialize;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::time::Duration;

/// The daemon band this build talks to (C7, S21): 22160–22179 live, 22260–22279 dev. The world is
/// decided the same way the engine's is — `debug_assertions` — so a source run never touches the
/// installed app's daemon (Hard Rule 9).
const WORLD: &str = if cfg!(debug_assertions) {
    "dev"
} else {
    "live"
};

/// What the webview needs to talk to the daemon itself: the loopback base URL and the **read**
/// token. Deliberately no control token, and no socket path — a browser context can use neither.
#[derive(Debug, Clone, Serialize)]
pub struct SyncdClientConfig {
    /// `http://127.0.0.1:<port>`, or `None` when no daemon is publishing.
    pub base_url: Option<String>,
    /// Line 2 of `syncd.token` — authorises the five read routes and nothing else (S17).
    pub read_token: Option<String>,
    /// `live` or `dev`, so a surface can say which world it is in.
    pub world: &'static str,
}

/// This world's matrx home, resolved exactly as the rest of this file resolves it.
fn matrx_home() -> Option<PathBuf> {
    if let Ok(dir) = std::env::var("MATRX_HOME_DIR") {
        if !dir.is_empty() {
            return Some(PathBuf::from(dir));
        }
    }
    #[cfg(unix)]
    let home = std::env::var("HOME").ok()?;
    #[cfg(windows)]
    let home = std::env::var("USERPROFILE").ok()?;
    Some(PathBuf::from(home).join(crate::DEFAULT_MATRX_HOME_DIRNAME))
}

fn discovery() -> Option<serde_json::Value> {
    let path = matrx_home()?.join("syncd.json");
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// The two scoped tokens, as the single two-line file holds them (C5, S17).
fn tokens() -> Option<(String, String)> {
    let path = matrx_home()?.join("syncd.token");
    let text = std::fs::read_to_string(path).ok()?;
    let mut lines = text.lines();
    let control = lines.next()?.trim().to_string();
    let read = lines.next()?.trim().to_string();
    if control.is_empty() || read.is_empty() {
        return None;
    }
    Some((control, read))
}

fn base_url() -> Option<String> {
    let port = discovery()?.get("tcp_port")?.as_u64()?;
    Some(format!("http://127.0.0.1:{port}"))
}

fn is_healthy_daemon_response(response: &[u8]) -> bool {
    const OK_FIELD: &[u8] = b"\"ok\":true";
    response.starts_with(b"HTTP/1.1 200")
        && response
            .windows(OK_FIELD.len())
            .any(|part| part == OK_FIELD)
}

/// A discovery file is only a hint.  The daemon can be killed or crash before it removes the
/// file, and treating that stale file as proof of life leaves the app permanently unable to
/// sign in.  Prove that the published endpoint is really our daemon with its scoped read token
/// before skipping the first-run spawn.
fn daemon_is_reachable() -> bool {
    let Some(port) = discovery()
        .and_then(|value| value.get("tcp_port")?.as_u64())
        .and_then(|value| u16::try_from(value).ok())
    else {
        return false;
    };
    let Some((_, read_token)) = tokens() else {
        return false;
    };
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(250)) else {
        return false;
    };
    if stream
        .set_read_timeout(Some(Duration::from_millis(500)))
        .is_err()
        || stream
            .set_write_timeout(Some(Duration::from_millis(500)))
            .is_err()
    {
        return false;
    }
    let request = format!(
        "GET /v1/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {read_token}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = Vec::with_capacity(512);
    if stream.read_to_end(&mut response).is_err() {
        return false;
    }
    is_healthy_daemon_response(&response)
}

/// The host talks to the daemon over the **loopback listener**, not the Unix socket.
///
/// C7 says the Tauri process *prefers* the socket, and it would: it is one fewer listener. But
/// `reqwest` — the app's one HTTP client — has no Unix-socket transport, and adding a second HTTP
/// stack to the desktop binary to save a hop is the kind of layer "simplicity is survival"
/// forbids. The security posture is identical either way: both transports demand the same
/// `0600` bearer token, and the listener additionally enforces the `Host` allow-list.
async fn call(
    method: reqwest::Method,
    path: &str,
    body: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let base =
        base_url().ok_or_else(|| "AI Matrx Sync is not running on this computer.".to_string())?;
    let (control, _read) = tokens().ok_or_else(|| {
        "AI Matrx Sync is running but its access token file could not be read.".to_string()
    })?;
    let port = base.rsplit(':').next().unwrap_or_default().to_string();

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|e| e.to_string())?;
    let mut request = client
        .request(method, format!("{base}{path}"))
        .header("Authorization", format!("Bearer {control}"))
        .header("X-Matrx-Client", "app")
        // The daemon's DNS-rebinding guard. The host is not a browser and sends no Origin.
        .header("Host", format!("127.0.0.1:{port}"));
    if let Some(body) = body {
        request = request.json(&body);
    }

    let response = request.send().await.map_err(|e| e.to_string())?;
    let status = response.status();
    let value: serde_json::Value = response.json().await.unwrap_or(serde_json::Value::Null);
    if status.is_success() {
        return Ok(value);
    }
    // The daemon's envelope already carries a user-facing sentence and a remedy (SPEC-ENGINE
    // §3.1). Surfacing our own wording here would be a second vocabulary for the same condition.
    let message = value
        .get("error")
        .and_then(|e| e.get("message"))
        .and_then(|m| m.as_str())
        .or_else(|| value.get("state_reason").and_then(|m| m.as_str()))
        .unwrap_or("The sync service refused that request.");
    Err(message.to_string())
}

// ------------------------------------------------------------------ commands

/// What the webview needs to read its own session and token. Called at window setup.
#[tauri::command]
pub fn syncd_client_config() -> SyncdClientConfig {
    let read_token = tokens().map(|(_control, read)| read);
    SyncdClientConfig {
        base_url: base_url(),
        read_token,
        world: WORLD,
    }
}

/// Start a sign-in. The daemon generates the verifier and returns the URL to open.
#[tauri::command]
pub async fn syncd_sign_in() -> Result<serde_json::Value, String> {
    call(
        reqwest::Method::POST,
        "/v1/sign-in",
        Some(serde_json::json!({})),
    )
    .await
}

/// Forward a callback the OS delivered to us. **The code goes to the daemon, never to the
/// webview** — the daemon holds the verifier, so a code alone is worthless to anyone else (S1).
#[tauri::command]
pub async fn syncd_sign_in_callback(url: String) -> Result<serde_json::Value, String> {
    let (code, state) = parse_callback(&url)
        .ok_or_else(|| "That sign-in link did not carry a code and a state.".to_string())?;
    call(
        reqwest::Method::POST,
        "/v1/sign-in/callback",
        Some(serde_json::json!({ "code": code, "state": state })),
    )
    .await
}

/// Sign this device out. Control scope: a page cannot do this (§12).
#[tauri::command]
pub async fn syncd_sign_out() -> Result<serde_json::Value, String> {
    call(
        reqwest::Method::POST,
        "/v1/sign-out",
        Some(serde_json::json!({})),
    )
    .await
}

/// Hand the daemon a session this Mac held **before** the custody cutover, once.
///
/// The webview is the one place that still has it: before the cutover the Supabase client
/// persisted a session to the webview's own storage, so on a Mac that was signed in yesterday
/// that entry is still there while the daemon's brand-new journal knows nothing. Reading it there
/// and handing it here is what turns "signed out this morning for no reason" into "still signed
/// in". The call is **control scope** — Rust makes it (S17), the same way sign-in and sign-out do,
/// so a page cannot install a session on this device.
///
/// It returns the daemon's outcome verbatim, including `spent`: on `retry` (offline, a captive
/// portal) the caller keeps what it has and offers it again, because that credential is the only
/// recoverable one on the machine.
#[tauri::command]
pub async fn syncd_adopt_legacy_session(
    refresh_token: Option<String>,
    email: Option<String>,
) -> Result<serde_json::Value, String> {
    call(
        reqwest::Method::POST,
        "/v1/adopt",
        Some(serde_json::json!({ "refresh_token": refresh_token, "email": email })),
    )
    .await
}

/// The session, for the host's own use (the tray, and the first render before the stream opens).
#[tauri::command]
pub async fn syncd_session() -> Result<serde_json::Value, String> {
    call(reqwest::Method::GET, "/v1/session", None).await
}

/// Pull `code` and `state` out of `aimatrx://auth/callback?code=…&state=…`.
fn parse_callback(url: &str) -> Option<(String, String)> {
    let query = url.split_once('?')?.1;
    let mut code = None;
    let mut state = None;
    for pair in query.split('&') {
        let (key, value) = pair.split_once('=')?;
        let decoded = percent_decode(value);
        match key {
            "code" => code = Some(decoded),
            "state" => state = Some(decoded),
            _ => {}
        }
    }
    Some((code?, state?))
}

fn percent_decode(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(byte) = u8::from_str_radix(&input[i + 1..i + 3], 16) {
                out.push(byte);
                i += 3;
                continue;
            }
        }
        out.push(if bytes[i] == b'+' { b' ' } else { bytes[i] });
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

// -------------------------------------------------------------------- spawn

/// Where the daemon binary lives for this build.
fn daemon_binary() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    #[cfg(windows)]
    let name = "matrx-syncd.exe";
    #[cfg(not(windows))]
    let name = "matrx-syncd";

    // Packaged: the Tauri `externalBin` sidecar sits next to the main executable. (SPEC-ENGINE
    // §0.1 moves this to `Contents/Frameworks/Matrx Sync.app` on macOS so the daemon can carry its
    // own TCC usage strings; that packaging change is FS-L2a's, and this lookup already prefers
    // the bundle when it exists.)
    #[cfg(target_os = "macos")]
    {
        if let Some(contents) = dir.parent() {
            let helper = contents
                .join("Frameworks")
                .join("Matrx Sync.app")
                .join("Contents")
                .join("MacOS")
                .join(name);
            if helper.exists() {
                return Some(helper);
            }
        }
    }
    let beside = dir.join(name);
    if beside.exists() {
        return Some(beside);
    }
    // A source run: `cargo build -p matrx-syncd` puts it in the shared target directory, which is
    // where the app binary itself is running from.
    let sibling = dir.join(name);
    sibling.exists().then_some(sibling)
}

/// SPEC-ENGINE §1.2 step 3 — the detached first-run spawn.
///
/// Idempotent and silent: if a daemon is already publishing a discovery file this does nothing,
/// and the daemon's own clobber rule refuses a second instance anyway. It is **not** an error for
/// the binary to be absent — a build without the sidecar simply has no sync, which the Sync
/// surface reports as `daemon_not_running` with its "Start sync" action rather than a crash.
pub fn ensure_running() {
    if daemon_is_reachable() {
        return;
    }
    let Some(binary) = daemon_binary() else {
        println!("[syncd] no matrx-syncd binary beside this build; sync is unavailable");
        return;
    };

    let mut command = std::process::Command::new(&binary);
    command
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());

    #[cfg(unix)]
    unsafe {
        use std::os::unix::process::CommandExt;
        // `setsid` puts the daemon in its own session and process group, so it is not in this
        // app's tree at steady state (rule 10) and no group signal aimed at the app reaches it.
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
        // Breakaway is what stops `taskkill /F /T /IM "AI Matrx.exe"` — which the NSIS uninstall
        // hook runs — from cascading into the daemon. Where the containing job forbids breakaway
        // the spawn is retried without it: one pre-sign-in daemon may then be job-bound, and the
        // next start is supervisor-owned anyway (SPEC-ENGINE §1.2).
        command.creation_flags(
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
        );
    }

    match command.spawn() {
        Ok(child) => println!("[syncd] started matrx-syncd (pid {})", child.id()),
        Err(error) => {
            #[cfg(windows)]
            {
                let mut retry = std::process::Command::new(&binary);
                use std::os::windows::process::CommandExt;
                retry
                    .stdin(std::process::Stdio::null())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .creation_flags(0x0000_0008 | 0x0000_0200);
                if let Ok(child) = retry.spawn() {
                    println!(
                        "[syncd] started matrx-syncd without job breakaway (pid {})",
                        child.id()
                    );
                    return;
                }
            }
            println!("[syncd] could not start matrx-syncd: {error}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_callback_url_yields_its_code_and_state() {
        let parsed = parse_callback("aimatrx://auth/callback?code=abc&state=xyz").expect("parse");
        assert_eq!(parsed, ("abc".to_string(), "xyz".to_string()));
    }

    #[test]
    fn percent_encoding_survives_the_forward() {
        let parsed =
            parse_callback("aimatrx://auth/callback?code=a%2Bb&state=c%3Dd").expect("parse");
        assert_eq!(parsed, ("a+b".to_string(), "c=d".to_string()));
    }

    #[test]
    fn a_link_missing_either_half_is_refused_rather_than_half_forwarded() {
        assert!(parse_callback("aimatrx://auth/callback?code=abc").is_none());
        assert!(parse_callback("aimatrx://auth/callback").is_none());
    }

    #[test]
    fn the_world_matches_the_engines_dev_live_rule() {
        // Hard Rule 9: a source build is the dev world, a release build the live one — the same
        // `debug_assertions` split the engine and the webview already use.
        assert_eq!(
            WORLD,
            if cfg!(debug_assertions) {
                "dev"
            } else {
                "live"
            }
        );
    }

    #[test]
    fn only_a_successful_syncd_health_response_proves_liveness() {
        assert!(is_healthy_daemon_response(
            b"HTTP/1.1 200 OK\r\ncontent-length: 11\r\n\r\n{\"ok\":true}"
        ));
        assert!(!is_healthy_daemon_response(
            b"HTTP/1.1 503 Service Unavailable\r\n\r\n{\"ok\":true}"
        ));
        assert!(!is_healthy_daemon_response(
            b"HTTP/1.1 200 OK\r\n\r\n{\"ok\":false}"
        ));
    }

    #[test]
    fn the_client_config_never_carries_the_control_token() {
        // S17/§12: a compromised webview must not be able to sign the device out or stop the
        // daemon, and the only thing standing between it and those routes is that it never holds
        // the control token.
        let config = syncd_client_config();
        let json = serde_json::to_string(&config).expect("serialize");
        assert!(!json.contains("control"));
        if let (Some((control, _read)), true) = (tokens(), config.read_token.is_some()) {
            assert!(!json.contains(&control));
        }
    }
}
