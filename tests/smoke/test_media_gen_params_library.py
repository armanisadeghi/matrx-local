"""In-process tests for media-gen parameter exposure, the media library, and
the image job queue.

IMPORTANT: unlike the other smoke tests these do NOT use the spawned-engine
``http`` fixture — everything runs in-process against a FastAPI TestClient
(no lifespan → no services started, no engine touched). Run with:

    uv run pytest tests/smoke/test_media_gen_params_library.py -v

Covers:
  - merge_extra_params / validate_pipeline_kwargs unit semantics
  - GET /image-gen/params/{model_id} and /video-gen/params/{model_id}
  - /media-library list → file → delete round-trip on a fabricated item
  - ImageJobStore queue/cancel semantics + jobs API validation paths
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.media_gen.params import (
    image_effective_params,
    merge_extra_params,
    validate_pipeline_kwargs,
)

# A tiny valid-enough PNG payload (magic bytes only — the library never
# parses the image, it just stores bytes).
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    # No context manager on purpose: lifespan must NOT run (it would start
    # engine services). Plain requests still traverse the middleware stack.
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


# ── extra_params merge semantics (unit) ───────────────────────────────────────


def test_merge_extra_params_user_values_win() -> None:
    base = {"prompt": "a cat", "num_inference_steps": 4, "guidance_scale": 1.0}
    merged = merge_extra_params(base, {"guidance_scale": 7.5, "eta": 0.3})
    assert merged["guidance_scale"] == 7.5, "extra_params must override defaults"
    assert merged["eta"] == 0.3, "new keys must pass through"
    assert merged["num_inference_steps"] == 4, "untouched defaults must survive"
    assert base["guidance_scale"] == 1.0, "inputs must not be mutated"


def test_merge_extra_params_empty_is_noop() -> None:
    base = {"prompt": "a cat", "width": 512}
    assert merge_extra_params(base, None) == base
    assert merge_extra_params(base, {}) == base


def test_merge_extra_params_protects_prompt() -> None:
    with pytest.raises(ValueError, match="prompt"):
        merge_extra_params({"prompt": "a cat"}, {"prompt": "hijacked"})


def test_validate_pipeline_kwargs_names_offenders() -> None:
    class FakePipe:
        def __call__(self, prompt=None, num_inference_steps=None, guidance_scale=None):
            pass

    pipe = FakePipe()
    validate_pipeline_kwargs(pipe, [])  # no keys — no-op
    validate_pipeline_kwargs(pipe, ["guidance_scale"])  # valid key — ok
    with pytest.raises(ValueError) as exc_info:
        validate_pipeline_kwargs(pipe, ["guidance_scale", "bogus_knob"])
    assert "bogus_knob" in str(exc_info.value), "error must name the bad parameter"
    assert "guidance_scale" not in str(exc_info.value).split(":")[1].split("bogus")[0]


def test_validate_pipeline_kwargs_var_kwargs_passthrough() -> None:
    class KwargsPipe:
        def __call__(self, prompt=None, **kwargs):
            pass

    validate_pipeline_kwargs(KwargsPipe(), ["anything_goes"])  # must not raise


# ── params endpoints ──────────────────────────────────────────────────────────


def test_image_params_endpoint_sdxl(client: TestClient) -> None:
    r = client.get("/image-gen/params/stabilityai/sdxl-turbo")
    assert r.status_code == 200, f"unexpected {r.status_code}: {r.text}"
    data = r.json()
    assert set(data) == {"common", "advanced", "supports_negative_prompt"}
    common = data["common"]
    for key in ("steps", "guidance", "width", "height", "negative_prompt", "seed"):
        assert key in common, f"common missing {key}: {common}"
    assert common["seed"] is None
    assert common["steps"] >= 1
    adv = data["advanced"]
    # SDXL family: guidance_scale is always passed; DDIM knobs exposed.
    for key in ("guidance_scale", "num_images_per_prompt", "eta", "guidance_rescale"):
        assert key in adv, f"advanced missing {key}: {adv}"
    from app.services.image_gen.models import IMAGE_GEN_MODELS

    catalog = next(
        m for m in IMAGE_GEN_MODELS if m.model_id == "stabilityai/sdxl-turbo"
    )
    assert data["supports_negative_prompt"] == catalog.supports_negative_prompt


def test_image_params_endpoint_qwen_uses_true_cfg(client: TestClient) -> None:
    r = client.get("/image-gen/params/Qwen/Qwen-Image")
    assert r.status_code == 200, f"unexpected {r.status_code}: {r.text}"
    adv = r.json()["advanced"]
    assert "true_cfg_scale" in adv, "Qwen passes true_cfg_scale, not guidance_scale"
    assert "guidance_scale" not in adv


def test_image_params_endpoint_unknown_model_404(client: TestClient) -> None:
    r = client.get("/image-gen/params/nope/does-not-exist")
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"


def test_video_params_endpoint(client: TestClient) -> None:
    r = client.get("/video-gen/params/Lightricks/LTX-Video")
    assert r.status_code == 200, f"unexpected {r.status_code}: {r.text}"
    data = r.json()
    common = data["common"]
    for key in (
        "steps",
        "guidance",
        "width",
        "height",
        "num_frames",
        "fps",
        "negative_prompt",
        "seed",
    ):
        assert key in common, f"common missing {key}: {common}"
    assert common["num_frames"] >= 9
    assert common["fps"] >= 4
    assert "guidance_scale" in data["advanced"]
    r404 = client.get("/video-gen/params/nope/does-not-exist")
    assert r404.status_code == 404


def test_image_effective_params_matches_catalog() -> None:
    from app.services.image_gen.models import IMAGE_GEN_MODELS

    for model in IMAGE_GEN_MODELS:
        data = image_effective_params(model)
        assert data["common"]["steps"] == model.recommended_steps
        assert data["common"]["width"] == model.default_width
        assert data["supports_negative_prompt"] == model.supports_negative_prompt


# ── media library round-trip ──────────────────────────────────────────────────


@pytest.fixture()
def library_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point the media library at a throwaway dir for the duration of a test."""
    from app.services.media_gen import library

    monkeypatch.setattr(library, "generated_media_dir", lambda: tmp_path / "generated")
    return library


