//! A fake gateway, and the one test that proves bytes really move.
//!
//! Everything else in this crate is a unit test: framing, the address policy, the status document,
//! the backoff. None of them proves the thing the helper exists to do — that a request handed to
//! this computer over the WebSocket comes back with the answer the public internet gave.
//!
//! So this module stands up a real WebSocket server speaking the contract's device protocol, runs
//! the real [`crate::relay::run_session`] against it, and drives a **real TLS client** through the
//! relayed stream to `api.ipify.org`. A TLS handshake is the strictest check available: one byte
//! reordered, dropped or duplicated in either direction and it fails outright. The answer is this
//! Mac's own public address, which the test compares against the same site fetched directly.
//!
//! It reaches the public internet, so it is `#[ignore]` and runs on request:
//!
//! ```text
//! cargo test -p matrx-egress -- --ignored --nocapture
//! ```

use crate::frame::{Frame, FrameType};
use crate::relay::{self, Hello, HelloAck, Opened, SessionConfig};
use crate::status::{State, StatusHandle};
use futures_util::{SinkExt, StreamExt};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio_tungstenite::tungstenite::Message;

/// What the gateway observed, for the assertions at the end.
#[derive(Debug, Default)]
struct Observed {
    hello: Option<Hello>,
    opened: Option<Opened>,
    bytes_to_device: usize,
    bytes_from_device: usize,
}

/// The site the relayed stream talks to. Chosen because its answer IS the evidence: the public
/// address the far end saw.
const TARGET_HOST: &str = "api.ipify.org";
const TARGET_PORT: u16 = 443;

