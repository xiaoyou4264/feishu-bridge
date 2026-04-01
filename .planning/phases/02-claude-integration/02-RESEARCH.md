# Phase 2: Claude Integration - Research

**Researched:** 2026-04-01
**Domain:** claude-agent-sdk v0.1.53, asyncio session management, Feishu card update API
**Confidence:** HIGH (SDK API verified via official GitHub README + source; Feishu event structure verified via official docs April 2025; card update API verified via official sample code)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-07:** Use `claude-agent-sdk` `query()` method with streaming mode. Phase 2: complete response updates card once. Phase 3: streaming card update.
- **D-08:** Per-session `ClaudeSDKClient` instance in `asyncio.Task`.
- **D-09:** Investigate SDK session capabilities first; fallback to manual history if SDK does not support concurrent queries.
- **D-10:** Group chat parallel processing is a hard requirement.
- **D-11:** `asyncio.Semaphore(MAX_CONCURRENT_TASKS)`, default 5.
- **D-12:** Queue overflow: block (Semaphore natural blocking), do not drop.
- **D-13:** Group chat messages parallel (not serial).
- **D-14:** Group chat messages inject sender prefix: `[display_name]: message_content`.
- **D-15:** P2P messages: no prefix.
- **D-16:** `asyncio.wait_for` timeout per Claude call, `CLAUDE_TIMEOUT` env var, default 120s.
- **D-17:** `/new` command clears session (destroys client instance, next message creates new one).

### Claude's Discretion

- Claude Agent SDK specific initialization parameters (model, max_tokens, etc.)
- Error card style and copy
- Session manager memory data structure
- Manual history window size when fallback is used

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CLAUDE-01 | Call Claude Code via `claude-agent-sdk` | SDK query() and ClaudeSDKClient patterns verified |
| CLAUDE-02 | Stream Claude responses (token-by-token) | ClaudeSDKClient.receive_response() yields AssistantMessage chunks |
| CLAUDE-03 | Multi-turn conversation with context continuity | ClaudeSDKClient maintains session across query() calls; concurrent limitation documented |
| CLAUDE-04 | Per-session asyncio Task, exception isolation | asyncio.create_task() + done_callback pattern |
| CLAUDE-05 | Single-message timeout watchdog | asyncio.wait_for() around receive_response() loop |
| CLAUDE-06 | Optional file system / command execution via config | ClaudeAgentOptions.allowed_tools + permission_mode |
| SESS-01 | P2P session isolated by open_id | session key = open_id |
| SESS-02 | Group chat session shared by chat_id, inject sender name | session key = chat_id; display_name via contact API |
| SESS-03 | /new command resets session | destroy ClaudeSDKClient instance; create new on next message |
| CONC-01 | Messages processed in parallel by asyncio Tasks | asyncio.create_task() per message |
| CONC-02 | Configurable MAX_CONCURRENT_TASKS | asyncio.Semaphore(n) wrapping each Task's Claude call |
| CONC-03 | Group chat multi-user parallel processing | Each message gets its own Task; shared session_history dict with asyncio.Lock |

</phase_requirements>

---

## Summary

Phase 2 introduces the Claude Agent SDK pipeline into the existing Phase 1 event handler. The core flow is: `handle_message()` sends a thinking card, then dispatches a `SessionManager.dispatch()` call that creates an `asyncio.Task` per message. Each task acquires the global semaphore, calls `ClaudeSDKClient.query()`, drains `receive_response()` to collect the full text, then updates the thinking card via `im.v1.message.patch`.

The SDK's `ClaudeSDKClient` supports multi-turn conversation natively: `await client.query(text)` followed by `async for msg in client.receive_response()` is repeatable within the same `async with ClaudeSDKClient() as client:` context. However, the client is NOT safe for concurrent query calls — only one outstanding `query()` at a time per instance. This has a direct design implication: for group chats where multiple users send messages concurrently, a per-session `asyncio.Lock` must serialize access to the shared `ClaudeSDKClient`, while still allowing multiple sessions to proceed in parallel.

The sender's display name is NOT included in the `im.message.receive_v1` event payload. It must be fetched from `contact/v3/users/{open_id}` asynchronously using `client.contact.v3.user.aget()`. A per-session cache avoids repeated API calls for the same user.

