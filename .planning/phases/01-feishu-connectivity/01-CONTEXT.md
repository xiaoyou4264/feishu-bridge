# Phase 1: Feishu Connectivity - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning

<domain>
## Phase Boundary

建立飞书 WebSocket 长连接，实现完整的事件管道：消息接收 → 去重 → 过滤 → 3 秒内发送"思考中"状态卡片。Phase 1 结束时，机器人能可靠地接收消息并立即回复初始卡片，但不调用 Claude。

</domain>

<decisions>
## Implementation Decisions

### 飞书应用凭据
- **D-01:** 新建独立飞书应用，不复用 cli_a92d11a974b89bcd。彻底避免与 mi-feishu MCP / feishu CLI 的事件竞争问题。
- **D-02:** 飞书 API 调用直接使用 lark-oapi Python SDK，不通过 MCP server。feishu CLI 仅用于辅助操作，不参与核心消息管道。

### "思考中"初始卡片
- **D-03:** 使用状态卡片形式 — 带标题栏 + "正在思考" 状态文本 + typing 动画元素。不是纯文本回复。

### 消息过滤规则
- **D-04:** 尽量处理所有消息类型（文本、富文本、图片、文件等），不仅限于纯文本。
- **D-05:** 对于当前不支持的消息类型，回复友好提示（如"暂不支持该类型消息，请发送文字"），不静默忽略。

### 配置方式
- **D-06:** 使用 `.env` 文件 + `python-dotenv`，支持环境变量覆盖。必填项：APP_ID、APP_SECRET。选填项：LOG_LEVEL、WORKING_DIR 等。

### Claude's Discretion
- WebSocket 重连策略的具体参数（退避时间、最大重试次数）
- 消息去重的 TTL 时长和数据结构选择
- asyncio 事件循环与 lark.ws.Client 线程桥接的具体模式

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 飞书 SDK
- `.planning/research/STACK.md` — 技术栈选型和版本锁定
- `.planning/research/ARCHITECTURE.md` — 组件边界和数据流设计
- `.planning/research/PITFALLS.md` — 飞书 SDK 踩坑点（3 秒超时、WebSocket 重连、事件去重）

### 项目上下文
- `.planning/PROJECT.md` — 项目背景和约束
- `.planning/REQUIREMENTS.md` — Phase 1 需求：CONN-01~06, CARD-01

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- 无（greenfield 项目）

### Established Patterns
- 无（待本阶段建立基础架构模式）

### Integration Points
- `~/.feishu/auth.json` — feishu CLI 的认证信息（仅参考，bridge 使用独立应用凭据）
- feishu CLI v1.1.6 已安装 — 可用于辅助操作但不参与核心管道

</code_context>

<specifics>
## Specific Ideas

- 初始卡片应该有专业感，类似 AI 助手的"正在处理您的请求"状态
- 机器人名可以从飞书应用配置获取，卡片标题使用机器人名

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-feishu-connectivity*
*Context gathered: 2026-04-01*
