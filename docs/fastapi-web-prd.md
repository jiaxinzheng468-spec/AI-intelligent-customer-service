---
title: Agent-Customer FastAPI Web 版 PRD 与开发规格
version: 1.0
status: Historical
date: 2026-10-07
owner: Agent-Customer
---

> 历史设计资料：Web 迁移已经完成，当前唯一受支持的入口是 FastAPI Web。文中的桌面迁移与回滚描述仅保留决策背景，不再作为当前启动或发布说明。

# Agent-Customer FastAPI Web 版 PRD 与开发规格

## 1. 文档目的

本文档定义 Agent-Customer 从 PyQt6 桌面应用迁移为 FastAPI Web 应用的产品范围、交互流程、技术边界、接口契约、验收标准与实施阶段，可直接作为产品、前端、后端和测试的共同开发依据。

本次改造的核心原则是：保留现有渠道接入、消息队列、AI Agent、知识库和数据库能力，用浏览器管理界面替换 PyQt6 表现层，并将 Qt 线程生命周期替换为 FastAPI 管理的异步后台任务生命周期。

## 2. 产品概述

### 2.1 产品名称

Agent-Customer Web

### 2.2 产品定位

面向电商商家的 AI 客服 Web 管理系统。管理员通过浏览器管理拼多多店铺账号、启停 AI 自动回复、维护转人工关键词与知识库、配置大模型，并实时查看账号连接状态、同步任务和运行日志。

### 2.3 当前问题

当前产品以 Windows 桌面应用交付，存在以下限制：

- 必须在安装了桌面客户端的机器上操作。
- 管理界面、后台任务和 Qt 线程生命周期耦合。
- 无法方便地通过局域网或远程浏览器管理。
- PyInstaller 打包体积较大，升级和运维成本较高。
- 后续增加多用户、权限、审计和集中部署能力较困难。

### 2.4 产品目标

- 使用浏览器完成当前桌面版的核心管理操作。
- 保持现有拼多多消息接收、AI 回复、工具调用和自动重连行为不变。
- 自动回复与浏览器会话解耦，关闭浏览器不影响后台运行。
- 提供明确、实时的账号连接状态和后台任务进度。
- 建立稳定的 REST API，为后续移动端或第三方系统接入提供基础。
- 首版在单机、单进程模式下可靠运行，并保留未来多实例扩展路径。

### 2.5 成功指标

- 核心功能验收用例通过率为 100%。
- 自动回复连续运行 24 小时，无重复连接、任务泄漏或服务崩溃。
- 账号启停指令在 2 秒内返回受理结果，最终连接状态可实时更新。
- Web 管理端主要页面在常规数据量下 2 秒内完成首屏展示。
- API、页面和日志中不泄露密码、Cookie、API Key 或完整授权头。
- 与桌面版使用相同账号和知识数据时，AI 消息处理结果保持兼容。

## 3. 用户与使用场景

### 3.1 首版用户角色

首版仅包含一个角色：系统管理员。

系统管理员可以：

- 登录 Web 管理后台。
- 管理店铺和客服账号。
- 设置账号在线或离线状态。
- 启动、停止单个或全部账号的自动回复。
- 管理转人工关键词。
- 管理商品知识和客服知识。
- 发起、取消商品知识同步。
- 配置营业时间、LLM 和 Agent 指令。
- 查看和导出运行日志。

多租户、细粒度角色权限和只读用户不在首版范围内。

### 3.2 典型场景

1. 管理员登录后台，确认 LLM 配置有效。
2. 管理员添加拼多多账号，系统通过 Playwright 登录并保存店铺、账号和 Cookie。
3. 管理员将账号设置为在线，并启动自动回复。
4. 后端持续维护拼多多 WebSocket，浏览器关闭后连接仍保持运行。
5. 客户发送消息后，现有消息队列、关键词处理器和 CustomerAgent 完成回复。
6. 管理员重新打开后台，查看实时连接状态和运行日志。

## 4. 范围定义

### 4.1 MVP 范围

- 单管理员认证。
- 自动回复监控与启停。
- 拼多多账号添加、验证、编辑、删除。
- 账号平台在线/离线设置。
- 转人工关键词增删改查和批量导入。
- 商品知识查询、详情、删除、清空和同步。
- 客服知识增删改查、启停、标签筛选和批量导入。
- 营业时间、LLM、Agent 指令配置。
- 实时日志查看、筛选、清空当前视图和导出。
- 后端健康检查、优雅启动与停止。
- Windows 单节点部署，FastAPI 单 worker 运行。

### 4.2 非目标

- 不修改现有 CustomerAgent 的工具决策逻辑。
- 不新增淘宝、京东、抖音等渠道。
- 不实现订单退款、支付、仓储或工单系统。
- 不实现多租户、组织、计费或套餐功能。
- 不在首版实现 FastAPI 多 worker 或多服务器部署。
- 不在首版将 SQLite 强制迁移为 PostgreSQL。
- 不在首版支持 Linux 上的旧 DPAPI 密文迁移。
- 不保留 PyQt6 与 Web 两套界面的长期并行维护。
- 不提供公网开放注册。

