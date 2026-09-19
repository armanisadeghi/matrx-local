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
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;
use tokio::sync::Mutex;

const APP_PROTOCOL_VERSION: u64 = 1;
/// The memoised outcome of startup reconciliation. A `Mutex` rather than a `OnceCell` because
/// **Start sync must be able to run it again** — a once-cell turned the first failure into the
/// permanent answer, which is how a Mac whose helper cannot exec had no way back short of a
/// relaunch.
static RECONCILE_OUTCOME: Mutex<Option<Result<(), DaemonDown>>> = Mutex::const_new(None);
/// The last daemon-down reason written to lifecycle.log. Only a CHANGE is written: the same
/// reconciliation failure was logged 4,400+ times on one machine in three days, which buries the
/// one line that matters under its own repeats.
static LAST_LOGGED_REASON: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
const PAYLOAD_VERSION_TIMEOUT: Duration = Duration::from_secs(5);
const VERSION_CALL_TIMEOUT: Duration = Duration::from_secs(2);
const VERIFY_REPLACEMENT_TIMEOUT: Duration = Duration::from_secs(10);
const RECONCILE_POLL_INTERVAL: Duration = Duration::from_millis(200);

/// The daemon band this build talks to (C7, S21): 22160–22179 live, 22260–22279 dev. The world is
/// decided the same way the engine's is — `debug_assertions` — so a source run never touches the
/// installed app's daemon (Hard Rule 9).
const WORLD: &str = if cfg!(debug_assertions) {
    "dev"
} else {
    "live"
};

/// The world this host exposes and passes to its daemon.
///
/// A packaged smoke app is still a release binary, but its private test home
/// must never select the live Keychain service.  The existing isolated-test
/// authority is the only exception to the normal debug/live build split.
fn syncd_world_for(isolated_test: bool) -> &'static str {
    if isolated_test {
        "dev"
    } else {
        WORLD
    }
}

fn syncd_world() -> &'static str {
    syncd_world_for(crate::isolated_test_run())
}

/// Why the sync daemon is not usable, as ONE honest snapshot (law 4).
///
/// The daemon's own `state`/`state_reason` enum (`crates/matrx-sync/contracts/honest_states.json`)
/// describes a *session*, and a daemon that never started has no session to describe — so these
/// are app-side codes, carried to the webview through the same `state_reason` contract plus a
/// `remedy` a non-technical person can act on.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DaemonDown {
    /// A stable app-side classification: `helper_missing`, `helper_cannot_execute`,
    /// `never_became_ready`, `unreachable`, `incompatible`, `upgrade_blocked`.
    pub code: &'static str,
    /// What happened, in words a person can read.
    pub state_reason: String,
    /// What that person can do about it.
    pub remedy: &'static str,
    /// The engineering half — exit status, signal, transport error. Shown inside `state_reason`
    /// and repeated here so a Support report does not need the sentence parsed.
    pub detail: String,
}

const REMEDY_UPDATE: &str =
    "Update AI Matrx to the latest version. If this keeps happening after updating, report it \
from Settings → Support.";
const REMEDY_REINSTALL: &str =
    "Reinstall AI Matrx. If this keeps happening after reinstalling, report it from \
Settings → Support.";
const REMEDY_START_SYNC: &str =
    "Choose Start sync to try again. If it keeps failing, restart your computer and report it \
from Settings → Support.";
const REMEDY_RESTART: &str =
    "Quit AI Matrx, restart your computer, then open AI Matrx again. If it keeps happening, \
report it from Settings → Support.";

impl DaemonDown {
    fn helper_missing() -> Self {
        Self {
            code: "helper_missing",
            state_reason: "AI Matrx Sync is missing from this copy of AI Matrx, so this computer \
cannot sign in or sync."
                .to_string(),
            remedy: REMEDY_REINSTALL,
            detail: "no matrx-syncd binary beside this build".to_string(),
        }
    }

    fn cannot_execute(detail: impl Into<String>) -> Self {
        let detail = detail.into();
        Self {
            code: "helper_cannot_execute",
            state_reason: format!(
                "This build's sync helper cannot start on this computer ({detail}), so this \
computer cannot sign in or sync."
            ),
            remedy: REMEDY_UPDATE,
            detail,
        }
    }

    fn never_became_ready(detail: impl Into<String>) -> Self {
        let detail = detail.into();
        Self {
            code: "never_became_ready",
            state_reason: format!(
                "AI Matrx Sync started but never became ready ({detail}), so this computer cannot \
sign in or sync."
            ),
            remedy: REMEDY_START_SYNC,
            detail,
        }
    }

    fn unreachable(detail: impl Into<String>) -> Self {
        let detail = detail.into();
        Self {
            code: "unreachable",
            state_reason: format!(
                "AI Matrx Sync is running on this computer but is not answering ({detail}), so \
this computer cannot sign in or sync."
            ),
            remedy: REMEDY_START_SYNC,
            detail,
        }
    }

