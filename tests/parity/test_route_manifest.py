"""
Route manifest parity test.

Verifies that:
1. Every navigation item in AppSidebar.tsx points to a route that exists
   in App.tsx's page list.
2. Every route in App.tsx has a corresponding page file in desktop/src/pages/.

Why this matters:
  - AI agents frequently add new pages or reorganize navigation.
  - A missing import or wrong route path causes a blank page or 404 in the app.
  - This test catches the "nav item points to /foo but the page is registered
    at /bar" class of bug without running the app.

This test runs without the engine (pure file parsing).
"""

from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent.parent
SIDEBAR_FILE = (
    PROJECT_ROOT / "desktop" / "src" / "components" / "layout" / "AppSidebar.tsx"
)
APP_FILE = PROJECT_ROOT / "desktop" / "src" / "App.tsx"
PAGES_DIR = PROJECT_ROOT / "desktop" / "src" / "pages"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_sidebar_routes() -> list[str]:
    """
    Extract route paths from navItems in AppSidebar.tsx.

    Looks for entries like:
        { to: "/dashboard", icon: ..., label: "..." },
    """
    source = SIDEBAR_FILE.read_text(encoding="utf-8")

    # Find the navItems array block
    match = re.search(
        r"(?:const|let|var)\s+navItems\s*=\s*\[(.+?)\];",
        source,
        re.DOTALL,
    )
    assert match, f"Could not find navItems array in {SIDEBAR_FILE}"

    block = match.group(1)
    routes = re.findall(r'to:\s*"(/[^"]*)"', block)
    return routes


def parse_app_registered_paths() -> list[str]:
    """
    Extract route paths registered in App.tsx appPages array.

    Looks for entries like:
        { path: "/configurations", element: ... }
    """
    source = APP_FILE.read_text(encoding="utf-8")
    paths = re.findall(r'path:\s*"(/[^"]*)"', source)
    return paths


def page_file_for_route(route: str) -> list[Path]:
    """
    Return candidate page file paths for a given route.

    Route "/configurations" → Configurations.tsx
    Route "/local-models" → LocalModels.tsx
    Route "/notes" → Documents.tsx (special case, alias)
    """
    # Normalize: strip leading slash, split on /
    parts = route.lstrip("/").split("/")
    base = parts[0] if parts else ""

    # Convert kebab-case to PascalCase
    pascal = "".join(word.capitalize() for word in base.replace("-", "_").split("_"))

    candidates = [
        PAGES_DIR / f"{pascal}.tsx",
        PAGES_DIR / f"{pascal}.ts",
    ]

    # Special alias: /notes → Documents.tsx
    if base == "notes":
        candidates.append(PAGES_DIR / "Documents.tsx")
    # /browser → BrowserLab.tsx
    if base == "browser":
        candidates.append(PAGES_DIR / "BrowserLab.tsx")
    # /tts → TextToSpeech.tsx
    if base == "tts":
        candidates.append(PAGES_DIR / "TextToSpeech.tsx")
    # /aimatrx → AiMatrx.tsx (intercaps the single-word route can't infer)
    if base == "aimatrx":
        candidates.append(PAGES_DIR / "AiMatrx.tsx")

    return candidates


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sidebar_file_exists() -> None:
    """AppSidebar.tsx exists."""
    assert SIDEBAR_FILE.exists(), f"AppSidebar.tsx not found at {SIDEBAR_FILE}"


def test_app_file_exists() -> None:
    """App.tsx exists."""
    assert APP_FILE.exists(), f"App.tsx not found at {APP_FILE}"


def test_pages_dir_exists() -> None:
    """desktop/src/pages/ directory exists."""
    assert PAGES_DIR.exists(), f"pages/ dir not found at {PAGES_DIR}"


def test_sidebar_has_nav_items() -> None:
    """AppSidebar.tsx defines at least 10 navigation items."""
    routes = parse_sidebar_routes()
    assert len(routes) >= 10, (
        f"Only found {len(routes)} nav items in AppSidebar. Parser may be broken. "
        f"Routes: {routes}"
    )


def test_each_sidebar_route_has_page_file() -> None:
    """Every sidebar nav item has a corresponding page file in pages/."""
    routes = parse_sidebar_routes()
    missing: list[str] = []

    for route in routes:
        # Skip external or dynamic routes
        if route in ("/", "/auth/callback", "/overlay"):
            continue
        candidates = page_file_for_route(route)
        if not any(c.exists() for c in candidates):
            missing.append(
                f"  {route!r} → none of {[c.name for c in candidates]} found in pages/"
            )

    assert not missing, (
        f"{len(missing)} sidebar route(s) have no matching page file:\n"
        + "\n".join(missing)
        + "\n\nEither the page file is missing or the route path doesn't match the file name."
    )


