//! The route table and the error envelope (SPEC-ENGINE §3, §3.1; C1's five auth routes).

use super::cors;
use super::{SharedState, MIN_PROTOCOL_VERSION, PROTOCOL_VERSION};
use crate::discovery::Scope;
use http_body_util::{BodyExt, Full};
use hyper::body::{Bytes, Incoming};
use hyper::{header, Method, Request, Response, StatusCode};
use matrx_sync::custody::{CustodyError, RedirectKind};
use serde::Deserialize;
use serde_json::json;

/// The response body type: either a whole JSON document or an SSE stream.
pub type BoxBody = http_body_util::combinators::BoxBody<Bytes, std::io::Error>;

fn full(bytes: impl Into<Bytes>) -> BoxBody {
    Full::new(bytes.into()).map_err(|never| match never {}).boxed()
}

/// SPEC-ENGINE §3.1's envelope. `message` and `remedy` are user-facing sentences; **no surface
/// invents its own wording for a state the daemon named.**
pub fn error(
    status: StatusCode,
    code: &str,
    message: &str,
    remedy: &str,
    retryable: bool,
    details: serde_json::Value,
) -> Response<BoxBody> {
    let body = json!({"error": {
        "code": code, "message": message, "remedy": remedy,
        "retryable": retryable, "details": details,
    }});
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .body(full(body.to_string()))
        .expect("static response")
}

fn ok_json(value: serde_json::Value) -> Response<BoxBody> {
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::CACHE_CONTROL, "no-store")
        .body(full(value.to_string()))
        .expect("static response")
}

/// Which transport a request arrived on. It changes nothing about authorisation — both transports
/// serve the identical API with the identical auth (C7) — but the socket carries no `Origin` and
/// no meaningful `Host`, so the browser guards apply only to the listener.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Transport {
    /// The per-user Unix socket or Windows named pipe (C6).
    Local,
    /// The loopback TCP listener in the daemon band (C7).
    Loopback,
}

/// Every route this daemon serves. Anything else is a 404 with the envelope.
pub async fn dispatch(
    state: SharedState,
    transport: Transport,
    request: Request<Incoming>,
) -> Response<BoxBody> {
    let method = request.method().clone();
    let path = request.uri().path().to_string();
    let origin = header_str(&request, header::ORIGIN);

    // --- The browser guards, before anything else, and only on the listener.
    if transport == Transport::Loopback {
        let host = header_str(&request, header::HOST).unwrap_or_default();
        if !cors::host_allowed(&host, state.tcp_port) {
            return error(
                StatusCode::FORBIDDEN,
                "forbidden_origin",
                "That request did not come from this computer's own sync service address.",
                "Nothing to do — AI Matrx refused a request that was not addressed to it.",
                false,
                json!({"host": host}),
            );
        }
        if let Some(origin) = origin.as_deref() {
            if !cors::origin_allowed(origin, state.world) {
                return error(
                    StatusCode::FORBIDDEN,
                    "forbidden_origin",
                    "That page is not allowed to talk to AI Matrx Sync.",
                    "Nothing to do — AI Matrx refused a request from an unknown page.",
                    false,
                    json!({"origin": origin}),
                );
            }
        }
        // A preflight carries no credentials by definition, so it is answered before auth.
        if method == Method::OPTIONS {
            let mut builder = Response::builder().status(StatusCode::NO_CONTENT);
            if let Some(origin) = origin.as_deref() {
                for (k, v) in cors::headers(origin, methods_for(&path)) {
                    builder = builder.header(k, v);
                }
            }
            return builder.body(full(Bytes::new())).expect("preflight");
        }
    }

    let mut response = route(&state, &method, &path, request).await;

    if transport == Transport::Loopback {
        if let Some(origin) = origin.as_deref() {
            if cors::origin_allowed(origin, state.world) {
                for (k, v) in cors::headers(origin, methods_for(&path)) {
                    if let (Ok(name), Ok(value)) = (
                        header::HeaderName::from_bytes(k.as_bytes()),
                        header::HeaderValue::from_str(&v),
                    ) {
                        response.headers_mut().insert(name, value);
                    }
                }
            }
        }
    }
    response
}

