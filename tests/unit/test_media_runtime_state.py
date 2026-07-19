from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.image_gen import installer, runtime_state


_FAKE_INSTALL_PROCESS = textwrap.dedent(
    r"""
    import base64
    import hashlib
    import os
    import sys
    import time
    from pathlib import Path

    from app.services.image_gen import installer

    sys.frozen = True
    home = Path(os.environ["MATRX_HOME_DIR"])
    requirements = home / "locked.txt"
    requirements.parent.mkdir(parents=True, exist_ok=True)
    requirements.write_text("demo==1.0 --hash=sha256:" + "a" * 64, encoding="utf-8")
    contract = installer.RuntimeInstallContract(
        contract_sha256="b" * 64,
        target=installer.runtime_target_id(),
        requirements_file=requirements,
        packages={"demo": "1.0"},
        record_hashes={},
    )
    installer.load_runtime_install_contract = lambda: contract

    def fake_install(packages, target, progress, **kwargs):
        del packages, progress, kwargs
        counter = home / "install-count.txt"
        with counter.open("a", encoding="utf-8") as handle:
            handle.write(os.environ.get("ATTEMPT", "unknown") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        package = target / "demo"
        package.mkdir()
        payload = b"ok"
        module = package / "__init__.py"
        module.write_bytes(payload)
        dist = target / "demo-1.0.dist-info"
        dist.mkdir()
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
        (dist / "RECORD").write_text(
            f"demo/__init__.py,sha256={digest},{len(payload)}\n"
            "demo-1.0.dist-info/RECORD,,\n",
            encoding="utf-8",
        )
        time.sleep(float(os.environ.get("MATRX_TEST_INSTALL_DELAY", "0")))

    installer._run_pip_streaming = fake_install
    installer._verify_runtime_subprocess = lambda path, cancel_event=None: contract.packages
    installer.verify_runtime_path = lambda path: installer.RuntimeVerification(
        True, contract.packages
    )
    from app.services.image_gen import service as image_service
    from app.services.video_gen import service as video_service
    image_service._check_deps = lambda: (True, "")
    video_service._check_deps = lambda: (True, "")

    progress = installer.InstallProgress()
    installer._install_runtime_sync(
        progress, None, "install", os.environ.get("ATTEMPT", "attempt")
    )
    print(progress.status, flush=True)
    """
)


def test_linux_glibc_floor_fails_before_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = (
        Path(__file__).resolve().parents[2]
        / "config"
        / "runtime-manifests"
        / "image-gen-x86_64-unknown-linux-gnu.json"
    )
    monkeypatch.setattr(installer, "_contract_manifest_candidates", lambda: [manifest])
    monkeypatch.setattr(
        installer, "runtime_target_id", lambda: "x86_64-unknown-linux-gnu"
    )
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer.platform, "libc_ver", lambda: ("glibc", "2.27"))

    with pytest.raises(installer.UnsupportedRuntimeError, match="requires glibc 2.28"):
        installer.load_runtime_install_contract()


_CRASHED_PHASE_PROCESS = textwrap.dedent(
    r"""
    import os
    from app.services.image_gen.runtime_state import (
        RuntimeFileLock,
        RuntimePhase,
        RuntimeSnapshot,
        create_staging_slot,
        write_snapshot,
    )

    with RuntimeFileLock():
        create_staging_slot()
        write_snapshot(
            RuntimeSnapshot(
                state=RuntimePhase(os.environ["CRASH_PHASE"]),
                operation="install",
                attempt_id="dead-process",
                stage=os.environ["CRASH_PHASE"],
                message="owner died",
            )
        )
        os._exit(17)
    """
)


