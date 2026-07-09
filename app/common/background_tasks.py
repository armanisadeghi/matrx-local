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


def fire_and_forget(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str | None = None,
    quiet: bool = False,
) -> None:
    """Schedule ``coro`` as a retained background task; log (never raise) failures.

    Failures are LOUD by default (WARNING with a traceback) — a silently
    swallowed background exception is exactly the class of defect this helper
    exists to make visible. Genuinely best-effort callers may pass
    ``quiet=True`` to drop the failure log to DEBUG.
    """

    async def _safe() -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            if quiet:
                logger.debug(
                    "Background task %s failed (quiet)", name or "?", exc_info=True
                )
            else:
                logger.warning(
                    "Background task %s failed", name or "?", exc_info=True
                )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(_safe(), name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