### 4.3 首版部署假设

- 服务运行在 Windows 主机，并由固定的 Windows 用户启动。
- 继续使用现有 SQLite 数据库和 DPAPI 密钥保护。
- 默认仅监听 `127.0.0.1`；开启局域网访问必须显式配置监听地址和 HTTPS 反向代理。
- Uvicorn 只启动一个 worker。
- Playwright 登录发生在服务器主机上；需要可见浏览器时，服务器必须具备可交互桌面会话。

## 5. 信息架构

Web 管理后台包含以下一级页面：

| 页面 | 路由 | 主要用途 |
| --- | --- | --- |
| 登录 | `/login` | 管理员认证 |
| 自动回复 | `/auto-reply` | 账号状态、批量启停和实时监控 |
| 账号管理 | `/accounts` | 添加、验证、编辑和删除账号 |
| 关键词管理 | `/keywords` | 维护转人工关键词 |
| 知识库 | `/knowledge` | 管理商品知识和客服知识 |
| 日志 | `/logs` | 查看、筛选和导出日志 |
| 设置 | `/settings` | 配置营业时间、LLM 和 Agent 指令 |

默认登录后进入 `/auto-reply`。桌面版“联系我们”二维码不迁移到首版。

## 6. 功能需求

### 6.1 身份认证

#### FR-AUTH-001 管理员登录

- 系统必须提供用户名和密码登录。
- 首版管理员凭据通过环境变量或部署时初始化，不允许内置通用默认密码。
- 登录成功后使用 `HttpOnly`、`Secure`、`SameSite` Cookie 保存会话。
- 登录失败只返回通用错误，不暴露用户名是否存在。
- 连续失败需要进行速率限制。

#### FR-AUTH-002 会话管理

- 管理员可以主动退出。
- 会话过期后，前端必须跳转登录页。
- 所有业务 API 默认要求认证。
- 修改类请求必须具备 CSRF 防护。

#### FR-AUTH-003 密钥展示

- API 不得返回账号密码、完整 Cookie 或完整 API Key。
- 设置页只能显示 API Key 是否已配置及掩码值。
- API Key 输入为空时表示保留原值；显式清除必须使用单独操作并再次确认。

### 6.2 自动回复监控

#### FR-RUNTIME-001 账号列表

- 展示账号名称、店铺、平台状态、自动回复状态、连接时间、重连次数和最近错误。
- 支持按店铺、平台状态、运行状态和关键词筛选。
- 展示账号总数、在线账号数、连接中账号数、自动回复运行数和错误数。

#### FR-RUNTIME-002 启动自动回复

- 只有平台状态为在线的账号允许启动自动回复。
- 同一账号同时只能存在一个 `PDDChannel` 实例和一条有效连接链路。
- 重复启动请求必须幂等，不得创建第二条连接。
- 启动请求返回后，状态依次可表现为 `connecting`、`connected`、`reconnecting` 或 `error`。
- 连接失败必须返回安全、可行动的错误描述，不返回 Cookie、Token 或服务端堆栈。

#### FR-RUNTIME-003 停止自动回复

- 停止操作必须关闭 WebSocket、重连任务、心跳任务、Cookie 健康检查任务、消息消费者和账号级 Agent 资源。
- 重复停止已停止账号必须幂等。
- 停止超时必须将状态标记为错误并记录原因，不得立即允许竞争性重启。

#### FR-RUNTIME-004 批量启停

- “全部启动”只处理平台状态在线且当前未运行的账号。
- “全部停止”只处理当前运行、连接中或重连中的账号。
- 前端必须先展示受影响账号数量并要求确认。
- 批量操作结果需要返回成功、跳过和失败明细。

#### FR-RUNTIME-005 实时状态

- 后端通过 WebSocket 推送账号状态、同步任务和系统事件。
- WebSocket 断开后，前端使用指数退避重连，并在重连成功后重新拉取完整状态快照。
- WebSocket 只用于事件通知，最终状态以 REST 查询结果为准。

### 6.3 平台状态管理

#### FR-STATUS-001 设置在线状态

- 管理员可以将单个账号设置为平台在线。
- 平台 API 调用成功后才更新本地状态。
- 调用失败时保留原状态并显示错误。

#### FR-STATUS-002 设置离线状态

- 设置离线前，如果自动回复正在运行，系统必须先停止自动回复。
- 自动回复未成功停止时，不得继续设置离线。
- 平台 API 调用成功后更新本地状态。

### 6.4 账号管理

#### FR-ACCOUNT-001 添加账号

