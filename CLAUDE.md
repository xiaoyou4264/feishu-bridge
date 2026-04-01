<!-- GSD:project-start source:PROJECT.md -->
## Project

**Feishu Bridge**

一个轻量级的飞书 ↔ Claude Code 桥接服务，用 Python 实现。接收飞书机器人消息，调用 Claude Code SDK 处理，通过流式卡片实时回复。面向小团队（2-5 人）日常使用，替代已移除的 metabot。

**Core Value:** 飞书消息到 Claude Code 的可靠桥接 — 消息进来，AI 回复出去，流式显示思考过程，不丢消息不崩溃。

### Constraints

- **Tech Stack**: Python 3.10+ / lark-oapi / claude_code_sdk / asyncio — 飞书官方 SDK 最成熟的选择
- **长连接限制**: 3 秒内处理完回调，最多 50 个连接，集群模式不广播 — 飞书平台硬限制
- **流式卡片**: CardKit PATCH API 未被 lark-oapi 封装 — 需用 BaseRequest 原生调用或直接 HTTP
- **无 sudo**: 运行环境无 root 权限 — 影响进程管理方式选择
- **飞书应用共用**: 与 mi-feishu MCP 共用同一应用凭据 — 事件路由需注意不冲突
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Recommended Stack
### Core Runtime
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | 3.10+ | Runtime | claude-agent-sdk hard requirement; asyncio feature parity; type union syntax (`X \| Y`) |
| asyncio | stdlib | Concurrency | Both lark-oapi ws.Client and claude-agent-sdk are async-native; no extra threads needed |
### Feishu Integration
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| lark-oapi | 1.5.3 | Feishu SDK: WebSocket long connection + API calls | Official ByteDance SDK; latest PyPI release Jan 2026; auto-reconnect built in; handles token refresh; `lark.ws.Client` eliminates need for public IP or webhook infrastructure |
| lark-oapi[ws] | same | WebSocket dependency extra | The `[ws]` install extra pulls in the `websockets` library required for `lark.ws.Client` |
### Claude Code Integration
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| claude-agent-sdk | 0.1.53 | Call Claude Code CLI, stream responses | CRITICAL: `claude-code-sdk` is officially deprecated as of Sept 2025. `claude-agent-sdk` is the Anthropic-maintained replacement, released 2026-03-31. Bundles its own CLI — no separate `npm install` step needed. Python 3.10+. |
### HTTP Client (for CardKit PATCH)
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| httpx | latest (>=0.27) | Raw HTTP calls to CardKit PATCH API | CardKit streaming PATCH is NOT wrapped by lark-oapi; requires direct HTTP. `httpx` has a clean async API (`AsyncClient`), excellent type hints, and `requests`-compatible ergonomics. Use `httpx.AsyncClient` to stay on the same event loop as the rest of the app. |
### Process Management
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| systemd user service | N/A | Keep the bridge alive across reboots | On Ubuntu 22.04/24.04 with a dedicated user, `systemctl --user` requires no sudo for runtime. `loginctl enable-linger` (one-time, may need sudo) allows the service to survive logout. Native OS integration beats Supervisor for a single-process service. |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dotenv | latest | Load `.env` file for APP_ID, APP_SECRET, etc. | Always — keeps secrets out of code and config files |
| pydantic | v2 | Session state models, config validation | Session isolation requires clean typed state; Pydantic v2 is fast and ships with claude-agent-sdk already |
| structlog | latest | Structured JSON logging | Small team service benefits from grep-able JSON logs over print statements; adds zero overhead in production |
| tenacity | latest | Retry logic for CardKit PATCH failures | Rate limit jitter and transient Feishu API errors need exponential backoff without reinventing it |
## Critical Version Note
## Alternatives Considered
| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Claude SDK | claude-agent-sdk | claude-code-sdk | Officially deprecated Sept 2025; no maintenance |
| HTTP client | httpx.AsyncClient | aiohttp | aiohttp is faster at scale but more verbose; this service makes low-frequency PATCH calls (not high-concurrency scraping). httpx shares the asyncio loop cleanly with no extra setup |
| Process management | systemd --user | supervisor (pip) | Supervisor needs its own daemon process running; systemd user services are native to Ubuntu 22.04+ with no extra moving parts. Exception: if `loginctl enable-linger` requires sudo that isn't available, fall back to supervisor |
| Process management | systemd --user | pm2 (Node.js) | Wrong runtime ecosystem for a Python service |
| Event transport | lark.ws.Client (WebSocket) | HTTP webhook | Webhook requires public IP or ngrok tunnel; long connection is zero-config for internal deployment |
| Async framework | raw asyncio | FastAPI/aiohttp server | No need for an HTTP server — the bot uses WebSocket long connection, not webhook. Adding a web framework adds complexity with no benefit for MVP |
## Architecture Fit
## Installation
# Create virtualenv (Python 3.10+)
# Core runtime
# Supporting
# Dev
## What NOT to Install
- `claude-code-sdk` — deprecated, do not use
- `requests` — sync-only, blocks the event loop; httpx covers both sync and async
- `flask` / `fastapi` — no HTTP server needed; long connection handles inbound events
- `redis` / `sqlite` / any DB — session state lives in memory (dict keyed by chat_id); 2-5 users don't need persistence
- `celery` — overkill; asyncio.Task is sufficient for per-message concurrency
## Confidence Assessment
| Component | Confidence | Source | Notes |
|-----------|-----------|--------|-------|
| lark-oapi 1.5.3 | HIGH | PyPI confirmed Jan 2026 | WS async handler behavior confirmed via multiple sources |
| claude-agent-sdk 0.1.53 | HIGH | PyPI confirmed Mar 2026; GitHub 6k stars | query() / ClaudeAgentOptions API confirmed |
| claude-code-sdk deprecation | HIGH | PyPI deprecation notice confirmed | Do not use |
| httpx for CardKit PATCH | HIGH | Well-established library; lark-oapi gap confirmed in PROJECT.md | |
| CardKit streaming_config params | MEDIUM | Web search consensus; official docs behind JS render | Verify `print_step`, `print_frequency_ms`, `done` fields against open.feishu.cn docs at implementation time |
| CardKit PATCH rate limit | LOW | Community reports only; official docs inaccessible | Treat ~5 req/sec as conservative estimate; batch every 300-500ms to be safe |
| systemd user service | HIGH | Well-documented Ubuntu feature; no version uncertainty | `loginctl enable-linger` may need one-time sudo |
## Sources
- lark-oapi PyPI: https://pypi.org/project/lark-oapi/ (v1.5.3, Jan 2026)
- lark-oapi GitHub: https://github.com/larksuite/oapi-sdk-python
- claude-agent-sdk PyPI: https://pypi.org/project/claude-agent-sdk/ (v0.1.53, Mar 2026)
- claude-code-sdk deprecation: https://pypi.org/project/claude-code-sdk/ (deprecated notice)
- claude-agent-sdk GitHub: https://github.com/anthropics/claude-code-sdk-python (redirects to agent sdk)
- httpx: https://www.python-httpx.org
- CardKit PATCH docs: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/cardkit-v1/card/patch (rendered JS, not fully readable)
- systemd user services: https://wiki.archlinux.org/title/Systemd/User and Ubuntu documentation
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
