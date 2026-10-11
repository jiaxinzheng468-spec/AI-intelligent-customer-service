# Agent-Customer 账号重启恢复与桌面端下线开发文档

> 状态：代码实施完成，自动化验证通过，待真实拼多多账号人工验收  
> 编写日期：2026-10-11  
> 本次范围：实现账号自动回复连接在服务重启后的恢复，并正式下线 PyQt6 桌面端

## 1. 目标

当管理员已经为某个账号启用自动回复后，即使 FastAPI 正常重启、程序异常退出或机器重新启动，只要服务再次运行，就应根据数据库中保存的期望状态重新建立该账号的拼多多长连接。

完成本次改造后，FastAPI Web 版是唯一受支持的应用入口；不再保留 `app.py`、`ui/`、PyQt6 依赖和桌面安装包作为回滚路径。

目标流程：

```text
管理员启用自动回复
        ↓
数据库保存 auto_reply_enabled=true
        ↓
建立 PDDChannel 长连接
        ↓
FastAPI 或程序重启，连接断开
        ↓
新进程读取 auto_reply_enabled=true 的账号
        ↓
重新建立 PDDChannel 长连接
```

## 2. 不在本次范围

本次明确不实施：

- 前后端分离。
- 多 Uvicorn worker 或多进程架构。
- Redis、PostgreSQL或外部消息队列。
- 后台任务断点恢复。
- 未处理客户消息的跨重启持久化。
- Vue 或其他前端框架迁移。
- `bridge/context.py` 改造。

`context.py` 继续负责平台消息转换后的内部统一上下文，与账号是否应在重启后重新连接无关。

桌面端下线属于本次范围，但不等于前后端分离：现有 Web 页面仍可继续由 FastAPI 托管，本次不迁移前端工程。

## 3. 当前行为

当前 `AccountRuntimeManager` 的运行记录保存在内存字典 `_records` 中。管理员调用启动接口后创建 `PDDChannel`；FastAPI 关闭时，`lifespan` 调用 `runtimes.shutdown()`，所有连接被停止。

重启后会创建新的 `AccountRuntimeManager`，其 `_records` 为空。数据库虽然仍保存账号、Cookie、店铺和平台状态，但没有保存“该账号应保持自动回复运行”的信息，因此账号不会自动连接。

需要区分：

| 状态 | 含义 | 当前存储位置 |
| --- | --- | --- |
| `Account.status` | 拼多多平台在线/离线状态 | SQLite，持久化 |
| `RuntimeRecord.state` | connecting/connected/error 等实际连接状态 | 内存，不持久化 |
| 自动回复期望状态 | 管理员是否希望该账号持续运行 | 当前不存在 |

不得复用 `Account.status` 表示自动回复期望状态，因为“平台在线”和“本系统应自动连接”是两种不同含义。

## 4. 设计原则

### 4.1 保存期望状态，不保存连接对象

WebSocket、事件循环、锁和 `PDDChannel` 对象不能持久化。数据库只保存：

```text
auto_reply_enabled = true / false
```

重启后根据该值创建全新的连接。

### 4.2 区分期望状态与实际状态

- **期望状态**：数据库中的 `auto_reply_enabled`，表示管理员希望账号运行。
- **实际状态**：内存中的 disconnected/connecting/connected/reconnecting/error。

即使恢复连接失败，期望状态仍可保持为 `true`，这样界面能够表达“希望运行，但当前连接失败”，而不是把一次网络错误误认为管理员关闭了功能。

### 4.3 区分手动停止与程序关闭

这是本次实现最重要的约束：

- 管理员点击“停止”：保存 `auto_reply_enabled=false`，然后断开连接。
- 服务正常关闭：只断开连接，不修改 `auto_reply_enabled`。

如果复用同一个 `stop_all()` 并在其中清除期望状态，那么每次正常重启都会把所有恢复配置删除，功能将失效。

### 4.4 单账号操作必须幂等

