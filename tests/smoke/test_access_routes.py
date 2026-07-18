"""HTTP contract of the /access surface against a real engine.

The engine fixture (tests/conftest.py) boots the real app, whose lifespan
registers notes-canonical (+ mappings) and files-replica and probes them, so
these endpoints must return populated, well-formed payloads.
"""

from __future__ import annotations

import httpx


def _assert_health_shape(data: dict) -> None:
    assert isinstance(data.get("generation"), int)
    assert isinstance(data.get("platform"), str)
    assert isinstance(data.get("degraded"), bool)
    assert isinstance(data.get("resources"), list)
    ids = {r["resource_id"] for r in data["resources"]}
    assert "notes-canonical" in ids, f"resources registered: {ids}"
    assert "files-replica" in ids, f"resources registered: {ids}"
    for res in data["resources"]:
        assert res["status"] in ("ok", "degraded", "unknown")
        assert isinstance(res["root"], str) and res["root"]
        assert isinstance(res["capabilities"], dict)
        assert isinstance(res["message"], str) and res["message"]
        assert res["provenance"] in ("default", "override", "fallback", "mapped")
    if data["platform"] == "darwin":
        assert data["fda"] is not None
        assert data["fda"]["status"] in ("granted", "denied", "indeterminate")
        assert data["fda"]["source"] == "engine-process probe"


def test_access_health_shape(http: httpx.Client) -> None:
    r = http.get("/access/health")
    assert r.status_code == 200, r.text
    _assert_health_shape(r.json())


def test_access_recheck_probes(http: httpx.Client) -> None:
    r = http.post("/access/recheck", json={"resource_ids": ["notes-canonical"]})
    assert r.status_code == 200, r.text
    data = r.json()
    _assert_health_shape(data)
    notes = next(
        res for res in data["resources"] if res["resource_id"] == "notes-canonical"
    )
    # An active probe records the full capability set (the old scandir-only
    # probe couldn't see a readable-but-unwritable dir).
    assert set(notes["capabilities"]) == {
        "enumerate", "read", "create", "write", "replace", "delete",
    }


def test_access_recheck_no_body(http: httpx.Client) -> None:
    r = http.post("/access/recheck")
    assert r.status_code == 200, r.text
    _assert_health_shape(r.json())


def test_access_reset_bumps_generation(http: httpx.Client) -> None:
    before = http.get("/access/health").json()["generation"]
    r = http.post("/access/reset")
    assert r.status_code == 200, r.text
    data = r.json()
    _assert_health_shape(data)
    assert data["generation"] > before