**Primary recommendation:** Use `ClaudeSDKClient` (not bare `query()`) with `permission_mode='acceptEdits'` for non-interactive use; per-session `asyncio.Lock` to serialize concurrent turns within one session; `asyncio.Semaphore` globally to cap concurrent Claude subprocesses.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| claude-agent-sdk | 0.1.53 | Claude Code subprocess + streaming | Only non-deprecated Anthropic agent SDK; bundles Claude CLI |
| asyncio | stdlib | Concurrency primitives | Native to Python 3.10+; all SDK and lark-oapi async paths are asyncio |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| lark-oapi | 1.5.3 | Feishu IM message patch + user info API | Already installed (Phase 1) |
| pydantic | v2 | Session state dataclass validation | Already available (Phase 1 dep) |

### Not Needed for Phase 2

- httpx: Reserved for Phase 3 CardKit PATCH streaming. Phase 2 uses `im.v1.message.patch` via lark-oapi, not raw HTTP.

**Installation (additions to requirements.txt):**

```bash
pip install claude-agent-sdk==0.1.53
```

**Version verification (confirmed 2026-03-31):**
`claude-agent-sdk` 0.1.53 is the latest as of 2026-03-31 per PyPI. Development Status: Alpha.

---

## Architecture Patterns

### Recommended Project Structure

```
src/
├── handler.py        # EXISTING — add SessionManager.dispatch() call after thinking card
├── session.py        # NEW — SessionManager: session dict, semaphore, dispatch, cleanup
├── claude_worker.py  # NEW — single_turn_worker(): acquire semaphore, call SDK, update card
├── cards.py          # EXISTING — add update_card_content() and send_error_card()
├── config.py         # EXISTING — add CLAUDE_TIMEOUT, MAX_CONCURRENT_TASKS, ALLOWED_TOOLS
├── filters.py        # EXISTING — unchanged
└── dedup.py          # EXISTING — unchanged
```

### Pattern 1: ClaudeSDKClient Multi-Turn with Per-Session Lock

**What:** Each chat session holds one `ClaudeSDKClient` instance plus an `asyncio.Lock`. New messages from any user in the same chat acquire the lock before calling `query()`. This allows group chats to process messages from multiple users in parallel at the session level, but serializes them within one SDK instance.

**When to use:** Always — the SDK client is stateful and not concurrent-safe.

```python
# Source: https://github.com/anthropics/claude-code-sdk-python (client.py docs)
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage

@dataclass
class SessionState:
    chat_id: str
    session_key: str          # open_id for P2P, chat_id for group
    client: ClaudeSDKClient
    lock: asyncio.Lock        # serializes concurrent query() calls
    last_activity: float

async def run_turn(session: SessionState, prompt: str) -> str:
    """Run one Claude turn. Must be called under session.lock."""
    await session.client.query(prompt)
    full_text = ""
    async for msg in session.client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    full_text += block.text
        # ResultMessage signals end — receive_response() auto-terminates
    return full_text
```

### Pattern 2: Global Semaphore + Session Lock Ordering

**What:** Two levels of concurrency control. The global `asyncio.Semaphore` caps total active Claude subprocesses. The per-session `asyncio.Lock` ensures only one `query()` runs at a time within a session.

**When to use:** Every Claude invocation.

```python
# Source: D-11, D-12 decisions + asyncio stdlib
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)  # e.g., 5

async def single_turn_worker(session, prompt, reply_message_id, api_client):
    async with semaphore:           # global cap — blocks if 5 already running
        async with session.lock:    # per-session serialization
            try:
                text = await asyncio.wait_for(
                    run_turn(session, prompt),
                    timeout=CLAUDE_TIMEOUT
                )
                await update_card_content(api_client, reply_message_id, text)
            except asyncio.TimeoutError:
                await send_error_card(api_client, reply_message_id, "超时")
            except Exception as exc:
                await send_error_card(api_client, reply_message_id, str(exc))
```

### Pattern 3: Session Key Design

**What:** P2P sessions keyed by `sender.sender_id.open_id`; group sessions keyed by `message.chat_id`.