- 已连接账号再次恢复，不创建第二条连接。
- 已停止账号再次停止，不报错。
- 同一账号的恢复、手动启动和手动停止共用现有 `RuntimeRecord.lock`。
- 创建连接前必须在锁内重新读取期望状态，避免恢复与手动停止发生竞态。

## 5. 数据模型

### 5.1 推荐新增独立表

新增模型 `AccountRuntimePreference`：

```text
account_runtime_preferences
├── account_id               INTEGER PRIMARY KEY, FK accounts.id
├── auto_reply_enabled       BOOLEAN NOT NULL DEFAULT false
├── updated_at               DATETIME NOT NULL
├── last_recovery_attempt_at DATETIME NULL
└── last_recovery_error      TEXT NULL
```

推荐独立表而不是给 `accounts` 直接增加字段，原因如下：

1. 当前项目使用 `Base.metadata.create_all()`，它能创建新表，但不会自动给已有表增加列。
2. 新表对旧数据库是向后兼容的，不需要执行高风险的 `ALTER TABLE accounts`。
3. 平台账号数据和本应用运行偏好职责分离。
4. 未来增加恢复次数、最近恢复时间等字段时更清晰。

数据库约定：

- 账号没有对应偏好记录时，默认视为 `auto_reply_enabled=false`。
- 外键使用 `ondelete="CASCADE"`，删除账号时同时删除偏好。
- `last_recovery_error` 只保存脱敏后的可操作错误，不保存 Cookie、密码或平台响应正文。
- 不持久化 `connected` 作为真实状态，因为进程退出后该状态必然已经过期。

### 5.2 首次升级行为

升级前无法可靠判断哪些账号是管理员希望长期自动运行的，因此所有旧账号默认 `auto_reply_enabled=false`。

升级完成后，管理员需要为目标账号手动启动一次。此后该选择会持久化，后续重启自动恢复。不能根据 `Account.status=1` 自动启用所有账号，因为平台在线不等于管理员希望本系统自动回复。

## 6. 服务层接口

在 `DatabaseManager` 和 `AccountService` 增加以下能力，具体命名可遵循现有代码风格：

```python
get_auto_reply_enabled(account_id: int) -> bool
set_auto_reply_enabled(account_id: int, enabled: bool) -> bool
get_auto_reply_enabled_accounts() -> list[dict]
record_recovery_result(account_id: int, error: str | None) -> None
```

要求：

- `set_auto_reply_enabled` 使用单个数据库事务提交。
- 不存在偏好记录时使用 upsert 或“查询后创建”。
- 查询恢复账号时联表返回 `AccountRuntimeManager.start()` 所需的账号信息。
- 所有返回数据继续执行现有密码和 Cookie 解密保护规则。
- 数据库异常必须记录错误类型，但日志不得包含密钥或 Cookie。

## 7. Runtime 接口调整

### 7.1 明确四类操作

建议在 `AccountRuntimeManager` 中明确区分：

```python
async def enable(account_id: int) -> dict:
    """保存启用意图，并确保账号连接。"""

async def disable(account_id: int) -> dict:
    """保存停用意图，并确保账号断开。"""

async def restore_enabled() -> RestoreSummary:
    """恢复所有已启用账号，不改变管理员意图。"""

async def shutdown() -> None:
    """关闭实际连接，但保留数据库中的启用意图。"""
```

现有底层 `start()` 和 `stop()` 可以继续负责实际连接操作，但不得隐式混淆“修改期望状态”和“断开当前连接”。

### 7.2 手动启用流程

```text
1. 查询账号，确认存在。
2. 检查 Account.status == 1。
3. 保存 auto_reply_enabled=true。
4. 获取该账号 RuntimeRecord.lock。
5. 再次读取账号和期望状态。
6. 如果尚未连接，创建 PDDChannel 并启动。
7. 发布 runtime.status_changed。
```

如果第 6 步因为临时网络问题失败：

- 返回现有安全错误响应。
- 实际状态记录为 `error`。
- `auto_reply_enabled` 保持 `true`。
- 保存脱敏后的 `last_recovery_error`。
- 管理员可以手动重试，下一次服务重启也会再次尝试。

