"""Records mirror sync — the custom record store on the desktop.

ONE custom Table's records live in the local SQLite mirror
(``custom_record_mirror``) and sync both ways through the store's own doors:
``custom.read_records`` down, ``custom.anon_capture`` (client-minted key,
idempotent) and ``custom.record_update`` up.  The whole path is gated on the
campaign switch — when the store is off for the organization the feature is
absent and says so with a remedy.

See app/services/records_sync/FEATURE.md.
"""

from app.services.records_sync.engine import (
    RecordsMirrorUnavailable,
    RecordsSyncEngine,
    get_records_sync_engine,
)

__all__ = ["RecordsMirrorUnavailable", "RecordsSyncEngine", "get_records_sync_engine"]
