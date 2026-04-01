# Feishu Bridge

## What This Is

一个轻量级的飞书 ↔ Claude Code 桥接服务，用 Python 实现。接收飞书机器人消息，调用 Claude Code SDK 处理，通过流式卡片实时回复。面向小团队（2-5 人）日常使用，替代已移除的 metabot。

## Core Value

飞书消息到 Claude Code 的可靠桥接 — 消息进来，AI 回复出去，流式显示思考过程，不丢消息不崩溃。

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] 飞书长连接事件订阅，接收用户消息（`im.message.receive_v1`）
- [ ] 调用 Claude Code SDK 处理用户消息并获取流式响应
- [ ] 通过 CardKit API 实现流式卡片更新（实时打字效果）
- [ ] 会话隔离：不同用户/群聊的对话互不干扰
- [ ] 卡片回调处理（按钮交互等）
- [ ] 可选的本地文件系统/命令执行能力（通过指令开启）
- [ ] 进程稳定性：异常自动恢复，不因单条消息崩溃
- [ ] 环境变量配置（APP_ID、APP_SECRET、工作目录等）

### Out of Scope

- 多租户/权限管理系统 — 小团队场景不需要
- Web UI 管理后台 — 通过配置文件和日志管理即可
- Docker 容器化 — MVP 阶段本机运行，后续再考虑
- 消息持久化/历史记录数据库 — Claude Code 自身有会话管理

## Context

- **前身**：metabot（Node.js/TypeScript），因定制性差、稳定性问题已移除
- **飞书应用**：复用已有应用 `cli_a92d11a974b89bcd`（与 mi-feishu MCP 共用）
- **飞书 Python SDK**：`lark-oapi` v1.4.6，支持长连接事件订阅（WebSocket），无需公网 IP
- **Claude Code SDK**：`claude_code_sdk` Python 包，Anthropic 官方出品
- **流式卡片**：飞书 CardKit API（`PATCH /open-apis/cardkit/v1/cards/:card_id`），SDK 未封装需用原生调用
- **运行环境**：Ubuntu Linux，Python 3.10+，本机部署
- **用户规模**：2-5 人小团队

## Constraints

- **Tech Stack**: Python 3.10+ / lark-oapi / claude_code_sdk / asyncio — 飞书官方 SDK 最成熟的选择
- **长连接限制**: 3 秒内处理完回调，最多 50 个连接，集群模式不广播 — 飞书平台硬限制
- **流式卡片**: CardKit PATCH API 未被 lark-oapi 封装 — 需用 BaseRequest 原生调用或直接 HTTP
- **无 sudo**: 运行环境无 root 权限 — 影响进程管理方式选择
- **飞书应用共用**: 与 mi-feishu MCP 共用同一应用凭据 — 事件路由需注意不冲突

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 用 Python 而非 Node.js | 飞书 Python SDK 文档完善、长连接代码极简、claude_code_sdk 原生支持 | — Pending |
| 长连接而非 Webhook | 无需公网 IP、无需内网穿透、SDK 封装鉴权、5 分钟接入 | — Pending |
| 流式卡片而非纯文本 | 用户需要实时看到 AI 思考过程（打字机效果），体验关键 | — Pending |
| 复用已有飞书应用 | 避免重新配置权限和审批流程 | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-01 after initialization*
