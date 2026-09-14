//! The fixed loopback redirect listener (S3, S21).
//!
//! The ONLY redirect in the dev world and the live world's fallback:
//! `http://localhost:22161/oauth/callback` (live) / `…:22261/…` (dev). The literal is `localhost`,
//! matching the already-registered `http://localhost:1420/auth/callback`, so no new matching
//! behaviour is assumed — and the daemon binds `127.0.0.1` **and** `[::1]` so either resolution of
//! `localhost` lands.
//!
//! It binds only while a transaction is live, answers exactly one request, and unbinds. Exact
//! redirect matching (§3.1) forbids RFC 8252's ephemeral ports, which is why the port is fixed.

use super::error::{CustodyError, Result};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

/// What one callback carried.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoopbackCallback {
    /// The authorization code.
    pub code: String,
    /// The `state` the authorization server echoed back.
    pub state: String,
}

/// A bound one-shot listener. Dropping it unbinds both sockets.
#[derive(Debug)]
pub struct LoopbackListener {
    v4: TcpListener,
    v6: Option<TcpListener>,
    port: u16,
}

impl LoopbackListener {
    /// Bind the fixed port on both loopback families.
    ///
    /// IPv4 is mandatory — a failure there is [`CustodyError::LoopbackPortUnavailable`], named so
    /// the dev world can say which process holds it. IPv6 is best effort: a host with IPv6
    /// disabled still resolves `localhost` to `127.0.0.1`, and refusing to sign in there would be
    /// a worse answer than binding one family.
    pub async fn bind(port: u16) -> Result<Self> {
        let v4 = TcpListener::bind(SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port))
            .await
            .map_err(|e| CustodyError::LoopbackPortUnavailable {
                port,
                cause: e.to_string(),
            })?;
        let v6 = TcpListener::bind(SocketAddr::new(IpAddr::V6(Ipv6Addr::LOCALHOST), port))
            .await
            .ok();
        Ok(LoopbackListener { v4, v6, port })
    }

    /// The port actually bound — always the one asked for; exact matching allows nothing else.
    pub fn port(&self) -> u16 {
        self.port
    }

    /// Accept exactly one request, answer it, and consume the listener.
    ///
    /// A request that is not the callback (a stray probe, a favicon fetch) is answered 404 and the
    /// listener keeps waiting — a browser preflight must not burn the one sign-in.
    pub async fn accept_callback(self) -> Result<LoopbackCallback> {
        loop {
            let stream = match &self.v6 {
                Some(v6) => tokio::select! {
                    r = self.v4.accept() => r.map(|(s, _)| s),
                    r = v6.accept() => r.map(|(s, _)| s),
                },
                None => self.v4.accept().await.map(|(s, _)| s),
            }
            .map_err(|e| CustodyError::Transport {
                endpoint: "the sign-in callback listener",
                cause: e.to_string(),
            })?;

            match handle(stream).await {
                Ok(Some(callback)) => return Ok(callback),
                Ok(None) => continue,
                Err(e) => return Err(e),
            }
        }
    }
}

async fn handle(mut stream: TcpStream) -> Result<Option<LoopbackCallback>> {
    // The request line is all we need, and a redirect carries no body. 8 KiB is far more than any
    // authorization code plus state; anything longer is not our callback.
    let mut buf = vec![0u8; 8192];
    let read = stream
        .read(&mut buf)
        .await
        .map_err(|e| CustodyError::Transport {
            endpoint: "the sign-in callback listener",
            cause: e.to_string(),
        })?;
    let request = String::from_utf8_lossy(&buf[..read]).to_string();
    let target = request
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or("");

    let parsed = parse_callback(target);
    let (status, body) = match &parsed {
        Some(_) => (
            "200 OK",
            "<!doctype html><meta charset=utf-8><title>Signed in</title>\
             <body style=\"font:16px system-ui;padding:3rem\">\
             <p>You are signed in to AI Matrx. You can close this tab and go back to the app.</p>",
        ),
        None => (
            "404 Not Found",
            "<!doctype html><meta charset=utf-8><title>Not found</title>\
             <body style=\"font:16px system-ui;padding:3rem\">\
             <p>This address only answers the AI Matrx sign-in callback.</p>",
        ),
    };
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\n\
         Connection: close\r\nCache-Control: no-store\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes()).await;
    let _ = stream.shutdown().await;
    Ok(parsed)
}

/// Pull `code` and `state` out of a request target. Both must be present on the callback path.
fn parse_callback(target: &str) -> Option<LoopbackCallback> {
    let (path, query) = target.split_once('?')?;
    if path != "/oauth/callback" {
        return None;
    }
    let mut code = None;
    let mut state = None;
    for pair in query.split('&') {
        let (k, v) = pair.split_once('=')?;
        let value = urlencoding::decode(v).ok()?.into_owned();
        match k {
            "code" => code = Some(value),
            "state" => state = Some(value),
            _ => {}
        }
    }
    Some(LoopbackCallback {
        code: code?,
        state: state?,
    })
}

/// Pull `code` and `state` out of a deep link (`aimatrx://auth/callback?code=…&state=…`).
///
/// The host forwards the whole URL; parsing it here keeps one parser for both legs.
pub fn parse_deep_link(url: &str) -> Option<LoopbackCallback> {
    let query = url.split_once('?')?.1;
    let mut code = None;
    let mut state = None;
    for pair in query.split('&') {
        let (k, v) = pair.split_once('=')?;
        let value = urlencoding::decode(v).ok()?.into_owned();
        match k {
            "code" => code = Some(value),
            "state" => state = Some(value),
            _ => {}
        }
    }
    Some(LoopbackCallback {
        code: code?,
        state: state?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_callback_path_is_the_only_one_answered() {
        assert!(parse_callback("/oauth/callback?code=a&state=b").is_some());
        assert!(parse_callback("/favicon.ico").is_none());
        assert!(parse_callback("/?code=a&state=b").is_none());
    }

    #[test]
    fn both_halves_are_required() {
        assert!(parse_callback("/oauth/callback?code=a").is_none());
        assert!(parse_callback("/oauth/callback?state=b").is_none());
    }

    #[test]
    fn percent_encoding_is_decoded() {
        let c = parse_callback("/oauth/callback?code=a%2Bb&state=c%3Dd").expect("parse");
        assert_eq!(c.code, "a+b");
        assert_eq!(c.state, "c=d");
    }

    #[test]
    fn the_deep_link_parses_with_the_same_rules() {
        let c = parse_deep_link("aimatrx://auth/callback?code=xyz&state=st").expect("parse");
        assert_eq!(c.code, "xyz");
        assert_eq!(c.state, "st");
        assert!(parse_deep_link("aimatrx://auth/callback").is_none());
    }
}
