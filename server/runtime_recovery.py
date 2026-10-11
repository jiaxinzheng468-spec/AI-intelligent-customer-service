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


RECOVERY_CONCURRENCY = 3
RECOVERY_DELAYS = (0.0, 2.0, 5.0, 15.0)
RECOVERY_ATTEMPT_TIMEOUT = 120.0


@dataclass
class RuntimeRecord:
    account_id: int
    account_key: str
    shop_id: str
    user_id: str
    state: str = "disconnected"
    channel: Any = None
    started_at: str | None = None
    last_error: str | None = None
    auto_reply_enabled: bool = False
    connection_result: asyncio.Future[str] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class RestoreSummary:
    total: int = 0
    succeeded: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "skipped": self.skipped,
            "failed": self.failed,
        }


class AccountRuntimeManager:
    """Own account connections and persist the administrator's desired state."""

    def __init__(
        self,
        broker: EventBroker,
        channel_factory: Callable[[], Any] = PDDChannel,
        *,
        recovery_concurrency: int = RECOVERY_CONCURRENCY,
        recovery_delays: tuple[float, ...] = RECOVERY_DELAYS,
        recovery_attempt_timeout: float = RECOVERY_ATTEMPT_TIMEOUT,
    ) -> None:
        self._broker = broker
        self._channel_factory = channel_factory
        self._records: dict[int, RuntimeRecord] = {}
        self._recovery_concurrency = max(1, recovery_concurrency)
        self._recovery_delays = recovery_delays or (0.0,)
        self._recovery_attempt_timeout = max(0.1, recovery_attempt_timeout)
        self._restore_task: asyncio.Task[RestoreSummary] | None = None
        self._shutting_down = False
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
            record = RuntimeRecord(
                account_id=account_id,
                account_key=self._key(account),
                shop_id=str(account["shop_id"]),
                user_id=str(account["user_id"]),
                auto_reply_enabled=bool(account.get("auto_reply_enabled", False)),
                last_error=account.get("last_recovery_error"),
            )
            self._records[account_id] = record
        else:
            record.account_key = self._key(account)
            record.shop_id = str(account["shop_id"])
            record.user_id = str(account["user_id"])
        return record

    async def _publish(self, record: RuntimeRecord) -> None:
        await self._broker.publish(
            "runtime.status_changed",
            {
                "account_id": record.account_id,
                "auto_reply_enabled": record.auto_reply_enabled,
                "state": record.state,
                "started_at": record.started_at,
                "last_error": record.last_error,
            },
        )

    async def _record_result(self, account_id: int, error: str | None) -> None:
        try:
            await asyncio.to_thread(
                account_service.record_recovery_result, account_id, error
            )
        except Exception as exc:
            self.logger.error(
                f"记录账号恢复结果失败: account_id={account_id}, "
                f"error_type={type(exc).__name__}"
            )

    def _schedule_state(
        self,
        record: RuntimeRecord,
        channel: Any,
        loop: asyncio.AbstractEventLoop,
        state: str,
        error: str | None = None,
        persist_result: bool = True,
    ) -> None:
        if loop.is_closed():
            return

        def apply_state() -> None:
            if record.channel is not channel or record.state in {
                "stopping",
                "disconnected",
            }:
                return
            record.state = state
            record.last_error = error
            result = record.connection_result
            if result is not None and not result.done():
                result.set_result(state)
            loop.create_task(self._publish(record))
            if persist_result:
                loop.create_task(
                    self._record_result(
                        record.account_id,
                        None if state == "connected" else "network_error",
                    )
                )

        loop.call_soon_threadsafe(apply_state)

    async def _disconnect_locked(self, record: RuntimeRecord) -> None:
        channel = record.channel
        if channel is None or record.state == "disconnected":
            record.channel = None
            record.state = "disconnected"
            record.started_at = None
            record.last_error = None
            result = record.connection_result
            if result is not None and not result.done():
                result.set_result("cancelled")
            record.connection_result = None
            await self._publish(record)
            return
        if record.state == "stopping":
            return

        record.state = "stopping"
        await self._publish(record)
        result = record.connection_result
        if result is not None and not result.done():
            result.set_result("cancelled")
        record.connection_result = None
        try:
            await asyncio.wait_for(
                channel.stop_account(record.shop_id, record.user_id), timeout=10
            )
        except asyncio.TimeoutError as exc:
            request_stop = getattr(channel, "request_stop", None)
            if callable(request_stop):
                request_stop()
            record.state = "error"
            record.last_error = "自动回复停止超时，请等待资源清理"
            await self._publish(record)
            raise APIError("RUNTIME_STOP_TIMEOUT", record.last_error, 504) from exc
        except Exception as exc:
            record.state = "error"
            record.last_error = "自动回复停止失败，请查看服务端日志"
            await self._publish(record)
            self.logger.error(
                f"账号运行时停止失败: account_id={record.account_id}, "
                f"error_type={type(exc).__name__}"
            )
            raise APIError("RUNTIME_STOP_FAILED", record.last_error, 500) from exc

        record.channel = None
        record.state = "disconnected"
        record.started_at = None
        record.last_error = None
        await self._publish(record)

    async def _connect_locked(
        self,
        account: dict[str, Any],
        record: RuntimeRecord,
        *,
        replace_error: bool,
        wait_for_initial_result: bool,
        persist_result: bool,
    ) -> tuple[dict[str, Any], asyncio.Future[str] | None]:
        if record.state in {"connecting", "connected", "reconnecting"}:
            return self.snapshot_one(account, record), record.connection_result
        if record.state == "stopping":
            raise APIError("ACCOUNT_RUNTIME_BUSY", "账号正在停止，请稍后重试", 409)
        if record.state == "error" and record.channel is not None:
            if not replace_error:
                return self.snapshot_one(account, record), record.connection_result
            await self._disconnect_locked(record)

        channel = self._channel_factory()
        loop = asyncio.get_running_loop()
        record.channel = channel
        record.state = "connecting"
        record.started_at = datetime.now().astimezone().isoformat()
        record.last_error = None
        record.connection_result = (
            loop.create_future() if wait_for_initial_result else None
        )
        await self._publish(record)

        def on_success() -> None:
            self._schedule_state(
                record,
                channel,
                loop,
                "connected",
                persist_result=persist_result,
            )

        def on_failure(_message: str) -> None:
            self._schedule_state(
                record,
                channel,
                loop,
                "error",
                "自动回复连接失败，请检查账号凭据和网络",
                persist_result=persist_result,
            )

        try:
            await channel.start_account(
                shop_id=record.shop_id,
                user_id=record.user_id,
                on_success=on_success,
                on_failure=on_failure,
            )
        except Exception as exc:
            record.channel = None
            record.state = "error"
            record.last_error = "自动回复启动失败，请检查账号和网络"
            result = record.connection_result
            if result is not None and not result.done():
                result.set_result("error")
            self.logger.error(
                f"账号运行时启动失败: account_id={record.account_id}, "
                f"error_type={type(exc).__name__}"
            )
            await self._publish(record)
            if persist_result:
                await self._record_result(record.account_id, "network_error")
            raise APIError("RUNTIME_START_FAILED", record.last_error, 502) from exc
        return self.snapshot_one(account, record), record.connection_result

    async def start(self, account_id: int) -> dict[str, Any]:
        """Start a connection without changing its persisted desired state."""
        account = await asyncio.to_thread(account_service.get_account_by_id, account_id)
        if not account:
            raise APIError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
        if account.get("status") != 1:
            raise APIError(
                "ACCOUNT_NOT_ONLINE", "账号必须先上线才能启动自动回复", 409
            )
        record = self._record_for(account)
        async with record.lock:
            snapshot, _ = await self._connect_locked(
                account,
                record,
                replace_error=False,
                wait_for_initial_result=False,
                persist_result=False,
            )
            return snapshot

    async def stop(self, account_id: int) -> dict[str, Any]:
        """Disconnect an account without changing its persisted desired state."""
        account = await asyncio.to_thread(account_service.get_account_by_id, account_id)
        if not account:
            raise APIError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
        record = self._record_for(account)
        async with record.lock:
            await self._disconnect_locked(record)
            return self.snapshot_one(account, record)

    async def enable(self, account_id: int) -> dict[str, Any]:
        """Persist the enabled intent and ensure the account is connecting."""
        if self._shutting_down:
            raise APIError("SERVICE_SHUTTING_DOWN", "服务正在关闭", 503)
        account = await asyncio.to_thread(account_service.get_account_by_id, account_id)
        if not account:
            raise APIError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
        if account.get("status") != 1:
            raise APIError(
                "ACCOUNT_NOT_ONLINE", "账号必须先上线才能启动自动回复", 409
            )
        if not await asyncio.to_thread(
            account_service.set_auto_reply_enabled, account_id, True
        ):
            raise APIError("ACCOUNT_NOT_FOUND", "账号不存在", 404)

        account["auto_reply_enabled"] = True
        record = self._record_for(account)
        record.auto_reply_enabled = True
        async with record.lock:
            current = await asyncio.to_thread(
                account_service.get_account_by_id, account_id
            )
            enabled = await asyncio.to_thread(
                account_service.get_auto_reply_enabled, account_id
            )
            if not current or not enabled:
                record.auto_reply_enabled = False
                if not current:
                    raise APIError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
                return self.snapshot_one(current, record)
            if current.get("status") != 1:
                await asyncio.to_thread(
                    account_service.set_auto_reply_enabled, account_id, False
                )
                record.auto_reply_enabled = False
                raise APIError(
                    "ACCOUNT_NOT_ONLINE", "账号必须先上线才能启动自动回复", 409
                )
            current["auto_reply_enabled"] = True
            snapshot, _ = await self._connect_locked(
                current,
                record,
                replace_error=True,
                wait_for_initial_result=False,
                persist_result=True,
            )
            return snapshot

    async def disable(self, account_id: int) -> dict[str, Any]:
        """Persist the disabled intent before disconnecting the account."""
        account = await asyncio.to_thread(account_service.get_account_by_id, account_id)
        if not account:
            raise APIError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
        if not await asyncio.to_thread(
            account_service.set_auto_reply_enabled, account_id, False
        ):
            raise APIError("ACCOUNT_NOT_FOUND", "账号不存在", 404)

        account["auto_reply_enabled"] = False
        record = self._record_for(account)
        record.auto_reply_enabled = False
        async with record.lock:
            await self._disconnect_locked(record)
            return self.snapshot_one(account, record)

    def is_active(self, account_id: int) -> bool:
        record = self._records.get(account_id)
        return bool(
            record
            and record.state
            in {"connecting", "connected", "reconnecting", "stopping", "error"}
            and record.channel is not None
        )

    def snapshot_one(
        self, account: dict[str, Any], record: RuntimeRecord | None = None
    ) -> dict[str, Any]:
        record = record or self._records.get(int(account["id"]))
        state = record.state if record else "disconnected"
        reconnect_count = 0
        connect_time = None
        if record and record.channel and state != "stopping":
            status_manager = getattr(record.channel, "status_manager", None)
            status = (
                status_manager.get_status(account["shop_id"], account["user_id"])
                if status_manager
                else None
            )
            if status:
                state = status.state.value
                reconnect_count = status.reconnect_count
                connect_time = (
                    status.connect_time.astimezone().isoformat()
                    if status.connect_time
                    else None
                )
        enabled = (
            record.auto_reply_enabled
            if record
            else bool(account.get("auto_reply_enabled", False))
        )
        last_error = (
            record.last_error
            if record
            else account.get("last_recovery_error") if enabled else None
        )
        return {
            "account_id": int(account["id"]),
            "channel_name": account["channel_name"],
            "shop_id": account["shop_id"],
            "shop_name": account["shop_name"],
            "user_id": account["user_id"],
            "username": account["username"],
            "platform_status": account.get("status"),
            "auto_reply_enabled": enabled,
            "state": state,
            "started_at": record.started_at if record else None,
            "connect_time": connect_time,
            "reconnect_count": reconnect_count,
            "last_error": last_error,
        }

    async def snapshots(self) -> list[dict[str, Any]]:
        accounts = await asyncio.to_thread(
            account_service.get_all_accounts_with_details
        )
        return [self.snapshot_one(account) for account in accounts]

    async def start_all(self) -> dict[str, Any]:
        accounts = await asyncio.to_thread(
            account_service.get_all_accounts_with_details
        )
        result = {"succeeded": [], "skipped": [], "failed": []}
        for account in accounts:
            account_id = int(account["id"])
            if account.get("status") != 1:
                result["skipped"].append(account_id)
                continue
            try:
                await self.enable(account_id)
                result["succeeded"].append(account_id)
            except APIError as exc:
                result["failed"].append(
                    {
                        "account_id": account_id,
                        "code": exc.code,
                        "message": exc.message,
                    }
                )
        return result

    async def stop_all(self) -> dict[str, Any]:
        """Administrator action: disable every enabled or active account."""
        accounts = await asyncio.to_thread(
            account_service.get_all_accounts_with_details
        )
        result = {"succeeded": [], "skipped": [], "failed": []}
        for account in accounts:
            account_id = int(account["id"])
            if not account.get("auto_reply_enabled") and not self.is_active(account_id):
                result["skipped"].append(account_id)
                continue
            try:
                await self.disable(account_id)
                result["succeeded"].append(account_id)
            except APIError as exc:
                result["failed"].append(
                    {
                        "account_id": account_id,
                        "code": exc.code,
                        "message": exc.message,
                    }
                )
        return result

    async def _mark_restore_error(
        self, record: RuntimeRecord, message: str, category: str
    ) -> None:
        record.state = "error"
        record.last_error = message
        await self._publish(record)
        await self._record_result(record.account_id, category)

    async def _restore_one(
        self,
        seeded_account: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, dict[str, Any]]:
        account_id = int(seeded_account["id"])
        async with semaphore:
            record = self._record_for(seeded_account)
            record.auto_reply_enabled = True
            for attempt, delay in enumerate(self._recovery_delays):
                if self._shutting_down:
                    return "skipped", {"account_id": account_id, "code": "shutdown"}
                if delay:
                    await asyncio.sleep(delay)

                async with record.lock:
                    account = await asyncio.to_thread(
                        account_service.get_account_by_id, account_id
                    )
                    enabled = await asyncio.to_thread(
                        account_service.get_auto_reply_enabled, account_id
                    )
                    if not account:
                        return "skipped", {
                            "account_id": account_id,
                            "code": "account_deleted",
                        }
                    if not enabled:
                        record.auto_reply_enabled = False
                        if record.channel is not None:
                            await self._disconnect_locked(record)
                        return "skipped", {
                            "account_id": account_id,
                            "code": "disabled",
                        }
                    account["auto_reply_enabled"] = True
                    record.auto_reply_enabled = True
                    if account.get("status") != 1:
                        await self._mark_restore_error(
                            record,
                            "账号平台状态不是在线，请先上线账号",
                            "platform_offline",
                        )
                        return "failed", {
                            "account_id": account_id,
                            "code": "platform_offline",
                            "message": record.last_error,
                        }
                    if not account.get("cookies"):
                        await self._mark_restore_error(
                            record,
                            "账号登录凭据缺失，请重新验证",
                            "credentials_invalid",
                        )
                        return "failed", {
                            "account_id": account_id,
                            "code": "credentials_invalid",
                            "message": record.last_error,
                        }
                    if record.state in {"connected", "connecting", "reconnecting"}:
                        return "skipped", {
                            "account_id": account_id,
                            "code": "already_active",
                        }
                    _, connection_result = await self._connect_locked(
                        account,
                        record,
                        replace_error=True,
                        wait_for_initial_result=True,
                        persist_result=True,
                    )

                try:
                    outcome = await asyncio.wait_for(
                        asyncio.shield(connection_result),
                        timeout=self._recovery_attempt_timeout,
                    )
                except asyncio.TimeoutError:
                    outcome = "error"
                    async with record.lock:
                        if record.state in {"connecting", "reconnecting"}:
                            await self._disconnect_locked(record)
                            await self._mark_restore_error(
                                record,
                                "自动回复连接超时，请检查网络",
                                "network_error",
                            )
                if outcome == "connected":
                    return "succeeded", {"account_id": account_id}
                if outcome == "cancelled":
                    return "skipped", {
                        "account_id": account_id,
                        "code": "disabled_or_shutdown",
                    }
                if attempt == len(self._recovery_delays) - 1:
                    await self._record_result(account_id, "network_error")
                    return "failed", {
                        "account_id": account_id,
                        "code": "network_error",
                        "message": record.last_error
                        or "自动回复连接失败，请检查账号凭据和网络",
                    }
            return "failed", {"account_id": account_id, "code": "network_error"}

    async def restore_enabled(self) -> RestoreSummary:
        """Restore enabled accounts with bounded concurrency and isolation."""
        try:
            accounts = await asyncio.to_thread(
                account_service.get_auto_reply_enabled_accounts
            )
        except Exception as exc:
            self.logger.error(
                f"读取待恢复账号失败: error_type={type(exc).__name__}"
            )
            return RestoreSummary(
                failed=[
                    {
                        "account_id": None,
                        "code": "database_error",
                        "message": "读取待恢复账号失败",
                    }
                ]
            )

        summary = RestoreSummary(total=len(accounts))
        self.logger.info(f"开始恢复账号连接: count={len(accounts)}")
        semaphore = asyncio.Semaphore(self._recovery_concurrency)
        results = await asyncio.gather(
            *(self._restore_one(account, semaphore) for account in accounts),
            return_exceptions=True,
        )
        for account, result in zip(accounts, results):
            account_id = int(account["id"])
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    summary.skipped.append(account_id)
                    continue
                self.logger.error(
                    f"恢复账号连接失败: account_id={account_id}, "
                    f"error_type={type(result).__name__}"
                )
                summary.failed.append(
                    {
                        "account_id": account_id,
                        "code": "recovery_error",
                        "message": "账号恢复失败，请查看服务端日志",
                    }
                )
                continue
            status, details = result
            if status == "succeeded":
                summary.succeeded.append(account_id)
            elif status == "skipped":
                summary.skipped.append(account_id)
            else:
                summary.failed.append(details)
        self.logger.info(
            "账号连接恢复结束: "
            f"succeeded={len(summary.succeeded)}, "
            f"skipped={len(summary.skipped)}, failed={len(summary.failed)}"
        )
        return summary

    def start_restore(self) -> asyncio.Task[RestoreSummary]:
        """Start and track the non-blocking startup restoration task."""
        if self._restore_task is not None and not self._restore_task.done():
            return self._restore_task
        self._shutting_down = False
        self._restore_task = asyncio.create_task(
            self.restore_enabled(), name="account-runtime-restore"
        )
        return self._restore_task

    async def disconnect_all(self) -> None:
        """Disconnect actual channels while preserving every desired state."""
        for record in list(self._records.values()):
            async with record.lock:
                try:
                    await self._disconnect_locked(record)
                except APIError as exc:
                    self.logger.error(
                        f"关闭账号连接失败: account_id={record.account_id}, "
                        f"code={exc.code}"
                    )

    async def shutdown(self) -> None:
        self._shutting_down = True
        restore_task = self._restore_task
        if restore_task is not None and not restore_task.done():
            restore_task.cancel()
            await asyncio.gather(restore_task, return_exceptions=True)
        try:
            await asyncio.wait_for(self.disconnect_all(), timeout=15)
        except asyncio.TimeoutError:
            self.logger.error("关闭账号运行时超时，仍有资源未完成清理")


__all__ = ["AccountRuntimeManager", "RestoreSummary", "RuntimeRecord"]
