"""A TLS connection that dies on the wire is retried once, not called offline.

WHY (SR-09, measured over the 72h to 2026-09-14): 394 deliveries of
/api/coding-sessions/bridge failed with

    [SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac

and 232 more with "Cannot reach". The shape of the data rules out the three
usual suspects: this client opens a FRESH httpx.AsyncClient per request (no
shared client, no keep-alive reuse across a suspend), the app configures no
proxy, and the failures are 370 DISTINCT rows spread evenly over three days,
never more than five in a minute — an individual brand-new TLS connection
failing about once in two hundred, on the network path. The client cannot
prevent that.

What it was doing wrong was calling it "the server is unreachable": 326 of
those 370 rows failed exactly once and were delivered on their very next
attempt, after an outbox backoff (2s → 64s) and a WARNING line each. One
immediate retry on a new connection turns that into a delivery.
"""

from __future__ import annotations

import ssl

import httpx
import pytest

from app.services.aidream import client as client_module
from app.services.aidream.client import (
    AIDreamClient,
    AIDreamOfflineError,
    AIDreamTimeoutError,
)

BASE = "https://aidream.test"
JWT = "test-jwt"
BRIDGE = "/coding-sessions/bridge"


# The counters are read through these helpers, never touched directly, so that
# against a build WITHOUT the fix this file fails on the BEHAVIOUR under test
# (a bad-record-mac delivery being called offline) rather than on an
# AttributeError while setting up. A guard whose red is an import-time typo has
# not proven anything about the system.
def _stats() -> dict[str, int]:
    getter = getattr(client_module, "wire_retry_stats", None)
    return getter() if getter else {"retried": 0, "recovered": 0}


@pytest.fixture(autouse=True)
def _reset_counters():
    counts = getattr(client_module, "_wire_retry_counts", None)
    if counts is not None:
        counts.update({"retried": 0, "recovered": 0})
    yield


@pytest.fixture
def resolves_org(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_jwt: str) -> str:
        return "11111111-2222-4333-8444-555555555555"

    monkeypatch.setattr(
        client_module, "resolve_active_organization_id", _resolve, raising=False
    )
    from app.services.aidream import organization as organization_module

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", _resolve
    )


def _transport(*outcomes: object) -> httpx.MockTransport:
    """Each outcome is either an exception to raise or a Response to return."""
    remaining = list(outcomes)

    def handler(request: httpx.Request) -> httpx.Response:
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]

    return httpx.MockTransport(handler)


def _bad_record_mac() -> ssl.SSLError:
    return ssl.SSLError(
        1, "[SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac (_ssl.c:2660)"
    )


@pytest.mark.anyio
async def test_a_bad_record_mac_on_a_bridge_delivery_is_retried_and_recovers(
    resolves_org: None,
) -> None:
    """THE SR-09 case: 88% of these succeeded on the next attempt anyway."""
    transport = _transport(_bad_record_mac(), httpx.Response(200, json={"ok": True}))
    client = AIDreamClient(BASE, transport=transport)

    assert await client.post(BRIDGE, {"entries": []}, jwt=JWT) == {"ok": True}
    assert _stats() == {"retried": 1, "recovered": 1}


@pytest.mark.anyio
async def test_two_wire_failures_in_a_row_are_still_offline(
    resolves_org: None,
) -> None:
    """The retry is one retry, not a loop — a real outage must still surface
    so the outbox defers the row and backs off."""
    transport = _transport(_bad_record_mac(), _bad_record_mac())
    client = AIDreamClient(BASE, transport=transport)

    with pytest.raises(AIDreamOfflineError) as excinfo:
        await client.post(BRIDGE, {"entries": []}, jwt=JWT)

    assert "Transport failure reaching" in str(excinfo.value)
    assert _stats()["recovered"] == 0


@pytest.mark.anyio
async def test_cannot_reach_keeps_its_exact_words(resolves_org: None) -> None:
    """Callers and log greps match on these sentences; the retry must not
    rename the two classes."""
    transport = _transport(
        httpx.ConnectError("no route to host"), httpx.ConnectError("no route to host")
    )
    client = AIDreamClient(BASE, transport=transport)

    with pytest.raises(AIDreamOfflineError) as excinfo:
        await client.post(BRIDGE, {"entries": []}, jwt=JWT)
    assert "Cannot reach" in str(excinfo.value)


@pytest.mark.anyio
async def test_a_non_replay_safe_post_is_never_repeated(resolves_org: None) -> None:
    """A record-mac failure cannot prove the request never landed, so a POST is
    only repeated where a replay is provably safe. Repeating an arbitrary write
    would be a data bug traded for a log line."""
    transport = _transport(_bad_record_mac(), httpx.Response(200, json={"ok": True}))
    client = AIDreamClient(BASE, transport=transport)

    with pytest.raises(AIDreamOfflineError):
        await client.post("/v2/runtime/open", {}, jwt=JWT)
    assert _stats() == {"retried": 0, "recovered": 0}


@pytest.mark.anyio
async def test_every_get_is_retried(resolves_org: None) -> None:
    transport = _transport(_bad_record_mac(), httpx.Response(200, json=[1]))
    client = AIDreamClient(BASE, transport=transport)

    assert await client.get("/ai-models") == [1]
    assert _stats()["recovered"] == 1


@pytest.mark.anyio
async def test_a_timeout_is_never_retried(resolves_org: None) -> None:
    """A timeout leaves the remote outcome UNKNOWN — the one failure this
    transport must never repeat, and the reason AIDreamTimeoutError exists."""
    transport = _transport(
        httpx.ReadTimeout("too slow"), httpx.Response(200, json={"ok": True})
    )
    client = AIDreamClient(BASE, transport=transport)

    with pytest.raises(AIDreamTimeoutError):
        await client.post(BRIDGE, {"entries": []}, jwt=JWT)
    assert _stats() == {"retried": 0, "recovered": 0}


@pytest.mark.anyio
async def test_an_http_status_is_never_retried(resolves_org: None) -> None:
    """409 (a replayed envelope the server already has) and 500 are answers,
    not wire failures. Re-sending them would double the load for nothing."""
    from app.services.aidream.client import AIDreamError

    transport = _transport(
        httpx.Response(409, json={"error": "duplicate"}),
        httpx.Response(200, json={"ok": True}),
    )
    client = AIDreamClient(BASE, transport=transport)

    with pytest.raises(AIDreamError) as excinfo:
        await client.post(BRIDGE, {"entries": []}, jwt=JWT)
    assert excinfo.value.status == 409
    assert _stats() == {"retried": 0, "recovered": 0}


@pytest.mark.anyio
async def test_the_retry_is_never_silent(resolves_org: None) -> None:
    """A network path dropping connections is a real fact about this machine;
    a retry that hid it would be as bad as the 394 WARNINGs that buried it."""
    transport = _transport(_bad_record_mac(), httpx.Response(200, json={"ok": True}))
    client = AIDreamClient(BASE, transport=transport)
    await client.post(BRIDGE, {"entries": []}, jwt=JWT)

    stats = _stats()
    assert stats["retried"] == 1 and stats["recovered"] == 1
