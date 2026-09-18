//! The relay — one WebSocket out to the gateway, N TCP streams back out to the internet.
//!
//! ```text
//!   AI Matrx gateway  ──wss://…/egress/device──▶  this helper  ──TCP──▶  the public internet
//! ```
//!
//! The socket is dialled OUT and held. Nothing listens; no port is opened; no router is touched
//! (contract rule 3). Every OPEN is judged by [`crate::policy`] before a single packet leaves, so
//! the socket cannot be used to reach the user's own network.
//!
//! ## What ends a session
//!
//! | Close code | What the helper does |
//! |---|---|
//! | `4401` | stops for good and says the computer was removed from the account |
//! | `4403` | shows "Paused from the web" and retries every 60 s |
//! | `4409` | stops: a newer connection for this computer won |
//! | anything else, or a dropped socket | reconnects with the jittered 1→60 s backoff |

use crate::backoff::Backoff;
use crate::frame::{Frame, FrameType, MAX_DATA_PAYLOAD};
use crate::policy;
use crate::status::{State, StatusHandle};
use crate::supervisor::Terminal;
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::Message;

/// The protocol version this build speaks — the `X-Matrx-Egress-Protocol` header and HELLO's
/// `protocol` field.
pub const PROTOCOL_VERSION: u32 = 1;

/// How long a TCP connect may take before the stream is refused with `timeout`.
pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);

/// The most bytes that may wait for one stream's TCP socket before the stream is closed
/// `overloaded`. The contract's per-stream cap.
pub const STREAM_BUFFER_CAP: usize = 4 * 1024 * 1024;

/// How many streams this helper offers to carry when the server names no number of its own.
pub const DEFAULT_MAX_STREAMS: usize = 64;

/// How long the helper waits to hear ANYTHING from the server before deciding the socket is dead.
/// The server pings every 20 s, so three missed pings is the signal.
pub const IDLE_TIMEOUT: Duration = Duration::from_secs(70);

/// How long a device paused from the web waits before trying again (contract: "retries every
/// 60 s").
pub const PAUSED_RETRY: Duration = Duration::from_secs(60);

/// The HELLO payload.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Hello {
    /// Always [`PROTOCOL_VERSION`].
    pub protocol: u32,
    /// This binary's version.
    pub helper_version: String,
    /// `darwin` / `windows` / `linux`.
    pub platform: String,
    /// This computer's hostname.
    pub hostname: String,
    /// `helper` or `desktop_app`.
    pub client_kind: String,
    /// How many concurrent streams this helper offers to carry.
    pub max_streams: usize,
}

/// The HELLO_ACK payload.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct HelloAck {
    /// `platform.egress_device.id`.
    #[serde(default)]
    pub device_id: Option<String>,
    /// The name the account shows for this computer.
    #[serde(default)]
    pub display_name: Option<String>,
    /// The server's ceiling, which wins over ours.
    #[serde(default)]
    pub max_streams: Option<usize>,
    /// How often the server will ping.
    #[serde(default)]
    pub ping_seconds: Option<u64>,
}

/// The OPEN payload.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Open {
    /// A name or a literal address.
    pub host: String,
    /// The TCP port.
    pub port: u16,
}

/// The OPENED payload.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Opened {
    /// Whether the stream is up.
    pub ok: bool,
    /// One of `dns` / `refused` / `timeout` / `policy` / `busy`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// A plain sentence.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

impl Opened {
    fn ok() -> Self {
        Opened {
            ok: true,
            error: None,
            message: None,
        }
    }

    fn failed(error: &str, message: impl Into<String>) -> Self {
        Opened {
            ok: false,
            error: Some(error.to_string()),
            message: Some(message.into()),
        }
    }
}

/// How one session ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionEnd {
    /// `4401` — this computer is no longer on the account.
    Removed,
    /// `4403` — switched off from the web.
    PausedFromWeb,
    /// `4409` — a newer connection for this computer took over.
    Replaced,
    /// Anything else: the socket went, with a sentence saying how.
    Lost(String),
    /// The helper itself decided to stop (pause from the tray, quit).
    StoppedLocally,
}

