//! A local stand-in for the AI Matrx gateway's device leg, for driving the helper by hand.
//!
//! On ONE port it serves both halves the helper talks to:
//!
//! * `ws://127.0.0.1:<port>/egress/device` — answers HELLO with HELLO_ACK, pings every 20 seconds
//!   as the real gateway does, and — with `--open <host:port>` — opens one stream and sends a
//!   plain HTTP request down it so you can watch bytes move.
//! * `POST /egress/pairings`, `GET /egress/pairings/{id}`, `PATCH`/`DELETE /egress/devices/{id}` —
//!   a pairing that approves itself immediately, so `matrx-egress pair --server
//!   http://127.0.0.1:<port>` completes and the helper's own binary writes the keychain item.
//!   (It must be the helper that writes it: macOS binds an item's ACL to the process that created
//!   it, so an item seeded with `security add-generic-password` makes the helper prompt.)
//!
//! A request is routed by peeking at its first bytes — `peek` reads without consuming, so the
//! WebSocket handshake still sees the whole request.
//!
//! ```text
//! cargo run -p matrx-egress --example fake-gateway -- --port 8099
//! cargo run -p matrx-egress --example fake-gateway -- --port 8099 --open api.ipify.org:80
//! ```
//!
//! It is a test harness, never shipped: `examples/` is not part of the binary. The automated
//! proof that bytes cross correctly lives in `src/fake_gateway.rs` and runs a real TLS client
//! through the relay; this exists so a person can start the real helper and look at it.

use futures_util::{SinkExt, StreamExt};
use std::time::Duration;
use tokio::net::TcpListener;
use tokio_tungstenite::tungstenite::Message;

/// `[u8 type][u32 BE stream_id][payload]` — the contract's framing, restated here so the harness
/// does not depend on the crate's internals.
fn frame(kind: u8, stream_id: u32, payload: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(5 + payload.len());
    out.push(kind);
    out.extend_from_slice(&stream_id.to_be_bytes());
    out.extend_from_slice(payload);
    out
}

fn parse(bytes: &[u8]) -> Option<(u8, u32, &[u8])> {
    if bytes.len() < 5 {
        return None;
    }
    Some((
        bytes[0],
        u32::from_be_bytes([bytes[1], bytes[2], bytes[3], bytes[4]]),
        &bytes[5..],
    ))
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut port = 8099u16;
    let mut open_target: Option<String> = None;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--port" => {
                index += 1;
                port = args
                    .get(index)
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(8099);
            }
            "--open" => {
                index += 1;
                open_target = args.get(index).cloned();
            }
            other => {
                eprintln!("fake-gateway: unknown argument {other:?}");
                std::process::exit(2);
            }
        }
        index += 1;
    }

    let listener = TcpListener::bind(("127.0.0.1", port))
        .await
        .unwrap_or_else(|e| panic!("could not bind 127.0.0.1:{port}: {e}"));
    println!("fake gateway listening on ws://127.0.0.1:{port}/egress/device");
    println!("point the helper at it with:  --server http://127.0.0.1:{port}");

    loop {
        let Ok((stream, peer)) = listener.accept().await else {
            continue;
        };
        let open_target = open_target.clone();
        tokio::spawn(async move {
            let mut head = [0u8; 1024];
            let peeked = stream.peek(&mut head).await.unwrap_or(0);
            let head = String::from_utf8_lossy(&head[..peeked]).to_lowercase();
            if !head.contains("upgrade: websocket") {
                serve_http(stream, &head).await;
                return;
            }
            let socket = match tokio_tungstenite::accept_async(stream).await {
                Ok(socket) => socket,
                Err(e) => {
                    eprintln!("[{peer}] handshake failed: {e}");
                    return;
                }
            };
            println!("[{peer}] connected");
            let (mut sink, mut source) = socket.split();

            while let Some(Ok(message)) = source.next().await {
                let Message::Binary(bytes) = message else {
                    continue;
                };
                let Some((kind, stream_id, payload)) = parse(&bytes) else {
                    continue;
                };
                match kind {
                    0x10 => {
                        println!(
                            "[{peer}] HELLO {}",
                            String::from_utf8_lossy(payload)
                        );
                        let ack = serde_json::json!({
                            "device_id": "d-local",
                            "display_name": "This computer (local test)",
                            "max_streams": 8,
                            "ping_seconds": 20,
                        });
                        let _ = sink
                            .send(Message::Binary(
                                frame(0x11, 0, ack.to_string().as_bytes()).into(),
                            ))
                            .await;

                        if let Some(target) = open_target.clone() {
                            let (host, port) = target.split_once(':').unwrap_or((&target, "80"));
                            let open = serde_json::json!({
                                "host": host,
                                "port": port.parse::<u16>().unwrap_or(80),
                            });
                            println!("[{peer}] OPEN {open}");
                            let _ = sink
                                .send(Message::Binary(
                                    frame(0x01, 1, open.to_string().as_bytes()).into(),
                                ))
                                .await;
                            let request = format!(
                                "GET /?format=text HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
                            );
                            let _ = sink
                                .send(Message::Binary(
                                    frame(0x02, 1, request.as_bytes()).into(),
                                ))
                                .await;
                        }

                        // Ping on the contract's cadence, from its own task.
                        let mut pinger = sink;
                        tokio::spawn(async move {
                            loop {
                                tokio::time::sleep(Duration::from_secs(20)).await;
                                if pinger
                                    .send(Message::Binary(frame(0x04, 0, &[]).into()))
                                    .await
                                    .is_err()
                                {
                                    return;
                                }
                            }
                        });
                        // The rest of the session is read-only for this harness.
                        while let Some(Ok(message)) = source.next().await {
                            if let Message::Binary(bytes) = message {
                                if let Some((kind, stream_id, payload)) = parse(&bytes) {
                                    describe(&peer.to_string(), kind, stream_id, payload);
                                }
                            }
                        }
                        println!("[{peer}] disconnected");
                        return;
                    }
                    _ => describe(&peer.to_string(), kind, stream_id, payload),
                }
            }
            println!("[{peer}] disconnected");
        });
    }
}