    fn incompatible(detail: impl Into<String>) -> Self {
        let detail = detail.into();
        Self {
            code: "incompatible",
            state_reason: format!(
                "AI Matrx Sync on this computer does not match this version of AI Matrx \
({detail}), so this computer cannot sign in or sync."
            ),
            remedy: REMEDY_UPDATE,
            detail,
        }
    }

    fn upgrade_blocked(detail: impl Into<String>) -> Self {
        let detail = detail.into();
        Self {
            code: "upgrade_blocked",
            state_reason: format!(
                "AI Matrx Sync is running an older version and would not step aside for this \
update ({detail}), so this computer cannot sign in or sync."
            ),
            remedy: REMEDY_RESTART,
            detail,
        }
    }
}

/// The ONE snapshot every surface renders for the daemon process itself.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DaemonState {
    /// True only when an authenticated, compatible daemon answered just now.
    pub running: bool,
    /// `running`, or the `DaemonDown` code.
    pub code: &'static str,
    /// Empty when running; otherwise the honest sentence.
    pub state_reason: String,
    /// `None` when running.
    pub remedy: Option<&'static str>,
    /// The engineering half, or empty.
    pub detail: String,
}

impl DaemonState {
    fn running() -> Self {
        Self {
            running: true,
            code: "running",
            state_reason: String::new(),
            remedy: None,
            detail: String::new(),
        }
    }
}

impl From<DaemonDown> for DaemonState {
    fn from(down: DaemonDown) -> Self {
        Self {
            running: false,
            code: down.code,
            state_reason: down.state_reason,
            remedy: Some(down.remedy),
            detail: down.detail,
        }
    }
}

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

