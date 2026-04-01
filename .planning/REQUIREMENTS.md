# Requirements: Feishu Bridge

**Defined:** 2026-04-01
**Core Value:** 飞书消息到 Claude Code 的可靠桥接 — 消息进来，AI 回复出去，流式显示思考过程，不丢消息不崩溃。

## v1 Requirements

### Connectivity (连接层)

- [x] **CONN-01**: 通过 `lark-oapi` WebSocket 长连接接收飞书消息事件（`im.message.receive_v1`）
- [x] **CONN-02**: 消息去重 — 对飞书重试的重复事件（相同 `event_id`，即每次投递的唯一标识）只处理一次
- [x] **CONN-03**: 群聊中仅在 @机器人 时响应，P2P 直聊始终响应
- [x] **CONN-04**: WebSocket 断连后自动重连（需 `lark-oapi>=1.4.9`）
- [x] **CONN-05**: 环境变量配置（APP_ID、APP_SECRET、工作目录、日志级别等）
- [x] **CONN-06**: 解决与 mi-feishu MCP 共享应用凭据的事件竞争问题

### Claude Integration (AI 接入)

- [x] **CLAUDE-01**: 通过 `claude-agent-sdk` 调用 Claude Code 处理用户消息
- [x] **CLAUDE-02**: 流式获取 Claude 响应（token-by-token）
- [x] **CLAUDE-03**: 多轮对话 — 同一会话保持上下文连续
- [x] **CLAUDE-04**: 每个活跃会话一个 asyncio Task，异常隔离不影响其他会话
- [x] **CLAUDE-05**: 单条消息处理超时看门狗（防止僵死）
- [x] **CLAUDE-06**: 可选的本地文件系统/命令执行能力（通过配置开启）

### Streaming Card (流式卡片)

- [x] **CARD-01**: 收到消息后 3 秒内发送"思考中"初始卡片
- [x] **CARD-02**: 通过 CardKit PATCH API 实时流式更新卡片内容（打字机效果）
- [x] **CARD-03**: 批量更新策略 — 每 300-500ms 合并一次 PATCH，避免触发速率限制
- [x] **CARD-04**: 流式过程中显示 typing indicator，完成后移除
- [x] **CARD-05**: Claude 工具调用可见 — 在卡片中显示 bash/read/write 等工具使用情况
- [x] **CARD-06**: 文件操作结果在卡片中展示（路径和摘要）
- [x] **CARD-07**: 最终卡片包含完整响应，格式化为 Markdown

### Session Management (会话管理)

- [x] **SESS-01**: P2P 会话按 `open_id` 隔离
- [x] **SESS-02**: 群聊会话按 `chat_id` 共享上下文，但注入发送者身份（如 `[张三]: 消息内容`）以区分不同用户
- [x] **SESS-03**: `/new` 命令重置当前会话
- [x] **SESS-04**: `/help` 命令显示可用命令列表
- [x] **SESS-05**: 空闲会话自动清理（TTL 过期释放资源）

### Concurrency (并发控制)

- [x] **CONC-01**: 消息并行处理 — 多条消息可同时被不同 asyncio Task 处理
- [x] **CONC-02**: 可配置最大并行数（`MAX_CONCURRENT_TASKS` 环境变量），超出时排队等待
- [x] **CONC-03**: 群聊内多用户消息并行处理 — 每条消息独立 Task，共享群聊对话历史，回复完成后追加到历史

### Interaction (交互)

- [ ] **INTER-01**: "Stop" 按钮 — 用户可取消正在运行的 Claude 任务
- [ ] **INTER-02**: 反馈按钮（👍/👎）— 每条回复可评价，记录到日志
- [x] **INTER-03**: 卡片回调处理（按钮交互通过长连接接收）

### Stability (稳定性)

- [ ] **STAB-01**: 进程级异常恢复 — 单条消息错误不导致服务崩溃，回复错误卡片
- [x] **STAB-02**: SIGTERM 优雅退出 — 级联取消所有活跃 Task，清理子进程
- [x] **STAB-03**: systemd user service 部署 — 开机自启、崩溃重启
- [x] **STAB-04**: 结构化日志（JSON 格式，便于排查问题）

## v2 Requirements

### Enhanced Experience

- **EXP-01**: 图片消息支持（当 Claude Agent SDK 支持 vision 时）
- **EXP-02**: 文件消息处理（下载飞书文件 → 传给 Claude）
- **EXP-03**: 会话分支（同一用户多个并行会话）
- **EXP-04**: Docker 容器化部署
- **EXP-05**: Thread 回复模式（在消息线程中回复）

## Out of Scope

| Feature | Reason |
|---------|--------|
| 多租户/权限管理 | 2-5 人小团队，全员可信 |
| Web UI 管理后台 | 配置文件 + 日志管理足够 |
| 消息持久化数据库 | Claude 自身维护会话上下文 |
| Redis 会话存储 | 内存 dict + TTL 足够覆盖 2-5 用户 |
| 多 LLM 路由 | 专注 Claude Code，不做通用网关 |
| 语音/音频消息 | 开发者工作流不需要 |
| Webhook 模式 | 已选择 WebSocket 长连接 |

## Traceability

| REQ-ID | Phase | Status |
|--------|-------|--------|
| CONN-01 | Phase 1 | Complete |
| CONN-02 | Phase 1 | Complete |
| CONN-03 | Phase 1 | Complete |
| CONN-04 | Phase 1 | Complete |
| CONN-05 | Phase 1 | Complete |
| CONN-06 | Phase 1 | Complete |
| CARD-01 | Phase 1 | Complete |
| CLAUDE-01 | Phase 2 | Complete |
| CLAUDE-02 | Phase 2 | Complete |
| CLAUDE-03 | Phase 2 | Complete |
| CLAUDE-04 | Phase 2 | Complete |
| CLAUDE-05 | Phase 2 | Complete |
| CLAUDE-06 | Phase 2 | Complete |
| SESS-01 | Phase 2 | Complete |
| SESS-02 | Phase 2 | Complete |
| SESS-03 | Phase 2 | Complete |
| CARD-02 | Phase 3 | Complete |
| CARD-03 | Phase 3 | Complete |
| CARD-04 | Phase 3 | Complete |
| CARD-05 | Phase 3 | Complete |
| CARD-06 | Phase 3 | Complete |
| CARD-07 | Phase 3 | Complete |
| INTER-03 | Phase 3 | Complete |
| STAB-01 | Phase 4 | Pending |
| STAB-02 | Phase 4 | Complete |
| STAB-03 | Phase 4 | Complete |
| STAB-04 | Phase 4 | Complete |
| SESS-04 | Phase 4 | Complete |
| SESS-05 | Phase 4 | Complete |
| INTER-01 | Phase 4 | Pending |
| INTER-02 | Phase 4 | Pending |
| CONC-01 | Phase 2 | Complete |
| CONC-02 | Phase 2 | Complete |
| CONC-03 | Phase 2 | Complete |

---
*Defined: 2026-04-01*
