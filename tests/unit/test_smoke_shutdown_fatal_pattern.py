"""The smoke log triage must retain every forced shutdown-timeout failure."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


SMOKE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke.sh"


def _fatal_patterns() -> str:
    source = SMOKE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"^FATAL_PATTERNS='([^']+)'$", source, flags=re.MULTILINE)
    assert match is not None
    return match.group(1)


def _is_fatal(line: str) -> bool:
    result = subprocess.run(
        ["grep", "-Ei", _fatal_patterns()],
        input=line + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def test_shutdown_timeout_messages_remain_smoke_fatal_across_copy_changes() -> None:
    """Exercise the real grep expression used by ``scan_log``."""
    old_timeout = (
        "[shutdown] Lifespan teardown did NOT complete within 15s — "
        "force-killing engine-owned children and exiting."
    )
    current_timeout = (
        "[shutdown] Lifespan teardown did NOT complete within 15s — "
        "forced cleanup ran because uvicorn drain or service teardown is blocked."
    )
    clean_shutdown = "[shutdown] uvicorn server thread exited"

    assert _is_fatal(old_timeout)
    assert _is_fatal(current_timeout)
    assert not _is_fatal(clean_shutdown)
