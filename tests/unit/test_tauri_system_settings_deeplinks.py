"""Regression coverage for macOS System Settings deep links.

The shell plugin has its own URL validator in addition to Tauri capabilities.
Without an explicit plugin validator it accepts web URLs but rejects every
``x-apple.systempreferences:`` link, leaving permission buttons looking dead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TAURI_CONFIG = REPO_ROOT / "desktop" / "src-tauri" / "tauri.conf.json"


def test_shell_open_validator_allows_full_disk_access_settings() -> None:
    config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    validator = config["plugins"]["shell"]["open"]

    assert re.fullmatch(
        validator,
        "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    )


def test_shell_open_validator_does_not_allow_arbitrary_schemes() -> None:
    config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    validator = config["plugins"]["shell"]["open"]

    assert re.fullmatch(validator, "file:///etc/passwd") is None
    assert re.fullmatch(validator, "javascript:alert(1)") is None
