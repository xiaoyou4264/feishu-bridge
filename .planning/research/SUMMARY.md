# Project Research Summary

**Project:** feishu-bridge
**Domain:** Feishu bot / AI bridge service (Feishu WebSocket long connection -> Claude Agent SDK -> CardKit streaming card)
**Researched:** 2026-04-01
**Confidence:** MEDIUM-HIGH

## Executive Summary

feishu-bridge is a Python daemon that bridges Feishu group/DM chat to Claude Code (via `claude-agent-sdk`), presenting streaming AI responses as live-updating Feishu cards. The canonical implementation pattern is a single `asyncio` event loop with a thread-safe queue bridge from `lark.ws.Client` (WebSocket long connection), per-conversation `asyncio.Task` workers, `ClaudeSDKClient` for multi-turn session context, and batched HTTP PATCH calls to the Feishu CardKit API for streaming card updates. No public IP, no HTTP server, and no database are required for the 2-5 user target scale. The stack is well-defined and mostly locked to official SDK choices.

The recommended approach is to build incrementally across four phases: (1) Feishu connectivity and event routing, (2) Claude Code integration with session management, (3) CardKit streaming card renderer, and (4) stability hardening and process management. Each phase produces a working, testable integration that the next phase builds on. The architecture's component boundaries map cleanly to these phases, and the ARCHITECTURE.md build order provides a dependency graph that must be respected.

The primary risks are a Feishu app credential collision with the existing `mi-feishu` MCP (critical — must be resolved before Phase 1 testing), Claude Agent SDK subprocess deadlocks and socket leaks (must pin `>=0.1.53`, use `async with`, add watchdog timeouts), and CardKit PATCH rate limits causing frozen cards (must batch updates at 300-500ms intervals, not per-token). These are well-understood with documented mitigations, but they must be designed in from the start — they cannot be retrofitted cleanly.

---

## Key Findings

### Recommended Stack

The entire service is a pure-Python `asyncio` application with no web framework needed. `lark-oapi==1.5.3` provides the WebSocket long connection and Feishu API wrappers; `claude-agent-sdk>=0.1.53` (not the deprecated `claude-code-sdk`) provides multi-turn Claude Code access with session context. CardKit PATCH is not wrapped by `lark-oapi` and requires raw async HTTP via `httpx.AsyncClient`. Process management is via `systemd --user` service on Ubuntu.

**Core technologies:**
- `lark-oapi[ws]==1.5.3`: Feishu WebSocket long connection + all IM/CardKit API wrappers — official ByteDance SDK, auto-reconnects, handles token refresh
- `claude-agent-sdk>=0.1.53`: Claude Code subprocess management with multi-turn session context — CRITICAL: replaces deprecated `claude-code-sdk` (deprecated Sept 2025); v0.1.53 fixes deadlock bug #780
- `httpx>=0.27` (AsyncClient): Raw PATCH to CardKit streaming API — lark-oapi does not wrap this endpoint
- `asyncio` (stdlib): Single event loop for all concurrency — both SDK and HTTP are async-native
- `python-dotenv`, `pydantic v2`, `structlog`, `tenacity`: Config, state models, structured logging, retry with backoff
- `systemd --user`: Process management — native Ubuntu, no extra dependencies, survives logout via `loginctl enable-linger`

### Expected Features

The core user expectation is: send a message, see immediate feedback, watch the AI response appear live. Everything else is secondary.

**Must have (table stakes):**
- WebSocket message receive with deduplication — Feishu retries delivery; without dedup, Claude runs twice
- Streaming card response (typing effect) — AI latency is 5-30s; users need visible progress
- Per-user/per-group session isolation — in-memory dict keyed on `open_id` (P2P) or `chat_id` (group)
- @mention detection in groups + unconditional P2P handling
- Graceful "thinking" card sent immediately on message receive — must happen within Feishu's 3-second callback window
- Error recovery per message — no crashes; bad messages get error cards, not process termination

**Should have (differentiators for daily use):**
- Tool use visibility in card — show which tools Claude invoked (`bash`, `read`, `write`)
- `/new` session reset command and `/help` command palette
- Thread reply mode (`reply_in_thread: true`)
- Working directory scoping per session