- 管理员输入拼多多用户名和密码后发起登录任务。
- 登录属于长耗时操作，API 必须返回任务 ID，不得占用普通请求直到完成。
- 登录成功后，系统读取店铺和用户信息，并以事务方式保存店铺、账号和 Cookie。
- 已存在的账号不得重复创建。
- 登录失败不得保存不完整账号。
- 密码输入后不得在日志、任务结果或错误响应中出现。

#### FR-ACCOUNT-002 登录任务状态

- 任务状态包含 `queued`、`running`、`succeeded`、`failed`、`cancelled`。
- 页面实时显示登录进度和最终结果。
- 任务失败只保存安全错误摘要，详细异常进入服务端脱敏日志。

#### FR-ACCOUNT-003 验证账号

- 管理员可以验证已保存账号的 Cookie 和平台身份。
- 验证成功后更新 Cookie 和本地平台状态。
- 验证失败时将账号标记为需要重新登录，不自动删除账号。

#### FR-ACCOUNT-004 编辑账号

- 支持修改账号显示名称、密码和本地平台状态。
- 密码为空时保留原值。
- 正在运行自动回复的账号不得修改身份字段。

#### FR-ACCOUNT-005 删除账号

- 删除前必须二次确认。
- 正在运行的账号必须先完成停止操作。
- 删除失败时数据库不得留下部分更新。

### 6.5 关键词管理

#### FR-KEYWORD-001 列表与统计

- 展示全部转人工关键词和总数。
- 支持搜索和分页。

#### FR-KEYWORD-002 增删改

- 关键词去除首尾空白后不能为空。
- 同一标准化关键词不得重复。
- 编辑和删除成功后实时刷新列表。

#### FR-KEYWORD-003 批量导入

- 支持粘贴多行文本导入，一行一个关键词。
- 空行和重复项跳过。
- 返回新增、跳过和失败数量。

### 6.6 知识库管理

#### FR-KNOWLEDGE-001 店铺选择

- 知识库操作必须先选择店铺。
- 商品知识和客服知识均严格按店铺隔离。

#### FR-KNOWLEDGE-002 商品知识

- 展示商品 ID、名称、价格、销量、更新时间和提取状态。
- 支持分页、搜索、查看详情和删除单条知识。
- 商品详情允许人工编辑规格和提取内容。
- 清空店铺全部商品知识属于高风险操作，必须二次确认并显示影响数量。

#### FR-KNOWLEDGE-003 商品同步

- 支持增量同步和全量同步。
- 增量同步只处理本地不存在的商品。
- 全量同步覆盖已提取知识，必须额外确认。
- 同一店铺同时只允许一个同步任务。
- 任务需要展示当前阶段、当前商品、总数、成功数、失败数和取消状态。
- 管理员可以取消运行中的同步；取消后已提交的数据保留。
- 页面关闭或浏览器断开不影响服务端同步任务。

#### FR-KNOWLEDGE-004 客服知识

- 支持标题、内容、标签和启用状态的增删改查。
- 支持按标签和启用状态筛选。
- 支持 CSV、XLSX 文件批量导入。
- 导入文件必须限制类型和大小，并对每一行进行校验。
- 重复的“标题 + 内容”记录跳过并计入结果。

### 6.7 系统设置

#### FR-SETTINGS-001 营业时间

- 支持配置开始时间和结束时间，格式为 `HH:MM`。
- 保存前使用现有 Pydantic 规则校验。

#### FR-SETTINGS-002 LLM 配置

- 支持 DeepSeek、火山引擎、OpenAI-compatible、Kimi/Moonshot、智谱/Z.AI 和 Qwen/DashScope。
- 支持供应商、模型、API Key、Base URL、端点信任模式和工具调用策略。
- 保留当前的端点安全校验、能力确认和工具调用兼容性规则。
- 保存失败时保留原有效配置。
- 修改后的 LLM 配置只影响之后启动或重启的账号，不热替换运行中账号的配置快照。

#### FR-SETTINGS-003 Agent 指令

- 支持按行编辑系统行为指令。
- 空行保存时忽略。
- 前端显示当前有效指令，不显示内部工具身份字段。

#### FR-SETTINGS-004 并发写保护

- 设置更新请求携带版本号或 ETag。
- 当配置已被其他请求修改时返回 `409 Conflict`，不得静默覆盖。

### 6.8 日志与诊断

#### FR-LOG-001 实时日志

- 支持实时查看 `DEBUG`、`INFO`、`WARNING`、`ERROR` 级别日志。
- 默认不向前端推送敏感字段和异常堆栈。
- 支持按级别、模块、账号标识、时间和关键词筛选。

#### FR-LOG-002 日志保留与导出

- Web 实时缓冲区默认保留最近 10,000 条脱敏日志。
- 支持导出当前筛选结果为 JSON、CSV 或 TXT。
- “清空”只清空当前 Web 缓冲区，不删除服务端持久化日志文件。