#[tokio::test]
#[ignore = "reaches the public internet; run with --ignored"]
async fn a_real_tls_request_travels_through_the_relay_and_comes_back() {
    let _ = rustls::crypto::ring::default_provider().install_default();

    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let port = listener.local_addr().expect("addr").port();

    // ── the fake gateway ────────────────────────────────────────────────────────────────────
    let gateway = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.expect("accept");
        let socket = tokio_tungstenite::accept_async(stream)
            .await
            .expect("websocket handshake");
        let (mut sink, mut source) = socket.split();
        let mut observed = Observed::default();

        // HELLO must be the first frame on the connection.
        let first = next_frame(&mut source).await.expect("HELLO");
        assert_eq!(first.kind, FrameType::Hello, "the first frame was not HELLO");
        assert_eq!(first.stream_id, 0, "HELLO was not on the control stream");
        observed.hello = Some(first.json().expect("HELLO is JSON"));

        let ack = HelloAck {
            device_id: Some("d-fake".into()),
            display_name: Some("The test Mac".into()),
            max_streams: Some(8),
            ping_seconds: Some(20),
        };
        send(
            &mut sink,
            Frame::control(
                FrameType::HelloAck,
                serde_json::to_vec(&ack).expect("ack json"),
            ),
        )
        .await;

        // A PING, answered within the contract's five seconds.
        send(&mut sink, Frame::control(FrameType::Ping, Vec::new())).await;

        // OPEN one stream to a real public site.
        let stream_id = 1u32;
        send(
            &mut sink,
            Frame::on(
                FrameType::Open,
                stream_id,
                serde_json::to_vec(&serde_json::json!({
                    "host": TARGET_HOST,
                    "port": TARGET_PORT,
                }))
                .expect("open json"),
            ),
        )
        .await;

        // The device's answers, until OPENED.
        let mut saw_pong = false;
        loop {
            let frame = next_frame(&mut source).await.expect("a frame after OPEN");
            match frame.kind {
                FrameType::Pong => saw_pong = true,
                FrameType::Opened => {
                    observed.opened = Some(frame.json().expect("OPENED is JSON"));
                    break;
                }
                other => panic!("unexpected {other:?} before OPENED"),
            }
        }
        assert!(saw_pong, "the device never answered the PING");
        let opened = observed.opened.clone().expect("OPENED");
        assert!(opened.ok, "the device refused the stream: {opened:?}");

        // ── pipe a real TLS client through the relayed stream ───────────────────────────────
        // One half of the duplex is the TLS client's socket; the other half is fed by, and feeds,
        // DATA frames. As far as rustls is concerned this is an ordinary TCP connection.
        let (tls_side, mut frame_side) = tokio::io::duplex(256 * 1024);

        let request = tokio::spawn(async move {
            let roots = rustls::RootCertStore {
                roots: webpki_roots::TLS_SERVER_ROOTS.to_vec(),
            };
            let config = rustls::ClientConfig::builder()
                .with_root_certificates(roots)
                .with_no_client_auth();
            let connector = tokio_rustls::TlsConnector::from(Arc::new(config));
            let name = rustls::pki_types::ServerName::try_from(TARGET_HOST).expect("server name");
            let mut tls = connector
                .connect(name, tls_side)
                .await
                .expect("the TLS handshake went through the relay");
            tls.write_all(
                format!(
                    "GET /?format=text HTTP/1.1\r\nHost: {TARGET_HOST}\r\n\
                     User-Agent: matrx-egress-test\r\nConnection: close\r\n\r\n"
                )
                .as_bytes(),
            )
            .await
            .expect("write the request");
            tls.flush().await.expect("flush");
            let mut answer = Vec::new();
            // `read_to_end` ends when the far side closes, which `Connection: close` guarantees.
            let _ = tls.read_to_end(&mut answer).await;
            String::from_utf8_lossy(&answer).into_owned()
        });

        // Pump: duplex → DATA frames, and DATA frames → duplex, until the stream ends.
        let mut buffer = vec![0u8; 16 * 1024];
        loop {
            tokio::select! {
                read = frame_side.read(&mut buffer) => match read {
                    Ok(0) | Err(_) => {
                        send(&mut sink, Frame::on(FrameType::Close, stream_id, Vec::new())).await;
                        break;
                    }
                    Ok(n) => {
                        observed.bytes_to_device += n;
                        send(
                            &mut sink,
                            Frame::on(FrameType::Data, stream_id, buffer[..n].to_vec()),
                        )
                        .await;
                    }
                },
                frame = next_frame(&mut source) => match frame {
                    Some(frame) if frame.kind == FrameType::Data => {
                        observed.bytes_from_device += frame.payload.len();
                        if frame_side.write_all(&frame.payload).await.is_err() {
                            break;
                        }
                    }
                    Some(frame) if frame.kind == FrameType::Close => {
                        let _ = frame_side.shutdown().await;
                        break;
                    }
                    Some(_) => continue,
                    None => break,
                },
            }
        }
        drop(frame_side);

        let answer = request.await.expect("the TLS client finished");
        (observed, answer)
    });

    // ── the real helper, against that gateway ───────────────────────────────────────────────
    let status = Arc::new(StatusHandle::new(
        format!("http://127.0.0.1:{port}"),
        None,
        false,
    ));
    let (stop_tx, stop_rx) = tokio::sync::watch::channel(false);
    let config = SessionConfig {
        socket_url: format!("ws://127.0.0.1:{port}/egress/device"),
        token: "mxe_d-fake_secret".into(),
        hello: Hello {
            protocol: relay::PROTOCOL_VERSION,
            helper_version: crate::VERSION.into(),
            platform: crate::identity::platform_word().into(),
            hostname: crate::identity::hostname(),
            client_kind: "helper".into(),
            max_streams: relay::DEFAULT_MAX_STREAMS,
        },
    };
    let session_status = Arc::clone(&status);
    let session = tokio::spawn(async move {
        relay::run_session(&config, session_status, stop_rx, || {}).await
    });

    let (observed, answer) = tokio::time::timeout(Duration::from_secs(60), gateway)
        .await
        .expect("the gateway finished within a minute")
        .expect("the gateway task did not panic");

    // What the site said, through this computer.
    let through_relay = answer
        .rsplit("\r\n\r\n")
        .next()
        .unwrap_or_default()
        .trim()
        .to_string();
    // …and the same site, fetched the ordinary way from this same machine.
    let directly = reqwest::get(format!("https://{TARGET_HOST}/?format=text"))
        .await
        .expect("a direct fetch")
        .text()
        .await
        .expect("a direct answer");

    println!("--- proof ---");
    println!("HELLO from the helper : {:?}", observed.hello);
    println!("OPENED                : {:?}", observed.opened);
    println!("bytes gateway→device  : {}", observed.bytes_to_device);
    println!("bytes device→gateway  : {}", observed.bytes_from_device);
    println!("public address through the relay : {through_relay}");
    println!("public address fetched directly  : {}", directly.trim());
    println!("status                : {}", status.snapshot().to_line());

    assert!(answer.starts_with("HTTP/1.1 200"), "answer was: {answer}");
    assert_eq!(
        through_relay,
        directly.trim(),
        "the site saw a different computer through the relay than it does directly"
    );
    assert!(observed.bytes_to_device > 0 && observed.bytes_from_device > 0);
    let hello = observed.hello.expect("HELLO");
    assert_eq!(hello.protocol, relay::PROTOCOL_VERSION);
    assert_eq!(hello.client_kind, "helper");

    let snapshot = status.snapshot();
    assert_eq!(snapshot.state, State::Connected);
    assert_eq!(snapshot.device_name.as_deref(), Some("The test Mac"));
    assert_eq!(snapshot.streams_total, 1, "the stream was not counted");
    assert!(snapshot.bytes_relayed > 0, "no bytes were counted");

    let _ = stop_tx.send(true);
    let _ = tokio::time::timeout(Duration::from_secs(5), session).await;
}

