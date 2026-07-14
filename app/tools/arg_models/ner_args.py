from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractEntitiesArgs(BaseModel):
    text: str = Field(description="Text to scan for named entities.", min_length=1)
    labels: list[str] | dict[str, str] = Field(
        description="Entity labels, or a mapping of label to description for higher precision.",
    )
    model_id: str | None = Field(
        default=None,
        description="Optional NER model id from /ner/models. Defaults to the active GLiNER2-base model.",
    )
    threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score to return when the model emits confidence.",
    )
    max_chars: int = Field(
        default=6000,
        ge=500,
        le=100_000,
        description="Maximum characters per inference chunk for long documents.",
    )
    overlap_chars: int = Field(
        default=500,
        ge=0,
        le=20_000,
        description="Character overlap between long-document chunks.",
    )


class ExtractPiiArgs(BaseModel):
    text: str = Field(description="Text to scan for common PII entities.", min_length=1)
    model_id: str | None = Field(
        default=None,
        description="Optional NER model id from /ner/models. Defaults to the active GLiNER2-base model.",
    )
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