#### FR-LOG-003 健康检查

- `/health/live` 只表示进程存活。
- `/health/ready` 检查数据库、配置加载和运行时管理器是否可用。
- 健康检查不得返回配置值、数据库路径或外部凭据。

## 7. 核心状态模型

### 7.1 自动回复状态

| 状态 | 含义 | 允许操作 |
| --- | --- | --- |
| `disconnected` | 未运行或已正常停止 | 启动 |
| `connecting` | 正在建立连接 | 停止 |
| `connected` | WebSocket 已连接 | 停止 |
| `reconnecting` | 断线后自动重连 | 停止 |
| `stopping` | 正在释放资源 | 等待 |
| `error` | 启动或运行失败 | 查看错误、确认清理后重试 |

状态迁移：

```text
disconnected -> connecting -> connected
                       |          |
                       v          v
                     error <- reconnecting
                       |          |
                       +----> stopping -> disconnected
```

### 7.2 后台任务状态

适用于账号登录和商品同步：

```text
queued -> running -> succeeded
                 |-> failed
                 |-> cancelled
```

任务 ID 使用不可预测的 UUID。任务结果不得包含任何凭据。

## 8. API 设计

### 8.1 通用约定

- API 前缀：`/api/v1`。
- 请求和响应使用 JSON；文件上传使用 `multipart/form-data`。
- 时间使用 ISO 8601，并包含时区。
- 列表接口使用 `page`、`page_size`，默认每页 20 条，最大 100 条。
- 修改操作返回更新后的资源或明确操作结果。
- 长耗时任务返回 `202 Accepted` 和任务 ID。
- 所有响应包含 `X-Request-ID`。
- 不直接将 Python 异常文本返回前端。

统一错误结构：

```json
{
  "error": {
    "code": "ACCOUNT_NOT_ONLINE",
    "message": "账号必须先上线才能启动自动回复",
    "request_id": "01J...",
    "details": {}
  }
}
```

### 8.2 主要端点

#### 认证与健康

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/auth/login` | 管理员登录 |
| `POST` | `/auth/logout` | 退出登录 |
| `GET` | `/auth/me` | 当前会话信息 |
| `GET` | `/health/live` | 存活检查 |
| `GET` | `/health/ready` | 就绪检查 |

#### 账号与店铺

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/shops` | 店铺列表 |
| `GET` | `/accounts` | 账号列表和筛选 |
| `POST` | `/account-login-jobs` | 发起 Playwright 登录并添加账号 |
| `POST` | `/accounts/{account_id}/verify` | 验证账号 |
| `PATCH` | `/accounts/{account_id}` | 编辑账号 |
| `DELETE` | `/accounts/{account_id}` | 删除账号 |
| `PUT` | `/accounts/{account_id}/platform-status` | 设置平台在线/离线 |

#### 自动回复

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/runtimes` | 所有账号运行状态快照 |
| `GET` | `/runtimes/{account_id}` | 单账号运行状态 |
| `POST` | `/runtimes/{account_id}/start` | 启动自动回复 |
| `POST` | `/runtimes/{account_id}/stop` | 停止自动回复 |
| `POST` | `/runtimes/actions/start-all` | 启动全部符合条件账号 |
| `POST` | `/runtimes/actions/stop-all` | 停止全部运行账号 |

#### 关键词

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/keywords` | 关键词列表 |
| `POST` | `/keywords` | 新增关键词 |
| `PATCH` | `/keywords/{keyword_id}` | 编辑关键词 |
| `DELETE` | `/keywords/{keyword_id}` | 删除关键词 |
| `POST` | `/keywords/import` | 批量导入关键词 |

#### 知识库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/shops/{shop_id}/product-knowledge` | 商品知识列表 |
| `GET` | `/product-knowledge/{product_id}` | 商品知识详情 |
| `PATCH` | `/product-knowledge/{product_id}` | 编辑商品知识 |
| `DELETE` | `/product-knowledge/{product_id}` | 删除商品知识 |
| `DELETE` | `/shops/{shop_id}/product-knowledge` | 清空商品知识 |
| `POST` | `/shops/{shop_id}/product-sync-jobs` | 发起增量或全量同步 |
| `POST` | `/jobs/{job_id}/cancel` | 取消后台任务 |
| `GET` | `/shops/{shop_id}/customer-knowledge` | 客服知识列表 |
| `POST` | `/shops/{shop_id}/customer-knowledge` | 新增客服知识 |
| `PATCH` | `/customer-knowledge/{knowledge_id}` | 编辑客服知识 |
| `DELETE` | `/customer-knowledge/{knowledge_id}` | 删除客服知识 |
| `POST` | `/shops/{shop_id}/customer-knowledge/import` | 批量导入客服知识 |

