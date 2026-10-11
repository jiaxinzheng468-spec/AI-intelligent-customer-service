# Agent-Customer 项目学习与面试指南

## 1. 项目定位

这是一个“电商 AI 客服 Web 管理系统”。它不是单纯的聊天机器人，而是把拼多多店铺消息、商品数据、客服知识库、AI Agent 和人工客服流程连接起来。

项目当前主要接入拼多多，使用 FastAPI 托管 API 和原生 Web 管理页面。用户可以在浏览器中管理店铺账号、配置大模型、维护知识库，并启动或停止自动回复。

## 2. 业务场景：解决什么问题

电商商家每天会遇到大量重复问题：

- “这个商品是什么成分？”
- “孕妇能不能用？”
- “什么时候发货？”
- “可以退款吗？”
- “有没有适合某种需求的商品？”
- “我要转人工客服。”

如果全部由人工处理，会产生响应慢、人力成本高、回答不一致、夜间无人值守等问题。

这个项目主要解决四类问题：

1. **自动接收平台消息**：通过拼多多 WebSocket 实时接收客户消息，不需要人工刷新页面。
2. **自动回答常见问题**：AI 查询商品知识库和客服知识库后生成回复。
3. **主动推荐商品**：AI 获取店铺商品列表，并发送商品卡片。
4. **复杂问题转人工**：遇到退款、投诉、改地址、纠纷等关键词时转接人工客服。

项目目前主要负责客服问答和消息处理，并没有实现完整的订单退款、支付或仓储系统。

## 3. 一个完整业务流程

例如客户问：“这款商品适合儿童吗？”

```text
客户发送消息
    ↓
拼多多 WebSocket 接收
    ↓
解析为统一 Context
    ↓
进入当前账号的异步消息队列
    ↓
关键词检测
    ├── 命中人工关键词 → 转接人工
    └── 未命中 → AI Handler
                    ↓
              CustomerAgent
                    ↓
          LLM 判断是否调用工具
                    ↓
              get_product_knowledge
                    ↓
              查询商品知识库
                    ↓
              生成最终回答
                    ↓
          拼多多 API 发送回复
                    ↓
              保存会话历史
```

## 4. 技术栈

| 模块 | 技术 |
|---|---|
| 编程语言 | Python 3.11+ |
| Web 管理端 | FastAPI、原生 HTML/CSS/JavaScript |
| AI 调用 | LiteLLM、OpenAI Chat Completions 协议 |
| Agent | 自研 Agent，不依赖 Agno 或 LangChain |
| 数据库 | SQLite、SQLAlchemy |
| 数据校验 | Pydantic |
| 平台通信 | WebSocket、HTTP、Playwright |
| 异步处理 | asyncio、asyncio.Queue、Semaphore、Lock |
| 知识检索 | jieba 分词、SQL `LIKE` 查询 |
| Token 统计 | tiktoken |
| 日志 | Loguru |
| 测试 | unittest |

重要入口和配置文件：

- `web_app.py`：FastAPI Web 应用入口。
- `pyproject.toml`：依赖和 Python 版本定义。
- `README.md`：项目功能、安装和构建说明。
- `config.py`：配置加载、校验和密钥保护。

## 5. 整体架构

项目不是微服务，而是：

> 单体 Web 应用 + 模块化分层 + 事件驱动异步架构

### 5.1 表现层

主要目录：

- `server/main.py`
- `server/api.py`
- `server/runtime.py`
- `server/static/`

FastAPI 负责账号、配置、知识库和自动回复接口，并托管原生 Web 页面。`AccountRuntimeManager` 在服务端事件循环中管理每个账号的 `PDDChannel`，浏览器关闭不会中断账号连接。管理员启用自动回复后，期望状态保存到 SQLite，FastAPI 重启会恢复已启用账号。

### 5.2 渠道接入层

主要目录：

- `Channel/pinduoduo/pdd_channel.py`
- `Channel/pinduoduo/core/pdd_lifecycle.py`
- `Channel/pinduoduo/core/pdd_connection.py`
- `Channel/pinduoduo/core/pdd_message_handler.py`
- `Channel/pinduoduo/pdd_message.py`
- `Channel/pinduoduo/utils/API/`

主要职责：

- 获取 Token 和 Cookie。
- 建立拼多多 WebSocket。
- 接收和解析平台消息。
- 心跳检测和自动重连。
- Cookie 健康检查和重新登录。
- 调用拼多多 HTTP API 发送文本、图片和商品卡片。

`ChannelType` 中预留了京东、淘宝、抖音等渠道，但目前真正实现的是拼多多。

### 5.3 统一消息和桥接层

主要文件：

- `bridge/context.py`
- `bridge/sender.py`
- `bridge/reply.py`

