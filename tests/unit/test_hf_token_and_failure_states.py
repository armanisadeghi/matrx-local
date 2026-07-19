"""Regression tests for the two download-failure doctrines.

1. TOKEN ATTACHMENT (MXL "the system is not seeing my token"): every Hugging
   Face call resolves the token AT REQUEST TIME from the app key store —
   ``read_hf_token`` must return the key-manager cache value even when nothing
   was ever mirrored into os.environ. The .env / environ path is a dev shim,
   never the source of truth for user tokens.

2. STATES, NOT ERRORS: expected user-actionable failures (token missing, HF
   license gate not accepted, access pending, Civitai key flows) are mapped to
   ``DownloadResolution`` states with precise attribution — a user WITH a
   configured token is never told to add one — and stale pre-taxonomy failure
   rows are re-triaged onto the same taxonomy.
"""

from __future__ import annotations

import asyncio
import gc
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app.services.downloads import failures
from app.services.downloads.failures import (
    ActionableDownloadError,
    retriage_stale_failure,
)
from app.services.downloads.manager import (
    _classify_hf_auth_failure,
    _hf_repo_from_url,
    _is_hf_url,
)


# ── 1. Request-time token resolution from the app key store ────────────────


def test_read_hf_token_resolves_from_key_store_cache(monkeypatch):
    """The token must come from the key-manager cache even with a clean
    environment — the exact failure mode was 'token is set in the app, request
    goes out unauthenticated'."""
    from app.services.ai import key_manager
    from app.services.media_gen import paths

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setattr(key_manager, "_user_keys", {"huggingface": "hf_store_token"})
    assert paths.read_hf_token() == "hf_store_token"


def test_read_hf_token_reflects_key_rotation_immediately(monkeypatch):
    """Request-time resolution: a key saved AFTER startup must be picked up by
    the very next call — no restart, no env mirror required."""
    from app.services.ai import key_manager
    from app.services.media_gen import paths

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    cache: dict[str, str] = {}
    monkeypatch.setattr(key_manager, "_user_keys", cache)
    # huggingface_hub's own cached-token store must not leak into this test.
    with patch("huggingface_hub.get_token", return_value=None, create=True):
        assert paths.read_hf_token() is None
        cache["huggingface"] = "hf_rotated"
        assert paths.read_hf_token() == "hf_rotated"


def _configure_ready_runtime_for_upgrade_check(
    monkeypatch: pytest.MonkeyPatch,
    versions: dict[str, str],
) -> None:
    """Provide authoritative slot/contract state around a version-policy test."""
    from app.services.image_gen import installer
    from app.services.image_gen.runtime_state import RuntimePhase, RuntimeSnapshot

    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(installer, "_compatibility_migration_pending", lambda: False)
    monkeypatch.setattr(installer, "is_image_gen_installed", lambda: True)
    monkeypatch.setattr(
        installer,
        "authoritative_snapshot",
        lambda: RuntimeSnapshot(
            state=RuntimePhase.READY,
            runtime_revision="contract-revision",
            active_slot="verified-slot",
            packages=dict(versions),
        ),
    )
    monkeypatch.setattr(
        installer,
        "load_runtime_install_contract",
        lambda: SimpleNamespace(
            runtime_revision="contract-revision",
            target="test-target",
            packages=dict(versions),
        ),
    )
    monkeypatch.setattr(installer, "validate_slot", lambda *args, **kwargs: (True, "", {}))
    monkeypatch.setattr(installer, "get_installed_package_versions", lambda: versions)
    monkeypatch.setattr(installer, "_get_torchvision_torch_requirement", lambda: "2.11.0")


def test_needs_upgrade_when_peft_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.image_gen import installer

    versions = {
        "diffusers": "0.39.0",
        "transformers": "5.3.0",
        "gguf": "0.17.1",
        "torch": "2.11.0",
        "torchvision": "0.26.0",
    }
    _configure_ready_runtime_for_upgrade_check(monkeypatch, versions)
    assert installer.needs_upgrade() is True

    versions["peft"] = "0.19.1"
    assert installer.needs_upgrade() is False