#### 设置、任务、日志和事件

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/settings` | 获取脱敏配置 |
| `PUT` | `/settings` | 校验并保存配置 |
| `GET` | `/jobs/{job_id}` | 查询任务状态 |
| `GET` | `/logs` | 分页查询日志缓冲区 |
| `GET` | `/logs/export` | 导出筛选后的日志 |
| `DELETE` | `/logs/buffer` | 清空 Web 日志缓冲区 |
| `WS` | `/events` | 实时状态、任务和日志事件 |

### 8.3 WebSocket 事件格式

```json
{
  "event_id": "01J...",
  "type": "runtime.status_changed",
  "occurred_at": "2026-10-07T10:30:00+08:00",
  "data": {
    "account_id": 12,
    "state": "connected",
    "last_error": null
  }
}
```

首版事件类型：

- `runtime.status_changed`
- `runtime.connection_failed`
- `job.progress_changed`
- `job.finished`
- `log.appended`
- `system.shutdown_requested`

前端必须忽略未知事件类型，以支持后端向前兼容扩展。

## 9. 后端技术设计

### 9.1 推荐目录

```text
server/
├── main.py                 # FastAPI 应用工厂
├── lifespan.py             # 启动和优雅停止
├── api/
│   ├── dependencies.py     # 认证、数据库和服务依赖
│   └── v1/
│       ├── auth.py
│       ├── accounts.py
│       ├── runtimes.py
│       ├── keywords.py
│       ├── knowledge.py
│       ├── settings.py
│       ├── jobs.py
│       ├── logs.py
│       └── events.py
├── schemas/                # API Pydantic 模型
├── services/
│   ├── runtime_manager.py  # 账号连接生命周期
│   ├── job_manager.py      # 登录和同步任务
│   ├── event_broker.py     # 进程内事件广播
│   ├── auth_service.py
│   └── log_buffer.py
└── security/
    ├── sessions.py
    ├── csrf.py
    └── redaction.py

