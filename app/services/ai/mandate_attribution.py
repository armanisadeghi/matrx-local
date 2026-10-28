"""Mandate attribution on this device (Arman, 2026-10-28).

Every mandate run, and its cost, is recorded against its mandate wherever it runs —
including here. The stamp is the SAME shape the server writes
(``matrx_ai.mandates.stamp_mandate_attribution`` / ``run_door.mandate_run_stamp``):

* ``chat.conversation.source_feature`` and ``chat.user_request.source_feature`` =
  ``mandate:<key>`` — what the usage ledger groups by;
* ``chat.user_request.metadata.mandate_key`` — the structured key;
* ``chat.conversation.metadata.mandate_run`` — ``{mandate_key, holder_type, door}``.

Rows reach the server through the ordinary chat_sync push (``chat_sync/engine.py``
allowlists ``source_feature`` and ``metadata`` for both tables).

Two doors write the stamp: the engine path (``conversation_handler`` reads
``ctx.metadata["mandate_key"]``) and the local-model path, where the desktop calls
llama-server directly and reports the run afterwards through
``POST /local-mandates/{key}/runs`` -> :func:`record_local_model_run`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.common.system_logger import get_logger

logger = get_logger()

MANDATE_KEY_METADATA = "mandate_key"
#: The door a device-made run came through (the server's own value is CLIENT_DOOR).
DEVICE_DOOR = "matrx-local"


def mandate_feature(mandate_key: str) -> str:
    return f"mandate:{mandate_key}"


def mandate_run_stamp(mandate_key: str, holder_type: str = "agent") -> dict[str, Any]:
    return {"mandate_key": mandate_key, "holder_type": holder_type, "door": DEVICE_DOOR}


def mandate_key_from_ctx(ctx: Any) -> str | None:
    if ctx is None:
        return None
    key = (getattr(ctx, "metadata", None) or {}).get(MANDATE_KEY_METADATA)
    return key if isinstance(key, str) and key else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


async def record_local_model_run(
    *,
    mandate_key: str,
    user_id: str,
    organization_id: str | None,
    usage: dict[str, Any],
    duration_ms: int | None = None,
    failed: bool = False,
    holder_type: str = "agent",
) -> dict[str, str]:
    """One conversation + one user_request for a run llama-server answered, enqueued for chat_sync.

    ``total_cost`` is 0 for a local model (it costs the person nothing but time) — written
    explicitly so the run is a priced run of 0, not an unpriced one.
    """
    from app.services.local_db.database import get_db  # noqa: PLC0415
    from app.services.local_db.outbox import enqueue_change  # noqa: PLC0415

    db = get_db()
    now = _now()
    conversation_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    prompt = _int(usage.get("prompt_tokens"))
    completion = _int(usage.get("completion_tokens"))
    total = _int(usage.get("total_tokens")) or prompt + completion
    feature = mandate_feature(mandate_key)
    status = "failed" if failed else "completed"

    await db.execute(
        """INSERT INTO chat.conversation
           (id, title, config, status, variables, overrides, organization_id,
            source_app, source_feature, created_by, created_at, updated_at,
            message_count, is_favorite, is_ephemeral, conversation_type, version,
            metadata, cache_state, exclude_from_kg, last_request_id, last_request_status)
           VALUES (?, ?, '{}', 'active', '{}', '{}', ?, 'matrx-local', ?, ?, ?, ?,
                   0, 0, 0, 'standard', 1, ?, '{}', 0, ?, ?)""",
        (
            conversation_id,
            mandate_key,
            organization_id,
            feature,
            user_id,
            now,
            now,
            json.dumps({"mandate_run": mandate_run_stamp(mandate_key, holder_type)}),
            request_id,
            status,
        ),
    )
    await db.execute(
        """INSERT INTO chat.user_request
           (id, status, source_app, source_feature, metadata, created_by,
            organization_id, created_at, completed_at, updated_at, last_activity_at,
            total_input_tokens, total_output_tokens, total_cached_tokens, total_tokens,
            total_cost, total_duration_ms, iterations, total_tool_calls, version)
           VALUES (?, ?, 'matrx-local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, 1, 0, 1)""",
        (
            request_id,
            status,
            feature,
            json.dumps(
                {
                    "conversation_id": conversation_id,
                    MANDATE_KEY_METADATA: mandate_key,
                    "local_model": True,
                }
            ),
            user_id,
            organization_id,
            now,
            now,
            now,
            now,
            prompt,
            completion,
            total,
            duration_ms,
        ),
    )
    await enqueue_change("chat", "conversation", conversation_id, db, commit=False)
    await enqueue_change("chat", "user_request", request_id, db, commit=False)
    await db.commit()
    logger.info(
        "[mandate_attribution] recorded local-model run of %s (%d tokens)", mandate_key, total
    )
    return {"conversation_id": conversation_id, "user_request_id": request_id}
