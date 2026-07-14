"""Characterization tests for NER result normalization."""

from __future__ import annotations

import asyncio

import pytest

from app.services.ner.service import _chunk_text, _dedupe_spans, _normalise_entities
from app.services.ner.service import (
    SNAPSHOT_COMPLETE_MARKER,
    NerError,
    NerService,
    _has_model_files,
)
from app.api.ner_routes import NerExtractRequest
from app.api.capabilities_routes import CAPABILITY_SPECS
from app.services.capabilities.installer import CAPABILITY_INSTALL
from app.tools.tool_schemas import generate_tool_schema


def test_normalise_gliner2_grouped_entities_with_spans_and_confidence() -> None:
    text = "Apple CEO Tim Cook spoke in Cupertino."
    raw = {
        "entities": {
            "company": [{"text": "Apple", "start": 0, "end": 5, "confidence": 0.95}],
            "person": [{"text": "Tim Cook", "start": 10, "end": 18, "confidence": 0.92}],
        }
    }

    spans = _normalise_entities(raw, text)

    assert [(s.text, s.label, s.start, s.end, s.score) for s in spans] == [
        ("Apple", "company", 0, 5, 0.95),
        ("Tim Cook", "person", 10, 18, 0.92),
    ]


def test_normalise_grouped_strings_finds_offsets_in_order() -> None:
    text = "Apple met Apple in Cupertino."
    raw = {"entities": {"company": ["Apple", "Apple"], "location": ["Cupertino"]}}

    spans = _normalise_entities(raw, text)

    assert [(s.text, s.label, s.start, s.end) for s in spans] == [
        ("Apple", "company", 0, 5),
        ("Apple", "company", 10, 15),
        ("Cupertino", "location", 19, 28),
    ]


def test_chunk_text_keeps_global_offsets_and_dedupe_applies_threshold() -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    chunks = _chunk_text(text, max_chars=24, overlap_chars=6)
    assert chunks[0][1] == 0
    assert chunks[1][1] > 0
    assert text[chunks[1][1] : chunks[1][1] + len(chunks[1][0])] == chunks[1][0]

    spans = _normalise_entities(
        [{"text": "beta", "label": "token", "start": 6, "end": 10, "score": 0.9}],
        text,
    )
    spans += _normalise_entities(
        [{"text": "beta", "label": "token", "start": 6, "end": 10, "score": 0.4}],
        text,
    )

    deduped = _dedupe_spans(spans, threshold=0.5)
    assert len(deduped) == 1
    assert deduped[0].score == 0.9


def test_legacy_tool_schema_preserves_union_label_shape() -> None:
    schema = generate_tool_schema("ExtractEntities")
    assert schema is not None
    labels = schema["input_schema"]["properties"]["labels"]
    assert labels == {
        "anyOf": [
            {"type": "array", "items": {"type": "string"}},
            {"type": "object"},
        ]
    }


def test_model_presence_requires_complete_snapshot_marker(tmp_path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")
    (model_dir / "tokenizer.json").write_text("{}")
    (model_dir / "model.safetensors").write_text("weights")

    assert not _has_model_files(model_dir)

    (model_dir / SNAPSHOT_COMPLETE_MARKER).write_text("repo/id")
    assert _has_model_files(model_dir)


def test_extract_request_preserves_offsets_for_whitespace() -> None:
    req = NerExtractRequest(text="  Apple hired Jane Doe.  ", labels=["company", "person"])
    assert req.text == "  Apple hired Jane Doe.  "


def test_service_validates_threshold_before_model_load() -> None:
    svc = NerService()
    with pytest.raises(NerError) as exc:
        asyncio.run(_extract_with_bad_threshold(svc))
    assert exc.value.code == "invalid_threshold"


async def _extract_with_bad_threshold(svc: NerService) -> None:
    await svc.extract(text="Apple", labels=["company"], threshold=1.1)


def test_ner_capability_has_managed_installer() -> None:
    assert "ner" in CAPABILITY_SPECS
    assert "ner" in CAPABILITY_INSTALL
    assert "gliner2[local]>=1.3.2" in CAPABILITY_INSTALL["ner"]["packages"]
    assert CAPABILITY_INSTALL["ner"]["needs_torch_index"] is True
