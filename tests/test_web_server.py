import asyncio
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

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
        }

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
