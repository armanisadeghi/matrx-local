"""Single-ML-stack guardrails for every optional package install.

The engine has exactly one certified provider of the heavy ML stack: the
managed media runtime slot (torch/transformers/numpy family, pinned by the
release-owned contract in ``config/runtime-manifests``). A second copy of any
of these distributions inside a capability ``pip --target`` dir is never a
convenience — whichever dir loses sys.path precedence still shadows partner
packages' ABI expectations the moment ordering regresses, and that exact
failure took image generation down in production (MXL-D-070,
``'_ClassNamespace' object is not iterable``).

Enforcement points, all of which must stay wired:
1. ``screen_install_packages`` — refuses an install request that names a
   slot-owned distribution (a coding agent adding ``torch>=x`` to a recipe
   fails immediately, in dev and at runtime).
2. ``find_shadowing_distributions`` / ``sanitize_target_dir`` — detects and
   removes slot-owned distributions that pip pulled transitively into a
   target dir, using each dist's own RECORD manifest.
3. ``write_slot_constraints_file`` — pins pip's resolver to the slot's exact
   versions so capability dependency resolution happens against the stack the
   capability will actually run on.

``tests/unit/test_ml_stack_guardrails.py`` keeps ``SLOT_OWNED_DISTRIBUTIONS``
a superset of the contract's managed requirements — bump the contract and the
test tells you to extend the denylist. Doctrine: FEATURE.md in this package.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.common.system_logger import get_logger

logger = get_logger()

# Distributions only the managed media runtime may provide to the engine
# process. This is the contract's managed_requirements plus the ABI-coupled
# shared distributions (numpy/tokenizers/safetensors) that torch-family
# packages compile or pin against. Pure-python overlaps (requests, tqdm,
# pillow, …) are intentionally NOT listed: with slot precedence enforced they
# are inert, and denying them would break unrelated lightweight capabilities.
SLOT_OWNED_DISTRIBUTIONS: frozenset[str] = frozenset(
    {
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "tokenizers",
        "numpy",
        "huggingface-hub",
        "safetensors",
        "diffusers",
        "accelerate",
        "peft",
        "sentencepiece",
        "gguf",
    }
)


def canonical_distribution_name(name: str) -> str:
    """PEP 503 normalization: case-insensitive, runs of ``-_.`` collapse."""
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def requirement_distribution_name(requirement: str) -> str:
    """Distribution name of a requirement string (extras/specifiers stripped)."""
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if not match:
        raise ValueError(f"Unparseable requirement: {requirement!r}")
    return canonical_distribution_name(match.group(1))


def screen_install_packages(packages: list[str], *, context: str) -> None:
    """Refuse an install request that names a slot-owned distribution.

    Raises RuntimeError naming every offender. This must run before pip for
    every optional-package install path — it is the tripwire that stops a
    recipe change from reintroducing a second ML stack.
    """
    offenders = [
        requirement
        for requirement in packages
        if requirement_distribution_name(requirement) in SLOT_OWNED_DISTRIBUTIONS
    ]
    if offenders:
        raise RuntimeError(
            f"Install request for {context} names slot-owned ML distributions "
            f"{offenders}: these are provided exclusively by the managed media "
            "runtime. Remove them from the recipe and declare "
            "requires_ml_runtime instead (see "
            "app/services/optional_packages/FEATURE.md)."
        )


def find_shadowing_distributions(target: Path) -> dict[str, str]:
    """Map slot-owned distributions present in a ``pip --target`` dir → version."""
    found: dict[str, str] = {}
    if not target.is_dir():
        return found
    for dist_info in target.glob("*.dist-info"):
        stem = dist_info.name[: -len(".dist-info")]
        name, _, version = stem.rpartition("-")
        if canonical_distribution_name(name) in SLOT_OWNED_DISTRIBUTIONS:
            found[canonical_distribution_name(name)] = version
    return found


def _record_top_level_paths(target: Path, dist_info: Path) -> set[Path]:
    """Top-level files/dirs a distribution owns inside the target dir."""
    roots: set[Path] = set()
    record = dist_info / "RECORD"
    if record.is_file():
        for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
            rel = line.split(",", 1)[0].strip()
            if not rel or rel.startswith(("..", "/")):
                continue
            roots.add(target / Path(rel).parts[0])
    top_level = dist_info / "top_level.txt"
    if top_level.is_file():
        for line in top_level.read_text(encoding="utf-8", errors="replace").splitlines():
            name = line.strip()
            if name:
                roots.add(target / name)
                roots.add(target / f"{name}.libs")
    roots.discard(dist_info)
    return roots


def sanitize_target_dir(target: Path, *, log_prefix: str) -> list[str]:
    """Remove slot-owned distributions pip pulled into a target dir.

    Returns the canonical names removed. Loud by design: every removal is an
    INFO line and the summary is a WARNING — a silent second ML stack is how
    MXL-D-070 shipped.
    """
    removed: list[str] = []
    for dist_info in sorted(target.glob("*.dist-info")):
        stem = dist_info.name[: -len(".dist-info")]
        name, _, version = stem.rpartition("-")
        canonical = canonical_distribution_name(name)
        if canonical not in SLOT_OWNED_DISTRIBUTIONS:
            continue
        for root in sorted(_record_top_level_paths(target, dist_info)):
            if not root.exists() and not root.is_symlink():
                continue
            try:
                if root.is_dir() and not root.is_symlink():
                    shutil.rmtree(root)
                else:
                    root.unlink()
                logger.info("[%s] Removed shadowing path %s (%s)", log_prefix, root, canonical)
            except OSError as exc:
                raise RuntimeError(
                    f"Could not remove shadowing ML distribution path {root}: {exc}"
                ) from exc
        shutil.rmtree(dist_info)
        removed.append(canonical)
        logger.info(
            "[%s] Removed slot-owned distribution %s==%s from %s",
            log_prefix,
            canonical,
            version,
            target,
        )
    if removed:
        logger.warning(
            "[%s] Sanitized %s: removed slot-owned ML distributions %s — these "
            "are provided by the managed media runtime and a second copy is a "
            "production hazard (MXL-D-070)",
            log_prefix,
            target,
            ", ".join(sorted(removed)),
        )
    return removed


def write_slot_constraints_file(dest: Path) -> Path:
    """Write a pip constraints file with the runtime contract's exact pins.

    Capability installs resolve against these constraints so pip chooses
    dependency versions compatible with the stack the capability will actually
    import at runtime (e.g. a whisper/numba release that works with the slot's
    numpy). Raises when no contract is available — a capability that requires
    the ML runtime must never resolve against a floating stack.
    """
    from app.services.image_gen.installer import (  # noqa: PLC0415 — avoid import cycle
        load_runtime_install_contract,
    )

    contract = load_runtime_install_contract()
    lines = [
        f"{canonical_distribution_name(name)}=={version}"
        for name, version in sorted(contract.packages.items())
    ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest
