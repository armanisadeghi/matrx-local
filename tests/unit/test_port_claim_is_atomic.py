"""A port this engine chose is a port this engine is holding.

WHY (observed live 2026-09-14/15 on this Mac): two source engines started 3
seconds apart both probed 22241 as free, both wrote a discovery file claiming
22241, both logged

    ── Startup complete in 20.9s — scraper=True, proxy=False ──

and only ONE of them had a listening socket. The loser sat there advertising a
port it did not own, serving nothing, with a tray icon and a healthy-looking
log.

Two mechanics produced that:

1. THE CHECK-THEN-BIND RACE. `_is_port_free()` binds a probe socket, closes
   it, and returns a number. Everything after — the orphan sweep's drain, the
   discovery-file write, and the whole of uvicorn's lifespan startup, which
   runs BEFORE uvicorn binds — happens with the port held by nobody.
2. A DEAD SERVER THREAD THAT SAID NOTHING. uvicorn's bind failure calls
   sys.exit(1) inside the server thread; the SystemExit died with the thread
   while the process stayed up.

The fix is to never let go: the scan binds and KEEPS the socket, hands it to
uvicorn, and a server thread that dies takes the process with it, loudly.

The pre-fix behaviour is reproduced by `_legacy_assign` below, so these cases
fail on the OLD mechanic rather than on a missing import.
"""

from __future__ import annotations

import ast
import copy
import os
import socket
import sys
from pathlib import Path

import pytest

from app import preflight

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    """A port nobody is using right now (kernel-assigned, then released)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _legacy_assign(base: int, scan: int) -> int:
    """Pre-fix: probe-bind, CLOSE, return the number."""
    for offset in range(scan):
        candidate = base + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if sys.platform != "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    raise SystemExit("no free port")


def _claim(base: int, scan: int) -> tuple[int, socket.socket | None]:
    """The seam under test, falling back to the pre-fix mechanic."""
    binder = getattr(preflight, "bind_engine_port", None)
    if binder is None:
        return _legacy_assign(base, scan), None
    return binder()


def _exception_fallback_binder() -> object:
    """Extract only run.py's preflight-exception fallback without importing it.

    The fallback exists precisely for an unavailable/broken preflight import, so
    importing application modules would not exercise that branch honestly.
    """
    module = ast.parse((REPO_ROOT / "run.py").read_text(), filename="run.py")
    main = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    fallback = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.FunctionDef) and node.name == "bind_engine_port"
    )
    extracted = ast.Module(body=[copy.deepcopy(fallback)], type_ignores=[])
    ast.fix_missing_locations(extracted)
    namespace = {
        "DEFAULT_PORT": _free_port(),
        "MAX_PORT_SCAN": 1,
        "os": os,
        "socket": socket,
        "sys": sys,
    }
    exec(compile(extracted, "run.py:fallback", "exec"), namespace)
    return namespace["bind_engine_port"]


@pytest.fixture
def scan_range(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    """Point the scan at a real, unused pair of ports on this machine."""
    base = _free_port()
    monkeypatch.setattr(preflight, "DEFAULT_ENGINE_PORT", base)
    monkeypatch.setattr(preflight, "ENGINE_PORT_SCAN", 8)
    monkeypatch.delenv("MATRX_PORT", raising=False)
    return base, 8


def test_the_chosen_port_is_held_so_a_rival_cannot_take_it(
    scan_range: tuple[int, int],
) -> None:
    """THE RACE, as the two engines hit it: whoever chose the port must own it
    the instant they are told the number."""
    base, scan = scan_range
    port, held = _claim(base, scan)

    rival = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    rival.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError):
            rival.bind(("127.0.0.1", port))
    finally:
        rival.close()
        if held is not None:
            held.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux SO_REUSEADDR contract")
def test_preflight_exception_fallback_keeps_an_exclusive_listening_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovery path must retain the same real socket claim as preflight.

    Before this repair, this exact extracted function bound but did not listen;
    a same-host rival with SO_REUSEADDR could bind the returned port on Linux.
    """
    monkeypatch.delenv("MATRX_PORT", raising=False)
    binder = _exception_fallback_binder()
    assert callable(binder)
    port, held = binder()
    rival = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    rival.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError):
            rival.bind(("127.0.0.1", port))
    finally:
        rival.close()
        held.close()


