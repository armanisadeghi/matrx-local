"""In-memory lifecycle authority for active user-remediation requirements."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from .models import ActionNeeded

SnapshotListener = Callable[[dict[str, Any]], Awaitable[None]]


def invocation_key(tool: str, tool_input: dict[str, Any]) -> str:
    return f"{tool}:{json.dumps(tool_input, sort_keys=True, separators=(',', ':'), default=str)}"


class ActionNeededRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ActionNeeded] = {}
        self._invocations: dict[str, str] = {}
        self._versions: dict[str, int] = {}
        self._listeners: set[SnapshotListener] = set()
        self._lock = asyncio.Lock()

    def subscribe(self, listener: SnapshotListener) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _snapshot_unlocked(self, source: str) -> dict[str, Any]:
        return {
            "type": "action_needed_snapshot",
            "source": source,
            "version": self._versions.get(source, 0),
            "items": [
                item.model_dump(mode="json", exclude_none=True)
                for item in self._items.values()
                if item.source == source
            ],
        }

    async def snapshots(self) -> list[dict[str, Any]]:
        async with self._lock:
            sources = set(self._versions) | {item.source for item in self._items.values()}
            return [self._snapshot_unlocked(source) for source in sorted(sources)]

    async def reconcile_invocation(
        self,
        tool: str,
        tool_input: dict[str, Any],
        item: ActionNeeded | None,
    ) -> None:
        key = invocation_key(tool, tool_input)
        changed_sources: set[str] = set()
        async with self._lock:
            previous = self._invocations.pop(key, None)
            if previous:
                prior_item = self._items.get(previous)
                if prior_item and previous not in self._invocations.values():
                    self._items.pop(previous, None)
                    changed_sources.add(prior_item.source)
            if item is not None:
                replaced = self._items.get(item.fingerprint)
                self._items[item.fingerprint] = item
                self._invocations[key] = item.fingerprint
                changed_sources.add(item.source)
                if replaced is not None and replaced.source != item.source:
                    changed_sources.add(replaced.source)
            for source in changed_sources:
                self._versions[source] = self._versions.get(source, 0) + 1
            snapshots = [self._snapshot_unlocked(source) for source in changed_sources]
            listeners = tuple(self._listeners)
        for snapshot in snapshots:
            await asyncio.gather(
                *(listener(snapshot) for listener in listeners),
                return_exceptions=True,
            )

    async def reset(self) -> None:
        async with self._lock:
            sources = set(self._versions) | {item.source for item in self._items.values()}
            self._items.clear()
            self._invocations.clear()
            for source in sources:
                self._versions[source] = self._versions.get(source, 0) + 1


_registry = ActionNeededRegistry()


def get_action_needed_registry() -> ActionNeededRegistry:
    return _registry
