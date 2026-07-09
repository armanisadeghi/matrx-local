"""Hardware gating for local media generation.

Single source of truth for:

- which compute device generation will run on ("mps" | "cuda" | "cpu"),
- how much memory that device can devote to model weights,
- whether video generation is available AT ALL on this machine,
- whether a specific model's minimum requirements are met.

Detection strategy
------------------
We reuse the existing hardware detector's cached profile
(``app.api.hardware_routes`` Phase 0d cache) for RAM/GPU facts — it is the
platform's canonical hardware inventory. Device *selection* (mps vs cuda vs
cpu) additionally consults torch when the optional packages are installed,
mirroring the exact logic the image-gen service has always used. All torch
imports are lazy and guarded — this module imports cleanly with no optional
packages present.

Hard rules (from the media-gen spec):

- Video generation is available only on (macOS arm64 with >= 16 GB unified
  memory) OR (CUDA GPU with >= 8 GB VRAM). CPU-only machines: never.
- On MPS, fp8 is not supported — pipelines must load bf16 (handled by the
  services; this module only reports the device).
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any

from app.common.system_logger import get_logger

logger = get_logger()

VIDEO_UNSUPPORTED_REASON = (
    "Video generation requires Apple Silicon with 16GB+ memory "
    "or an NVIDIA GPU with 8GB+ VRAM"
)

_VIDEO_MIN_UNIFIED_GB = 16.0
_VIDEO_MIN_CUDA_VRAM_GB = 8.0


@dataclass(frozen=True)
class ComputeProfile:
    """Resolved compute facts for media generation."""

    device: str
    """"mps" | "cuda" | "cpu"."""

    is_apple_silicon: bool
    total_ram_gb: float
    """Total system RAM (== unified memory budget on Apple Silicon)."""

    cuda_vram_gb: float
    """Largest CUDA GPU's VRAM in GB; 0.0 when no CUDA GPU."""

    video_supported: bool
    video_unsupported_reason: str | None


def _cached_hardware_profile() -> dict[str, Any]:
    """Best-effort read of the canonical Phase 0d hardware cache.

    Returns {} when detection hasn't run yet (early startup) — callers fall
    back to psutil/platform facts so gating never blocks on the cache.
    """
    try:
        from app.api import hardware_routes
        return hardware_routes._cached_profile or {}
    except Exception:
        return {}


def _total_ram_gb(profile: dict[str, Any]) -> float:
    ram_mb = (profile.get("ram") or {}).get("total_mb")
    if ram_mb:
        return round(ram_mb / 1024.0, 1)
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _cuda_vram_gb(profile: dict[str, Any]) -> float:
    """Largest CUDA GPU VRAM in GB, from the hardware profile, else torch."""
    best = 0.0
    for gpu in profile.get("gpus") or []:
        backend = str(gpu.get("backend") or "")
        if "cuda" in backend and gpu.get("vram_mb"):
            best = max(best, gpu["vram_mb"] / 1024.0)
    if best > 0:
        return round(best, 1)
    # Fall back to torch when packages are installed (covers machines where
    # nvidia-smi wasn't found but the CUDA runtime works).
    try:
        import torch  # noqa: PLC0415
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            return round(props.total_memory / (1024 ** 3), 1)
    except Exception:
        pass
    return 0.0


def select_device() -> str:
    """"cuda" > "mps" > "cpu" — identical priority to the image-gen service.

    When torch is not installed we infer from platform facts alone (Apple
    Silicon → "mps", CUDA GPU in the profile → "cuda") so the status endpoints
    can report the device users WILL get after installing packages.
    """
    try:
        import torch  # noqa: PLC0415
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    except Exception:
        pass

    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mps"
    if _cuda_vram_gb(_cached_hardware_profile()) > 0:
        return "cuda"
    return "cpu"


def get_compute_profile() -> ComputeProfile:
    """Resolve the full compute profile (cheap — no model loads)."""
    profile = _cached_hardware_profile()
    is_apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    ram_gb = _total_ram_gb(profile)
    vram_gb = _cuda_vram_gb(profile)
    device = select_device()

    video_ok = (is_apple and ram_gb >= _VIDEO_MIN_UNIFIED_GB) or (
        vram_gb >= _VIDEO_MIN_CUDA_VRAM_GB
    )

    return ComputeProfile(
        device=device,
        is_apple_silicon=is_apple,
        total_ram_gb=ram_gb,
        cuda_vram_gb=vram_gb,
        video_supported=video_ok,
        video_unsupported_reason=None if video_ok else VIDEO_UNSUPPORTED_REASON,
    )


def check_model_hardware(
    *,
    min_vram_gb: float,
    min_ram_gb: float,
    compute: ComputeProfile | None = None,
) -> tuple[bool, str | None]:
    """Return (hardware_ok, reason) for a single model's minimum requirements.

    On Apple Silicon the unified memory pool must cover the larger of the
    model's RAM/VRAM requirements. On CUDA machines VRAM and system RAM are
    checked independently. CPU-only machines are gated by RAM only (video
    models additionally gate on video_supported at the service level).
    """
    c = compute or get_compute_profile()

    if c.is_apple_silicon:
        needed = max(min_vram_gb, min_ram_gb)
        if c.total_ram_gb and c.total_ram_gb < needed:
            return False, (
                f"Requires {needed:.0f}GB+ unified memory; "
                f"this Mac has {c.total_ram_gb:.0f}GB"
            )
        return True, None

    if c.device == "cuda":
        if c.cuda_vram_gb and c.cuda_vram_gb < min_vram_gb:
            return False, (
                f"Requires {min_vram_gb:.0f}GB+ VRAM; "
                f"this GPU has {c.cuda_vram_gb:.0f}GB"
            )
        if c.total_ram_gb and c.total_ram_gb < min_ram_gb:
            return False, (
                f"Requires {min_ram_gb:.0f}GB+ system RAM; "
                f"this machine has {c.total_ram_gb:.0f}GB"
            )
        return True, None

    # CPU-only: RAM is the only budget.
    if c.total_ram_gb and c.total_ram_gb < min_ram_gb:
        return False, (
            f"Requires {min_ram_gb:.0f}GB+ RAM; "
            f"this machine has {c.total_ram_gb:.0f}GB"
        )
    return True, None