async fn route(
    state: &SharedState,
    method: &Method,
    path: &str,
    request: Request<Incoming>,
) -> Response<BoxBody> {
    // `GET /v1/health` is the ONE unauthenticated route, and it says nothing a caller could not
    // learn by looking at the discovery file.
    if method == Method::GET && path == "/v1/health" {
        let snapshot = state.custodian.session().await;
        return ok_json(json!({
            "ok": true,
            "protocol_version": PROTOCOL_VERSION,
            "state": snapshot.state.as_str(),
            "world": state.world.as_str(),
        }));
    }

    // --- Bearer auth and `X-Matrx-Client` on every other route.
    let scope = match bearer(&request).and_then(|t| state.tokens.scope_of(&t)) {
        Some(scope) => scope,
        None => {
            return error(
                StatusCode::UNAUTHORIZED,
                "unauthorized",
                "That request did not carry a valid AI Matrx Sync token.",
                "Restart AI Matrx; it reads the token from this computer's own sync folder.",
                false,
                json!({}),
            )
        }
    };
    if header_str(&request, header::HeaderName::from_static("x-matrx-client")).is_none() {
        return error(
            StatusCode::BAD_REQUEST,
            "bad_request",
            "That request did not say which client it came from.",
            "Nothing to do — every AI Matrx client sends an X-Matrx-Client header.",
            false,
            json!({"missing_header": "X-Matrx-Client"}),
        );
    }

    let read_only = matches!(
        (method, path),
        (&Method::GET, "/v1/token")
            | (&Method::GET, "/v1/session")
            | (&Method::GET, "/v1/status")
            | (&Method::GET, "/v1/version")
            | (&Method::GET, "/v1/events")
    );
    if scope == Scope::Read && !read_only {
        return error(
            StatusCode::FORBIDDEN,
            "forbidden_scope",
            "That action needs the control token, which only AI Matrx itself holds.",
            "Use the app's own controls — a page cannot sign this computer in or out.",
            false,
            json!({"scope": "read"}),
        );
    }

    match (method, path) {
        (&Method::GET, "/v1/version") => ok_json(json!({
            "daemon_version": state.daemon_version,
            "protocol_version": PROTOCOL_VERSION,
            "min_protocol_version": MIN_PROTOCOL_VERSION,
            "executable_path": state.executable_path,
            "world": state.world.as_str(),
        })),

        // ---- The five auth routes SPEC-CUSTODY owns (C1).
        (&Method::GET, "/v1/token") => match state.custodian.token().await {
            Ok(grant) => ok_json(json!({
                "access_token": grant.access_token,
                "expires_at": grant.expires_at,
                "user_id": grant.user_id,
                "token_type": grant.token_type,
            })),
            // Never a 500, never an empty 200: a consumer's switch over these four plus the 200 is
            // exhaustive (A5).
            Err(refusal) => Response::builder()
                .status(StatusCode::CONFLICT)
                .header(header::CONTENT_TYPE, "application/json")
                .header(header::CACHE_CONTROL, "no-store")
                .body(full(
                    json!({
                        "state": refusal.state.as_str(),
                        "state_reason": refusal.state_reason,
                        "since": refusal.since,
                        "email": refusal.email,
                    })
                    .to_string(),
                ))
                .expect("refusal"),
        },

        (&Method::GET, "/v1/session") => {
            let s = state.custodian.session().await;
            ok_json(json!({
                "signed_in": s.signed_in,
                "user_id": s.user_id,
                "email": s.email,
                "state": s.state.as_str(),
                "state_reason": s.state_reason,
                "since": s.since,
                "next_attempt_at": s.next_attempt_at,
                "cloud_state_write_pending": s.cloud_state_write_pending,
            }))
        }

        (&Method::POST, "/v1/sign-in") => {
            #[derive(Deserialize, Default)]
            struct Body {
                redirect_kind: Option<String>,
            }
            let body: Body = read_json(request).await.unwrap_or_default();
            let prefer = match body.redirect_kind.as_deref() {
                Some("deep_link") => Some(RedirectKind::DeepLink),
                Some("loopback") => Some(RedirectKind::Loopback),
                _ => None,
            };
            match state.custodian.begin_sign_in(prefer).await {
                Ok(start) => ok_json(json!({
                    "transaction_id": start.transaction_id,
                    "authorize_url": start.authorize_url,
                    "redirect_uri": start.redirect_uri,
                    "redirect_kind": start.redirect_kind,
                })),
                Err(e) => custody_error(&e),
            }
        }

        (&Method::POST, "/v1/sign-in/callback") => {
            #[derive(Deserialize)]
            struct Body {
                code: String,
                state: String,
            }
            let Some(body) = read_json::<Body>(request).await else {
                return error(
                    StatusCode::BAD_REQUEST,
                    "bad_request",
                    "That sign-in callback did not carry a code and a state.",
                    "Start sign-in again from the app.",
                    false,
                    json!({}),
                );
            };
            match state.custodian.complete_sign_in(&body.code, &body.state).await {
                Ok(signed_in) => ok_json(json!({
                    "user_id": signed_in.user_id,
                    "email": signed_in.email,
                })),
                // S14: an unknown state is refused honestly — 409, with the sentence the host
                // shows after it has tried the other world's daemon.
                Err(e @ CustodyError::UnknownTransaction) => error(
                    StatusCode::CONFLICT,
                    "unknown_transaction",
                    &e.message(),
                    e.remedy(),
                    false,
                    json!({}),
                ),
                Err(e) => custody_error(&e),
            }
        }

        (&Method::POST, "/v1/sign-out") => match state.custodian.sign_out().await {
            Ok(()) => ok_json(json!({"ok": true})),
            Err(e) => custody_error(&e),
        },

        // ---- SPEC-ENGINE's own verbs that FS-C5 needs.
        (&Method::POST, "/v1/shutdown") => {
            state.shutdown.notify_waiters();
            Response::builder()
                .status(StatusCode::ACCEPTED)
                .header(header::CONTENT_TYPE, "application/json")
                .body(full(
                    json!({"accepted": true, "budget_s": state.shutdown_budget_s}).to_string(),
                ))
                .expect("accepted")
        }

        (&Method::GET, "/v1/events") => super::server::sse(state.clone()),

        // Every other route in SPEC-ENGINE's table is FS-L2a's and does not exist yet. It answers
        // 404 with the envelope — never a stub with a plausible shape (law 4).
        _ => error(
            StatusCode::NOT_FOUND,
            "not_found",
            "This version of AI Matrx Sync does not serve that request yet.",
            "Update AI Matrx.",
            false,
            json!({"method": method.as_str(), "path": path}),
        ),
    }
}

