import asyncio
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from database.db_manager import DatabaseManager
from server.api import _parse_import_file
from server.auth import AuthService, hash_password, verify_password
from server.events import EventBroker
from server.environment import load_environment
from server.jobs import JobManager
from server.logs import redact
from server.main import create_app
from server.runtime import AccountRuntimeManager


class AuthServiceTests(unittest.TestCase):
    def test_password_hash_round_trip_and_malformed_hash(self):
        encoded = hash_password("correct horse", iterations=1_000)

        self.assertTrue(verify_password("correct horse", encoded))
        self.assertFalse(verify_password("wrong", encoded))
        self.assertFalse(verify_password("anything", "not-a-valid-hash"))

    def test_login_rate_limit_and_csrf(self):
        auth = AuthService(username="admin", password="secret")
        for _ in range(5):
            with self.assertRaisesRegex(Exception, "用户名或密码错误"):
                auth.authenticate("admin", "wrong", "client")

        with self.assertRaisesRegex(Exception, "登录尝试过于频繁"):
            auth.authenticate("admin", "secret", "client")

        session = auth.authenticate("admin", "secret", "another-client")
        auth.verify_csrf(session, session.csrf_token)
        with self.assertRaisesRegex(Exception, "请求校验失败"):
            auth.verify_csrf(session, "wrong")


class EnvironmentTests(unittest.TestCase):
    def test_dotenv_loads_values_without_overriding_system_environment(self):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "CUSTOMER_AGENT_ADMIN_USERNAME=file-admin\n"
                "CUSTOMER_AGENT_ADMIN_PASSWORD=file-password\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"CUSTOMER_AGENT_ADMIN_USERNAME": "system-admin"},
                clear=False,
            ):
                os.environ.pop("CUSTOMER_AGENT_ADMIN_PASSWORD", None)
                loaded = load_environment(env_file)

                self.assertTrue(loaded)
                self.assertEqual(
                    os.environ["CUSTOMER_AGENT_ADMIN_USERNAME"], "system-admin"
                )
                self.assertEqual(
                    os.environ["CUSTOMER_AGENT_ADMIN_PASSWORD"], "file-password"
                )


