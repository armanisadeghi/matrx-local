"""Request auth verifies Supabase sessions LOCALLY, against the project JWKS.

These tests are the guard for the 2026-09-20 conversion described in
``app/api/remote_auth.py`` and in
``common-docs/systems/platform/proxy-identity/FEATURE.md``:

  1. verification never makes a per-request auth-server round trip
     (``GET /auth/v1/user`` — the old implementation's only move),
  2. the resolve is bounded, and
  3. an authority we could not REACH is never read as a signed-out person.

Every test runs against a REAL HTTP server that records every path it is
asked for, and a REAL ES256 key pair — so "no round trip" is proven by the
server's own log, not by a mock. The previous, GoTrue-introspecting version
of this module fails :func:`test_verification_never_calls_the_auth_server`
head-on: it would ask for ``/auth/v1/user`` and never fetch the key set.
"""
from __future__ import annotations

import http.server
import json
import threading
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.api import remote_auth as subject

@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


USER_ID = "11111111-1111-4111-8111-111111111111"
KID = "test-es256-key"


# ---------------------------------------------------------------------------
# A real key pair, a real JWKS document, a real server
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="module")
def jwks_document(signing_key) -> dict:
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "ES256"})
    return {"keys": [jwk]}


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Serves the JWKS document and records every path asked of it."""

    document: dict = {}
    paths: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        type(self).paths.append(self.path)
        if self.path.endswith("/.well-known/jwks.json"):
            body = json.dumps(type(self).document).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args) -> None:  # silence the stdlib access log
        return


def _reset_engine_state(monkeypatch, base: str) -> None:
    """Point the engine at ``base`` and clear every verification cache.

    Deliberately tolerant of attributes a *different* implementation of
    ``remote_auth`` may not have: this fixture must set an old, round-tripping
    version up correctly enough to REACH the assertions and fail on its
    behavior, not blow up in setup and look like a fixture bug.
    """
    monkeypatch.setattr(subject, "SUPABASE_URL", base)
    if hasattr(subject, "SUPABASE_PUBLISHABLE_KEY"):
        monkeypatch.setattr(subject, "SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")
    for name in (
        "_verify_cache",
        "_jwks_client_cache",
        "_jwks_keys",
        "_unknown_kid_until",
        "_unverifiable_alg_logged",
    ):
        cache = getattr(subject, name, None)
        if cache is not None:
            cache.clear()
    for name in ("_jwks_failure_until", "_jwks_last_refresh"):
        if hasattr(subject, name):
            setattr(subject, name, 0.0)


@pytest.fixture
def issuer(jwks_document, monkeypatch):
    """Run the project's auth origin on loopback and point the engine at it."""
    _RecordingHandler.document = jwks_document
    _RecordingHandler.paths = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    _reset_engine_state(monkeypatch, base)

    try:
        yield server, base, _RecordingHandler.paths
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass  # a test may already have closed it on purpose
        _reset_engine_state(monkeypatch, base)


def mint(signing_key, **overrides) -> str:
    claims = {
        "sub": USER_ID,
        "email": "admin@admin.com",
        "role": "authenticated",
        "aud": "authenticated",
        "is_anonymous": False,
        "iat": int(time.time()) - 5,
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="ES256", headers={"kid": KID})


# ---------------------------------------------------------------------------
# Rule 1 — local verification, never a per-request auth-server round trip
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verification_never_calls_the_auth_server(issuer, signing_key):
    """The guard. A round-trip implementation cannot pass this.

    Verifying three different tokens must fetch the key set at most once and
    must NEVER ask ``/auth/v1/user`` — the endpoint the retired implementation
    called on every cache miss.
    """
    _server, _base, paths = issuer

    for index in range(3):
        result = await subject.verify_supabase_token_result(
            mint(signing_key, sub=f"{USER_ID[:-1]}{index}")
        )
        assert result.status == "verified", result

    assert not [p for p in paths if "/auth/v1/user" in p], (
        f"request auth made an auth-server round trip: {paths}"
    )
    jwks_fetches = [p for p in paths if p.endswith("/.well-known/jwks.json")]
    assert jwks_fetches, "the project key set was never fetched — nothing was verified locally"
    assert len(jwks_fetches) == 1, f"key set re-fetched per request: {paths}"


