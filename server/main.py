from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.environment import load_environment

load_environment()

from config import config as app_config
from core.di_container import configure_standard_services, container
from database.db_manager import DatabaseManager
from database.knowledge_service import KnowledgeService
from server.api import router
from server.auth import AuthService, SESSION_COOKIE
from server.errors import APIError
from server.events import EventBroker
from server.jobs import JobManager
from server.logs import LogBuffer
from server.runtime import AccountRuntimeManager
from utils.instance_lock import InstanceLock
from utils.logger_loguru import get_logger


logger = get_logger("WebApp")
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    *,
    auth_service: AuthService | None = None,
    acquire_instance_lock: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        instance_lock = InstanceLock()
        if acquire_instance_lock:
            instance_lock.acquire()
        try:
            configure_standard_services(app_config)
            database = container.get(DatabaseManager)
            knowledge = container.get(KnowledgeService)
            events = EventBroker()
            jobs = JobManager(events)
            runtimes = AccountRuntimeManager(events)
            log_buffer = LogBuffer(events)

            app.state.database = database
            app.state.knowledge = knowledge
            app.state.events = events
            app.state.jobs = jobs
            app.state.runtimes = runtimes
            app.state.log_buffer = log_buffer
            app.state.settings_lock = __import__("asyncio").Lock()
            log_buffer.install()
            runtimes.start_restore()
            app.state.ready = True
            logger.info("Agent-Customer Web 服务已启动")
            yield
        finally:
            app.state.ready = False
            events = getattr(app.state, "events", None)
            if events:
                await events.publish("system.shutdown_requested", {})
            runtimes = getattr(app.state, "runtimes", None)
            if runtimes:
                await runtimes.shutdown()
            jobs = getattr(app.state, "jobs", None)
            if jobs:
                await jobs.shutdown()
            log_buffer = getattr(app.state, "log_buffer", None)
            if log_buffer:
                log_buffer.uninstall()
            container.dispose()
            instance_lock.release()
            logger.info("Agent-Customer Web 服务已停止")

    app = FastAPI(
        title="Agent-Customer Web API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.auth = auth_service or AuthService()
    app.state.ready = False

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        started_at = time.perf_counter()
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        operator = "anonymous"
        try:
            operator = app.state.auth.get_session(
                request.cookies.get(SESSION_COOKIE)
            ).username
        except APIError:
            pass
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.url.path.startswith("/api/v1/")
        ):
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                f"Web 变更操作: operator={operator}, method={request.method}, "
                f"path={request.url.path}, status={response.status_code}, "
                f"duration_ms={elapsed_ms:.1f}, request_id={request_id}"
            )
        return response

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": getattr(request.state, "request_id", ""),
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "REQUEST_INVALID",
                    "message": "请求内容无效",
                    "request_id": getattr(request.state, "request_id", ""),
                    "details": {"fields": details},
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.error(
            f"未处理的 Web 请求异常: error_type={type(exc).__name__}, "
            f"request_id={getattr(request.state, 'request_id', '')}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务暂时无法处理请求",
                    "request_id": getattr(request.state, "request_id", ""),
                    "details": {},
                }
            },
        )

    @app.get("/health/live", include_in_schema=False)
    async def live():
        return {"status": "alive"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready(request: Request):
        auth_configured = request.app.state.auth.configured
        is_ready = bool(request.app.state.ready and auth_configured)
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={
                "status": "ready" if is_ready else "not_ready",
                "admin_configured": auth_configured,
            },
        )

    app.include_router(router)
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        icon = Path(__file__).resolve().parents[1] / "icon" / "icon.ico"
        return FileResponse(icon)

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    return app


app = create_app()


__all__ = ["app", "create_app"]