`Context` 是系统内部的统一消息格式，包含消息类型、消息内容、渠道类型、店铺 ID、账号 ID、客户 UID、消息 ID 和原始数据。

上层 Agent 不需要了解拼多多原始 JSON 的具体结构，这实现了渠道解耦。

`ReplySender` 是发送器抽象接口，`PinduoduoSender` 将统一发送操作适配为拼多多 API 调用。以后增加淘宝渠道，可以新增淘宝 Channel 和淘宝 Sender。

这里主要使用了**适配器模式**。

### 5.4 消息队列和处理器链

主要文件：

- `Message/core/queue.py`
- `Message/core/consumer.py`
- `Message/core/handlers.py`
- `Message/handlers/keyword_handler.py`
- `Message/handlers/ai_handler.py`

每个账号拥有独立的消息队列，队列名称由“渠道 + 店铺 ID + 账号 ID”生成，再经过哈希处理。

队列具有以下能力：

- FIFO 顺序。
- 最大容量。
- 消息去重。
- 固定数量 Worker。
- 有界并发。
- 停机排空。
- 失败日志记录。

处理器顺序大致是：

```text
关键词处理器
    ↓
AI 处理器
    ↓
CatchAll 兜底处理器
```

关键词处理器负责转人工，AI 处理器负责自动回复，兜底处理器记录未处理消息。

这里使用了**责任链模式**和**生产者-消费者模型**。

### 5.5 Agent 层

主要文件：

- `Agent/CustomerAgent/custom/customer_agent.py`
- `Agent/CustomerAgent/custom/llm_client.py`
- `Agent/CustomerAgent/custom/message_builder.py`
- `Agent/CustomerAgent/custom/tool_executor.py`
- `Agent/CustomerAgent/custom/session_manager.py`

`CustomerAgent` 的核心循环是：

```text
读取历史消息
    ↓
检查上下文是否需要压缩
    ↓
构造 System Prompt 和消息列表
    ↓
调用 LLM
    ↓
判断是否存在 tool_calls
    ├── 没有工具调用 → 返回最终文本
    └── 有工具调用
            ↓
      执行工具
            ↓
      把工具结果加入消息
            ↓
      再次调用 LLM
```

普通 LLM 调用通常是“用户问题 → 模型文本”；Agent 调用则是“用户问题 → 模型决策 → 工具执行 → 结果反馈 → 最终回答”。

这是本项目最核心的 AI 部分。

### 5.6 数据和知识库层

主要文件：

- `database/models.py`
- `database/db_manager.py`
- `database/knowledge_service.py`
- `database/product_sync.py`

主要数据表：

- `channels`：渠道。
- `shops`：店铺。
- `accounts`：店铺账号。
- `keywords`：转人工关键词。
- `product_knowledge`：商品知识。
- `customer_service_knowledge`：售后、物流、退款等客服知识。

## 6. Agent 工具

工具定义位于 `Agent/CustomerAgent/tools/`：

| 工具 | 作用 |
|---|---|
| `get_shop_products` | 获取店铺商品列表 |
| `send_goods_link` | 发送商品卡片 |
| `get_product_knowledge` | 查询某个商品的详细知识 |
| `search_customer_service_knowledge` | 查询售后、物流、退款等知识 |
| `transfer_conversation` | 转人工客服 |

工具使用 Pydantic 定义参数，再转换成 OpenAI tools 格式。

工具执行时，`shop_id`、`user_id`、`recipient_uid` 等身份字段不会完全相信模型，而是从受信任的会话上下文注入。

这是一个非常重要的安全设计：

> 模型只能决定业务参数，不能伪造当前店铺和客户身份。

发送商品卡片前，还会通过当前账号 API 验证商品是否属于当前店铺，避免模型传入错误商品 ID。
## 7. 知识库和商品同步

### 7.1 商品知识同步

商品同步流程：

```text
拼多多商品列表
    ↓
获取商品详情
    ↓
获取商品图片
    ↓
发送给多模态 LLM
    ↓
提取品牌、产地、成分、规格、适用年龄、FAQ
    ↓
保存到 product_knowledge
```

如果 LLM 解析失败，会降级保存商品价格、销量、规格等基本信息。

### 7.2 当前检索方式

当前不是向量数据库检索，而是：

```text
jieba 分词
    ↓
SQL LIKE / contains 匹配
    ↓
返回商品知识和客服知识
```

因此面试时应该准确表述为：

> 这是轻量级的关键词检索增强，不是基于 Embedding 和向量数据库的完整 RAG。

### 7.3 会话记忆和上下文压缩

`SessionManager` 将用户消息、AI 消息、工具调用和工具结果保存到 SQLite。

当历史消息 Token 超过阈值时：

