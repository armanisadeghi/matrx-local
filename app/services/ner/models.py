"""Model catalog for the local NER subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


NerBackend = Literal["gliner", "gliner2"]
NerTier = Literal["edge", "base", "large", "xxl"]


@dataclass(frozen=True)
class NerModelSpec:
    """One downloadable local NER model."""

    model_id: str
    repo_id: str
    display_name: str
    backend: NerBackend
    tier: NerTier
    description: str
    license: str
    estimated_disk_mb: int
    estimated_ram_mb: tuple[int, int]
    default: bool = False
    hardware_gate: str | None = None


NER_MODELS: tuple[NerModelSpec, ...] = (
    NerModelSpec(
        model_id="gliner2-base",
        repo_id="fastino/gliner2-base-v1",
        display_name="GLiNER2 Base",
        backend="gliner2",
        tier="base",
        description="Default server-compatible local IE model for NER, classification, structured extraction, and relations.",
        license="apache-2.0",
        estimated_disk_mb=850,
        estimated_ram_mb=(2048, 4096),
        default=True,
    ),
    NerModelSpec(
        model_id="gliner2-large",
        repo_id="fastino/gliner2-large-v1",
        display_name="GLiNER2 Large",
        backend="gliner2",
        tier="large",
        description="Higher-quality GLiNER2 tier for harder extraction workloads.",
        license="apache-2.0",
        estimated_disk_mb=1800,
        estimated_ram_mb=(4096, 8192),
    ),
    NerModelSpec(
        model_id="gliner-bi-edge",
        repo_id="knowledgator/gliner-bi-edge-v2.0",
        display_name="GLiNER Bi-Edge",
        backend="gliner",
        tier="edge",
        description="Small bi-encoder GLiNER model for high-volume CPU NER with many labels.",
        license="apache-2.0",
        estimated_disk_mb=600,
        estimated_ram_mb=(512, 1536),
    ),
    NerModelSpec(
        model_id="gliner-small-v25",
        repo_id="gliner-community/gliner_small-v2.5",
        display_name="GLiNER Small v2.5",
        backend="gliner",
        tier="edge",
        description="Small classic GLiNER v2.5 model for low-resource zero-shot NER.",
        license="apache-2.0",
        estimated_disk_mb=600,
        estimated_ram_mb=(512, 1536),
    ),
    NerModelSpec(
        model_id="gliner-large-v25",
        repo_id="gliner-community/gliner_large-v2.5",
        display_name="GLiNER Large v2.5",
        backend="gliner",
        tier="large",
        description="Strong classic GLiNER v2.5 model for on-demand quality.",
        license="apache-2.0",
        estimated_disk_mb=1900,
        estimated_ram_mb=(4096, 8192),
    ),
    NerModelSpec(
        model_id="gliner-xxl-v25",
        repo_id="gliner-community/gliner_xxl-v2.5",
        display_name="GLiNER XXL v2.5",
        backend="gliner",
        tier="xxl",
        description="Largest downloadable classic GLiNER v2.5 tier; opt-in for powerful machines only.",
        license="apache-2.0",
        estimated_disk_mb=6500,
        estimated_ram_mb=(10240, 20480),
        hardware_gate="Requires roughly 10-20 GB free RAM; never auto-load.",
    ),
)

NER_MODEL_BY_ID: dict[str, NerModelSpec] = {m.model_id: m for m in NER_MODELS}
DEFAULT_NER_MODEL_ID = next(m.model_id for m in NER_MODELS if m.default)


PII_LABELS: tuple[str, ...] = (
    "person",
    "organization",
    "email",
    "phone number",
    "address",
    "credit card number",
    "social security number",
    "bank account number",
    "date of birth",
    "medical record number",
    "ip address",
    "url",
    "username",
    "password",
    "api key",
)