def test_media_library_round_trip(client: TestClient, library_tmp) -> None:
    item = library_tmp.save_generated_image(
        FAKE_PNG,
        model_id="stabilityai/sdxl-turbo",
        prompt="a fabricated test image",
        negative_prompt="blurry",
        params={
            "num_inference_steps": 1,
            "guidance_scale": 0.0,
            "width": 512,
            "height": 512,
            "custom_knob": 0.5,
        },
        seed=1234,
        width=512,
        height=512,
        elapsed_seconds=0.01,
    )
    item_id = item["id"]
    assert Path(item["file_path"]).exists()

    # list — newest first, full metadata + absolute path
    r = client.get("/media-library/items", params={"media_type": "image"})
    assert r.status_code == 200, f"unexpected {r.status_code}: {r.text}"
    body = r.json()
    assert body["total"] == 1
    listed = body["items"][0]
    assert listed["id"] == item_id
    assert listed["media_type"] == "image"
    assert listed["prompt"] == "a fabricated test image"
    assert listed["seed"] == 1234
    assert listed["params"]["custom_knob"] == 0.5, "extra_params must be in sidecar"
    assert listed["file_path"] == item["file_path"]
    assert listed["file_size_bytes"] == len(FAKE_PNG)
    assert "T" in listed["created_at"], "created_at must be ISO"

    # file — correct bytes + content type
    rf = client.get(f"/media-library/file/{item_id}")
    assert rf.status_code == 200
    assert rf.headers["content-type"].startswith("image/png")
    assert rf.content == FAKE_PNG

    # delete — file AND sidecar gone; second delete is a loud 404
    rd = client.delete(f"/media-library/items/{item_id}")
    assert rd.status_code == 200 and rd.json()["deleted"] is True
    assert not Path(item["file_path"]).exists()
    assert client.get(f"/media-library/file/{item_id}").status_code == 404
    assert client.delete(f"/media-library/items/{item_id}").status_code == 404
    assert client.get("/media-library/items").json()["total"] == 0