/// The pairing and device routes, answered the way the contract says the gateway answers them —
/// except that a pairing approves itself at once, because there is nobody here to click Connect.
async fn serve_http(mut stream: tokio::net::TcpStream, head: &str) {
    use tokio::io::AsyncWriteExt as _;
    let first_line = head.lines().next().unwrap_or_default().to_string();
    println!("[http] {first_line}");

    let body = if first_line.starts_with("post /egress/pairings") {
        serde_json::json!({
            "pairing_id": "p-local",
            "pairing_secret": "s-local",
            "user_code": "TEST-2345",
            "connect_url": "http://127.0.0.1/connect-computer?code=TEST-2345",
            "expires_at": "2099-01-01T00:00:00Z",
            "poll_seconds": 1,
        })
    } else if first_line.starts_with("get /egress/pairings/") {
        serde_json::json!({
            "status": "approved",
            "device_id": "d-local",
            "display_name": "This computer (local test)",
            "device_token": "mxe_d-local_localtestsecret",
        })
    } else if first_line.starts_with("patch /egress/devices/")
        || first_line.starts_with("delete /egress/devices/")
    {
        serde_json::json!({ "ok": true })
    } else {
        serde_json::json!({
            "error": { "message": "this fake gateway does not serve that" }
        })
    };
    let body = body.to_string();
    let response = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\
         Connection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes()).await;
    let _ = stream.flush().await;
    let _ = stream.shutdown().await;
}

fn describe(peer: &str, kind: u8, stream_id: u32, payload: &[u8]) {
    match kind {
        0x05 => println!("[{peer}] PONG"),
        0x12 => println!(
            "[{peer}] OPENED stream {stream_id}: {}",
            String::from_utf8_lossy(payload)
        ),
        0x02 => println!("[{peer}] DATA stream {stream_id}: {} bytes", payload.len()),
        0x03 => println!(
            "[{peer}] CLOSE stream {stream_id}: {}",
            String::from_utf8_lossy(payload)
        ),
        other => println!("[{peer}] frame 0x{other:02x} on stream {stream_id}"),
    }
}