**Defer to v2+:**
- "Stop" button — high complexity (card callback + process kill within 3s); session timeout handles runaway tasks for now
- Feedback buttons (thumbs up/down)
- Image/voice message handling

### Architecture Approach

The architecture is a single process with five distinct component layers connected by asyncio primitives. A daemon thread runs `lark.ws.Client`, crossing the thread boundary into the asyncio loop via `loop.call_soon_threadsafe(queue.put_nowait, event)`. The Event Router drains the queue and dispatches events to the Session Manager. The Session Manager maps `(chat_id, user_id)` keys to per-conversation `asyncio.Task` workers. Each Conversation Worker owns one `ClaudeSDKClient` (multi-turn context) and one Card Renderer instance. The Card Renderer manages the CardKit card lifecycle (POST to create, PATCH to stream, sequence counter per card). Error isolation is enforced by task boundaries — an exception in one conversation worker does not affect others.

**Major components:**
1. **WebSocket Receiver** — `lark.ws.Client` in daemon thread; posts events to asyncio Queue via thread-safe call
2. **Event Router** — async loop draining queue; dispatches to Session Manager or Callback Handler
3. **Session Manager** — `(chat_id, user_id)` -> `asyncio.Task` registry; dedup via bounded `message_id` LRU set; enforces one-task-per-conversation invariant
4. **Conversation Worker** — per-chat asyncio.Task; owns `ClaudeSDKClient` (session context) and `ConversationState` dataclass
5. **Card Renderer** — manages CardKit POST (create) + PATCH (stream) lifecycle; owns per-card sequence counter; batches token updates at 300-500ms
6. **Config** — dotenv + pydantic validation at startup; all tunables via env vars

### Critical Pitfalls

1. **Shared Feishu app credentials with mi-feishu MCP** — Both services use `cli_a92d11a974b89bcd`. Feishu cluster-mode long connection routes each event to only one client. If mi-feishu MCP also connects via WebSocket, the bridge will silently drop ~50% of messages. Verify mi-feishu uses HTTP-only API calls (no event subscription); if not, disable one before testing.

2. **Duplicate event delivery / 3-second callback window** — If the sync event handler does any work beyond queuing, it exceeds the 3-second deadline and Feishu retries delivery, spawning duplicate Claude queries. The fix is non-negotiable: sync handler does ONLY `queue.put_nowait`, all processing in asyncio Tasks, dedup via `event_id` set.

