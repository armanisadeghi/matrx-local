"""Characterization: discovery-file ownership guards (MXL-D-043).

Pins the contract that keeps dev and live engines from fighting over
local.json:

  1. write_discovery_file REFUSES to overwrite a file owned by a different
     pid that is alive AND answering /health at its recorded URL.
  2. write_discovery_file DOES overwrite when the recorded owner is dead or
     unhealthy (stale file reclaim).
  3. update_discovery_service only mutates a file whose recorded pid is ours.

The alive/healthy checks are monkeypatched — this pins the decision logic,
not psutil or HTTP.
"""

from __future__ import annotations

import json
import os

import pytest

from app import preflight


@pytest.fixture()
def discovery(tmp_path, monkeypatch):
    """Point the module-level DISCOVERY_FILE at a temp location."""
    path = tmp_path / "local.json"
    monkeypatch.setattr(preflight, "DISCOVERY_FILE", path)
    return path


def _seed(path, *, pid: int, port: int = 22140) -> None:
    path.write_text(
        json.dumps(
            {
                "port": port,
                "host": "127.0.0.1",
                "url": f"http://127.0.0.1:{port}",
                "ws": f"ws://127.0.0.1:{port}/ws",
                "pid": pid,
                "version": "9.9.9",
            }
        )
    )


def test_write_refuses_to_clobber_live_owner(discovery, monkeypatch):
    _seed(discovery, pid=111, port=22140)
    monkeypatch.setattr(preflight.psutil, "pid_exists", lambda p: p == 111)
    monkeypatch.setattr(preflight, "_probe_matrx_health", lambda url, **kw: True)

    preflight.write_discovery_file(engine_port=22241, pid=222, version="0.0.1")

    data = json.loads(discovery.read_text())
    assert data["pid"] == 111, "live owner must keep the discovery file"
    assert data["port"] == 22140


def test_write_reclaims_dead_owner(discovery, monkeypatch):
    _seed(discovery, pid=111)
    monkeypatch.setattr(preflight.psutil, "pid_exists", lambda p: False)

    preflight.write_discovery_file(engine_port=22241, pid=222, version="0.0.1")

    data = json.loads(discovery.read_text())
    assert data["pid"] == 222
    assert data["port"] == 22241


def test_write_reclaims_unhealthy_owner(discovery, monkeypatch):
    _seed(discovery, pid=111)
    monkeypatch.setattr(preflight.psutil, "pid_exists", lambda p: True)
    monkeypatch.setattr(preflight, "_probe_matrx_health", lambda url, **kw: False)

    preflight.write_discovery_file(engine_port=22241, pid=222, version="0.0.1")

    assert json.loads(discovery.read_text())["pid"] == 222


def test_write_allows_self_overwrite(discovery, monkeypatch):
    """Same pid rewriting its own file (e.g. tunnel came up) must succeed."""
    me = os.getpid()
    _seed(discovery, pid=me, port=22140)
    # Even if the probe says healthy, our own pid is never blocked.
    monkeypatch.setattr(preflight.psutil, "pid_exists", lambda p: True)
    monkeypatch.setattr(preflight, "_probe_matrx_health", lambda url, **kw: True)

    preflight.write_discovery_file(
        engine_port=22140, pid=me, version="0.0.2", tunnel_url="https://t.example"
    )

    data = json.loads(discovery.read_text())
    assert data["pid"] == me
    assert data["tunnel_url"] == "https://t.example"


def test_update_service_skipped_for_non_owner(discovery):
    _seed(discovery, pid=111)

    preflight.update_discovery_service("tunnel", {"url": "https://evil.example"})

    data = json.loads(discovery.read_text())
    assert "tunnel" not in data.get("services", {})
    assert "tunnel_url" not in data


def test_update_service_allowed_for_owner(discovery):
    _seed(discovery, pid=os.getpid())

    preflight.update_discovery_service("tunnel", {"url": "https://t.example"})

    data = json.loads(discovery.read_text())
    assert data["services"]["tunnel"]["url"] == "https://t.example"
    assert data["tunnel_url"] == "https://t.example"