/// The discovery file exactly as it is on disk — alive or not. Only the reconciliation ladder,
/// which exists to notice a corpse's file, reads it.
fn discovery_file() -> Option<serde_json::Value> {
    let path = matrx_home()?.join("syncd.json");
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// The stale-pid rule, as a pure function so it can be proven.
///
/// **A discovery file whose `pid` is not alive is ABSENT.** Reading a dead daemon's port out of it
/// is what sent `POST http://127.0.0.1:22162/v1/sign-in` at nothing for three days and showed the
/// person a transport error instead of a state. A file with no `pid` proves no liveness either, so
/// it is absent too — the daemon's own writer always writes one.
fn live_discovery(file: Option<serde_json::Value>, alive: impl Fn(u32) -> bool) -> Option<serde_json::Value> {
    let value = file?;
    let pid = value
        .get("pid")
        .and_then(serde_json::Value::as_u64)
        .and_then(|pid| u32::try_from(pid).ok())?;
    alive(pid).then_some(value)
}

/// The discovery file, for every consumer that wants to TALK to the daemon.
fn discovery() -> Option<serde_json::Value> {
    live_discovery(discovery_file(), process_is_alive)
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

#[cfg(test)]
fn is_healthy_daemon_response(response: &[u8]) -> bool {
    const OK_FIELD: &[u8] = b"\"ok\":true";
    response.starts_with(b"HTTP/1.1 200")
        && response
            .windows(OK_FIELD.len())
            .any(|part| part == OK_FIELD)
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
        .no_proxy()
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
pub async fn syncd_client_config() -> Result<SyncdClientConfig, String> {
    let state = daemon_state_from(startup_reconcile().await).await;
    if !state.running {
        // ONE vocabulary: the rejection carries the same sentence `syncd_daemon_state` gives the
        // screen, and the lifecycle line is written once per distinct reason, not per call.
        log_reason_once(&format!(
            "[syncd] sync is not available: {} ({})",
            state.state_reason, state.detail
        ));
        return Err(state.state_reason);
    }
    Ok(SyncdClientConfig {
        base_url: base_url(),
        read_token: tokens().map(|(_control, read)| read),
        world: syncd_world(),
    })
}

/// The daemon-process state, for every surface that renders it. Never throws: a daemon that is
/// not running is an answer, not a failure.
#[tauri::command]
pub async fn syncd_daemon_state() -> DaemonState {
    let state = daemon_state_from(startup_reconcile().await).await;
    if !state.running {
        log_reason_once(&format!(
            "[syncd] sync is not available: {} ({})",
            state.state_reason, state.detail
        ));
    }
    state
}

/// **Start sync.** Runs startup reconciliation again and answers with the outcome — so a control
/// that fails updates the reason instead of doing nothing.
#[tauri::command]
pub async fn syncd_start() -> DaemonState {
    let state = daemon_state_from(retry_reconcile().await).await;
    log_reason_once(&format!(
        "[syncd] Start sync: {}",
        if state.running {
            "sync is running".to_string()
        } else {
            format!("{} ({})", state.state_reason, state.detail)
        }
    ));
    state
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

    // Packaged: the daemon sits next to the main executable in Contents/MacOS. On macOS it gets
    // there as a `bundle.macOS.files` entry pre-signed with the sidecar entitlements, NOT as an
    // `externalBin` (see crate::bundled_helper_path — externalBin inherits the host's
    // profile-backed entitlements and AMFI SIGKILLs the helper at exec). Windows and Linux still
    // use `externalBin`; either way the binary lands beside the host executable. (SPEC-ENGINE
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

#[derive(Debug, Clone, PartialEq, Eq)]
struct DaemonVersion {
    daemon_version: String,
    protocol_version: u64,
    min_protocol_version: u64,
    world: String,
}

#[derive(Debug, PartialEq, Eq)]
enum ReconcileDecision {
    StartAbsent,
    Adopt,
    Upgrade,
    Refuse(&'static str),
}

fn compare_versions(left: &str, right: &str) -> Option<std::cmp::Ordering> {
    Some(
        semver::Version::parse(left)
            .ok()?
            .cmp(&semver::Version::parse(right).ok()?),
    )
}

fn decide_reconciliation(
    expected_version: &str,
    expected_world: &str,
    observed: Option<&DaemonVersion>,
) -> ReconcileDecision {
    let Some(observed) = observed else {
        return ReconcileDecision::StartAbsent;
    };
    if observed.world != expected_world {
        return ReconcileDecision::Refuse("world mismatch");
    }
    if observed.min_protocol_version > APP_PROTOCOL_VERSION
        || observed.protocol_version < APP_PROTOCOL_VERSION
    {
        return ReconcileDecision::Refuse("protocol mismatch");
    }
    match compare_versions(&observed.daemon_version, expected_version) {
        Some(std::cmp::Ordering::Less) => ReconcileDecision::Upgrade,
        Some(std::cmp::Ordering::Equal | std::cmp::Ordering::Greater) => ReconcileDecision::Adopt,
        None => ReconcileDecision::Refuse("daemon version is malformed"),
    }
}

fn may_spawn_replacement(
    shutdown_accepted: bool,
    original_exited: bool,
    discovery_removed: bool,
) -> bool {
    shutdown_accepted && original_exited && discovery_removed
}

#[cfg(test)]
fn start_absent_allowed(discovery_present: bool) -> bool {
    !discovery_present
}

fn stale_discovery_allows_recovery(discovery_present: bool, valid_pid_exited: bool) -> bool {
    !discovery_present || valid_pid_exited
}

fn replacement_matches(expected_version: &str, world: &str, version: &DaemonVersion) -> bool {
    version.world == world
        && version.protocol_version >= APP_PROTOCOL_VERSION
        && version.min_protocol_version <= APP_PROTOCOL_VERSION
        && version.daemon_version == expected_version
}

fn version_from_value(value: serde_json::Value) -> Result<DaemonVersion, String> {
    let number = |name: &str| {
        value
            .get(name)
            .and_then(serde_json::Value::as_u64)
            .ok_or_else(|| format!("/v1/version did not contain {name}"))
    };
    Ok(DaemonVersion {
        daemon_version: value
            .get("daemon_version")
            .and_then(serde_json::Value::as_str)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| "/v1/version did not contain daemon_version".to_string())?
            .to_string(),
        protocol_version: number("protocol_version")?,
        min_protocol_version: number("min_protocol_version")?,
        world: value
            .get("world")
            .and_then(serde_json::Value::as_str)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| "/v1/version did not contain world".to_string())?
            .to_string(),
    })
}

/// The exit status of a helper that did not succeed, in the terms a Support report needs: on
/// Unix a signal (SIGKILL = 9, which is what Gatekeeper does to an unsigned helper) beats a code.
fn exit_detail(status: &std::process::ExitStatus) -> String {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return format!("was killed by signal {signal}");
        }
    }
    match status.code() {
        Some(code) => format!("exited with status {code}"),
        None => "exited for an unknown reason".to_string(),
    }
}

async fn payload_version(binary: &Path) -> Result<String, String> {
    use tokio::io::AsyncReadExt;

    let mut child = tokio::process::Command::new(binary)
        .arg("--version")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|error| format!("could not inspect bundled matrx-syncd version: {error}"))?;
    let status = match tokio::time::timeout(PAYLOAD_VERSION_TIMEOUT, child.wait()).await {
        Ok(Ok(status)) => status,
        Ok(Err(error)) => {
            return Err(format!(
                "could not inspect bundled matrx-syncd version: {error}"
            ))
        }
        Err(_) => {
            let _ = child.kill().await;
            return Err("bundled matrx-syncd --version timed out".to_string());
        }
    };
    if !status.success() {
        // "failed" alone is what made the macOS SIGKILL-at-exec regression invisible: every
        // sealed release since v1.4.137 logged the same five words and never the signal.
        return Err(format!(
            "bundled matrx-syncd --version {}",
            exit_detail(&status)
        ));
    }
    let mut output = String::new();
    if let Some(mut stdout) = child.stdout.take() {
        stdout
            .read_to_string(&mut output)
            .await
            .map_err(|error| format!("could not read bundled matrx-syncd version: {error}"))?;
    }
    output
        .split_whitespace()
        .nth(1)
        .filter(|version| semver::Version::parse(version).is_ok())
        .map(str::to_string)
        .ok_or_else(|| "bundled matrx-syncd --version returned no usable app version".to_string())
}