def test_media_library_init_image_round_trip(client: TestClient, library_tmp) -> None:
    """The img2img SOURCE image is stored beside the result and served back.

    This is what makes "Remix" honest: without it the client could restore every
    setting except the input image the generation actually started from.
    """
    init_bytes = FAKE_PNG + b"\x00init"
    item = library_tmp.save_generated_image(
        FAKE_PNG,
        model_id="stabilityai/sdxl-turbo",
        prompt="img2img result",
        negative_prompt="",
        params={"has_init_image": True},
        seed=7,
        width=512,
        height=512,
        elapsed_seconds=0.01,
        init_image_bytes=init_bytes,
    )
    item_id = item["id"]

    listed = client.get("/media-library/items").json()["items"][0]
    assert listed["init_image_file"] == f"{item_id}.init.png", (
        "the client decides whether to offer a full Remix off this field"
    )

    r = client.get(f"/media-library/items/{item_id}/init-image")
    assert r.status_code == 200
    assert r.content == init_bytes
    assert r.headers["content-type"].startswith("image/png")

    # Deleting the item takes the source image with it — leaving the plaintext
    # input behind after the user deleted (or vaulted) the result would be a leak.
    init_path = Path(item["file_path"]).with_suffix("").with_name(f"{item_id}.init.png")
    assert init_path.exists()
    assert client.delete(f"/media-library/items/{item_id}").status_code == 200
    assert not init_path.exists()
    assert client.get(f"/media-library/items/{item_id}/init-image").status_code == 404


def test_media_library_no_init_image_is_404(client: TestClient, library_tmp) -> None:
    """A text-to-image item advertises no source image and 404s if asked."""
    item = library_tmp.save_generated_image(
        FAKE_PNG,
        model_id="m",
        prompt="txt2img",
        negative_prompt="",
        params={},
        seed=1,
        width=8,
        height=8,
        elapsed_seconds=0.0,
    )
    listed = client.get("/media-library/items").json()["items"][0]
    assert listed["init_image_file"] is None
    r = client.get(f"/media-library/items/{item['id']}/init-image")
    assert r.status_code == 404


def test_media_library_unknown_and_malicious_ids(
    client: TestClient, library_tmp
) -> None:
    assert client.get(f"/media-library/file/{uuid.uuid4()}").status_code == 404
    # Non-uuid ids (e.g. traversal attempts) must 404, never touch the fs.
    r = client.get("/media-library/file/..%2f..%2fetc%2fpasswd")
    assert r.status_code == 404


def test_media_library_pagination(client: TestClient, library_tmp) -> None:
    ids = []
    for i in range(3):
        item = library_tmp.save_generated_image(
            FAKE_PNG,
            model_id="m",
            prompt=f"p{i}",
            negative_prompt="",
            params={},
            seed=i,
            width=8,
            height=8,
            elapsed_seconds=0.0,
        )
        ids.append(item["id"])
    r = client.get("/media-library/items", params={"limit": 2, "offset": 1})
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


