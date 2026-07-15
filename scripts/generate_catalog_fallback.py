#!/usr/bin/env python3
"""Regenerate app/services/catalogs/compiled_data.py from the Rust/TS sources.

``compiled_data.py`` is the compiled-fallback tier for the four catalog kinds
whose source of truth lives OUTSIDE Python:

  - ``llm_model``        ← desktop/src-tauri/src/llm/model_selector.rs
  - ``whisper_model``    ← desktop/src-tauri/src/transcription/model_selector.rs
                           + transcription/downloader.rs (HF base URLs, VAD)
  - ``system_prompt``    ← desktop/src/lib/system-prompts.ts
  - ``api_key_provider`` ← desktop/src/lib/api-key-patterns.ts

All extraction is MECHANICAL (regex + balanced-brace parsing over the real
source files — adapted from the original seed extraction, catalog-seeds/
build_seeds.py, 2026-07-14); nothing is hand-transcribed. Any edit to those
Rust/TS constants MUST be followed by rerunning this script, or the offline
fallback silently rots. The drift guard
``tests/unit/test_catalog_fallback_drift.py`` re-extracts at test time and
fails the suite until the fallback is regenerated.

Usage:
    uv run python scripts/generate_catalog_fallback.py           # rewrite compiled_data.py
    uv run python scripts/generate_catalog_fallback.py --check   # exit 1 on drift, write nothing
"""
from __future__ import annotations

import importlib.util
import pprint
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "app" / "services" / "catalogs" / "compiled_data.py"

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/ _-]{0,199}$")

# The exact Rust struct shapes this extractor understands. A new/renamed
# field in model_selector.rs fails the field-set assertion LOUDLY — update
# these lists (and the DB kind schema) deliberately, then regenerate.
LLM_FIELDS = [
    "tier", "name", "provider", "filename", "disk_size_gb", "ram_required_gb",
    "text_rating", "code_rating", "vision_rating", "tool_calling_rating",
    "speed", "description", "knowledge_cutoff", "hf_model_card_url",
    "is_uncensored", "is_server_grade", "hf_url", "hf_parts", "context_length",
    "expected_size_bytes", "hf_part_sizes", "mmproj_filename", "mmproj_url",
    "mmproj_expected_size_bytes", "variants",
]
VARIANT_FIELDS = [
    "label", "quant", "filename", "disk_size_gb", "ram_required_gb",
    "hf_url", "hf_parts", "expected_size_bytes", "hf_part_sizes",
    "mmproj_filename", "mmproj_url", "mmproj_expected_size_bytes",
]


def _entry(key: str, payload: dict, *, artifact_url=None, artifact_sha256=None,
           artifact_size_bytes=None, sort_order=0, notes=None) -> dict:
    assert KEY_RE.match(key), f"key fails the DB key regex: {key!r}"
    e: dict[str, Any] = {
        "key": key,
        "payload": payload,
        "artifact_url": artifact_url,
        "artifact_sha256": artifact_sha256,
        "artifact_size_bytes": artifact_size_bytes,
        "sort_order": sort_order,
    }
    if notes:
        e["notes"] = notes
    return e


# ─────────────────────────────────────────────────────────────────────────────
# Rust parsing primitives (balanced-brace struct splitting + literal parsing)
# ─────────────────────────────────────────────────────────────────────────────


def _strip_line_comments(src: str) -> str:
    # Full-line comments only: URLs contain //, but never at line start.
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


def _parse_rust_value(field: str, raw: str):
    raw = raw.strip().rstrip(",").strip()
    if raw.startswith("&["):
        inner = raw[2:-1].strip() if raw.endswith("]") else raw[2:].strip()
        if not inner:
            return []
        items = [s.strip().rstrip(",") for s in re.findall(r'"[^"]*"|[\d_]+', inner)]
        out: list[Any] = []
        for it in items:
            out.append(it[1:-1] if it.startswith('"') else int(it.replace("_", "")))
        return out
    if raw.startswith('"'):
        m = re.match(r'^"(.*)"$', raw, re.S)
        assert m, f"bad string literal for {field}: {raw!r}"
        return m.group(1)
    if raw in ("true", "false"):
        return raw == "true"
    if "::" in raw and re.match(r"^\w+::\w+$", raw):  # enum variant → its name
        return raw.split("::", 1)[1]
    if re.match(r"^[\d_]+$", raw):
        return int(raw.replace("_", ""))
    if re.match(r"^[\d_]*\.[\d_]+$", raw):
        return float(raw.replace("_", ""))
    if re.match(r"^[A-Z][A-Z0-9_]*$", raw):  # reference to a variant-slice const
        return ("IDENT", raw)
    raise ValueError(f"unparsed Rust value for {field}: {raw!r}")


