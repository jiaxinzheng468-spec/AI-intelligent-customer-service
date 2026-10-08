from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from server.errors import APIError


SESSION_COOKIE = "agent_customer_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
PBKDF2_ITERATIONS = 390_000


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        (
            "pbkdf2_sha256",
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Session:
    session_id: str
    username: str
    csrf_token: str
    expires_at: float


class AuthService:
    """Single-admin, server-side sessions with login rate limiting."""

    def __init__(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        password_hash: str | None = None,
        secure_cookie: bool | None = None,
    ) -> None:
        self.username = username or os.environ.get(
            "CUSTOMER_AGENT_ADMIN_USERNAME", "admin"
        )
        self._password = password or os.environ.get("CUSTOMER_AGENT_ADMIN_PASSWORD", "")
        self._password_hash = password_hash or os.environ.get(
            "CUSTOMER_AGENT_ADMIN_PASSWORD_HASH", ""
        )
        if secure_cookie is None:
            secure_cookie = os.environ.get(
                "CUSTOMER_AGENT_SECURE_COOKIE", "false"
            ).lower() in {"1", "true", "yes"}
        self.secure_cookie = secure_cookie
        self._sessions: dict[str, Session] = {}
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return bool(self.username and (self._password or self._password_hash))

    def authenticate(self, username: str, password: str, client_key: str) -> Session:
        now = time.time()
        with self._lock:
            attempts = self._attempts[client_key]
            while attempts and attempts[0] < now - 60:
                attempts.popleft()
            if len(attempts) >= 5:
                raise APIError(
                    "AUTH_RATE_LIMITED",
                    "登录尝试过于频繁，请稍后再试",
                    429,
                )
            if not self.configured:
                raise APIError(
                    "AUTH_NOT_CONFIGURED",
                    "管理员凭据尚未配置，请设置服务环境变量",
                    503,
                )

            username_ok = hmac.compare_digest(username, self.username)
            if self._password_hash:
                password_ok = verify_password(password, self._password_hash)
            else:
                password_ok = hmac.compare_digest(password, self._password)
            if not (username_ok and password_ok):
                attempts.append(now)
                raise APIError(
                    "AUTH_INVALID_CREDENTIALS", "用户名或密码错误", 401
                )

            attempts.clear()
            session = Session(
                session_id=secrets.token_urlsafe(32),
                username=self.username,
                csrf_token=secrets.token_urlsafe(32),
                expires_at=now + SESSION_TTL_SECONDS,
            )
            self._sessions[session.session_id] = session
            return session

    def get_session(self, session_id: str | None) -> Session:
        if not session_id:
            raise APIError("AUTH_SESSION_EXPIRED", "登录已过期，请重新登录", 401)
        now = time.time()
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.expires_at <= now:
                self._sessions.pop(session_id, None)
                raise APIError("AUTH_SESSION_EXPIRED", "登录已过期，请重新登录", 401)
            return session

    def verify_csrf(self, session: Session, token: str | None) -> None:
        if not token or not hmac.compare_digest(token, session.csrf_token):
            raise APIError("AUTH_CSRF_INVALID", "请求校验失败，请刷新页面后重试", 403)

    def logout(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def prune(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                key for key, session in self._sessions.items()
                if session.expires_at <= now
            ]
            for key in expired:
                self._sessions.pop(key, None)


__all__ = [
    "AuthService",
    "SESSION_COOKIE",
    "Session",
    "hash_password",
    "verify_password",
]

