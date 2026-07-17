"""Regression coverage for the PyInstaller bootstrap config generator."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_generator_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "write_bundled_config.py"
    spec = importlib.util.spec_from_file_location("write_bundled_config_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundled_config_contains_only_supabase_bootstrap_values() -> None:
    generator = _load_generator_module()

    rendered = generator.render_bundled_config(
        supabase_url="https://db.example.com",
        supabase_key="sb_publishable_example",
    )

    assert "SUPABASE_URL: str = 'https://db.example.com'" in rendered
    assert "SUPABASE_PUBLISHABLE_KEY: str = 'sb_publishable_example'" in rendered
    assert "AIDREAM_SERVER_URL" not in rendered
    compile(rendered, "bundled_config.py", "exec")


def test_generator_does_not_require_aidream_url(tmp_path, monkeypatch) -> None:
    generator = _load_generator_module()
    target = tmp_path / "bundled_config.py"
    monkeypatch.setattr(generator, "TARGET", target)
    monkeypatch.setenv("SUPABASE_URL", "https://db.example.com")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_example")
    monkeypatch.delenv("AIDREAM_SERVER_URL_LIVE", raising=False)

    assert generator.main() == 0
    assert "AIDREAM_SERVER_URL" not in target.read_text(encoding="utf-8")
