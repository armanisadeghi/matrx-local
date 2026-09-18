//! The server address, and the three URLs derived from it.
//!
//! One value, one place. `--server` overrides it; everything else (the device WebSocket, the
//! pairing routes, the device routes) is derived here so a typo cannot make two halves of the
//! helper talk to two different servers.

use std::fmt;

/// The production gateway. The ONLY hard-coded address in this crate.
pub const DEFAULT_SERVER: &str = "https://server.app.matrxserver.com";

/// A normalised server base URL.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Server {
    base: String,
}

/// Why a server address cannot be used.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServerError {
    /// What the user typed.
    pub given: String,
}

impl fmt::Display for ServerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{:?} is not a web address the Home Connection can use. Give it one that starts with \
             https:// (for example {DEFAULT_SERVER}).",
            self.given
        )
    }
}

impl std::error::Error for ServerError {}

impl Server {
    /// Parse and normalise: a missing scheme becomes `https://`, a trailing slash is dropped.
    pub fn parse(given: &str) -> Result<Self, ServerError> {
        let refuse = || ServerError {
            given: given.to_string(),
        };
        let trimmed = given.trim();
        // The scheme is split off FIRST. Trimming trailing slashes from the whole string would
        // turn "https://" into "https:", which then reads as a bare host and becomes
        // "https://https:" — a nonsense address that looked valid.
        let (scheme, rest) = if let Some(rest) = trimmed.strip_prefix("https://") {
            ("https", rest)
        } else if let Some(rest) = trimmed.strip_prefix("http://") {
            ("http", rest)
        } else if trimmed.contains("://") {
            // A scheme we do not speak — say so rather than silently rewriting it.
            return Err(refuse());
        } else {
            ("https", trimmed)
        };
        let host = rest.trim_end_matches('/');
        if host.is_empty() || host.starts_with('/') {
            return Err(refuse());
        }
        Ok(Server {
            base: format!("{scheme}://{host}"),
        })
    }

    /// The production server.
    pub fn default_server() -> Self {
        Server::parse(DEFAULT_SERVER).expect("the built-in server address is well formed")
    }

    /// `https://host` with no trailing slash — what the status document shows.
    pub fn base(&self) -> &str {
        &self.base
    }

    /// `wss://host/egress/device` (or `ws://` when the base is plain http, which only a local
    /// development server ever is).
    pub fn device_socket_url(&self) -> String {
        let socket_base = if let Some(rest) = self.base.strip_prefix("https://") {
            format!("wss://{rest}")
        } else if let Some(rest) = self.base.strip_prefix("http://") {
            format!("ws://{rest}")
        } else {
            self.base.clone()
        };
        format!("{socket_base}/egress/device")
    }

    /// `https://host/egress/<path>`.
    pub fn egress_url(&self, path: &str) -> String {
        format!("{}/egress/{}", self.base, path.trim_start_matches('/'))
    }
}

impl fmt::Display for Server {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.base)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_is_the_production_gateway_and_its_socket_is_the_contracts_url() {
        let server = Server::default_server();
        assert_eq!(server.base(), "https://server.app.matrxserver.com");
        assert_eq!(
            server.device_socket_url(),
            "wss://server.app.matrxserver.com/egress/device"
        );
    }

    #[test]
    fn a_trailing_slash_and_a_missing_scheme_are_both_normalised() {
        for given in [
            "https://server.app.matrxserver.com",
            "https://server.app.matrxserver.com/",
            "server.app.matrxserver.com",
            "  server.app.matrxserver.com/  ",
        ] {
            assert_eq!(
                Server::parse(given).expect(given).base(),
                "https://server.app.matrxserver.com",
                "{given}"
            );
        }
    }

    #[test]
    fn a_local_development_server_keeps_its_plain_http_and_gets_a_plain_socket() {
        let server = Server::parse("http://127.0.0.1:8000").expect("local");
        assert_eq!(server.base(), "http://127.0.0.1:8000");
        assert_eq!(
            server.device_socket_url(),
            "ws://127.0.0.1:8000/egress/device"
        );
    }

    #[test]
    fn every_route_hangs_off_the_one_base() {
        let server = Server::parse("https://example.test").expect("server");
        assert_eq!(
            server.egress_url("pairings"),
            "https://example.test/egress/pairings"
        );
        assert_eq!(
            server.egress_url("/devices/abc"),
            "https://example.test/egress/devices/abc"
        );
    }

    #[test]
    fn nonsense_is_refused_with_a_sentence_that_says_what_to_type() {
        for given in ["", "   ", "https://", "ftp://example.test", "wss://example.test"] {
            let error = Server::parse(given).expect_err(given);
            let sentence = error.to_string();
            assert!(sentence.contains("https://"), "{given}: {sentence}");
        }
    }
}
