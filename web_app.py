"""Command-line entry point for the FastAPI web application."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from server.environment import load_environment


def setup_playwright_browsers_path() -> None:
    browsers_path = Path(__file__).resolve().parent / ".browsers"
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers_path))


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent-Customer Web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument(
        "--hash-password",
        action="store_true",
        help="生成 CUSTOMER_AGENT_ADMIN_PASSWORD_HASH 后退出",
    )
    args = parser.parse_args()
    load_environment()

    if args.hash_password:
        from server.auth import hash_password

        password = getpass.getpass("管理员密码: ")
        confirmation = getpass.getpass("再次输入: ")
        if not password or password != confirmation:
            raise SystemExit("两次密码不一致或密码为空")
        print(hash_password(password))
        return

    if not (
        os.environ.get("CUSTOMER_AGENT_ADMIN_PASSWORD")
        or os.environ.get("CUSTOMER_AGENT_ADMIN_PASSWORD_HASH")
    ):
        raise SystemExit(
            "请在 .env 或系统环境变量中设置 "
            "CUSTOMER_AGENT_ADMIN_PASSWORD 或 CUSTOMER_AGENT_ADMIN_PASSWORD_HASH"
        )
    setup_playwright_browsers_path()
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        workers=1,
        reload=False,
    )


if __name__ == "__main__":
    main()
