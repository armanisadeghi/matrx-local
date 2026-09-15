"""Follow the daemon's session lifecycle without owning a credential."""

from __future__ import annotations

import asyncio
from app.common.system_logger import get_logger

logger = get_logger()


class DaemonSessionReconciler:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._adoption: asyncio.Task[None] | None = None
        self._user_id: str | None = None
        self._revision = 0
        self._completed = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(), name="daemon-session-reconciler"
            )

    async def stop(self) -> None:
        for task in (self._task, self._adoption):
            if task:
                task.cancel()
        for task in (self._task, self._adoption):
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        from app.services.ai.key_manager import clear_vault_keys
        from app.api.extension_broadcast import disconnect_broadcast

        clear_vault_keys()
        if self._user_id:
            await disconnect_broadcast(self._user_id)
        self._user_id = None
        self._completed = False
        self._task = self._adoption = None

    async def _run(self) -> None:
        from app.services.sync_client import get_sync_client

        while True:
            try:
                await self.reconcile()
                async for event in get_sync_client().session_events():
                    await self.reconcile(rotated=bool(event.get("rotated")))
            except Exception:
                logger.warning(
                    "Session listener unavailable; reconnecting", exc_info=True
                )
            await asyncio.sleep(1)

    async def reconcile(self, *, rotated: bool = False) -> None:
        from app.services.sync_client import get_sync_client

        async with self._lock:
            snapshot = await get_sync_client().session()
            user_id = snapshot.user_id if snapshot.signed_in else None
            in_progress = self._adoption is not None and not self._adoption.done()
            if (
                user_id == self._user_id
                and not rotated
                and (self._completed or in_progress or not user_id)
            ):
                return
            old = self._user_id
            self._revision += 1
            self._completed = False
            # Clear synchronously before awaiting cancellation or network cleanup.
            if old != user_id:
                from app.services.ai.key_manager import clear_vault_keys

                clear_vault_keys()
            if in_progress:
                self._adoption.cancel()
                try:
                    await self._adoption
                except asyncio.CancelledError:
                    pass
            if old:
                from app.api.extension_broadcast import disconnect_broadcast

                await disconnect_broadcast(old)
            self._user_id = user_id
            if user_id:
                self._adoption = asyncio.create_task(
                    self._adopt(user_id, self._revision), name="daemon-session-adoption"
                )

    async def _adopt(self, user_id: str, revision: int) -> None:
        from app.services.sync_client import get_sync_client

        delay = 1.0
        while revision == self._revision:
            if await get_sync_client().user_id() != user_id:
                return
            if await self._adopt_once(user_id, revision):
                self._completed = True
                logger.info("Session services connected for the current daemon account")
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _adopt_once(self, user_id: str, revision: int) -> bool:
        from app.services.sync_client import get_sync_client

        async def current() -> bool:
            return (
                revision == self._revision
                and await get_sync_client().user_id() == user_id
            )

        try:
            from app.services.session_freshness import session_restored
            from app.services.scraper.scrape_store import sync_after_sign_in
            from app.services.ai.key_manager import refresh_vault_keys
            from app.services.ai.engine import refresh_server_tool_definitions
            from app.config import CLOUD_PARTICIPATION_ENABLED

            if not await current():
                return
            session_restored()
            await sync_after_sign_in()
            if not await current():
                return
            await refresh_vault_keys()
            if not await current():
                return
            await refresh_server_tool_definitions()
            if not await current():
                return
            from app.services.local_db.sync_engine import get_sync_engine

            await get_sync_engine().sync_agents()
            if not await current():
                return
            if CLOUD_PARTICIPATION_ENABLED:
                from app.api.extension_broadcast import (
                    connect_broadcast,
                    disconnect_broadcast,
                )

                await connect_broadcast(user_id)
                if not await current():
                    await disconnect_broadcast(user_id)
                    return
            return True
        except Exception:
            logger.warning(
                "Session services could not reconnect; will retry", exc_info=True
            )


_RECONCILER = DaemonSessionReconciler()


def get_daemon_session_reconciler() -> DaemonSessionReconciler:
    return _RECONCILER
