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


# ─────────────────────────────────────────────────────────────────────────────
# Remote-catalog accessors — the canonical read path
#
# The live catalogs are the remote `catalog_entries` table (kinds `ner_model`
# / `ner_pii_labels`) served through app/services/catalogs. The compiled
# tuples above stay only as the explicit defaults tier (first boot / total
# network failure) — adapted by app/services/catalogs/compiled.py. Read
# through these accessors, never the tuples directly.
# ─────────────────────────────────────────────────────────────────────────────


def _ram_tuple(payload: dict) -> dict:
    # JSON has no tuples — the payload carries estimated_ram_mb as a
    # 2-element array; the dataclass field is a tuple.
    p = dict(payload)
    ram = p.get("estimated_ram_mb")
    if isinstance(ram, list):
        p["estimated_ram_mb"] = tuple(ram)
    return p


def get_ner_models() -> list[NerModelSpec]:
    """Resolved NER model catalog (remote > cache > compiled fallback)."""
    from app.services.catalogs import get_catalog  # noqa: PLC0415 — lazy: avoids import cycle
    from app.services.catalogs.adapt import entries_to_dataclasses  # noqa: PLC0415

    return entries_to_dataclasses(
        get_catalog("ner_model"), NerModelSpec, transform=_ram_tuple
    )


def get_ner_model(model_id: str) -> NerModelSpec | None:
    """Single resolved NER model by id."""
    return next((m for m in get_ner_models() if m.model_id == model_id), None)


def get_default_ner_model_id() -> str:
    """The catalog's flagged default NER model id (payload.default)."""
    from app.common.system_logger import get_logger  # noqa: PLC0415

    for m in get_ner_models():
        if m.default:
            return m.model_id
    get_logger().warning(
        "[ner] no catalog entry flagged default — falling back to the "
        "compiled DEFAULT_NER_MODEL_ID (%s)",
        DEFAULT_NER_MODEL_ID,
    )
    return DEFAULT_NER_MODEL_ID


def get_pii_labels() -> tuple[str, ...]:
    """Resolved PII label set (kind ner_pii_labels, key 'default')."""
    from app.common.system_logger import get_logger  # noqa: PLC0415
    from app.services.catalogs import get_catalog_entry  # noqa: PLC0415

    entry = get_catalog_entry("ner_pii_labels", "default")
    if entry is not None:
        labels = entry.payload.get("labels")
        if isinstance(labels, list) and labels and all(isinstance(x, str) for x in labels):
            return tuple(labels)
        get_logger().error(
            "[ner] ner_pii_labels/default payload is malformed (%r) — falling "
            "back to the compiled PII_LABELS",
            labels,
        )
    return PII_LABELS