def test_a_second_engine_gets_a_different_port_and_holds_that(
    scan_range: tuple[int, int],
) -> None:
    """Two claims in a row — the second must not be handed the first's port,
    which is exactly what both engines were handed live."""
    base, scan = scan_range
    first_port, first_sock = _claim(base, scan)
    second_port, second_sock = _claim(base, scan)
    try:
        assert first_port != second_port, (
            "both engines were given the same port; the loser of the bind race "
            "then ran with no listening socket at all"
        )
    finally:
        for s in (first_sock, second_sock):
            if s is not None:
                s.close()


def test_a_port_held_by_someone_else_is_skipped(scan_range: tuple[int, int]) -> None:
    """A socket the test holds stands in for a live engine or foreign process."""
    base, scan = scan_range
    squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    squatter.bind(("127.0.0.1", base))
    squatter.listen(1)
    try:
        port, held = _claim(base, scan)
        assert port != base
        assert base < port < base + scan
        if held is not None:
            held.close()
    finally:
        squatter.close()


def test_an_exhausted_range_is_fatal_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never a silent fallback to an unbound port."""
    base = _free_port()
    squatters: list[socket.socket] = []
    monkeypatch.setattr(preflight, "DEFAULT_ENGINE_PORT", base)
    monkeypatch.setattr(preflight, "ENGINE_PORT_SCAN", 3)
    monkeypatch.delenv("MATRX_PORT", raising=False)
    try:
        for offset in range(3):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", base + offset))
                s.listen(1)
                squatters.append(s)
            except OSError:
                s.close()
        with pytest.raises(SystemExit) as excinfo:
            _claim(base, 3)
        assert "No free port" in str(excinfo.value) or "no free port" in str(
            excinfo.value
        )
    finally:
        for s in squatters:
            s.close()


def test_the_reporting_helper_is_labelled_as_unsafe_for_binding() -> None:
    """`assign_engine_port` still exists for `preflight.py ports`, and must say
    in its own docstring that it closes the socket — otherwise the next caller
    reintroduces the race by reading the name and assuming ownership."""
    doc = (preflight.assign_engine_port.__doc__ or "").lower()
    assert "reporting only" in doc or "never decide" in doc, (
        "a helper that returns a port it does not hold has to say so"
    )


def test_uvicorn_is_handed_the_bound_socket() -> None:
    """THE WIRING — holding the socket is pointless if the server opens its
    own."""
    run_src = (REPO_ROOT / "run.py").read_text()

    assert "bind_engine_port()" in run_src, (
        "run.py must claim the port with the socket-holding seam"
    )
    assert "server.run(sockets=" in run_src, (
        "the bound socket must be handed to uvicorn, or uvicorn binds again "
        "after the lifespan has already printed 'Startup complete'"
    )
    assert "start_server, args=(port, engine_socket)" in run_src


def test_a_dead_server_thread_takes_the_process_down_loudly() -> None:
    """The loser must never be left alive advertising a port it lost."""
    run_src = (REPO_ROOT / "run.py").read_text()
    start = run_src.index("def start_server(")
    body = run_src[start : start + 4000]

    assert "logger.critical" in body, (
        "a server thread that dies must be announced at CRITICAL — it was "
        "silent, and the log's last word was 'Startup complete'"
    )
    assert "remove_discovery_file()" in body, (
        "a process with no listener must stop advertising itself"
    )
    assert "os._exit(1)" in body, "a dead server thread must be fatal"

def test_a_dying_server_thread_really_exits_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property, not just the source text: when uvicorn's serve loop dies,
    the process stops advertising itself and exits non-zero.

    Pre-fix, uvicorn's own sys.exit(1) died with the thread: the process
    stayed up, the tray stayed up, the discovery file kept pointing at a port
    with no listener, and the last log line was "Startup complete".
    """
    import run

    exits: list[int] = []
    removed: list[bool] = []

    class DyingServer:
        def __init__(self, config: object) -> None:
            self.config = config

        def run(self, *args: object, **kwargs: object) -> None:
            raise OSError(48, "address already in use")

    class FakeConfig:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    monkeypatch.setattr(run.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(run.uvicorn, "Server", DyingServer)
    monkeypatch.setattr(run, "remove_discovery_file", lambda: removed.append(True))
    monkeypatch.setattr(run.os, "_exit", lambda code: exits.append(code))
    previous = run._uvicorn_server
    run._server_stopped_event.clear()
    run._shutdown_event.clear()
    try:
        run.start_server(22399)
    finally:
        run._uvicorn_server = previous
        run._server_stopped_event.clear()
        run._shutdown_event.clear()

    assert exits == [1], (
        "a server thread that died must take the process down with a non-zero "
        "code, not leave it running with nothing listening"
    )
    assert removed == [True], (
        "a process with no listener must delete its discovery file so nothing "
        "keeps dialling a dead engine"
    )