async fn authenticated_version() -> Result<DaemonVersion, String> {
    let value = tokio::time::timeout(
        VERSION_CALL_TIMEOUT,
        call(reqwest::Method::GET, "/v1/version", None),
    )
    .await
    .map_err(|_| "authenticated /v1/version timed out".to_string())??;
    version_from_value(value)
}

fn discovery_pid() -> Option<u32> {
    discovery_file()?
        .get("pid")?
        .as_u64()
        .and_then(|pid| u32::try_from(pid).ok())
}

fn discovery_is_removed() -> bool {
    matrx_home().is_some_and(|dir| !dir.join("syncd.json").exists())
}

fn process_is_alive(pid: u32) -> bool {
    use sysinfo::{Pid, ProcessesToUpdate, System};

    let process = Pid::from_u32(pid);
    let mut system = System::new();
    system.refresh_processes(ProcessesToUpdate::Some(&[process]), true);
    system.process(process).is_some()
}

async fn wait_for_original_shutdown(pid: u32, budget_seconds: u64) -> bool {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(budget_seconds + 5);
    loop {
        if !process_is_alive(pid) && discovery_is_removed() {
            return true;
        }
        if tokio::time::Instant::now() >= deadline {
            return false;
        }
        tokio::time::sleep(RECONCILE_POLL_INTERVAL).await;
    }
}

/// SPEC-ENGINE §1.2 step 3 — the detached first-run spawn.
fn spawn_daemon(binary: &Path, world: &str) -> Result<(), String> {
    let mut command = std::process::Command::new(&binary);
    command
        .args(["--world", world])
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
        Ok(child) => {
            crate::lifecycle_log::log(&format!("[syncd] started matrx-syncd (pid {})", child.id()));
            Ok(())
        }
        Err(error) => {
            #[cfg(windows)]
            {
                let mut retry = std::process::Command::new(&binary);
                use std::os::windows::process::CommandExt;
                retry
                    .args(["--world", world])
                    .stdin(std::process::Stdio::null())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .creation_flags(0x0000_0008 | 0x0000_0200);
                if let Ok(child) = retry.spawn() {
                    println!(
                        "[syncd] started matrx-syncd without job breakaway (pid {})",
                        child.id()
                    );
                    return Ok(());
                }
            }
            Err(format!("could not start matrx-syncd: {error}"))
        }
    }
}

async fn verify_replacement(expected_version: &str, world: &str) -> bool {
    let deadline = tokio::time::Instant::now() + VERIFY_REPLACEMENT_TIMEOUT;
    loop {
        if let Ok(version) = authenticated_version().await {
            if replacement_matches(expected_version, world, &version) {
                return true;
            }
        }
        if tokio::time::Instant::now() >= deadline {
            return false;
        }
        tokio::time::sleep(RECONCILE_POLL_INTERVAL).await;
    }
}

#[allow(async_fn_in_trait)]
trait ReconcileOps {
    async fn payload_version(&self, binary: &Path) -> Result<String, String>;
    async fn version(&self) -> Result<DaemonVersion, String>;
    fn discovery_pid(&self) -> Option<u32>;
    fn discovery_present(&self) -> bool;
    fn process_alive(&self, pid: u32) -> bool;
    async fn shutdown(&self) -> Result<serde_json::Value, String>;
    async fn wait_for_shutdown(&self, pid: u32, budget: u64) -> bool;
    fn spawn(&self, binary: &Path, world: &str) -> Result<(), String>;
    async fn verify(&self, expected_version: &str, world: &str) -> bool;
}

struct RuntimeOps;

impl ReconcileOps for RuntimeOps {
    async fn payload_version(&self, binary: &Path) -> Result<String, String> { payload_version(binary).await }
    async fn version(&self) -> Result<DaemonVersion, String> { authenticated_version().await }
    fn discovery_pid(&self) -> Option<u32> { discovery_pid() }
    fn discovery_present(&self) -> bool { !discovery_is_removed() }
    fn process_alive(&self, pid: u32) -> bool { process_is_alive(pid) }
    async fn shutdown(&self) -> Result<serde_json::Value, String> {
        call(reqwest::Method::POST, "/v1/shutdown", Some(serde_json::json!({ "reason": "upgrade" }))).await
    }
    async fn wait_for_shutdown(&self, pid: u32, budget: u64) -> bool { wait_for_original_shutdown(pid, budget).await }
    fn spawn(&self, binary: &Path, world: &str) -> Result<(), String> { spawn_daemon(binary, world) }
    async fn verify(&self, expected_version: &str, world: &str) -> bool { verify_replacement(expected_version, world).await }
}