web/
├── src/
│   ├── api/
│   ├── components/
│   ├── pages/
│   ├── router/
│   ├── stores/
│   └── types/
└── dist/                   # 生产构建结果，可由 FastAPI 托管
```

### 9.2 应用生命周期

FastAPI `lifespan` 启动顺序：

1. 加载并验证配置。
2. 调用 `configure_standard_services()` 注册现有服务。
3. 初始化数据库并检查 SQLite 可写性。
4. 初始化 `AccountRuntimeManager`、`JobManager`、`EventBroker` 和日志缓冲区。
5. 注册日志脱敏 Sink。
6. 将应用标记为 ready。

停止顺序：

1. 将应用标记为 not ready，拒绝新的启动和同步任务。
2. 广播服务关闭事件。
3. 停止所有账号连接并等待资源清理。
4. 取消仍在运行的后台任务。
5. 关闭事件连接和日志 Sink。
6. 释放数据库引擎和 DI 容器。

### 9.3 AccountRuntimeManager

该服务替换现有 `AutoReplyManager`、`AutoReplyThread` 和 Qt 信号。

每个运行项至少保存：

```text
account_id
account_key
PDDChannel instance
state
asyncio.Lock
started_at
last_error
```

约束：

- 使用不可变账号身份 `channel_name + shop_id + user_id` 生成内部 key。
- 每个账号使用独立 `PDDChannel`、消息队列、消费者和 CustomerAgent。
- 使用账号级 `asyncio.Lock` 串行化启动、停止和删除。
- 运行项只存在于服务进程内，应用重启后默认不自动恢复连接。
- `PDDChannel` 的成功和失败回调转换为状态事件，不再依赖 Qt 信号。
- 停止完成前保留运行项，防止竞争性重复启动。

### 9.4 JobManager

- 管理 Playwright 登录和商品同步等长耗时任务。
- 首版任务状态保存在内存，最多保留最近 500 个终态任务或 24 小时。
- 同一账号只允许一个登录/验证任务，同一店铺只允许一个商品同步任务。
- 商品同步全局并发默认不超过 2，避免 LLM 和平台 API 限流。
- 任务执行不依赖发起请求或浏览器连接继续存在。
- 服务重启时未完成任务记为中断，不自动重放。

### 9.5 同步代码与事件循环

现有 SQLAlchemy 服务和部分拼多多 HTTP API 是同步代码。开发时必须遵守：

- 普通同步 CRUD 路由使用 FastAPI 同步端点或线程池。
- 异步生命周期内调用同步网络或数据库操作时使用 `asyncio.to_thread()`。
- 不得在主事件循环中直接执行长时间同步 I/O。
- 不得在请求处理器中创建新的全局事件循环。

### 9.6 单 worker 约束

首版运行命令必须等价于：

```bash
uvicorn server.main:app --host 127.0.0.1 --port 8000 --workers 1
```

原因：账号连接、任务表、状态管理和事件广播均为进程内状态。部署脚本必须阻止或明确告警 `workers > 1`。

多实例演进需要至少引入：

- PostgreSQL 持久化业务与任务状态。
- Redis 分布式锁、状态缓存和事件广播。
- 独立连接 Worker，并为账号分配唯一所有者。
- API 服务与长连接服务之间的命令队列。

## 10. 前端技术设计

### 10.1 推荐技术栈

- Vue 3
- TypeScript
- Vite
- Vue Router
- Pinia
- 原生 Fetch 或 Axios
- 成熟的企业后台组件库

首版前端采用桌面优先的管理后台布局，同时保证 1024px 以上常规浏览器可用。不将复杂管理表格强行压缩到手机布局。

### 10.2 交互规范

- 启动、停止、删除和全量同步操作必须有清晰的 loading 状态。
- 同一资源操作中禁用冲突按钮，避免重复提交。
- 成功提示简短；失败提示必须包含用户可执行的下一步。
- 列表刷新不得覆盖仍在编辑的表单。
- 浏览器刷新后通过 REST 快照恢复页面，不依赖前端内存状态。
- 实时连接断开时显示非阻塞状态提示，并自动重连。
- 密码、API Key 和 Cookie 不存入 `localStorage`、Pinia 持久化或浏览器日志。

## 11. 数据与迁移

### 11.1 现有数据复用

首版继续复用以下表：

- `channels`
- `shops`
- `accounts`
- `keywords`
- `product_knowledge`
- `customer_service_knowledge`

现有 SQLite 文件在迁移前必须备份。任何 schema 变更必须使用可回滚的迁移脚本，不允许在应用启动时进行不可追踪的破坏性修改。

### 11.2 API 标识

- 对外 API 优先使用数据库整数主键，如 `account_id`、`shop_id` 和 `knowledge_id`。
- 平台店铺 ID 和用户 ID 作为业务字段返回，不直接作为所有资源 URL 的唯一标识。
- 内部连接 key 继续使用渠道、平台店铺 ID 和平台用户 ID 组合。

### 11.3 配置迁移

- 继续读取现有 `config.json`。
- 保存时保持当前原子替换机制。
- API 响应使用脱敏配置模型，不直接序列化内部配置字典。
- Windows 首版继续使用当前用户 DPAPI；服务必须由可解密旧数据的同一 Windows 用户运行。

## 12. 安全要求

### 12.1 网络边界

- 默认绑定回环地址。
- 局域网或公网部署必须使用 HTTPS。
- CORS 默认关闭；前后端同源部署。
- 反向代理必须限制请求体大小和连接速率。

### 12.2 凭据保护

- 密码、Cookie、Token 和 API Key 不得进入响应、日志、指标或异常详情。
- 所有日志写入前经过统一脱敏器。
- 前端表单不回显账号密码。
- 文件导出不得包含敏感凭据。
- 不允许将密钥放入 URL 查询参数。

### 12.3 输入与文件安全

- 所有请求使用 Pydantic 模型校验长度、格式和枚举值。
- 上传文件限制扩展名、MIME、大小和行数。
- 文件解析在受控临时目录中执行，完成后删除。
- 服务端不信任前端提交的店铺归属、账号状态或任务所有权。
- 自定义 LLM 端点继续执行 SSRF、HTTPS 和重定向安全校验。

### 12.4 操作安全

- 删除账号、清空知识、全量同步和全部停止必须二次确认。
- API 对重复请求提供幂等行为或使用幂等键。
- 所有修改操作记录操作者、时间、资源和结果；审计记录不得含秘密。

## 13. 非功能需求

### 13.1 性能

- 100 个账号的状态列表接口目标响应时间小于 500ms，不包含外部平台请求。
- 单页默认最多返回 20 条，最大 100 条。
- 日志 WebSocket 必须具备背压策略；慢客户端不得拖慢业务处理。
- WebSocket 客户端队列超过上限时允许丢弃低级别日志，但不得丢弃账号状态和任务终态事件。

### 13.2 稳定性

- 单个账号连接失败不得影响其他账号。
- 单个同步任务失败不得导致 FastAPI 进程退出。
- 进程收到关闭信号后应在 15 秒内完成常规资源清理。
- 关闭超时后记录未清理资源并由进程管理器终止，不无限等待。

### 13.3 可观测性

- 日志包含 request ID、模块和非敏感账号标识。
- 记录 API 延迟、活跃连接数、重连次数、任务成功/失败数和消息处理错误数。
- 首版指标可先通过内部状态接口提供，后续接入 Prometheus。

### 13.4 兼容性

- 后端运行环境：Python 3.11+。
- 首版服务端：Windows 10/11 或 Windows Server，能够运行本机 Chrome/Edge。
- 浏览器：最近两个主版本的 Chrome、Edge 和 Firefox。

### 13.5 可维护性

- API schema 自动生成 OpenAPI 文档。
- 业务服务不得导入 `ui` 或 PyQt6。
- 新增后端代码不得依赖 Qt 事件循环或 Qt 信号。
- 核心状态迁移和安全规则必须有自动化测试。

## 14. 错误码

| 错误码 | HTTP 状态 | 含义 |
| --- | --- | --- |
| `AUTH_INVALID_CREDENTIALS` | 401 | 登录凭据错误 |
| `AUTH_SESSION_EXPIRED` | 401 | 会话过期 |
| `ACCOUNT_NOT_FOUND` | 404 | 账号不存在 |
| `ACCOUNT_NOT_ONLINE` | 409 | 账号未上线，不能启动自动回复 |
| `ACCOUNT_RUNTIME_BUSY` | 409 | 账号正在启动、停止或清理 |
| `ACCOUNT_ALREADY_EXISTS` | 409 | 账号已存在 |
| `RUNTIME_START_FAILED` | 502 | 连接启动失败 |
| `RUNTIME_STOP_TIMEOUT` | 504 | 停止和资源清理超时 |
| `JOB_ALREADY_RUNNING` | 409 | 同类任务已在运行 |
| `JOB_NOT_FOUND` | 404 | 任务不存在或已过期 |
| `CONFIG_CONFLICT` | 409 | 配置版本冲突 |
| `CONFIG_INVALID` | 422 | 配置校验失败 |
| `UPLOAD_INVALID` | 422 | 上传文件无效 |
| `PROVIDER_UNAVAILABLE` | 502 | 外部平台或 LLM 暂不可用 |
| `INTERNAL_ERROR` | 500 | 未分类服务端错误 |

## 15. 验收标准

### 15.1 自动回复

- Given 账号平台状态为在线且未运行，When 管理员启动自动回复，Then API 返回受理结果，页面最终显示 `connected`。
- Given 同一账号已连接，When 连续发送两次启动请求，Then 只存在一个 `PDDChannel` 和一条连接链路。
- Given 账号正在自动回复，When 管理员停止，Then WebSocket、重连、心跳、健康检查和消费者任务均被清理。
- Given 浏览器关闭，When 后端仍在运行，Then 自动回复连接继续工作。
- Given FastAPI 收到关闭信号，When 进入 lifespan shutdown，Then 所有账号执行停止并释放数据库资源。

### 15.2 账号管理

- Given 正确拼多多凭据，When 添加账号任务完成，Then 店铺、账号和 Cookie 一次性写入数据库。
- Given 登录失败，When 任务结束，Then 不产生半成品店铺或账号记录。
- Given 响应和日志，Then 不包含明文密码、Cookie 和 API Key。
- Given 账号正在运行，When 删除账号，Then 系统拒绝删除或先完成安全停止。

### 15.3 知识库

- Given 店铺存在商品，When 执行增量同步，Then 只同步本地不存在的商品并实时报告进度。
- Given 同一店铺已有同步任务，When 再次发起，Then 返回 `JOB_ALREADY_RUNNING`。
- Given 同步任务运行中，When 管理员取消，Then 任务进入 `cancelled`，已保存数据保留。
- Given 客服知识导入文件含重复行，When 导入完成，Then 返回新增和跳过数量且不产生重复记录。

### 15.4 配置与安全

- Given API Key 已保存，When 获取设置，Then 只返回已配置状态和掩码。
- Given 设置请求中的 API Key 为空，When 保存其他字段，Then 原 API Key 保持不变。
- Given 未认证请求，When 访问任一业务 API，Then 返回 401。
- Given 非 HTTPS 的非本地自定义 LLM 端点，When 保存，Then 按现有安全规则拒绝。
- Given 两个基于同一版本的设置更新，When 第二个更新较晚提交，Then 返回配置冲突而不覆盖第一个更新。

### 15.5 实时事件

- Given WebSocket 在线，When 账号状态变化，Then 前端在 2 秒内收到事件。
- Given WebSocket 断线，When 自动重连成功，Then 前端重新请求状态快照并恢复一致状态。
- Given 慢日志客户端，When 日志大量产生，Then 业务消息处理不被阻塞。

## 16. 测试策略

### 16.1 单元测试

- `AccountRuntimeManager` 启停幂等和状态迁移。
- 账号级锁和并发启动保护。
- `JobManager` 并发限制、取消和过期清理。
- 配置脱敏和 API Key 保留语义。
- 日志脱敏规则。
- API schema 和错误码映射。

### 16.2 集成测试

- FastAPI lifespan 初始化与关闭。
- SQLite CRUD 与事务回滚。
- 使用 mock `PDDChannel` 验证启停和错误恢复。
- 使用 mock Playwright 登录验证任务流程。
- 使用 mock LLM 和拼多多 API 验证知识同步。
- WebSocket 鉴权、事件推送、断线和背压。

### 16.3 端到端测试

- 管理员登录和退出。
- 添加账号、上线、启动、停止、离线和删除完整流程。
- 关键词 CRUD 与批量导入。
- 商品同步、进度展示和取消。
- 客服知识 CRUD 与文件导入。
- LLM 设置保存、冲突和掩码展示。
- 日志实时查看、筛选和导出。

### 16.4 回归测试

现有以下能力必须继续通过：

- LLM 供应商路由和请求兼容性。
- CustomerAgent 工具调用。
- 账号级会话隔离。
- 消息队列消费和停机清理。
- 商品知识同步与提取。
- 配置迁移和密钥保护。

## 17. 实施计划

### 阶段 0：基线和契约

- 冻结现有核心行为并补充关键回归测试。
- 定义 API Pydantic schema、错误码和 OpenAPI 契约。
- 确认现有数据库备份与恢复流程。

交付物：接口契约、测试基线、迁移检查表。

### 阶段 1：FastAPI 骨架与运行时

- 新增应用工厂和 lifespan。
- 复用 DI、配置和数据库初始化。
- 实现认证、健康检查、事件总线和日志脱敏。
- 实现 `AccountRuntimeManager`，替代 Qt 自动回复线程。
- 实现账号状态和启停 API。

交付物：可通过 API 管理自动回复的后端 MVP。

### 阶段 2：核心管理 API

- 账号和店铺 API。
- Playwright 登录任务。
- 关键词 API。
- 知识库和商品同步任务 API。
- 设置与日志 API。

交付物：覆盖桌面版核心能力的完整后端。

### 阶段 3：Web 管理后台

- 登录与主导航。
- 自动回复、账号、关键词、知识库、日志和设置页面。
- WebSocket 实时状态和任务进度。
- 错误、空状态、确认和 loading 交互。

交付物：可供管理员日常使用的 Web 版。

### 阶段 4：迁移与下线桌面端

- 使用现有数据库和配置执行迁移验证。
- 完成 24 小时稳定性测试和安全检查。
- 更新部署文档和服务启动脚本。
- 功能验收通过后删除 PyQt6 入口、`ui/` 依赖和桌面打包流程。

交付物：正式 Web 版本和回滚包。

## 18. 发布与回滚

### 18.1 发布前检查

- 备份 SQLite 数据库和 `config.json`。
- 确认服务运行用户与旧 DPAPI 数据所属用户一致。
- 确认 Chrome 或 Edge 可由服务用户启动。
- 确认只配置一个 Uvicorn worker。
- 确认管理员密码、会话密钥和 HTTPS 配置完成。
- 执行数据库、配置、LLM 和拼多多连接烟雾测试。

### 18.2 回滚策略

- 首次 Web 发布不立即删除旧桌面版安装包。
- 数据库 schema 变更必须提供向下迁移或兼容读方案。
- 回滚前先停止全部 Web 自动回复连接，避免桌面版与 Web 版同时连接同一账号。
- 恢复备份数据库和配置后，再启动旧版本。

## 19. 风险与应对

| 风险 | 影响 | 应对措施 |
| --- | --- | --- |
| 多 worker 重复持有账号连接 | 重复回复、连接冲突 | 首版强制单 worker，启动时校验 |
| Playwright 在服务会话无法显示浏览器 | 无法添加或重新登录账号 | 首版运行于交互式 Windows 用户；后续设计二维码或远程浏览器流程 |
| 同步数据库调用阻塞事件循环 | API 和 WebSocket 卡顿 | 同步端点线程池化，长 I/O 使用 `asyncio.to_thread()` |
| DPAPI 绑定 Windows 用户 | 换机或换服务用户后无法解密 | 发布前校验运行用户，提供凭据重新录入流程 |
| 浏览器暴露管理能力 | 凭据和账号控制风险 | 强制认证、默认仅本机、HTTPS、CSRF、速率限制 |
| 日志实时推送过量 | 内存上涨或业务阻塞 | 有界队列、背压、低级日志丢弃策略 |
| 服务重启丢失任务状态 | 用户无法确认任务结果 | 首版明确标记中断；后续将任务持久化 |
| Web 与桌面端同时运行 | 同账号重复连接 | 部署文档禁止并行，启动时增加进程/实例锁 |


## 20. 开发完成定义

只有同时满足以下条件，本次 Web 化才视为完成：

- MVP 范围内页面和 API 全部实现。
- 自动回复不再依赖 `QThread`、Qt 信号或 PyQt 事件循环。
- 浏览器关闭不影响后台连接与任务。
- 单账号不会产生重复连接。
- 服务能够优雅启动、停止和释放资源。
- 敏感信息不出现在 API、日志和前端存储中。
- 单元、集成、端到端和现有回归测试通过。
- Windows 单节点部署文档、备份和回滚流程完成。
- 完成至少一次基于现有数据库副本的迁移演练。
- 产品验收通过后，PyQt6 桌面入口和打包流程被正式下线。
