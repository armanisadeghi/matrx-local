//! The browser-origin policy on the TCP listener (SPEC-ENGINE §3, F2, C8).
//!
//! The earlier "reject any request carrying `Origin`" rule would have refused the one client the
//! listener exists for: the webview is a browser context, so **every** `fetch()` it makes carries
//! an `Origin`. It is replaced by an allow-list — and the `Host` allow-list, not the `Origin` ban,
//! stays as the DNS-rebinding guard.

use matrx_sync::custody::World;

/// The origins a webview may present.
///
/// `tauri://localhost` (macOS/Linux) and `http://tauri.localhost` (Windows) are the packaged
/// app's; `http://localhost:1420` is the Vite dev server and is accepted **in the dev world only**
/// (VERIFIED `tauri.conf.json` `build.devUrl`).
pub fn origin_allowed(origin: &str, world: World) -> bool {
    matches!(origin, "tauri://localhost" | "http://tauri.localhost")
        || (world == World::Dev && origin == "http://localhost:1420")
}

/// The `Host` allow-list — `127.0.0.1:<port>` or `localhost:<port>` and nothing else.
///
/// **This is the DNS-rebinding guard.** A page on a hostile domain that resolves to 127.0.0.1
/// still sends that domain in `Host`, and is refused here before any handler runs.
pub fn host_allowed(host: &str, port: Option<u16>) -> bool {
    let Some(port) = port else {
        // No TCP listener is up, so the only transport is the socket/pipe, where `Host` is
        // whatever the client library invented and carries no authority either way.
        return true;
    };
    host == format!("127.0.0.1:{port}") || host == format!("localhost:{port}")
}

/// The headers every CORS answer carries. `Access-Control-Allow-Origin` echoes the accepted value
/// — never `*`, because the request is credentialed.
pub fn headers(origin: &str, methods: &str) -> Vec<(&'static str, String)> {
    vec![
        ("Access-Control-Allow-Origin", origin.to_string()),
        ("Vary", "Origin".to_string()),
        (
            "Access-Control-Allow-Headers",
            // `X-Matrx-Client` is required because §3 itself demands the header and it is not a
            // CORS-simple one; without it every request would fail preflight.
            "Authorization, Content-Type, X-Matrx-Client, Last-Event-ID".to_string(),
        ),
        ("Access-Control-Allow-Methods", methods.to_string()),
        ("Access-Control-Max-Age", "600".to_string()),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_packaged_webviews_two_origins_are_allowed_in_both_worlds() {
        for world in [World::Live, World::Dev] {
            assert!(origin_allowed("tauri://localhost", world));
            assert!(origin_allowed("http://tauri.localhost", world));
        }
    }

    #[test]
    fn the_vite_dev_server_is_allowed_in_the_dev_world_only() {
        assert!(origin_allowed("http://localhost:1420", World::Dev));
        assert!(!origin_allowed("http://localhost:1420", World::Live));
    }

    #[test]
    fn every_other_origin_is_refused() {
        for origin in [
            "https://evil.example",
            "http://localhost:3000",
            "null",
            "",
            "http://127.0.0.1:1420",
        ] {
            assert!(!origin_allowed(origin, World::Dev), "{origin}");
            assert!(!origin_allowed(origin, World::Live), "{origin}");
        }
    }

    #[test]
    fn the_host_guard_refuses_a_rebound_dns_name() {
        assert!(host_allowed("127.0.0.1:22263", Some(22263)));
        assert!(host_allowed("localhost:22263", Some(22263)));
        assert!(!host_allowed("evil.example:22263", Some(22263)));
        assert!(!host_allowed("127.0.0.1:22264", Some(22263)));
        assert!(!host_allowed("127.0.0.1", Some(22263)));
    }

    #[test]
    fn the_allow_origin_header_is_never_a_wildcard() {
        let h = headers("tauri://localhost", "GET, OPTIONS");
        let origin = h
            .iter()
            .find(|(k, _)| *k == "Access-Control-Allow-Origin")
            .expect("header");
        assert_eq!(origin.1, "tauri://localhost");
        assert_ne!(origin.1, "*");
        assert!(h.iter().any(|(k, v)| *k == "Access-Control-Allow-Headers"
            && v.contains("X-Matrx-Client")));
    }
}
