from __future__ import annotations

import asyncio
import re
import threading
from collections import deque
from datetime import datetime
from typing import Any

from loguru import logger

from server.events import EventBroker


_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(api[_ -]?key|password|cookie|authorization|token)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"),
    re.compile(r"dpapi:v1:[A-Za-z0-9_=-]+"),
)


def redact(value: Any) -> str:
    text = str(value)
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.pattern.lower().startswith("(?i)bearer"):
            text = pattern.sub("Bearer [REDACTED]", text)
        elif pattern.pattern.startswith("dpapi"):
            text = pattern.sub("[REDACTED]", text)
        else:
            text = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text


class LogBuffer:
    """Thread-safe, redacted log buffer exposed to authenticated clients."""

    def __init__(self, broker: EventBroker, maxlen: int = 10_000) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.RLock()
        self._broker = broker
        self._handler_id: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def install(self) -> None:
        if self._handler_id is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._handler_id = logger.add(self._sink, level="DEBUG", catch=True)

    def uninstall(self) -> None:
        if self._handler_id is not None:
            logger.remove(self._handler_id)
            self._handler_id = None

    def _sink(self, message) -> None:
        record = message.record
        extra = record.get("extra") or {}
        item = {
            "timestamp": record["time"].astimezone().isoformat(),
            "level": record["level"].name,
            "module": str(extra.get("module") or record["name"]),
            "message": redact(record["message"]),
        }
        with self._lock:
            self._items.append(item)
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    self._broker.publish("log.appended", item)
                )
            )

    def query(
        self,
        *,
        level: str | None = None,
        module: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._items)
        if level:
            items = [item for item in items if item["level"] == level.upper()]
        if module:
            needle = module.casefold()
            items = [item for item in items if needle in item["module"].casefold()]
        if search:
            needle = search.casefold()
            items = [
                item for item in items
                if needle in item["message"].casefold()
                or needle in item["module"].casefold()
            ]
        return items

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


__all__ = ["LogBuffer", "redact"]

