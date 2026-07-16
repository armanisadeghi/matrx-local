"""
Auth-gate smoke tests — the tunnel/loopback trust boundary.

These exercise app/api/auth.py AuthMiddleware + app/api/remote_auth.py:

  * Direct loopback (no Cloudflare headers) keeps the presence-only boundary:
    a present Bearer is accepted, a missing one is rejected.
  * Tunnel-borne requests (simulated with a Cf-Ray header) are untrusted: a
    junk/unverifiable token is rejected with 401 even on a route that is
    permissive over direct loopback.
  * Always-public routes stay public regardless of how they arrive.

The tunnel-rejection tests assert 401 — that holds whether or not the test
environment can reach the Supabase auth server (an unreachable issuer fails
closed, which is exactly the behaviour we want to guarantee).

We deliberately do NOT hit /admin/shutdown here (a pass-through would kill the
shared test engine); /settings and /tools/invoke are sufficient witnesses.
"""

from __future__ import annotations

import httpx


# Headers a Cloudflare tunnel stamps on forwarded traffic.
_TUNNEL_HEADERS = {"cf-ray": "test-ray-0000000000000000", "cf-connecting-ip": "203.0.113.7"}


# ---------------------------------------------------------------------------
# Always-public routes
# ---------------------------------------------------------------------------


def test_health_public_even_over_tunnel(http_public: httpx.Client) -> None:
    r = http_public.get("/health", headers=_TUNNEL_HEADERS)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Direct loopback (no tunnel headers) — presence-only boundary preserved
# ---------------------------------------------------------------------------


def test_protected_route_rejects_missing_token_local(http_public: httpx.Client) -> None:
    """A protected route with no token is 401 even on direct loopback."""
    r = http_public.post("/tools/invoke", json={"tool": "SystemInfo", "input": {}})
    assert r.status_code == 401, r.text


def test_protected_route_accepts_present_token_local(http: httpx.Client) -> None:
    """Direct loopback keeps presence-only: the test token is accepted
    (the call may succeed or error inside the tool, but it is NOT a 401)."""
    r = http.post("/tools/invoke", json={"tool": "SystemInfo", "input": {}})
    assert r.status_code != 401, r.text


# ---------------------------------------------------------------------------
# Via tunnel — verified identity required
# ---------------------------------------------------------------------------


def test_tunnel_rejects_junk_token_on_protected_route(http_public: httpx.Client) -> None:
    """An unverifiable bearer arriving via the tunnel is rejected."""
    r = http_public.post(
        "/tools/invoke",
        json={"tool": "SystemInfo", "input": {}},
        headers={**_TUNNEL_HEADERS, "Authorization": "Bearer not-a-real-jwt"},
    )
    assert r.status_code == 401, r.text


def test_tunnel_rejects_junk_token_on_local_bootstrap_route(http_public: httpx.Client) -> None:
    """Local-bootstrap routes (permissive on loopback) require a verified
    identity when reached over the tunnel — here GET /settings."""
    r = http_public.get(
        "/settings",
        headers={**_TUNNEL_HEADERS, "Authorization": "Bearer not-a-real-jwt"},
    )
    assert r.status_code == 401, r.text


def test_local_llm_bridge_status_is_loopback_bootstrap(http_public: httpx.Client) -> None:
    """The desktop can reconcile an already-running llama-server before the
    webview token provider has hydrated."""
    r = http_public.get("/chat/local-llm/status")
    assert r.status_code == 200, r.text


def test_tunnel_rejects_junk_token_on_local_llm_bridge(http_public: httpx.Client) -> None:
    """The loopback-only local LLM bootstrap exception must not expose the
    bridge over a Cloudflare tunnel."""
    r = http_public.get(
        "/chat/local-llm/status",
        headers={**_TUNNEL_HEADERS, "Authorization": "Bearer not-a-real-jwt"},
    )
    assert r.status_code == 401, r.text


def test_tunnel_missing_token_rejected(http_public: httpx.Client) -> None:
    r = http_public.post(
        "/tools/invoke",
        json={"tool": "SystemInfo", "input": {}},
        headers=_TUNNEL_HEADERS,
    )
    assert r.status_code == 401, r.text