fn custody_error(e: &CustodyError) -> Response<BoxBody> {
    let status = match e.code() {
        "credential_store_unavailable" | "sign_in_needed" | "signed_out"
        | "unknown_transaction" | "loopback_port_unavailable" => StatusCode::CONFLICT,
        "offline" => StatusCode::SERVICE_UNAVAILABLE,
        _ => StatusCode::INTERNAL_SERVER_ERROR,
    };
    error(status, e.code(), &e.message(), e.remedy(), e.retryable(), json!({}))
}

/// The methods a route actually serves, for `Access-Control-Allow-Methods`.
fn methods_for(path: &str) -> &'static str {
    match path {
        "/v1/health" | "/v1/version" | "/v1/token" | "/v1/session" | "/v1/status"
        | "/v1/events" => "GET, OPTIONS",
        "/v1/sign-in" | "/v1/sign-in/callback" | "/v1/sign-out" | "/v1/shutdown" => {
            "POST, OPTIONS"
        }
        _ => "GET, POST, OPTIONS",
    }
}

fn header_str<T>(request: &Request<T>, name: header::HeaderName) -> Option<String> {
    request
        .headers()
        .get(name)
        .and_then(|v| v.to_str().ok())
        .map(str::to_string)
}

fn bearer<T>(request: &Request<T>) -> Option<String> {
    header_str(request, header::AUTHORIZATION)?
        .strip_prefix("Bearer ")
        .map(|t| t.trim().to_string())
        .filter(|t| !t.is_empty())
}

async fn read_json<T: for<'de> Deserialize<'de>>(request: Request<Incoming>) -> Option<T> {
    // A control-API body is a handful of fields; anything larger is not one of ours.
    let bytes = request.into_body().collect().await.ok()?.to_bytes();
    if bytes.len() > 64 * 1024 {
        return None;
    }
    serde_json::from_slice(&bytes).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_five_read_routes_accept_the_read_token() {
        // The scope split is what makes §12's sentence true: a compromised webview can obtain
        // short-lived access tokens, and cannot sign the device out or stop the daemon.
        let read_routes = [
            (Method::GET, "/v1/token"),
            (Method::GET, "/v1/session"),
            (Method::GET, "/v1/status"),
            (Method::GET, "/v1/version"),
            (Method::GET, "/v1/events"),
        ];
        let control_routes = [
            (Method::POST, "/v1/sign-in"),
            (Method::POST, "/v1/sign-in/callback"),
            (Method::POST, "/v1/sign-out"),
            (Method::POST, "/v1/shutdown"),
        ];
        for (m, p) in read_routes {
            assert!(
                matches!(
                    (&m, p),
                    (&Method::GET, "/v1/token")
                        | (&Method::GET, "/v1/session")
                        | (&Method::GET, "/v1/status")
                        | (&Method::GET, "/v1/version")
                        | (&Method::GET, "/v1/events")
                ),
                "{m} {p}"
            );
        }
        for (m, p) in control_routes {
            assert!(
                !matches!(
                    (&m, p),
                    (&Method::GET, "/v1/token")
                        | (&Method::GET, "/v1/session")
                        | (&Method::GET, "/v1/status")
                        | (&Method::GET, "/v1/version")
                        | (&Method::GET, "/v1/events")
                ),
                "{m} {p} must not be read-scoped"
            );
        }
    }

    #[test]
    fn the_error_envelope_always_carries_a_remedy() {
        let response = error(
            StatusCode::NOT_FOUND,
            "not_found",
            "m",
            "r",
            false,
            json!({}),
        );
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            response.headers().get(header::CONTENT_TYPE).unwrap(),
            "application/json"
        );
    }

    #[test]
    fn allow_methods_never_offers_a_verb_a_route_does_not_serve() {
        assert_eq!(methods_for("/v1/token"), "GET, OPTIONS");
        assert_eq!(methods_for("/v1/sign-out"), "POST, OPTIONS");
    }
}
