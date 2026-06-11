"""openWakeWord model management.

Models are stored in ~/.matrx/oww_models/ and loaded by the WakeWordService
at runtime.  Pre-trained models are downloaded from the openWakeWord HuggingFace
repository.  Custom-trained models (e.g. hey_matrix.onnx) are placed in the
same directory after training.

Download sizes are approximate; actual .onnx files are small (~3–5 MB each).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.common.system_logger import get_logger

logger = get_logger()

# Pre-trained model registry.  Each entry maps a short name to its HuggingFace
# download URL.  These are the official openWakeWord v0.6 ONNX models.
_PRETRAINED_REGISTRY: dict[str, dict] = {
    "hey_jarvis": {
        "url": "https://huggingface.co/davidscripka/openWakeWord/resolve/main/hey_jarvis_v0.1.onnx",
        "size_mb": 3.1,
        "description": "Hey Jarvis (closest phonetics to 'Hey Matrix', good for testing)",
        "built_in": True,
    },
    "alexa": {
        "url": "https://huggingface.co/davidscripka/openWakeWord/resolve/main/alexa_v0.1.onnx",
        "size_mb": 3.1,
        "description": "Alexa (well-tested reference model, useful for accuracy baseline)",
        "built_in": True,
    },
    "hey_mycroft": {
        "url": "https://huggingface.co/davidscripka/openWakeWord/resolve/main/hey_mycroft_v0.1.onnx",
        "size_mb": 3.1,
        "description": "Hey Mycroft (open-source assistant wake word)",
        "built_in": True,
    },
    "ok_nabu": {
        "url": "https://huggingface.co/davidscripka/openWakeWord/resolve/main/ok_nabu_v0.1.onnx",
        "size_mb": 3.1,
        "description": "OK Nabu (Home Assistant voice assistant)",
        "built_in": True,
    },
}

# Models we want available immediately at first OWW engine start.
BUNDLED_MODELS: list[str] = ["hey_jarvis", "alexa"]


@dataclass
class OWWModelInfo:
    name: str
    filename: str
    downloaded: bool
    size_mb: float
    description: str
    is_built_in: bool
    is_custom: bool
    path: str | None


def oww_models_dir() -> Path:
    """Return ~/.matrx/oww_models/, creating it if needed."""
    home = Path.home()
    d = home / ".matrx" / "oww_models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _onnx_filename(name: str) -> str:
    """Convert a short model name to its .onnx filename."""
    if name.endswith(".onnx"):
        return name
    return f"{name}.onnx"


def model_exists(name: str) -> bool:
    """Return True if the model file is present and non-empty."""
    path = oww_models_dir() / _onnx_filename(name)
    return path.exists() and path.stat().st_size > 0


def list_available_models() -> list[OWWModelInfo]:
    """Return all known pre-trained models plus any custom .onnx files on disk."""
    mdir = oww_models_dir()
    result: list[OWWModelInfo] = []

    # Pre-trained registry
    for name, meta in _PRETRAINED_REGISTRY.items():
        fname = _onnx_filename(name)
        path = mdir / fname
        downloaded = path.exists() and path.stat().st_size > 0
        result.append(OWWModelInfo(
            name=name,
            filename=fname,
            downloaded=downloaded,
            size_mb=meta["size_mb"],
            description=meta["description"],
            is_built_in=meta["built_in"],
            is_custom=False,
            path=str(path) if downloaded else None,
        ))

    # Custom models: any .onnx files not in the registry
    known_filenames = {_onnx_filename(n) for n in _PRETRAINED_REGISTRY}
    for onnx_file in sorted(mdir.glob("*.onnx")):
        if onnx_file.name not in known_filenames:
            name = onnx_file.stem
            result.append(OWWModelInfo(
                name=name,
                filename=onnx_file.name,
                downloaded=True,
                size_mb=round(onnx_file.stat().st_size / 1_048_576, 1),
                description=f"Custom model ({onnx_file.name})",
                is_built_in=False,
                is_custom=True,
                path=str(onnx_file),
            ))

    return result


async def download_model(name: str, on_progress=None) -> OWWModelInfo:
    """Download a pre-trained model from HuggingFace.

    Args:
        name: Short model name (e.g. "hey_jarvis") or filename (e.g. "hey_jarvis.onnx").
        on_progress: Optional async callable(bytes_done, total_bytes).

    Returns:
        Updated OWWModelInfo with downloaded=True and path set.

    Raises:
        ValueError: If the model name is not in the registry.
        httpx.HTTPError: On download failure.
    """
    # Normalise — strip .onnx suffix for registry lookup
    lookup_name = name.removesuffix(".onnx")
    if lookup_name not in _PRETRAINED_REGISTRY:
        raise ValueError(
            f"Unknown pre-trained model: {name!r}. "
            f"Available: {', '.join(_PRETRAINED_REGISTRY)}"
        )

    meta = _PRETRAINED_REGISTRY[lookup_name]
    url = meta["url"]
    dest = oww_models_dir() / _onnx_filename(lookup_name)

    logger.info(f"Downloading OWW model {lookup_name} from {url} → {dest}")

    # Download to a temp file and rename: writing directly to the final path
    # left a partial .onnx on interruption, which model_exists() (st_size > 0)
    # then treated as valid forever — the service crashed at ONNX load on
    # every start with no self-heal path.
    tmp = dest.with_suffix(".onnx.tmp")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                done = 0
                with open(tmp, "wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        fh.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            await on_progress(done, total)
        if total and tmp.stat().st_size != total:
            raise OSError(
                f"Incomplete download: got {tmp.stat().st_size} of {total} bytes"
            )
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    logger.info(f"OWW model {lookup_name} downloaded ({dest.stat().st_size} bytes)")

    return OWWModelInfo(
        name=lookup_name,
        filename=_onnx_filename(lookup_name),
        downloaded=True,
        size_mb=round(dest.stat().st_size / 1_048_576, 1),
        description=meta["description"],
        is_built_in=meta["built_in"],
        is_custom=False,
        path=str(dest),
    )
