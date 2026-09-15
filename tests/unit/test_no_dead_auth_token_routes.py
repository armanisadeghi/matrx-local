"""Guard: the POST /auth/token and DELETE /auth/token routes were removed in
the FS-C5b custody cutover (commit 7faafcff2) — the engine's session now comes
from the sync daemon (app/services/sync_client/client.py,
get_sync_client().access_grant()); see app/services/session_freshness.py for
the current honest wording. No code under app/ may reference either dead
route in a string literal or comment: a remedy, allowlist entry, or
docstring naming a route that 404s live is a defect (lane CS-18, 2026-09-15).

This test lives under tests/, outside the app/ tree it scans, so this
docstring naming the two dead routes does not trip its own guard.
"""

from __future__ import annotations

from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_GUARD_FILE = Path(__file__).resolve()

_BANNED_PHRASES = (
    "POST /auth/token",
    "DELETE /auth/token",
)


def test_no_dead_auth_token_route_string_literals() -> None:
    """Scan every .py file under app/ for the two dead route strings, in both
    string literals (remedy text, docstrings) and comments (stale narration)
    — both mislead a reader equally once the routes 404 live."""
    offenders: list[str] = []
    for py_file in APP_ROOT.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            for phrase in _BANNED_PHRASES:
                if phrase in line:
                    offenders.append(f"{py_file.relative_to(APP_ROOT.parent)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Dead auth-token route referenced in app/ (POST /auth/token and "
        "DELETE /auth/token were removed in commit 7faafcff2; the engine's "
        "session comes from the sync daemon — see "
        "app/services/session_freshness.py):\n" + "\n".join(offenders)
    )
