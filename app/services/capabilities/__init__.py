"""Capabilities package — on-demand optional feature installs for end users."""

from app.services.capabilities.installer import (
    inject_all_capability_paths,
    inject_capability_path,
    is_capability_installed,
)

__all__ = [
    "inject_all_capability_paths",
    "inject_capability_path",
    "is_capability_installed",
]