impl SessionEnd {
    /// Whether this ending is one that will not come back by itself — and which one.
    ///
    /// The supervisor parks on a `Some` instead of returning, so that every surface can go on
    /// answering honestly rather than offering controls that would do nothing.
    pub fn terminal(&self) -> Option<Terminal> {
        match self {
            SessionEnd::Removed => Some(Terminal::Removed),
            SessionEnd::Replaced => Some(Terminal::Replaced),
            _ => None,
        }
    }
}

/// Everything one session needs.
pub struct SessionConfig {
    /// `wss://…/egress/device`.
    pub socket_url: String,
    /// The full bearer `mxe_<device_id>_<secret>`.
    pub token: String,
    /// The HELLO this helper introduces itself with.
    pub hello: Hello,
}

/// Run one session: connect, HELLO, relay until the socket ends.
///
/// `on_ready` is called once HELLO_ACK has arrived — the supervisor uses it to reset the backoff,
/// because only a session that got this far counts as progress.
pub async fn run_session(
    config: &SessionConfig,
    status: Arc<StatusHandle>,
    mut stop: tokio::sync::watch::Receiver<bool>,
    on_ready: impl FnOnce(),
) -> SessionEnd {
    let request = match build_request(&config.socket_url, &config.token) {
        Ok(request) => request,
        Err(message) => return SessionEnd::Lost(message),
    };

    let (socket, _response) = match tokio_tungstenite::connect_async(request).await {
        Ok(pair) => pair,
        Err(e) => return SessionEnd::Lost(describe_connect_error(&e)),
    };
    let (mut sink, mut source) = socket.split();

    // One writer owns the sink. Every task that needs to send a frame sends it here, so frames are
    // never interleaved mid-message and no task can block another's write.
    let (outbound, mut outbound_rx) = mpsc::channel::<Message>(64);
    let writer = tokio::spawn(async move {
        while let Some(message) = outbound_rx.recv().await {
            if sink.send(message).await.is_err() {
                break;
            }
        }
        let _ = sink.close().await;
    });

    let hello = Frame::control(
        FrameType::Hello,
        serde_json::to_vec(&config.hello).unwrap_or_default(),
    );
    if outbound.send(Message::Binary(hello.encode().into())).await.is_err() {
        writer.abort();
        return SessionEnd::Lost("the connection to AI Matrx closed before it could start".into());
    }

    let mut streams: HashMap<u32, StreamHandle> = HashMap::new();
    let mut acknowledged = false;
    let mut max_streams = config.hello.max_streams;
    let mut on_ready = Some(on_ready);

    let end = loop {
        let next = tokio::time::timeout(IDLE_TIMEOUT, source.next());
        tokio::select! {
            changed = stop.changed() => {
                // A local pause or a quit. Close politely; the server sees a normal close.
                if changed.is_err() || *stop.borrow() {
                    break SessionEnd::StoppedLocally;
                }
                continue;
            }
            message = next => {
                let message = match message {
                    Err(_) => break SessionEnd::Lost(
                        "AI Matrx stopped answering this computer, so the connection was restarted"
                            .into(),
                    ),
                    Ok(None) => break SessionEnd::Lost(
                        "the connection to AI Matrx closed".into(),
                    ),
                    Ok(Some(Err(e))) => break SessionEnd::Lost(describe_connect_error(&e)),
                    Ok(Some(Ok(message))) => message,
                };

                match message {
                    Message::Binary(bytes) => {
                        match Frame::decode(&bytes) {
                            Ok(frame) => {
                                if let Some(end) = handle_frame(
                                    frame,
                                    &outbound,
                                    &status,
                                    &mut streams,
                                    &mut acknowledged,
                                    &mut max_streams,
                                    &mut on_ready,
                                )
                                .await
                                {
                                    break end;
                                }
                            }
                            Err(e) => {
                                break SessionEnd::Lost(format!(
                                    "AI Matrx sent something this version of the Home Connection \
                                     cannot read ({e}), so the connection was restarted"
                                ));
                            }
                        }
                    }
                    Message::Close(frame) => break close_end(frame.as_ref()),
                    Message::Ping(payload) => {
                        // The WebSocket's own ping, which is not the protocol's PING frame.
                        let _ = outbound.send(Message::Pong(payload)).await;
                    }
                    Message::Text(_) | Message::Pong(_) | Message::Frame(_) => {}
                }
            }
        }
    };

    for (_, handle) in streams.drain() {
        handle.shutdown();
    }
    status.clear_active_streams();
    drop(outbound);
    let _ = tokio::time::timeout(Duration::from_secs(2), writer).await;
    end
}

