from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from Channel.pinduoduo.pdd_channel import PDDChannel
from server.errors import APIError
from server.events import EventBroker
from service.account_service import account_service
from utils.logger_loguru import get_logger


@dataclass
class RuntimeRecord:
    account_id: int
    account_key: str
    state: str = "disconnected"
    channel: Any = None
    started_at: str | None = None
    last_error: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class AccountRuntimeManager:
    """Qt-free owner for account PDDChannel lifecycles."""

    def __init__(
        self,
        broker: EventBroker,
        channel_factory: Callable[[], Any] = PDDChannel,
    ) -> None:
        self._broker = broker
        self._channel_factory = channel_factory
        self._records: dict[int, RuntimeRecord] = {}
        self.logger = get_logger("AccountRuntimeManager")

    @staticmethod
    def _key(account: dict[str, Any]) -> str:
        return "_".join(
            str(account.get(key, ""))
            for key in ("channel_name", "shop_id", "user_id")
        )

    def _record_for(self, account: dict[str, Any]) -> RuntimeRecord:
        account_id = int(account["id"])
        record = self._records.get(account_id)
        if not record:
            record = RuntimeRecord(account_id=account_id, account_key=self._key(account))
            self._records[account_id] = record
        return record

    async def _publish(self, record: RuntimeRecord) -> None:
        await self._broker.publish(
            "runtime.status_changed",
            {
                "account_id": record.account_id,
                "state": record.state,
                "started_at": record.started_at,
                "last_error": record.last_error,
            },
        )

    def _schedule_state(
        self,
        record: RuntimeRecord,
        channel: Any,
        loop: asyncio.AbstractEventLoop,
        state: str,
        error: str | None = None,
    ) -> None:
        if loop.is_closed():
            return

        def apply_state() -> None:
            if record.channel is not channel or record.state in {"stopping", "disconnected"}:
                return
            record.state = state
            record.last_error = error
            loop.create_task(self._publish(record))

        loop.call_soon_threadsafe(apply_state)

    async def start(self, account_id: int) -> dict[str, Any]:
        account = await asyncio.to_thread(account_service.get_account_by_id, account_id)
        if not account:
            raise APIError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
        if account.get("status") != 1:
            raise APIError(
                "ACCOUNT_NOT_ONLINE", "账号必须先上线才能启动自动回复", 409
            )
        record = self._record_for(account)
        async with record.lock:
            if record.state in {"connecting", "connected", "reconnecting", "error"}:
                return self.snapshot_one(account, record)
            if record.state == "stopping":
                raise APIError("ACCOUNT_RUNTIME_BUSY", "账号正在停止，请稍后重试", 409)

            record.channel = self._channel_factory()
            channel = record.channel
            loop = asyncio.get_running_loop()
            record.state = "connecting"
            record.started_at = datetime.now().astimezone().isoformat()
            record.last_error = None
            await self._publish(record)

            def on_success() -> None:
                self._schedule_state(record, channel, loop, "connected")

            def on_failure(message: str) -> None:
                self._schedule_state(record, channel, loop, "error", str(message))

            try:
                await channel.start_account(
                    shop_id=account["shop_id"],
                    user_id=account["user_id"],
                    on_success=on_success,
                    on_failure=on_failure,
                )
            except Exception as exc:
                record.state = "error"
                record.last_error = "自动回复启动失败，请检查账号和网络"
                self.logger.error(
                    f"账号运行时启动失败: account_id={account_id}, "
                    f"error_type={type(exc).__name__}"
                )
                await self._publish(record)
                raise APIError(
                    "RUNTIME_START_FAILED", record.last_error, 502
                ) from exc
            return self.snapshot_one(account, record)

    async def stop(self, account_id: int) -> dict[str, Any]:
        account = await asyncio.to_thread(account_service.get_account_by_id, account_id)
        if not account:
            raise APIError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
        record = self._record_for(account)
        async with record.lock:
            if record.state == "disconnected" or not record.channel:
                record.state = "disconnected"
                return self.snapshot_one(account, record)
            if record.state == "stopping":
                return self.snapshot_one(account, record)
            record.state = "stopping"
            await self._publish(record)
            try:
                await asyncio.wait_for(
                    record.channel.stop_account(account["shop_id"], account["user_id"]),
                    timeout=10,
                )
            except asyncio.TimeoutError as exc:
                record.state = "error"
                record.last_error = "自动回复停止超时，请等待资源清理"
                await self._publish(record)
                raise APIError("RUNTIME_STOP_TIMEOUT", record.last_error, 504) from exc
            except Exception as exc:
                record.state = "error"
                record.last_error = "自动回复停止失败，请查看服务端日志"
                await self._publish(record)
                self.logger.error(
                    f"账号运行时停止失败: account_id={account_id}, "
                    f"error_type={type(exc).__name__}"
                )
                raise APIError("RUNTIME_STOP_FAILED", record.last_error, 500) from exc
            record.channel = None
            record.state = "disconnected"
            record.started_at = None
            record.last_error = None
            await self._publish(record)
            return self.snapshot_one(account, record)

    def is_active(self, account_id: int) -> bool:
        record = self._records.get(account_id)
        return bool(
            record and record.state in {
                "connecting", "connected", "reconnecting", "stopping", "error"
            } and record.channel is not None
        )

    def snapshot_one(
        self, account: dict[str, Any], record: RuntimeRecord | None = None
    ) -> dict[str, Any]:
        record = record or self._records.get(int(account["id"]))
        state = record.state if record else "disconnected"
        reconnect_count = 0
        connect_time = None
        if record and record.channel and state != "stopping":
            status = record.channel.status_manager.get_status(
                account["shop_id"], account["user_id"]
            )
            if status:
                state = status.state.value
                reconnect_count = status.reconnect_count
                connect_time = (
                    status.connect_time.astimezone().isoformat()
                    if status.connect_time else None
                )
        return {
            "account_id": int(account["id"]),
            "channel_name": account["channel_name"],
            "shop_id": account["shop_id"],
            "shop_name": account["shop_name"],
            "user_id": account["user_id"],
            "username": account["username"],
            "platform_status": account.get("status"),
            "state": state,
            "started_at": record.started_at if record else None,
            "connect_time": connect_time,
            "reconnect_count": reconnect_count,
            "last_error": record.last_error if record else None,
        }

    async def snapshots(self) -> list[dict[str, Any]]:
        accounts = await asyncio.to_thread(account_service.get_all_accounts_with_details)
        return [self.snapshot_one(account) for account in accounts]

    async def start_all(self) -> dict[str, Any]:
        accounts = await asyncio.to_thread(account_service.get_all_accounts_with_details)
        result = {"succeeded": [], "skipped": [], "failed": []}
        for account in accounts:
            account_id = int(account["id"])
            if account.get("status") != 1 or self.is_active(account_id):
                result["skipped"].append(account_id)
                continue
            try:
                await self.start(account_id)
                result["succeeded"].append(account_id)
            except APIError as exc:
                result["failed"].append(
                    {"account_id": account_id, "code": exc.code, "message": exc.message}
                )
        return result

    async def stop_all(self) -> dict[str, Any]:
        account_ids = [
            account_id for account_id, record in self._records.items()
            if record.channel is not None and record.state != "disconnected"
        ]
        result = {"succeeded": [], "skipped": [], "failed": []}
        for account_id in account_ids:
            try:
                await self.stop(account_id)
                result["succeeded"].append(account_id)
            except APIError as exc:
                result["failed"].append(
                    {"account_id": account_id, "code": exc.code, "message": exc.message}
                )
        return result

    async def shutdown(self) -> None:
        try:
            await asyncio.wait_for(self.stop_all(), timeout=15)
        except asyncio.TimeoutError:
            self.logger.error("关闭账号运行时超时，仍有资源未完成清理")


__all__ = ["AccountRuntimeManager", "RuntimeRecord"]
