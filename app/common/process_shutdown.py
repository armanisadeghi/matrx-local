"""Process-wide shutdown signal for long-lived application work.

Uvicorn's ``should_exit`` flag is private to the server loop. Long-lived SSE
generators also need an application-owned signal so they can stop accepting
work and unregister before Uvicorn's graceful connection-drain deadline.
``threading.Event`` is intentional: shutdown can originate in the main signal
handler, the parent watchdog, the tray thread, or the Uvicorn server thread.
"""

from __future__ import annotations

import threading


process_shutdown_event = threading.Event()


def request_process_shutdown() -> None:
    """Announce that this process is beginning graceful shutdown."""
    process_shutdown_event.set()


def process_shutdown_requested() -> bool:
    """Return whether graceful process shutdown has begun."""
    return process_shutdown_event.is_set()