#[tokio::test]
#[ignore = "reaches the public internet; run with --ignored"]
async fn the_relay_refuses_to_reach_this_computers_own_network() {
    // The same gateway, asking for the user's LAN instead. Nothing is dialled and the refusal
    // names the policy — this is rule 3, proven end to end rather than at the unit.
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let port = listener.local_addr().expect("addr").port();

    let gateway = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.expect("accept");
        let socket = tokio_tungstenite::accept_async(stream)
            .await
            .expect("handshake");
        let (mut sink, mut source) = socket.split();
        let hello = next_frame(&mut source).await.expect("HELLO");
        assert_eq!(hello.kind, FrameType::Hello);
        send(
            &mut sink,
            Frame::control(
                FrameType::HelloAck,
                serde_json::to_vec(&HelloAck {
                    device_id: Some("d-fake".into()),
                    display_name: Some("The test Mac".into()),
                    max_streams: Some(8),
                    ping_seconds: Some(20),
                })
                .expect("json"),
            ),
        )
        .await;

        let mut refusals = Vec::new();
        for (index, host) in ["127.0.0.1", "192.168.1.1", "169.254.169.254", "localhost"]
            .into_iter()
            .enumerate()
        {
            let stream_id = index as u32 + 1;
            send(
                &mut sink,
                Frame::on(
                    FrameType::Open,
                    stream_id,
                    serde_json::to_vec(&serde_json::json!({"host": host, "port": 80}))
                        .expect("json"),
                ),
            )
            .await;
            loop {
                let frame = next_frame(&mut source).await.expect("an answer");
                if frame.kind == FrameType::Opened {
                    let opened: Opened = frame.json().expect("OPENED json");
                    refusals.push((host, opened));
                    break;
                }
            }
        }
        refusals
    });

    let status = Arc::new(StatusHandle::new("http://127.0.0.1".into(), None, false));
    let (stop_tx, stop_rx) = tokio::sync::watch::channel(false);
    let config = SessionConfig {
        socket_url: format!("ws://127.0.0.1:{port}/egress/device"),
        token: "mxe_d-fake_secret".into(),
        hello: Hello {
            protocol: relay::PROTOCOL_VERSION,
            helper_version: crate::VERSION.into(),
            platform: crate::identity::platform_word().into(),
            hostname: "test-host".into(),
            client_kind: "helper".into(),
            max_streams: relay::DEFAULT_MAX_STREAMS,
        },
    };
    let session = tokio::spawn(async move {
        relay::run_session(&config, Arc::clone(&status), stop_rx, || {}).await
    });

    let refusals = tokio::time::timeout(Duration::from_secs(30), gateway)
        .await
        .expect("the gateway finished")
        .expect("no panic");

    println!("--- proof: the LAN is out of reach ---");
    for (host, opened) in &refusals {
        println!("{host:>16} → {opened:?}");
        assert!(!opened.ok, "{host} was allowed");
        assert_eq!(opened.error.as_deref(), Some("policy"), "{host}");
        assert!(opened.message.is_some(), "{host} refused without saying why");
    }
    assert_eq!(refusals.len(), 4);

    let _ = stop_tx.send(true);
    let _ = tokio::time::timeout(Duration::from_secs(5), session).await;
}

type WsSink = futures_util::stream::SplitSink<
    tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>,
    Message,
>;
type WsSource =
    futures_util::stream::SplitStream<tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>>;

async fn send(sink: &mut WsSink, frame: Frame) {
    sink.send(Message::Binary(frame.encode().into()))
        .await
        .expect("the gateway could not send a frame");
}

async fn next_frame(source: &mut WsSource) -> Option<Frame> {
    while let Some(message) = source.next().await {
        match message.expect("a websocket message") {
            Message::Binary(bytes) => return Some(Frame::decode(&bytes).expect("a valid frame")),
            Message::Close(_) => return None,
            _ => continue,
        }
    }
    None
}
