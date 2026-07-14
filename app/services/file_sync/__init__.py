"""User file sync — the desktop replica of the matrx-files cloud tree.

Two user-choosable modes over one engine (docs/handoffs/file-sync-system.md):
full (bytes mirrored, bidirectional, offline-capable) and pointers (metadata
tree + placeholders locally, bytes hydrated on demand).
"""

from app.services.file_sync.engine import FileSyncEngine, get_file_sync_engine

__all__ = ["FileSyncEngine", "get_file_sync_engine"]