3. **Claude Agent SDK subprocess deadlocks and CLOSE_WAIT leaks** — Pin `>=0.1.53` (fixes deadlock #780 and CLOSE_WAIT socket leak #665). Always use `async with ClaudeSDKClient(...)`. Add a per-conversation watchdog timeout (e.g., 5 minutes) and cancel the task if exceeded. Register SIGTERM handler to cancel all tasks before exit.

4. **`allowed_tools` semantics inversion** — `ClaudeAgentOptions(allowed_tools=[...])` means auto-approve these tools, NOT restrict to them. In a non-interactive bridge, an unapproved tool stalls indefinitely. Set `permission_mode="acceptEdits"` or use `disallowed_tools` explicitly.

5. **CardKit PATCH rate limits and frozen cards** — Naive per-token PATCHing hits Feishu rate limits (~5 QPS per card per community reports). Batch token buffer flushes at 300-500ms intervals using a periodic asyncio timer. Handle HTTP 429 with exponential backoff. Keep card size under 30KB (one card per response, never accumulate conversation history in a card).

---

## Implications for Roadmap

Based on the combined research, a four-phase structure is recommended. The ARCHITECTURE.md build order provides the underlying dependency graph and directly maps to phases.

### Phase 1: Feishu Connectivity and Event Pipeline

**Rationale:** Everything depends on receiving Feishu events reliably. The credential collision risk (Pitfall 1) must be resolved before any other work. This phase has no Claude dependency and produces a fully testable foundation.

**Delivers:** Config loader, WebSocket long connection, asyncio queue bridge, Event Router, message dedup, @mention/P2P filter, initial "thinking" card send (without AI content). Bot is alive and responsive.

**Addresses:** Plain text receive, graceful immediate feedback card, P2P/group detection, config via env vars.

**Avoids:** Pitfall 1 (credential collision — document and verify before testing), Pitfall 2 (3-second limit — queue bridge is the entire solution), Pitfall 7 (reconnection — use lark-oapi v1.5.3, not v1.4.6), Pitfall 13 (task-crash isolation, not process-crash).

**Research flag:** Standard patterns, no additional research needed. lark-oapi is well-documented.

---

### Phase 2: Claude Code Integration and Session Management

**Rationale:** Session management depends on the event pipeline (Phase 1). Claude SDK integration is the core value driver and the highest-risk component due to subprocess complexity. Isolating it in Phase 2 allows focused stabilization before adding CardKit complexity.

**Delivers:** Session Manager with per-conversation asyncio.Task workers, `ClaudeSDKClient` multi-turn context, `ClaudeAgentOptions` configuration, watchdog timeout, SIGTERM handler, basic text reply (not yet streaming card). End-to-end: user message -> Claude response -> plain text reply.

**Addresses:** Session isolation, multi-turn context preservation, error recovery per message, `/new` session reset.

**Uses:** `claude-agent-sdk>=0.1.53`, `pydantic v2` for `ConversationState`, `asyncio.Task` per conversation.

**Avoids:** Pitfall 3 (deadlocks — pin version, use async with, watchdog), Pitfall 4 (CLOSE_WAIT — verify CPU baseline after conversation ends), Pitfall 6 (session race conditions — per-session asyncio lock, atomic dict.setdefault()), Pitfall 8 (allowed_tools semantics — set permission_mode explicitly).

**Research flag:** Standard patterns for asyncio Task management. SDK-specific configuration (`permission_mode`, `ClaudeAgentOptions`) should be validated against official docs at implementation time.

---

### Phase 3: CardKit Streaming Card Renderer

**Rationale:** Card rendering depends on both event pipeline (Phase 1) and Claude streaming output (Phase 2). It is isolated here because it has its own rate limit risk profile and the Card Renderer is an independent component. By Phase 3, Claude output is known to be stable.

**Delivers:** Card Renderer with CardKit POST (create) + PATCH (stream) lifecycle, per-card sequence counter, 300-500ms batched flush, HTTP 429 handling with backoff, typing indicator, tool use visibility in card, final flush on `ResultMessage`. Full streaming UX: user sees text appear live.

**Addresses:** Streaming card response (table stakes), typing indicator, tool use visibility, file operation results in card.

**Uses:** `httpx.AsyncClient` for CardKit PATCH (not wrapped by lark-oapi), `tenacity` for retry with jitter.

**Avoids:** Pitfall 5 (rate limits — batch at 300-500ms, handle 429, never per-token), Pitfall 10 (card size growth — one card per response, never accumulate history in card payload).

**Research flag:** CardKit PATCH `streaming_config` parameters (`print_step`, `print_frequency_ms`, `print_strategy`) need verification against official docs at implementation time. Official doc pages returned JS boilerplate during research — MEDIUM confidence only.

---

### Phase 4: Stability, Polish, and Process Management

**Rationale:** Once the core loop works end-to-end, harden for production use. This phase has no new feature dependencies and can be partially parallelized.

**Delivers:** systemd user service unit, `loginctl enable-linger`, structured JSON logging with event_id correlation, idle session cleanup (TTL-based), SIGTERM handler (cancel all tasks, wait, exit), CPU/CLOSE_WAIT monitoring hook, `/help` command, thread reply mode.

**Addresses:** Systemd process management, operational observability, graceful shutdown.

**Uses:** `systemd --user`, `structlog`, `asyncio.signal` handlers.

**Avoids:** Pitfall 12 (SIGTERM incomplete cleanup), Pitfall 4 (CLOSE_WAIT — add CPU alert trigger), Pitfall 11 (event loop blocking — enforce in all new code).

**Research flag:** Standard patterns. systemd user services on Ubuntu are well-documented with no uncertainty.

---

### Phase Ordering Rationale

- **Dependency chain enforced:** Config -> WebSocket -> Queue Bridge -> Event Router -> Session Manager -> Conversation Worker -> Card Renderer. Each component requires the prior one.
- **Risk front-loading:** The credential collision (Pitfall 1) and 3-second limit (Pitfall 2) are addressed in Phase 1 before any costly SDK integration work begins.
- **Isolated stabilization:** Separating Claude SDK integration (Phase 2) from CardKit streaming (Phase 3) allows each to be validated independently. Debugging deadlocks and debugging rate limits simultaneously would be intractable.
- **Avoids metabot failure modes:** The predecessor Node.js bot failed due to monolithic handlers and no crash isolation. The phased architecture establishes task-level crash isolation in Phase 1 and does not deviate from it.

### Research Flags

Phases needing deeper research during planning:
- **Phase 3 (CardKit PATCH):** `streaming_config` fields (`print_step`, `print_frequency_ms`, `done` flag) and exact rate limit values need live doc verification. Official Feishu CardKit docs were behind JS rendering during research. Recommend a focused `/gsd:research-phase` or manual doc check before Phase 3 implementation.

Phases with standard patterns (skip research-phase):
- **Phase 1:** `lark-oapi` WebSocket is well-documented via official GitHub and PyPI. asyncio queue bridge is a textbook pattern.
- **Phase 2:** `claude-agent-sdk` official docs are accessible and verified (platform.claude.com). Session management with asyncio Tasks is standard.
- **Phase 4:** systemd user services on Ubuntu are mature and thoroughly documented.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Core libraries verified via PyPI/GitHub; SDK deprecation confirmed; critical version requirements documented |
| Features | MEDIUM | Core Feishu API patterns well-known; Claude SDK streaming behavior inferred from docs + training data; CardKit specifics partially unverified |
| Architecture | HIGH | Official Claude Agent SDK sessions docs verified; Feishu 3-second constraint documented; asyncio patterns are standard |
| Pitfalls | MEDIUM-HIGH | SDK bugs verified via CHANGELOG; Feishu credential collision is documented platform behavior; CardKit rate limits MEDIUM (community sources only) |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **CardKit `streaming_config` exact parameter values:** Official docs inaccessible during research (JS rendering). Treat `print_step: 2`, `print_frequency_ms: 30`, `print_strategy: "fast"` as directional; verify against `open.feishu.cn` at Phase 3 start.
- **CardKit PATCH rate limits:** Community reports suggest ~5 QPS per card, 50 QPS app-wide. Not confirmed in official docs. Design the 300-500ms batch flush conservatively and monitor 429 responses in Phase 3 testing.
- **mi-feishu MCP WebSocket subscription status:** Must be verified before Phase 1 testing. If the MCP connects via long connection, one service must be disabled or a separate Feishu app must be created for the bridge.
- **`lark.ws.Client` async handler invocation model:** Research indicates the sync handler is called from a daemon thread and must use `loop.call_soon_threadsafe()`. Confirm the exact thread model via lark-oapi GitHub source before Phase 1 implementation to avoid the wrong threading pattern.

---

## Sources

### Primary (HIGH confidence)
- `lark-oapi` PyPI v1.5.3 (Jan 2026): https://pypi.org/project/lark-oapi/
- `lark-oapi` GitHub (official ByteDance): https://github.com/larksuite/oapi-sdk-python
- `claude-agent-sdk` PyPI v0.1.53 (Mar 2026): https://pypi.org/project/claude-agent-sdk/
- `claude-code-sdk` deprecation notice: https://pypi.org/project/claude-code-sdk/
- Claude Agent SDK official docs (sessions, Python reference, overview): https://platform.claude.com/docs/en/agent-sdk/
- Claude Agent SDK CHANGELOG (bugs #578, #630, #665, #728, #780): https://github.com/anthropics/claude-code-sdk-python/blob/main/CHANGELOG.md
- Feishu long connection constraints (3s timeout, cluster/no-broadcast, max 50 connections): https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events
- systemd user services: https://wiki.archlinux.org/title/Systemd/User

### Secondary (MEDIUM confidence)
- CardKit streaming PATCH API patterns: https://open.feishu.cn/document/cardkit/v1/card/overview (JS-rendered, web search synthesis)
- Feishu IM API rate limits: Feishu IM v1 message docs (search synthesis)
- lark-oapi WebSocket + asyncio integration patterns: GitHub issues + PyPI

### Tertiary (LOW confidence)
- CardKit PATCH rate limits (~5 QPS/card, 50 QPS app-wide, 30KB size limit): multiple community search sources; not verified in official docs. Treat as directional.

---
*Research completed: 2026-04-01*
*Ready for roadmap: yes*