如果账号不存在或平台状态不是在线，应在写入 `true` 之前拒绝请求。

### 7.3 手动停用流程

```text
1. 保存 auto_reply_enabled=false。
2. 获取 RuntimeRecord.lock。
3. 停止 PDDChannel。
4. 清理内存 channel 引用。
5. 发布 disconnected 状态。
```

必须先保存 `false` 再停止连接。这样即使停止过程中程序崩溃，下一次启动也不会把管理员明确关闭的账号重新连接。

### 7.4 服务关闭流程

```text
1. 停止接收新的恢复操作。
2. 等待或取消正在进行的 restore task。
3. 逐个停止实际 PDDChannel。
4. 不修改 auto_reply_enabled。
5. 释放数据库和实例锁。
```

现有 `shutdown()` 调用的停止路径必须具备 `preserve_preference=True` 语义，或者调用完全独立的 `disconnect_all()`，避免复用管理员的“全部停用”操作。

## 8. 启动恢复流程

### 8.1 启动顺序

FastAPI `lifespan` 建议按以下顺序执行：

```text
1. 获取 InstanceLock。
2. 初始化依赖注入和数据库。
3. 创建 EventBroker、JobManager、AccountRuntimeManager。
4. 把依赖写入 app.state。
5. 安装日志缓冲。
6. 启动账号恢复任务。
7. 标记 API ready。
```

恢复任务不能因单个账号失败导致整个 FastAPI 启动失败。

### 8.2 恢复算法

```text
查询 auto_reply_enabled=true 的账号
        ↓
以有限并发逐个恢复
        ↓
对每个账号：
  ├─ 账号已删除：跳过并清理孤立偏好
  ├─ 平台状态不是在线：记录失败，保持期望状态
  ├─ Cookie 缺失或失效：记录失败，保持期望状态
  ├─ 用户已手动停用：锁内重读后跳过
  ├─ 已经连接：幂等跳过
  └─ 可以连接：调用底层 start()
        ↓
发布恢复汇总日志和状态事件
```

建议默认恢复并发为 3，避免程序启动时同时创建大量连接和 Agent 实例。该数值可以作为常量开始，不必在本次引入新的配置系统。

### 8.3 API 就绪语义

建议数据库和核心服务初始化完成后即可返回 ready，账号恢复在由 `AccountRuntimeManager` 跟踪的启动恢复协程中进行。这里的恢复协程只负责账号连接，不涉及 `JobManager` 中的业务后台任务。理由：

- 某个账号 Cookie 失效不应让整个管理后台不可用。
- 管理员需要进入界面查看并修复恢复失败的账号。
- 多账号串行连接可能显著延长启动时间。

账号列表应显示 connecting、connected 或 error，而不是让 `/health/ready` 等待全部账号成功。

### 8.4 初始失败重试

本次建议实现有界重试，而不是永久循环：

- 首次启动立即尝试。
- 对网络类临时错误分别在 2 秒、5 秒、15 秒后重试，最多 3 次。
- 账号不存在、平台离线、Cookie 无效等不可重试错误不自动重试。
- 达到上限后保持 `auto_reply_enabled=true`，实际状态为 error，等待管理员处理或下次服务重启。
- 运行中已经建立连接后的断线，继续使用 `PDDChannel` 现有自动重连机制。

## 9. API 行为

现有接口路径保持不变：

```text
POST /api/v1/runtimes/{account_id}/start
POST /api/v1/runtimes/{account_id}/stop
POST /api/v1/runtimes/actions/start-all
POST /api/v1/runtimes/actions/stop-all
GET  /api/v1/runtimes
```

语义调整：

- `start` 调用 `enable()`，成功保存启用意图后启动连接。
- `stop` 调用 `disable()`，先保存停用意图再断开连接。
- `start-all` 只为满足平台在线条件的账号保存启用意图并启动。
- `stop-all` 是管理员操作，应把相关账号全部保存为停用。
- 服务关闭不能调用具有管理员 `stop-all` 语义的方法。