```python
# Source: D-08, SESS-01, SESS-02 decisions
def get_session_key(message) -> str:
    if message.chat_type == "p2p":
        return data.event.sender.sender_id.open_id
    else:  # group
        return message.chat_id
```

### Pattern 4: display_name Injection for Group Chat

**What:** For group messages, prepend `[name]: ` to the prompt before passing to Claude. Fetch display name via lark-oapi contact API with per-session user cache.

```python
# Source: https://docs.pingcode.com/baike/3312456 (verified against lark-oapi sample)
# Required app permission: contact:user.base:readonly
from lark_oapi.api.contact.v3 import GetUserRequest

async def get_display_name(api_client, open_id: str, name_cache: dict) -> str:
    if open_id in name_cache:
        return name_cache[open_id]
    request = GetUserRequest.builder() \
        .user_id(open_id) \
        .user_id_type("open_id") \
        .build()
    resp = await api_client.contact.v3.user.aget(request)
    name = resp.data.user.name if resp.success() else open_id  # fallback to open_id
    name_cache[open_id] = name
    return name

# Usage (D-14, D-15):
if message.chat_type == "group":
    display_name = await get_display_name(api_client, sender_open_id, session_name_cache)
    claude_prompt = f"[{display_name}]: {text}"
else:
    claude_prompt = text  # P2P: no prefix
```

### Pattern 5: Card Update via im.v1.message.patch

**What:** After Claude responds, replace the "thinking" card content with the actual response using `PatchMessageRequest`. Phase 2 does one final patch with full text; Phase 3 will do incremental patches.

```python
# Source: https://github.com/larksuite/oapi-sdk-python samples/api/im/v1/patch_message_sample.py
import json
from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

async def update_card_content(api_client, message_id: str, text: str) -> None:
    card = {
        "header": {
            "title": {"tag": "plain_text", "content": "AI 助手"},
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": text}
        ],
    }
    request = PatchMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(
            PatchMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        ) \
        .build()
    resp = await api_client.im.v1.message.apatch(request)
    if not resp.success():
        raise RuntimeError(f"Card update failed: {resp.code} {resp.msg}")
```

**Note on `apatch`:** lark-oapi follows the pattern `client.im.v1.message.areply()` for async (confirmed in Phase 1). The async version of `patch` is likely `apatch`. If `apatch` does not exist, use `BaseRequest` raw HTTP call. Verify at implementation time.

### Pattern 6: /new Command Handling

```python
# Source: D-17 decision
async def handle_new_command(session_manager, session_key: str, api_client, message_id: str):
    await session_manager.destroy_session(session_key)
    # Send confirmation card
    card = {
        "header": {"title": {"tag": "plain_text", "content": "AI 助手"}, "template": "green"},
        "elements": [{"tag": "markdown", "content": "会话已重置，开始新对话吧！"}],
    }
    # ... send via areply
```

### Pattern 7: ClaudeSDKClient Initialization

```python
# Source: https://github.com/anthropics/claude-code-sdk-python README (verified)
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

options = ClaudeAgentOptions(
    permission_mode="acceptEdits",   # non-interactive: auto-approve file edits
    cwd=config.working_dir,          # from WORKING_DIR env var
    allowed_tools=config.allowed_tools,  # from ALLOWED_TOOLS env var, e.g. ["Read","Bash"]
    # model: leave None to use default (Claude's discretion per context)
)

# Create and keep alive for session lifetime:
async with ClaudeSDKClient(options=options) as client:
    # client survives multiple await client.query() calls
    # destroy context (exit async with) when session is reset or idle
    ...
```

### Anti-Patterns to Avoid

