from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.image_gen import installer, runtime_state


def test_completion_marker_is_not_runtime_authority(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        runtime_state, "packages_dir", lambda name: tmp_path / name
    )
    monkeypatch.setattr(
        installer, "packages_dir", lambda name: tmp_path / name
    )
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    legacy = tmp_path / "image-gen-packages"
    legacy.mkdir()
    (legacy / runtime_state.INSTALL_EVIDENCE).write_text("legacy", encoding="utf-8")

    assert runtime_state.authoritative_snapshot().state is runtime_state.RuntimePhase.ABSENT
    assert installer.is_image_gen_installed() is False


def test_exact_slot_manifest_is_authoritative(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        runtime_state, "packages_dir", lambda name: tmp_path / name
    )
    slots = runtime_state.runtime_slots_dir()
    slot = slots / "verified-slot"
    slot.mkdir(parents=True)
    (slot / runtime_state.INSTALL_EVIDENCE).write_text("evidence", encoding="utf-8")
    runtime_state.write_slot_manifest(
        slot,
        runtime_revision="contract-sha",
        packages={"diffusers": "0.39.0"},
        target="test-target",
    )
    runtime_state.write_snapshot(
        runtime_state.RuntimeSnapshot(
            state=runtime_state.RuntimePhase.READY,
            runtime_revision="contract-sha",
            active_slot=slot.name,
            packages={"diffusers": "0.39.0"},
        )
    )

    snapshot = runtime_state.authoritative_snapshot()
    assert snapshot.ready is True
    assert runtime_state.active_slot_path() == slot


def test_model_file_oserror_does_not_poison_runtime() -> None:
    assert (
        installer.is_runtime_integrity_failure(
            OSError("Error no file named diffusion_pytorch_model.safetensors")
        )
        is False
    )


@pytest.mark.parametrize(
    "exc",
    [
        ModuleNotFoundError("No module named 'tqdm.contrib.logging'"),
        OSError("dlopen(/tmp/torch/_C.so): mach-o, but wrong architecture"),
        RuntimeError("DLL load failed while importing _C: undefined symbol"),
    ],
)
def test_import_and_native_loader_failures_poison_runtime(exc: BaseException) -> None:
    assert installer.is_runtime_integrity_failure(exc) is True


def test_nested_missing_module_is_runtime_integrity_failure() -> None:
    try:
        try:
            raise ModuleNotFoundError("No module named 'tqdm.contrib.logging'")
        except ModuleNotFoundError as cause:
            raise RuntimeError("Failed to load pipeline") from cause
    except RuntimeError as exc:
        assert installer.is_runtime_integrity_failure(exc) is True


def test_critical_verifier_imports_lazy_modules_and_pipeline_classes() -> None:
    modules: dict[str, object] = {
        name: SimpleNamespace(__version__="1.0")
        for name in installer.CRITICAL_RUNTIME_IMPORTS
    }
    diffusers = modules["diffusers"]
    for name in installer.CRITICAL_PIPELINE_CLASSES:
        setattr(diffusers, name, object())
    diffusers.__version__ = "0.39.0"
    transformers = modules["transformers"]
    transformers.__version__ = "5.3.0"
    transformers.AutoImageProcessor = object()
    transformers.AutoTokenizer = object()

    versions = installer.critical_runtime_import_check(
        importer=lambda name: modules[name]
    )

    assert versions["diffusers"] == "0.39.0"
    assert versions["transformers"] == "5.3.0"


def test_contract_unavailable_is_failed_and_not_repairable(monkeypatch) -> None:
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        installer,
        "authoritative_snapshot",
        lambda: runtime_state.RuntimeSnapshot(),
    )
    monkeypatch.setattr(
        installer,
        "load_runtime_install_contract",
        lambda: (_ for _ in ()).throw(RuntimeError("missing embedded contract")),
    )
    monkeypatch.setattr(installer, "get_active_progress", lambda: None)

    status = installer.get_runtime_status()

    assert status["state"] == "failed"
    assert status["failure_code"] == "contract_unavailable"
    assert status["repairable"] is False
    assert isinstance(status["required_revision"], str)


def test_frozen_candidate_verifier_uses_sidecar_env_and_sentinel(
    monkeypatch, tmp_path
) -> None:
    contract = installer.RuntimeInstallContract(
        contract_sha256="a" * 64,
        target="aarch64-apple-darwin",
        requirements_file=tmp_path / "locked.txt",
        packages={"diffusers": "0.39.0"},
        record_hashes={},
    )
    observed: dict[str, object] = {}

    def run(command, *, cancel_event, timeout, env=None):
        observed.update(command=command, env=env, timeout=timeout)
        payload = {
            "ok": True,
            "contract": contract.runtime_revision,
        }
        import json

        return SimpleNamespace(
            returncode=0,
            stdout="MATRX_FROZEN_RUNTIME_VERIFY=" + json.dumps(payload) + "\n",
            stderr="",
        )

    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(installer, "load_runtime_install_contract", lambda: contract)
    monkeypatch.setattr(installer, "_run_subprocess_cancellable", run)

    result = installer._verify_runtime_subprocess(tmp_path, cancel_event=None)

    assert result == contract.packages
    env = observed["env"]
    assert env["MATRX_FROZEN_RUNTIME_VERIFY"] == "1"
    assert env["MATRX_FROZEN_RUNTIME_PATH"] == str(tmp_path)
    assert env["MATRX_FROZEN_RUNTIME_TARGET"] == contract.target
    assert observed["command"] == [installer.sys.executable]
