"""Canonical catalog of user-supplied provider grants.

This is intentionally data-only so repositories, routes, chat preflight, and
runtime key resolution can all import it without creating service cycles.
Provider-specific network validation remains in ``key_validation.py``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderGrantSpec:
    key: str
    label: str
    description: str
    env_vars: tuple[str, ...]
    chat_endpoints: tuple[str, ...] = ()

    @property
    def supports_chat(self) -> bool:
        return bool(self.chat_endpoints)


PROVIDER_GRANTS: dict[str, ProviderGrantSpec] = {
    "anthropic": ProviderGrantSpec("anthropic", "Anthropic", "Claude Sonnet, Claude Haiku, Claude Opus", ("ANTHROPIC_API_KEY",), ("anthropic_chat", "anthropic_adaptive")),
    "brave": ProviderGrantSpec("brave", "Brave Search", "Private web search and research", ("BRAVE_API_KEY",)),
    "cerebras": ProviderGrantSpec("cerebras", "Cerebras", "Llama models with wafer-scale inference", ("CEREBRAS_API_KEY",), ("cerebras_chat",)),
    "civitai": ProviderGrantSpec("civitai", "Civitai", "Custom image models and LoRAs from Civitai", ("CIVITAI_API_KEY", "CIVITAI_API_TOKEN")),
    "elevenlabs": ProviderGrantSpec("elevenlabs", "ElevenLabs", "Text-to-speech voices", ("ELEVENLABS_API_KEY",)),
    "fastino": ProviderGrantSpec("fastino", "Fastino", "Fastino / Pioneer models", ("PIONEER_API_KEY", "FASTINO_API_KEY")),
    "google": ProviderGrantSpec("google", "Google", "Gemini models", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), ("google_chat",)),
    "groq": ProviderGrantSpec("groq", "Groq", "Fast hosted open-model inference", ("GROQ_API_KEY",), ("groq_chat",)),
    "huggingface": ProviderGrantSpec("huggingface", "Hugging Face", "Model downloads, including gated repositories", ("HUGGING_FACE_HUB_TOKEN", "HF_TOKEN")),
    "openai": ProviderGrantSpec("openai", "OpenAI", "GPT and reasoning models", ("OPENAI_API_KEY",), ("openai_chat",)),
    "together": ProviderGrantSpec("together", "Together AI", "Hosted open models", ("TOGETHER_API_KEY",), ("together_chat",)),
    "xai": ProviderGrantSpec("xai", "xAI", "Grok models", ("XAI_API_KEY",), ("xai_chat",)),
}

VALID_PROVIDERS: frozenset[str] = frozenset(PROVIDER_GRANTS)
CHAT_PROVIDERS: frozenset[str] = frozenset(
    key for key, spec in PROVIDER_GRANTS.items() if spec.supports_chat
)
ENDPOINT_TO_PROVIDER: dict[str, str] = {
    endpoint: key
    for key, spec in PROVIDER_GRANTS.items()
    for endpoint in spec.chat_endpoints
}
