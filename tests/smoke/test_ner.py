"""NER subsystem smoke tests that do not require downloaded models."""

from __future__ import annotations

import httpx


def test_ner_status_returns_struct(http: httpx.Client) -> None:
    r = http.get("/ner/status")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "active_model_id",
        "default_model_id",
        "model_loaded",
        "model_downloaded",
        "needs_download",
        "model_dir",
        "deps",
    ):
        assert key in body, f"missing key: {key}"
    assert body["default_model_id"] == "gliner2-base"
    assert "modules" in body["deps"]


def test_ner_models_include_server_default(http: httpx.Client) -> None:
    r = http.get("/ner/models")
    assert r.status_code == 200, r.text
    models = r.json()
    default = next((m for m in models if m["model_id"] == "gliner2-base"), None)
    assert default is not None
    assert default["repo_id"] == "fastino/gliner2-base-v1"
    assert default["backend"] == "gliner2"
    assert default["default"] is True


def test_ner_extract_validation_before_model_load(http: httpx.Client) -> None:
    r = http.post(
        "/ner/extract",
        json={"text": "Apple hired Jane Doe.", "labels": []},
    )
    assert r.status_code == 422, r.text


def test_ner_extract_invalid_overlap_is_client_error(http: httpx.Client) -> None:
    r = http.post(
        "/ner/extract",
        json={
            "text": "Apple hired Jane Doe.",
            "labels": ["company", "person"],
            "max_chars": 500,
            "overlap_chars": 500,
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "invalid_overlap"


def test_ner_extract_reports_missing_model_or_dependency(http: httpx.Client) -> None:
    r = http.post(
        "/ner/extract",
        json={"text": "Apple hired Jane Doe.", "labels": ["company", "person"]},
    )
    assert r.status_code in (409, 503), r.text
    body = r.json()
    assert body["detail"]["code"] in {"model_not_downloaded", "missing_dependency"}
