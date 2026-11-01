"""Feedback b1d61a7d: with no local or Vault key, matrx-ai falls through to os.environ — on this Mac
that was the PRODUCTION Anthropic key from .env, spending silently. The fall-through must announce
itself (once per variable per process), and must stay quiet when a saved key answers."""

from __future__ import annotations

from app.services.ai import key_manager


def _reset(monkeypatch):
    monkeypatch.setattr(key_manager, "_user_keys", {})
    monkeypatch.setattr(key_manager, "_vault_keys", {})
    if hasattr(key_manager, "_announced_env_fallback"):
        monkeypatch.setattr(key_manager, "_announced_env_fallback", set())


class _Recorder:
    def __init__(self):
        self.lines: list[str] = []

    def warning(self, msg, *args):
        self.lines.append(msg % args if args else msg)

    def __getattr__(self, name):
        return lambda *a, **k: None


def _record(monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(key_manager, "logger", rec)
    return rec


def _warnings(rec: _Recorder):
    return [line for line in rec.lines if "ANTHROPIC_API_KEY" in line]


def test_environment_fallback_is_announced_once(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    rec = _record(monkeypatch)
    assert key_manager._resolve_env_var("ANTHROPIC_API_KEY") is None
    assert key_manager._resolve_env_var("ANTHROPIC_API_KEY") is None
    assert len(_warnings(rec)) == 1


def test_a_saved_key_is_used_without_a_word(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    rec = _record(monkeypatch)
    monkeypatch.setattr(key_manager, "_user_keys", {"anthropic": "sk-ant-local-test"})
    assert key_manager._resolve_env_var("ANTHROPIC_API_KEY") == "sk-ant-local-test"
    assert _warnings(rec) == []
