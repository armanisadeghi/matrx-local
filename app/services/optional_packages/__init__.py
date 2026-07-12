"""Shared frozen-safe optional package installation.

Used by image-gen and Settings → Capabilities (Whisper, etc.) to install
heavy Python deps into a user-writable ``--target`` directory that the
frozen sidecar can import via ``sys.path`` injection.

Never run ``sys.executable -m pip`` when frozen — that re-executes the
engine binary.
"""

from app.services.optional_packages.core import (
    InstallProgress,
    find_python,
    packages_dir,
    run_pip_streaming,
)

__all__ = [
    "InstallProgress",
    "find_python",
    "packages_dir",
    "run_pip_streaming",
]
