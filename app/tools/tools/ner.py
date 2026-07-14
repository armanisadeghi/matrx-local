"""Local entity extraction tools backed by the NER service."""

from __future__ import annotations

from app.services.ner.service import NerError, get_ner_service
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType


def _result_output(entities: list[dict]) -> str:
    if not entities:
        return "No entities found."
    lines = []
    for entity in entities[:100]:
        score = entity.get("score")
        score_text = f" score={score:.3f}" if isinstance(score, float) else ""
        lines.append(
            f"- {entity['label']}: {entity['text']} "
            f"[{entity['start']}:{entity['end']}]{score_text}"
        )
    if len(entities) > 100:
        lines.append(f"... {len(entities) - 100} more")
    return "\n".join(lines)


async def tool_extract_entities(
    session: ToolSession,
    text: str,
    labels: list[str] | dict[str, str],
    model_id: str | None = None,
    threshold: float = 0.5,
    max_chars: int = 6000,
    overlap_chars: int = 500,
) -> ToolResult:
    """Extract named entities from text using a local GLiNER / GLiNER2 model."""
    del session
    try:
        result = await get_ner_service().extract(
            text=text,
            labels=labels,
            model_id=model_id,
            threshold=threshold,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    except NerError as exc:
        return ToolResult(type=ToolResultType.ERROR, output=f"{exc.code}: {exc.message}")

    entities = [span.__dict__ for span in result.entities]
    return ToolResult(
        output=_result_output(entities),
        metadata={
            "entities": entities,
            "model_id": result.model_id,
            "repo_id": result.repo_id,
            "elapsed_seconds": result.elapsed_seconds,
            "chunk_count": result.chunk_count,
        },
    )


async def tool_extract_pii(
    session: ToolSession,
    text: str,
    model_id: str | None = None,
    threshold: float = 0.5,
) -> ToolResult:
    """Extract common PII entities from text using the local NER service."""
    del session
    try:
        result = await get_ner_service().extract_pii(
            text=text,
            model_id=model_id,
            threshold=threshold,
        )
    except NerError as exc:
        return ToolResult(type=ToolResultType.ERROR, output=f"{exc.code}: {exc.message}")

    entities = [span.__dict__ for span in result.entities]
    return ToolResult(
        output=_result_output(entities),
        metadata={
            "entities": entities,
            "model_id": result.model_id,
            "repo_id": result.repo_id,
            "elapsed_seconds": result.elapsed_seconds,
            "chunk_count": result.chunk_count,
            "preset": "pii",
        },
    )