def _real_png_bytes(size: int = 64) -> bytes:
    """A real decodeable PNG — FAKE_PNG is storage-only and cannot be thumbed."""
    from PIL import Image
    import io

    img = Image.new("RGB", (size, size), color=(40, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_media_library_thumb_self_heals(client: TestClient, library_tmp) -> None:
    """Missing .thumb.jpg is generated on GET, written to disk, and served.

    This is the self-healing contract: no backfill job; the next thumb request
    regenerates anything missing or deleted.
    """
    pytest.importorskip("PIL")
    item = library_tmp.save_generated_image(
        _real_png_bytes(128),
        model_id="m",
        prompt="thumb me",
        negative_prompt="",
        params={},
        seed=1,
        width=128,
        height=128,
        elapsed_seconds=0.0,
    )
    media_path = Path(item["file_path"])
    thumb_path = media_path.with_name(f"{item['id']}.thumb.jpg")
    # save_generated_image best-effort writes a thumb — delete it to prove heal.
    thumb_path.unlink(missing_ok=True)
    assert not thumb_path.exists()

    r = client.get(f"/media-library/thumb/{item['id']}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/jpeg")
    assert len(r.content) > 100
    assert thumb_path.is_file() and thumb_path.stat().st_size > 0

    # Second request is a cache hit (same bytes on disk).
    r2 = client.get(f"/media-library/thumb/{item['id']}")
    assert r2.status_code == 200
    assert r2.content == thumb_path.read_bytes()

    # Delete takes the thumb with it.
    assert client.delete(f"/media-library/items/{item['id']}").status_code == 200
    assert not thumb_path.exists()
    assert client.get(f"/media-library/thumb/{item['id']}").status_code == 404


def test_media_library_thumb_unknown_id(client: TestClient) -> None:
    assert client.get(f"/media-library/thumb/{uuid.uuid4()}").status_code == 404


# ── image job queue ───────────────────────────────────────────────────────────


@pytest.fixture()
def job_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A fresh ImageJobStore persisted into a throwaway dir (no worker)."""
    from app.services.image_gen import jobs

    monkeypatch.setattr(jobs, "generated_images_dir", lambda: tmp_path / "img-jobs")
    store = jobs.ImageJobStore()
    store.load()
    return store


def test_image_job_store_fifo_and_cancel(job_store) -> None:
    j1 = job_store.create(prompt="one", model_id="m")
    j2 = job_store.create(prompt="two", model_id="m")
    assert job_store.queued_count() == 2
    assert job_store.next_queued().job_id == j1.job_id, "FIFO order"

    # Cancel a queued job → record kept as cancelled, skipped by the queue.
    assert job_store.cancel(j1.job_id) == "cancelled"
    assert job_store.get(j1.job_id).status == "cancelled"
    assert job_store.next_queued().job_id == j2.job_id

    # Running jobs cannot be cancelled.
    assert job_store.mark_running(j2.job_id, total_steps=4, seed=42) is True
    assert job_store.get(j2.job_id).seed == 42, "concrete seed recorded"
    assert job_store.cancel(j2.job_id) == "running"

    # Completed jobs are removable; the record disappears.
    job_store.mark_completed(
        j2.job_id, item_id="itm", file_path="/tmp/x.png", elapsed_seconds=1.5
    )
    assert job_store.get(j2.job_id).item_id == "itm"
    assert job_store.cancel(j2.job_id) == "removed"
    assert job_store.get(j2.job_id) is None
    assert job_store.cancel(str(uuid.uuid4())) == "not_found"


def test_image_job_store_cancelled_never_starts(job_store) -> None:
    j = job_store.create(prompt="x", model_id="m")
    assert job_store.cancel(j.job_id) == "cancelled"
    assert job_store.mark_running(j.job_id, total_steps=4, seed=1) is False, (
        "a job cancelled between dequeue and start must not be resurrected"
    )


def test_image_jobs_api_validation(client: TestClient) -> None:
    # Listing always works (may include real history from this machine).
    r = client.get("/image-gen/jobs")
    assert r.status_code == 200 and isinstance(r.json(), list)

    # Unknown model → 404 (or 503 when the AI packages are absent).
    r = client.post(
        "/image-gen/jobs",
        json={
            "prompt": "test",
            "model_id": "__matrx_smoke_unknown_model__",
        },
    )
    assert r.status_code in (404, 503), f"got {r.status_code}: {r.text}"

    # extra_params cannot hijack the prompt — rejected before enqueue.
    from app.services.image_gen.models import IMAGE_GEN_MODELS

    real_model = IMAGE_GEN_MODELS[0].model_id
    r = client.post(
        "/image-gen/jobs",
        json={
            "prompt": "test",
            "model_id": real_model,
            "extra_params": {"prompt": "hijacked"},
        },
    )
    # 400 = protected-param rejection; 409 = model not downloaded (checked
    # first); 503 = packages absent. All are loud, none enqueue.
    assert r.status_code in (400, 409, 503), f"got {r.status_code}: {r.text}"

    # Unknown job id → loud 404s.
    assert client.get(f"/image-gen/jobs/{uuid.uuid4()}").status_code == 404
    assert client.delete(f"/image-gen/jobs/{uuid.uuid4()}").status_code == 404
