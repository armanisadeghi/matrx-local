"""Nothing may read, write, or describe as live the ``auth_tokens`` table V34 dropped.

The FS-C5b custody cutover made ``matrx-syncd`` the device's only session holder and local-DB
migration V34 dropped ``auth_tokens``. Verification then found a documented proof script still
``SELECT``-ing from it (it could only raise ``no such table``) and ten engine files still telling
the next reader that credentials come from "the persisted auth_tokens row" (findings C5b-3 and
C5b-4). A stale description of where credentials live is how the next agent rebuilds a second
session holder, so both shapes are fenced here:

* SQL that touches the table anywhere but the migration that dropped it, and
* prose that presents it as a current credential source.

Historical mentions ("dropped in V34", "used to own") are fine and are not matched.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("app", "scripts")
SUFFIXES = {".py", ".md"}
# The migration that created and then dropped the table is its only legitimate SQL home.
ALLOWED = {Path("app/services/local_db/schema.py")}

SQL = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)\s+auth_tokens\b", re.IGNORECASE)
LIVE_SOURCE = re.compile(
    r"persisted\s+`?auth_tokens`?\s+row"
    r"|from\s+the\s+`?auth_tokens`?\s+(SQLite\s+)?(table|row)"
    r"|user_id\s+persisted\s+in\s+auth_tokens"
    r"|stored\s+auth_tokens"
    r"|auth_tokens\s+table\s+\(single\s+row",
    re.IGNORECASE,
)


def _findings(root: Path) -> list[str]:
    found: list[str] = []
    for top in ROOTS:
        base = root / top
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            rel = path.relative_to(root)
            if rel in ALLOWED:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            # Collapse line wraps so a phrase split across comment lines is still one phrase.
            flat = re.sub(r"\s*\n\s*(#\s*)?", " ", text)
            for pattern, label in ((SQL, "SQL on the dropped table"), (LIVE_SOURCE, "live-source prose")):
                for match in pattern.finditer(flat):
                    found.append(f"{rel}: {label}: {match.group(0)!r}")
    return found


def test_nothing_reads_or_describes_the_dropped_auth_tokens_table() -> None:
    findings = _findings(REPO)
    assert not findings, (
        "auth_tokens was dropped in V34; credentials come from the sync daemon "
        "(TokenRepo -> app.services.sync_client):\n  " + "\n  ".join(findings)
    )


def test_the_guard_catches_both_shapes(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "rogue.py").write_text(
        'row = conn.execute("SELECT access_token FROM auth_tokens WHERE key=?")\n'
    )
    (tmp_path / "app" / "rogue.py").write_text(
        "# Credentials come from the persisted auth_tokens\n# row, every tick.\n"
    )
    (tmp_path / "app" / "honest.py").write_text(
        "# auth_tokens was dropped in V34; this class used to own a row in it.\n"
    )
    findings = _findings(tmp_path)
    assert any("scripts/rogue.py" in f and "SQL" in f for f in findings), findings
    assert any("app/rogue.py" in f and "live-source" in f for f in findings), findings
    assert not any("honest.py" in f for f in findings), findings