建议在运行时响应中新增：

```json
{
  "auto_reply_enabled": true,
  "state": "error",
  "last_error": "账号登录已失效，请重新验证"
}
```

前端可以暂时忽略新增字段，因此本次不要求前端重构。后续可以用“已启用但连接异常”的状态替代单一的“未运行”。

## 10. 并发和竞态处理

### 10.1 恢复与手动停止同时发生

可能过程：恢复任务已查询到账号，管理员随后点击停止。

处理要求：

1. `disable()` 先把数据库改为 false。
2. 恢复逻辑获得账号锁后重新读取数据库。
3. 如果已经为 false，跳过连接。
4. 如果连接已经被恢复任务创建，`disable()` 随后在同一锁下将其关闭。

最终状态必须以数据库中的最新期望状态为准。

### 10.2 重复启动请求

继续利用现有 `RuntimeRecord.lock` 和状态判断，确保同一账号只创建一个 `PDDChannel`。

### 10.3 启动过程中服务关闭

`AccountRuntimeManager` 保存 restore task 引用。`shutdown()` 先阻止新恢复，再取消或等待该 task，然后关闭已经创建的连接。取消恢复不得修改数据库偏好。

## 11. 错误处理与日志

日志至少包含：

- 恢复开始和结束。
- 待恢复账号数量。
- 每个账号的内部数据库 ID和渠道名。
- 成功、跳过、失败数量。
- 安全错误分类，例如 platform_offline、credentials_invalid、network_error、shutdown。

日志不得包含：

- 密码。
- Cookie。
- API Key。
- 完整客户消息。
- 平台返回的敏感响应正文。

恢复一个账号失败时不得中断其他账号恢复，也不得导致 FastAPI 进程退出。

## 12. 数据兼容和回滚

### 12.1 升级

新版本首次启动时，SQLAlchemy `create_all()` 创建 `account_runtime_preferences`。已有账号没有偏好记录，默认不自动启动，不会产生意外连接。

### 12.2 回滚

旧版本会忽略新增表，可以继续读取原有账号数据。回滚前停止服务并备份 SQLite 文件。

新增表暂时保留，不需要在回滚时删除。删除表会丢失管理员的自动回复偏好，且对旧版本没有收益。

桌面端代码删除后，当前工作区不再提供 `python app.py` 回滚。需要回滚时使用删除桌面端之前的 Git tag、发布包或归档制品，不能临时恢复部分 `ui/` 文件。回滚制品必须提前验证能够读取当前数据库，并且启动前必须停止 Web 服务，避免重复连接账号。

### 12.3 桌面端正式下线

下线目标：

- FastAPI Web 是唯一应用入口。
- 删除 PyQt6 UI 和桌面启动入口。
- 删除只服务于桌面程序的打包和安装流程。
- 保留与渠道、Agent、数据库和 Web API 共用的业务模块。
- 保留 `InstanceLock`，用于阻止同一数据目录启动两个 Web 实例并重复持有账号连接。

删除前置条件：

1. Web 端已经覆盖账号管理、平台登录/校验、自动回复启停、关键词、知识库、日志和模型设置。
2. 本文定义的账号重启恢复已经通过自动化和人工验收。
3. `tests/test_setting_ui.py` 中仍有价值的模型配置和能力策略测试，已迁移到 `test_llm_config.py`、`test_web_server.py` 或新的非 UI 测试文件。
4. 已保存删除前的 Git tag 或可恢复归档，并验证数据库备份。
5. 已确认不再发布 Windows 桌面安装包，CI 和 Release 不会继续引用桌面构建脚本。

计划删除：

```text
app.py
ui/
tests/test_setting_ui.py             # 先迁移非 UI 测试后删除
scripts/agent_customer.spec
scripts/build_exe.py
scripts/build_win_exe.py
scripts/installer.iss
```

计划清理：

