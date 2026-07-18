"""
Settings parity test.

Verifies that the AppSettings interface in desktop/src/lib/settings.ts
and the DEFAULT_SETTINGS dict in app/services/cloud_sync/settings_sync.py
define exactly the same set of settings keys (after camelCase→snake_case
conversion).

Why this matters:
  - settings.ts is the source of truth for the React frontend.
  - settings_sync.py DEFAULT_SETTINGS is the source of truth for Python.
  - Cloud sync uses snake_case keys. The mergeCloudSettings() and
    settingsToCloud() functions in settings.ts do the conversion.
  - If a key exists in one but not the other, settings will silently
    drop or ignore it during cloud sync.

This test runs without the engine (pure file parsing) so it catches
drift as soon as a developer adds a setting to one side but forgets
the other.
"""

from __future__ import annotations

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent.parent
TS_SETTINGS_FILE = PROJECT_ROOT / "desktop" / "src" / "lib" / "settings.ts"
PY_SETTINGS_FILE = (
    PROJECT_ROOT / "app" / "services" / "cloud_sync" / "settings_sync.py"
)

# Engine-owned settings keys: synced through the app_settings blob like
# everything else, but with a SINGLE WRITER — the Python engine. The React
# app reads/writes them only via dedicated engine endpoints (e.g.
# `cloud_tools` via GET/PUT /chat/local-tools*), never through the
# AppSettings merge in settings.ts, so a stale local copy can never clobber
# the cloud value. These are intentionally absent from the TS interface.
ENGINE_OWNED_PY_KEYS = {"cloud_tools"}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case.

    Examples:
      launchOnStartup -> launch_on_startup
      llmChatTopP     -> llm_chat_top_p
      proxyEnabled    -> proxy_enabled
    """
    # Insert underscore before sequences of uppercase letters followed by lowercase
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    # Insert underscore before a single uppercase letter preceded by a lowercase letter
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.lower()


def parse_ts_app_settings_keys() -> set[str]:
    """
    Extract field names from the AppSettings interface in settings.ts.

    Looks for the block:
        export interface AppSettings {
          fieldName: type;
          ...
        }
    and extracts the field names (camelCase), then converts to snake_case.
    """
    source = TS_SETTINGS_FILE.read_text(encoding="utf-8")

    # Find the AppSettings interface block
    match = re.search(
        r"export\s+interface\s+AppSettings\s*\{([^}]+)\}",
        source,
        re.DOTALL,
    )
    assert match, (
        f"Could not find 'export interface AppSettings {{...}}' in {TS_SETTINGS_FILE}"
    )

    block = match.group(1)

    # Extract field names — lines like:  fieldName: type;
    # Skip comment lines (// ...) and blank lines
    field_names: set[str] = set()
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("/*") or line.startswith("*"):
            continue
        # Match: identifier followed by optional ? then colon
        m = re.match(r"^(\w+)\??\s*:", line)
        if m:
            field_names.add(m.group(1))

    return {camel_to_snake(k) for k in field_names}


def parse_py_default_settings_keys() -> set[str]:
    """
    Extract keys from DEFAULT_SETTINGS dict in settings_sync.py.

    Looks for:
        DEFAULT_SETTINGS: dict[str, Any] = {
            "key": value,
            ...
        }
    """
    source = PY_SETTINGS_FILE.read_text(encoding="utf-8")

    # Find the start of the DEFAULT_SETTINGS dict, then walk to the MATCHING
    # closing brace. A naive `\{([^}]+)\}` capture stops at the first `}`,
    # which silently truncates the block as soon as any value is a nested
    # dict (e.g. "cloud_tools": {"disabled_tools": []}) — every key after it
    # would vanish from the parity check.
    match = re.search(
        r"DEFAULT_SETTINGS\s*(?::\s*[^=]+)?\s*=\s*\{",
        source,
    )
    assert match, (
        f"Could not find 'DEFAULT_SETTINGS = {{...}}' in {PY_SETTINGS_FILE}"
    )

    start = match.end()
    depth = 1
    end = start
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    assert depth == 0, (
        f"Unbalanced braces after DEFAULT_SETTINGS in {PY_SETTINGS_FILE}"
    )

    block = source[start:end]

    # Extract string keys — lines like:  "key": value,
    keys: set[str] = set()
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^"(\w+)"\s*:', line)
        if m:
            keys.add(m.group(1))

    return keys


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ts_settings_file_exists() -> None:
    """settings.ts exists at the expected path."""
    assert TS_SETTINGS_FILE.exists(), (
        f"settings.ts not found at {TS_SETTINGS_FILE}"
    )


def test_py_settings_file_exists() -> None:
    """settings_sync.py exists at the expected path."""
    assert PY_SETTINGS_FILE.exists(), (
        f"settings_sync.py not found at {PY_SETTINGS_FILE}"
    )


def test_ts_keys_not_empty() -> None:
    """AppSettings interface has at least 40 fields."""
    keys = parse_ts_app_settings_keys()
    assert len(keys) >= 40, (
        f"Only found {len(keys)} keys in AppSettings. Parser may be broken. Keys: {sorted(keys)}"
    )


def test_py_keys_not_empty() -> None:
    """DEFAULT_SETTINGS has at least 40 keys."""
    keys = parse_py_default_settings_keys()
    assert len(keys) >= 40, (
        f"Only found {len(keys)} keys in DEFAULT_SETTINGS. Parser may be broken. Keys: {sorted(keys)}"
    )


def test_ts_keys_not_in_python() -> None:
    """No AppSettings key is missing from Python DEFAULT_SETTINGS."""
    ts_keys = parse_ts_app_settings_keys()
    py_keys = parse_py_default_settings_keys()
    missing_from_python = ts_keys - py_keys
    assert not missing_from_python, (
        f"{len(missing_from_python)} key(s) in AppSettings (TS) but missing from "
        f"DEFAULT_SETTINGS (Python):\n  "
        + "\n  ".join(sorted(missing_from_python))
        + "\n\nAdd these to DEFAULT_SETTINGS in app/services/cloud_sync/settings_sync.py"
    )


def test_python_keys_not_in_ts() -> None:
    """No Python DEFAULT_SETTINGS key is missing from AppSettings."""
    ts_keys = parse_ts_app_settings_keys()
    py_keys = parse_py_default_settings_keys()
    missing_from_ts = py_keys - ts_keys - ENGINE_OWNED_PY_KEYS
    assert not missing_from_ts, (
        f"{len(missing_from_ts)} key(s) in DEFAULT_SETTINGS (Python) but missing from "
        f"AppSettings (TS):\n  "
        + "\n  ".join(sorted(missing_from_ts))
        + "\n\nAdd these to the AppSettings interface in desktop/src/lib/settings.ts"
    )


def test_settings_to_cloud_covers_all_ts_keys() -> None:
    """settingsToCloud() in settings.ts maps every AppSettings key to cloud format."""
    source = TS_SETTINGS_FILE.read_text(encoding="utf-8")

    # Find settingsToCloud function body
    match = re.search(
        r"export\s+function\s+settingsToCloud\s*\([^)]+\)[^{]*\{(.+?)\n\}",
        source,
        re.DOTALL,
    )
    if not match:
        # Function might not exist yet — skip gracefully
        return

    cloud_body = match.group(1)
    ts_keys = parse_ts_app_settings_keys()
    ts_camel_keys = {
        k for k in parse_ts_app_settings_keys()  # already parsed
    }

    # Re-parse original camelCase keys for this check
    ts_source = TS_SETTINGS_FILE.read_text(encoding="utf-8")
    iface_match = re.search(
        r"export\s+interface\s+AppSettings\s*\{([^}]+)\}", ts_source, re.DOTALL
    )
    if not iface_match:
        return
    camel_keys: set[str] = set()
    for line in iface_match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("/*"):
            continue
        m = re.match(r"^(\w+)\??\s*:", line)
        if m:
            camel_keys.add(m.group(1))

    missing = [k for k in camel_keys if f"settings.{k}" not in cloud_body]
    assert not missing, (
        f"{len(missing)} AppSettings key(s) not referenced in settingsToCloud():\n  "
        + "\n  ".join(sorted(missing))
    )


def test_settings_ts_exports_critical_functions() -> None:
    """
    settings.ts exports the functions that replaced direct localStorage access.

    - saveSetting(): used by AppSidebar (and others) for single-key writes
      that propagate through the engine + cloud sync pipeline.
    - broadcastSettingsChanged(): fires the matrx-settings-changed CustomEvent
      so reactive hooks (useNotifications, etc.) pick up changes.
    - syncAllSettings(): called by the background task on engine connect.
    - hydrateFromEngine(): called by the background task on first connect.
    """
    source = TS_SETTINGS_FILE.read_text(encoding="utf-8")

    required_exports = [
        "saveSetting",
        "broadcastSettingsChanged",
        "syncAllSettings",
        "hydrateFromEngine",
        "mergeCloudSettings",
        "settingsToCloud",
        "loadSettings",
        "saveSettings",
    ]
    missing = [fn for fn in required_exports if f"export " not in source or f"export async function {fn}" not in source and f"export function {fn}" not in source]
    # Fallback: check for arrow-style exports too
    truly_missing = [
        fn for fn in required_exports
        if not any(
            pattern in source
            for pattern in [
                f"export async function {fn}",
                f"export function {fn}",
                f"export const {fn}",
            ]
        )
    ]
    assert not truly_missing, (
        f"settings.ts is missing critical function exports: {truly_missing}\n"
        "These are used by AppSidebar, background tasks, and reactive hooks."
    )


def test_sync_result_type_exported() -> None:
    """settings.ts exports the SyncResult interface (used by callers of syncAllSettings)."""
    source = TS_SETTINGS_FILE.read_text(encoding="utf-8")
    assert "export interface SyncResult" in source, (
        "settings.ts is missing 'export interface SyncResult'. "
        "syncAllSettings() now returns a structured SyncResult instead of void."
    )
