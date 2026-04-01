# Phase 3: Streaming Card Renderer - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning
**Source:** Auto mode (recommended defaults selected)

<domain>
## Phase Boundary

将 Phase 2 的一次性卡片更新改为实时流式更新：Claude 输出时实时 PATCH 卡片内容（打字机效果），工具调用实时可见，typing indicator 动态显示。Phase 3 结束时用户能看到 Claude 的回复逐字出现在飞书卡片中。

</domain>

<decisions>
## Implementation Decisions

### CardKit PATCH 机制
- **D-18:** 使用 `httpx.AsyncClient` 直接调用 CardKit PATCH API（`PATCH /open-apis/cardkit/v1/cards/:card_id`），因为 lark-oapi 未封装此接口。
- **D-19:** 流式更新流程：Phase 2 的 `send_thinking_card()` 发送初始卡片 → 获取 card_id → 通过 CardKit PATCH 增量更新内容 → 最终卡片用 `im.v1.message.patch` 确认。
- **D-20:** 需要从初始卡片回复中提取 `card_id`（区别于 `message_id`），用于后续 PATCH 调用。

### 流式批量策略
- **D-21:** 每 300-500ms 合并一次 PATCH 请求，不逐 token 发送。用 asyncio timer/event 机制实现 flush。
- **D-22:** PATCH 请求使用递增的 `sequence` 号（per-card，从 1 开始），确保顺序。
- **D-23:** 遇到 429 rate limit 时退避重试（tenacity），不丢弃内容。

### 工具调用卡片渲染
- **D-24:** Claude 调用工具时，在卡片中显示可折叠的工具信息区域：工具名称 + 简要摘要。使用飞书卡片的 `collapsible` 组件或 markdown 分隔线 + 代码块。
- **D-25:** 工具调用信息在最终文本之前显示，用户能看到 Claude 的"工作过程"。

### Typing Indicator
- **D-26:** 流式过程中在卡片底部显示 typing indicator（markdown 格式的 `_正在输入..._` 或飞书 typing 标签）。
- **D-27:** 最终卡片移除 typing indicator，只保留完整的 Markdown 响应。

### 卡片回调基础设施
- **D-28:** 通过长连接注册 `card.action.trigger` 回调处理器（`register_p2_card_action_trigger`），为 Phase 4 的 Stop/Feedback 按钮做准备。
- **D-29:** Phase 3 只建立回调基础设施，按钮逻辑在 Phase 4 实现。

### Claude's Discretion
- CardKit `streaming_config` 的具体参数（需要运行时验证）
- PATCH 请求的 Content-Type 和 body 格式细节
- 工具信息的具体展示格式（取决于飞书卡片组件可用性）
- httpx.AsyncClient 的连接池配置

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目研究
- `.planning/research/STACK.md` — httpx 用法、CardKit API 信息
- `.planning/research/ARCHITECTURE.md` — 流式更新数据流
- `.planning/research/PITFALLS.md` — CardKit rate limits、batch 策略

### Phase 2 代码
- `src/claude_worker.py` — 当前 `_run_claude_turn()` 收集完整响应后一次性返回，Phase 3 需要改为流式处理
- `src/cards.py` — 当前 `update_card_content()` 用 `im.v1.message.patch`，Phase 3 需要新增 CardKit PATCH 流式更新
- `src/handler.py` — 调度逻辑，Phase 3 需要传递流式回调

### Prior Phase Context
- `.planning/phases/01-feishu-connectivity/01-CONTEXT.md` — D-01~D-06
- `.planning/phases/02-claude-integration/02-CONTEXT.md` — D-07~D-17

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/cards.py:send_thinking_card()` — 发初始卡片，返回 reply_message_id
- `src/cards.py:update_card_content()` — 用 `im.v1.message.patch` 更新卡片（Phase 3 保留作为最终确认）
- `src/cards.py:send_error_card()` — 错误卡片
- `src/cards.py:_build_card()` — 构建卡片 JSON 的工具函数
- `src/claude_worker.py:_run_claude_turn()` — 当前同步收集响应，需要改为流式
- `src/session.py:SessionManager` — 会话管理，不需要改动

### Established Patterns
- 卡片 JSON 使用 CardKit v2 格式：`{"data": {"schema": "2.0", "header": ..., "body": {"elements": [...]}}}`
- asyncio Task 隔离 per-message
- Semaphore + per-session Lock 并发控制

### Integration Points
- `src/claude_worker.py` — 主要改动点：从收集完整响应改为流式回调
- `src/cards.py` — 新增 CardKit PATCH 流式更新函数
- `requirements.txt` — 已有 httpx，可能需要 tenacity

</code_context>

<specifics>
## Specific Ideas

- 用户强调"实时打字效果"是核心体验需求
- 批量更新间隔 300-500ms 是研究阶段确定的安全值
- Phase 2 已经实现了 `receive_response()` 流式接收，Phase 3 需要把这个流对接到卡片 PATCH

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-streaming-card-renderer*
*Context gathered: 2026-04-01 via auto mode*
