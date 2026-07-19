"""Release-script contracts that must hold before native CI can run."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_windows_binary_name_is_not_reset_after_adding_exe_suffix() -> None:
    script = (ROOT / "scripts" / "build-sidecar.sh").read_text(encoding="utf-8")

    suffix_normalization = '*windows*) BINARY_NAME="$BINARY_NAME.exe"'
    verifier_path = 'FROZEN_VERIFY_BINARY="dist/$BINARY_NAME"'

    assert suffix_normalization in script
    assert verifier_path in script
    assert script.index(suffix_normalization) < script.index(verifier_path)
    assert 'export BINARY_NAME="matrx-engine-$TARGET"' not in script


def test_bundled_config_failure_aborts_sidecar_build() -> None:
    script = (ROOT / "scripts" / "build-sidecar.sh").read_text(encoding="utf-8")
    config_block_start = script.index("=== Writing bundled config ===")
    config_block_end = script.index("# ── macOS code signing", config_block_start)
    config_block = script[config_block_start:config_block_end]

    assert "refusing to build an incomplete sidecar" in config_block
    assert "exit 1" in config_block
    assert "WARNING: write_bundled_config.py failed" not in config_block
