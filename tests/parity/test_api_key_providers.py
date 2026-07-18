"""
API key provider parity test.

Verifies that:
1. Every provider in the TypeScript PROVIDER_PATTERNS (api-key-patterns.ts)
   has a canonical ID that also appears in Python's VALID_PROVIDERS set.
2. Every provider in Python's VALID_PROVIDERS is also defined in the TS file.
3. The Hugging Face provider is present in both (added in recent release).

Why this matters:
  - The frontend's bulk-import UI parses .env files and maps keys to
    provider IDs. Those IDs are then sent to PUT /settings/api-keys/{provider}.
  - If a provider is listed in the frontend but not in VALID_PROVIDERS on the
    Python side, the API rejects the key with a 400 error.
  - If Python accepts a new provider (like huggingface) but the frontend
    doesn't know about it, the bulk import silently drops the key.

This test runs without the engine (pure file parsing).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.ai.provider_grants import VALID_PROVIDERS


PROJECT_ROOT = Path(__file__).parent.parent.parent
TS_PATTERNS_FILE = (
    PROJECT_ROOT / "desktop" / "src" / "lib" / "api-key-patterns.ts"
)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_ts_provider_ids() -> set[str]:
    """
    Extract canonical provider IDs from PROVIDER_PATTERNS in api-key-patterns.ts.

    Each entry looks like:
        names: ["openai", "open_ai", ...],
    The first element of `names` is the canonical ID sent to the backend.
    """
    source = TS_PATTERNS_FILE.read_text(encoding="utf-8")

    # Find the PROVIDER_PATTERNS array
    array_match = re.search(
        r"PROVIDER_PATTERNS\s*:\s*ProviderPattern\[\]\s*=\s*\[(.+?)\];",
        source,
        re.DOTALL,
    )
    assert array_match, (
        f"Could not find PROVIDER_PATTERNS array in {TS_PATTERNS_FILE}"
    )

    block = array_match.group(1)

    # Find each `names: ["canonical", ...]` array — the first element is canonical
    ids: set[str] = set()
    for m in re.finditer(r'names\s*:\s*\[\s*"([^"]+)"', block):
        ids.add(m.group(1))

    return ids


def parse_python_valid_providers() -> set[str]:
    """Read the canonical Python provider catalog."""
    return set(VALID_PROVIDERS)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ts_patterns_file_exists() -> None:
    assert TS_PATTERNS_FILE.exists(), (
        f"api-key-patterns.ts not found at {TS_PATTERNS_FILE}"
    )


def test_ts_providers_all_in_python() -> None:
    """Every TS provider canonical ID is accepted by the Python backend."""
    ts_ids = parse_ts_provider_ids()
    python_ids = parse_python_valid_providers()

    missing_in_python = ts_ids - python_ids
    assert not missing_in_python, (
        f"Provider(s) in api-key-patterns.ts not in Python VALID_PROVIDERS:\n"
        f"  {sorted(missing_in_python)}\n\n"
        "The frontend would send these to PUT /settings/api-keys/{provider} "
        "but the backend would reject them. Add them to VALID_PROVIDERS in "
        "app/services/ai/provider_grants.py."
    )


def test_python_providers_all_in_ts() -> None:
    """Every Python VALID_PROVIDER has a corresponding TS pattern entry."""
    ts_ids = parse_ts_provider_ids()
    python_ids = parse_python_valid_providers()

    missing_in_ts = python_ids - ts_ids
    assert not missing_in_ts, (
        f"Provider(s) in Python VALID_PROVIDERS not in api-key-patterns.ts:\n"
        f"  {sorted(missing_in_ts)}\n\n"
        "The bulk .env import UI won't recognize these provider keys. "
        "Add them to PROVIDER_PATTERNS in desktop/src/lib/api-key-patterns.ts."
    )


def test_huggingface_in_both() -> None:
    """Hugging Face (recently added) is present in both frontend and backend."""
    ts_ids = parse_ts_provider_ids()
    python_ids = parse_python_valid_providers()

    assert "huggingface" in ts_ids, (
        "'huggingface' missing from PROVIDER_PATTERNS in api-key-patterns.ts"
    )
    assert "huggingface" in python_ids, (
        "'huggingface' missing from the canonical provider grant catalog"
    )

    assert "brave" in ts_ids and "brave" in python_ids


def test_provider_count_reasonable() -> None:
    """At least 7 providers defined (guards against empty parse)."""
    ts_ids = parse_ts_provider_ids()
    python_ids = parse_python_valid_providers()

    assert len(ts_ids) >= 7, (
        f"Only {len(ts_ids)} TS providers parsed — parser may be broken. Got: {ts_ids}"
    )
    assert len(python_ids) >= 7, (
        f"Only {len(python_ids)} Python providers parsed — parser may be broken. Got: {python_ids}"
    )