/// What one open stream needs, from the reader loop's side.
struct StreamHandle {
    inbound: mpsc::Sender<Vec<u8>>,
    pending: Arc<AtomicUsize>,
    task: tokio::task::JoinHandle<()>,
}

impl StreamHandle {
    fn shutdown(self) {
        drop(self.inbound);
        self.task.abort();
    }
}

#[allow(clippy::too_many_arguments)]
async fn handle_frame(
    frame: Frame,
    outbound: &mpsc::Sender<Message>,
    status: &Arc<StatusHandle>,
    streams: &mut HashMap<u32, StreamHandle>,
    acknowledged: &mut bool,
    max_streams: &mut usize,
    on_ready: &mut Option<impl FnOnce()>,
) -> Option<SessionEnd> {
    match frame.kind {
        FrameType::HelloAck => {
            let ack: HelloAck = frame.json().unwrap_or(HelloAck {
                device_id: None,
                display_name: None,
                max_streams: None,
                ping_seconds: None,
            });
            if let Some(name) = ack.display_name.clone() {
                status.set_device_name(name);
            }
            if let Some(limit) = ack.max_streams {
                // The server's ceiling wins, in both directions: it knows the account's knob.
                *max_streams = limit;
            }
            *acknowledged = true;
            status.set_state(State::Connected);
            if let Some(ready) = on_ready.take() {
                ready();
            }
            None
        }
        FrameType::Ping => {
            let pong = Frame::control(FrameType::Pong, Vec::new());
            let _ = outbound.send(Message::Binary(pong.encode().into())).await;
            None
        }
        FrameType::Open => {
            let stream_id = frame.stream_id;
            let open: Open = match frame.json() {
                Ok(open) => open,
                Err(e) => {
                    send_opened(
                        outbound,
                        stream_id,
                        Opened::failed("policy", format!("that request could not be read: {e}")),
                    )
                    .await;
                    return None;
                }
            };
            if streams.len() >= *max_streams {
                send_opened(
                    outbound,
                    stream_id,
                    Opened::failed(
                        "busy",
                        "this computer is already carrying as many connections as it is set to",
                    ),
                )
                .await;
                return None;
            }
            if streams.contains_key(&stream_id) {
                // The server reused a live id. Refusing is the only safe answer: adopting it would
                // cross two callers' traffic.
                send_opened(
                    outbound,
                    stream_id,
                    Opened::failed("busy", "that connection number is already in use"),
                )
                .await;
                return None;
            }
            let (inbound_tx, inbound_rx) = mpsc::channel::<Vec<u8>>(64);
            let pending = Arc::new(AtomicUsize::new(0));
            let task = tokio::spawn(relay_stream(
                stream_id,
                open,
                inbound_rx,
                Arc::clone(&pending),
                outbound.clone(),
                Arc::clone(status),
            ));
            streams.insert(
                stream_id,
                StreamHandle {
                    inbound: inbound_tx,
                    pending,
                    task,
                },
            );
            None
        }
        FrameType::Data => {
            let Some(handle) = streams.get(&frame.stream_id) else {
                // A stream we already closed. Silently dropping is right: CLOSE crossed in flight.
                return None;
            };
            let len = frame.payload.len();
            let pending = handle.pending.fetch_add(len, Ordering::SeqCst) + len;
            if pending > STREAM_BUFFER_CAP || handle.inbound.try_send(frame.payload).is_err() {
                // Never buffer without bound: the contract's 4 MiB cap, then CLOSE overloaded.
                handle.pending.fetch_sub(len.min(pending), Ordering::SeqCst);
                if let Some(handle) = streams.remove(&frame.stream_id) {
                    handle.shutdown();
                    status.stream_closed();
                }
                send_close(outbound, frame.stream_id, "overloaded").await;
            }
            None
        }
        FrameType::Close => {
            if let Some(handle) = streams.remove(&frame.stream_id) {
                handle.shutdown();
                status.stream_closed();
            }
            None
        }
        FrameType::Hello | FrameType::Opened | FrameType::Pong => {
            // Device→server types arriving from the server. Nothing to do, and nothing to panic
            // about: a newer gateway may say more than this build understands.
            None
        }
    }
}