@pytest.mark.anyio
async def test_verified_user_carries_the_standard_claims(issuer, signing_key):
    result = await subject.verify_supabase_token_result(mint(signing_key))
    assert result.status == "verified"
    assert result.user == subject.VerifiedUser(
        user_id=USER_ID, email="admin@admin.com", is_anon=False
    )


@pytest.mark.anyio
async def test_anonymous_session_is_marked_anonymous(issuer, signing_key):
    token = mint(signing_key, role="anon", is_anonymous=True, email=None)
    result = await subject.verify_supabase_token_result(token)
    assert result.status == "verified"
    assert result.user is not None and result.user.is_anon is True


# ---------------------------------------------------------------------------
# A bad token is still rejected — locally, on the cryptography
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_token_signed_by_another_key_is_invalid(issuer):
    stranger = ec.generate_private_key(ec.SECP256R1())
    forged = jwt.encode(
        {"sub": USER_ID, "exp": int(time.time()) + 3600},
        stranger,
        algorithm="ES256",
        headers={"kid": KID},
    )
    result = await subject.verify_supabase_token_result(forged)
    assert result.status == "invalid"
    assert result.user is None


@pytest.mark.anyio
async def test_expired_token_is_invalid(issuer, signing_key):
    result = await subject.verify_supabase_token_result(
        mint(signing_key, iat=int(time.time()) - 7200, exp=int(time.time()) - 60)
    )
    assert result.status == "invalid"


@pytest.mark.anyio
async def test_garbage_is_invalid(issuer):
    assert (await subject.verify_supabase_token_result("not-a-jwt")).status == "invalid"
    assert (await subject.verify_supabase_token_result("")).status == "invalid"


@pytest.mark.anyio
async def test_verified_token_is_never_cached_past_its_expiry(issuer, signing_key):
    """A short-lived token must not keep passing on a warm cache."""
    token = mint(signing_key, exp=int(time.time()) + 1)
    assert (await subject.verify_supabase_token_result(token)).status == "verified"
    time.sleep(1.2)
    assert (await subject.verify_supabase_token_result(token)).status == "invalid"


# ---------------------------------------------------------------------------
# Rule 3 — an authority we could not REACH is never a signed-out person
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unreachable_key_set_is_unavailable_not_invalid(issuer, signing_key):
    """The lockout this conversion exists to end.

    A network blip must report "could not check", so callers that persist
    credentials keep the user's session. ``invalid`` here would sign a user
    out of their own desktop app over a dropped packet.
    """
    server, _base, _paths = issuer
    token = mint(signing_key)
    server.shutdown()
    server.server_close()

    result = await subject.verify_supabase_token_result(token)
    assert result.status == "unavailable", result
    assert result.user is None
    # And it is not remembered as a verdict about the token.
    assert subject._token_key(token) not in subject._verify_cache


@pytest.mark.anyio
async def test_a_valid_session_survives_a_blip_on_the_warm_path(issuer, signing_key):
    """A token verified before the blip keeps working through it."""
    server, _base, _paths = issuer
    token = mint(signing_key)
    assert (await subject.verify_supabase_token_result(token)).status == "verified"

    server.shutdown()
    server.server_close()
    assert (await subject.verify_supabase_token_result(token)).status == "verified"


