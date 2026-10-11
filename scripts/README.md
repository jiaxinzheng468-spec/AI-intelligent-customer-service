# Web 辅助脚本

FastAPI Web 是项目唯一受支持的应用入口。此目录不再包含 PyInstaller、Inno Setup 或桌面安装包构建流程。

## 安装 Playwright 浏览器

账号登录和凭据刷新仍使用 Playwright 控制本机 Chrome 或 Edge，因此 Playwright 依赖与登录逻辑必须保留。

```powershell
uv run python scripts/install_playwright.py
```

应用启动入口：

```powershell
uv run python web_app.py --host 127.0.0.1 --port 8000
```
