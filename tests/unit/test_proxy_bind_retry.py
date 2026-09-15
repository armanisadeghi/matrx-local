"""A taken proxy port moves the proxy, it does not remove the proxy.

WHY (observed live 2026-09-14/15): the proxy port is derived from the engine's
own port (22241 → 22281). When two engines raced the same ENGINE port, they
also derived the same proxy port, and the loser's Phase 4 ended as

    proxy → ✗ FAILED — port 22281 in use: [errno 48] address already in use
    GET /health → {"health": "failed_services", "failed": ["proxy"]}

for the whole session. The proxy's own `_find_available_port()` scan existed
but only ran when NO port was requested, and a requested port that turned out
to be taken was a hard failure — a check-then-bind race with no retry at the
only moment that matters, the bind.

Now a bind refusal walks forward through the same scan range, announces the
move, and the engine reports the port it actually bound. Only exhausting the
range fails.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from app.services.proxy.server import ProxyServer

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _squat(port: int) -> socket.socket:
    """Hold a port the way a rival engine's proxy would."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


@pytest.mark.anyio
async def test_a_requested_port_that_is_taken_moves_the_proxy_forward() -> None:
    """THE live case: the second engine derived 22281, someone had it, and it
    ended up with no proxy at all."""
    wanted = _free_port()
    rival = _squat(wanted)
    proxy = ProxyServer()
    try:
        bound = await proxy.start(port=wanted)

        assert bound != wanted, "the requested port was held by the test"
        assert proxy.running is True
        assert proxy.port == bound, "the proxy must report the port it bound"
        assert wanted < bound < wanted + 10
    finally:
        await proxy.stop()
        rival.close()


@pytest.mark.anyio
async def test_the_bound_port_is_really_listening() -> None:
    """Reporting a port is not the same as serving on it."""
    wanted = _free_port()
    rival = _squat(wanted)
    proxy = ProxyServer()
    try:
        bound = await proxy.start(port=wanted)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(("127.0.0.1", bound))  # raises if nothing listens
        assert proxy.stats["port"] == bound
    finally:
        await proxy.stop()
        rival.close()


@pytest.mark.anyio
async def test_a_free_requested_port_is_used_exactly_as_asked() -> None:
    """The normal case must be untouched — no drifting off the derived port."""
    wanted = _free_port()
    proxy = ProxyServer()
    try:
        assert await proxy.start(port=wanted) == wanted
    finally:
        await proxy.stop()


@pytest.mark.anyio
async def test_an_exhausted_range_still_fails_loudly() -> None:
    """Moving forward is a repair, not a licence to pretend."""
    import app.services.proxy.server as server_module

    wanted = _free_port()
    squatters = [_squat(wanted)]
    monkey_scan = 2
    original = server_module.MAX_PORT_SCAN
    server_module.MAX_PORT_SCAN = monkey_scan
    try:
        try:
            squatters.append(_squat(wanted + 1))
        except OSError:
            pytest.skip("could not hold the second port in the range")
        proxy = ProxyServer()
        with pytest.raises(OSError):
            await proxy.start(port=wanted)
        assert proxy.running is False
    finally:
        server_module.MAX_PORT_SCAN = original
        for s in squatters:
            s.close()


def test_phase_4_reports_the_port_the_proxy_bound() -> None:
    """THE WIRING — a retry is worthless if /admin/status keeps naming the
    port we asked for."""
    main_src = (REPO_ROOT / "app" / "main.py").read_text()
    phase4 = main_src.split("# Phase 4: Start HTTP proxy", 1)[1][:3000]

    assert "bound_port = await proxy.start(" in phase4, (
        "Phase 4 must keep the port proxy.start() returns"
    )
    assert '_registry.ready("proxy", port=bound_port)' in phase4, (
        "the registry (and therefore /health and /admin/status) must carry the "
        "bound port, not the requested one"
    )