@pytest.mark.anyio
async def test_unknown_kid_is_unavailable_and_costs_one_fetch(issuer, signing_key):
    """No published key for this kid: we did not check it, so no verdict.

    And a flood of invented kids — which needs no credentials at all — must
    not become a fetch per request against the project's JWKS.
    """
    _server, _base, paths = issuer
    stranger = ec.generate_private_key(ec.SECP256R1())

    def with_kid(kid: str) -> str:
        return jwt.encode(
            {"sub": USER_ID, "exp": int(time.time()) + 3600},
            stranger,
            algorithm="ES256",
            headers={"kid": kid},
        )

    assert (await subject.verify_supabase_token_result(with_kid("never-published"))).status == (
        "unavailable"
    )
    fetches_after_first = len(paths)
    assert fetches_after_first >= 1

    # 50 DIFFERENT invented kids, none of them cached by token: still no more
    # fetches, because the key-set refresh is one-per-cooldown for everyone.
    for index in range(50):
        result = await subject.verify_supabase_token_result(with_kid(f"invented-{index}"))
        assert result.status == "unavailable"
    assert len(paths) == fetches_after_first, (
        f"unknown kids amplified into repeated key-set fetches: {paths}"
    )

    # ...and a token whose key IS published still verifies straight through,
    # from the key map, while that cooldown is running.
    assert (await subject.verify_supabase_token_result(mint(signing_key))).status == "verified"


@pytest.mark.anyio
async def test_a_known_key_verifies_with_the_issuer_down_and_a_cold_token_cache(
    issuer, signing_key
):
    """The blip must cost nothing to anyone whose key we already hold.

    Not the token cache — the KEY cache. A guard that leans on the 60s token
    cache proves nothing about a token minted during the outage.
    """
    server, _base, _paths = issuer
    assert (await subject.verify_supabase_token_result(mint(signing_key))).status == "verified"

    server.shutdown()
    server.server_close()
    subject._verify_cache.clear()  # cold token cache, warm key map

    fresh = mint(signing_key, sub=USER_ID.replace("1111-1111", "1111-2222"))
    assert (await subject.verify_supabase_token_result(fresh)).status == "verified"


def test_the_algorithm_allow_list_is_public_key_only():
    """The single most load-bearing line in the module, pinned.

    Behaviour alone cannot guard this: adding "HS256" to the list does not
    make an HS256 token verify (PyJWT refuses an EC key to HMAC downstream),
    so every behavioural assertion stays green while the contract is gone.
    The allow-list IS the contract — a symmetric algorithm in it means this
    engine claims to hold a secret it must never hold.
    """
    assert set(subject.VERIFY_ALGORITHMS) == {"ES256", "RS256"}


