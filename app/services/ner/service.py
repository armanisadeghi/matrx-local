"""Local GLiNER / GLiNER2 NER service."""

from __future__ import annotations

import asyncio
import inspect
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.config import MATRX_HOME_DIR
from app.services.ner.models import (
    DEFAULT_NER_MODEL_ID,
    NER_MODEL_BY_ID,
    NER_MODELS,
    PII_LABELS,
    NerModelSpec,
)

logger = get_logger()

NER_MODELS_DIR = MATRX_HOME_DIR / "ner-models"
SNAPSHOT_COMPLETE_MARKER = ".snapshot-complete"
DEFAULT_THRESHOLD = 0.5
DEFAULT_MAX_CHARS = 6000
DEFAULT_OVERLAP_CHARS = 500
MAX_BATCH_ITEMS = 128

_REGISTRY_NAME = "ner"


class NerError(Exception):
    """Domain error with a stable code for route/tool mapping."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class NerSpan:
    text: str
    label: str
    start: int
    end: int
    score: float | None = None


@dataclass(frozen=True)
class NerExtraction:
    entities: list[NerSpan]
    model_id: str
    repo_id: str
    elapsed_seconds: float
    chunk_count: int


def _deps_status() -> dict[str, Any]:
    modules: dict[str, bool] = {}
    for name in ("gliner2", "gliner", "torch", "transformers", "huggingface_hub"):
        try:
            __import__(name)
            modules[name] = True
        except Exception:
            modules[name] = False
    return {
        "available": (modules["gliner2"] or modules["gliner"]) and modules["torch"],
        "modules": modules,
        "message": (
            "Install the NER capability in Settings, or use `uv sync --extra ner` in development."
            if not ((modules["gliner2"] or modules["gliner"]) and modules["torch"])
            else ""
        ),
    }


def _model_dir(spec: NerModelSpec) -> Path:
    return NER_MODELS_DIR / spec.model_id


def _has_model_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    if not (path / SNAPSHOT_COMPLETE_MARKER).exists():
        return False
    has_config = (path / "config.json").exists()
    has_tokenizer = (path / "tokenizer.json").exists() or (path / "tokenizer_config.json").exists()
    has_weights = any(path.glob("*.safetensors")) or any(path.glob("*.bin"))
    return has_config and has_tokenizer and has_weights


def _coerce_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_entities(raw: Any, source_text: str, offset: int = 0) -> list[NerSpan]:
    """Convert GLiNER and GLiNER2 result shapes into a flat span list."""
    if isinstance(raw, dict) and "entities" in raw:
        raw = raw["entities"]

    flat: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for label, values in raw.items():
            if not isinstance(values, list):
                values = [values]
            search_from = 0
            for value in values:
                if isinstance(value, dict):
                    item = {"label": str(label), **value}
                else:
                    item = {"label": str(label), "text": str(value)}
                if "start" not in item or "end" not in item:
                    needle = str(item.get("text") or "")
                    pos = source_text.find(needle, search_from) if needle else -1
                    if pos >= 0:
                        item["start"] = pos
                        item["end"] = pos + len(needle)
                        search_from = item["end"]
                flat.append(item)
    elif isinstance(raw, list):
        flat = [item for item in raw if isinstance(item, dict)]

    spans: list[NerSpan] = []
    for item in flat:
        text = str(item.get("text") or item.get("span") or "").strip()
        label = str(item.get("label") or item.get("entity") or item.get("type") or "").strip()
        if not text or not label:
            continue
        try:
            start = int(item["start"]) + offset
            end = int(item["end"]) + offset
        except (KeyError, TypeError, ValueError):
            start = source_text.find(text)
            if start < 0:
                continue
            end = start + len(text)
            start += offset
            end += offset
        score = _coerce_score(item.get("score", item.get("confidence")))
        spans.append(NerSpan(text=text, label=label, start=start, end=end, score=score))
    return spans


def _dedupe_spans(spans: list[NerSpan], threshold: float) -> list[NerSpan]:
    best: dict[tuple[int, int, str, str], NerSpan] = {}
    for span in spans:
        if span.score is not None and span.score < threshold:
            continue
        key = (span.start, span.end, span.label.lower(), span.text.lower())
        prev = best.get(key)
        if prev is None or (span.score or 0.0) > (prev.score or 0.0):
            best[key] = span
    return sorted(best.values(), key=lambda s: (s.start, s.end, s.label))


def _normalise_labels(labels: list[str] | dict[str, str]) -> list[str] | dict[str, str]:
    if isinstance(labels, list):
        cleaned = [str(label).strip() for label in labels if str(label).strip()]
        if not cleaned:
            raise NerError("empty_labels", "labels are required")
        return cleaned
    cleaned_dict = {
        str(key).strip(): str(value).strip()
        for key, value in labels.items()
        if str(key).strip()
    }
    if not cleaned_dict:
        raise NerError("empty_labels", "labels are required")
    return cleaned_dict


def _chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[tuple[str, int]]:
    if len(text) <= max_chars:
        return [(text, 0)]

    chunks: list[tuple[str, int]] = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(text_len, start + max_chars)
        if end < text_len:
            boundary = text.rfind(" ", start + max_chars // 2, end)
            if boundary > start:
                end = boundary
        chunks.append((text[start:end], start))
        if end >= text_len:
            break
        next_start = max(0, end - overlap_chars)
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


class NerService:
    """Singleton service that owns downloaded NER models and inference."""

    def __init__(self) -> None:
        self._active_model_id = DEFAULT_NER_MODEL_ID
        self._model: Any = None
        self._loaded_model_id: str | None = None
        self._load_lock = asyncio.Lock()
        self._inference_lock = threading.Lock()
        self._is_downloading = False
        self._download_model_id: str | None = None
        self._last_error: str | None = None

    @property
    def active_model_id(self) -> str:
        return self._active_model_id

    def list_models(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for spec in NER_MODELS:
            path = _model_dir(spec)
            rows.append({
                **asdict(spec),
                "downloaded": _has_model_files(path),
                "local_path": str(path),
                "active": spec.model_id == self._active_model_id,
                "loaded": spec.model_id == self._loaded_model_id,
            })
        return rows

    def get_status(self) -> dict[str, Any]:
        spec = self._spec(self._active_model_id)
        deps = _deps_status()
        downloaded = _has_model_files(_model_dir(spec))
        return {
            "active_model_id": self._active_model_id,
            "loaded_model_id": self._loaded_model_id,
            "model_loaded": self._model is not None,
            "model_downloaded": downloaded,
            "needs_download": not downloaded,
            "is_downloading": self._is_downloading,
            "download_model_id": self._download_model_id,
            "model_dir": str(NER_MODELS_DIR),
            "default_model_id": DEFAULT_NER_MODEL_ID,
            "deps": deps,
            "last_error": self._last_error,
        }

    async def download_model(self, model_id: str | None = None, force: bool = False) -> dict[str, Any]:
        spec = self._spec(model_id or self._active_model_id)
        dest = _model_dir(spec)
        if _has_model_files(dest) and not force:
            return {
                "success": True,
                "already_downloaded": True,
                "model_id": spec.model_id,
                "local_path": str(dest),
            }
        if self._is_downloading:
            raise NerError("in_progress", f"NER download already in progress for {self._download_model_id}")

        self._is_downloading = True
        self._download_model_id = spec.model_id
        self._last_error = None
        try:
            from app.launcher import get_registry

            registry = get_registry()
            registry.starting(_REGISTRY_NAME, phase="download", model_id=spec.model_id)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._download_snapshot, spec, dest, force)
            registry.degraded(_REGISTRY_NAME, reason="model downloaded; not loaded", model_id=spec.model_id)
            return {
                "success": True,
                "already_downloaded": False,
                "model_id": spec.model_id,
                "repo_id": spec.repo_id,
                "local_path": str(dest),
            }
        except NerError:
            raise
        except Exception as exc:
            self._last_error = str(exc)
            raise NerError("download_failed", f"download failed for {spec.model_id}: {exc}") from exc
        finally:
            self._is_downloading = False
            self._download_model_id = None

    async def unload(self) -> dict[str, Any]:
        async with self._load_lock:
            self._model = None
            self._loaded_model_id = None
        return {"success": True}

    async def extract(
        self,
        *,
        text: str,
        labels: list[str] | dict[str, str],
        model_id: str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        max_chars: int = DEFAULT_MAX_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    ) -> NerExtraction:
        if not text.strip():
            raise NerError("empty_text", "text is empty")
        labels = _normalise_labels(labels)
        if threshold < 0.0 or threshold > 1.0:
            raise NerError("invalid_threshold", "threshold must be between 0.0 and 1.0")
        if max_chars < 500:
            raise NerError("invalid_chunk_size", "max_chars must be at least 500")
        if overlap_chars < 0 or overlap_chars >= max_chars:
            raise NerError("invalid_overlap", "overlap_chars must be >= 0 and < max_chars")

        spec = self._spec(model_id or self._active_model_id)
        model = await self._ensure_loaded(spec)
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()
        spans, chunk_count = await loop.run_in_executor(
            None,
            self._extract_blocking,
            model,
            spec,
            text,
            labels,
            threshold,
            max_chars,
            overlap_chars,
        )
        return NerExtraction(
            entities=spans,
            model_id=spec.model_id,
            repo_id=spec.repo_id,
            elapsed_seconds=time.monotonic() - t0,
            chunk_count=chunk_count,
        )

    async def extract_batch(
        self,
        *,
        texts: list[str],
        labels: list[str] | dict[str, str],
        model_id: str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        max_chars: int = DEFAULT_MAX_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    ) -> list[NerExtraction]:
        if not texts:
            raise NerError("empty_batch", "texts are required")
        if len(texts) > MAX_BATCH_ITEMS:
            raise NerError("batch_too_large", f"batch size must be <= {MAX_BATCH_ITEMS}")
        results: list[NerExtraction] = []
        for text in texts:
            results.append(
                await self.extract(
                    text=text,
                    labels=labels,
                    model_id=model_id,
                    threshold=threshold,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )
            )
        return results

    async def extract_pii(
        self,
        *,
        text: str,
        model_id: str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> NerExtraction:
        return await self.extract(
            text=text,
            labels=list(PII_LABELS),
            model_id=model_id,
            threshold=threshold,
        )

    def _spec(self, model_id: str) -> NerModelSpec:
        spec = NER_MODEL_BY_ID.get(model_id)
        if spec is None:
            raise NerError("unknown_model", f"unknown NER model: {model_id}")
        return spec

    def _download_snapshot(self, spec: NerModelSpec, dest: Path, force: bool) -> None:
        try:
            from huggingface_hub import snapshot_download
        except Exception as exc:
            raise NerError("missing_dependency", "huggingface_hub is required for NER downloads") from exc

        if force and dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / SNAPSHOT_COMPLETE_MARKER).unlink(missing_ok=True)
        snapshot_download(
            repo_id=spec.repo_id,
            local_dir=str(dest),
            local_dir_use_symlinks=False,
            ignore_patterns=["*.md", ".git*", "onnx/*", "openvino/*", "flax/*"],
        )
        (dest / SNAPSHOT_COMPLETE_MARKER).write_text(spec.repo_id)

    async def _ensure_loaded(self, spec: NerModelSpec) -> Any:
        async with self._load_lock:
            if self._model is not None and self._loaded_model_id == spec.model_id:
                return self._model

            dest = _model_dir(spec)
            if not _has_model_files(dest):
                raise NerError("model_not_downloaded", f"NER model not downloaded: {spec.model_id}")

            deps = _deps_status()
            if spec.backend == "gliner2" and not deps["modules"]["gliner2"]:
                raise NerError("missing_dependency", "gliner2 local inference package is not installed")
            if spec.backend == "gliner" and not deps["modules"]["gliner"]:
                raise NerError("missing_dependency", "gliner local inference package is not installed")

            loop = asyncio.get_running_loop()
            try:
                from app.launcher import get_registry

                registry = get_registry()
                registry.starting(_REGISTRY_NAME, phase="load", model_id=spec.model_id)
                self._model = await loop.run_in_executor(None, self._load_model, spec, dest)
                self._loaded_model_id = spec.model_id
                self._active_model_id = spec.model_id
                registry.ready(_REGISTRY_NAME, model_id=spec.model_id)
                return self._model
            except NerError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                try:
                    from app.launcher import get_registry

                    get_registry().failed(_REGISTRY_NAME, exc)
                except Exception:
                    pass
                raise NerError("load_failed", f"failed to load NER model {spec.model_id}: {exc}") from exc

    def _load_model(self, spec: NerModelSpec, dest: Path) -> Any:
        if spec.backend == "gliner2":
            from gliner2 import GLiNER2

            return GLiNER2.from_pretrained(str(dest))

        from gliner import GLiNER

        return GLiNER.from_pretrained(str(dest))

    def _extract_blocking(
        self,
        model: Any,
        spec: NerModelSpec,
        text: str,
        labels: list[str] | dict[str, str],
        threshold: float,
        max_chars: int,
        overlap_chars: int,
    ) -> tuple[list[NerSpan], int]:
        with self._inference_lock:
            if spec.backend == "gliner2" and len(text) > max_chars and hasattr(model, "extract_entities_long"):
                kwargs: dict[str, Any] = {
                    "chunk_size": 384,
                    "chunk_overlap": 64,
                    "include_spans": True,
                    "include_confidence": True,
                }
                try:
                    sig = inspect.signature(model.extract_entities_long)
                    if "threshold" in sig.parameters:
                        kwargs["threshold"] = threshold
                except (TypeError, ValueError):
                    pass
                raw = model.extract_entities_long(text, labels, **kwargs)
                chunk_count = len(_chunk_text(text, max_chars, overlap_chars))
                return _dedupe_spans(_normalise_entities(raw, text), threshold), chunk_count

            spans: list[NerSpan] = []
            chunks = _chunk_text(text, max_chars, overlap_chars)
            for chunk, offset in chunks:
                raw = self._predict_chunk(model, spec, chunk, labels, threshold)
                spans.extend(_normalise_entities(raw, chunk, offset))
            return _dedupe_spans(spans, threshold), len(chunks)

    def _predict_chunk(
        self,
        model: Any,
        spec: NerModelSpec,
        chunk: str,
        labels: list[str] | dict[str, str],
        threshold: float,
    ) -> Any:
        if spec.backend == "gliner2":
            kwargs = {"include_spans": True, "include_confidence": True}
            try:
                sig = inspect.signature(model.extract_entities)
                if "threshold" in sig.parameters:
                    kwargs["threshold"] = threshold
            except (TypeError, ValueError):
                pass
            return model.extract_entities(chunk, labels, **kwargs)

        try:
            return model.predict_entities(chunk, labels, threshold=threshold)
        except TypeError:
            return model.predict_entities(chunk, labels)


_SERVICE: NerService | None = None
_SERVICE_LOCK = threading.Lock()


def get_ner_service() -> NerService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = NerService()
    return _SERVICE