1. 保留最近几条消息。
2. 调用 LLM 生成旧消息摘要。
3. 删除旧消息。
4. 把摘要作为普通历史数据重新保存。

这样可以在不无限增加上下文长度的情况下，保留历史关键信息。

## 8. 最重要的学习重点

### 8.1 Python 基础

重点学习：

- 类和继承。
- 抽象基类。
- 装饰器。
- 类型注解。
- 异常处理。
- 上下文管理器。
- `dataclass`。
- Pydantic 模型。

### 8.2 Python 异步编程

这是项目最重要的工程基础之一：

- Event loop。
- `async def` 和 `await`。
- `asyncio.create_task`。
- `asyncio.Queue`。
- `asyncio.Semaphore`。
- `asyncio.Lock`。
- `asyncio.gather`。
- `asyncio.wait`。
- `asyncio.to_thread`。
- Task 取消和资源清理。

要理解一个关键问题：

> WebSocket 和 AI 请求是异步的，但 HTTP、SQLite、商品 API 可能是同步阻塞的，所以项目使用 `asyncio.to_thread` 将阻塞操作放到线程中。

### 8.3 LLM 工具调用

重点理解 OpenAI Tool Calling 协议：

```json
{
  "type": "function",
  "function": {
    "name": "get_product_knowledge",
    "arguments": "{\"goods_id\": 123456, \"shop_id\": 99}"
  }
}
```

需要理解：

- Tool schema。
- Tool call ID。
- Assistant tool-call 消息。
- Tool result 消息。
- 多轮工具调用。
- 最大循环次数。
- 工具失败和超时处理。

### 8.4 RAG 和 Agent

建议学习：

- Prompt 构造。
- 会话记忆。
- Token 限制。
- 上下文压缩。
- 关键词检索。
- Embedding。
- 向量数据库。
- RAG 召回和重排。
- 工具权限和副作用控制。

### 8.5 工程设计模式

项目中可以重点理解：

- 责任链模式：消息处理器链。
- 适配器模式：`ReplySender`。
- 依赖注入：`core/di_container.py`。
- 工厂思想：根据渠道获取 Sender。
- 单例：数据库、知识库、连接状态。
- 瞬态对象：每个账号独立的 `CustomerAgent`。
- 生产者-消费者：消息队列和消费者。

## 9. 账号隔离和安全设计

### 9.1 账号隔离

系统用以下信息构造账号范围：

```text
渠道 + 店铺 ID + 账号 ID
```

用以下信息构造客户会话范围：

```text
渠道 + 店铺 ID + 账号 ID + 客户 UID
```

不同账号使用独立 Agent、独立会话管理器和独立异步资源。同一客户使用会话锁串行处理，不同客户可以并发处理。

### 9.2 配置和密钥

配置由 Pydantic 校验。API Key 和账号 Cookie 在 Windows 上使用 DPAPI 保护后保存。

LLM Endpoint 还会检查：

- 远程地址必须使用 HTTPS。
- URL 不能包含用户名和密码。
- 禁止危险的本地或元数据地址。
- 本地 HTTP Endpoint 必须明确开启本地信任。
- 已知不支持工具调用的模型不能启用工具。

### 9.3 Prompt Injection 防护

商品目录、知识库内容和历史摘要都可能来自外部数据，因此项目会：

- 将其标记为“不可信数据”。
- 不把商品内容放进系统指令中。
- 转义特殊字符。
- 不允许历史摘要获得系统消息权限。
- 对工具身份字段使用可信上下文覆盖模型参数。

## 10. 面试时的项目介绍

可以这样介绍：

> 我学习和维护的是一个基于 Python 和 FastAPI 的电商 AI 客服 Web 应用，当前接入拼多多 WebSocket。系统将平台消息标准化为统一 Context，按账号隔离到异步消息队列中，再通过责任链分别进行关键词转人工和 AI 回复。AI 部分是自研 Agent，基于 LiteLLM 兼容多个大模型供应商，通过 OpenAI Tool Calling 调用商品查询、知识库检索、商品推荐和人工转接工具。项目使用 SQLAlchemy 和 SQLite 持久化账号、自动回复启用意图、商品知识和客服知识，并通过会话锁、Token 压缩、有限并发、重启恢复、自动重连和身份字段注入保证稳定性和安全性。

## 11. 面试高频问题

### Q1：为什么要使用消息队列？

WebSocket 接收速度和 AI 回复速度不同，AI 调用可能耗时几秒。队列可以解耦接收和处理、削峰，并通过有界队列提供背压，避免消息无限堆积。每个账号独立队列，还能避免不同账号互相影响。

### Q2：如何避免不同客户的聊天记录混在一起？

