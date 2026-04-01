# Domain Pitfalls: Feishu ↔ Claude Code Bridge

**Domain:** Feishu bot ↔ AI bridge service
**Researched:** 2026-04-01
**Confidence:** MEDIUM — Core pitfalls verified via official SDK docs, GitHub issues, and Feishu platform docs. CardKit rate limits unverified (Feishu doc pages return JS boilerplate only).

---

## Critical Pitfalls

Mistakes that cause rewrites, data loss, or systemic instability.

---

### Pitfall 1: Cluster Mode Event Routing — Shared App Credentials

**What goes wrong:**
The project reuses `cli_a92d11a974b89bcd` — the same Feishu app credentials shared with `mi-feishu` MCP. When multiple processes connect via WebSocket long connection with the same App ID, Feishu's platform routes each event to **one random connected client** (cluster mode, no broadcast). The MCP server and the bridge compete for the same events. Some user messages silently land in the MCP process and are dropped.

**Why it happens:**
Feishu's long connection SDK explicitly states: "消息推送为集群模式，不支持广播" — only one of up to 50 connected clients receives each message. There is no namespace or service-key filtering; all clients with the same App ID share a single routing pool.

**Consequences:**
- Intermittent dropped messages (race condition, hard to reproduce)
- Events land in the wrong process, silently discarded
- No error or log on the receiving process — it just processes a message meant for the other service

**Prevention:**
- **Do not run the bridge and `mi-feishu` MCP simultaneously on the same machine** while both use WebSocket long connection.
- `mi-feishu` MCP uses HTTP-based API calls (it reads/sends Feishu messages on demand, not via event subscription). Verify this before deployment. If mi-feishu MCP also subscribes to events via long connection, one must be disabled.
- Long-term: request a separate Feishu app for the bridge to fully isolate routing.

**Warning signs:**
- User sends a message, no reply appears; no error in bridge logs
- Intermittent failures correlating with when `mi-feishu` MCP is active
- `lark.ws.Client` connection count approaching 2 when both services run

**Phase:** Must address in Phase 1 (Feishu connectivity). Document the constraint in deployment notes.

---

### Pitfall 2: Duplicate Event Delivery and Idempotent Handlers

**What goes wrong:**
If the bridge does not respond to a Feishu event within 3 seconds, the platform retries delivery up to 3 times with increasing backoff. If the first delivery triggers a Claude Code subprocess that takes 5+ seconds to start, the event fires twice, spawning two concurrent AI responses to the same message. The user sees two replies.

**Why it happens:**
The 3-second deadline applies to the SDK's event handler coroutine returning control. Blocking on Claude Code initialization (CLI discovery, subprocess spawn, MCP server registration) inside the handler exceeds this deadline trivially. The SDK `lark.ws.Client` does acknowledge messages at the transport level, but Feishu's server-side retry is based on its own timeout, not the SDK acknowledgement.

**Consequences:**
- Duplicate AI responses in chat
- Two concurrent Claude Code subprocesses consuming tokens and compute
- Session state corruption if both responses write to the same session

**Prevention:**
- Return from the event handler immediately — use `asyncio.create_task()` to offload all processing.
- Maintain a short-lived deduplication set (keyed on `event_id` from `data.header.event_id`) with TTL of ~60 seconds. In-memory is sufficient for a single-process service.
- Design the Claude Code invocation path to be idempotent: if a task for a given `message_id` is already running, discard the duplicate.

**Warning signs:**
- Duplicate replies in Feishu chat
- Two `ClaudeSDKClient` context managers running for the same `open_message_id`
- Event handler duration metrics exceeding 500ms

**Phase:** Phase 1 (event handling) and Phase 2 (Claude Code invocation).

---

### Pitfall 3: Claude Code SDK — Subprocess Deadlocks and Hangs

**What goes wrong:**
The `claude_code_sdk` / `claude_agent_sdk` spawns a Claude Code CLI subprocess and communicates via stdin/stdout pipes. Multiple specific deadlock scenarios have been found and fixed in recent versions, but the underlying architecture is still prone to hangs:

