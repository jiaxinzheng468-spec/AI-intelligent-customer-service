from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from server.errors import APIError
from server.events import EventBroker
from utils.logger_loguru import get_logger


ProgressReporter = Callable[[dict[str, Any]], None]
JobRunner = Callable[[ProgressReporter], Awaitable[Any]]


@dataclass
class JobRecord:
    id: str
    kind: str
    resource_key: str
    status: str = "queued"
    progress: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    cancellation_requested: bool = False

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("resource_key", None)
        return value


class JobManager:
    """Owns long-running login and knowledge synchronization jobs."""

    def __init__(self, broker: EventBroker) -> None:
        self._broker = broker
        self._jobs: dict[str, JobRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._resources: dict[str, str] = {}
        self._cancel_callbacks: dict[str, Callable[[], None]] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphores = {"product_sync": asyncio.Semaphore(2)}
        self.logger = get_logger("JobManager")

    async def submit(
        self,
        *,
        kind: str,
        resource_key: str,
        runner: JobRunner,
        cancel_callback: Callable[[], None] | None = None,
    ) -> JobRecord:
        async with self._lock:
            existing_id = self._resources.get(resource_key)
            if existing_id:
                existing = self._jobs.get(existing_id)
                if existing and existing.status in {"queued", "running"}:
                    raise APIError(
                        "JOB_ALREADY_RUNNING", "同类任务已在运行", 409,
                        {"job_id": existing.id},
                    )
            record = JobRecord(
                id=str(uuid4()), kind=kind, resource_key=resource_key
            )
            self._jobs[record.id] = record
            self._resources[resource_key] = record.id
            if cancel_callback:
                self._cancel_callbacks[record.id] = cancel_callback
            self._loop = asyncio.get_running_loop()
            self._tasks[record.id] = asyncio.create_task(
                self._run(record, runner), name=f"job:{kind}:{record.id}"
            )
        await self._broker.publish("job.progress_changed", record.public())
        return record

    async def _run(self, record: JobRecord, runner: JobRunner) -> None:
        semaphore = self._semaphores.get(record.kind)
        acquired = False
        try:
            if semaphore:
                await semaphore.acquire()
                acquired = True
            if record.cancellation_requested:
                record.status = "cancelled"
                return

            record.status = "running"
            record.started_at = datetime.now().astimezone().isoformat()
            await self._broker.publish("job.progress_changed", record.public())

            def report(progress: dict[str, Any]) -> None:
                loop = self._loop
                if not loop or loop.is_closed():
                    return
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        self._update_progress(record.id, progress)
                    )
                )

            result = await runner(report)
            if record.cancellation_requested:
                record.status = "cancelled"
            else:
                record.status = "succeeded"
            record.result = result
        except asyncio.CancelledError:
            record.status = "cancelled"
        except Exception as exc:
            record.status = "failed"
            record.error = "任务执行失败，请查看服务端日志"
            self.logger.error(
                f"后台任务失败: kind={record.kind}, error_type={type(exc).__name__}"
            )
        finally:
            if acquired and semaphore:
                semaphore.release()
            record.finished_at = datetime.now().astimezone().isoformat()
            async with self._lock:
                if self._resources.get(record.resource_key) == record.id:
                    self._resources.pop(record.resource_key, None)
                self._tasks.pop(record.id, None)
                self._cancel_callbacks.pop(record.id, None)
            await self._broker.publish("job.finished", record.public())
            self._prune()

    async def _update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        record = self._jobs.get(job_id)
        if not record or record.status not in {"queued", "running"}:
            return
        record.progress = dict(progress)
        await self._broker.publish("job.progress_changed", record.public())

    def get(self, job_id: str) -> JobRecord:
        record = self._jobs.get(job_id)
        if not record:
            raise APIError("JOB_NOT_FOUND", "任务不存在或已过期", 404)
        return record

    async def cancel(self, job_id: str) -> JobRecord:
        record = self.get(job_id)
        if record.status not in {"queued", "running"}:
            return record
        record.cancellation_requested = True
        await self._broker.publish("job.progress_changed", record.public())
        callback = self._cancel_callbacks.get(job_id)
        task = self._tasks.get(job_id)
        cancel_task = record.status == "queued" or callback is None
        if callback and not cancel_task:
            try:
                callback()
            except Exception as exc:
                self.logger.error(
                    f"任务取消回调失败: kind={record.kind}, "
                    f"error_type={type(exc).__name__}"
                )
                cancel_task = True
        if cancel_task:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            # A task cancelled before its coroutine starts never reaches
            # _run()'s finally block, so finalize it here.
            if record.status in {"queued", "running"}:
                record.status = "cancelled"
                record.finished_at = datetime.now().astimezone().isoformat()
                async with self._lock:
                    if self._resources.get(record.resource_key) == record.id:
                        self._resources.pop(record.resource_key, None)
                    self._tasks.pop(record.id, None)
                    self._cancel_callbacks.pop(record.id, None)
                await self._broker.publish("job.finished", record.public())
        return record

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for job_id, callback in list(self._cancel_callbacks.items()):
            try:
                callback()
            except Exception:
                pass
            record = self._jobs.get(job_id)
            if record:
                record.cancellation_requested = True
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _prune(self) -> None:
        terminal = [
            job for job in self._jobs.values()
            if job.status in {"succeeded", "failed", "cancelled"}
        ]
        if len(terminal) <= 500:
            return
        terminal.sort(key=lambda item: item.finished_at or item.created_at)
        for record in terminal[:-500]:
            self._jobs.pop(record.id, None)


__all__ = ["JobManager", "JobRecord"]