async fn send_opened(outbound: &mpsc::Sender<Message>, stream_id: u32, opened: Opened) {
    let frame = Frame::on(
        FrameType::Opened,
        stream_id,
        serde_json::to_vec(&opened).unwrap_or_default(),
    );
    let _ = outbound.send(Message::Binary(frame.encode().into())).await;
}

async fn send_close(outbound: &mpsc::Sender<Message>, stream_id: u32, reason: &str) {
    let payload = serde_json::to_vec(&serde_json::json!({ "reason": reason })).unwrap_or_default();
    let frame = Frame::on(FrameType::Close, stream_id, payload);
    let _ = outbound.send(Message::Binary(frame.encode().into())).await;
}

/// One stream: resolve, judge, connect, then pipe both ways until either end stops.
async fn relay_stream(
    stream_id: u32,
    open: Open,
    mut inbound: mpsc::Receiver<Vec<u8>>,
    pending: Arc<AtomicUsize>,
    outbound: mpsc::Sender<Message>,
    status: Arc<StatusHandle>,
) {
    let addrs = match resolve(&open).await {
        Ok(addrs) => addrs,
        Err(message) => {
            send_opened(&outbound, stream_id, Opened::failed("dns", message)).await;
            return;
        }
    };
    if let Err(refusal) = policy::judge_all(&addrs) {
        send_opened(
            &outbound,
            stream_id,
            Opened::failed("policy", refusal.message()),
        )
        .await;
        return;
    }

    // Dial the addresses this policy approved — never a fresh resolution, so nothing can change
    // between the check and the connection.
    let targets: Vec<std::net::SocketAddr> = addrs
        .into_iter()
        .map(|ip| std::net::SocketAddr::new(ip, open.port))
        .collect();
    let socket = match connect_any(&targets).await {
        Ok(socket) => socket,
        Err((error, message)) => {
            send_opened(&outbound, stream_id, Opened::failed(error, message)).await;
            return;
        }
    };
    let _ = socket.set_nodelay(true);

    send_opened(&outbound, stream_id, Opened::ok()).await;
    status.stream_opened();

    let (mut reader, mut writer) = socket.into_split();
    let mut buffer = vec![0u8; MAX_DATA_PAYLOAD];
    let reason = loop {
        tokio::select! {
            read = reader.read(&mut buffer) => match read {
                Ok(0) => break "closed",
                Ok(n) => {
                    let frame = Frame::on(FrameType::Data, stream_id, buffer[..n].to_vec());
                    // `send` rather than `try_send`: when the WebSocket cannot keep up, the right
                    // answer is to stop reading this TCP socket, which pushes back on the far end
                    // rather than growing a buffer here.
                    if outbound.send(Message::Binary(frame.encode().into())).await.is_err() {
                        return;
                    }
                    status.add_bytes(n as u64);
                }
                Err(_) => break "closed",
            },
            chunk = inbound.recv() => match chunk {
                Some(chunk) => {
                    pending.fetch_sub(chunk.len().min(pending.load(Ordering::SeqCst)), Ordering::SeqCst);
                    let len = chunk.len() as u64;
                    if writer.write_all(&chunk).await.is_err() {
                        break "closed";
                    }
                    status.add_bytes(len);
                }
                None => return,
            },
        }
    };

    let _ = writer.shutdown().await;
    status.stream_closed();
    send_close(&outbound, stream_id, reason).await;
}