- String prompt passed to `connect()` before MCP server initialization completes causes the SDK to close stdin early → MCP tools fail to register (was bug #578, #630)
- `SubprocessCLITransport.close()` hangs indefinitely if the subprocess shutdown handler blocks (bug #728)
- `wait_for_result_and_end_input()` called synchronously can deadlock when hooks/tools are active (bug #780, fixed in v0.1.53)
- Async generator cleanup across task boundaries raises `RuntimeError` (bug #454, #746)

**Why it happens:**
The SDK bridges asyncio event loops and OS subprocess pipes. Race conditions in teardown, especially when the bridge handles SIGTERM or Feishu triggers concurrent requests, expose edge cases.

**Consequences:**
- Hung tasks consume fd and memory indefinitely
- SIGTERM during teardown does not clean up CLI process → zombie subprocess
- In long-running bridge, zombie accumulation eventually exhausts process limits

**Prevention:**
- Pin `claude_code_sdk` / `claude_agent_sdk` to `>=0.1.53` (contains deadlock fix #780).
- Always drive `query()` or `ClaudeSDKClient` from an `async with` block with explicit cancellation. Do not hold references across task boundaries.
- Implement a watchdog: if a Claude invocation has not completed after N minutes, cancel the task and send an error card to the user.
- On bridge SIGTERM, cancel all running Claude tasks before calling `sys.exit()`. Use `asyncio.wait_for()` with a hard timeout.
- Use `query()` (stateless) rather than `ClaudeSDKClient` (stateful) for simple single-turn requests to reduce lifecycle complexity.

**Warning signs:**
- `ps aux` shows `node` processes that are children of the bridge but receive no CPU
- `lsof` shows sockets in `CLOSE_WAIT` state from the bridge process (SDK bug #665 pattern)
- Event queue grows without being processed

**Phase:** Phase 2 (Claude Code integration) and Phase 4 (stability/process management).

---

### Pitfall 4: CLOSE_WAIT Socket Leak — CPU Spin in Long-Running Daemon

**What goes wrong:**
When `ClaudeSDKClient.disconnect()` is called, the underlying `SubprocessCLITransport` closes stdin/stderr but historically did NOT call `aclose()` on the stdout stream. The file descriptor stays registered in the asyncio event loop selector (epoll/kqueue). `CLOSE_WAIT` sockets are permanently "readable" (EOF), so the selector returns them immediately every poll cycle — the event loop busy-spins at ~24% CPU between requests.

**Why it happens:**
SDK bug #665 (fixed in v0.1.51 via PR #712). Even after the fix, any code path that exits a `ClaudeSDKClient` context manager abnormally (exception, cancellation) may not guarantee `aclose()` runs.

**Consequences:**
- CPU never returns to baseline between conversations
- Over time, accumulated leaked fds approach system limits
- On Linux the pattern is subtler than macOS kqueue but the underlying fd leak still occurs

**Prevention:**
- Use `>=0.1.51` of the SDK (contains the fix).
- Wrap every `ClaudeSDKClient` usage in `async with` — never call `connect()` / `disconnect()` manually.
- Add periodic monitoring: alert if bridge process CPU is >5% when idle.

**Warning signs:**
- `lsof -p <pid> | grep CLOSE_WAIT` shows accumulating sockets
- CPU stays elevated between Feishu messages
- `asyncio.get_event_loop().is_running()` spin detected in profiler

**Phase:** Phase 2 (Claude Code integration). Must be verified before marking stable.

---

### Pitfall 5: CardKit Streaming — Rate Limits Causing Silent Drops

**What goes wrong:**
Naively forwarding every streaming token from Claude Code to a Feishu card update results in hundreds of PATCH requests per second. Feishu enforces rate limits (MEDIUM confidence: ~5 QPS per card, 50 QPS app-wide). Exceeding these limits returns HTTP 429. If the bridge does not handle 429 with backoff, updates silently fail and the card freezes mid-stream.

**Why it happens:**
Claude Code's streaming output arrives as individual `TextBlock` events, potentially dozens per second. Without batching, every chunk triggers a separate `PATCH /open-apis/cardkit/v1/cards/{card_id}` call.

**Consequences:**
- Card appears to freeze partway through streaming
- HTTP 429 errors fill logs
- If not handled, subsequent card updates for other users are also throttled (app-wide limit)

**Prevention:**
- Batch card updates: accumulate tokens into a buffer, flush to Feishu at most every 300-500ms using a periodic `asyncio` timer.
- Handle HTTP 429 explicitly: pause updates and retry after the `Retry-After` header interval (or 1-2 seconds if absent).
- Send a final flush after Claude finishes to ensure the last partial buffer is committed.
- Keep card JSON size well under 30KB — long conversations must truncate or paginate.

**Warning signs:**
- HTTP 429 responses in CardKit PATCH calls
- Card stops updating mid-response but Claude Code is still running
- `asyncio.sleep(0)` tight loops in update path

**Phase:** Phase 3 (streaming card implementation). Batching must be designed in, not retrofitted.

---

## Moderate Pitfalls

---

### Pitfall 6: Session Isolation — Race Conditions in Concurrent Requests

**What goes wrong:**
Two users send messages simultaneously. The session manager must map each user/chat to an independent Claude Code invocation. If sessions are stored in a plain dict and accessed from multiple concurrent coroutines without locking, writes can interleave: user A's session context gets contaminated with user B's message history.

**Why it happens:**
Python asyncio is single-threaded but `await` yields control. If `session_dict[user_id] = new_session` and `session_dict[user_id].add_message(msg)` are separate operations with an `await` between them, another coroutine can run between them.

**Prevention:**
- Use `asyncio.Lock` per session keyed on `(chat_id, user_id)`.
- Prefer immutable session creation: build the complete initial state before inserting into the registry.
- Use `dict.setdefault()` for atomic "get or create" patterns.
- For 2-5 users, in-memory session state is fine; no external locking needed.

**Warning signs:**
- User receives AI response referencing a different user's conversation
- `KeyError` or `AttributeError` on session objects under concurrent load

**Phase:** Phase 2 (session management design).

---

### Pitfall 7: Feishu Long Connection — No Explicit Reconnection Control

**What goes wrong:**
`lark.ws.Client.start()` is a blocking call that handles reconnection internally. The reconnection logic was added in v1.4.9 (February 2025). Earlier SDK versions (including v1.4.6 specified in PROJECT.md) may have incomplete reconnection. If the WebSocket drops (network blip, Feishu server restart), the bridge silently stops receiving events with no visible error.

**Why it happens:**
`lark-oapi` v1.4.6 is behind the current v1.5.3 (January 2026). Reconnection logic was specifically added in v1.4.9 (three versions later). Using v1.4.6 means no automatic reconnect.

**Consequences:**
- Bridge process stays running but receives no messages
- No error in logs — the WebSocket just stops delivering
- Users see the bot as unresponsive

**Prevention:**
- Upgrade to `lark-oapi>=1.4.9` (or latest v1.5.3) for reconnection support.
- Add a heartbeat check: if no event received in N minutes, attempt to reconnect or alert.
- Run `cli.start()` in a supervised async task that restarts on exception.

**Warning signs:**
- Bridge is "running" but no events processed for extended period
- No `disconnect` or `error` log entries — just silence
- Manual `ping` to Feishu API succeeds but no bot reply

**Phase:** Phase 1 (Feishu connectivity). Address version pinning immediately.

---

### Pitfall 8: Claude Code SDK — `allowed_tools` Semantics Inversion

**What goes wrong:**
`ClaudeAgentOptions(allowed_tools=["Read", "Write", "Bash"])` does NOT mean only these tools are available. It means these tools are auto-approved without prompting. Other tools still exist and trigger permission prompts. In a non-interactive bridge context with no human to answer permission prompts, tool use stalls indefinitely.

**Why it happens:**
The SDK documentation is explicit but counterintuitive: `allowed_tools` is an allowlist for auto-approval, not a restriction list. `disallowed_tools` is the restriction mechanism.

**Consequences:**
- Claude requests a tool not in `allowed_tools`; bridge hangs waiting for approval that never comes
- No timeout → hung task (see Pitfall 3)

**Prevention:**
- Set `permission_mode="acceptEdits"` for non-interactive bridge use, OR
- Explicitly set `disallowed_tools` for tools that should never run
- Always pair with a task watchdog timeout (see Pitfall 3 prevention)

**Warning signs:**
- Claude task running but no output produced for >30 seconds
- No error, no result — silent stall

**Phase:** Phase 2 (Claude Code integration configuration).

---

### Pitfall 9: Feishu App Shared with MCP — Duplicate Message Sends

**What goes wrong:**
If the bridge and `mi-feishu` MCP are both active, and the MCP also sends messages (e.g., in response to a tool call that the bridge forwarded), the same conversation can receive two replies: one from the bridge and one from the MCP, both appearing as bot messages from the same app.

**Why it happens:**
Both services share the same App ID and therefore the same bot identity in Feishu. There is no sender differentiation from the user's perspective.

**Prevention:**
- Ensure `mi-feishu` MCP sends messages only when explicitly invoked, not in response to events.
- Document the shared identity as a constraint in the bridge's operational notes.
- For long-term safety, request a dedicated bot app for the bridge.

**Warning signs:**
- User receives unexpected messages from the bot
- Two replies appear in quick succession after one user message

**Phase:** Phase 1 deployment. Document and mitigate before first user test.

---

## Minor Pitfalls

---

### Pitfall 10: Card Size Growth — Long Conversations Break Cards

**What goes wrong:**
If the bridge appends all conversation history into a single card (to simulate a chat thread), the card JSON size grows with every exchange. Feishu limits card content size (~30KB, MEDIUM confidence). Long conversations silently fail to update or produce a truncation error.

**Prevention:**
- Never store full conversation history in a card. Use the card for the current reply only.
- For multi-turn context, store conversation history in memory (Python dict) keyed on session ID, not in the card payload.

**Phase:** Phase 3 (card design). Must be decided before implementation.

---

### Pitfall 11: asyncio Event Loop Blocking — Synchronous Operations

**What goes wrong:**
Using synchronous HTTP calls (e.g., `requests.get()`) or blocking file I/O inside an asyncio coroutine blocks the entire event loop. No other events are processed during the block. For the bridge, this means Feishu events queue up while a blocking call runs.

**Prevention:**
- Use `aiohttp` or `httpx` (async) for all outbound HTTP calls, including CardKit PATCH.
- For blocking I/O, use `asyncio.run_in_executor()`.
- Never use `time.sleep()` — use `asyncio.sleep()`.

**Phase:** Throughout all phases. Enforce in code review.

---

### Pitfall 12: SIGTERM Handling — Incomplete Cleanup on Deploy

**What goes wrong:**
If the bridge process receives SIGTERM (e.g., from `systemd stop` or `kill`) while a Claude response is streaming, the subprocess may not be properly terminated. The Claude Code CLI child process continues running, orphaned, consuming tokens and resources.

**Prevention:**
- Register a SIGTERM handler in the bridge: cancel all running Claude tasks, wait for cleanup with a hard timeout (5 seconds), then exit.
- Use `asyncio.shield()` carefully — only for final card flush, not for the full Claude invocation.

**Warning signs:**
- After bridge restart, `ps aux | grep claude` shows orphaned processes
- Token usage logs show unexpectedly high consumption

**Phase:** Phase 4 (stability and process management).

---

### Pitfall 13: Metabot Anti-Patterns to Avoid

The predecessor metabot (Node.js/TypeScript) was removed due to poor customizability and stability. Based on the project context, the likely root causes were:

| Metabot Problem | Root Cause | Bridge Prevention |
|-----------------|------------|-------------------|
| Poor customizability | Hardcoded behavior, no config system | Use env vars + config file for all tunables |
| Stability issues | No crash recovery, unhandled promise rejections | asyncio exception handling per-task, not top-level |
| Difficult to modify | Monolithic handler | Separate event routing, session management, Claude invocation, and card update into distinct modules |
| No observability | Silent failures | Structured logging with event_id correlation throughout |

The single most important architectural decision: **crash one task, not the process**. Each incoming Feishu event should be wrapped in a `try/except` that logs the error and sends an error card reply, never propagating to the top-level event loop.

**Phase:** Establish in Phase 1 architecture. Non-negotiable.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Phase 1: Feishu long connection | SDK version too old for reconnection | Upgrade to `>=1.4.9` |
| Phase 1: Feishu long connection | Shared app credential event routing clash | Verify `mi-feishu` MCP does not use WebSocket event subscription |
| Phase 1: Event handling | Duplicate delivery on timeout retry | Implement `event_id` deduplication + async task offload |
| Phase 2: Claude Code integration | Subprocess deadlocks and hangs | Pin `>=0.1.53`, use watchdog timeouts |
| Phase 2: Claude Code integration | `allowed_tools` semantics confusion | Set `permission_mode` explicitly |
| Phase 2: Session management | Concurrent session state corruption | Per-session asyncio locks |
| Phase 3: CardKit streaming | Rate limit exceeded | Batch updates at 300-500ms intervals |
| Phase 3: CardKit streaming | Card size growth | One card per response, not per conversation |
| Phase 4: Process stability | SIGTERM cleanup incomplete | SIGTERM handler with cascading cancel |
| Phase 4: Process stability | CLOSE_WAIT socket leak | Monitor CPU between messages, verify fix in SDK version |

---

## Sources

**HIGH confidence (official documentation verified):**
- Feishu long connection constraints (max 50 connections, cluster/no-broadcast, 3s timeout): [Feishu Open Platform — Handle Events](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events)
- Feishu IM API rate limits (1000/min, 50/s global; 5 QPS per user/group): Feishu IM v1 message docs
- `lark-oapi` reconnection added in v1.4.9: [PyPI lark-oapi release history](https://pypi.org/project/lark-oapi/#history)
- `lark-oapi` current version v1.5.3: same source

**HIGH confidence (SDK changelog / GitHub issues verified):**
- Subprocess deadlocks (bugs #578, #630, #728, #780): [claude-agent-sdk-python CHANGELOG](https://github.com/anthropics/claude-code-sdk-python/blob/main/CHANGELOG.md)
- CLOSE_WAIT socket leak (bug #665, fixed PR #712): GitHub issue content
- `allowed_tools` semantics: SDK README
- SIGTERM hang (bug #728): SDK CHANGELOG

**MEDIUM confidence (search synthesis, unverified against live docs):**
- CardKit PATCH rate limits (~5 QPS per card, 50 QPS app-wide, 30KB size limit): multiple search sources agree; official Feishu CardKit doc pages were inaccessible (returned frontend JS only). Treat as directionally correct but verify before Phase 3 implementation.
