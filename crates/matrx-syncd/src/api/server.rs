//! The two transports (C6, C7) and the SSE stream (C8).
//!
//! **Both serve the identical `/v1` API with the identical auth.** The socket/pipe is the Tauri
//! host's and the Python engine's; the loopback listener is the webview's, because a browser
//! context cannot open a Unix socket.

use super::routes::{self, BoxBody, Transport};
use super::SharedState;
use http_body_util::{BodyExt, StreamBody};
use hyper::body::{Bytes, Frame};
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{header, Response, StatusCode};
use hyper_util::rt::TokioIo;
use matrx_sync::custody::World;
use std::io;
use std::net::{Ipv4Addr, SocketAddr};
use std::sync::Arc;
use tokio::net::TcpListener;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt as _;

/// The bound transports, reported honestly: either may be absent, and the daemon says which.
///
/// Binding is separated from serving because `syncd.json`'s `tcp_port` and the `Host` allow-list
/// both need the port the allocator actually took, and the state a handler reads must carry it
/// before the first request can arrive.
pub struct Bound {
    /// The Unix socket path or the Windows pipe name, as written into `syncd.json`.
    pub endpoint: String,
    /// The loopback API listener's port, or `None` when no port in the band was free.
    pub tcp_port: Option<u16>,
    local: local::LocalTransport,
    tcp: Option<TcpListener>,
}

/// Bind both transports. Nothing is served until [`serve`] is called with the state.
pub async fn bind(world: World, endpoint: LocalEndpoint) -> io::Result<Bound> {
    let tcp = bind_in_band(world).await;
    let local = local::bind(&endpoint).await?;
    Ok(Bound {
        endpoint: local.endpoint.clone(),
        tcp_port: tcp.as_ref().map(|(_, port)| *port),
        local,
        tcp: tcp.map(|(listener, _)| listener),
    })
}

/// Where the per-user control endpoint lives, as the daemon resolved it (C6).
pub enum LocalEndpoint {
    /// The Unix socket path.
    #[cfg(unix)]
    Socket(std::path::PathBuf),
    /// The Windows named pipe name.
    #[cfg(windows)]
    Pipe(String),
}

/// Start accepting on both transports.
pub fn serve(state: SharedState, bound: Bound) -> ServeHandles {
    let local_state = Arc::clone(&state);
    let mut handles = ServeHandles {
        local: Some(tokio::spawn(bound.local.serve(local_state))),
        tcp: None,
    };
    if let Some(listener) = bound.tcp {
        handles.tcp = Some(tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else {
                    continue;
                };
                let state = Arc::clone(&state);
                tokio::spawn(async move {
                    let io = TokioIo::new(stream);
                    let service = service_fn(move |req| {
                        let state = Arc::clone(&state);
                        async move {
                            Ok::<_, std::convert::Infallible>(
                                routes::dispatch(state, Transport::Loopback, req).await,
                            )
                        }
                    });
                    let _ = http1::Builder::new()
                        .keep_alive(true)
                        .serve_connection(io, service)
                        .await;
                });
            }
        }));
    }
    handles
}

/// The tasks serving each transport. Aborting them stops accepting.
pub struct ServeHandles {
    /// The socket/pipe accept loop.
    pub local: Option<tokio::task::JoinHandle<()>>,
    /// The loopback accept loop, when a port in the band was free.
    pub tcp: Option<tokio::task::JoinHandle<()>>,
}

impl ServeHandles {
    /// Stop accepting on both transports.
    pub fn abort(&self) {
        if let Some(h) = &self.local {
            h.abort();
        }
        if let Some(h) = &self.tcp {
            h.abort();
        }
    }
}

/// C7's allocator: a sequential scan of the daemon band, **skipping the OAuth callback port**
/// reserved by S21 — try `base+2 … base+19`, then `base+0`.
///
/// The band never overlaps the engine's 22140–22159 / 22240–22259, which the engine claims by its
/// own sequential scan with no shared allocator.
async fn bind_in_band(world: World) -> Option<(TcpListener, u16)> {
    let base = world.daemon_band_base();
    let oauth = world.oauth_callback_port();
    let order = (2..=19u16).map(|n| base + n).chain(std::iter::once(base));
    for port in order {
        if port == oauth {
            continue;
        }
        if let Ok(listener) = TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, port))).await
        {
            return Some((listener, port));
        }
    }
    // Every port in the band is taken. The daemon still serves the socket/pipe, and the webview's
    // state will say so rather than spinning — it is named, not silent.
    eprintln!(
        "[syncd] no free port in the {} daemon band {}–{}; the loopback API is unavailable this \
         run and the webview will report it",
        world.as_str(),
        base,
        world.daemon_band_last()
    );
    None
}

// ------------------------------------------------------------------- the SSE stream