- 从 `pyproject.toml` 删除 `pyqt6` 和 `pyqt6-fluent-widgets`，重新生成 `uv.lock`。
- 删除 `utils/logger_loguru.py` 中只为 PyQt 信号服务的可选适配，保留普通 Loguru 功能。
- 删除 `utils/runtime_path.py` 中对 `app.py` 的调试引用。
- 更新 `core/di_container.py` 中“通常在 app.py 调用”等过时注释。
- 删除或改写 Windows 桌面安装包相关 GitHub Actions；CI 改为测试 Web 入口。
- 更新 `README.md`、`scripts/README.md`、部署文档和项目学习文档，不再说明桌面版是回滚入口。
- 历史设计文档可以保留，但必须明确标注为历史资料，不能继续作为当前启动说明。

必须保留：

- `Channel/`、`Message/`、`Agent/`、`bridge/`、`database/`、`service/` 和 Web 端共用工具。
- Playwright 依赖及拼多多登录逻辑，因为 Web 端账号登录仍需要浏览器自动化。
- `icon/icon.ico` 等仍被 Web favicon 或品牌展示使用的资源。
- `utils/instance_lock.py`，但其文档字符串应从“阻止桌面/Web 双开”改为“阻止同一数据目录的多个运行实例”。

删除后的验证命令：

```powershell
rg -n "PyQt6|qfluentwidgets|from ui|import ui|app\.py|agent_customer\.spec" `
  -g "*.py" -g "*.toml" -g "*.yml" -g "*.yaml" -g "*.spec" .
```

除明确标记的历史文档外，活动代码、测试、构建配置和启动说明中不得再出现桌面入口或 PyQt 依赖。

## 13. 计划修改文件

| 文件 | 计划修改 |
| --- | --- |
| `database/models.py` | 新增 `AccountRuntimePreference` 模型 |
| `database/db_manager.py` | 新增偏好查询、upsert 和恢复结果记录 |
| `service/account_service.py` | 暴露运行偏好服务方法 |
| `server/runtime.py` | 增加 enable、disable、restore_enabled，并分离管理员停用与关闭断连 |
| `server/api.py` | 保持路由不变，改用新的 enable/disable 语义 |
| `server/main.py` | 在 lifespan 中启动受管理的恢复任务 |
| `server/schemas.py` | 如需要，为恢复汇总或新增响应字段补充 schema |
| `tests/test_web_server.py` | 增加运行时恢复、失败隔离和关闭语义测试 |
| `tests/test_regressions.py` | 增加数据库兼容和连接唯一性回归测试 |
| `tests/test_setting_ui.py` | 迁移其中非 UI 的模型策略测试后删除 |
| `pyproject.toml`、`uv.lock` | 删除 PyQt6 和 qfluentwidgets 依赖并更新锁文件 |
| `utils/logger_loguru.py` | 删除 Qt 信号适配，保留通用日志实现 |
| `utils/runtime_path.py` | 删除桌面入口引用 |
| `utils/instance_lock.py` | 保留锁功能，更新为 Web 单实例语义 |
| `app.py`、`ui/` | 删除桌面入口及全部桌面 UI |
| `scripts/agent_customer.spec` 等桌面构建文件 | 删除桌面 PyInstaller/Inno Setup 流程 |
| README、部署文档和 CI | 删除桌面启动、回滚和发布说明，改为 Web-only |

明确不修改：

- `bridge/context.py`
- `Message/core/queue.py`
- `Message/core/consumer.py`
- 前端目录
- LLM 与 Agent 消息处理逻辑

## 14. 自动化测试

### 14.1 数据库测试

1. 旧数据库启动后能自动创建偏好表。
2. 没有偏好记录的账号默认 false。
3. 启用和停用可以重复调用。
4. 删除账号后偏好记录级联删除。
5. 恢复错误中不包含敏感字段。

### 14.2 Runtime 单元测试

1. `enable()` 保存 true 并只创建一个连接。
2. 平台离线时不保存 true。
3. 建立连接失败后偏好仍为 true。
4. `disable()` 先保存 false，再停止连接。
5. `shutdown()` 停止连接但保留 true。
6. 恢复只处理已启用账号。
7. 一个账号失败不影响其他账号。
8. 已连接账号不会被恢复两次。
9. 恢复与手动停止竞态最终以 false 为准。
10. 关闭过程中恢复任务能够有界退出。

### 14.3 重启集成测试

使用临时 SQLite 数据库和 FakeChannel：

```text
进程生命周期 A
  ├─ 创建账号
  ├─ enable(account_id)
  ├─ 确认偏好为 true
  └─ shutdown()，确认偏好仍为 true