async fn reconcile_with(
    ops: &impl ReconcileOps,
    binary: &Path,
    world: &str,
) -> Result<(), DaemonDown> {
    let expected_version = match ops.payload_version(binary).await {
        Ok(version) => version,
        // The helper could not be run at all — spawn refused, the exec was killed, the process
        // hung. This is the class macOS puts every sealed release in when the helper's signature
        // is not acceptable, and it is the reason a person must be told.
        Err(error) => return Err(DaemonDown::cannot_execute(error)),
    };
    let observed = match ops.version().await {
        Ok(version) => Some(version),
        Err(_)
            if stale_discovery_allows_recovery(
                ops.discovery_present(),
                ops.discovery_pid().is_some_and(|pid| !ops.process_alive(pid)),
            ) =>
        {
            None
        }
        Err(error) => return Err(DaemonDown::unreachable(error)),
    };
    match decide_reconciliation(&expected_version, &world, observed.as_ref()) {
        ReconcileDecision::StartAbsent => {
            ops.spawn(binary, world).map_err(DaemonDown::cannot_execute)?;
            if ops.verify(&expected_version, world).await {
                Ok(())
            } else {
                Err(DaemonDown::never_became_ready(
                    "the new daemon did not report the expected version in time",
                ))
            }
        }
        ReconcileDecision::Adopt => Ok(()),
        ReconcileDecision::Refuse(reason) => Err(DaemonDown::incompatible(reason)),
        ReconcileDecision::Upgrade => {
            let Some(original_pid) = ops.discovery_pid() else {
                return Err(DaemonDown::upgrade_blocked("the live daemon had no discovery pid"));
            };
            let shutdown = match ops.shutdown().await {
                Ok(value) => value,
                Err(error) => {
                    return Err(DaemonDown::upgrade_blocked(format!(
                        "it refused the shutdown request: {error}"
                    )))
                }
            };
            let budget = shutdown.get("budget_s").and_then(serde_json::Value::as_u64);
            if shutdown
                .get("accepted")
                .and_then(serde_json::Value::as_bool)
                != Some(true)
                || !matches!(budget, Some(5..=120))
            {
                return Err(DaemonDown::upgrade_blocked(
                    "it answered the shutdown request with an invalid acknowledgement",
                ));
            }
            let budget = budget.expect("validated above");
            if !may_spawn_replacement(
                true,
                ops.wait_for_shutdown(original_pid, budget).await,
                !ops.discovery_present(),
            ) {
                return Err(DaemonDown::upgrade_blocked(
                    "it accepted the shutdown request and then did not stop",
                ));
            }
            ops.spawn(binary, world).map_err(DaemonDown::cannot_execute)?;
            if ops.verify(&expected_version, world).await {
                Ok(())
            } else {
                Err(DaemonDown::never_became_ready(
                    "the replacement daemon did not report the expected version in time",
                ))
            }
        }
    }
}

async fn reconcile(binary: PathBuf, world: String) -> Result<(), DaemonDown> {
    reconcile_with(&RuntimeOps, &binary, &world).await
}

async fn run_reconcile() -> Result<(), DaemonDown> {
    let Some(binary) = daemon_binary() else {
        return Err(DaemonDown::helper_missing());
    };
    let world = syncd_world().to_string();
    reconcile(binary, world).await
}

/// Reconcile once and remember the outcome.
async fn startup_reconcile() -> Result<(), DaemonDown> {
    let mut outcome = RECONCILE_OUTCOME.lock().await;
    if let Some(remembered) = outcome.as_ref() {
        return remembered.clone();
    }
    let fresh = run_reconcile().await;
    *outcome = Some(fresh.clone());
    fresh
}

/// Run reconciliation AGAIN — what the Start sync control performs.
async fn retry_reconcile() -> Result<(), DaemonDown> {
    let mut outcome = RECONCILE_OUTCOME.lock().await;
    let fresh = run_reconcile().await;
    *outcome = Some(fresh.clone());
    fresh
}

/// Write one lifecycle line per DISTINCT reason. Repeats are dropped.
fn log_reason_once(reason: &str) {
    let Ok(mut last) = LAST_LOGGED_REASON.lock() else { return };
    if last.as_deref() == Some(reason) {
        return;
    }
    *last = Some(reason.to_string());
    crate::lifecycle_log::log(reason);
}

