"""
Import smoke tests — verify critical modules can be imported without errors.

These tests catch missing imports (like `sys`, `os`, etc.) that would crash
the engine at startup before any endpoint becomes reachable. They run
without starting the engine, so they're fast and suitable for CI pre-checks.

Cross-platform: runs identically on macOS, Windows, and Linux.
"""

from __future__ import annotations

import importlib
import sys

import pytest


CRITICAL_MODULES = [
    "app.main",
    "app.config",
    "app.api.routes",
    "app.api.tool_routes",
    "app.api.settings_routes",
    "app.api.auth",
    "app.api.token_routes",
    "app.tools.dispatcher",
    # Media generation — must import with NO optional packages installed
    # (all torch/diffusers imports are lazy behind the service boundary).
    "app.api.image_gen_routes",
    "app.api.video_gen_routes",
    "app.services.media_gen.hardware",
    "app.services.media_gen.paths",
    "app.services.image_gen.service",
    "app.services.video_gen.service",
    "app.services.video_gen.jobs",
    "app.services.downloads.manager",
]


@pytest.mark.parametrize("module_path", CRITICAL_MODULES)
def test_critical_module_imports(module_path: str) -> None:
    """Each critical module can be imported without NameError or ImportError.

    The original module object is restored into sys.modules afterwards:
    leaving the freshly re-imported copy in place breaks module identity for
    every later test that monkeypatches attributes on these modules (the
    FastAPI app's handlers stay bound to the ORIGINAL module globals).
    """
    original = sys.modules.pop(module_path, None)
    try:
        importlib.import_module(module_path)
    except Exception as exc:
        pytest.fail(
            f"Failed to import {module_path}: {type(exc).__name__}: {exc}\n"
            "This would crash the engine at startup."
        )
    finally:
        if original is not None:
            sys.modules[module_path] = original
            # Re-importing also rebinds the submodule attribute on the parent
            # package object; `from pkg import mod` resolves through that
            # attribute, so it must be restored too.
            parent_name, _, child = module_path.rpartition(".")
            parent = sys.modules.get(parent_name) if parent_name else None
            if parent is not None:
                setattr(parent, child, original)


def test_main_app_object_exists() -> None:
    """app.main exposes a FastAPI `app` object (the ASGI entry point)."""
    from app.main import app  # noqa: F401
    assert app is not None, "app.main.app is None"
    assert hasattr(app, "router"), "app.main.app has no router attribute"


def test_main_lifespan_defined() -> None:
    """The lifespan async context manager is defined in app.main."""
    from app.main import lifespan  # noqa: F401
    assert callable(lifespan), "app.main.lifespan is not callable"
