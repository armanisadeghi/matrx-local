"""THE ONE VARIABLE RULE guard for matrx-local's Python app (Arman, 2026-10-10).

The local engine fills prompts only through matrx-ai's shared functions
(``matrx_ai/config/template_substitution.py``). No module of the local app may search text for
``{{...}}`` itself: a second fill is a second rule. This is the same scan as aidream's
``aidream/services/ai_execution/tests/test_the_server_never_hunts_for_braces.py``; keep the
patterns identical.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The whole local Python app: every module under it can reach a local run.
RUN_PATH = ["app"]

#: The only modules allowed to build a ``{{`` pattern, and why. None today.
ALLOWED: dict[str, str] = {}

_STR = r"""(?:[rRbBuU]{0,2})["']"""
BRACE_PATTERN = re.compile(
    r"""re\.(?:compile|search|findall|finditer|sub|subn|split|match|fullmatch)\(\s*"""
    + _STR
    + r"""[^"']*\\\{\\\{"""
)
RAW_PATTERN = re.compile(r"""\b[rR]["'][^"'\n]*\\\{\\\{""")
SUBSTRING_PATTERN = re.compile(r"""["']\{\{[^"'\n]*["']\s+(?:not\s+)?in\s""")
METHOD_PATTERN = re.compile(
    r"""\.(?:find|rfind|index|rindex|count|startswith|endswith)\(\s*""" + _STR + r"""\{\{"""
)
PATTERNS = (BRACE_PATTERN, RAW_PATTERN, SUBSTRING_PATTERN, METHOD_PATTERN)


def _hits(text: str) -> list[int]:
    return sorted(
        {text.count("\n", 0, m.start()) + 1 for pattern in PATTERNS for m in pattern.finditer(text)}
    )


def _offenders() -> list[str]:
    hits: list[str] = []
    for root in RUN_PATH:
        for path in (REPO / root).rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.split("/")[-1].startswith("test_") or rel in ALLOWED:
                continue
            hits.extend(f"{rel}:{n}" for n in _hits(path.read_text(encoding="utf-8")))
    return hits


def test_no_local_app_module_searches_text_for_braces() -> None:
    assert (REPO / "app" / "services" / "ai").is_dir(), "the local run path moved; point RUN_PATH at it"
    assert _offenders() == [], "The local app never searches text for {{...}}: " + ", ".join(_offenders())


def test_the_guard_can_fail() -> None:
    """Every pattern the guard knows is proven to catch its own break."""
    breaks = {
        "one-line compile": 'PLACEHOLDER_RE = re.compile(r"\\{\\{\\s*([A-Za-z0-9_]+)\\s*\\}\\}")',
        "multi-line compile": 'PLACEHOLDER_RE = re.compile(\n    r"\\{\\{\\s*(\\w+)\\}\\}"\n)',
        "pattern built first": 'PATTERN = r"\\{\\{(\\w+)\\}\\}"',
        "substring in": 'had_template = "{{" in original_text and "}}" in original_text',
        "substring not in": 'if "{{" not in line:',
        ".find": 'at = text.find("{{")',
        ".count": 'n = text.count("{{")',
        ".startswith": 'if text.startswith("{{"):',
        ".endswith": "if text.endswith('{{'):",
        ".index": 'at = text.index("{{")',
    }
    for label, source in breaks.items():
        assert _hits(source), f"the guard misses a {label} brace search"
    assert _hits('x = 1\ny = 2\nat = text.find("{{")') == [3]
    assert not _hits('text = "Hello {{name}}"\nvalue = {"a": 1}')


def test_a_planted_break_in_the_app_is_caught(tmp_path) -> None:
    """The scan walks real files: a brace search planted in an app module is named by path and line."""
    app = tmp_path / "app" / "services" / "ai"
    app.mkdir(parents=True)
    (app / "fill.py").write_text('import re\n\nX = re.compile(r"\\{\\{(\\w+)\\}\\}")\n', encoding="utf-8")
    globals()["REPO"] = tmp_path
    try:
        assert _offenders() == ["app/services/ai/fill.py:3"]
    finally:
        globals()["REPO"] = Path(__file__).resolve().parents[2]
