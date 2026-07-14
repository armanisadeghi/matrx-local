"""Model Repository Analyzer — intelligently analyzes model repos and scores files
against the user's actual hardware.

Supports HuggingFace out of the box; extensible to CivitAI, GitHub, etc. via
the ModelRepoProvider ABC.

Endpoints
---------
POST /model-repo/analyze  – Analyze a repo URL and return scored file list
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.common.system_logger import get_logger

logger = get_logger()

router = APIRouter(prefix="/model-repo", tags=["model-repo"])

_CLIENT_HEADERS = {
    "User-Agent": "matrx-local/1.0",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Hardware models
# ---------------------------------------------------------------------------


class HardwareInfo(BaseModel):
    total_ram_mb: int
    gpu_vram_mb: int | None = None
    supports_cuda: bool = False
    supports_metal: bool = False
    is_apple_silicon: bool = False


def compute_effective_capacity_gb(hw: HardwareInfo) -> float:
    """Compute how many GB of model this machine can load, mirroring the logic
    in model_selector.rs.

    - Apple Silicon: full unified RAM pool (Metal shares RAM+VRAM).
    - CUDA GPU: VRAM is primary; overflow to 50% of remaining RAM.
    - CPU only: 75% of total RAM (OS + overhead reserve).
    """
    total_ram_gb = hw.total_ram_mb / 1024.0
    gpu_vram_gb = (hw.gpu_vram_mb or 0) / 1024.0

    if hw.is_apple_silicon:
        return total_ram_gb

    if hw.supports_cuda and gpu_vram_gb > 0:
        # Primary budget is VRAM; spill capacity is half remaining RAM
        spill = max(0.0, total_ram_gb - gpu_vram_gb) * 0.5
        return gpu_vram_gb + spill

    # CPU-only — reserve 25% for OS + overhead
    return total_ram_gb * 0.75


def describe_hardware(hw: HardwareInfo) -> str:
    """Return a short human-readable label for the machine."""
    total_ram_gb = hw.total_ram_mb / 1024
    gpu_vram_gb = (hw.gpu_vram_mb or 0) / 1024

    if hw.is_apple_silicon:
        return f"Apple Silicon, {total_ram_gb:.0f} GB unified memory"
    if hw.supports_cuda and gpu_vram_gb > 0:
        return f"NVIDIA GPU, {gpu_vram_gb:.0f} GB VRAM ({total_ram_gb:.0f} GB RAM)"
    return f"CPU only, {total_ram_gb:.0f} GB RAM"


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

CompatibilityStatus = Literal["works", "needs_more_ram", "accessory_only", "incompatible_format"]
FileFormat = Literal["gguf", "safetensors", "bin", "onnx", "other"]
FileRole = Literal["main_model", "mmproj", "adapter", "tokenizer", "config", "other"]


class ModelFileEntry(BaseModel):
    filename: str
    format: FileFormat
    role: FileRole
    quant: str | None  # e.g. "Q4_K_M", "BF16", None for non-GGUF
    is_split: bool
    split_group: str | None  # key shared by all parts of the same logical model
    part_index: int | None  # 1-based; None for single-file or non-GGUF
    total_parts: int | None
    size_bytes: int  # individual file size
    total_size_bytes: int  # sum of all parts (filled in after grouping)
    ram_required_gb: float  # total_size_bytes / GB * 1.35
    compatibility_status: CompatibilityStatus
    compatibility_reason: str  # single personalized sentence
    download_urls: list[str]  # all part URLs in download order
    recommended: bool  # True for exactly one best-fit entry


class RepoAnalysisResult(BaseModel):
    provider: str  # "huggingface" | "raw" | etc.
    repo_id: str  # "owner/repo" or raw URL
    repo_url: str
    author: str | None
    model_name: str | None
    architecture: str | None  # from model card tags
    total_files: int
    hardware_label: str | None  # human-readable machine description
    effective_capacity_gb: float | None
    files: list[ModelFileEntry]


class AnalyzeRequest(BaseModel):
    url: str
    hardware: HardwareInfo | None = None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# Matches quantization labels in GGUF filenames
_QUANT_RE = re.compile(
    r"\b(BF16|F16|F32|Q8_0|Q6_K_L?|Q5_K_[MS]|Q5_1|Q5_0|Q4_K_[MS]|Q4_1|Q4_0|"
    r"IQ4_XS|IQ4_NL|IQ3_M|IQ3_XS|IQ3_S|IQ3_XXS|IQ2_M|IQ2_S|IQ2_XS|IQ2_XXS|"
    r"IQ1_M|IQ1_S|Q3_K_[SML]|Q2_K)\b",
    re.IGNORECASE,
)

# Detects split-part suffix: -00001-of-00003
_SPLIT_RE = re.compile(r"-(\d+)-of-(\d+)\.gguf$", re.IGNORECASE)

_QUANT_QUALITY: dict[str, int] = {
    "BF16": 10, "F16": 9, "F32": 11,
    "Q8_0": 8,
    "Q6_K": 7, "Q6_K_L": 7,
    "Q5_K_M": 6, "Q5_K_S": 5, "Q5_1": 5, "Q5_0": 4,
    "Q4_K_M": 5, "Q4_K_S": 4, "Q4_1": 3, "Q4_0": 2,
    "IQ4_XS": 4, "IQ4_NL": 4,
    "Q3_K_L": 3, "Q3_K_M": 3, "Q3_K_S": 2,
    "IQ3_M": 3, "IQ3_XS": 2, "IQ3_S": 2, "IQ3_XXS": 2,
    "Q2_K": 2,
    "IQ2_M": 2, "IQ2_S": 1, "IQ2_XS": 1, "IQ2_XXS": 1,
    "IQ1_M": 1, "IQ1_S": 1,
}

_QUANT_DESCRIPTIONS: dict[str, str] = {
    "Q4_K_M": "Best balance of quality and size — recommended for most users",
    "Q4_K_S": "Slightly smaller than Q4_K_M, minor quality trade-off",
    "Q8_0": "Near-lossless quality, uses about 2x more RAM than Q4_K_M",
    "BF16": "Full precision, enormous — only practical on servers with 64+ GB VRAM",
    "F16": "Full precision (float16), very large",
    "IQ4_XS": "Good quality, slightly smaller than Q4_K_M",
    "Q5_K_M": "High quality, between Q4 and Q8 in size",
    "Q3_K_M": "Moderate compression, runs on smaller machines with some quality loss",
    "IQ3_M": "Aggressive compression, similar to Q3_K_M",
    "Q2_K": "Maximum compression, significant quality loss — last resort for tiny machines",
    "IQ2_M": "Very aggressive compression, similar to Q2_K",
    "IQ2_XS": "Extreme compression, noticeable quality loss",
}


def _detect_format(filename: str) -> FileFormat:
    lower = filename.lower()
    if lower.endswith(".gguf"):
        return "gguf"
    if lower.endswith(".safetensors"):
        return "safetensors"
    if lower.endswith(".bin"):
        return "bin"
    if lower.endswith(".onnx"):
        return "onnx"
    return "other"


def _detect_role(filename: str, fmt: FileFormat) -> FileRole:
    lower = filename.lower()
    if fmt != "gguf":
        if "tokenizer" in lower or lower.endswith(".json"):
            return "tokenizer"
        if "adapter" in lower or "lora" in lower:
            return "adapter"
        return "other"
    # GGUF-specific roles
    if "mmproj" in lower or "projector" in lower or "vision" in lower:
        return "mmproj"
    if "adapter" in lower or "lora" in lower:
        return "adapter"
    return "main_model"


def _parse_quant(filename: str) -> str | None:
    m = _QUANT_RE.search(filename)
    if m:
        return m.group(0).upper()
    return None


def _parse_split(filename: str) -> tuple[int, int] | None:
    """Return (part_index, total_parts) if this is a split file, else None."""
    m = _SPLIT_RE.search(filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _split_group_key(filename: str, quant: str | None) -> str:
    """Derive a stable group key for split-part files."""
    # Strip the -00001-of-00003.gguf suffix to get the base name
    base = _SPLIT_RE.sub("", filename)
    base = re.sub(r"\.gguf$", "", base, flags=re.IGNORECASE)
    return base.lower()


def _make_compatibility(
    role: FileRole,
    fmt: FileFormat,
    ram_required_gb: float,
    capacity_gb: float | None,
) -> tuple[CompatibilityStatus, str]:
    if fmt != "gguf":
        return (
            "incompatible_format",
            f"This is a {fmt} file — only .gguf files can be loaded by this app.",
        )
    if role == "mmproj":
        return (
            "accessory_only",
            "Vision projector file — not a standalone model. Only needed for image input, which isn't supported yet.",
        )
    if role == "adapter":
        return (
            "accessory_only",
            "LoRA adapter — not a standalone model. Adapter loading isn't supported yet.",
        )
    if role == "tokenizer":
        return (
            "accessory_only",
            "Tokenizer/config file — not a model weight file.",
        )
    # main_model GGUF
    if capacity_gb is None:
        return ("works", "Compatible format. Hardware info unavailable — check RAM manually.")
    if ram_required_gb <= capacity_gb:
        headroom = capacity_gb - ram_required_gb
        if headroom < 1.0:
            return ("works", f"Fits on your machine, but tight — only {headroom:.1f} GB to spare.")
        return ("works", f"Fits comfortably on your machine ({ram_required_gb:.1f} GB needed, {capacity_gb:.0f} GB available).")
    shortfall = ram_required_gb - capacity_gb
    return (
        "needs_more_ram",
        f"Needs {ram_required_gb:.1f} GB but your machine can handle ~{capacity_gb:.0f} GB — {shortfall:.1f} GB short.",
    )


def _pick_recommended(entries: list[ModelFileEntry], capacity_gb: float | None) -> None:
    """Set `recommended = True` on the single best-fit entry."""
    if capacity_gb is None:
        return

    # All "works" main_model GGUF entries (split models represented by their first-part entry)
    candidates = [
        e for e in entries
        if e.compatibility_status == "works" and e.role == "main_model"
    ]

    if not candidates:
        return

    # Sort: prefer Q4_K_M, then largest size that fits within 90% capacity
    budget = capacity_gb * 0.90

    def score(e: ModelFileEntry) -> tuple[int, float]:
        # Prefer Q4_K_M, then maximize size within budget
        is_q4km = (e.quant or "").upper() == "Q4_K_M"
        size_score = float(e.total_size_bytes) if e.total_size_bytes / 1024**3 <= budget else 0.0
        return (1 if is_q4km else 0, size_score)

    candidates.sort(key=score, reverse=True)
    if candidates:
        candidates[0].recommended = True


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class ModelRepoProvider(ABC):
    @abstractmethod
    def can_handle(self, url: str) -> bool: ...

    @abstractmethod
    async def analyze(
        self, url: str, hardware: HardwareInfo | None
    ) -> RepoAnalysisResult: ...


# ---------------------------------------------------------------------------
# HuggingFace provider
# ---------------------------------------------------------------------------


class HuggingFaceProvider(ModelRepoProvider):
    BASE_API = "https://huggingface.co/api/models"
    RESOLVE_BASE = "https://huggingface.co"

    def can_handle(self, url: str) -> bool:
        return "huggingface.co" in url

    def _parse_repo_id(self, url: str) -> str:
        """Extract owner/repo from any HuggingFace URL."""
        # Normalize: strip scheme, strip trailing slashes/fragments
        clean = re.sub(r"^https?://", "", url).strip("/")
        # huggingface.co/owner/repo[/anything]
        m = re.match(r"huggingface\.co/([^/]+/[^/?\s]+)", clean)
        if m:
            return m.group(1)
        raise ValueError(f"Cannot parse HuggingFace repo ID from URL: {url}")

    async def analyze(
        self, url: str, hardware: HardwareInfo | None
    ) -> RepoAnalysisResult:
        repo_id = self._parse_repo_id(url)
        capacity_gb = compute_effective_capacity_gb(hardware) if hardware else None
        hw_label = describe_hardware(hardware) if hardware else None

        # Attach the stored Hugging Face token, resolved AT REQUEST TIME from
        # the app key store (read_hf_token) — gated repos (e.g. Llama GGUFs)
        # are inspectable only with it, and telling a user who configured a
        # token that "only public models are supported" is a dead end.
        from app.services.media_gen.paths import read_hf_token  # noqa: PLC0415
        hf_token = read_hf_token()
        headers = dict(_CLIENT_HEADERS)
        if hf_token:
            headers["Authorization"] = f"Bearer {hf_token}"

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20.0,
            headers=headers,
        ) as client:
            # blobs=true is required for the API to include per-file sizes on
            # `siblings`; without it every size reads 0 and all RAM-fit
            # compatibility scoring below degenerates to "fits comfortably".
            api_url = f"{self.BASE_API}/{repo_id}?blobs=true"
            resp = await client.get(api_url)

        if resp.status_code == 401 or resp.status_code == 403:
            # Attribution matters: with a token attached this is a gate/license
            # state, NOT a credentials problem — never ask a user with a
            # configured token to add one.
            if hf_token:
                detail = (
                    f"'{repo_id}' is a gated model and your Hugging Face "
                    "account doesn't have access to it yet. Open the model "
                    f"page (https://huggingface.co/{repo_id}), accept its "
                    "license or request access, then try again."
                )
            else:
                detail = (
                    f"'{repo_id}' is gated or private on Hugging Face. Add "
                    "your Hugging Face token under Settings → API Keys → "
                    "Hugging Face, then try again."
                )
            raise HTTPException(status_code=422, detail=detail)
        if resp.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Repository '{repo_id}' was not found on HuggingFace. "
                    "Double-check the URL — the repo may have been renamed, deleted, or the URL may have a typo."
                ),
            )
        if not resp.is_success:
            raise HTTPException(
                status_code=502,
                detail=f"HuggingFace API returned {resp.status_code} for '{repo_id}'. Try again in a moment.",
            )

        data = resp.json()
        siblings: list[dict] = data.get("siblings", [])
        author: str | None = data.get("author")
        model_name: str | None = data.get("id", repo_id).split("/")[-1]
        tags: list[str] = data.get("tags", [])
        pipeline_tag: str | None = data.get("pipeline_tag")

        # Attempt to derive architecture from tags
        arch_candidates = [
            t for t in tags
            if any(kw in t.lower() for kw in ("llama", "mistral", "qwen", "phi", "gemma", "falcon", "mpt", "bloom", "gpt", "bert", "t5"))
        ]
        architecture = arch_candidates[0] if arch_candidates else pipeline_tag

        # Build raw file entries
        raw_entries: list[dict] = []
        for sib in siblings:
            fname: str = sib.get("rfilename", "")
            size: int = sib.get("size", 0)
            fmt = _detect_format(fname)
            role = _detect_role(fname, fmt)
            quant = _parse_quant(fname) if fmt == "gguf" else None
            split_info = _parse_split(fname) if fmt == "gguf" else None
            is_split = split_info is not None
            part_index = split_info[0] if split_info else None
            total_parts = split_info[1] if split_info else None
            split_group = _split_group_key(fname, quant) if is_split else None
            download_url = f"{self.RESOLVE_BASE}/{repo_id}/resolve/main/{fname}"

            raw_entries.append({
                "filename": fname,
                "format": fmt,
                "role": role,
                "quant": quant,
                "is_split": is_split,
                "split_group": split_group,
                "part_index": part_index,
                "total_parts": total_parts,
                "size_bytes": size,
                "download_url": download_url,
            })

        # ── Group split parts ────────────────────────────────────────────────
        # For split models: compute total_size_bytes, collect all part URLs,
        # then emit only the first-part entry as the representative.
        split_groups: dict[str, list[dict]] = defaultdict(list)
        standalone: list[dict] = []

        for entry in raw_entries:
            if entry["is_split"]:
                split_groups[entry["split_group"]].append(entry)
            else:
                standalone.append(entry)

        final_entries: list[ModelFileEntry] = []

        # Process standalone files
        for e in standalone:
            size = e["size_bytes"]
            ram_gb = (size / 1024**3) * 1.35
            status, reason = _make_compatibility(e["role"], e["format"], ram_gb, capacity_gb)
            final_entries.append(ModelFileEntry(
                filename=e["filename"],
                format=e["format"],
                role=e["role"],
                quant=e["quant"],
                is_split=False,
                split_group=None,
                part_index=None,
                total_parts=None,
                size_bytes=size,
                total_size_bytes=size,
                ram_required_gb=round(ram_gb, 2),
                compatibility_status=status,
                compatibility_reason=reason,
                download_urls=[e["download_url"]],
                recommended=False,
            ))

        # Process split groups — emit one representative entry per group
        for group_key, parts in split_groups.items():
            parts.sort(key=lambda p: p["part_index"] or 0)
            first = parts[0]
            total_size = sum(p["size_bytes"] for p in parts)
            ram_gb = (total_size / 1024**3) * 1.35
            status, reason = _make_compatibility(first["role"], first["format"], ram_gb, capacity_gb)
            urls = [p["download_url"] for p in parts]
            n_parts = first["total_parts"] or len(parts)
            final_entries.append(ModelFileEntry(
                filename=first["filename"],
                format=first["format"],
                role=first["role"],
                quant=first["quant"],
                is_split=True,
                split_group=group_key,
                part_index=1,
                total_parts=n_parts,
                size_bytes=first["size_bytes"],
                total_size_bytes=total_size,
                ram_required_gb=round(ram_gb, 2),
                compatibility_status=status,
                compatibility_reason=reason,
                download_urls=urls,
                recommended=False,
            ))

        # ── Pick recommended ─────────────────────────────────────────────────
        _pick_recommended(final_entries, capacity_gb)

        # ── Sort: recommended first, then works (largest first), needs_more_ram
        #    (smallest-overage first), then accessories, then incompatible
        def sort_key(e: ModelFileEntry) -> tuple[int, float]:
            order = {
                "works": 1,
                "needs_more_ram": 2,
                "accessory_only": 3,
                "incompatible_format": 4,
            }
            if e.recommended:
                return (0, -e.total_size_bytes)
            rank = order.get(e.compatibility_status, 5)
            if e.compatibility_status == "works":
                return (rank, -e.total_size_bytes)
            if e.compatibility_status == "needs_more_ram":
                overage = e.ram_required_gb - (capacity_gb or 0)
                return (rank, overage)
            return (rank, 0.0)

        final_entries.sort(key=sort_key)

        # Check if the repo has any files at all vs. just no GGUF files
        has_gguf = any(e.format == "gguf" for e in final_entries)
        has_safetensors = any(e.format == "safetensors" for e in final_entries)

        if not has_gguf and has_safetensors:
            # Repo exists and has safetensors but no GGUF — provide actionable guidance
            # by looking for known GGUF conversion repos on HuggingFace
            gguf_search_url = f"https://huggingface.co/models?search={model_name}+gguf"
            unsloth_url = f"https://huggingface.co/unsloth/{model_name}-GGUF"
            bartowski_url = f"https://huggingface.co/bartowski/{model_name}-GGUF"
            # Add a synthetic informational entry to guide the user
            final_entries.append(ModelFileEntry(
                filename=f"[No GGUF files in {repo_id}]",
                format="other",
                role="other",
                quant=None,
                is_split=False,
                split_group=None,
                part_index=None,
                total_parts=None,
                size_bytes=0,
                total_size_bytes=0,
                ram_required_gb=0.0,
                compatibility_status="incompatible_format",
                compatibility_reason=(
                    f"This repo contains {len(siblings)} file(s) but none are .gguf format. "
                    f"It uses safetensors (PyTorch format), which requires a different runtime. "
                    f"To use this model here, look for a GGUF conversion: try searching "
                    f"'{model_name} GGUF' on HuggingFace, or check unsloth/{model_name}-GGUF or "
                    f"bartowski/{model_name}-GGUF."
                ),
                download_urls=[],
                recommended=False,
            ))

        return RepoAnalysisResult(
            provider="huggingface",
            repo_id=repo_id,
            repo_url=f"https://huggingface.co/{repo_id}",
            author=author,
            model_name=model_name,
            architecture=architecture,
            total_files=len(siblings),
            hardware_label=hw_label,
            effective_capacity_gb=round(capacity_gb, 1) if capacity_gb is not None else None,
            files=final_entries,
        )


# ---------------------------------------------------------------------------
# Raw file provider (fallback for direct .gguf URLs)
# ---------------------------------------------------------------------------


class RawFileProvider(ModelRepoProvider):
    def can_handle(self, url: str) -> bool:
        return url.lower().endswith(".gguf") or ".gguf?" in url.lower()

    async def analyze(
        self, url: str, hardware: HardwareInfo | None
    ) -> RepoAnalysisResult:
        capacity_gb = compute_effective_capacity_gb(hardware) if hardware else None
        hw_label = describe_hardware(hardware) if hardware else None

        # HEAD request to get size
        size_bytes = 0
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers=_CLIENT_HEADERS,
        ) as client:
            try:
                head = await client.head(url)
                size_bytes = int(head.headers.get("content-length", 0))
                if size_bytes == 0:
                    # Some servers don't support HEAD — try x-linked-size
                    size_bytes = int(head.headers.get("x-linked-size", 0))
            except Exception:
                pass

        filename = url.split("/")[-1].split("?")[0]
        fmt = _detect_format(filename)
        role = _detect_role(filename, fmt)
        quant = _parse_quant(filename)
        ram_gb = (size_bytes / 1024**3) * 1.35 if size_bytes else 0.0
        status, reason = _make_compatibility(role, fmt, ram_gb, capacity_gb)

        entry = ModelFileEntry(
            filename=filename,
            format=fmt,
            role=role,
            quant=quant,
            is_split=False,
            split_group=None,
            part_index=None,
            total_parts=None,
            size_bytes=size_bytes,
            total_size_bytes=size_bytes,
            ram_required_gb=round(ram_gb, 2),
            compatibility_status=status,
            compatibility_reason=reason,
            download_urls=[url],
            recommended=False,
        )
        if status == "works":
            entry.recommended = True

        return RepoAnalysisResult(
            provider="raw",
            repo_id=url,
            repo_url=url,
            author=None,
            model_name=filename,
            architecture=None,
            total_files=1,
            hardware_label=hw_label,
            effective_capacity_gb=round(capacity_gb, 1) if capacity_gb is not None else None,
            files=[entry],
        )


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROVIDERS: list[ModelRepoProvider] = [
    HuggingFaceProvider(),
    RawFileProvider(),  # fallback
]


def _get_provider(url: str) -> ModelRepoProvider:
    for p in _PROVIDERS:
        if p.can_handle(url):
            return p
    raise HTTPException(
        status_code=422,
        detail="URL not recognized. Supported sources: HuggingFace (huggingface.co) or a direct .gguf download link.",
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/analyze", response_model=RepoAnalysisResult)
async def analyze_repo(req: AnalyzeRequest) -> RepoAnalysisResult:
    """Analyze a model repository URL and return a scored list of files.

    Pass the user's hardware info to get personalized compatibility scores.
    Hardware info is available from the Tauri `detect_hardware` command and
    stored in the frontend's LlmContext.
    """
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="URL is required.")

    # Normalize: add https:// if missing
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    provider = _get_provider(url)
    try:
        result = await provider.analyze(url, req.hardware)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("model-repo analyze failed for %s", url)
        raise HTTPException(status_code=502, detail=f"Analysis failed: {exc}") from exc

    logger.info(
        "model-repo: analyzed %s → %d files, provider=%s",
        result.repo_id,
        result.total_files,
        result.provider,
    )
    return result
