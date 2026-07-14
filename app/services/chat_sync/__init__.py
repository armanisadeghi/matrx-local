"""Chat-system sync — bidirectional mirror sync for the chat.* schema.

The local SQLite mirror (chat.conversation, chat.message, …) syncs with the
canonical cloud tables over PostgREST with the user's JWT:

- outbound: the sync_queue outbox (written by every local-origin mutation)
  drains as batched upserts, parents before children
- inbound: per-table incremental keyset pulls on (updated_at, id) with
  checkpoints in sync_meta

See docs/SYNC_CONTRACT.md and app/services/chat_sync/engine.py.
"""

from app.services.chat_sync.engine import get_chat_sync_engine

__all__ = ["get_chat_sync_engine"]
