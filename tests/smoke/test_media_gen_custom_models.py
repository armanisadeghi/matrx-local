"""In-process tests for custom image models (HF + Civitai) and the Civitai key.

Like test_media_gen_img2img_lora.py these do NOT use the spawned-engine
``http`` fixture — everything runs against a FastAPI TestClient (no lifespan)
or directly against module functions. NETWORK-FREE: every HF/Civitai HTTP
call goes through ``custom_models._http_get_json``, which is monkeypatched.
Run with:

    uv run --no-sync pytest tests/smoke/test_media_gen_custom_models.py -v

Covers:
  - ref parsing (HF repo id/URL, Civitai model/version URL/id, garbage → 400)
  - inspect: HF diffusers repo (mocked model_index) → family/format/size from
    the filtered listing; unknown pipeline class → not registerable
  - inspect: Civitai version → baseModel family mapping (incl. Pony → sdxl and
    unknown-baseModel refusal); LoRA type → 400 directing to /loras/download
  - registry round-trip: register → merged into GET /image-gen/models with
    custom=true → DELETE removes entry + weights dir
  - registration refusals: family "unknown", single_file for a family without
    from_single_file support — rejected AT REGISTRATION with the reason
  - Civitai key endpoints (mirrors the HF token pattern): PUT/GET/DELETE via
    /settings/api-keys/civitai — configured flag only, never the raw key
  - LoRA downloads from Civitai: type-mismatch rejection both directions,
    401 → friendly "Civitai API key" message, source=civitai in the store
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services.image_gen import custom_models as cm


@pytest.fixture(autouse=True)
def _force_image_gen_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic on CI where the optional image-gen packages are absent."""
    from app.services.image_gen import service as service_module

    monkeypatch.setattr(service_module, "DEPS_AVAILABLE", True)
    monkeypatch.setattr(service_module, "DEPS_REASON", "")


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    # No context manager on purpose: lifespan must NOT run.
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.fixture()
def model_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the custom registry AND the service's model dirs at a tmp dir."""
    from app.services.image_gen import service as service_module

    base = tmp_path / "image-models"
    monkeypatch.setattr(cm, "image_models_dir", lambda: base)
    monkeypatch.setattr(service_module, "image_models_dir", lambda: base)
    return base


@pytest.fixture()
def lora_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from app.services.image_gen import loras as loras_module

    base = tmp_path / "loras"
    monkeypatch.setattr(loras_module, "image_loras_dir", lambda: base)
    return base