- **Using bare `query()` for multi-turn**: `query()` is stateless — each call creates a fresh session. Use `ClaudeSDKClient` for D-08 compliance.
- **Concurrent `query()` on same client without lock**: The SDK client is not concurrent-safe within one instance. Always acquire `session.lock` before `query()`.
- **Calling `client.connect()` manually**: Use `async with ClaudeSDKClient()` context manager — `__aenter__` calls `connect()` and `__aexit__` calls `disconnect()` including cleanup (Pitfall 4 prevention).
- **Holding semaphore during lock wait**: Acquire semaphore FIRST, then session lock. Reverse order risks global deadlock if session lock is held by a task waiting for the semaphore.
- **Not setting `permission_mode`**: Without `permission_mode='acceptEdits'`, Claude may prompt for permission on file operations. In non-interactive bridge context with no human to respond, task hangs indefinitely (Pitfall 8).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Claude subprocess management | Custom subprocess + stdin/stdout | `ClaudeSDKClient` | Deadlock scenarios in teardown (#780, #665); SDK handles all edge cases |
| Concurrent access to shared state | Manual threading.Lock | `asyncio.Lock` per session | asyncio-native; no thread overhead |
| Timeout enforcement | Custom `asyncio.sleep` polling | `asyncio.wait_for(coro, timeout=N)` | Standard pattern; cancels correctly on timeout |
| Display name lookup | Cache + API call from scratch | `api_client.contact.v3.user.aget()` | lark-oapi already wraps auth/token refresh |
| Card content update | Raw HTTP to Feishu API | `api_client.im.v1.message.apatch()` | lark-oapi handles token refresh automatically |
| History management | Custom message list | `ClaudeSDKClient` session | SDK maintains context across `query()` calls internally |

---

## Common Pitfalls

### Pitfall 1: ClaudeSDKClient Is NOT Concurrent-Safe Within One Instance

**What goes wrong:** Two coroutines call `await client.query()` on the same `ClaudeSDKClient` instance simultaneously. The client's internal stream gets corrupted; one turn receives partial/wrong data or raises an exception.

**Why it happens:** The client maintains a single anyio task group from `connect()` to `disconnect()`. Concurrent `query()` calls write to the same internal stream without synchronization.

**How to avoid:** Per-session `asyncio.Lock` — acquire before every `client.query()` call. The lock ensures only one turn runs at a time per session.

**Warning signs:** Corrupted responses, `RuntimeError` from anyio task group, one user's response appearing in another user's card.

---

### Pitfall 2: display_name NOT in Event Payload

**What goes wrong:** Code tries to read `data.event.sender.name` or `data.event.sender.display_name` — these fields do not exist. The result is `AttributeError` or silent `None`.

**Why it happens:** The `im.message.receive_v1` sender object only contains `sender_id` (open_id/union_id/user_id), `sender_type`, and `tenant_key`. No name field.

**How to avoid:** Always fetch display name via `contact/v3/users/{open_id}` API with `user_id_type=open_id`. Cache results per session to avoid repeated calls. Requires app permission `contact:user.base:readonly`.

**Warning signs:** AttributeError on sender object, `None` values in `[None]: message` group chat prefixes.

---

### Pitfall 3: `im.v1.message.apatch` May Not Exist — Verify Async Method Name

**What goes wrong:** Phase 1 uses `client.im.v1.message.areply()`. The async equivalent of `patch` may be `apatch`, `async_patch`, or may require `BaseRequest`. Calling a non-existent method silently returns an error at runtime.

**Why it happens:** lark-oapi naming is consistent (sync → `method`, async → `amethod`) but not all methods have async wrappers in 1.5.3.

**How to avoid:** At implementation time, inspect `client.im.v1.message` for available methods. If `apatch` is not available, fall back to `BaseRequest` raw HTTP call (same pattern as `get_bot_open_id` in `main.py`).

**Warning signs:** `AttributeError: 'MessageResource' has no attribute 'apatch'` at startup or first card update.

---

### Pitfall 4: Semaphore Acquired INSIDE Session Lock — Potential Deadlock

**What goes wrong:** If the global semaphore is acquired after the session lock, a deadlock scenario emerges: Task A holds the session lock and waits for the semaphore; Task B holds the semaphore and needs the session lock for a different turn in the same session.

**Why it happens:** Lock ordering matters. Reverse acquisition order creates circular wait.

**How to avoid:** Always acquire semaphore first, then session lock:
```python
async with semaphore:       # OUTER
    async with session.lock:  # INNER
        ...
```

---

### Pitfall 5: ClaudeSDKClient Context Manager Across Task Boundaries

**What goes wrong:** Creating `ClaudeSDKClient` in one `asyncio.Task` and sharing it with another Task (e.g., passing it via a queue). The SDK's internal anyio task group is pinned to the task/context where `connect()` was called.

**Why it happens:** SDK uses anyio task groups which are bound to the async context that created them.

**How to avoid:** Create, use, and destroy each `ClaudeSDKClient` entirely within one Task. The `SessionState` object holds the client, and only the owning worker task ever calls methods on it. If the session needs to be handed to a different task (e.g., after worker restart), destroy the old client and create a new one.

---

### Pitfall 6: `contact:user.base:readonly` Permission Not Granted

**What goes wrong:** `get_display_name()` call returns error code 99991671 ("No permission to access user information"). All group chat messages fail with error card instead of fallback to open_id prefix.

**Why it happens:** Feishu app requires explicit permission `contact:user.base:readonly` to call `contact/v3/users`. This is NOT auto-granted.

**How to avoid:** Verify permission is enabled in Feishu Open Platform app console before testing group chat. Implement graceful fallback: if API fails, use `open_id[-6:]` as display name rather than crashing.

**Warning signs:** All group chat prefixes show error; logs show contact API code 99991671.

---

## Code Examples

### Full Single-Turn Worker Pattern

```python
# Source: synthesized from claude-agent-sdk README + asyncio patterns
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage
import asyncio

async def single_turn_worker(
    session: "SessionState",
    prompt: str,
    reply_message_id: str,
    api_client,
    semaphore: asyncio.Semaphore,
    timeout: float,
) -> None:
    async with semaphore:                    # global cap
        async with session.lock:             # per-session serialization
            try:
                result_text = await asyncio.wait_for(
                    _run_claude_turn(session.client, prompt),
                    timeout=timeout,
                )
                await update_card_content(api_client, reply_message_id, result_text)
            except asyncio.TimeoutError:
                await send_error_card(api_client, reply_message_id,
                                      f"响应超时（>{timeout}s），请重试")
            except Exception as exc:
                await send_error_card(api_client, reply_message_id, f"处理出错：{exc}")


async def _run_claude_turn(client: ClaudeSDKClient, prompt: str) -> str:
    await client.query(prompt)
    text_parts = []
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        # ResultMessage ends the iterator automatically
    return "".join(text_parts)
```

### SessionManager Core

```python
# Source: architecture pattern from ARCHITECTURE.md + D-08, D-11 decisions
import asyncio
from dataclasses import dataclass, field
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

@dataclass
class SessionState:
    session_key: str
    client: ClaudeSDKClient
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    name_cache: dict = field(default_factory=dict)  # open_id -> display_name
    last_activity: float = field(default_factory=lambda: __import__("time").time())

class SessionManager:
    def __init__(self, options: ClaudeAgentOptions, semaphore: asyncio.Semaphore):
        self._sessions: dict[str, SessionState] = {}
        self._options = options
        self._semaphore = semaphore

    async def get_or_create(self, session_key: str) -> SessionState:
        if session_key not in self._sessions:
            client = ClaudeSDKClient(options=self._options)
            await client.connect()   # manual connect (not context manager) for session lifetime
            self._sessions[session_key] = SessionState(
                session_key=session_key,
                client=client,
            )
        self._sessions[session_key].last_activity = __import__("time").time()
        return self._sessions[session_key]

    async def destroy(self, session_key: str) -> None:
        if session_key in self._sessions:
            state = self._sessions.pop(session_key)
            await state.client.disconnect()
```

**Note:** Manual `connect()`/`disconnect()` is used here instead of `async with` to match session lifetime (which spans multiple event handler calls). Wrap `disconnect()` in `try/except` to tolerate errors during cleanup.

### Config Extension

```python
# Addition to src/config.py
class Config(pydantic.BaseModel):
    # ... existing fields ...
    claude_timeout: float = 120.0
    max_concurrent_tasks: int = 5
    allowed_tools: list[str] = []  # empty = all tools subject to permission_mode
    working_dir: str = "."  # reuse existing field

    @classmethod
    def from_env(cls) -> "Config":
        # ... existing ...
        return cls(
            # ... existing ...
            claude_timeout=float(os.environ.get("CLAUDE_TIMEOUT", "120")),
            max_concurrent_tasks=int(os.environ.get("MAX_CONCURRENT_TASKS", "5")),
            allowed_tools=os.environ.get("ALLOWED_TOOLS", "").split(",") if os.environ.get("ALLOWED_TOOLS") else [],
        )
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `claude-code-sdk` | `claude-agent-sdk` | Sept 2025 | Different package name and import; `ClaudeCodeOptions` → `ClaudeAgentOptions` |
| Bare `query()` per message | `ClaudeSDKClient` with persistent context | SDK architecture | Multi-turn context preserved without manual history |
| Manual history list (messages[]) | SDK session management | SDK v0.1.x | SDK handles context internally; no need for manual message list unless SDK limitations force it |

**Deprecated/outdated:**
- `claude-code-sdk`: Deprecated Sept 2025. Import `claude_agent_sdk`, not `claude_code_sdk`.
- `ClaudeCodeOptions`: Renamed to `ClaudeAgentOptions`.

---

## Open Questions

1. **Does `client.im.v1.message.apatch()` exist in lark-oapi 1.5.3?**
   - What we know: Phase 1 confirmed `areply()` exists. lark-oapi follows `a{method}` naming convention consistently.
   - What's unclear: Not directly verified for `patch` specifically.
   - Recommendation: At Wave 0, run `print(dir(client.im.v1.message))` or inspect source to confirm. If absent, use `BaseRequest` HTTP fallback (already proven in `main.py`).

2. **Does `ClaudeSDKClient.connect(prompt=None)` work correctly for interactive multi-turn use?**
   - What we know: README shows `async with ClaudeSDKClient() as client:` pattern. `connect()` accepts `prompt=None` to open empty stream for interactive use.
   - What's unclear: Whether calling `connect(None)` and then multiple `query()` calls maintains full context in v0.1.53.
   - Recommendation: Verify with a quick integration test at Wave 0 (`test_claude_worker.py`). If context is not preserved, fallback to manual history per D-09.

3. **Does Feishu app `cli_a92d11a974b89bcd` have `contact:user.base:readonly` permission?**
   - What we know: This permission is required for `get_display_name()`. It is NOT auto-granted.
   - What's unclear: Current permission set of the shared app.
   - Recommendation: Check app permissions in Feishu Open Platform console before first group chat test. Implement fallback to `open_id[-8:]` if permission denied.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.10+ | claude-agent-sdk | Yes | 3.10.12 | — |
| claude-agent-sdk | CLAUDE-01 to CLAUDE-06 | Not yet installed | — (0.1.53 on PyPI) | — blocking: must install |
| Claude Code CLI | claude-agent-sdk (bundles it) | Yes | 2.1.81 | — (SDK bundles its own) |
| lark-oapi 1.5.3 | Card patch, user info API | Yes (Phase 1) | 1.5.3 | — |
| contact API permission | SESS-02 (display_name) | Unknown | — | Use open_id[-8:] as name |

**Missing dependencies with no fallback:**
- `claude-agent-sdk` not yet in `requirements.txt` or installed. Must add `claude-agent-sdk==0.1.53` to requirements.txt and install before Wave 1.

**Missing dependencies with fallback:**
- `contact:user.base:readonly` Feishu permission — if absent, fall back to using last 8 chars of `open_id` as display name.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | `pyproject.toml` (`asyncio_mode = "auto"`) |
| Quick run command | `python3 -m pytest tests/test_session.py tests/test_claude_worker.py -x` |
| Full suite command | `python3 -m pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CLAUDE-01 | `ClaudeSDKClient.query()` called with correct prompt | unit (mock SDK) | `pytest tests/test_claude_worker.py::test_query_called_with_prompt -x` | Wave 0 |
| CLAUDE-02 | `receive_response()` text accumulated correctly | unit (mock SDK) | `pytest tests/test_claude_worker.py::test_response_text_accumulated -x` | Wave 0 |
| CLAUDE-03 | Same client instance reused across turns | unit (mock SDK) | `pytest tests/test_session.py::test_session_client_reused -x` | Wave 0 |
| CLAUDE-04 | Exception in one worker does not crash others | unit | `pytest tests/test_claude_worker.py::test_exception_isolation -x` | Wave 0 |
| CLAUDE-05 | asyncio.TimeoutError → error card sent | unit | `pytest tests/test_claude_worker.py::test_timeout_sends_error_card -x` | Wave 0 |
| CLAUDE-06 | allowed_tools passed to ClaudeAgentOptions | unit | `pytest tests/test_session.py::test_options_allowed_tools -x` | Wave 0 |
| SESS-01 | P2P session key = open_id | unit | `pytest tests/test_session.py::test_p2p_session_key -x` | Wave 0 |
| SESS-02 | Group session key = chat_id, prompt has prefix | unit | `pytest tests/test_session.py::test_group_session_key_and_prefix -x` | Wave 0 |
| SESS-03 | /new destroys session and sends confirmation | unit | `pytest tests/test_session.py::test_new_command_destroys_session -x` | Wave 0 |
| CONC-01 | Multiple tasks created for multiple messages | unit | `pytest tests/test_session.py::test_parallel_tasks_created -x` | Wave 0 |
| CONC-02 | Semaphore limits concurrent workers | unit | `pytest tests/test_session.py::test_semaphore_limits_concurrency -x` | Wave 0 |
| CONC-03 | Group users processed in parallel (lock not held globally) | unit | `pytest tests/test_session.py::test_group_parallel_users -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `python3 -m pytest tests/test_session.py tests/test_claude_worker.py tests/test_cards.py -x`
- **Per wave merge:** `python3 -m pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_session.py` — covers SESS-01, SESS-02, SESS-03, CONC-01, CONC-02, CONC-03, CLAUDE-03, CLAUDE-06
- [ ] `tests/test_claude_worker.py` — covers CLAUDE-01, CLAUDE-02, CLAUDE-04, CLAUDE-05
- [ ] `tests/test_cards.py` — add `test_update_card_content` and `test_send_error_card` (file exists, needs new test classes)
- [ ] `tests/test_config.py` — add tests for `claude_timeout`, `max_concurrent_tasks`, `allowed_tools` fields (file exists)

---

## Sources

### Primary (HIGH confidence)

- `https://github.com/anthropics/claude-code-sdk-python` — SDK README: `ClaudeAgentOptions` fields, `query()` signature, `ClaudeSDKClient` API, `receive_response()` behavior
- `https://github.com/anthropics/claude-code-sdk-python/blob/main/src/claude_agent_sdk/client.py` — `ClaudeSDKClient.query(session_id)` signature, concurrent limitation, `receive_response()` vs `receive_messages()` distinction
- `https://open.feishu.cn/document/server-docs/im-v1/message/events/receive` — Official im.message.receive_v1 event structure (updated April 2025); confirmed sender has no name field
- `https://github.com/larksuite/oapi-sdk-python` samples — `PatchMessageRequest` pattern for interactive card update

### Secondary (MEDIUM confidence)

- `https://docs.pingcode.com/baike/3312456` — `contact/v3/users` async pattern (`aget`), permission requirement `contact:user.base:readonly` — verified against official lark-oapi sample directory structure
- `https://pypi.org/pypi/claude-agent-sdk/json` — Package metadata: v0.1.53, Python 3.10+, MIT, alpha status, `anyio>=4.0.0` dependency

### Tertiary (LOW confidence)

- `https://open.feishu.cn/document/server-docs/im-v1/message/patch` — im.v1.message.patch supports interactive card update — confirmed from search snippet but doc page rendered JS only; verify `apatch` async method at implementation time

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — SDK verified via GitHub README + PyPI metadata
- Architecture: HIGH — Based on verified SDK API + Phase 1 established patterns
- Pitfalls: HIGH — Pitfalls 1/5 from SDK source code analysis; Pitfalls 2/3 from official Feishu docs
- Card update API: MEDIUM — `patch` endpoint confirmed by search snippet; async method name needs runtime verification

**Research date:** 2026-04-01
**Valid until:** 2026-05-01 (claude-agent-sdk is Alpha; check for breaking changes on install)
