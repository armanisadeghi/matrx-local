"""Single-ML-stack guardrails (MXL-D-070).

The managed media runtime slot is the only provider of the torch/transformers/
numpy family inside the engine process. These tests are the tripwire that
stops any change — human or agent — from reintroducing a second ML stack via
a capability recipe, a lightweight capability spec, or a contract/denylist
drift. If one of these fails, read app/services/optional_packages/FEATURE.md
before "fixing" the test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.optional_packages.guardrails import (
    SLOT_OWNED_DISTRIBUTIONS,
    canonical_distribution_name,
    find_shadowing_distributions,
    requirement_distribution_name,
    sanitize_target_dir,
    screen_install_packages,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "config" / "runtime-manifests" / "image-gen-contract.json"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


class TestDenylistTracksContract:
    def test_denylist_covers_every_managed_requirement(self) -> None:
        """Bumping the runtime contract must keep the denylist a superset.

        If this fails, a new package was added to the managed runtime without
        extending SLOT_OWNED_DISTRIBUTIONS in guardrails.py.
        """
        managed = {
            requirement_distribution_name(req)
            for req in _contract()["managed_requirements"]
        }
        missing = managed - SLOT_OWNED_DISTRIBUTIONS
        assert not missing, (
            f"Runtime contract ships {sorted(missing)} but the guardrail "
            "denylist does not cover them — extend SLOT_OWNED_DISTRIBUTIONS."
        )

    def test_denylist_covers_abi_coupled_shared_distributions(self) -> None:
        assert {"numpy", "tokenizers", "safetensors"} <= SLOT_OWNED_DISTRIBUTIONS


class TestNoRecipeShipsASecondMlStack:
    def test_heavy_capability_recipes_are_clean(self) -> None:
        from app.services.capabilities.installer import CAPABILITY_INSTALL

        for cap_id, recipe in CAPABILITY_INSTALL.items():
            screen_install_packages(
                list(recipe["packages"]), context=f"recipe {cap_id}"
            )

    def test_lightweight_capability_specs_are_clean(self) -> None:
        from app.api.capabilities_routes import CAPABILITY_SPECS

        for cap_id, spec in CAPABILITY_SPECS.items():
            screen_install_packages(
                list(spec["packages"]), context=f"spec {cap_id}"
            )

    def test_torch_dependent_recipes_declare_ml_runtime(self) -> None:
        from app.services.capabilities.installer import CAPABILITY_INSTALL

        for cap_id in ("transcription", "ner"):
            assert CAPABILITY_INSTALL[cap_id].get("requires_ml_runtime") is True, (
                f"{cap_id} imports torch and must declare requires_ml_runtime"
            )

    def test_gliner_floor_survives_transformers_5(self) -> None:
        """gliner < 0.2.27 silently mis-scores on transformers 5.x."""
        from app.services.capabilities.installer import CAPABILITY_INSTALL

        gliner_req = next(
            req
            for req in CAPABILITY_INSTALL["ner"]["packages"]
            if requirement_distribution_name(req) == "gliner"
        )
        assert ">=0.2.27" in gliner_req.replace(" ", "")

    def test_screen_rejects_slot_owned_requirements(self) -> None:
        for requirement in ("torch>=2.6", "numpy", "Transformers[torch]==5.0"):
            with pytest.raises(RuntimeError, match="slot-owned"):
                screen_install_packages([requirement], context="test")


class TestContractPinsSatisfyCapabilityConsumers:
    """The pin-bump checklist, encoded. Verified against consumer metadata
    2026-07-19 (see app/services/optional_packages/FEATURE.md); update the
    bounds here only after re-validating the consumers."""

    def test_transformers_pin_within_gliner_supported_range(self) -> None:
        version = _contract()["managed_direct_versions"]["transformers"]
        major, minor, *_ = (int(part) for part in version.split("."))
        assert (major, minor) >= (4, 52) and (major, minor) < (5, 7), (
            f"transformers {version} is outside gliner 0.2.27's supported "
            "range (>=4.51.3,<5.7) — re-validate NER before bumping."
        )

    def test_numpy_pin_within_numba_ceiling(self) -> None:
        shared = _contract()["shared_versions_by_target"]
        for target, versions in shared.items():
            major, minor, *_ = (int(part) for part in versions["numpy"].split("."))
            assert (major, minor) < (2, 5), (
                f"numpy {versions['numpy']} ({target}) breaches numba's <2.5 "
                "ceiling — whisper transcription will stop resolving."
            )

    def test_huggingface_hub_pin_within_transformers_range(self) -> None:
        version = _contract()["managed_direct_versions"]["huggingface-hub"]
        major, *_ = (int(part) for part in version.split("."))
        assert major == 1, (
            f"huggingface-hub {version}: transformers 5.x requires >=1.3,<2."
        )


class TestSanitizer:
    @staticmethod
    def _fake_dist(target: Path, name: str, version: str, tops: list[str]) -> None:
        dist_info = target / f"{name}-{version}.dist-info"
        dist_info.mkdir(parents=True)
        record_lines = []
        for top in tops:
            pkg = target / top
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            record_lines.append(f"{top}/__init__.py,,")
        record_lines.append(f"{dist_info.name}/RECORD,,")
        (dist_info / "RECORD").write_text("\n".join(record_lines), encoding="utf-8")
        (dist_info / "top_level.txt").write_text(
            "\n".join(tops) + "\n", encoding="utf-8"
        )

    def test_finds_and_removes_only_slot_owned(self, tmp_path: Path) -> None:
        self._fake_dist(tmp_path, "torch", "2.13.0", ["torch"])
        self._fake_dist(tmp_path, "transformers", "5.6.2", ["transformers"])
        self._fake_dist(tmp_path, "tiktoken", "0.13.0", ["tiktoken"])

        assert set(find_shadowing_distributions(tmp_path)) == {
            "torch",
            "transformers",
        }

        removed = sanitize_target_dir(tmp_path, log_prefix="test")

        assert sorted(removed) == ["torch", "transformers"]
        assert find_shadowing_distributions(tmp_path) == {}
        assert not (tmp_path / "torch").exists()
        assert not (tmp_path / "transformers").exists()
        assert (tmp_path / "tiktoken" / "__init__.py").exists()
        assert (tmp_path / "tiktoken-0.13.0.dist-info").exists()

    def test_canonical_names(self) -> None:
        assert canonical_distribution_name("Huggingface_Hub") == "huggingface-hub"
        assert requirement_distribution_name("gliner2[local]>=1.3.2") == "gliner2"
        assert requirement_distribution_name(" torch >=2.6") == "torch"
