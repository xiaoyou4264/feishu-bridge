# Phase 4: Stability and Operations - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning
**Source:** Auto mode (recommended defaults selected)

<domain>
## Phase Boundary

生产级加固：异常恢复、优雅退出、systemd 服务、结构化日志、会话清理、命令系统（/help）、交互按钮（Stop/Feedback）。Phase 4 结束时机器人能无人值守运行。

</domain>

<decisions>
## Implementation Decisions

### 异常恢复 (STAB-01)
- **D-30:** 每条消息处理在独立的 try/except 中，异常时回复错误卡片（send_error_card），不影响其他消息。已在 Phase 2 的 claude_worker.py 中部分实现，Phase 4 加强边界情况处理。

### SIGTERM 优雅退出 (STAB-02)
- **D-31:** 使用 `asyncio.get_event_loop().add_signal_handler(SIGTERM, ...)` 注册信号处理器。
- **D-32:** 收到 SIGTERM 后：1) 停止接收新消息 2) 取消所有活跃 asyncio Task 3) 等待最多 10 秒 4) 清理 Claude SDK 子进程 5) 退出。

### systemd 服务 (STAB-03)
- **D-33:** 创建 `~/.config/systemd/user/feishu-bridge.service` 文件，使用 `systemctl --user` 管理。
- **D-34:** 配置 `Restart=on-failure`、`RestartSec=3`。需要一次性 `loginctl enable-linger` 允许用户服务在退出后继续运行。

### 结构化日志 (STAB-04)
- **D-35:** structlog 已在 Phase 1 配置。Phase 4 添加 event_id 关联：每条消息处理时绑定 `event_id` 到 structlog context，所有子调用自动携带。
- **D-36:** 生产环境使用 JSON renderer（`structlog.dev.ConsoleRenderer` 改为 `structlog.processors.JSONRenderer`），通过 LOG_FORMAT env var 控制。

### 会话清理 (SESS-05)
- **D-37:** 后台 asyncio Task 每 60 秒扫描一次 SessionManager，清理超过 SESSION_TTL（默认 3600 秒，.env 可配）的空闲会话。
- **D-38:** 清理时销毁 ClaudeSDKClient 实例，释放资源。

### /help 命令 (SESS-04)
- **D-39:** 用户发送 `/help` 时，回复静态卡片列出可用命令：`/new`（重置会话）、`/help`（显示帮助）。绿色头部。

### Stop 按钮 (INTER-01)
- **D-40:** 流式卡片中添加"停止"按钮（card action trigger）。点击时取消对应的 asyncio Task（通过 message_id → task 映射），回复确认"已停止"。
- **D-41:** 需要在 claude_worker.py 中维护一个 message_id → Task 的映射，供 card callback handler 查找和取消。

### Feedback 按钮 (INTER-02)
- **D-42:** 最终卡片底部添加 👍/👎 按钮。点击时记录到日志文件（structlog event），不需要数据库。
- **D-43:** 按钮使用 card.action.trigger 回调，Phase 3 已注册基础设施。

### Claude's Discretion
- systemd service 文件的具体 ExecStart 路径
- 日志轮转策略（journald 自动处理或手动 logrotate）
- Stop 按钮的 3 秒回调窗口处理策略
- 反馈日志的具体格式

</decisions>

<canonical_refs>
## Canonical References

### Prior Phase Code
- `src/claude_worker.py` — Stop 按钮需要修改的主要文件
- `src/handler.py` — card callback handler 和 /help 命令
- `src/session.py` — SessionManager 需要添加 TTL 清理
- `src/cards.py` — 需要添加 /help 卡片
- `main.py` — SIGTERM handler 和 cleanup task 初始化
- `.planning/phases/01-feishu-connectivity/01-CONTEXT.md` — D-01~D-06
- `.planning/phases/02-claude-integration/02-CONTEXT.md` — D-07~D-17
- `.planning/phases/03-streaming-card-renderer/03-CONTEXT.md` — D-18~D-29

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/handler.py:create_card_action_handler()` — Phase 3 已注册，Phase 4 需要扩展逻辑
- `src/cards.py:send_error_card()` — 已有错误卡片
- `src/cards.py:_build_card()` — 构建卡片的工具函数
- structlog 已全局配置

### Established Patterns
- asyncio Task per-message 隔离
- Semaphore + per-session Lock 并发控制
- 卡片使用 CardKit v2 JSON 格式

### Integration Points
- `main.py` — SIGTERM handler、cleanup task、service setup
- `src/handler.py` — /help 命令、card callback 逻辑扩展
- `src/session.py` — TTL 清理方法
- `src/claude_worker.py` — task 映射、Stop 按钮支持

</code_context>

<specifics>
## Specific Ideas

- 无 sudo 环境，systemd --user 是唯一选项
- Stop 按钮是 HIGH complexity（card callback + process kill + 3s 窗口），需要仔细设计

</specifics>

<deferred>
## Deferred Ideas

None — this is the final phase

</deferred>

---

*Phase: 04-stability-and-operations*
*Context gathered: 2026-04-01 via auto mode*
