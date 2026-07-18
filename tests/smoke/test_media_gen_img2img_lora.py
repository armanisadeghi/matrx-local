"""In-process tests for image-to-image + LoRA support (image generation).

Like test_media_gen_cancel.py these do NOT use the spawned-engine ``http``
fixture — everything runs against a FastAPI TestClient (no lifespan) or
directly against service/store instances with STUB pipelines. No real model
is ever loaded, no network is touched. Run with:

    uv run pytest tests/smoke/test_media_gen_img2img_lora.py -v

Covers:
  - catalog exposes supports_img2img / img2img_strength / lora_family
  - /params gains a common.strength default for strength-capable img2img
    families (and deliberately NOT for flux2-klein)
  - request validation 400s: strength without an init image, garbage
    init_image_b64, unknown LoRA id, LoRA base-family mismatch
  - the service routes an init-image generation through the img2img pipeline
    with strength passed (and drops width/height for SD/SDXL img2img sigs)
  - LoRA apply → set_adapters → pipe() → unload ORDER, including unload on a
    failed load (pipeline always left clean; error names the LoRA)
  - job records + media-library sidecars carry the new fields (never the
    init image bytes — only its sha256)
  - /image-gen/loras HTTP contract (fabricated store dirs): list, download
    routes through the DownloadManager, delete
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.image_gen.jobs import ImageJobStore
from app.services.image_gen.models import IMAGE_GEN_MODELS
from app.services.image_gen.service import ImageGenService, prepare_init_image

SDXL_MODEL = next(
    m for m in IMAGE_GEN_MODELS if m.pipeline_type == "stable-diffusion-xl"
)
FLUX_MODEL = next(m for m in IMAGE_GEN_MODELS if m.pipeline_type == "flux")
KLEIN_MODEL = next(m for m in IMAGE_GEN_MODELS if m.pipeline_type == "flux2-klein")


@pytest.fixture(autouse=True)
def _force_image_gen_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests use STUB pipelines — they must be hermetic on CI where the
    optional image-gen packages (diffusers/transformers/accelerate) are not
    installed. Without this, ImageGenService.available is False and every
    stub generation bails with "requires optional packages" before running
    (exactly what broke CI on 2026-07-10)."""
    from app.services.image_gen import service as service_module

    monkeypatch.setattr(service_module, "DEPS_AVAILABLE", True)
    monkeypatch.setattr(service_module, "DEPS_REASON", "")
    # LoRA apply tests stub the pipeline — peft is an optional managed-package
    # dep that CI/dev venvs may not carry; the guard is tested separately.
    monkeypatch.setattr(service_module, "_ensure_peft_for_loras", lambda: None)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    # No context manager on purpose: lifespan must NOT run (it would start
    # engine services). Plain requests still traverse the middleware stack.
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.fixture()
def lora_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the LoRA store at an empty tmp dir (loras.py resolves the dir
    through its own imported image_loras_dir symbol)."""
    from app.services.image_gen import loras as loras_module

    base = tmp_path / "loras"
    monkeypatch.setattr(loras_module, "image_loras_dir", lambda: base)
    return base


def _install_fake_lora(
    base: Path, repo_id: str, *, base_family: str, weight_name: str = "w.safetensors"
) -> str:
    """Fabricate a fully-installed LoRA on disk; returns its store id."""
    from app.services.image_gen.loras import lora_id_for_repo, write_lora_meta
    from app.services.media_gen.paths import DOWNLOAD_COMPLETE_MARKER

    lora_id = lora_id_for_repo(repo_id)
    write_lora_meta(
        lora_id, repo_id=repo_id, weight_name=weight_name, base_family=base_family
    )
    d = base / lora_id
    (d / weight_name).write_bytes(b"\x00" * 16)
    (d / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")
    return lora_id


def _png_b64(width: int = 8, height: int = 8) -> tuple[str, bytes]:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 40, 40)).save(buf, format="PNG")
    raw = buf.getvalue()
    return base64.b64encode(raw).decode("ascii"), raw


def _isolate_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from app.services.media_gen import library as library_module

    media_dir = tmp_path / "media"
    monkeypatch.setattr(library_module, "generated_media_dir", lambda: media_dir)
    return media_dir


def _isolate_image_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services.image_gen import jobs as jobs_module

    monkeypatch.setattr(
        jobs_module, "generated_images_dir", lambda: tmp_path / "img-jobs"
    )


# ── catalog + params contract ─────────────────────────────────────────────────


def test_models_expose_img2img_and_lora_family(client: TestClient) -> None:
    r = client.get("/image-gen/models")
    assert r.status_code == 200, r.text
    models = {m["model_id"]: m for m in r.json()}
    for m in models.values():
        for key in ("supports_img2img", "img2img_strength", "lora_family"):
            assert key in m, f"{m['model_id']} missing {key}"
    assert models[SDXL_MODEL.model_id]["supports_img2img"] is True
    assert models[SDXL_MODEL.model_id]["lora_family"] == "sdxl"
    # flux2-klein: img2img via reference image, but NO strength knob
    assert models[KLEIN_MODEL.model_id]["supports_img2img"] is True
    assert models[KLEIN_MODEL.model_id]["img2img_strength"] is False


def test_params_strength_default_only_for_strength_families(client: TestClient) -> None:
    r = client.get(f"/image-gen/params/{SDXL_MODEL.model_id}")
    assert r.status_code == 200, r.text
    assert r.json()["common"]["strength"] == 0.6

    r = client.get(f"/image-gen/params/{KLEIN_MODEL.model_id}")
    assert r.status_code == 200, r.text
    assert "strength" not in r.json()["common"], (
        "flux2-klein has no strength parameter — exposing a default would be "
        "a silent lie"
    )


def test_load_reports_missing_selected_text_encoder_as_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import image_gen_routes

    class StubService:
        available = True
        unavailable_reason = ""

        async def load_model(self, model_id: str, text_encoder_id: str | None):
            assert model_id == KLEIN_MODEL.model_id
            assert text_encoder_id == "candidate"
            return {
                "success": False,
                "error": "Text encoder 'candidate' is not downloaded.",
                "needs_text_encoder_download": True,
            }

    monkeypatch.setattr(
        image_gen_routes, "get_image_gen_service", lambda: StubService()
    )
    response = client.post(
        "/image-gen/load",
        json={"model_id": KLEIN_MODEL.model_id, "text_encoder_id": "candidate"},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["detail"] == "Text encoder 'candidate' is not downloaded."
    assert body["needs_text_encoder_download"] is True


# ── request validation 400s ───────────────────────────────────────────────────


def test_strength_without_init_image_400(client: TestClient) -> None:
    for path in ("/image-gen/generate", "/image-gen/jobs"):
        r = client.post(
            path,
            json={
                "prompt": "x",
                "model_id": SDXL_MODEL.model_id,
                "strength": 0.5,
            },
        )
        assert r.status_code == 400, f"{path}: {r.status_code} {r.text}"
        assert "input image" in r.json()["detail"], r.text


def test_garbage_init_image_b64_400(client: TestClient) -> None:
    for payload in ("!!!not-base64!!!", base64.b64encode(b"not an image").decode()):
        for path in ("/image-gen/generate", "/image-gen/jobs"):
            r = client.post(
                path,
                json={
                    "prompt": "x",
                    "model_id": SDXL_MODEL.model_id,
                    "init_image_b64": payload,
                },
            )
            assert r.status_code == 400, f"{path}: {r.status_code} {r.text}"
            assert "PNG or JPEG" in r.json()["detail"], r.text


def test_strength_rejected_for_flux2_klein(client: TestClient) -> None:
    b64, _ = _png_b64()
    r = client.post(
        "/image-gen/jobs",
        json={
            "prompt": "x",
            "model_id": KLEIN_MODEL.model_id,
            "init_image_b64": b64,
            "strength": 0.5,
        },
    )
    assert r.status_code == 400, r.text
    assert "strength" in r.json()["detail"]


def test_unknown_lora_id_400_names_it(client: TestClient, lora_store: Path) -> None:
    for path in ("/image-gen/generate", "/image-gen/jobs"):
        r = client.post(
            path,
            json={
                "prompt": "x",
                "model_id": SDXL_MODEL.model_id,
                "loras": [{"id": "no-such--lora", "scale": 1.0}],
            },
        )
        assert r.status_code == 400, f"{path}: {r.status_code} {r.text}"
        assert "no-such--lora" in r.json()["detail"], r.text


def test_lora_family_mismatch_400(client: TestClient, lora_store: Path) -> None:
    lora_id = _install_fake_lora(lora_store, "acme/style-sdxl", base_family="sdxl")
    r = client.post(
        "/image-gen/jobs",
        json={
            "prompt": "x",
            "model_id": FLUX_MODEL.model_id,
            "loras": [{"id": lora_id, "scale": 1.0}],
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "sdxl" in detail and "flux" in detail, (
        f"mismatch must name both families: {detail}"
    )


# ── stub pipelines ────────────────────────────────────────────────────────────


class RecordingPipe:
    """Text-to-image stub that records LoRA + call activity."""

    def __init__(self, calls: list, fail_lora_load: bool = False) -> None:
        self.calls = calls
        self.fail_lora_load = fail_lora_load

    def load_lora_weights(
        self, path: str, *, weight_name: str, adapter_name: str
    ) -> None:
        self.calls.append(("load_lora_weights", weight_name, adapter_name))
        if self.fail_lora_load:
            raise RuntimeError("synthetic LoRA load failure")

    def set_adapters(self, names: list, adapter_weights: list) -> None:
        self.calls.append(("set_adapters", tuple(names), tuple(adapter_weights)))

    def unload_lora_weights(self) -> None:
        self.calls.append(("unload_lora_weights",))

    def __call__(
        self,
        prompt: str,
        num_inference_steps: int,
        width: int,
        height: int,
        generator: Any = None,
        guidance_scale: float | None = None,
        negative_prompt: str | None = None,
        callback_on_step_end: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(("call", "t2i"))
        from PIL import Image

        return SimpleNamespace(
            images=[Image.new("RGB", (width, height), (10, 200, 30))]
        )


class RecordingImg2ImgPipe:
    """img2img stub with an SDXL-like signature: image + strength, NO
    width/height (the service must pre-fit the image and drop the dims)."""

    def __init__(self, calls: list) -> None:
        self.calls = calls
        self.kwargs: dict[str, Any] | None = None

    def load_lora_weights(
        self, path: str, *, weight_name: str, adapter_name: str
    ) -> None:
        self.calls.append(("load_lora_weights", weight_name, adapter_name))

    def set_adapters(self, names: list, adapter_weights: list) -> None:
        self.calls.append(("set_adapters", tuple(names), tuple(adapter_weights)))

    def unload_lora_weights(self) -> None:
        self.calls.append(("unload_lora_weights",))

    def __call__(
        self,
        prompt: str,
        num_inference_steps: int,
        image: Any,
        strength: float = 0.3,
        generator: Any = None,
        guidance_scale: float | None = None,
        negative_prompt: str | None = None,
        callback_on_step_end: Any = None,
    ) -> Any:
        self.calls.append(("call", "img2img"))
        self.kwargs = {
            "num_inference_steps": num_inference_steps,
            "image": image,
            "strength": strength,
        }
        return SimpleNamespace(images=[image])


def _make_stub_service(pipe: Any, model=SDXL_MODEL) -> ImageGenService:
    svc = ImageGenService()
    svc._pipeline = pipe
    svc._loaded_model_id = model.model_id
    svc._device = "cpu"
    return svc


# ── service: img2img routing ──────────────────────────────────────────────────


def test_prepare_init_image_aspect_fill_center_crop() -> None:
    _, raw = _png_b64(20, 10)
    out = prepare_init_image(raw, 64, 64)
    assert (out.width, out.height) == (64, 64)
    assert out.mode == "RGB"
    with pytest.raises(ValueError, match="decoded"):
        prepare_init_image(b"garbage", 64, 64)


def test_img2img_routes_to_img2img_pipe_with_strength(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("torch")
    from app.services.image_gen import service as service_module

    media_dir = _isolate_library(monkeypatch, tmp_path)
    calls: list = []
    t2i = RecordingPipe(calls)
    i2i = RecordingImg2ImgPipe(calls)
    monkeypatch.setattr(service_module, "_to_img2img", lambda pipe, model: i2i)
    svc = _make_stub_service(t2i)

    _, raw = _png_b64(32, 16)
    result = asyncio.run(
        svc.generate(
            prompt="an edit",
            model_id=SDXL_MODEL.model_id,
            steps=4,
            width=64,
            height=64,
            init_image_bytes=raw,
            strength=0.7,
        )
    )
    assert result.success is True, result.error
    assert ("call", "img2img") in calls and ("call", "t2i") not in calls, (
        "init_image must route to the img2img pipeline, never text-to-image"
    )
    assert i2i.kwargs is not None
    assert i2i.kwargs["strength"] == 0.7
    # aspect-fill + center-crop to the requested dims (SDXL i2i has no
    # width/height params — the image itself carries the size)
    assert (i2i.kwargs["image"].width, i2i.kwargs["image"].height) == (64, 64)

    # sidecar: strength + init_image_sha256 recorded; image bytes NOT persisted
    sidecars = list((media_dir / "images").glob("*.json"))
    assert len(sidecars) == 1
    meta = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert meta["params"]["strength"] == 0.7
    assert meta["params"]["has_init_image"] is True
    assert meta["params"]["init_image_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "image" not in meta["params"], "the init image must never be persisted"


def test_flux_revision_lineage_is_persisted_with_the_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("torch")
    from app.services.image_gen import service as service_module

    media_dir = _isolate_library(monkeypatch, tmp_path)
    calls: list = []
    i2i = RecordingImg2ImgPipe(calls)
    monkeypatch.setattr(service_module, "_to_img2img", lambda pipe, model: i2i)
    svc = _make_stub_service(RecordingPipe(calls), model=FLUX_MODEL)

    _, raw = _png_b64()
    result = asyncio.run(
        svc.generate(
            prompt="keep the scene, make the jacket red",
            model_id=FLUX_MODEL.model_id,
            steps=4,
            width=32,
            height=32,
            init_image_bytes=raw,
            strength=0.6,
            revision_parent_item_id="parent-1",
            revision_root_item_id="root-1",
        )
    )
    assert result.success is True, result.error
    meta = json.loads(
        next((media_dir / "images").glob("*.json")).read_text(encoding="utf-8")
    )
    assert meta["params"]["pipeline_type"] == "flux"
    assert meta["params"]["revision_parent_item_id"] == "parent-1"
    assert meta["params"]["revision_root_item_id"] == "root-1"


def test_img2img_default_strength_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("torch")
    from app.services.image_gen import service as service_module

    _isolate_library(monkeypatch, tmp_path)
    calls: list = []
    i2i = RecordingImg2ImgPipe(calls)
    monkeypatch.setattr(service_module, "_to_img2img", lambda pipe, model: i2i)
    svc = _make_stub_service(RecordingPipe(calls))

    _, raw = _png_b64()
    result = asyncio.run(
        svc.generate(
            prompt="x",
            model_id=SDXL_MODEL.model_id,
            steps=4,
            width=32,
            height=32,
            init_image_bytes=raw,
        )
    )
    assert result.success is True, result.error
    assert i2i.kwargs is not None and i2i.kwargs["strength"] == 0.6


def test_img2img_zero_effective_steps_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """sdxl-turbo's 1 recommended step x default strength 0.6 rounds to zero
    denoising steps — must fail with actionable text, not a deep pipe error."""
    pytest.importorskip("torch")
    from app.services.image_gen import service as service_module

    _isolate_library(monkeypatch, tmp_path)
    calls: list = []
    i2i = RecordingImg2ImgPipe(calls)
    monkeypatch.setattr(service_module, "_to_img2img", lambda pipe, model: i2i)
    svc = _make_stub_service(RecordingPipe(calls))

    _, raw = _png_b64()
    result = asyncio.run(
        svc.generate(
            prompt="x",
            model_id=SDXL_MODEL.model_id,
            steps=1,  # 1 * 0.6 → 0
            width=32,
            height=32,
            init_image_bytes=raw,
        )
    )
    assert result.success is False
    assert "strength" in (result.error or "")
    assert ("call", "img2img") not in calls


def test_service_guard_strength_without_image() -> None:
    svc = _make_stub_service(RecordingPipe([]))
    result = asyncio.run(
        svc.generate(
            prompt="x",
            model_id=SDXL_MODEL.model_id,
            strength=0.5,
        )
    )
    assert result.success is False
    assert "input image" in (result.error or "")


# ── service: LoRA apply/unload ordering ──────────────────────────────────────


def test_lora_apply_call_unload_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lora_store: Path
) -> None:
    pytest.importorskip("torch")
    _isolate_library(monkeypatch, tmp_path)
    lora_id = _install_fake_lora(lora_store, "acme/style-sdxl", base_family="sdxl")

    calls: list = []
    pipe = RecordingPipe(calls)
    svc = _make_stub_service(pipe)
    result = asyncio.run(
        svc.generate(
            prompt="x",
            model_id=SDXL_MODEL.model_id,
            steps=2,
            width=32,
            height=32,
            loras=[{"id": lora_id, "scale": 0.8}],
        )
    )
    assert result.success is True, result.error
    kinds = [c[0] for c in calls]
    assert kinds == [
        "load_lora_weights",
        "set_adapters",
        "call",
        "unload_lora_weights",
    ], f"LoRA lifecycle order wrong: {kinds}"
    assert calls[0][1] == "w.safetensors"
    assert calls[1][2] == (0.8,)  # adapter_weights carries the scale


def test_lora_failed_load_aborts_and_still_unloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lora_store: Path
) -> None:
    pytest.importorskip("torch")
    _isolate_library(monkeypatch, tmp_path)
    lora_id = _install_fake_lora(lora_store, "acme/broken-sdxl", base_family="sdxl")

    calls: list = []
    pipe = RecordingPipe(calls, fail_lora_load=True)
    svc = _make_stub_service(pipe)
    result = asyncio.run(
        svc.generate(
            prompt="x",
            model_id=SDXL_MODEL.model_id,
            steps=2,
            width=32,
            height=32,
            loras=[{"id": lora_id, "scale": 1.0}],
        )
    )
    assert result.success is False
    assert lora_id in (result.error or ""), "the failed LoRA must be named"
    kinds = [c[0] for c in calls]
    assert "call" not in kinds, "generation must abort on a failed LoRA load"
    assert kinds[-1] == "unload_lora_weights", (
        "the pipeline must be left clean even after a failed load"
    )


def test_lora_family_mismatch_service_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lora_store: Path
) -> None:
    pytest.importorskip("torch")
    _isolate_library(monkeypatch, tmp_path)
    lora_id = _install_fake_lora(lora_store, "acme/style-sdxl", base_family="sdxl")

    calls: list = []
    pipe = RecordingPipe(calls)
    svc = _make_stub_service(pipe, model=FLUX_MODEL)
    result = asyncio.run(
        svc.generate(
            prompt="x",
            model_id=FLUX_MODEL.model_id,
            steps=2,
            width=32,
            height=32,
            loras=[{"id": lora_id, "scale": 1.0}],
        )
    )
    assert result.success is False
    assert "sdxl" in (result.error or "") and "flux" in (result.error or "")
    assert ("load_lora_weights", "w.safetensors", "matrx_lora_0") not in calls, (
        "a known-family mismatch must fail BEFORE any weights load"
    )


def test_lora_sidecar_records_applied_loras(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lora_store: Path
) -> None:
    pytest.importorskip("torch")
    media_dir = _isolate_library(monkeypatch, tmp_path)
    lora_id = _install_fake_lora(lora_store, "acme/style-sdxl", base_family="sdxl")

    svc = _make_stub_service(RecordingPipe([]))
    result = asyncio.run(
        svc.generate(
            prompt="x",
            model_id=SDXL_MODEL.model_id,
            steps=2,
            width=32,
            height=32,
            loras=[{"id": lora_id, "scale": 0.5}],
        )
    )
    assert result.success is True, result.error
    meta = json.loads(
        next((media_dir / "images").glob("*.json")).read_text(encoding="utf-8")
    )
    assert meta["params"]["loras"] == [
        {
            "id": lora_id,
            "repo_id": "acme/style-sdxl",
            "weight_name": "w.safetensors",
            "scale": 0.5,
        }
    ]


# ── job records carry the new fields ──────────────────────────────────────────


def test_job_record_carries_img2img_and_lora_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_image_history(monkeypatch, tmp_path)
    store = ImageJobStore()
    _, raw = _png_b64()
    sha = hashlib.sha256(raw).hexdigest()
    assert store.put_init_image(raw) == sha
    job = store.create(
        prompt="x",
        model_id=SDXL_MODEL.model_id,
        has_init_image=True,
        init_image_sha256=sha,
        strength=0.4,
        loras=[{"id": "acme--style-sdxl", "scale": 0.9}],
    )

    d = job.to_dict()
    assert d["has_init_image"] is True
    assert d["init_image_sha256"] == sha
    assert d["strength"] == 0.4
    assert d["loras"] == [{"id": "acme--style-sdxl", "scale": 0.9}]
    assert "init_image_b64" not in d and "init_image_bytes" not in d

    # Bytes are readable for as long as the job is pending — and idempotently,
    # so a retry after a failed attempt still has its input image.
    assert store.get_init_image(job.job_id) == raw
    assert store.get_init_image(job.job_id) == raw

    # The job record persists the sha only — never the bytes.
    history = json.loads(
        (tmp_path / "img-jobs" / "jobs.json").read_text(encoding="utf-8")
    )
    record = history["jobs"][0]
    assert record["init_image_sha256"] == sha
    assert record["strength"] == 0.4
    assert "init_image_b64" not in record and "init_image_bytes" not in record

    # The BYTES live beside it, content-addressed, so a restart can still run
    # the job (a queued img2img job with no input is dead work).
    store2 = ImageJobStore()
    store2.load()
    j2 = store2.get(job.job_id)
    assert j2 is not None and j2.init_image_sha256 == sha and j2.loras
    assert j2.status == "queued", "a restart must not kill a queued job"
    assert store2.get_init_image(job.job_id) == raw


def test_jobs_api_echoes_new_fields(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lora_store: Path,
) -> None:
    """Enqueue an img2img+LoRA job through the API (model 'downloaded' via
    monkeypatch, runner suppressed) and read the fields back."""
    from app.api import image_gen_routes
    from app.services.image_gen import jobs as jobs_module

    _isolate_image_history(monkeypatch, tmp_path)
    lora_id = _install_fake_lora(lora_store, "acme/style-sdxl", base_family="sdxl")

    store = ImageJobStore()
    monkeypatch.setattr(jobs_module, "_store", store)
    monkeypatch.setattr(jobs_module, "_runner", None)

    class NoRunRunner:
        def ensure_running(self) -> None:  # never start the worker in tests
            pass

    monkeypatch.setattr(jobs_module, "get_image_job_runner", lambda: NoRunRunner())
    monkeypatch.setattr(
        image_gen_routes,
        "get_image_gen_service",
        lambda: SimpleNamespace(
            available=True,
            unavailable_reason="",
            get_model=lambda mid: SDXL_MODEL if mid == SDXL_MODEL.model_id else None,
            is_downloaded=lambda mid: True,
        ),
    )

    b64, raw = _png_b64()
    r = client.post(
        "/image-gen/jobs",
        json={
            "prompt": "an edit",
            "model_id": SDXL_MODEL.model_id,
            "init_image_b64": b64,
            "strength": 0.55,
            "loras": [{"id": lora_id, "scale": 0.7}],
        },
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    r = client.get(f"/image-gen/jobs/{job_id}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["has_init_image"] is True
    assert d["init_image_sha256"] == hashlib.sha256(raw).hexdigest()
    assert d["strength"] == 0.55
    assert d["loras"] == [{"id": lora_id, "scale": 0.7}]
    assert store.get_init_image(job_id) == raw, "bytes must be available to the runner"


def test_revision_job_contract_is_durable_and_replayable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.api import image_gen_routes
    from app.services.image_gen import jobs as jobs_module

    _isolate_image_history(monkeypatch, tmp_path)
    store = ImageJobStore()
    monkeypatch.setattr(jobs_module, "_store", store)

    class NoRunRunner:
        def ensure_running(self) -> None:
            pass

    monkeypatch.setattr(jobs_module, "get_image_job_runner", lambda: NoRunRunner())
    monkeypatch.setattr(
        image_gen_routes,
        "get_image_gen_service",
        lambda: SimpleNamespace(
            available=True,
            unavailable_reason="",
            get_model=lambda mid: FLUX_MODEL if mid == FLUX_MODEL.model_id else None,
            is_downloaded=lambda mid: True,
        ),
    )

    b64, _ = _png_b64()
    response = client.post(
        "/image-gen/jobs",
        json={
            "prompt": "make the jacket red",
            "model_id": FLUX_MODEL.model_id,
            "init_image_b64": b64,
            "strength": 0.55,
            "revision": {
                "parent_item_id": "parent-1",
                "root_item_id": "root-1",
            },
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    body = client.get(f"/image-gen/jobs/{job_id}").json()
    assert body["revision_parent_item_id"] == "parent-1"
    assert body["revision_root_item_id"] == "root-1"
    assert body["params"]["revision_parent_item_id"] == "parent-1"
    assert body["params"]["revision_root_item_id"] == "root-1"
    assert body["params"]["has_init_image"] is True

    store2 = ImageJobStore()
    store2.load()
    restored = store2.get(job_id)
    assert restored is not None
    assert restored.revision_parent_item_id == "parent-1"
    assert restored.revision_root_item_id == "root-1"


def test_revision_requires_an_input_and_a_z_image_or_flux_model(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import image_gen_routes

    model_by_id = {m.model_id: m for m in (FLUX_MODEL, SDXL_MODEL)}
    monkeypatch.setattr(
        image_gen_routes,
        "get_image_gen_service",
        lambda: SimpleNamespace(
            available=True,
            unavailable_reason="",
            get_model=model_by_id.get,
            is_downloaded=lambda mid: True,
        ),
    )
    missing_input = client.post(
        "/image-gen/jobs",
        json={
            "prompt": "edit",
            "model_id": FLUX_MODEL.model_id,
            "revision": {"parent_item_id": "parent-1"},
        },
    )
    assert missing_input.status_code == 400
    assert "init_image_b64" in missing_input.text

    b64, _ = _png_b64()
    unsupported = client.post(
        "/image-gen/jobs",
        json={
            "prompt": "edit",
            "model_id": SDXL_MODEL.model_id,
            "init_image_b64": b64,
            "revision": {"parent_item_id": "parent-1"},
        },
    )
    assert unsupported.status_code == 400
    assert "Z-Image or FLUX" in unsupported.text


# ── /loras HTTP contract ──────────────────────────────────────────────────────


def test_resolve_lora_display_name_catalog_backfill() -> None:
    from app.services.image_gen.loras import resolve_lora_display_name

    catalog = {
        "civitai:580857@2674760": {"name": "Realistic Skin Texture (ZTurbo v4.5)"},
    }
    assert (
        resolve_lora_display_name(
            {"repo_id": "civitai:580857@2674760", "id": "civitai--580857-2674760"},
            catalog_by_repo=catalog,
        )
        == "Realistic Skin Texture (ZTurbo v4.5)"
    )
    assert (
        resolve_lora_display_name(
            {
                "repo_id": "civitai:580857@2674760",
                "id": "civitai--580857-2674760",
                "name": "Stored Civitai title",
            },
            catalog_by_repo=catalog,
        )
        == "Stored Civitai title"
    )
    assert resolve_lora_display_name({"repo_id": "acme/x", "id": "acme--x"}) is None


def test_loras_list_contract(client: TestClient, lora_store: Path) -> None:
    lora_id = _install_fake_lora(lora_store, "acme/style-sdxl", base_family="sdxl")

    r = client.get("/image-gen/loras")
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {"installed", "catalog"}
    installed = {e["id"]: e for e in data["installed"]}
    assert lora_id in installed
    e = installed[lora_id]
    assert e["repo_id"] == "acme/style-sdxl"
    assert e["weight_name"] == "w.safetensors"
    assert e["base_family"] == "sdxl"
    assert e["size_bytes"] == 16
    assert e["installed"] is True and e["added_at"]
    assert e.get("name") is None

    assert len(data["catalog"]) >= 4
    for c in data["catalog"]:
        for key in (
            "repo_id",
            "name",
            "description",
            "weight_name",
            "base_family",
            "license",
            "source",
            "unverified",
            "installed",
        ):
            assert key in c, f"catalog entry missing {key}: {c}"
        assert c["unverified"] is False, (
            "current curated entries were all verified against live HF / "
            "Civitai metadata"
        )
        assert c["source"] in ("hf", "civitai")
    zit = [c for c in data["catalog"] if c["base_family"] == "z-image"]
    assert zit, "curated catalog must include Z-Image Turbo LoRAs"
    assert all(
        c["source"] == "civitai" and c["repo_id"].startswith("civitai:") for c in zit
    )


def test_loras_list_exposes_civitai_name_from_sidecar(
    client: TestClient, lora_store: Path
) -> None:
    from app.services.image_gen.loras import write_lora_meta
    from app.services.media_gen.paths import DOWNLOAD_COMPLETE_MARKER

    lora_id = "civitai--580857-2674760"
    write_lora_meta(
        lora_id,
        repo_id="civitai:580857@2674760",
        weight_name="skin.safetensors",
        base_family="z-image",
        source="civitai",
        extra={"name": "Realistic Skin Texture (ZTurbo v4.5)"},
    )
    d = lora_store / lora_id
    (d / "skin.safetensors").write_bytes(b"\x00" * 16)
    (d / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")

    r = client.get("/image-gen/loras")
    assert r.status_code == 200
    row = {e["id"]: e for e in r.json()["installed"]}[lora_id]
    assert row["name"] == "Realistic Skin Texture (ZTurbo v4.5)"


def test_lora_download_routes_through_download_manager(
    client: TestClient, lora_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import image_gen_routes
    from app.services.downloads import manager as manager_module

    async def fake_resolve(
        repo_id: str, weight_name: str | None, *, weight_is_hint: bool = False
    ):
        return "style.safetensors", "sdxl", None

    enqueued: list[dict] = []

    class FakeManager:
        async def enqueue(self, **kwargs: Any):
            enqueued.append(kwargs)
            return SimpleNamespace(id="dl-123")

    monkeypatch.setattr(image_gen_routes, "_resolve_lora_weight", fake_resolve)
    monkeypatch.setattr(manager_module, "get_download_manager", lambda: FakeManager())

    r = client.post("/image-gen/loras/download", json={"repo_id": "acme/new-style"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["queued"] is True
    assert data["download_id"] == "dl-123"
    assert data["lora_id"] == "acme--new-style"
    assert data["weight_name"] == "style.safetensors"

    assert len(enqueued) == 1, "the download MUST route through the DownloadManager"
    kw = enqueued[0]
    assert kw["category"] == "image_gen_lora"
    assert kw["metadata"]["hf_repo_id"] == "acme/new-style"
    assert kw["metadata"]["hf_allow_files"] == ["style.safetensors"]
    assert kw["metadata"]["dest_dir"] == str(lora_store / "acme--new-style")

    # the pending sidecar exists immediately (installed=false until the marker)
    r = client.get("/image-gen/loras")
    pend = {e["id"]: e for e in r.json()["installed"]}["acme--new-style"]
    assert pend["installed"] is False


def test_lora_delete_contract(client: TestClient, lora_store: Path) -> None:
    lora_id = _install_fake_lora(lora_store, "acme/tmp-lora", base_family="sdxl")
    r = client.delete(f"/image-gen/loras/{lora_id}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert not (lora_store / lora_id).exists()
    assert client.delete(f"/image-gen/loras/{lora_id}").status_code == 404
    # path traversal shapes are rejected as unknown, never resolved
    assert client.delete("/image-gen/loras/..%2F..%2Fetc").status_code == 404


# ── base-family heuristics ────────────────────────────────────────────────────


def test_guess_base_family() -> None:
    from app.services.image_gen.loras import guess_base_family

    assert (
        guess_base_family(
            "acme/thing", None, "stabilityai/stable-diffusion-xl-base-1.0"
        )
        == "sdxl"
    )
    assert (
        guess_base_family("acme/thing", None, "black-forest-labs/FLUX.1-dev") == "flux"
    )
    assert (
        guess_base_family("acme/thing", None, "black-forest-labs/FLUX.2-klein-4B")
        == "flux2"
    )
    assert (
        guess_base_family(
            "acme/thing", None, "stable-diffusion-v1-5/stable-diffusion-v1-5"
        )
        == "sd15"
    )
    assert guess_base_family(
        "nerijs/pixel-art-xl", "pixel-art-xl.safetensors", None
    ) in ("sdxl", "unknown")
    assert (
        guess_base_family(
            "civitai:2268008@2617751",
            "RealisticSnapshot-Zimage-Turbov5.safetensors",
            "ZImageTurbo",
        )
        == "z-image"
    )
    assert (
        guess_base_family("acme/mystery-lora", "weights.safetensors", None) == "unknown"
    )
