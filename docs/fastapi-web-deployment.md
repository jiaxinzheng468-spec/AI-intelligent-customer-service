# FastAPI Web 部署指南

本文适用于当前单机 Windows 版本。Web 服务、账号长连接、任务状态和事件广播都在同一个 Python 进程内，因此只能运行一个 Uvicorn worker。

## 1. 发布前备份

先停止桌面版和 Web 版，然后在项目目录执行：

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path "backup" $stamp
New-Item -ItemType Directory -Path $backupDir | Out-Null
Copy-Item "temp/channel_shop.db" $backupDir
if (Test-Path "config.json") { Copy-Item "config.json" $backupDir }
```

确认备份目录中存在数据库副本后再升级。`config.json` 中的 API Key 以及数据库中的密码、Cookie 在 Windows 上使用 DPAPI 保护，服务必须继续由原 Windows 用户运行；更换用户或机器后通常需要重新录入凭据。

## 2. 安装和认证

```powershell
uv sync
Copy-Item .env.example .env
uv run python web_app.py --hash-password
```

将生成的哈希写入项目根目录 `.env`，然后再启动服务：

```dotenv
CUSTOMER_AGENT_ADMIN_USERNAME=admin
CUSTOMER_AGENT_ADMIN_PASSWORD=
CUSTOMER_AGENT_ADMIN_PASSWORD_HASH='pbkdf2_sha256$...'
```

```powershell
uv run python web_app.py --host 127.0.0.1 --port 8000
```

`.env` 已被 Git 忽略，但仍应限制文件读取权限，且不能通过压缩包、日志或聊天工具分发。系统环境变量的优先级高于 `.env`，生产服务也可以继续使用服务管理器注入密钥。

仅限本机访问时，Cookie 的 Secure 标记可保持默认值；任何跨机器访问都必须使用 HTTPS，并在 `.env` 设置：

```dotenv
CUSTOMER_AGENT_SECURE_COOKIE=true
```

## 3. 启动

仅本机管理：

```powershell
uv run python web_app.py --host 127.0.0.1 --port 8000
```

局域网部署时，可以监听 `0.0.0.0`，但必须由 IIS、Caddy 或 Nginx 提供 HTTPS 反向代理。不要使用 `uvicorn --workers 2` 或其他多进程配置，否则不同进程可能为同一账号建立重复连接。

服务运行账户还必须能够启动本机 Chrome 或 Edge。当前 Playwright 登录流程适合交互式 Windows 会话；作为不可交互的系统服务运行时，新增账号或重新登录可能无法完成。

## 4. 验证

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

`live` 应返回 `alive`，配置管理员凭据且初始化完成后 `ready` 应返回 `ready`。然后完成以下人工检查：

1. 登录管理后台，确认账号、关键词和知识库数据与升级前一致。
2. 启动一个在线账号，关闭浏览器页面后确认自动回复仍在运行。
3. 重新打开页面，确认运行状态可从服务端恢复。
4. 执行一次增量商品同步，并确认任务进度、取消和日志页面正常。
5. 停止账号，确认连接状态回到“未运行”。

## 5. 回滚

1. 停止 Web 服务，确认所有自动回复连接已经释放。
2. 如需恢复数据，将备份的 `channel_shop.db` 和 `config.json` 放回原位置。
3. 使用原 Windows 用户执行 `uv run python app.py`。
4. 桌面版启动后，检查账号状态和配置，再逐个恢复自动回复。

实例锁会阻止 Web 版与桌面版同时运行。若进程异常退出，操作系统会自动释放文件锁；锁文件本身存在不代表实例仍在运行。

## 6. 当前限制

- 任务和运行状态保存在内存中，服务重启后不会恢复正在执行的任务，也不会自动重连账号。
- SQLite、内存事件总线和账号连接所有权限制了横向扩容能力。
- 管理员会话保存在内存中，服务重启后需要重新登录。
- 添加账号和刷新登录依赖本机浏览器及 Windows 用户会话。
- 桌面入口仍保留，正式下线 PyQt6 前需要完成真实拼多多环境验收和持续运行测试。