@pytest.mark.anyio
@pytest.mark.parametrize("alg", ["none", "HS384", "HS512", "EVIL", "RS512", "ES384"])
async def test_an_algorithm_we_do_not_accept_is_a_verdict_not_a_shrug(issuer, alg):
    """Refusing an algorithm is a VERDICT — it must not read as "unchecked".

    This is not pedantry. ``/extension/*`` downgrades "could not check" to a
    presence-only principal on loopback, so reporting these as ``unavailable``
    let an RS512 token with a garbage signature in where it had been rejected.
    """
    import base64

    def seg(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    token = (
        f"{seg({'alg': alg, 'typ': 'JWT', 'kid': KID})}."
        f"{seg({'sub': USER_ID, 'exp': int(time.time()) + 3600})}.c2lnbmF0dXJl"
    )
    result = await subject.verify_supabase_token_result(token)
    assert result.status == "invalid", f"alg={alg} -> {result.status}"
    assert result.user is None


@pytest.mark.anyio
async def test_an_asymmetric_token_naming_no_key_is_a_verdict(issuer):
    token = jwt.encode(
        {"sub": USER_ID, "exp": int(time.time()) + 3600},
        ec.generate_private_key(ec.SECP256R1()),
        algorithm="ES256",
    )
    assert (await subject.verify_supabase_token_result(token)).status == "invalid"


@pytest.mark.anyio
async def test_an_invented_algorithm_cannot_grow_the_log_set_without_bound(issuer):
    """``alg`` is attacker text on a PRE-AUTH path: no unbounded state on it."""
    import base64

    for index in range(200):
        head = base64.urlsafe_b64encode(
            json.dumps({"alg": f"EVIL{index}", "typ": "JWT", "kid": KID}).encode()
        ).decode().rstrip("=")
        await subject.verify_supabase_token_result(f"{head}.e30.c2ln")
    assert len(subject._unverifiable_alg_logged) <= subject._MAX_LOGGED_ALGS


@pytest.mark.anyio
async def test_hs256_token_is_unverifiable_never_invalid(issuer):
    """This engine holds no symmetric secret, so it cannot judge an HS256 token."""
    legacy = jwt.encode(
        {"sub": USER_ID, "exp": int(time.time()) + 3600},
        "a-symmetric-secret-this-engine-must-never-hold",
        algorithm="HS256",
    )
    result = await subject.verify_supabase_token_result(legacy)
    assert result.status == "unavailable"
    assert result.user is None


@pytest.mark.anyio
async def test_unconfigured_engine_says_so(monkeypatch):
    monkeypatch.setattr(subject, "SUPABASE_URL", "")
    subject._verify_cache.clear()
    result = await subject.verify_supabase_token_result("anything")
    assert result.status == "unconfigured"
    assert subject.missing_supabase_config() == ["SUPABASE_URL"]


# ---------------------------------------------------------------------------
# The public wrapper keeps its fail-closed contract
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_supabase_token_returns_none_on_every_failure(issuer, signing_key):
    assert (await subject.verify_supabase_token(mint(signing_key))) is not None
    assert (await subject.verify_supabase_token("not-a-jwt")) is None


@pytest.mark.anyio
async def test_invalidate_token_drops_the_cached_identity(issuer, signing_key):
    token = mint(signing_key)
    assert (await subject.verify_supabase_token_result(token)).status == "verified"
    assert subject._token_key(token) in subject._verify_cache
    subject.invalidate_token(token)
    assert subject._token_key(token) not in subject._verify_cache


# ---------------------------------------------------------------------------
# The gates that consume the verdict must not turn "could not check" into
# "your session is over". They still REFUSE — they just tell the truth.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tunnel_gate_refuses_honestly_when_the_issuer_is_unreachable(
    issuer, signing_key, monkeypatch
):
    import httpx
    from fastapi import FastAPI

    from app.api.auth import AuthMiddleware

    server, _base, _paths = issuer
    good = mint(signing_key)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/tools/run")
    async def _protected() -> dict:
        return {"ok": True}

    tunnel = {"Authorization": f"Bearer {good}", "Cf-Ray": "test-ray"}
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 43210))

    async with httpx.AsyncClient(transport=transport, base_url="http://engine.test") as http:
        # A token the project's key set refuses is a real verdict: 401.
        stranger = ec.generate_private_key(ec.SECP256R1())
        forged = jwt.encode(
            {"sub": USER_ID, "exp": int(time.time()) + 3600},
            stranger,
            algorithm="ES256",
            headers={"kid": KID},
        )
        bad = await http.get(
            "/tools/run",
            headers={"Authorization": f"Bearer {forged}", "Cf-Ray": "test-ray"},
        )
        assert bad.status_code == 401
        assert "invalid or expired" in bad.json()["detail"].lower()

        # The issuer going away is NOT that. Still refused — but never as
        # "invalid or expired credentials", which sends a user who is signed in
        # perfectly well off to sign in again.
        # Cold caches AND a dead issuer: the key set cannot be reached at all.
        # (With a warm key cache the blip would not even be felt — which is the
        # other half of the win.)
        server.shutdown()
        server.server_close()
        subject._verify_cache.clear()
        subject._jwks_client_cache.clear()
        subject._jwks_keys.clear()
        subject._jwks_last_refresh = 0.0
        blip = await http.get("/tools/run", headers=tunnel)
        assert blip.status_code == 503, blip.text
        detail = blip.json()["detail"].lower()
        assert "not been signed out" in detail
        assert "invalid" not in detail
