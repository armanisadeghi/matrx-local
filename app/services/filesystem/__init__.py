"""Progressive, cross-platform local filesystem discovery.

The service is intentionally metadata-first.  Directory browsing works
directly from the disk before the index is warm; background indexing only
makes discovery faster and broader over time.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.filesystem.service import FilesystemService


def get_filesystem_service() -> "FilesystemService":
    from app.services.filesystem.service import get_filesystem_service as _get

    return _get()

__all__ = ["FilesystemService", "get_filesystem_service"]