def _write_demo_slot(root: Path, name: str = "verified-slot") -> tuple[Path, dict[str, str]]:
    slot = root / name
    package = slot / "demo"
    package.mkdir(parents=True)
    payload = b"abc"
    module = package / "__init__.py"
    module.write_bytes(payload)
    dist = slot / "demo-1.0.dist-info"
    dist.mkdir()
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
    record = dist / "RECORD"
    record.write_text(
        f"demo/__init__.py,sha256={digest},3\n"
        "demo-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    (slot / runtime_state.INSTALL_EVIDENCE).write_text("contract", encoding="utf-8")
    packages = {"demo": "1.0"}
    runtime_state.write_slot_manifest(
        slot,
        runtime_revision="contract",
        packages=packages,
        target=installer.runtime_target_id(),
        record_hashes=installer._record_anchor_hashes(slot),
    )
    return slot, packages


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


def test_model_specific_importerror_does_not_poison_verified_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(installer, "RuntimeFileLock", nullcontext)
    monkeypatch.setattr(
        installer,
        "validate_active_runtime",
        lambda *, force_inventory=False: installer.RuntimeVerification(
            True, {"diffusers": "0.39.0"}
        ),
    )
    writes: list[object] = []
    monkeypatch.setattr(installer, "write_snapshot", writes.append)

    recorded = installer.record_runtime_integrity_failure(
        ImportError("custom model pipeline requires an optional repository module")
    )

    assert recorded is False
    assert writes == []


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


def test_record_digest_detects_same_size_runtime_corruption(tmp_path) -> None:
    package = tmp_path / "demo"
    package.mkdir()
    module = package / "__init__.py"
    original = b"abc"
    module.write_bytes(original)
    dist = tmp_path / "demo-1.0.dist-info"
    dist.mkdir()
    digest = base64.urlsafe_b64encode(hashlib.sha256(original).digest()).rstrip(b"=").decode()
    (dist / "RECORD").write_text(
        f"demo/__init__.py,sha256={digest},3\n"
        "demo-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    assert installer._validate_installed_inventory(tmp_path, {"demo": "1.0"}) == (
        True,
        "",
    )
    module.write_bytes(b"xyz")

    valid, reason = installer._validate_installed_inventory(
        tmp_path, {"demo": "1.0"}
    )
    assert valid is False
    assert "digest mismatch" in reason


def test_status_revalidates_ready_inventory_once_and_exposes_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = runtime_state.RuntimeSnapshot(
        state=runtime_state.RuntimePhase.READY,
        runtime_revision="contract",
        active_slot="active",
        packages={"demo": "1.0"},
        updated_at=123.0,
    )
    contract = installer.RuntimeInstallContract(
        contract_sha256="contract",
        target="test-target",
        requirements_file=Path("unused"),
        packages={"demo": "1.0"},
        record_hashes={},
    )
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(installer, "authoritative_snapshot", lambda: snapshot)
    monkeypatch.setattr(installer, "load_runtime_install_contract", lambda: contract)
    monkeypatch.setattr(installer, "RuntimeFileLock", lambda **kwargs: nullcontext())
    monkeypatch.setattr(installer, "read_snapshot", lambda: snapshot)
    monkeypatch.setattr(installer, "validate_slot", lambda *args, **kwargs: (True, "", {}))
    monkeypatch.setattr(installer, "slot_path", lambda _slot: Path("/runtime"))
    persisted: list[runtime_state.RuntimeSnapshot] = []
    monkeypatch.setattr(installer, "write_snapshot", persisted.append)
    monkeypatch.setattr(
        installer,
        "_validate_installed_inventory",
        lambda *args, **kwargs: (False, "installed runtime file digest mismatch: demo.py"),
    )
    installer._inventory_validation_cache.clear()

    status = installer.get_runtime_status()

    assert status["state"] == "failed"
    assert status["failure_code"] == "runtime_inventory_invalid"
    assert status["repairable"] is True
    assert status["image_available"] is False
    assert status["updated_at"] > 0
    assert persisted[0].failure_code == "runtime_inventory_invalid"


def test_two_process_install_contention_installs_exact_contract_once(tmp_path) -> None:
    env = os.environ.copy()
    env["MATRX_HOME_DIR"] = str(tmp_path)
    env["MATRX_TEST_INSTALL_DELAY"] = "0.4"
    first_env = {**env, "ATTEMPT": "first"}
    second_env = {**env, "ATTEMPT": "second"}
    first = subprocess.Popen(
        [sys.executable, "-c", _FAKE_INSTALL_PROCESS],
        cwd=Path(__file__).resolve().parents[2],
        env=first_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    second = subprocess.Popen(
        [sys.executable, "-c", _FAKE_INSTALL_PROCESS],
        cwd=Path(__file__).resolve().parents[2],
        env=second_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    first_stdout, first_stderr = first.communicate(timeout=20)
    second_stdout, second_stderr = second.communicate(timeout=20)

    assert first.returncode == 0, first_stderr
    assert second.returncode == 0, second_stderr
    assert "complete" in first_stdout
    assert "complete" in second_stdout
    attempts = (tmp_path / "install-count.txt").read_text(encoding="utf-8").splitlines()
    assert len(attempts) == 1
    state = json.loads(
        (tmp_path / "image-gen-runtime" / "state.json").read_text(encoding="utf-8")
    )
    assert state["state"] == "ready"


@pytest.mark.parametrize(
    "phase",
    ["installing", "updating", "repairing", "validating", "activating"],
)
def test_process_death_in_every_transient_phase_recovers(
    tmp_path, phase: str
) -> None:
    home = tmp_path / phase
    env = {**os.environ, "MATRX_HOME_DIR": str(home), "CRASH_PHASE": phase}
    crashed = subprocess.run(
        [sys.executable, "-c", _CRASHED_PHASE_PROCESS],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert crashed.returncode == 17

    recovered = subprocess.run(
        [sys.executable, "-c", _FAKE_INSTALL_PROCESS],
        cwd=Path(__file__).resolve().parents[2],
        env={**env, "ATTEMPT": f"recovery-{phase}"},
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert "complete" in recovered.stdout
    state = json.loads(
        (home / "image-gen-runtime" / "state.json").read_text(encoding="utf-8")
    )
    assert state["state"] == "ready"
    slots = home / "image-gen-runtime" / "slots"
    assert not list(slots.glob(".staging-*"))


@pytest.mark.parametrize(
    "phase",
    [
        runtime_state.RuntimePhase.INSTALLING,
        runtime_state.RuntimePhase.UPDATING,
        runtime_state.RuntimePhase.REPAIRING,
        runtime_state.RuntimePhase.VALIDATING,
        runtime_state.RuntimePhase.ACTIVATING,
    ],
)
def test_every_transient_phase_is_owned_by_startup_recovery(
    monkeypatch: pytest.MonkeyPatch, phase: runtime_state.RuntimePhase
) -> None:
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        installer,
        "authoritative_snapshot",
        lambda: runtime_state.RuntimeSnapshot(state=phase),
    )

    assert installer._compatibility_migration_pending() is True


def test_slot_retention_prunes_staging_orphans_and_old_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(runtime_state, "packages_dir", lambda name: tmp_path / name)
    slots = runtime_state.runtime_slots_dir()
    for name in ("active", "last-good", "candidate", "old", ".staging-dead"):
        (slots / name).mkdir(parents=True)
    snapshot = runtime_state.RuntimeSnapshot(
        state=runtime_state.RuntimePhase.RESTART_REQUIRED,
        active_slot="active",
        last_known_good_slot="last-good",
        candidate_slot="candidate",
    )

    removed = runtime_state.cleanup_unreferenced_slots(snapshot)

    assert set(removed) == {"old", ".staging-dead"}
    assert {path.name for path in slots.iterdir()} == {
        "active",
        "last-good",
        "candidate",
    }


def test_in_slot_symlink_resolving_outside_is_rejected(tmp_path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"abc")
    slot = tmp_path / "slot"
    package = slot / "demo"
    package.mkdir(parents=True)
    (package / "__init__.py").symlink_to(outside)
    dist = slot / "demo-1.0.dist-info"
    dist.mkdir()
    digest = base64.urlsafe_b64encode(hashlib.sha256(b"abc").digest()).rstrip(b"=").decode()
    (dist / "RECORD").write_text(
        f"demo/__init__.py,sha256={digest},3\n"
        "demo-1.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    valid, reason = installer._validate_installed_inventory(slot, {"demo": "1.0"})

    assert valid is False
    assert "symlink escapes" in reason


def test_record_anchor_detects_record_that_omits_runtime_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(runtime_state, "packages_dir", lambda name: tmp_path / name)
    slot, packages = _write_demo_slot(runtime_state.runtime_slots_dir())
    valid, _, _ = runtime_state.validate_slot(
        slot.name,
        expected_revision="contract",
        expected_packages=packages,
        expected_target=installer.runtime_target_id(),
    )
    assert valid is True

    (slot / "demo-1.0.dist-info" / "RECORD").write_text(
        "demo-1.0.dist-info/RECORD,,\n", encoding="utf-8"
    )

    valid, reason, _ = runtime_state.validate_slot(slot.name)
    assert valid is False
    assert "record hash mismatch" in reason


def test_inventory_cache_never_rehashes_multi_gb_runtime_on_status_poll(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(runtime_state, "packages_dir", lambda name: tmp_path / name)
    slot, packages = _write_demo_slot(runtime_state.runtime_slots_dir())
    snapshot = runtime_state.RuntimeSnapshot(
        state=runtime_state.RuntimePhase.READY,
        runtime_revision="contract",
        active_slot=slot.name,
        packages=packages,
        updated_at=50.0,
    )
    contract = installer.RuntimeInstallContract(
        contract_sha256="contract",
        target=installer.runtime_target_id(),
        requirements_file=tmp_path / "unused",
        packages=packages,
        record_hashes={},
    )
    installer._inventory_validation_cache.clear()

    assert installer._snapshot_matches_contract(snapshot, contract)[0] is True
    (slot / "demo" / "__init__.py").write_bytes(b"xyz")
    monkeypatch.setattr(
        installer,
        "_validate_installed_inventory",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("status polling must not rehash a cached runtime")
        ),
    )
    for _ in range(20):
        assert installer._snapshot_matches_contract(snapshot, contract)[0] is True


def test_forced_inventory_validation_bypasses_process_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(runtime_state, "packages_dir", lambda name: tmp_path / name)
    slot, packages = _write_demo_slot(runtime_state.runtime_slots_dir())
    snapshot = runtime_state.RuntimeSnapshot(
        state=runtime_state.RuntimePhase.READY,
        runtime_revision="contract",
        active_slot=slot.name,
        packages=packages,
        updated_at=50.0,
    )
    contract = installer.RuntimeInstallContract(
        contract_sha256="contract",
        target=installer.runtime_target_id(),
        requirements_file=tmp_path / "unused",
        packages=packages,
        record_hashes={},
    )
    installer._inventory_validation_cache.clear()
    assert installer._snapshot_matches_contract(snapshot, contract)[0] is True
    (slot / "demo" / "__init__.py").write_bytes(b"xyz")

    valid, reason = installer._snapshot_matches_contract(
        snapshot, contract, force_inventory=True
    )
    assert valid is False
    assert "digest mismatch" in reason


def test_state_loss_adopts_exact_verified_slot_before_gc(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(runtime_state, "packages_dir", lambda name: tmp_path / name)
    slot, packages = _write_demo_slot(runtime_state.runtime_slots_dir())
    contract = installer.RuntimeInstallContract(
        contract_sha256="contract",
        target=installer.runtime_target_id(),
        requirements_file=tmp_path / "unused",
        packages=packages,
        record_hashes={},
    )
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(installer, "load_runtime_install_contract", lambda: contract)
    installs: list[object] = []
    monkeypatch.setattr(
        installer,
        "_run_pip_streaming",
        lambda *args, **kwargs: installs.append((args, kwargs)),
    )
    installer._inventory_validation_cache.clear()

    progress = installer.InstallProgress()
    installer._install_runtime_sync(progress, None, "install", "recover-state")

    recovered = runtime_state.read_snapshot()
    assert progress.status == "complete"
    assert installs == []
    assert recovered.state is runtime_state.RuntimePhase.READY
    assert recovered.active_slot == slot.name
    assert slot.is_dir()


def test_lock_timeout_persists_repairable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimedOutLock:
        def __init__(self, timeout=0):
            del timeout

        def __enter__(self):
            raise TimeoutError("runtime lock owner stopped responding")

        def __exit__(self, *args):
            return None

    current = runtime_state.RuntimeSnapshot(
        state=runtime_state.RuntimePhase.INSTALLING,
        operation="install",
        attempt_id="other-attempt",
    )
    writes: list[runtime_state.RuntimeSnapshot] = []
    monkeypatch.setattr(installer, "RuntimeFileLock", TimedOutLock)
    monkeypatch.setattr(installer, "authoritative_snapshot", lambda: current)
    monkeypatch.setattr(
        installer,
        "load_runtime_install_contract",
        lambda: (_ for _ in ()).throw(RuntimeError("not needed")),
    )
    monkeypatch.setattr(installer, "write_snapshot", writes.append)

    progress = installer.InstallProgress()
    installer._install_runtime_sync(progress, None, "install", "waiting-attempt")

    assert progress.status == "error"
    assert len(writes) == 1
    assert writes[0].state is runtime_state.RuntimePhase.FAILED
    assert writes[0].failure_code == "lock_timeout"


def test_linux_glibc_preflight_rejects_older_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    requirements = tmp_path / "locked.txt"
    requirements.write_text(
        "demo==1.0 --hash=sha256:" + "a" * 64, encoding="utf-8"
    )
    canonical = {
        "contract_sha256": "c" * 64,
        "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    (tmp_path / "image-gen-contract.json").write_text(
        json.dumps(canonical), encoding="utf-8"
    )
    target = {
        "schema_version": 1,
        "target": "x86_64-unknown-linux-gnu",
        "supported": True,
        "python_minor": canonical["python_minor"],
        "contract_sha256": canonical["contract_sha256"],
        "minimum_glibc": "2.28",
        "torch_variant": "cu126",
        "lock_file": requirements.name,
        "lock_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
        "packages": [{"name": "demo", "version": "1.0", "wheels": ["demo.whl"]}],
    }
    manifest = tmp_path / "image-gen-x86_64-unknown-linux-gnu.json"
    manifest.write_text(json.dumps(target), encoding="utf-8")
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer, "runtime_target_id", lambda: target["target"])
    monkeypatch.setattr(installer, "_contract_manifest_candidates", lambda: [manifest])
    monkeypatch.setattr(installer.platform, "libc_ver", lambda: ("glibc", "2.27"))

    with pytest.raises(installer.UnsupportedRuntimeError, match="glibc 2.28"):
        installer.load_runtime_install_contract()