class WebAPITests(unittest.TestCase):
    def setUp(self):
        auth = AuthService(username="admin", password="secret", secure_cookie=False)
        self.client = TestClient(
            create_app(auth_service=auth, acquire_instance_lock=False)
        )

    def tearDown(self):
        self.client.close()

    def test_openapi_contains_business_routes(self):
        paths = self.client.get("/openapi.json").json()["paths"]

        self.assertIn("/api/v1/accounts", paths)
        self.assertIn("/api/v1/runtimes/{account_id}/start", paths)
        self.assertIn("/api/v1/settings", paths)

    def test_authentication_cookie_and_csrf(self):
        unauthorized = self.client.get("/api/v1/accounts")
        self.assertEqual(unauthorized.status_code, 401)
        self.assertNotIn("password", unauthorized.text.lower())

        login = self.client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "secret"},
        )
        self.assertEqual(login.status_code, 200)
        csrf_token = login.json()["csrf_token"]
        self.assertIn("HttpOnly", login.headers["set-cookie"])

        rejected = self.client.post("/api/v1/auth/logout")
        self.assertEqual(rejected.status_code, 403)

        logout = self.client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_health_and_security_headers(self):
        response = self.client.get("/health/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertTrue(response.headers["x-request-id"])


class ImportAndRedactionTests(unittest.TestCase):
    def test_customer_knowledge_import_reports_invalid_rows(self):
        rows, invalid = _parse_import_file(
            "knowledge.csv",
            "title,content,tags\n有效标题,有效内容,售后\n,缺少标题,物流\n".encode("utf-8"),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(invalid, 1)

    def test_log_redaction(self):
        value = redact("password=secret authorization:Bearer-token cookie=session")

        self.assertNotIn("secret", value)
        self.assertNotIn("Bearer-token", value)
        self.assertNotIn("session", value)


class EventBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_critical_events_survive_low_priority_overflow(self):
        broker = EventBroker(queue_size=1)
        queue = await broker.subscribe()

        await broker.publish("log.appended", {"message": "old"})
        await broker.publish("log.appended", {"message": "latest"})
        await broker.publish("runtime.status_changed", {"state": "connected"})

        critical = await queue.get()
        latest_log = await queue.get()
        self.assertEqual(critical["type"], "runtime.status_changed")
        self.assertEqual(latest_log["data"]["message"], "latest")


class FakeStatusManager:
    def get_status(self, shop_id, user_id):
        return None


class FakeChannel:
    created = 0

    def __init__(self, *, fail=False):
        type(self).created += 1
        self.fail = fail
        self.status_manager = FakeStatusManager()
        self.stop_calls = 0

    async def start_account(self, shop_id, user_id, on_success, on_failure):
        if self.fail:
            on_failure("connection failed")
        else:
            on_success()

    async def stop_account(self, shop_id, user_id):
        self.stop_calls += 1


class GateChannel(FakeChannel):
    def __init__(self, started: asyncio.Event):
        super().__init__()
        self.started = started

    async def start_account(self, shop_id, user_id, on_success, on_failure):
        self.started.set()


class RuntimeManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeChannel.created = 0
        self.account = {
            "id": 7,
            "channel_name": "pinduoduo",
            "shop_id": "10",
            "shop_name": "测试店铺",
            "user_id": "20",
            "username": "agent",
            "status": 1,
            "cookies": '{"session":"valid"}',
            "auto_reply_enabled": False,
        }

    def account_service_patches(self, preference, accounts=None, operations=None):
        accounts = accounts or {7: self.account}
        operations = operations if operations is not None else []

        def get_account(account_id):
            account = accounts.get(account_id)
            if account is None:
                return None
            return dict(
                account,
                auto_reply_enabled=preference.get(account_id, False),
            )

        def set_enabled(account_id, enabled):
            operations.append(("preference", enabled))
            if account_id not in accounts:
                return False
            preference[account_id] = enabled
            return True

        return mock.patch.multiple(
            "server.runtime_recovery.account_service",
            get_account_by_id=get_account,
            get_auto_reply_enabled=lambda account_id: preference.get(account_id, False),
            set_auto_reply_enabled=set_enabled,
            get_auto_reply_enabled_accounts=lambda: [
                get_account(account_id)
                for account_id, enabled in preference.items()
                if enabled and account_id in accounts
            ],
            get_all_accounts_with_details=lambda: [
                get_account(account_id) for account_id in accounts
            ],
            record_recovery_result=lambda account_id, error=None: True,
        )

    async def test_start_and_stop_are_idempotent(self):
        manager = AccountRuntimeManager(EventBroker(), channel_factory=FakeChannel)
        with mock.patch(
            "server.runtime.account_service.get_account_by_id",
            return_value=self.account,
        ):
            await manager.start(7)
            await asyncio.sleep(0)
            await manager.start(7)
            self.assertEqual(FakeChannel.created, 1)

            channel = manager._records[7].channel
            await manager.stop(7)
            await manager.stop(7)

        self.assertEqual(channel.stop_calls, 1)
        self.assertEqual(manager._records[7].state, "disconnected")

    async def test_error_state_does_not_create_duplicate_channel(self):
        manager = AccountRuntimeManager(
            EventBroker(), channel_factory=lambda: FakeChannel(fail=True)
        )
        with mock.patch(
            "server.runtime.account_service.get_account_by_id",
            return_value=self.account,
        ):
            await manager.start(7)
            await asyncio.sleep(0)
            self.assertEqual(manager._records[7].state, "error")
            await manager.start(7)

        self.assertEqual(FakeChannel.created, 1)

    async def test_enable_disable_order_and_shutdown_preserves_preference(self):
        preference = {7: False}
        operations = []

        class OrderedChannel(FakeChannel):
            async def stop_account(self, shop_id, user_id):
                operations.append(("channel", "stop"))
                await super().stop_account(shop_id, user_id)

        manager = AccountRuntimeManager(EventBroker(), channel_factory=OrderedChannel)
        with self.account_service_patches(preference, operations=operations):
            enabled = await manager.enable(7)
            await asyncio.sleep(0)
            self.assertTrue(enabled["auto_reply_enabled"])
            self.assertTrue(preference[7])

            await manager.shutdown()
            self.assertTrue(preference[7])
            self.assertNotIn(("preference", False), operations)

            manager = AccountRuntimeManager(EventBroker(), channel_factory=OrderedChannel)
            await manager.enable(7)
            await asyncio.sleep(0)
            operations.clear()
            disabled = await manager.disable(7)

        self.assertFalse(disabled["auto_reply_enabled"])
        self.assertEqual(operations[:2], [("preference", False), ("channel", "stop")])

    async def test_offline_account_is_not_persisted_as_enabled(self):
        preference = {7: False}
        offline = dict(self.account, status=3)
        set_enabled = mock.Mock(return_value=True)
        manager = AccountRuntimeManager(EventBroker(), channel_factory=FakeChannel)
        with mock.patch.multiple(
            "server.runtime_recovery.account_service",
            get_account_by_id=lambda account_id: offline,
            set_auto_reply_enabled=set_enabled,
        ):
            with self.assertRaisesRegex(Exception, "必须先上线"):
                await manager.enable(7)
        set_enabled.assert_not_called()

    async def test_restore_failure_is_isolated_from_other_accounts(self):
        accounts = {
            7: self.account,
            8: dict(self.account, id=8, user_id="21", username="agent-2"),
        }
        preference = {7: True, 8: True}

        def channel_factory():
            return FakeChannel(fail=FakeChannel.created == 0)

        manager = AccountRuntimeManager(
            EventBroker(),
            channel_factory=channel_factory,
            recovery_delays=(0.0,),
            recovery_attempt_timeout=1,
        )
        with self.account_service_patches(preference, accounts=accounts):
            summary = await manager.restore_enabled()
            await asyncio.sleep(0)
            await manager.shutdown()

        self.assertEqual(summary.succeeded, [8])
        self.assertEqual(summary.failed[0]["account_id"], 7)
        self.assertEqual(FakeChannel.created, 2)
        self.assertTrue(preference[7])

    async def test_restore_retries_network_failure_with_bounded_schedule(self):
        preference = {7: True}

        def channel_factory():
            return FakeChannel(fail=FakeChannel.created < 2)

        manager = AccountRuntimeManager(
            EventBroker(),
            channel_factory=channel_factory,
            recovery_delays=(0.0, 0.0, 0.0),
            recovery_attempt_timeout=1,
        )
        with self.account_service_patches(preference):
            summary = await manager.restore_enabled()
            await asyncio.sleep(0)
            await manager.shutdown()

        self.assertEqual(summary.succeeded, [7])
        self.assertEqual(FakeChannel.created, 3)

    async def test_restore_rechecks_preference_when_disable_races(self):
        preference = {7: True}
        started = asyncio.Event()
        manager = AccountRuntimeManager(
            EventBroker(),
            channel_factory=lambda: GateChannel(started),
            recovery_delays=(0.0,),
            recovery_attempt_timeout=1,
        )
        with self.account_service_patches(preference):
            restore_task = asyncio.create_task(manager.restore_enabled())
            await asyncio.wait_for(started.wait(), timeout=1)
            await manager.disable(7)
            summary = await asyncio.wait_for(restore_task, timeout=1)

        self.assertFalse(preference[7])
        self.assertEqual(summary.skipped, [7])
        self.assertEqual(manager._records[7].state, "disconnected")

    async def test_restart_restores_enabled_account_from_same_database(self):
        with TemporaryDirectory() as directory:
            database = DatabaseManager(str(Path(directory) / "restart.db"))
            try:
                database.add_shop("pinduoduo", "10", "测试店铺", "")
                database.add_account(
                    "pinduoduo",
                    "10",
                    "20",
                    "agent",
                    "password",
                    cookies={"session": "valid"},
                )
                account = database.get_all_accounts_with_details()[0]
                account_id = int(account["id"])
                database.update_account_status(
                    "pinduoduo", "10", "20", 1
                )

                patches = mock.patch.multiple(
                    "server.runtime_recovery.account_service",
                    get_account_by_id=database.get_account_by_id,
                    get_auto_reply_enabled=database.get_auto_reply_enabled,
                    set_auto_reply_enabled=database.set_auto_reply_enabled,
                    get_auto_reply_enabled_accounts=(
                        database.get_auto_reply_enabled_accounts
                    ),
                    get_all_accounts_with_details=(
                        database.get_all_accounts_with_details
                    ),
                    record_recovery_result=database.record_recovery_result,
                )
                with patches:
                    lifecycle_a = AccountRuntimeManager(
                        EventBroker(), channel_factory=FakeChannel
                    )
                    await lifecycle_a.enable(account_id)
                    await asyncio.sleep(0)
                    await lifecycle_a.shutdown()
                    self.assertTrue(
                        database.get_auto_reply_enabled(account_id)
                    )

                    FakeChannel.created = 0
                    lifecycle_b = AccountRuntimeManager(
                        EventBroker(),
                        channel_factory=FakeChannel,
                        recovery_delays=(0.0,),
                        recovery_attempt_timeout=1,
                    )
                    summary = await lifecycle_b.restore_enabled()
                    await asyncio.sleep(0)
                    self.assertEqual(summary.succeeded, [account_id])
                    self.assertEqual(FakeChannel.created, 1)

                    await lifecycle_b.disable(account_id)
                    await lifecycle_b.shutdown()
                    FakeChannel.created = 0
                    lifecycle_c = AccountRuntimeManager(
                        EventBroker(),
                        channel_factory=FakeChannel,
                        recovery_delays=(0.0,),
                    )
                    stopped_summary = await lifecycle_c.restore_enabled()
                    self.assertEqual(stopped_summary.total, 0)
                    self.assertEqual(FakeChannel.created, 0)
            finally:
                database.dispose()


class JobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_success_duplicate_guard_and_cancel(self):
        manager = JobManager(EventBroker())
        gate = asyncio.Event()

        async def runner(report):
            report({"current": 1, "total": 2})
            await gate.wait()
            return {"ok": True}

        record = await manager.submit(kind="test", resource_key="same", runner=runner)
        with self.assertRaisesRegex(Exception, "同类任务已在运行"):
            await manager.submit(kind="test", resource_key="same", runner=runner)

        await manager.cancel(record.id)
        await asyncio.sleep(0)
        self.assertEqual(manager.get(record.id).status, "cancelled")

    async def test_product_sync_concurrency_is_limited_to_two(self):
        manager = JobManager(EventBroker())
        gate = asyncio.Event()
        active = 0
        maximum_active = 0

        async def runner(report):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await gate.wait()
            active -= 1

        records = [
            await manager.submit(
                kind="product_sync",
                resource_key=f"shop:{index}",
                runner=runner,
            )
            for index in range(3)
        ]
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(sum(record.status == "running" for record in records), 2)
        self.assertEqual(sum(record.status == "queued" for record in records), 1)
        self.assertEqual(maximum_active, 2)

        gate.set()
        await asyncio.gather(*list(manager._tasks.values()), return_exceptions=True)
        self.assertTrue(all(record.status == "succeeded" for record in records))


if __name__ == "__main__":
    unittest.main()