/// Is an authenticated daemon answering right now, and is it one this app can use?
async fn observe_daemon() -> Result<(), DaemonDown> {
    let version = authenticated_version()
        .await
        .map_err(DaemonDown::unreachable)?;
    if version.world != syncd_world()
        || version.min_protocol_version > APP_PROTOCOL_VERSION
        || version.protocol_version < APP_PROTOCOL_VERSION
    {
        return Err(DaemonDown::incompatible(format!(
            "it reports world {} and protocol {}",
            version.world, version.protocol_version
        )));
    }
    if base_url().is_none() || tokens().is_none() {
        return Err(DaemonDown::never_became_ready(
            "it published no endpoint or access token",
        ));
    }
    Ok(())
}

/// The ONE snapshot: what reconciliation concluded, corrected by what the daemon says now.
async fn daemon_state_from(reconciled: Result<(), DaemonDown>) -> DaemonState {
    match observe_daemon().await {
        Ok(()) => DaemonState::running(),
        // A daemon that is not answering NOW is explained by why it never started, when that is
        // what happened — never by the transport error that is only the symptom.
        Err(live) => match reconciled {
            Err(startup) => startup.into(),
            Ok(()) => live.into(),
        },
    }
}

/// Queue one serialized, non-blocking startup reconciliation.
pub fn ensure_running() {
    tauri::async_runtime::spawn(async move {
        if let Err(down) = startup_reconcile().await {
            log_reason_once(&format!(
                "[syncd] sync is not available: {} ({})",
                down.state_reason, down.detail
            ));
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

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
    fn an_isolated_packaged_smoke_run_selects_dev_for_config_and_spawn() {
        assert_eq!(syncd_world_for(true), "dev");
        assert_eq!(syncd_world_for(false), WORLD);
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
        let config = SyncdClientConfig {
            base_url: Some("http://127.0.0.1:22262".to_string()),
            read_token: tokens().map(|(_control, read)| read),
            world: "dev",
        };
        let json = serde_json::to_string(&config).expect("serialize");
        assert!(!json.contains("control"));
        if let (Some((control, _read)), true) = (tokens(), config.read_token.is_some()) {
            assert!(!json.contains(&control));
        }
    }

    fn daemon(version: &str) -> DaemonVersion {
        DaemonVersion {
            daemon_version: version.to_string(),
            protocol_version: APP_PROTOCOL_VERSION,
            min_protocol_version: APP_PROTOCOL_VERSION,
            world: "dev".to_string(),
        }
    }

    struct FakeOps {
        version: Result<DaemonVersion, String>,
        discovery_present: Cell<bool>,
        pid: Option<u32>,
        alive: Cell<bool>,
        shutdown: Result<serde_json::Value, String>,
        stopped: bool,
        verified: bool,
        shutdown_calls: Cell<u8>,
        spawn_calls: Cell<u8>,
    }

    impl FakeOps {
        fn ready(version: Result<DaemonVersion, String>) -> Self {
            Self {
                version,
                discovery_present: Cell::new(true),
                pid: Some(42),
                alive: Cell::new(true),
                shutdown: Ok(serde_json::json!({ "accepted": true, "budget_s": 5 })),
                stopped: true,
                verified: true,
                shutdown_calls: Cell::new(0),
                spawn_calls: Cell::new(0),
            }
        }
    }

    impl ReconcileOps for FakeOps {
        async fn payload_version(&self, _: &Path) -> Result<String, String> { Ok("1.4.141".to_string()) }
        async fn version(&self) -> Result<DaemonVersion, String> { self.version.clone() }
        fn discovery_pid(&self) -> Option<u32> { self.pid }
        fn discovery_present(&self) -> bool { self.discovery_present.get() }
        fn process_alive(&self, _: u32) -> bool { self.alive.get() }
        async fn shutdown(&self) -> Result<serde_json::Value, String> {
            self.shutdown_calls.set(self.shutdown_calls.get() + 1);
            self.shutdown.clone()
        }
        async fn wait_for_shutdown(&self, _: u32, _: u64) -> bool {
            if self.stopped {
                self.discovery_present.set(false);
            }
            self.stopped
        }
        fn spawn(&self, _: &Path, _: &str) -> Result<(), String> {
            self.spawn_calls.set(self.spawn_calls.get() + 1);
            Ok(())
        }
        async fn verify(&self, _: &str, _: &str) -> bool { self.verified }
    }

    #[tokio::test]
    async fn reconcile_orchestrates_only_confirmed_replacements() {
        let stale = FakeOps::ready(Ok(daemon("0.1.0")));
        assert!(reconcile_with(&stale, Path::new("payload"), "dev").await.is_ok());
        assert_eq!(stale.shutdown_calls.get(), 1);
        assert_eq!(stale.spawn_calls.get(), 1);

        let mut refused = FakeOps::ready(Ok(daemon("0.1.0")));
        refused.shutdown = Ok(serde_json::json!({ "accepted": false, "budget_s": 5 }));
        assert!(reconcile_with(&refused, Path::new("payload"), "dev").await.is_err());
        assert_eq!(refused.spawn_calls.get(), 0);

        let mut stalled = FakeOps::ready(Ok(daemon("0.1.0")));
        stalled.stopped = false;
        assert!(reconcile_with(&stalled, Path::new("payload"), "dev").await.is_err());
        assert_eq!(stalled.spawn_calls.get(), 0);
    }

    #[tokio::test]
    async fn reconcile_refuses_live_unreachable_and_adopts_equal_or_newer() {
        let unreachable = FakeOps::ready(Err("http failed".to_string()));
        assert!(reconcile_with(&unreachable, Path::new("payload"), "dev").await.is_err());
        assert_eq!(unreachable.spawn_calls.get(), 0);

        for version in ["1.4.141", "1.5.0", "1.5.0-beta.2"] {
            let adopted = FakeOps::ready(Ok(daemon(version)));
            assert!(reconcile_with(&adopted, Path::new("payload"), "dev").await.is_ok());
            assert_eq!(adopted.shutdown_calls.get(), 0);
            assert_eq!(adopted.spawn_calls.get(), 0);
        }
        assert_eq!(
            compare_versions("1.4.141-beta.2", "1.4.141-beta.10"),
            Some(std::cmp::Ordering::Less)
        );

        let stale_dead = FakeOps::ready(Err("http failed".to_string()));
        stale_dead.alive.set(false);
        assert!(reconcile_with(&stale_dead, Path::new("payload"), "dev").await.is_ok());
        assert_eq!(stale_dead.spawn_calls.get(), 1);

        let mut verify_failure = FakeOps::ready(Ok(daemon("0.1.0")));
        verify_failure.verified = false;
        assert!(reconcile_with(&verify_failure, Path::new("payload"), "dev").await.is_err());
    }

    #[test]
    fn stale_legacy_daemon_requires_an_upgrade() {
        assert_eq!(
            decide_reconciliation("1.4.141", "dev", Some(&daemon("0.1.0"))),
            ReconcileDecision::Upgrade
        );
    }

    #[test]
    fn equal_or_newer_compatible_daemon_is_adopted_without_downgrade() {
        assert_eq!(
            decide_reconciliation("1.4.141", "dev", Some(&daemon("1.4.141"))),
            ReconcileDecision::Adopt
        );
        assert_eq!(
            decide_reconciliation("1.4.141", "dev", Some(&daemon("1.5.0"))),
            ReconcileDecision::Adopt
        );
    }

    #[test]
    fn world_or_protocol_mismatch_never_requests_a_replacement() {
        let mut wrong_world = daemon("0.1.0");
        wrong_world.world = "live".to_string();
        assert_eq!(
            decide_reconciliation("1.4.141", "dev", Some(&wrong_world)),
            ReconcileDecision::Refuse("world mismatch")
        );
        let mut wrong_protocol = daemon("0.1.0");
        wrong_protocol.protocol_version = 0;
        assert_eq!(
            decide_reconciliation("1.4.141", "dev", Some(&wrong_protocol)),
            ReconcileDecision::Refuse("protocol mismatch")
        );
    }

    #[test]
    fn no_shutdown_failure_or_premature_unreachable_state_can_spawn_a_replacement() {
        assert!(!may_spawn_replacement(false, true, true));
        assert!(!may_spawn_replacement(true, false, true));
        assert!(!may_spawn_replacement(true, true, false));
        assert!(may_spawn_replacement(true, true, true));
    }

    #[test]
    fn unreachable_daemon_with_discovery_is_not_treated_as_absent() {
        assert!(!start_absent_allowed(true));
        assert!(start_absent_allowed(false));
        assert!(stale_discovery_allows_recovery(true, true));
        assert!(!stale_discovery_allows_recovery(true, false));
    }

    #[test]
    fn a_discovery_file_whose_pid_is_dead_is_absent_for_every_consumer() {
        // The three-day defect: pid 52323 had been gone since the reboot, the file still named
        // port 22162, and "Sign in with AI Matrx" POSTed at nothing. Proven failing before the
        // rule: `live_discovery` did not exist and `base_url()` read the port regardless.
        let file = serde_json::json!({ "pid": 52323, "tcp_port": 22162 });
        assert!(live_discovery(Some(file.clone()), |_| false).is_none());
        assert_eq!(live_discovery(Some(file), |pid| pid == 52323), Some(serde_json::json!({ "pid": 52323, "tcp_port": 22162 })));
        // No file, and a file that proves no liveness, are the same answer.
        assert!(live_discovery(None, |_| true).is_none());
        assert!(live_discovery(Some(serde_json::json!({ "tcp_port": 22162 })), |_| true).is_none());
    }

    #[tokio::test]
    async fn every_way_the_daemon_can_be_down_has_its_own_reason_and_remedy() {
        struct Unrunnable;
        impl ReconcileOps for Unrunnable {
            async fn payload_version(&self, _: &Path) -> Result<String, String> {
                Err("bundled matrx-syncd --version was killed by signal 9".to_string())
            }
            async fn version(&self) -> Result<DaemonVersion, String> { unreachable!() }
            fn discovery_pid(&self) -> Option<u32> { None }
            fn discovery_present(&self) -> bool { false }
            fn process_alive(&self, _: u32) -> bool { false }
            async fn shutdown(&self) -> Result<serde_json::Value, String> { unreachable!() }
            async fn wait_for_shutdown(&self, _: u32, _: u64) -> bool { unreachable!() }
            fn spawn(&self, _: &Path, _: &str) -> Result<(), String> { unreachable!() }
            async fn verify(&self, _: &str, _: &str) -> bool { unreachable!() }
        }
        // (b) it cannot execute — the live macOS regression, carrying the signal.
        let cannot_execute = reconcile_with(&Unrunnable, Path::new("payload"), "dev")
            .await
            .expect_err("an unrunnable helper is not a success");
        assert_eq!(cannot_execute.code, "helper_cannot_execute");
        assert!(cannot_execute.state_reason.contains("killed by signal 9"));
        assert!(cannot_execute.remedy.contains("Update AI Matrx"));

        // (c) it started but never became ready.
        let mut never_ready = FakeOps::ready(Err("connection refused".to_string()));
        never_ready.verified = false;
        never_ready.discovery_present.set(false);
        let never_ready = reconcile_with(&never_ready, Path::new("payload"), "dev")
            .await
            .expect_err("an unverified spawn is not a success");
        assert_eq!(never_ready.code, "never_became_ready");
        assert!(never_ready.remedy.contains("Start sync"));

        // (d) it is running but unreachable — a live discovery file and no answer.
        let unreachable = FakeOps::ready(Err("connection refused".to_string()));
        let unreachable = reconcile_with(&unreachable, Path::new("payload"), "dev")
            .await
            .expect_err("an unreachable live daemon is not a success");
        assert_eq!(unreachable.code, "unreachable");
        assert!(unreachable.state_reason.contains("connection refused"));

        // An incompatible daemon is its own reason, never "unreachable".
        let mut wrong_world = FakeOps::ready(Ok(daemon("0.1.0")));
        wrong_world.version = Ok(DaemonVersion { world: "live".to_string(), ..daemon("0.1.0") });
        let incompatible = reconcile_with(&wrong_world, Path::new("payload"), "dev")
            .await
            .expect_err("a world mismatch is not a success");
        assert_eq!(incompatible.code, "incompatible");

        // An older daemon that will not step aside is not the same thing as one that never ran.
        let mut stalled = FakeOps::ready(Ok(daemon("0.1.0")));
        stalled.stopped = false;
        let blocked = reconcile_with(&stalled, Path::new("payload"), "dev")
            .await
            .expect_err("a stalled shutdown is not a success");
        assert_eq!(blocked.code, "upgrade_blocked");

        // (a) the helper is missing beside the build.
        assert_eq!(DaemonDown::helper_missing().code, "helper_missing");
        assert!(DaemonDown::helper_missing().remedy.contains("Reinstall"));

        // Every reason a person reads carries a remedy they can act on.
        for down in [cannot_execute, never_ready, unreachable, incompatible, blocked, DaemonDown::helper_missing()] {
            assert!(!down.state_reason.is_empty() && !down.remedy.is_empty());
            assert!(down.remedy.contains("Support"), "{} has no way to report it", down.code);
        }
    }

    #[test]
    fn a_killed_helper_says_which_signal_killed_it() {
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            assert_eq!(
                exit_detail(&std::process::ExitStatus::from_raw(9)),
                "was killed by signal 9"
            );
            assert_eq!(
                exit_detail(&std::process::ExitStatus::from_raw(1 << 8)),
                "exited with status 1"
            );
        }
    }

    #[test]
    fn one_lifecycle_line_per_distinct_reason() {
        // 4,400 identical lines in three days is the defect; the reason changing is the signal.
        *LAST_LOGGED_REASON.lock().expect("lock") = None;
        let seen = |reason: &str| {
            let mut last = LAST_LOGGED_REASON.lock().expect("lock");
            let repeat = last.as_deref() == Some(reason);
            *last = Some(reason.to_string());
            !repeat
        };
        assert!(seen("reason a"));
        assert!(!seen("reason a"));
        assert!(seen("reason b"));
        assert!(seen("reason a"));
    }

    #[test]
    fn replacement_requires_expected_version_world_and_protocol() {
        assert!(replacement_matches("1.4.141", "dev", &daemon("1.4.141")));
        assert!(!replacement_matches("1.4.141", "dev", &daemon("1.4.142")));
    }
}
