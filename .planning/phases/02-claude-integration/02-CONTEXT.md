# Phase 2: Claude Integration - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning
**Source:** Auto mode (recommended defaults selected)

<domain>
## Phase Boundary

端到端 Claude 响应管道：用户飞书消息 → Claude Agent SDK 处理 → 文本回复到飞书卡片。包含会话隔离（P2P 按 open_id，群聊按 chat_id 共享上下文）、并发控制（MAX_CONCURRENT_TASKS）、多轮对话、超时看门狗、/new 命令。Phase 2 结束时用户能收到真正的 Claude 回复（非流式，纯文本更新到已有的"思考中"卡片）。

</domain>

<decisions>
## Implementation Decisions

### Claude SDK 调用模式
- **D-07:** 使用 `claude-agent-sdk` 的 `query()` 方法，配合 streaming 模式获取逐步输出。Phase 2 先将完整响应一次性更新到卡片（非流式 UI），Phase 3 再做流式卡片更新。
- **D-08:** 每个活跃会话维护一个 `ClaudeSDKClient` 实例（或等价的 session 对象），通过 `asyncio.Task` 隔离。

### 会话上下文策略
- **D-09:** Phase 2 实现时先调研 `claude-agent-sdk` 的实际会话能力。如果 SDK 的 `ClaudeSDKClient` 支持多轮 `query()` 自动延续上下文 → 直接用 SDK 内置会话。如果不支持并发 query → 降级为手动历史管理（快照+合并模式），加历史窗口限制控制 token 消耗。
- **D-10:** 群聊内多用户消息并行处理是硬需求。如果 SDK 内置会话不支持并发，必须用手动历史方案。

### 并发模型
- **D-11:** 全局 `asyncio.Semaphore(MAX_CONCURRENT_TASKS)` 控制总并行数。`MAX_CONCURRENT_TASKS` 通过 .env 配置，默认值 5。
- **D-12:** 超出并发上限的消息排队（Semaphore 自然阻塞），不丢弃。
- **D-13:** 群聊内不同用户的消息并行处理（各自独立 Task），不串行等待。

### 群聊用户身份注入
- **D-14:** 群聊消息注入发送者身份前缀：`[display_name]: message_content`。display_name 从飞书事件的 sender 信息中提取。
- **D-15:** P2P 消息不注入前缀（只有一个用户，无需区分）。

### 超时看门狗
- **D-16:** 每个 Claude 调用设置超时（通过 asyncio.wait_for），超时后取消 Task 并回复错误卡片。超时时长通过 .env 配置（CLAUDE_TIMEOUT），默认 120 秒。

### /new 命令
- **D-17:** 用户发送 `/new` 时，清除当前会话的 Claude 上下文（销毁 session/client 实例，下次消息创建新的）。回复确认卡片"会话已重置"。

### Claude's Discretion
- Claude Agent SDK 的具体初始化参数（model、max_tokens 等）
- 错误卡片的具体样式和文案
- 会话管理器的内存数据结构选择
- 手动历史管理时的历史窗口大小

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目研究
- `.planning/research/STACK.md` — claude-agent-sdk 版本和用法
- `.planning/research/ARCHITECTURE.md` — 组件边界和数据流（特别是 Session Manager 部分）
- `.planning/research/PITFALLS.md` — Claude SDK 踩坑点（子进程死锁、SIGTERM hang、async generator 清理）

### Phase 1 代码
- `src/handler.py` — 现有事件处理管道，Phase 2 需要在此基础上集成 Claude 调用
- `src/cards.py` — 卡片回复函数，Phase 2 需要新增"回复完成"卡片更新和"错误"卡片
- `src/config.py` — 配置模块，需要新增 CLAUDE_TIMEOUT、MAX_CONCURRENT_TASKS 等配置项
- `main.py` — 入口，需要初始化 session manager 和 Claude SDK

### Phase 1 Context
- `.planning/phases/01-feishu-connectivity/01-CONTEXT.md` — Phase 1 决策（D-01~D-06）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/handler.py:handle_message()` — 现有 dedup→filter→card 管道，Phase 2 在 card 之后插入 Claude 调用
- `src/cards.py:send_thinking_card()` — 返回 reply_message_id，可用于后续更新卡片内容
- `src/config.py:Config` — Pydantic 模型，新增字段即可扩展
- `src/dedup.py:DeduplicationCache` — 已验证可用
- `src/filters.py:parse_message_content()` — 提取文本内容，可直接传给 Claude

### Established Patterns
- Sync handler → `loop.create_task()` 桥接到 asyncio（main.py 已建立）
- 卡片回复使用 `im.v1.message.areply()` + interactive card JSON
- 配置通过 `Config.from_env()` + .env 文件

### Integration Points
- `handler.py:handle_message()` — Claude 调用插入点
- `main.py` — session manager 初始化点
- `cards.py` — 新增 update_card_content() 和 send_error_card()

</code_context>

<specifics>
## Specific Ideas

- Phase 2 的 Claude 回复先以纯文本更新已有的"思考中"卡片（替换卡片内容），不做流式更新（那是 Phase 3 的事）
- 用户之前明确说过群聊并发是硬需求："同一个群聊里的不同用户同时发了消息，我希望的效果是能够并行处理"
- 会话方案在实现时根据 SDK 实际能力决定（用户在讨论阶段明确选择了"Phase 2 时再定"）
- 复用现有飞书应用 cli_a92d11a974b89bcd（Phase 1 实际验证中确认了这个决策）

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-claude-integration*
*Context gathered: 2026-04-01 via auto mode*