/// Resolve a host to every address it answers with.
async fn resolve(open: &Open) -> Result<Vec<IpAddr>, String> {
    // A literal address needs no lookup — and must not be handed to the resolver, which on some
    // systems will happily "resolve" a bare number through a search domain.
    if let Ok(ip) = open.host.parse::<IpAddr>() {
        return Ok(vec![ip]);
    }
    match tokio::net::lookup_host((open.host.as_str(), open.port)).await {
        Ok(addrs) => {
            let list: Vec<IpAddr> = addrs.map(|addr| addr.ip()).collect();
            if list.is_empty() {
                Err(format!("{} has no address on the internet", open.host))
            } else {
                Ok(list)
            }
        }
        Err(e) => Err(format!("{} could not be looked up: {e}", open.host)),
    }
}

/// Connect to the first address that answers, within the 15 s budget for the whole attempt.
async fn connect_any(
    targets: &[std::net::SocketAddr],
) -> Result<TcpStream, (&'static str, String)> {
    let deadline = tokio::time::Instant::now() + CONNECT_TIMEOUT;
    let mut last: Option<String> = None;
    for target in targets {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        match tokio::time::timeout(remaining, TcpStream::connect(target)).await {
            Ok(Ok(socket)) => return Ok(socket),
            Ok(Err(e)) => last = Some(e.to_string()),
            Err(_) => {
                return Err((
                    "timeout",
                    "that site did not answer this computer within 15 seconds".to_string(),
                ))
            }
        }
    }
    Err((
        "refused",
        match last {
            Some(cause) => format!("that site refused this computer's connection: {cause}"),
            None => "that site refused this computer's connection".to_string(),
        },
    ))
}

/// The two headers the contract fixes, on a request tungstenite will accept.
fn build_request(
    url: &str,
    token: &str,
) -> Result<tokio_tungstenite::tungstenite::handshake::client::Request, String> {
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    let mut request = url
        .into_client_request()
        .map_err(|e| format!("{url} is not an address this computer can connect to: {e}"))?;
    let headers = request.headers_mut();
    headers.insert(
        "Authorization",
        format!("Bearer {token}")
            .parse()
            .map_err(|_| "this computer's saved connection is not usable".to_string())?,
    );
    headers.insert(
        "X-Matrx-Egress-Protocol",
        PROTOCOL_VERSION
            .to_string()
            .parse()
            .map_err(|_| "the protocol version could not be sent".to_string())?,
    );
    Ok(request)
}

/// Map a close frame to an outcome. The four codes the contract names, and everything else.
fn close_end(frame: Option<&CloseFrame>) -> SessionEnd {
    let code = frame.map(|f| u16::from(f.code)).unwrap_or(1000);
    match code {
        4401 => SessionEnd::Removed,
        4403 => SessionEnd::PausedFromWeb,
        4409 => SessionEnd::Replaced,
        4429 => SessionEnd::Lost(
            "AI Matrx is carrying as many connections as this computer is set to, so it paused \
             briefly"
                .into(),
        ),
        _ => {
            let reason = frame
                .map(|f| f.reason.to_string())
                .filter(|r| !r.is_empty())
                .unwrap_or_else(|| "the connection to AI Matrx closed".to_string());
            SessionEnd::Lost(reason)
        }
    }
}