进程生命周期 B
  ├─ 使用同一数据库创建新的 RuntimeManager
  ├─ restore_enabled()
  └─ 确认 FakeChannel 被创建且账号进入 connected
```

再验证生命周期 A 中执行 `disable()` 后，生命周期 B 不会创建连接。

### 14.4 全量回归

```powershell
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q server database service Channel
```

## 15. 人工验收

1. 启动一个平台在线账号并确认进入 connected。
2. 正常停止 FastAPI，再重新启动。
3. 不进行人工操作，确认该账号自动经历 connecting 并回到 connected。
4. 点击停止账号，重启 FastAPI，确认账号保持 disconnected。
5. 启动两个以上账号，重启后确认每个账号只有一条连接。
6. 让一个账号 Cookie 失效，确认其他账号仍能恢复，失败账号显示安全错误。
7. 恢复进行时点击停止，确认最终不会再次连接。
8. 检查日志和数据库，确认没有密码、Cookie 或完整消息内容。
9. 确认 `app.py`、`ui/`、桌面打包脚本和 PyQt 依赖已经删除。
10. 使用 Web 端完成账号登录、设置保存、知识库维护和自动回复启停，确认没有功能依赖桌面 UI。
11. 尝试启动第二个 Web 实例，确认 `InstanceLock` 仍能阻止重复账号所有权。

## 16. 完成标准

以下条件全部满足才视为完成：

- 管理员启用意图已持久化，并与平台在线状态分离。
- 正常重启和异常退出后的下一次启动都能恢复已启用账号。
- 管理员主动停止的账号不会在重启后复活。
- 单账号恢复失败不会阻止服务启动或其他账号恢复。
- 服务关闭不会清除启用意图。
- 同一账号不会产生重复连接。
- 旧数据库可以无损升级和回滚。
- `context.py`、消息解析和 Agent 处理链保持不变。
- FastAPI Web 已成为唯一入口，桌面 UI、PyQt 依赖和桌面构建流程已删除。
- 原 `test_setting_ui.py` 中与模型配置有关的有效测试已迁移，而不是随 UI 一起丢失。
- CI、README 和部署文档不再引用桌面启动或桌面安装包。
- 自动化测试和人工验收全部通过。

## 17. 实施记录

2026-10-11 已完成代码实施：

- 新增 `account_runtime_preferences`，持久化自动回复启用意图和安全恢复结果。
- API 启停已改为 `enable()` / `disable()` 语义；应用关闭只断开实际连接，不清除启用意图。
- FastAPI lifespan 已启动受管理的有限并发恢复任务，并实现 2 秒、5 秒、15 秒有界重试。
- 已覆盖旧数据库建表、幂等启停、失败隔离、停止竞态、关闭保留意图和跨生命周期恢复测试。
- 已删除 PyQt6 桌面入口、`ui/`、桌面测试和安装包构建流程；Playwright 与 Web 单实例锁保留。
- `uv.lock` 已重新生成，最终环境无法导入 PyQt6 或 qfluentwidgets。
- 全量自动化测试 70 项通过，项目模块编译检查通过，Web 健康检查返回 ready。

仍需在真实拼多多账号环境执行第 15 节人工验收，特别是有效 Cookie 恢复、失效 Cookie 隔离和真实消息收发。后台任务断点恢复仍明确不在本次范围。