def test_needs_upgrade_when_torchvision_requires_another_torch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.image_gen import installer

    versions = {
        "diffusers": "0.39.0",
        "transformers": "5.3.0",
        "peft": "0.19.1",
        "gguf": "0.17.1",
        "torch": "2.13.0",
        "torchvision": "0.26.0",
    }
    _configure_ready_runtime_for_upgrade_check(monkeypatch, versions)
    assert installer.needs_upgrade() is True


def test_torchvision_torch_requirement_is_read_without_importing_native_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.image_gen import installer

    metadata_dir = tmp_path / "torchvision-0.26.0.dist-info"
    metadata_dir.mkdir()
    (metadata_dir / "METADATA").write_text(
        "Metadata-Version: 2.4\n"
        "Name: torchvision\n"
        "Version: 0.26.0\n"
        "Requires-Dist: torch (==2.11.0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(installer, "get_image_gen_packages_dir", lambda: tmp_path)

    assert installer._get_torchvision_torch_requirement() == "2.11.0"


def test_managed_image_runtime_cannot_shadow_engine_core_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A verified slot fills missing imports but remains behind bundled core."""
    from app.services.image_gen import installer

    monkeypatch.setattr(installer.sys, "path", ["/frozen-engine"])

    assert installer.inject_image_gen_path(tmp_path) is True
    assert installer.sys.path == ["/frozen-engine", str(tmp_path)]


def test_frozen_runtime_hook_withholds_verifier_and_appends_verified_slot() -> None:
    hook = (
        Path(__file__).resolve().parents[2] / "hooks" / "runtime_hook.py"
    ).read_text(encoding="utf-8")

    assert 'os.getenv("MATRX_FROZEN_RUNTIME_VERIFY") == "1"' in hook
    assert "sys.path.append(_runtime_slot_text)" in hook
    assert "sys.path.insert(0, _runtime_slot_text)" not in hook


def test_needs_upgrade_when_diffusers_predates_z_image_lora_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.image_gen import installer

    versions = {
        "diffusers": "0.37.1",
        "transformers": "5.3.0",
        "peft": "0.19.1",
        "gguf": "0.17.1",
        "torch": "2.11.0",
        "torchvision": "0.26.0",
    }
    _configure_ready_runtime_for_upgrade_check(monkeypatch, versions)
    assert installer.needs_upgrade() is True


def test_install_status_prefers_canonical_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import image_gen_routes

    monkeypatch.setattr(
        image_gen_routes,
        "get_runtime_status",
        lambda: {
            "state": "failed",
            "stage": "validating",
            "percent": 72.0,
            "message": "Runtime validation failed.",
            "failure_detail": "frozen activation failed",
            "log_lines": ["verification failed"],
        },
    )

    response = asyncio.run(image_gen_routes.get_install_status())

    assert response.status == "error"
    assert response.error == "frozen activation failed"
    assert response.already_installed is False


def test_interrupted_staged_install_is_durable_and_retriable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.image_gen import installer, runtime_state

    lock = tmp_path / "requirements.txt"
    lock.write_text("diffusers==0.39.0 --hash=sha256:" + "a" * 64, encoding="utf-8")
    contract = installer.RuntimeInstallContract(
        contract_sha256="b" * 64,
        target="test-target",
        requirements_file=lock,
        packages={"diffusers": "0.39.0"},
        record_hashes={},
    )
    monkeypatch.setattr(runtime_state, "packages_dir", lambda name: tmp_path / name)
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(installer, "load_runtime_install_contract", lambda: contract)
    monkeypatch.setattr(
        installer,
        "_run_pip_streaming",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    progress = installer.InstallProgress()
    installer._install_runtime_sync(progress, None, "install", "attempt-1")

    snapshot = runtime_state.read_snapshot()
    assert progress.status == "error"
    assert snapshot.state is runtime_state.RuntimePhase.FAILED
    assert snapshot.failure_code == "install_failed"
    assert snapshot.failure_detail == "offline"
    assert list(runtime_state.runtime_slots_dir().glob("*.staging-*")) == []


def test_background_runtime_failure_is_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.image_gen import installer

    def fail(progress: installer.InstallProgress) -> None:
        progress.fail("migration exploded")
        raise RuntimeError("migration exploded")

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        unhandled: list[dict] = []
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        installer._background_futures.clear()
        progress = installer.InstallProgress()

        installer._submit_background(fail, progress)
        for _ in range(100):
            if not installer._background_futures:
                break
            await asyncio.sleep(0.001)
        gc.collect()
        await asyncio.sleep(0)

        assert progress.status == "error"
        assert not installer._background_futures
        assert unhandled == []

    asyncio.run(exercise())


def test_nonready_runtime_is_hard_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.image_gen import installer, service

    monkeypatch.setattr(service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(service, "DEPS_AVAILABLE", True)
    monkeypatch.setattr(service, "DEPS_REASON", "")
    monkeypatch.setattr(
        installer,
        "get_runtime_status",
        lambda: {
            "state": "failed",
            "failure_detail": "Runtime update required.",
            "message": "Runtime unavailable.",
        },
    )
    svc = service.ImageGenService()

    assert svc.available is False
    assert "update required" in svc.unavailable_reason




def test_ensure_peft_for_loras_raises_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import sys

    from app.services.image_gen import service as svc

    real_import = builtins.__import__

    def _import_without_peft(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "peft":
            raise ImportError("No module named 'peft'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _import_without_peft)
    sys.modules.pop("peft", None)
    with pytest.raises(RuntimeError, match="PEFT package"):
        svc._ensure_peft_for_loras()


def test_read_civitai_key_resolves_from_key_store_cache(monkeypatch):
    from app.services.ai import key_manager
    from app.services.media_gen import paths

    monkeypatch.delenv("CIVITAI_API_KEY", raising=False)
    monkeypatch.delenv("CIVITAI_API_TOKEN", raising=False)
    monkeypatch.setattr(key_manager, "_user_keys", {"civitai": "civ_key"})
    assert paths.read_civitai_key() == "civ_key"


def test_hf_url_detection_hub_only():
    """The bearer token is attached to the Hub, never to signed CDN hosts."""
    assert _is_hf_url("https://huggingface.co/org/model/resolve/main/w.gguf")
    assert _is_hf_url("https://hf.co/org/model/resolve/main/w.gguf")
    assert not _is_hf_url("https://cdn-lfs.huggingface.co/signed/blob")
    assert not _is_hf_url("https://civitai.com/api/download/models/1")
    assert not _is_hf_url("not a url")


def test_hf_repo_id_parsed_from_resolve_url():
    assert (
        _hf_repo_from_url(
            "https://huggingface.co/unsloth/Llama-GGUF/resolve/main/q4.gguf"
        )
        == "unsloth/Llama-GGUF"
    )
    assert _hf_repo_from_url("https://huggingface.co/api/models/x") is None


# ── 2. Attribution: 401/403 classified into the thing the user must do ─────


def _http_401(status: int = 401) -> Exception:
    exc = Exception("401 Client Error")
    exc.response = SimpleNamespace(status_code=status)  # type: ignore[attr-defined]
    return exc


def _classify(exc, token, verdict="valid"):
    result = SimpleNamespace(verdict=verdict, message="")

    async def fake_validate(provider, tok):
        return result

    with patch("app.services.ai.key_validation.validate_key", fake_validate):
        return asyncio.run(
            _classify_hf_auth_failure(exc, "black-forest-labs/FLUX.1-schnell", token)
        )


def test_hf_401_without_token_asks_for_token():
    classified = _classify(_http_401(), token=None)
    assert isinstance(classified, ActionableDownloadError)
    assert classified.resolution.code == "hf_token_missing"


def test_hf_401_with_valid_token_asks_for_license_not_token():
    """THE FLUX case: token configured and valid → the ask is the license
    gate. Telling this user to re-enter their token is the forbidden outcome."""
    classified = _classify(_http_401(), token="hf_valid")
    assert isinstance(classified, ActionableDownloadError)
    assert classified.resolution.code == "hf_gate_not_accepted"
    assert "token" not in classified.resolution.action_label.lower()
    assert classified.resolution.action_url == (
        "https://huggingface.co/black-forest-labs/FLUX.1-schnell"
    )


def test_hf_403_with_pending_review_reports_pending_state():
    exc = _http_401(403)
    exc.args = ("Access to model X is awaiting a review",)
    classified = _classify(exc, token="hf_valid")
    assert isinstance(classified, ActionableDownloadError)
    assert classified.resolution.code == "hf_gate_pending"


def test_hf_401_with_dead_token_asks_to_replace_it():
    classified = _classify(_http_401(), token="hf_revoked", verdict="invalid")
    assert isinstance(classified, ActionableDownloadError)
    assert classified.resolution.code == "hf_token_invalid"


@pytest.mark.parametrize("verdict", ["unknown", "unavailable", ""])
def test_hf_inconclusive_token_validation_never_guesses_license(verdict):
    original = _http_401(403)
    classified = _classify(original, token="hf_unverified", verdict=verdict)
    assert classified is original


def test_non_auth_error_passes_through_unclassified():
    exc = Exception("boom")
    exc.response = SimpleNamespace(status_code=500)  # type: ignore[attr-defined]
    out = asyncio.run(_classify_hf_auth_failure(exc, "org/model", "tok"))
    assert out is exc


# ── 3. Stale-row re-triage (pre-taxonomy failures → prompt states) ─────────

FLUX_OLD_ERROR = (
    "Hugging Face download failed for black-forest-labs/FLUX.1-schnell: "
    "401 Client Error. Access to model black-forest-labs/FLUX.1-schnell is "
    "restricted. You must have access to it and be authenticated to access it. "
    "Please log in."
)


def test_stale_flux_row_with_token_becomes_license_prompt():
    res = retriage_stale_failure(
        FLUX_OLD_ERROR,
        {"hf_repo_id": "black-forest-labs/FLUX.1-schnell", "dest_dir": "/x"},
        hf_token_present=True,
        civitai_key_present=False,
    )
    assert res is not None
    assert res.code == "hf_gate_not_accepted"
    assert "black-forest-labs/FLUX.1-schnell" in res.message


def test_stale_flux_row_without_token_asks_for_token():
    res = retriage_stale_failure(
        FLUX_OLD_ERROR,
        {"hf_repo_id": "black-forest-labs/FLUX.1-schnell"},
        hf_token_present=False,
        civitai_key_present=False,
    )
    assert res is not None
    assert res.code == "hf_token_missing"


def test_stale_row_repo_id_recovered_from_message_text():
    res = retriage_stale_failure(
        "401 Client Error for url https://huggingface.co/org/gated-model/resolve/main/f",
        None,
        hf_token_present=True,
        civitai_key_present=False,
    )
    assert res is not None
    assert res.code == "hf_gate_not_accepted"
    assert "org/gated-model" in res.message


@pytest.mark.parametrize(
    "key_present,msg,expected",
    [
        (False, "401 Unauthorized from Civitai", "civitai_key_required"),
        # the pre-taxonomy blanket message, observed live in stale rows
        (
            False,
            "Civitai API key required or invalid — add your key under "
            "Settings → API Keys → Civitai, then retry the download.",
            "civitai_key_required",
        ),
        (True, "401 Unauthorized from Civitai", "civitai_key_rejected"),
        (True, "403 Forbidden", "civitai_access_restricted"),
    ],
)
def test_stale_civitai_rows(key_present, msg, expected):
    res = retriage_stale_failure(
        msg,
        {"civitai_download": True},
        hf_token_present=False,
        civitai_key_present=key_present,
    )
    assert res is not None
    assert res.code == expected


def _civitai_http_status(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://civitai.com/api/v1/models/123")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError("civitai refused", request=req, response=resp)


@pytest.mark.parametrize(
    "status,key,expected",
    [
        (401, None, "requires an API key"),
        (401, "civ_saved", "rejected the saved API key"),
        (403, "civ_saved", "key is connected"),
    ],
)
def test_civitai_inspect_errors_attribute_saved_key_correctly(
    monkeypatch, status, key, expected
):
    """The pre-download Civitai resolver must not collapse every 401/403 into
    "add your key" — with a saved key, the ask is update the key or unlock
    account access."""
    from app.services.image_gen import custom_models

    monkeypatch.setattr(custom_models, "read_civitai_key", lambda: key)

    err = custom_models._friendly_civitai_http_error(
        _civitai_http_status(status), "model 123"
    )

    assert expected in str(err)


@pytest.mark.parametrize(
    "token,verdict,expected_status,expected_text,forbidden_text",
    [
        (None, "unknown", 401, "Hugging Face token", "could not be verified"),
        ("hf_saved", "invalid", 401, "did not accept the token", "license"),
        ("hf_saved", "valid", 401, "gated model", "Add your Hugging Face token"),
        (
            "hf_saved",
            "unknown",
            503,
            "could not be verified",
            "accept their license",
        ),
    ],
)
def test_custom_hf_inspection_uses_shared_auth_attribution(
    monkeypatch, token, verdict, expected_status, expected_text, forbidden_text
):
    from app.services.ai import key_validation
    from app.services.image_gen import custom_models

    request = httpx.Request("GET", "https://huggingface.co/api/models/org/model")
    response = httpx.Response(403, request=request)

    async def denied(*_args, **_kwargs):
        raise httpx.HTTPStatusError(
            "denied", request=request, response=response
        )

    async def validate(*_args, **_kwargs):
        return SimpleNamespace(verdict=verdict, message="validator result")

    monkeypatch.setattr(custom_models, "_http_get_json", denied)
    monkeypatch.setattr(custom_models, "read_hf_token", lambda: token)
    monkeypatch.setattr(key_validation, "validate_key", validate)

    with pytest.raises(custom_models.InspectError) as raised:
        asyncio.run(custom_models.resolve_hf("org/model"))

    assert raised.value.status_code == expected_status
    assert expected_text in str(raised.value)
    assert forbidden_text not in str(raised.value)


def test_stale_installer_message_becomes_packages_prompt():
    res = retriage_stale_failure(
        "Image generation requires the AI packages. Run the in-app installer "
        "(POST /image-gen/install).",
        None,
        hf_token_present=False,
        civitai_key_present=False,
    )
    assert res is not None
    assert res.code == "ai_packages_missing"


def test_genuine_errors_are_not_retriaged():
    for msg in (
        "Download failed after 3 attempts. Last error: connection reset",
        "No space left on device",
        "HTTP 500 from server",
        None,
        "",
    ):
        assert (
            retriage_stale_failure(
                msg, None, hf_token_present=True, civitai_key_present=True
            )
            is None
        ), msg


def test_every_resolution_constructor_yields_actionable_state():
    """Contract pin: each catalog constructor produces a complete resolution
    the UI can render (title, message, action label + a working target)."""
    cases = [
        failures.hf_gate_not_accepted("o/m"),
        failures.hf_gate_pending("o/m"),
        failures.hf_token_missing("o/m"),
        failures.hf_token_invalid("o/m"),
        failures.civitai_key_required(),
        failures.civitai_key_rejected(),
        failures.civitai_access_restricted(None),
        failures.ai_packages_missing(),
    ]
    for err in cases:
        r = err.resolution
        assert r.title and r.message and r.action_label and r.code
        if r.action_kind == "open_url":
            assert r.action_url
        if r.action_kind == "settings_api_keys":
            assert r.provider