fn describe_connect_error(e: &tokio_tungstenite::tungstenite::Error) -> String {
    use tokio_tungstenite::tungstenite::Error;
    match e {
        Error::Http(response) => format!(
            "AI Matrx refused this computer's connection ({})",
            response.status()
        ),
        Error::Io(io) => format!("this computer could not reach AI Matrx: {io}"),
        other => format!("the connection to AI Matrx failed: {other}"),
    }
}

/// What the supervisor decided to do after a session ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AfterSession {
    /// Reconnect after this long.
    RetryAfter(Duration),
    /// Stop for good; the status already says why.
    Stop,
}

/// The one place a session outcome becomes a status and a next step.
///
/// Extracted from the supervisor loop so it can be asserted without a socket: this is the table
/// the contract's close-code list describes, and it is the part that is easy to get subtly wrong.
pub fn after_session(
    end: &SessionEnd,
    status: &StatusHandle,
    backoff: &mut Backoff,
) -> AfterSession {
    match end {
        SessionEnd::Removed | SessionEnd::Replaced => {
            // One place spells these two out: `Terminal`, which the supervisor and the menu also
            // read, so the tray can never say something the status file does not.
            let terminal = end.terminal().expect("both arms are terminal");
            status.set_error(terminal.state(), terminal.sentence(), terminal.remedy());
            AfterSession::Stop
        }
        SessionEnd::PausedFromWeb => {
            status.set_error(
                State::Paused,
                "Paused from the web",
                "Turn this computer's home connection back on in AI Matrx.",
            );
            AfterSession::RetryAfter(PAUSED_RETRY)
        }
        SessionEnd::StoppedLocally => AfterSession::Stop,
        SessionEnd::Lost(reason) => {
            let delay = backoff.next_delay();
            // One log line per reconnect, naming the attempt: a helper that quietly retries for an
            // hour and a helper that is wedged look identical without it.
            eprintln!(
                "[egress] {reason} — trying again in {}s (attempt {})",
                delay.as_secs().max(1),
                backoff.attempt()
            );
            status.set_error(
                State::Connecting,
                reason.clone(),
                format!(
                    "Nothing to do — it will try again in about {} seconds.",
                    delay.as_secs().max(1)
                ),
            );
            AfterSession::RetryAfter(delay)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::status::StatusHandle;

    fn status() -> StatusHandle {
        StatusHandle::new("https://example.test".into(), None, false)
    }

    #[test]
    fn the_handshake_carries_exactly_the_two_headers_the_contract_names() {
        let request = build_request(
            "wss://server.app.matrxserver.com/egress/device",
            "mxe_d-1_secret",
        )
        .expect("request");
        assert_eq!(
            request.headers().get("Authorization").expect("auth"),
            "Bearer mxe_d-1_secret"
        );
        assert_eq!(
            request
                .headers()
                .get("X-Matrx-Egress-Protocol")
                .expect("protocol"),
            "1"
        );
        assert_eq!(request.uri().path(), "/egress/device");
    }

    #[test]
    fn a_removed_device_stops_for_good_with_the_contracts_sentence() {
        let status = status();
        let mut backoff = Backoff::new();
        let next = after_session(&SessionEnd::Removed, &status, &mut backoff);
        assert_eq!(next, AfterSession::Stop);
        let snapshot = status.snapshot();
        assert_eq!(snapshot.state, State::SignedOut);
        assert_eq!(
            snapshot.last_error.as_deref(),
            Some("This computer was removed from your account — run Connect again to add it back.")
        );
        assert!(snapshot.remedy.is_some());
    }

    #[test]
    fn a_device_paused_from_the_web_says_so_and_retries_in_a_minute() {
        let status = status();
        let mut backoff = Backoff::new();
        let next = after_session(&SessionEnd::PausedFromWeb, &status, &mut backoff);
        assert_eq!(next, AfterSession::RetryAfter(PAUSED_RETRY));
        let snapshot = status.snapshot();
        assert_eq!(snapshot.state, State::Paused);
        assert_eq!(snapshot.last_error.as_deref(), Some("Paused from the web"));
        // The 60 s retry is a fixed cadence, not the backoff — the backoff must not have moved.
        assert_eq!(backoff.attempt(), 0);
    }

    #[test]
    fn a_replaced_connection_stops_without_reconnecting() {
        let status = status();
        let mut backoff = Backoff::new();
        assert_eq!(
            after_session(&SessionEnd::Replaced, &status, &mut backoff),
            AfterSession::Stop
        );
        // Replaced is NOT signed out: the account still lists this computer, another copy simply
        // holds its connection. The two must not wear the same title in the menu.
        assert_eq!(status.snapshot().state, State::Error);
        assert_eq!(
            after_session(&SessionEnd::Replaced, &status, &mut backoff)
                .eq(&AfterSession::Stop)
                .then(|| SessionEnd::Replaced.terminal())
                .flatten(),
            Some(crate::supervisor::Terminal::Replaced)
        );
    }

    #[test]
    fn a_lost_socket_backs_off_and_keeps_showing_connecting() {
        let status = status();
        let mut backoff = Backoff::new();
        let mut previous = Duration::ZERO;
        for _ in 0..4 {
            match after_session(
                &SessionEnd::Lost("the connection to AI Matrx closed".into()),
                &status,
                &mut backoff,
            ) {
                AfterSession::RetryAfter(delay) => {
                    assert!(delay >= crate::backoff::MIN_DELAY);
                    assert!(delay <= crate::backoff::MAX_DELAY);
                    previous = delay;
                }
                other => panic!("a lost socket returned {other:?}"),
            }
        }
        assert!(previous >= Duration::from_secs(6), "{previous:?}");
        let snapshot = status.snapshot();
        assert_eq!(snapshot.state, State::Connecting);
        assert!(snapshot.remedy.expect("remedy").contains("try again"));
    }

    #[test]
    fn every_close_code_the_contract_names_maps_to_its_outcome() {
        use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
        let cases = [
            (4401u16, SessionEnd::Removed),
            (4403, SessionEnd::PausedFromWeb),
            (4409, SessionEnd::Replaced),
        ];
        for (code, expected) in cases {
            let frame = CloseFrame {
                code: CloseCode::from(code),
                reason: "".into(),
            };
            assert_eq!(close_end(Some(&frame)), expected, "code {code}");
        }
        // 4429 and a plain close both mean "try again", never "stop".
        for code in [4429u16, 1000, 1006, 1011] {
            let frame = CloseFrame {
                code: CloseCode::from(code),
                reason: "".into(),
            };
            assert!(
                matches!(close_end(Some(&frame)), SessionEnd::Lost(_)),
                "code {code}"
            );
        }
        assert!(matches!(close_end(None), SessionEnd::Lost(_)));
    }

    #[tokio::test]
    async fn a_literal_address_is_never_sent_through_the_resolver() {
        let addrs = resolve(&Open {
            host: "93.184.216.34".into(),
            port: 443,
        })
        .await
        .expect("literal");
        assert_eq!(addrs, vec!["93.184.216.34".parse::<IpAddr>().expect("ip")]);
    }

    #[tokio::test]
    async fn a_name_that_does_not_exist_is_a_dns_failure_not_a_policy_one() {
        let error = resolve(&Open {
            host: "this-name-does-not-exist.matrx-egress-test.invalid".into(),
            port: 443,
        })
        .await
        .expect_err("no such name");
        assert!(error.contains("this-name-does-not-exist"), "{error}");
    }

    #[tokio::test]
    async fn localhost_resolves_and_is_then_refused_by_the_policy() {
        // The two halves in order: resolution succeeds, the judgement refuses. This is the shape
        // that keeps the user's own network out of reach.
        let addrs = resolve(&Open {
            host: "localhost".into(),
            port: 80,
        })
        .await
        .expect("localhost resolves");
        assert!(!addrs.is_empty());
        assert!(policy::judge_all(&addrs).is_err(), "{addrs:?} was allowed");
    }
}
