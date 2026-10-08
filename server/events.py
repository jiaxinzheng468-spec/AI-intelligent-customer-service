from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from typing import Any
from uuid import uuid4


CRITICAL_EVENT_TYPES = {
    "runtime.status_changed",
    "job.finished",
    "system.shutdown_requested",
}


class EventQueue:
    """Prioritize lifecycle events while bounding noisy progress and logs."""

    def __init__(self, low_priority_size: int) -> None:
        self._critical: deque[dict[str, Any]] = deque()
        self._low_priority: deque[dict[str, Any]] = deque(
            maxlen=max(1, low_priority_size)
        )
        self._available = asyncio.Event()

    def put_nowait(self, event: dict[str, Any]) -> None:
        target = (
            self._critical
            if event["type"] in CRITICAL_EVENT_TYPES
            else self._low_priority
        )
        target.append(event)
        self._available.set()

    async def get(self) -> dict[str, Any]:
        while True:
            if self._critical:
                return self._critical.popleft()
            if self._low_priority:
                return self._low_priority.popleft()
            self._available.clear()
            await self._available.wait()


class EventBroker:
    """Bounded in-process fan-out for browser status updates."""

    def __init__(self, queue_size: int = 500) -> None:
        self.queue_size = queue_size
        self._subscribers: set[EventQueue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> EventQueue:
        queue = EventQueue(self.queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: EventQueue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        event = {
            "event_id": str(uuid4()),
            "type": event_type,
            "occurred_at": datetime.now().astimezone().isoformat(),
            "data": data,
        }
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            queue.put_nowait(event)


__all__ = ["EventBroker", "EventQueue"]