def _parse_rust_struct(body: str) -> dict:
    """Parse ``field: value,`` pairs; array values (may span lines) first."""
    d: dict[str, Any] = {}
    for m in re.finditer(r"(\w+):\s*(&\[[^\]]*\])\s*,", body, re.S):
        d[m.group(1)] = _parse_rust_value(m.group(1), m.group(2))
    body_no_arrays = re.sub(r"(\w+):\s*&\[[^\]]*\]\s*,", "", body, flags=re.S)
    for m in re.finditer(r"^\s*(\w+):\s*(.+?),\s*$", body_no_arrays, re.M):
        f = m.group(1)
        if f not in d:
            d[f] = _parse_rust_value(f, m.group(2))
    return d


def _split_structs(blob: str, marker: str) -> list[str]:
    """Split on ``marker {`` and return the balanced-brace bodies."""
    bodies: list[str] = []
    idx = 0
    while True:
        start = blob.find(marker + " {", idx)
        if start == -1:
            break
        i = blob.index("{", start)
        depth = 0
        for j in range(i, len(blob)):
            if blob[j] == "{":
                depth += 1
            elif blob[j] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(blob[i + 1:j])
                    idx = j
                    break
        else:
            raise ValueError(f"unbalanced braces after {marker!r}")
    return bodies


# ─────────────────────────────────────────────────────────────────────────────
# Per-kind extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_llm_model_entries() -> list[dict]:
    src = _strip_line_comments(
        (REPO / "desktop/src-tauri/src/llm/model_selector.rs").read_text(
            encoding="utf-8"
        )
    )

    # Variant slices are discovered dynamically — every IDENT reference in
    # LLM_MODELS must resolve to one of them.
    variant_slices: dict[str, list[dict]] = {}
    for m in re.finditer(
        r"static (\w+): &\[LlmModelVariant\] = &\[(.*?)\n\];", src, re.S
    ):
        variant_slices[m.group(1)] = [
            _parse_rust_struct(b) for b in _split_structs(m.group(2), "LlmModelVariant")
        ]

    models_m = re.search(
        r"pub const LLM_MODELS: &\[LlmModelInfo\] = &\[(.*?)\n\];", src, re.S
    )
    assert models_m, "LLM_MODELS const not found in model_selector.rs"
    models = [
        _parse_rust_struct(b) for b in _split_structs(models_m.group(1), "LlmModelInfo")
    ]
    assert models, "parsed ZERO LLM models"

    entries: list[dict] = []
    for i, m in enumerate(models):
        assert set(m) == set(LLM_FIELDS), (
            f"LlmModelInfo field drift on {m.get('name')!r}: "
            f"{sorted(set(LLM_FIELDS) ^ set(m))} — update LLM_FIELDS + the "
            "kind schema deliberately, then regenerate"
        )
        payload = {f: m[f] for f in LLM_FIELDS if f != "variants"}
        v = m["variants"]
        if isinstance(v, tuple) and v[0] == "IDENT":
            assert v[1] in variant_slices, f"unknown variant slice {v[1]!r}"
            variants = variant_slices[v[1]]
        else:
            assert v == [], f"unexpected variants value on {m['name']!r}: {v}"
            variants = []
        for var in variants:
            assert set(var) == set(VARIANT_FIELDS), (
                f"LlmModelVariant field drift in {m['name']!r}: "
                f"{sorted(set(VARIANT_FIELDS) ^ set(var))}"
            )
        payload["variants"] = variants
        multi = len(variants) > 0
        entries.append(
            _entry(
                m["tier"],
                payload,
                artifact_url=None if multi else m["hf_url"],
                artifact_size_bytes=None if multi else m["expected_size_bytes"],
                sort_order=i * 10,
                notes=(
                    "multi-variant model: artifact URLs live on payload.variants[].hf_url"
                    if multi
                    else None
                ),
            )
        )
    return entries


