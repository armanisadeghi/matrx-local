"""A second engine in the same world gets its own proxy port.

WHY (observed live 2026-09-14, two source engines on this Mac): the engine
picks its port dynamically — it scans its world's block (dev 22240–22259) and
takes the first free one, so the second dev engine bound 22241. The proxy port
was computed from the static PORT BASE instead of from the bound port, so both
engines wanted 22280 and the second one booted with

    proxy → ✗ FAILED — port 22280 in use: [errno 48] address already in use
    GET /health → {"health": "failed_services", "failed": ["proxy"]}

`ProxyServer._find_available_port()` would have scanned past it, but Phase 4
passes an explicit port, so it never ran. The +40 offset exists to keep the
proxy inside its world's block; taking it off the bound port does that AND
gives every engine its own proxy.

The pre-fix expression is reproduced by `_legacy_choice` below, so this file
fails on the OLD behaviour rather than on a missing import.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.proxy import server as proxy_server

DEV_BASE = 22240
LIVE_BASE = 22140


def _legacy_choice(engine_port: int, configured: int | None = None) -> int:
    """Pre-fix Phase 4: the stored setting (base + 40), engine port ignored."""
    return configured if configured is not None else proxy_server.DEFAULT_PROXY_PORT


def _choose(engine_port: int, configured: int | None = None) -> int:
    derive = getattr(proxy_server, "derive_proxy_port", None)
    if derive is None:
        return _legacy_choice(engine_port, configured)
    return derive(engine_port, configured)


@pytest.fixture
def dev_world(monkeypatch: pytest.MonkeyPatch) -> int:
    """A dev engine's world: port base 22240, shipped proxy default 22280."""
    monkeypatch.setattr(proxy_server, "DEFAULT_PROXY_PORT", DEV_BASE + 40)
    return DEV_BASE


def test_two_dev_engines_never_want_the_same_proxy_port(dev_world: int) -> None:
    """THE bug: the second engine's proxy could not bind, so it booted
    degraded with no proxy at all."""
    first = _choose(DEV_BASE, DEV_BASE + 40)
    second = _choose(DEV_BASE + 1, DEV_BASE + 40)

    assert first != second, (
        "both engines were handed the same proxy port, so the second one "
        "fails to bind and reports failed:['proxy'] for its whole session"
    )
    assert (first, second) == (22280, 22281)


def test_a_third_engine_keeps_stacking(dev_world: int) -> None:
    for offset in range(3):
        assert _choose(DEV_BASE + offset, DEV_BASE + 40) == 22280 + offset


def test_the_derived_port_stays_inside_its_worlds_block(dev_world: int) -> None:
    """The engine block is 20 ports wide (22240–22259); +40 off any of them
    lands in 22280–22299 and never in another world's range."""
    for offset in range(20):
        port = _choose(DEV_BASE + offset, DEV_BASE + 40)
        assert 22280 <= port <= 22299


def test_the_first_engine_is_unchanged_in_both_worlds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal case must produce exactly the port it always has."""
    monkeypatch.setattr(proxy_server, "DEFAULT_PROXY_PORT", LIVE_BASE + 40)
    assert _choose(LIVE_BASE, LIVE_BASE + 40) == 22180

    monkeypatch.setattr(proxy_server, "DEFAULT_PROXY_PORT", DEV_BASE + 40)
    assert _choose(DEV_BASE, DEV_BASE + 40) == 22280


def test_a_port_the_user_chose_is_never_overridden(dev_world: int) -> None:
    """Opinions are knobs: a value that is not this world's shipped default is
    a choice, and a choice wins over the derivation."""
    assert _choose(DEV_BASE + 1, 31337) == 31337


def test_an_unknown_engine_port_falls_back_to_the_shipped_default(
    dev_world: int,
) -> None:
    """Discovery unreadable ⇒ no bound port to offset from; the old static
    answer is still better than an invented one."""
    assert _choose(0, DEV_BASE + 40) == DEV_BASE + 40


def test_phase_4_actually_uses_the_derivation() -> None:
    """THE WIRING — the fix is worthless if main.py still passes the raw
    setting to proxy.start()."""
    main_src = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text()

    assert "derive_proxy_port(" in main_src, (
        "app/main.py Phase 4 must derive the proxy port from the engine's own "
        "bound port, not pass the stored setting through"
    )
    phase4 = main_src.split("# Phase 4: Start HTTP proxy", 1)[1][:2000]
    assert "derive_proxy_port(main_server_port" in phase4, (
        "the derivation must be fed the port this engine actually bound"
    )


def test_the_derivation_announces_itself() -> None:
    """Nothing intervenes silently: when the port is not the configured one,
    the log says so and says why."""
    main_src = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text()
    phase4 = main_src.split("# Phase 4: Start HTTP proxy", 1)[1][:2000]

    assert "proxy_port != configured_proxy_port" in phase4, (
        "a derived port that differs from the configured one must be announced"
    )