@pytest.fixture()
def fake_download_manager(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture DownloadManager.enqueue calls (nothing is ever downloaded)."""
    from app.services.downloads import manager as manager_module

    enqueued: list[dict] = []

    class FakeManager:
        async def enqueue(self, **kwargs: Any):
            enqueued.append(kwargs)
            return SimpleNamespace(id=f"dl-{len(enqueued)}")

    monkeypatch.setattr(manager_module, "get_download_manager", lambda: FakeManager())
    return enqueued


def _mock_http(monkeypatch: pytest.MonkeyPatch, responses: dict[str, Any]) -> list[str]:
    """Replace custom_models._http_get_json with a URL→payload table.

    A payload that is an Exception is raised; unmatched URLs fail the test.
    Returns the list of requested URLs (ignoring query strings for matching).
    """
    requested: list[str] = []

    async def fake_get_json(url: str, headers: dict | None = None) -> Any:
        requested.append(url)
        for key, payload in responses.items():
            if url.split("?")[0] == key.split("?")[0]:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected HTTP call in a network-free test: {url}")

    monkeypatch.setattr(cm, "_http_get_json", fake_get_json)
    return requested


def _http_401(url: str) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        "401", request=req, response=httpx.Response(401, request=req)
    )


# ── ref parsing ───────────────────────────────────────────────────────────────


def test_parse_ref_variants() -> None:
    assert cm.parse_ref("acme/dream-xl") == {
        "kind": "hf",
        "repo_id": "acme/dream-xl",
        "weight_name": None,
    }
    assert cm.parse_ref("https://huggingface.co/acme/dream-xl/tree/main") == {
        "kind": "hf",
        "repo_id": "acme/dream-xl",
        "weight_name": None,
    }
    # A deep file URL names the exact weight — capture it so the user isn't
    # forced to re-pick a file they just pasted (subdirs preserved).
    assert cm.parse_ref(
        "https://huggingface.co/acme/dream-xl/resolve/main/pytorch_lora_weights.safetensors"
    ) == {
        "kind": "hf",
        "repo_id": "acme/dream-xl",
        "weight_name": "pytorch_lora_weights.safetensors",
    }
    assert cm.parse_ref(
        "https://huggingface.co/acme/dream-xl/blob/main/sub/dir/my_lora.safetensors"
    ) == {
        "kind": "hf",
        "repo_id": "acme/dream-xl",
        "weight_name": "sub/dir/my_lora.safetensors",
    }
    # A non-weight deep path (e.g. the config) resolves to the repo only.
    assert cm.parse_ref(
        "https://huggingface.co/acme/dream-xl/blob/main/model_index.json"
    ) == {"kind": "hf", "repo_id": "acme/dream-xl", "weight_name": None}
    # Multi-segment revisions (refs/pr/N) must not fold the ref into the file.
    assert (
        cm.parse_ref(
            "https://huggingface.co/acme/dream-xl/resolve/refs/pr/1/w.safetensors"
        )["weight_name"]
        == "w.safetensors"
    )
    assert cm.parse_ref("12345") == {
        "kind": "civitai",
        "model_id": 12345,
        "version_id": None,
    }
    assert cm.parse_ref("civitai:12@34") == {
        "kind": "civitai",
        "model_id": 12,
        "version_id": 34,
    }
    assert cm.parse_ref(
        "https://civitai.com/models/257749/pony-diffusion-v6-xl?modelVersionId=290640"
    ) == {"kind": "civitai", "model_id": 257749, "version_id": 290640}
    # .red is the full/NSFW front door — same path shape, must parse identically.
    assert cm.parse_ref(
        "https://civitai.red/models/1379962/amateur-instagramification"
        "?modelVersionId=2457938"
    ) == {"kind": "civitai", "model_id": 1379962, "version_id": 2457938}
    assert cm.parse_ref("https://civitai.com/api/download/models/290640") == {
        "kind": "civitai",
        "model_id": None,
        "version_id": 290640,
    }
    for bad in (
        "",
        "no-slash-not-a-repo!",
        "https://example.com/models/1",
        "https://huggingface.co/datasets/acme/x",
    ):
        with pytest.raises(cm.InspectError):
            cm.parse_ref(bad)


def test_map_civitai_base_model_table() -> None:
    assert cm.map_civitai_base_model("SDXL 1.0") == "sdxl"
    assert cm.map_civitai_base_model("Pony") == "sdxl"
    assert cm.map_civitai_base_model("Illustrious") == "sdxl"
    assert cm.map_civitai_base_model("SD 1.5") == "sd15"
    assert cm.map_civitai_base_model("Flux.1 D") == "flux"
    assert cm.map_civitai_base_model("Flux.1 S") == "flux"
    # Live Civitai string is the camel-concat form, not "Z-Image Turbo".
    assert cm.map_civitai_base_model("ZImageTurbo") == "z-image"
    assert cm.map_civitai_base_model("ZImageBase") == "z-image"
    assert cm.map_civitai_base_model("Z-Image Turbo") == "z-image"
    assert cm.map_civitai_base_model("SD 3.5") == "unknown"
    assert cm.map_civitai_base_model(None) == "unknown"


def test_estimate_hardware_conservative_floors() -> None:
    vram_small, ram_small = cm.estimate_hardware(2.0, "single_file")
    assert vram_small == 6.0 and ram_small == 10.0  # floors
    vram_big, ram_big = cm.estimate_hardware(12.0, "single_file")
    assert vram_big == 13.5 and ram_big == 17.5  # size + 1.5 / vram + 4
    vram_dif, _ = cm.estimate_hardware(20.0, "diffusers")
    assert vram_dif == 14.5  # 20 * 0.65 + 1.5


# ── inspect: HF ───────────────────────────────────────────────────────────────

_HF_API = "https://huggingface.co/api/models/acme/dream-xl"
_HF_INDEX = "https://huggingface.co/acme/dream-xl/raw/main/model_index.json"


def _hf_diffusers_responses(class_name: str = "StableDiffusionXLPipeline") -> dict:
    return {
        _HF_API: {
            "siblings": [
                {"rfilename": "model_index.json", "size": 500},
                {
                    "rfilename": "unet/diffusion_pytorch_model.safetensors",
                    "size": 5_000_000_000,
                },
                {
                    "rfilename": "unet/diffusion_pytorch_model.bin",
                    "size": 5_000_000_000,
                },
                {"rfilename": "text_encoder/model.safetensors", "size": 1_000_000_000},
                {
                    "rfilename": "vae/diffusion_pytorch_model.safetensors",
                    "size": 300_000_000,
                },
                {"rfilename": "sd_xl_dream.safetensors", "size": 7_000_000_000},
                {"rfilename": "README.md", "size": 1000},
            ],
            "gated": False,
            "cardData": {},
            "tags": [],
        },
        _HF_INDEX: {"_class_name": class_name},
    }


def test_inspect_hf_diffusers_repo(
    client: TestClient, model_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_http(monkeypatch, _hf_diffusers_responses())
    r = client.post("/image-gen/custom-models/inspect", json={"ref": "acme/dream-xl"})
    assert r.status_code == 200, r.text
    data = r.json()
    e = data["entry"]
    assert e["model_id"] == "custom/acme--dream-xl"
    assert e["source"] == "hf" and e["source_ref"] == "acme/dream-xl"
    assert e["format"] == "diffusers"
    assert e["family"] == "sdxl"
    assert e["pipeline_type"] == "stable-diffusion-xl"
    # size uses the SAME filter as the DownloadManager: .bin, README and the
    # root single-file duplicate are excluded → 5 + 1 + 0.3 GB
    assert e["size_gb"] == pytest.approx(6.3, abs=0.01)
    kept_names = {f["name"] for f in e["files"]}
    assert "unet/diffusion_pytorch_model.bin" not in kept_names
    assert "sd_xl_dream.safetensors" not in kept_names
    assert e["vram_gb"] >= 6.0 and e["ram_gb"] >= 10.0
    assert data["registerable"] is True and data["refusal_reason"] is None


def test_inspect_hf_unknown_pipeline_not_registerable(
    client: TestClient, model_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_http(monkeypatch, _hf_diffusers_responses("KandinskyV22Pipeline"))
    r = client.post("/image-gen/custom-models/inspect", json={"ref": "acme/dream-xl"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["entry"]["family"] == "unknown"
    assert data["registerable"] is False
    assert "not supported" in data["refusal_reason"]
    # ... and the same entry is REFUSED at registration, not at load time
    r2 = client.post("/image-gen/custom-models", json=data["entry"])
    assert r2.status_code == 400, r2.text
    assert "not supported" in r2.json()["detail"]


def test_inspect_unresolvable_ref_400(client: TestClient, model_store: Path) -> None:
    r = client.post("/image-gen/custom-models/inspect", json={"ref": "not a ref !!"})
    assert r.status_code == 400, r.text
    assert "Could not interpret" in r.json()["detail"]


# ── inspect: Civitai ─────────────────────────────────────────────────────────

_CIV_VER = "https://civitai.com/api/v1/model-versions/999"


def _civitai_version_payload(
    *, base_model: str = "Pony", model_type: str = "Checkpoint"
) -> dict:
    return {
        "id": 999,
        "modelId": 123,
        "name": "v6 XL",
        "baseModel": base_model,
        "model": {"name": "Pony Dream", "type": model_type},
        "files": [
            {
                "name": "ponyDreamV6.safetensors",
                "sizeKB": 6_500_000,
                "type": "Model",
                "primary": True,
                "metadata": {"format": "SafeTensor"},
                "downloadUrl": "https://civitai.com/api/download/models/999",
            },
            {
                "name": "ponyDreamV6.ckpt",
                "sizeKB": 6_500_000,
                "type": "Model",
                "metadata": {"format": "PickleTensor"},
                "downloadUrl": "https://civitai.com/api/download/models/999?format=PickleTensor",
            },
        ],
    }


def test_inspect_civitai_version_maps_family(
    client: TestClient, model_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_http(monkeypatch, {_CIV_VER: _civitai_version_payload()})
    r = client.post(
        "/image-gen/custom-models/inspect",
        json={"ref": "https://civitai.com/models/123?modelVersionId=999"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    e = data["entry"]
    assert e["model_id"] == "custom/civitai-123-999"
    assert e["source"] == "civitai"
    assert e["source_ref"] == "civitai:123@999"
    assert e["family"] == "sdxl"  # Pony is SDXL-architecture
    assert e["format"] == "single_file"
    assert e["weight_name"] == "ponyDreamV6.safetensors"  # safetensors preferred
    assert e["download_url"] == "https://civitai.com/api/download/models/999"
    assert e["size_gb"] == pytest.approx(6.5 * 1024 * 1000 / 1e6, rel=0.01)
    assert data["registerable"] is True
    assert any("Civitai" in w for w in data["warnings"])  # community warning


def test_inspect_civitai_unknown_base_model_refused(
    client: TestClient, model_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_http(monkeypatch, {_CIV_VER: _civitai_version_payload(base_model="SD 3.5")})
    r = client.post("/image-gen/custom-models/inspect", json={"ref": "civitai:123@999"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["entry"]["family"] == "unknown"
    assert data["registerable"] is False
    assert "not supported" in data["refusal_reason"]
    r2 = client.post("/image-gen/custom-models", json=data["entry"])
    assert r2.status_code == 400, r2.text


def test_inspect_civitai_lora_directed_to_lora_endpoint(
    client: TestClient, model_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_http(monkeypatch, {_CIV_VER: _civitai_version_payload(model_type="LORA")})
    r = client.post("/image-gen/custom-models/inspect", json={"ref": "civitai:123@999"})
    assert r.status_code == 400, r.text
    assert "loras/download" in r.json()["detail"]


def test_inspect_civitai_401_friendly_key_message(
    client: TestClient, model_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_http(monkeypatch, {_CIV_VER: _http_401(_CIV_VER)})
    r = client.post("/image-gen/custom-models/inspect", json={"ref": "civitai:123@999"})
    assert r.status_code == 401, r.text
    detail = r.json()["detail"]
    assert "API key" in detail and "Settings" in detail


# ── registry round-trip ───────────────────────────────────────────────────────


def _registered_pony_entry(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict:
    _mock_http(monkeypatch, {_CIV_VER: _civitai_version_payload()})
    r = client.post("/image-gen/custom-models/inspect", json={"ref": "civitai:123@999"})
    assert r.status_code == 200, r.text
    entry = r.json()["entry"]
    r = client.post("/image-gen/custom-models", json=entry)
    assert r.status_code == 200, r.text
    return {"entry": entry, "register": r.json()}


def test_register_roundtrip_models_merge_and_delete(
    client: TestClient,
    model_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_download_manager: list[dict],
) -> None:
    out = _registered_pony_entry(client, monkeypatch)
    reg = out["register"]
    assert reg["registered"] is True and reg["queued"] is True
    assert reg["model_id"] == "custom/civitai-123-999"
    assert reg["download_id"] == "dl-1"

    # download routed through the DownloadManager with the Civitai auth path
    assert len(fake_download_manager) == 1
    kw = fake_download_manager[0]
    assert kw["category"] == "image_gen"
    assert kw["urls"] == ["https://civitai.com/api/download/models/999"]
    md = kw["metadata"]
    assert md["civitai_download"] is True
    assert md["write_complete_marker"] is True
    assert md["dest_filename"] == "ponyDreamV6.safetensors"
    assert md["dest_dir"] == str(model_store / "custom--civitai-123-999")

    # registry file exists and is well-formed (atomic write)
    reg_file = model_store / "custom-models.json"
    assert reg_file.exists()
    stored = json.loads(reg_file.read_text(encoding="utf-8"))
    assert stored["version"] == 1 and len(stored["models"]) == 1

    # merged into GET /image-gen/models with custom=true
    r = client.get("/image-gen/models")
    assert r.status_code == 200, r.text
    models = {m["model_id"]: m for m in r.json()}
    m = models["custom/civitai-123-999"]
    assert m["custom"] is True
    assert m["source"] == "civitai"
    assert m["format"] == "single_file"
    assert m["lora_family"] == "sdxl"
    assert m["pipeline_type"] == "stable-diffusion-xl"
    assert m["supports_img2img"] is True and m["img2img_strength"] is True
    assert m["is_downloaded"] is False  # no completion marker yet
    # catalog entries are never flagged custom
    assert all(
        not v["custom"] for k, v in models.items() if not k.startswith("custom/")
    )

    # idempotent re-register
    r = client.post("/image-gen/custom-models", json=out["entry"])
    assert r.status_code == 200 and r.json()["already_registered"] is True

    # simulate a finished download → is_downloaded flips via the marker
    from app.services.media_gen.paths import DOWNLOAD_COMPLETE_MARKER

    d = model_store / "custom--civitai-123-999"
    d.mkdir(parents=True, exist_ok=True)
    (d / "ponyDreamV6.safetensors").write_bytes(b"\x00" * 8)
    (d / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")
    r = client.get("/image-gen/models")
    assert {m["model_id"]: m for m in r.json()}["custom/civitai-123-999"][
        "is_downloaded"
    ] is True

    # delete removes the registry entry AND the weights dir
    r = client.delete("/image-gen/custom-models/custom/civitai-123-999")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert not d.exists()
    r = client.get("/image-gen/models")
    assert "custom/civitai-123-999" not in {m["model_id"] for m in r.json()}
    # second delete → 404
    assert (
        client.delete("/image-gen/custom-models/custom/civitai-123-999").status_code
        == 404
    )


def test_register_single_file_unsupported_family_rejected(
    client: TestClient, model_store: Path, fake_download_manager: list[dict]
) -> None:
    """qwen has no from_single_file loader — refused AT REGISTRATION."""
    entry = {
        "model_id": "custom/acme--qwen-ckpt",
        "name": "Qwen ckpt",
        "source": "hf",
        "source_ref": "acme/qwen-ckpt",
        "family": "qwen",
        "pipeline_type": "qwen-image",
        "format": "single_file",
        "weight_name": "qwen.safetensors",
        "size_gb": 20.0,
    }
    r = client.post("/image-gen/custom-models", json=entry)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "qwen" in detail and "from_single_file" in detail
    assert fake_download_manager == [], "a refused entry must never enqueue"
    assert (
        not (model_store / "custom-models.json").exists()
        or json.loads((model_store / "custom-models.json").read_text(encoding="utf-8"))[
            "models"
        ]
        == []
    )


# ── Civitai API key endpoints (mirrors the HF token pattern) ─────────────────


class _FakeApiKeysRepo:
    store: dict[str, str] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def get_all(self) -> dict[str, str]:
        return dict(self.store)

    async def get(self, provider: str) -> str | None:
        return self.store.get(provider)

    async def set(self, provider: str, key: str) -> None:
        self.store[provider] = key

    async def delete(self, provider: str) -> None:
        self.store.pop(provider, None)

    async def is_configured(self, provider: str) -> bool:
        return bool(self.store.get(provider, "").strip())

    # The settings route reports each key's last validation verdict alongside
    # the masked value; this stub has never validated anything.
    async def get_validations(self) -> dict[str, dict[str, Any]]:
        return {}

    async def record_validation(
        self, provider: str, verdict: str, account: str | None
    ) -> None:
        return None

    async def clear_validation(self, provider: str) -> None:
        return None


def test_civitai_key_endpoints_masked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import settings_routes
    from app.services.ai import key_manager
    from app.services.local_db import repositories
    from app.services.media_gen.paths import read_civitai_key

    _FakeApiKeysRepo.store = {}
    monkeypatch.setattr(settings_routes, "ApiKeysRepo", _FakeApiKeysRepo)
    monkeypatch.setattr(repositories, "ApiKeysRepo", _FakeApiKeysRepo)
    monkeypatch.delenv("CIVITAI_API_KEY", raising=False)
    monkeypatch.delenv("CIVITAI_API_TOKEN", raising=False)
    key_manager._user_keys.pop("civitai", None)

    # listed as a provider, unconfigured
    r = client.get("/settings/api-keys")
    assert r.status_code == 200, r.text
    providers = {p["provider"]: p for p in r.json()["providers"]}
    assert "civitai" in providers
    assert providers["civitai"]["configured"] is False
    assert providers["civitai"]["label"] == "Civitai"

    # save — same shape as every other provider key
    secret = "civ-test-key-not-real-0001"
    r = client.put("/settings/api-keys/civitai", json={"key": secret})
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is True
    assert secret not in r.text, "the raw key must never be echoed back"

    # masked read-back: configured flag only, never the value
    r = client.get("/settings/api-keys")
    providers = {p["provider"]: p for p in r.json()["providers"]}
    assert providers["civitai"]["configured"] is True
    assert secret not in r.text

    # the download path resolves the key (env/cache injection)
    assert read_civitai_key() == secret

    # delete → unconfigured, key no longer resolvable
    r = client.delete("/settings/api-keys/civitai")
    assert r.status_code == 200 and r.json()["configured"] is False
    r = client.get("/settings/api-keys")
    providers = {p["provider"]: p for p in r.json()["providers"]}
    assert providers["civitai"]["configured"] is False
    assert read_civitai_key() is None


# ── LoRAs from Civitai / HF URLs ─────────────────────────────────────────────

_CIV_LORA_VER = "https://civitai.com/api/v1/model-versions/555"


def _civitai_lora_payload(model_type: str = "LORA") -> dict:
    return {
        "id": 555,
        "modelId": 321,
        "name": "v2",
        "baseModel": "Flux.1 D",
        "model": {"name": "Neon Style", "type": model_type},
        "files": [
            {
                "name": "neon-style-v2.safetensors",
                "sizeKB": 150_000,
                "type": "Model",
                "primary": True,
                "metadata": {"format": "SafeTensor"},
                "downloadUrl": "https://civitai.com/api/download/models/555",
            }
        ],
    }


def test_lora_download_from_civitai(
    client: TestClient,
    lora_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_download_manager: list[dict],
) -> None:
    _mock_http(monkeypatch, {_CIV_LORA_VER: _civitai_lora_payload()})
    r = client.post(
        "/image-gen/loras/download",
        json={"civitai": "https://civitai.com/models/321?modelVersionId=555"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["queued"] is True
    assert data["lora_id"] == "civitai--321-555"
    assert data["weight_name"] == "neon-style-v2.safetensors"
    assert data["base_family"] == "flux"  # Flux.1 D → flux
    assert data["source"] == "civitai"

    assert len(fake_download_manager) == 1
    kw = fake_download_manager[0]
    assert kw["category"] == "image_gen_lora"
    assert kw["urls"] == ["https://civitai.com/api/download/models/555"]
    md = kw["metadata"]
    assert md["civitai_download"] is True
    assert md["write_complete_marker"] is True
    assert md["validate_safetensors"] is True
    assert md["expected_sha256"] is None
    assert md["dest_filename"] == "neon-style-v2.safetensors"
    assert md["dest_dir"] == str(lora_store / "civitai--321-555")

    # pending sidecar visible immediately, carrying source=civitai
    r = client.get("/image-gen/loras")
    installed = {e["id"]: e for e in r.json()["installed"]}
    pend = installed["civitai--321-555"]
    assert pend["installed"] is False
    assert pend["source"] == "civitai"
    assert pend["repo_id"] == "civitai:321@555"
    assert pend["name"] == "Neon Style"
    assert pend["base_family"] == "flux"


def test_lora_download_civitai_checkpoint_rejected(
    client: TestClient,
    lora_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_download_manager: list[dict],
) -> None:
    _mock_http(
        monkeypatch, {_CIV_LORA_VER: _civitai_lora_payload(model_type="Checkpoint")}
    )
    r = client.post("/image-gen/loras/download", json={"civitai": "civitai:321@555"})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "Checkpoint" in detail and "custom-models" in detail
    assert fake_download_manager == []


def test_lora_download_civitai_401_friendly(
    client: TestClient,
    lora_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_download_manager: list[dict],
) -> None:
    _mock_http(monkeypatch, {_CIV_LORA_VER: _http_401(_CIV_LORA_VER)})
    r = client.post("/image-gen/loras/download", json={"civitai": "civitai:321@555"})
    assert r.status_code == 401, r.text
    detail = r.json()["detail"]
    assert "API key" in detail and "Settings" in detail
    assert fake_download_manager == []


def test_lora_download_requires_exactly_one_source(
    client: TestClient, lora_store: Path
) -> None:
    r = client.post("/image-gen/loras/download", json={})
    assert r.status_code == 400 and "exactly one" in r.json()["detail"]
    r = client.post(
        "/image-gen/loras/download",
        json={"repo_id": "acme/x-lora", "civitai": "123"},
    )
    assert r.status_code == 400 and "exactly one" in r.json()["detail"]


def test_lora_download_accepts_hf_url(
    client: TestClient,
    lora_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_download_manager: list[dict],
) -> None:
    """An HF URL in repo_id is normalized to the repo id."""
    from app.api import image_gen_routes

    async def fake_resolve(
        repo_id: str, weight_name: str | None, *, weight_is_hint: bool = False
    ):
        assert repo_id == "acme/neon-lora", repo_id
        return "neon.safetensors", "sdxl", None

    monkeypatch.setattr(image_gen_routes, "_resolve_lora_weight", fake_resolve)
    r = client.post(
        "/image-gen/loras/download",
        json={"repo_id": "https://huggingface.co/acme/neon-lora"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["lora_id"] == "acme--neon-lora"
    assert data["source"] == "hf"
    assert fake_download_manager[0]["metadata"]["hf_repo_id"] == "acme/neon-lora"


def test_lora_download_deep_hf_url_uses_captured_weight(
    client: TestClient,
    lora_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_download_manager: list[dict],
) -> None:
    """A deep HF file URL must install in ONE step — the weight captured from
    the URL flows into the resolver, so the user is never re-asked to pick a
    file they already pointed us at."""
    from app.api import image_gen_routes

    seen: dict[str, Any] = {}

    async def fake_resolve(
        repo_id: str, weight_name: str | None, *, weight_is_hint: bool = False
    ):
        seen["repo_id"] = repo_id
        seen["weight_name"] = weight_name
        seen["weight_is_hint"] = weight_is_hint
        return weight_name or "fallback.safetensors", "sdxl", None

    monkeypatch.setattr(image_gen_routes, "_resolve_lora_weight", fake_resolve)
    r = client.post(
        "/image-gen/loras/download",
        json={
            "repo_id": "https://huggingface.co/acme/neon-lora/resolve/main/neon_v2.safetensors"
        },
    )
    assert r.status_code == 200, r.text
    # A URL-captured weight flows in as a HINT (not a strict user-typed name),
    # so a slightly-off deep link still auto-resolves instead of failing.
    assert seen == {
        "repo_id": "acme/neon-lora",
        "weight_name": "neon_v2.safetensors",
        "weight_is_hint": True,
    }
    assert r.json()["weight_name"] == "neon_v2.safetensors"


def test_resolve_lora_weight_hint_vs_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the REAL _resolve_lora_weight (the route tests mock it out).

    The whole point of weight_is_hint is a strict-vs-soft fork: a URL-captured
    weight is a HINT that auto-resolves when it doesn't match a real repo file,
    while an explicitly-typed weight is validated strictly. Drive the real
    resolver against a mocked HF repo so both branches are actually covered.
    """
    import asyncio

    import huggingface_hub
    from fastapi import HTTPException

    from app.api import image_gen_routes
    from app.services.media_gen import paths as mg_paths

    class FakeHfApi:
        def __init__(self, token: Any = None) -> None:
            pass

        def model_info(self, repo_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                siblings=[
                    SimpleNamespace(rfilename="real_weight.safetensors"),
                    SimpleNamespace(rfilename="config.json"),
                ],
                card_data={"base_model": "stabilityai/stable-diffusion-xl-base-1.0"},
            )

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeHfApi)
    monkeypatch.setattr(mg_paths, "read_hf_token", lambda: None)

    def resolve(weight: str | None, *, hint: bool) -> tuple[str, str, str | None]:
        return asyncio.run(
            image_gen_routes._resolve_lora_weight(
                "acme/neon-lora", weight, weight_is_hint=hint
            )
        )

    # (a) URL-derived HINT that doesn't match a real file → auto-resolve to the
    #     repo's single real .safetensors instead of failing.
    chosen, _family, _title = resolve(
        "weight_from_a_slightly_off_url.safetensors", hint=True
    )
    assert chosen == "real_weight.safetensors"

    # (b) The SAME non-matching weight typed by the user (hint=False) → strict 400.
    with pytest.raises(HTTPException) as ei:
        resolve("weight_from_a_slightly_off_url.safetensors", hint=False)
    assert ei.value.status_code == 400
    assert "not a .safetensors file" in str(ei.value.detail)

    # (c) A hint that DOES match a real file is honored exactly.
    chosen_match, _, _ = resolve("real_weight.safetensors", hint=True)
    assert chosen_match == "real_weight.safetensors"

    # (d) No weight at all → auto-resolve to the single candidate.
    chosen_auto, _, _ = resolve(None, hint=False)
    assert chosen_auto == "real_weight.safetensors"