/// `GET /v1/events` — SSE framing over the same `/v1` API (C8).
///
/// Consumed with `fetch()` + `ReadableStream` so an `Authorization` header can be sent;
/// `EventSource` is never used, and the token never appears in a URL.
pub fn sse(state: SharedState) -> Response<BoxBody> {
    let receiver = state.custodian.subscribe();
    let stream = BroadcastStream::new(receiver).filter_map(|event| {
        let event = event.ok()?;
        let data = serde_json::to_string(&event).ok()?;
        // C9's names, owned by SPEC-ENGINE. Custody emits exactly one of them.
        Some(Ok(Frame::data(Bytes::from(format!(
            "event: session.changed\ndata: {data}\n\n"
        )))))
    });

    // A 15 s keepalive comment, so a proxy or a sleeping NIC cannot silently strand the stream.
    let keepalive = tokio_stream::wrappers::IntervalStream::new(tokio::time::interval(
        std::time::Duration::from_secs(15),
    ))
    .skip(1)
    .map(|_| Ok(Frame::data(Bytes::from_static(b": keepalive\n\n"))));

    let body = StreamBody::new(stream.merge(keepalive)).boxed();
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, "no-store")
        .header("X-Accel-Buffering", "no")
        .body(body)
        .expect("stream response")
}

// ------------------------------------------------------- the per-user socket / pipe

#[cfg(unix)]
mod local {
    use super::*;
    use tokio::net::UnixListener;

    pub struct LocalTransport {
        pub endpoint: String,
        listener: UnixListener,
    }

    impl LocalTransport {
        pub async fn serve(self, state: SharedState) {
            loop {
                let Ok((stream, _)) = self.listener.accept().await else {
                    continue;
                };
                let state = Arc::clone(&state);
                tokio::spawn(async move {
                    let io = TokioIo::new(stream);
                    let service = service_fn(move |req| {
                        let state = Arc::clone(&state);
                        async move {
                            Ok::<_, std::convert::Infallible>(
                                routes::dispatch(state, Transport::Local, req).await,
                            )
                        }
                    });
                    let _ = http1::Builder::new().serve_connection(io, service).await;
                });
            }
        }
    }

    pub async fn bind(endpoint: &super::LocalEndpoint) -> io::Result<LocalTransport> {
        use std::os::unix::fs::PermissionsExt;
        let super::LocalEndpoint::Socket(path) = endpoint;
        let path = path.clone();
        // A stale socket file from a daemon that died without unlinking is removed — but only
        // after the clobber check has already established that no live daemon is serving it.
        if path.exists() {
            std::fs::remove_file(&path)?;
        }
        let listener = UnixListener::bind(&path)?;
        // 0600: another OS user cannot connect even if they can see the path (§12, D18).
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        Ok(LocalTransport {
            endpoint: path.display().to_string(),
            listener,
        })
    }
}

#[cfg(windows)]
mod local {
    use super::*;
    use tokio::net::windows::named_pipe::{NamedPipeServer, ServerOptions};

    pub struct LocalTransport {
        pub endpoint: String,
        first: NamedPipeServer,
    }

    impl LocalTransport {
        pub async fn serve(self, state: SharedState) {
            // A named pipe serves one client per instance, so a fresh instance is created before
            // handing the connected one off — the standard Windows accept loop.
            let name = self.endpoint.clone();
            let mut server = self.first;
            loop {
                if server.connect().await.is_err() {
                    continue;
                }
                let connected = server;
                server = match ServerOptions::new().create(&name) {
                    Ok(next) => next,
                    Err(e) => {
                        eprintln!("[syncd] could not create the next pipe instance: {e}");
                        return;
                    }
                };
                let state = Arc::clone(&state);
                tokio::spawn(async move {
                    let io = TokioIo::new(connected);
                    let service = service_fn(move |req| {
                        let state = Arc::clone(&state);
                        async move {
                            Ok::<_, std::convert::Infallible>(
                                routes::dispatch(state, Transport::Local, req).await,
                            )
                        }
                    });
                    let _ = http1::Builder::new().serve_connection(io, service).await;
                });
            }
        }
    }

    pub async fn bind(endpoint: &super::LocalEndpoint) -> io::Result<LocalTransport> {
        // `%LOCALAPPDATA%` is already ACL'd to this user; the pipe name carries sha1(SID)[:12] so
        // two logged-on users never collide, and `first_pipe_instance` refuses to attach to a
        // pipe somebody else already created under our name.
        let super::LocalEndpoint::Pipe(name) = endpoint;
        let name = name.clone();
        let first = ServerOptions::new()
            .first_pipe_instance(true)
            .create(&name)?;
        Ok(LocalTransport {
            endpoint: name,
            first,
        })
    }
}