def extract_whisper_model_entries() -> list[dict]:
    src = _strip_line_comments(
        (REPO / "desktop/src-tauri/src/transcription/model_selector.rs").read_text(
            encoding="utf-8"
        )
    )
    blob_m = re.search(r"pub const MODELS: &\[ModelInfo\] = &\[(.*?)\n\];", src, re.S)
    assert blob_m, "MODELS const not found in transcription/model_selector.rs"
    models = [
        _parse_rust_struct(b) for b in _split_structs(blob_m.group(1), "ModelInfo")
    ]
    assert models, "parsed ZERO whisper models"

    dl = (REPO / "desktop/src-tauri/src/transcription/downloader.rs").read_text(
        encoding="utf-8"
    )
    hf_whisper_base = re.search(r'const HF_WHISPER_BASE: &str = "([^"]+)"', dl).group(1)
    hf_vad_base = re.search(r'const HF_VAD_BASE: &str = "([^"]+)"', dl).group(1)
    vad_filename = re.search(
        r'pub const VAD_MODEL_FILENAME: &str = "([^"]+)"', dl
    ).group(1)

    entries: list[dict] = []
    for i, m in enumerate(models):
        url = f"{hf_whisper_base}/{m['filename']}"
        payload = dict(m)
        payload["download_url"] = url
        payload["role"] = "transcription"
        entries.append(_entry(m["filename"], payload, artifact_url=url, sort_order=i * 10))
    entries.append(
        _entry(
            vad_filename,
            {
                "tier": None,
                "filename": vad_filename,
                "download_size_mb": None,
                "ram_required_mb": None,
                "relative_speed": None,
                "accuracy": None,
                "description": (
                    "Silero VAD model required for streaming transcription "
                    "(no GGML header; validated by size > 50KB)."
                ),
                "download_url": f"{hf_vad_base}/{vad_filename}",
                "role": "vad",
            },
            artifact_url=f"{hf_vad_base}/{vad_filename}",
            sort_order=len(models) * 10,
            notes=(
                "VAD companion model; source defines only filename + base URL "
                "(downloader.rs), struct fields null."
            ),
        )
    )
    return entries


def extract_system_prompt_entries() -> list[dict]:
    src = (REPO / "desktop/src/lib/system-prompts.ts").read_text(encoding="utf-8")
    prompt_consts = dict(re.findall(r"const (PROMPT_BUILTIN_\w+) = `(.*?)`;", src, re.S))
    arr_m = re.search(r"export const BUILTIN_PROMPTS[^=]*=\s*\[(.*?)\n\];", src, re.S)
    assert arr_m, "BUILTIN_PROMPTS array not found in system-prompts.ts"
    prompt_objs = re.findall(
        r'\{\s*id:\s*"([^"]+)",\s*name:\s*"([^"]+)",\s*content:\s*'
        r'(PROMPT_BUILTIN_\w+),\s*category:\s*"([^"]+)",\s*\}',
        arr_m.group(1),
    )
    assert prompt_objs, "parsed ZERO builtin prompts"
    entries: list[dict] = []
    for i, (pid, name, const_ref, category) in enumerate(prompt_objs):
        assert const_ref in prompt_consts, (
            f"BUILTIN_PROMPTS references {const_ref} but no matching "
            "template-literal const was parsed — extractor drift"
        )
        entries.append(
            _entry(
                pid,
                {
                    "id": pid,
                    "name": name,
                    "content": prompt_consts[const_ref],
                    "category": category,
                },
                sort_order=i * 10,
            )
        )
    return entries


def extract_api_key_provider_entries() -> list[dict]:
    src = (REPO / "desktop/src/lib/api-key-patterns.ts").read_text(encoding="utf-8")

    def ts_string_array(name: str) -> list[str]:
        m = re.search(rf"export const {name}: string\[\] = \[(.*?)\];", src, re.S)
        assert m, f"{name} not found in api-key-patterns.ts"
        return re.findall(r'"([^"]+)"', m.group(1))

    strip_prefixes = ts_string_array("GLOBAL_STRIP_PREFIXES")
    strip_suffixes = ts_string_array("GLOBAL_STRIP_SUFFIXES")
    assert strip_prefixes and strip_suffixes

    pp_m = re.search(
        r"export const PROVIDER_PATTERNS: ProviderPattern\[\] = \[(.*?)\n\];", src, re.S
    )
    assert pp_m, "PROVIDER_PATTERNS not found in api-key-patterns.ts"
    providers: list[dict] = []
    for m in re.finditer(
        r'\{\s*names:\s*\[([^\]]*)\],\s*(?:envVarNames:\s*\[([^\]]*)\],\s*)?label:\s*"([^"]+)",\s*\}',
        pp_m.group(1),
    ):
        providers.append(
            {
                "names": re.findall(r'"([^"]+)"', m.group(1)),
                "env_var_names": re.findall(r'"([^"]+)"', m.group(2) or ""),
                "label": m.group(3),
            }
        )
    assert providers, "parsed ZERO provider patterns"

    entries = [_entry(p["names"][0], p, sort_order=i * 10) for i, p in enumerate(providers)]
    entries.append(
        _entry(
            "global-strip-lists",
            {"strip_prefixes": strip_prefixes, "strip_suffixes": strip_suffixes},
            sort_order=len(providers) * 10,
            notes=(
                "GLOBAL_STRIP_PREFIXES + GLOBAL_STRIP_SUFFIXES (env-var noise "
                "stripped before provider matching). Key '_strip_lists' "
                "impossible: DB key regex forbids leading underscore."
            ),
        )
    )
    return entries


