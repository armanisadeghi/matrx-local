"""Async ownership for small, read-only Notes filesystem operations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


async def offload_read_only(operation: Callable[[], T]) -> T:
    """Run one read-only filesystem operation without starving the event loop.

    ``asyncio.to_thread`` copies the caller's ``ContextVar`` context into its
    worker, which keeps a sync operation tied to the account that started it.
    A thread cannot be cancelled once it has begun I/O.  If the owner is
    cancelled, keep ownership until that worker completes, including if the
    owner receives repeated cancellation, then propagate cancellation.
    """
    worker = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                # A second shutdown cancellation must not detach the running
                # thread from this task.  Continue draining its read-only work.
                continue
            except BaseException:
                # The caller was already cancelled.  The worker still has to
                # finish, but its read failure must not replace that original
                # cancellation and abort a wider shutdown teardown.
                break
        try:
            worker.result()
        except BaseException:
            # Consume a drained worker failure; normal, non-cancelled callers
            # still receive their operation's exception from the first await.
            pass
        raise
