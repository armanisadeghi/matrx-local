"""Retained fire-and-forget task helper.

asyncio.create_task results must be retained: the event loop keeps only a
weak reference, so an un-referenced background task can be garbage-collected
mid-execution. Every "kick off a sync and move on" call site should go
through ``fire_and_forget`` instead of a bare create_task.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def fire_and_forget(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> None:
    """Schedule ``coro`` as a retained background task; log (never raise) failures."""

    async def _safe() -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Background task %s failed (non-critical): %s", name or "?", exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(_safe(), name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