# Kind → (constant name in compiled_data.py, extractor, source comment).
KINDS: list[tuple[str, str, Any, str]] = [
    ("llm_model", "COMPILED_LLM_MODEL_ENTRIES", extract_llm_model_entries,
     "desktop/src-tauri/src/llm/model_selector.rs"),
    ("whisper_model", "COMPILED_WHISPER_MODEL_ENTRIES", extract_whisper_model_entries,
     "desktop/src-tauri/src/transcription/model_selector.rs + downloader.rs"),
    ("system_prompt", "COMPILED_SYSTEM_PROMPT_ENTRIES", extract_system_prompt_entries,
     "desktop/src/lib/system-prompts.ts"),
    ("api_key_provider", "COMPILED_API_KEY_PROVIDER_ENTRIES",
     extract_api_key_provider_entries, "desktop/src/lib/api-key-patterns.ts"),
]


def extract_all() -> dict[str, list[dict]]:
    """kind → extracted entry dicts, with per-kind duplicate-key checks."""
    out: dict[str, list[dict]] = {}
    for kind, _const, extractor, _src in KINDS:
        entries = extractor()
        keys = [e["key"] for e in entries]
        assert len(keys) == len(set(keys)), f"duplicate keys in {kind}: {keys}"
        out[kind] = entries
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Render / check
# ─────────────────────────────────────────────────────────────────────────────

_HEADER = '''"""Vendored compiled-fallback catalog data for kinds whose source of truth
lives OUTSIDE Python (Rust consts / desktop TS constants).

GENERATED — do not hand-edit. Regenerate with

    uv run python scripts/generate_catalog_fallback.py

whenever the Rust/TS fallback constants change; the drift guard
(tests/unit/test_catalog_fallback_drift.py) re-extracts from the sources at
test time and fails the suite until this file is regenerated. Python-sourced
kinds (image/video/TTS/NER/wake-word/LoRA/presets) are NOT vendored here —
compiled.py adapts them live from their legacy in-code lists so they can
never drift.

Shape per kind: list of dicts matching the ``catalog_entries`` seed shape
(key / payload / artifact_* / sort_order / notes?).
"""

from __future__ import annotations

from typing import Any

'''


def render_module(data: dict[str, list[dict]]) -> str:
    parts = [_HEADER]
    for kind, const, _extractor, src in KINDS:
        formatted = pprint.pformat(data[kind], indent=1, width=100, sort_dicts=False)
        parts.append(f"# Source: {src}\n{const}: list[dict[str, Any]] = {formatted}\n\n")
    return "".join(parts)


def load_current() -> dict[str, list[dict]]:
    """The constants currently committed in compiled_data.py (import by path
    so this works without the app package on sys.path)."""
    spec = importlib.util.spec_from_file_location("compiled_data_current", TARGET)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {kind: getattr(mod, const) for kind, const, _e, _s in KINDS}


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    fresh = extract_all()

    if check_only:
        current = load_current()
        drifted = [k for k in current if current[k] != fresh[k]]
        if drifted:
            print(
                "DRIFT: compiled_data.py no longer matches the Rust/TS sources "
                f"for kind(s): {', '.join(drifted)}.\n"
                "The Rust/TS catalog constants changed without regenerating the "
                "offline fallback. Run:\n"
                "    uv run python scripts/generate_catalog_fallback.py",
                file=sys.stderr,
            )
            return 1
        counts = {k: len(v) for k, v in fresh.items()}
        print(f"compiled_data.py matches the Rust/TS sources ({counts})")
        return 0

    TARGET.write_text(render_module(fresh), encoding="utf-8")
    counts = {k: len(v) for k, v in fresh.items()}
    print(f"wrote {TARGET.relative_to(REPO)} ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