系统使用“渠道、店铺 ID、账号 ID、客户 UID”构造会话 Key，并且同一个客户使用 `asyncio.Lock` 串行处理，避免历史读写和回复顺序错乱。

### Q3：为什么 `CustomerAgent` 是 transient，而数据库服务是 singleton？

数据库和连接状态需要跨模块共享，所以适合单例。每个账号的 Agent 则需要独立保存 LLM 配置、会话管理器和异步资源，如果共享 Agent，容易发生账号配置、会话历史和事件循环串用。

### Q4：项目有没有真正使用向量数据库？

没有。当前是 jieba 分词加 SQL `LIKE` 查询，属于轻量关键词检索。它部署简单，但语义召回能力有限。后续可以使用 Embedding、向量数据库和 reranker 改进。

### Q5：如何防止模型调用错误的商品或店铺？

店铺 ID、账号 ID 和客户 UID 从可信 Context 注入；工具参数使用 Pydantic 校验；发送商品前再次通过当前账号 API 校验商品归属；外部商品数据和知识库内容会被标记为不可信数据。

### Q6：如何处理大模型失败？

项目有多层降级：LLM 初始化失败返回配置提示；调用失败返回服务不可用；工具失败作为工具结果反馈给模型；回复发送失败使用备用话术；商品知识提取失败保存基本信息；WebSocket 断开后自动重连。

### Q7：为什么自己实现 Agent，而不是直接使用 LangChain 或 Agno？

自研实现可以精确控制消息协议、工具执行、会话持久化、账号隔离和错误处理，并减少额外依赖。代价是需要自己维护 Agent 循环、工具注册、重试和兼容逻辑。

## 12. 项目局限和可改进方向

从面试官角度，不要只讲优点，也要能主动说明局限：

- 当前主要是单机单进程 Web 应用，不是分布式服务。
- SQLite 不适合大量并发写入。
- 知识检索依赖关键词，语义理解有限。
- 当前只有拼多多渠道真正实现完成。
- 缺少统一监控、指标和链路追踪。
- 副作用工具虽然有标记，但还不是完整的人工审批系统。
- AI 结果仍可能出现幻觉，需要知识库、规则和人工转接共同兜底。

可以提出以下改进：

1. 使用 Embedding、向量数据库和 reranker 改进检索。
2. 使用 Redis 或 RabbitMQ 支持多进程、多机器部署。
3. 将 SQLite 迁移到 PostgreSQL，改善并发写入能力。
4. 增加 Prometheus 指标、日志追踪和告警。
5. 为退款、改地址等高风险操作增加人工确认。
6. 增加幂等键、重试、熔断和限流机制。
7. 增加真正的多渠道 Sender 和渠道插件机制。

## 13. 推荐学习顺序

### 第一步：先看入口和配置

阅读：

```text
web_app.py
server/main.py
config.py
core/di_container.py
```

目标：理解程序如何启动、服务如何注册、配置如何加载。

### 第二步：看消息接入和队列

阅读：

```text
Channel/pinduoduo/pdd_message.py
Channel/pinduoduo/core/pdd_lifecycle.py
bridge/context.py
Message/core/queue.py
Message/core/consumer.py
```

目标：理解一条消息如何从平台进入系统。

### 第三步：看处理器链

阅读：

```text
Message/handlers/keyword_handler.py
Message/handlers/ai_handler.py
Message/__init__.py
```

目标：理解为什么关键词转人工会优先于 AI 回复。

### 第四步：看 Agent 核心循环

阅读：

```text
Agent/CustomerAgent/custom/customer_agent.py
Agent/CustomerAgent/custom/llm_client.py
Agent/CustomerAgent/custom/tool_executor.py
Agent/CustomerAgent/custom/message_builder.py
```

目标：理解 LLM、工具调用、上下文和最终回复如何配合。

### 第五步：看数据和知识库

阅读：

```text
database/models.py
database/knowledge_service.py
database/product_sync.py
Agent/CustomerAgent/custom/session_manager.py
```

目标：理解商品知识如何产生、保存、查询和注入 Agent。

### 第六步：结合测试理解边界

重点阅读：

```text
tests/test_customer_agent_llm.py
tests/test_llm_transport.py
tests/test_regressions.py
tests/test_llm_config.py
```

这些测试重点覆盖账号隔离、工具权限、队列并发、LLM 配置、提示词注入防护和商品同步。

## 14. 当前验证情况

测试覆盖 Web API、账号重启恢复、数据库兼容、账号隔离、工具权限、队列并发、LLM 配置、提示词注入防护和商品同步。以 CI 与本地 `unittest discover` 的最新结果为准。

# 学习路线

拼多多 WebSocket
→ Context
→ 消息队列
→ 处理器链
→ CustomerAgent
→ Tool Calling
→ 知识库/API
→ 回复客户