def test_each_sidebar_route_is_registered_in_app() -> None:
    """Every sidebar nav item has a corresponding path registered in App.tsx."""
    sidebar_routes = set(parse_sidebar_routes())
    app_paths = set(parse_app_registered_paths())

    # "/" is always registered as the catch-all
    sidebar_routes.discard("/")

    unregistered = []
    for route in sorted(sidebar_routes):
        # Check exact match or prefix match (e.g. /browser matches /browser/tauri)
        is_registered = any(
            p == route or p.startswith(route + "/") or route.startswith(p.rstrip("*"))
            for p in app_paths
        )
        if not is_registered:
            unregistered.append(route)

    assert not unregistered, (
        f"{len(unregistered)} sidebar route(s) not registered in App.tsx appPages:\n  "
        + "\n  ".join(unregistered)
        + "\n\nAdd the missing route to the appPages array in App.tsx."
    )


def test_page_files_are_exported() -> None:
    """Spot-check that key page files exist and export a component."""
    required_pages = [
        ("Dashboard.tsx", "Dashboard"),
        ("Chat.tsx", "Chat"),
        ("Documents.tsx", "Documents"),
        ("Scraping.tsx", "Scraping"),
        ("Tools.tsx", "Tools"),
        ("Settings.tsx", "Settings"),
        ("Configurations.tsx", "Configurations"),
        ("Voice.tsx", "Voice"),
        ("LocalModels.tsx", "LocalModels"),
        ("SystemPrompts.tsx", "SystemPrompts"),
        ("Activity.tsx", "Activity"),
        ("Devices.tsx", "Devices"),
    ]
    missing: list[str] = []
    for filename, export_name in required_pages:
        path = PAGES_DIR / filename
        if not path.exists():
            missing.append(f"  {filename} (missing file)")
            continue
        source = path.read_text(encoding="utf-8")
        if f"export function {export_name}" not in source and f"export const {export_name}" not in source:
            missing.append(
                f"  {filename} (exists but doesn't export '{export_name}')"
            )

    assert not missing, (
        f"{len(missing)} page file(s) missing or not exporting expected component:\n"
        + "\n".join(missing)
    )


def test_wakeword_context_exists() -> None:
    """WakeWordContext.tsx exists (required by App.tsx wrapping WakeWordProvider)."""
    ctx_file = (
        PROJECT_ROOT / "desktop" / "src" / "contexts" / "WakeWordContext.tsx"
    )
    assert ctx_file.exists(), (
        "WakeWordContext.tsx not found. App.tsx wraps the entire app in "
        "<WakeWordProvider> which comes from this file."
    )
    source = ctx_file.read_text(encoding="utf-8")
    assert "WakeWordProvider" in source, (
        "WakeWordContext.tsx exists but does not export WakeWordProvider."
    )


def test_no_unrouted_page_file_is_sidebar_nav() -> None:
    """
    Guard: no page file exists that is referenced in the sidebar but not in App.tsx.

    This is a stricter version of test_each_sidebar_route_is_registered_in_app.
    It specifically ensures that 'orphan' pages (like WakeWord.tsx, which exists
    but is not in the sidebar or App.tsx routes) don't accidentally sneak into
    the sidebar without a corresponding route registration.
    """
    sidebar_routes = set(parse_sidebar_routes())
    app_paths = set(parse_app_registered_paths())

    # Every sidebar route must be in App.tsx
    sidebar_routes.discard("/")
    unregistered = [
        r for r in sorted(sidebar_routes)
        if not any(
            p == r or p.startswith(r + "/") or r.startswith(p.rstrip("*"))
            for p in app_paths
        )
    ]
    assert not unregistered, (
        f"Sidebar nav item(s) point to routes not registered in App.tsx:\n  "
        + "\n  ".join(unregistered)
        + "\n\nEither register the route in App.tsx or remove the sidebar entry."
    )


def test_background_tasks_dir_exists() -> None:
    """background-tasks/ module directory exists (required for engine startup)."""
    bg_dir = PROJECT_ROOT / "desktop" / "src" / "lib" / "background-tasks"
    assert bg_dir.exists(), (
        f"background-tasks/ directory not found at {bg_dir}. "
        "This module was introduced to replace direct syncAllSettings() calls "
        "in use-engine.ts. If it was deleted or moved, engine startup tasks "
        "(settings sync, token push, cloud configure) will fail silently."
    )
    assert (bg_dir / "index.ts").exists(), (
        "background-tasks/index.ts not found. The orchestrator entry point is missing."
    )
    assert (bg_dir / "orchestrator.ts").exists(), (
        "background-tasks/orchestrator.ts not found. The task runner is missing."
    )
