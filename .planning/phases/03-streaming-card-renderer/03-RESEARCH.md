# Phase 3: Streaming Card Renderer - Research

**Researched:** 2026-04-01
**Domain:** Feishu CardKit Streaming API + claude-agent-sdk streaming events + asyncio batch flush
**Confidence:** MEDIUM (CardKit sequence API confirmed via official search results; exact request body verified via Go SDK source + Feishu search snippets; lark-oapi internals verified live; claude-agent-sdk types verified by inspecting installed package)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-18:** Use `httpx.AsyncClient` for CardKit sequence API calls (lark-oapi does NOT wrap this API)
- **D-19:** Flow: `send_thinking_card()` sends initial IM card → get `reply_message_id` → create CardKit card → send as UPDATE to IM card → create sequence → PATCH sequence → finish sequence
- **D-20:** Extract `card_id` from CardKit create response (`resp.data.card_id`), used for all subsequent sequence calls
- **D-21:** Batch flush every 300-500ms — asyncio timer or asyncio.Event mechanism
- **D-22:** `sequence_id` is caller-generated string (not server-assigned int) — must be unique per card
- **D-23:** 429 rate limit retry via tenacity
- **D-24:** Collapsible tool info: tool name + summary using markdown formatting or card collapsible component
- **D-25:** Tool call info shown before final text — user sees Claude's "work in progress"
- **D-26:** Typing indicator in card footer during streaming (`_正在输入..._` or similar)
- **D-27:** Final card: remove typing indicator, show complete Markdown response
- **D-28:** Register `card.action.trigger` callback via `register_p2_card_action_trigger` in EventDispatcherHandler
- **D-29:** Phase 3 only wires up callback infrastructure — button logic deferred to Phase 4

### Claude's Discretion

- CardKit `streaming_config` exact parameters (runtime validation needed)
- PATCH request Content-Type and body format details
- Tool info display format (depends on Feishu card component availability)
- httpx.AsyncClient connection pool configuration

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CARD-02 | CardKit PATCH API real-time streaming update | Sequence API: POST create + PATCH update + PATCH finish; full URL paths documented below |
| CARD-03 | Batch update 300-500ms — avoid rate limits | asyncio.Event flush pattern; tenacity retry on 429 |
| CARD-04 | Typing indicator during streaming, removed on finish | markdown `_正在输入..._` footer; removed in finish sequence |
| CARD-05 | Tool call visibility in card | ToolUseBlock detected from AssistantMessage; markdown `> tool:` block or collapsed section |
| CARD-06 | File operation results shown | ToolResultBlock content extracted from AssistantMessage |
| CARD-07 | Final card: complete Markdown response | finish sequence call after ResultMessage |
| INTER-03 | Card callback handler registration via long connection | `register_p2_card_action_trigger` on EventDispatcherHandler — verified working |

</phase_requirements>

---

## Summary

Phase 3 converts the Phase 2 one-shot card update into a real-time streaming experience using the Feishu CardKit Sequence API. The critical architectural discovery is that the PATCH endpoint originally described in prior research is **not** a single `PATCH /cardkit/v1/cards/{card_id}` call — the actual API is a **three-step sequence lifecycle**: create sequence → update sequence (repeat) → finish sequence.

The streaming flow is: (1) Phase 2's `send_thinking_card()` creates a thinking IM card and returns `reply_message_id`. Phase 3 must now **also** call `POST /cardkit/v1/cards` to create a streaming CardKit card, getting a `card_id`. That `card_id` is then used to update the IM card via a second `im.v1.message.patch` call that embeds the `card_id`. Then the sequence APIs drive the typewriter animation.

claude-agent-sdk's `receive_response()` yields `AssistantMessage` (with `TextBlock`, `ToolUseBlock`, `ToolResultBlock` content blocks), `SystemMessage`, and `ResultMessage`. Only `AssistantMessage` carries text and tool events. `ResultMessage` signals completion and triggers the finish sequence call.

The lark-oapi 1.5.3 SDK has `client.cardkit.v1.card.acreate()` for creating CardKit cards, but does NOT have sequence APIs. Use `httpx.AsyncClient` for all three sequence endpoints. The tenant token for httpx can be extracted via `TokenManager.get_self_tenant_token(client._config)` without making a separate auth call.

**Primary recommendation:** Build a `CardStreamingManager` class that encapsulates the card_id, sequence lifecycle, buffer accumulation, and 300-500ms flush timer. This keeps `claude_worker.py` changes minimal — just swap the one-shot `_run_claude_turn()` for a streaming version that calls callbacks on the manager.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | >=0.27 (already installed) | CardKit sequence API calls | lark-oapi does not wrap sequence endpoints; httpx shares asyncio loop cleanly |
| tenacity | latest (add to requirements.txt) | 429 retry with exponential backoff | Avoids hand-rolling retry logic; integrates cleanly with async |
| asyncio | stdlib | Batch flush timer (asyncio.Event or asyncio.sleep loop) | Already the app's concurrency model |
| lark-oapi | 1.5.3 (already installed) | CardKit card create (acreate), IM message reply/patch | Has `acreate` for card creation; sequence calls go via httpx |
| uuid | stdlib | Generate unique sequence_id values | Required by sequence API — must be unique per card |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | >=24 (already installed) | Log sequence lifecycle, PATCH timings, 429 events | Always |

**Installation (additions only):**
```bash
pip install tenacity
```

**Version verification (2026-04-01):**
```bash
npm view tenacity  # not applicable — pip package
pip show httpx     # confirm >=0.27
pip show tenacity  # add if missing
```

---

## Architecture Patterns

### CardKit Streaming Sequence Lifecycle (VERIFIED via Feishu official docs + Go SDK source)

The actual API flow — not the simple PATCH originally documented:

```
Phase 2 flow (existing):
  send_thinking_card(client, msg_id) → reply_message_id

Phase 3 additions per AI response:
  1. POST /open-apis/cardkit/v1/cards              → card_id
  2. client.im.v1.message.apatch(reply_message_id, card_id_content)
  3. POST /open-apis/cardkit/v1/cards/{card_id}/sequences   (create sequence, first chunk)
  4. PATCH /open-apis/cardkit/v1/cards/{card_id}/sequences/{sequence_id}  (update, repeat ~300ms)
  5. PATCH /open-apis/cardkit/v1/cards/{card_id}/sequences/{sequence_id}/finish  (done)
```

### Pattern 1: CardKit Card Create via lark-oapi

The lark-oapi SDK DOES wrap card creation (`acreate`). Use it to avoid manual token management:

```python
# Source: lark-oapi cardkit.v1.card inspection + Feishu create card docs
from lark_oapi.api.cardkit.v1 import CreateCardRequest, CreateCardRequestBody
import json

streaming_card_data = {
    "schema": "2.0",
    "config": {
        "streaming_mode": True,
        "streaming_config": {
            "print_frequency_ms": {"default": 70},
            "print_step": {"default": 1},
            "print_strategy": "fast",
        },
    },
    "header": {
        "title": {"tag": "plain_text", "content": "AI 助手"},
        "template": "blue",
    },
    "body": {
        "elements": [{"tag": "markdown", "content": "_正在输入..._"}]
    },
}

request = (
    CreateCardRequest.builder()
    .request_body(
        CreateCardRequestBody.builder()
        .type("card_json")
        .data(json.dumps(streaming_card_data, ensure_ascii=False))
        .build()
    )
    .build()
)
resp = await api_client.cardkit.v1.card.acreate(request)
if not resp.success():
    raise RuntimeError(f"CardKit create failed: {resp.code} {resp.msg}")
card_id = resp.data.card_id   # e.g. "7355372766134157313"
```

### Pattern 2: Update IM Card with CardKit card_id

After getting `card_id`, update the thinking IM card to embed it:

```python
# Source: lark-oapi im.v1.message.apatch — same as Phase 2 update_card_content()
# Content format when embedding a CardKit card_id:
card_id_content = json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False)

request = (
    lark.im.v1.PatchMessageRequest.builder()
    .message_id(reply_message_id)  # from send_thinking_card()
    .request_body(
        lark.im.v1.PatchMessageRequestBody.builder()
        .content(card_id_content)
        .build()
    )
    .build()
)
resp = await api_client.im.v1.message.apatch(request)
```

### Pattern 3: Sequence Create/Update/Finish via httpx

lark-oapi has no sequence APIs. Use httpx with tenant token from TokenManager:

```python
# Source: lark-oapi TokenManager inspection + Feishu sequence API docs
from lark_oapi.core.token import TokenManager
import httpx
import uuid

BASE = "https://open.feishu.cn/open-apis"

def _get_token(api_client) -> str:
    """Extract tenant_access_token from existing lark.Client config."""
    return TokenManager.get_self_tenant_token(api_client._config)

async def create_sequence(http: httpx.AsyncClient, api_client, card_id: str, seq_id: str, content: str) -> None:
    """POST /cardkit/v1/cards/{card_id}/sequences"""
    token = _get_token(api_client)
    resp = await http.post(
        f"{BASE}/cardkit/v1/cards/{card_id}/sequences",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json={"sequence_id": seq_id, "content": content},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"create_sequence failed: {data}")

async def update_sequence(http: httpx.AsyncClient, api_client, card_id: str, seq_id: str, content: str) -> None:
    """PATCH /cardkit/v1/cards/{card_id}/sequences/{sequence_id}"""
    token = _get_token(api_client)
    resp = await http.patch(
        f"{BASE}/cardkit/v1/cards/{card_id}/sequences/{seq_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json={"content": content},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"update_sequence failed: {data}")

async def finish_sequence(http: httpx.AsyncClient, api_client, card_id: str, seq_id: str) -> None:
    """PATCH /cardkit/v1/cards/{card_id}/sequences/{sequence_id}/finish"""
    token = _get_token(api_client)
    resp = await http.patch(
        f"{BASE}/cardkit/v1/cards/{card_id}/sequences/{seq_id}/finish",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json={},
    )
    resp.raise_for_status()
```

### Pattern 4: Streaming Worker Refactor — _run_claude_turn() with callbacks

```python
# Source: claude-agent-sdk type inspection (live package)
# AssistantMessage.content is list[TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock]
# ResultMessage signals end of stream

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, ToolResultBlock

async def _run_claude_turn_streaming(
    client: ClaudeSDKClient,
    prompt: str,
    on_text: Callable[[str], Awaitable[None]],    # called with each TextBlock
    on_tool_use: Callable[[ToolUseBlock], Awaitable[None]],  # called with each tool call
    on_tool_result: Callable[[ToolResultBlock], Awaitable[None]],  # called with each tool result
) -> str:
    """Run one Claude turn, invoking callbacks for each content block."""
    await client.query(prompt)
    full_text_parts: list[str] = []
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    full_text_parts.append(block.text)
                    await on_text(block.text)
                elif isinstance(block, ToolUseBlock):
                    await on_tool_use(block)
                elif isinstance(block, ToolResultBlock):
                    await on_tool_result(block)
        # ResultMessage auto-terminates the iterator
    return "".join(full_text_parts)
```

### Pattern 5: Batch Flush with asyncio.Event

```python
# Source: asyncio stdlib pattern for timer-triggered batch flush
import asyncio

class CardStreamingManager:
    def __init__(self, card_id: str, seq_id: str, http: httpx.AsyncClient, api_client):
        self.card_id = card_id
        self.seq_id = seq_id
        self._http = http
        self._api_client = api_client
        self._buffer: list[str] = []
        self._sequence_created = False
        self._flush_event = asyncio.Event()
        self._flush_task: asyncio.Task | None = None
        self._tool_sections: list[str] = []

    def append_text(self, text: str) -> None:
        self._buffer.append(text)
        self._flush_event.set()

    def append_tool_use(self, block: ToolUseBlock) -> None:
        summary = f"> **工具调用**: `{block.name}`"
        self._tool_sections.append(summary)
        self._flush_event.set()

    async def _flush_loop(self) -> None:
        """Flush buffer to CardKit every 300-500ms."""
        while True:
            await asyncio.wait_for(
                self._flush_event.wait(),
                timeout=0.4,  # 400ms max wait
            ) if not self._flush_event.is_set() else None
            self._flush_event.clear()
            await self._flush_to_card()

    async def _flush_to_card(self) -> None:
        combined = "".join(self._tool_sections + self._buffer) + "\n\n_正在输入..._"
        content = combined
        if not self._sequence_created:
            await create_sequence(self._http, self._api_client, self.card_id, self.seq_id, content)
            self._sequence_created = True
        else:
            await update_sequence(self._http, self._api_client, self.card_id, self.seq_id, content)

    async def finalize(self) -> None:
        """Final flush without typing indicator, then finish sequence."""
        final_content = "".join(self._tool_sections + self._buffer)
        if not self._sequence_created:
            await create_sequence(self._http, self._api_client, self.card_id, self.seq_id, final_content)
        else:
            await update_sequence(self._http, self._api_client, self.card_id, self.seq_id, final_content)
        await finish_sequence(self._http, self._api_client, self.card_id, self.seq_id)
```

### Pattern 6: Card Callback Registration (INTER-03)

```python
# Source: lark-oapi EventDispatcherHandler — same pattern as register_p2_im_message_receive_v1
# in main.py, add to the EventDispatcherHandler builder chain:

from lark_oapi.card.model.card_action_trigger_event import CardActionTriggerEvent
from lark_oapi.card.model.card_action_trigger_response import CardActionTriggerResponse

def on_card_action(data: CardActionTriggerEvent) -> CardActionTriggerResponse:
    """
    Sync callback — called by lark SDK on card button click.
    Must return within 3 seconds. Use loop.call_soon_threadsafe to schedule async work.
    Phase 3: only infrastructure; no button logic yet (D-29).
    """
    # Phase 3: log and return empty response
    logger.info("card_action_received", action_tag=getattr(data.action, 'tag', None))
    return CardActionTriggerResponse()

event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(on_message)
    .register_p2_card_action_trigger(on_card_action)   # INTER-03
    .build()
)
```

### Pattern 7: tenacity Retry for 429

```python
# Source: tenacity docs + Pitfall 5 from PITFALLS.md
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

def is_rate_limit_error(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429

@retry(
    retry=retry_if_exception(is_rate_limit_error),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def update_sequence_with_retry(http, api_client, card_id, seq_id, content):
    await update_sequence(http, api_client, card_id, seq_id, content)
```

### Recommended Project Structure

```
src/
├── cards.py           # MODIFY: add create_streaming_card(), patch_im_with_card_id()
├── card_streaming.py  # NEW: CardStreamingManager, sequence lifecycle, httpx calls
├── claude_worker.py   # MODIFY: _run_claude_turn_streaming(), single_turn_worker() callback wiring
├── handler.py         # MODIFY: register_p2_card_action_trigger callback registration
├── session.py         # UNCHANGED
├── config.py          # UNCHANGED
├── dedup.py           # UNCHANGED
├── filters.py         # UNCHANGED
```

### Anti-Patterns to Avoid

- **Per-token PATCH:** Never PATCH on every TextBlock received. Buffer for 300-500ms (Pitfall 5 from PITFALLS.md).
- **Global httpx.AsyncClient:** Do not create a new AsyncClient per message. Share one client (or one per worker at most).
- **Calling TokenManager in tight loops:** `get_self_tenant_token()` is cached internally; calling it before each PATCH is safe and idiomatic.
- **Missing sequence_id uniqueness:** `sequence_id` must be unique per card (not globally). Use `uuid.uuid4().hex[:16]` per response.
- **Not finishing sequence:** Leaving a sequence open causes the card to stay in streaming state with animation running indefinitely. Always call `/finish` even on error.
- **Appending conversation history to card:** Each card shows the current reply only. Multi-turn context lives in `ClaudeSDKClient`, not in card content (Pitfall 10 from PITFALLS.md).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry with backoff on 429 | Custom sleep/retry loop | tenacity | Handles jitter, max attempts, exception filtering cleanly |
| Token caching/refresh | Manual TTL dict | `TokenManager.get_self_tenant_token(client._config)` | Already cached in lark-oapi — no extra code needed |
| Card create | Raw BaseRequest via httpx | `client.cardkit.v1.card.acreate()` | lark-oapi wraps this one; handles auth/serialization |
| IM message patch with card_id | New code | `client.im.v1.message.apatch()` — already in cards.py | Phase 2 already has this pattern |
| Unique ID generation | Custom counter | `uuid.uuid4().hex` | Standard, collision-free, no shared state |

**Key insight:** lark-oapi wraps card creation but not the sequence API. The dividing line is: `cardkit/v1/cards` → use lark-oapi; `cardkit/v1/cards/{id}/sequences` → use httpx directly.

---

## Common Pitfalls

### Pitfall 1: CardKit API Split — card create vs sequence update

**What goes wrong:** Developer calls `client.cardkit.v1.card.acreate()` for the initial card (correct), then tries to find a sequence update method on the same object (it doesn't exist), then falls back to implementing a bare `PATCH /cardkit/v1/cards/{id}` (wrong endpoint).

**Why it happens:** Prior research documented a single `PATCH /open-apis/cardkit/v1/cards/{card_id}` endpoint. The actual production API is a separate `sequences` sub-resource. The Go SDK and official search results confirm the correct paths.

**How to avoid:** Always use the sequence sub-resource paths:
- `POST /open-apis/cardkit/v1/cards/{card_id}/sequences` (create)
- `PATCH /open-apis/cardkit/v1/cards/{card_id}/sequences/{sequence_id}` (update)
- `PATCH /open-apis/cardkit/v1/cards/{card_id}/sequences/{sequence_id}/finish` (finish)

**Warning signs:** 404 or method-not-allowed errors when PATCHing directly to `/cards/{card_id}`.

### Pitfall 2: sequence_id is Caller-Generated, Not Server-Assigned

**What goes wrong:** Developer expects the create-sequence response to return a sequence_id to use in subsequent PATCH calls.

**Why it happens:** The API takes `sequence_id` as input in the POST body, not the other way around. The sequence_id must be unique within the card.

**How to avoid:** Generate `sequence_id` before calling create: `seq_id = uuid.uuid4().hex[:16]`. Use the same `seq_id` for all update and finish calls.

### Pitfall 3: IM Card Must Be Updated with card_id

**What goes wrong:** Developer creates a CardKit card (getting `card_id`) then calls `create_sequence()` but the user never sees the streaming animation because the IM message still shows the old thinking card content, not the CardKit card.

**Why it happens:** Creating a CardKit card via `POST /cardkit/v1/cards` creates a card resource on the server. It does NOT automatically update the IM message. A separate `im.v1.message.apatch` call must update the IM card content to reference the new `card_id`.

**How to avoid:** After `acreate()` returns `card_id`, immediately patch the IM message:
```python
content = json.dumps({"type": "card", "data": {"card_id": card_id}})
# apatch the reply_message_id from send_thinking_card()
```

**Warning signs:** `card_id` obtained, sequences being created, but user sees no typewriter animation — the IM message still shows old static content.

### Pitfall 4: Leaving Sequences Open on Error

**What goes wrong:** Claude raises an exception mid-stream. The card stays in animated streaming state indefinitely. Users see a card that keeps "blinking" with no new content.

**Why it happens:** The sequence is only marked done when `finish` is called. If the worker task crashes, the finish call never executes.

**How to avoid:** Use `try/finally` to guarantee finish is called:
```python
try:
    async for msg in client.receive_response():
        ...
finally:
    await finish_sequence(http, api_client, card_id, seq_id)
```

**Warning signs:** Feishu cards with persistent typing animation after Claude errors.

### Pitfall 5: Wrong content format for IM card with card_id

**What goes wrong:** Patching the IM message with `{"data": {"card_id": "..."}}` returns error or shows blank card.

**Why it happens:** The content format when embedding a CardKit `card_id` differs from the CardKit v2 schema format. The correct format is `{"type": "card", "data": {"card_id": "<id>"}}`.

**How to avoid:** Use the exact format `json.dumps({"type": "card", "data": {"card_id": card_id}})` when calling `im.v1.message.apatch`.

### Pitfall 6: Accessing card.action.trigger in WS long connection

**What goes wrong:** Card button clicks do not reach the callback because the `register_p2_card_action_trigger` handler was not added to the EventDispatcherHandler, or the handler was registered as async (which the lark SDK cannot call).

**Why it happens:** The callback must be a sync function just like `on_message_receive`. The lark SDK calls it synchronously. Any async work must be scheduled via `loop.call_soon_threadsafe()`.

**How to avoid:** Register as sync, use the same pattern as message handler (D-29: Phase 3 just needs infrastructure, no actual async dispatch needed yet).

---

## Code Examples

### Verified: claude-agent-sdk streaming event types (inspected from installed package v0.1.53)

```python
# ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock
# AssistantMessage.content: list[ContentBlock]

@dataclass
class TextBlock:
    text: str                   # Incremental text from Claude

@dataclass
class ToolUseBlock:
    id: str                     # Tool call ID (used to match results)
    name: str                   # Tool name e.g. "Bash", "Read", "Write"
    input: dict[str, Any]       # Tool parameters

@dataclass
class ToolResultBlock:
    tool_use_id: str             # Matches ToolUseBlock.id
    content: str | list | None   # Result content (text or structured)
    is_error: bool | None        # True if tool call failed

@dataclass
class ResultMessage:
    subtype: str                 # "success" or "error"
    is_error: bool
    num_turns: int
    session_id: str
    stop_reason: str | None
    total_cost_usd: float | None
    # ResultMessage terminates receive_response() iteration
```

### Verified: lark-oapi cardkit CreateCardResponse (inspected from installed package)

```python
# resp = await api_client.cardkit.v1.card.acreate(request)
# resp.data is CreateCardResponseBody
# resp.data.card_id is str | None

# Request body (type + data fields confirmed):
# CreateCardRequestBody._types = {"type": str, "data": str}
# type = "card_json"
# data = JSON string of the card schema
```

### Verified: TokenManager usage for httpx auth

```python
from lark_oapi.core.token import TokenManager
# api_client._config is the lark_oapi.core.model.config.Config instance
token = TokenManager.get_self_tenant_token(api_client._config)
# token is a str (tenant_access_token), cached and auto-refreshed by lark-oapi
# Use as: "Authorization": f"Bearer {token}"
```

### Verified: card.action.trigger handler registration (via lark-oapi source + search)

```python
from lark_oapi.card.model.card_action_trigger_event import CardActionTriggerEvent
from lark_oapi.card.model.card_action_trigger_response import CardActionTriggerResponse

def on_card_action(data: CardActionTriggerEvent) -> CardActionTriggerResponse:
    # data.action.tag  -- component type ("button", etc.)
    # data.action.value -- dict of action values
    # data.operator.open_id -- who clicked
    return CardActionTriggerResponse()

event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(on_message)
    .register_p2_card_action_trigger(on_card_action)
    .build()
)
```

---

## State of the Art

| Old Approach (Prior Research) | Current Approach (Verified) | When Changed | Impact |
|-------------------------------|----------------------------|--------------|--------|
| Single `PATCH /cardkit/v1/cards/{card_id}` with `sequence` int field | Separate `sequences` sub-resource: POST create + PATCH update + PATCH finish | Confirmed in Feishu 2025 docs | Phase 3 must use 3-step lifecycle, not 1-step PATCH |
| `sequence` is an integer counter in request body | `sequence_id` is a caller-generated string in URL path | Same | No shared counter needed; each worker generates its own UUID |
| `streaming` flag in card body | `streaming_mode: true` in card `config` object | Same | Confirmed exact JSON structure for card create |

**Deprecated/outdated:**
- Simple `PATCH /cardkit/v1/cards/{card_id}` body with `{"sequence": N, "card": {...}}`: This appears to be an older or alternative API. The verified current API uses the `sequences` sub-resource.

---

## Open Questions

1. **`streaming_config` exact effect**
   - What we know: `print_frequency_ms`, `print_step`, `print_strategy: "fast"` are documented parameters; `streaming_mode: true` is required in `config`.
   - What's unclear: Exact relationship between `streaming_config` in the create-card body and the animation behavior of sequences. Whether omitting `streaming_config` uses defaults.
   - Recommendation: Start with `{"print_frequency_ms": {"default": 70}, "print_step": {"default": 1}, "print_strategy": "fast"}`. Runtime validate at Phase 3 execution.

2. **IM card patch format for card_id embedding**
   - What we know: Content format should be `{"type": "card", "data": {"card_id": "..."}}` based on Feishu patterns.
   - What's unclear: Whether `msg_type` field also needs changing from `"interactive"` to something else.
   - Recommendation: Try `{"type": "card", "data": {"card_id": card_id}}` with existing `apatch` pattern first; validate at runtime.

3. **collapsible tool sections in Feishu card markdown**
   - What we know: Feishu card markdown supports bold, italic, code blocks, blockquotes.
   - What's unclear: Whether `<details>/<summary>` HTML is supported in Feishu card markdown for collapsible sections.
   - Recommendation: Use blockquote format `> **工具**: tool_name\n> 执行中...` as fallback. Test collapsible at runtime.

4. **tenacity version in requirements.txt**
   - What we know: tenacity is not currently in requirements.txt.
   - What's unclear: None.
   - Recommendation: Add `tenacity>=8.0.0` to requirements.txt.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| httpx | CardKit sequence API | Yes (in requirements.txt) | >=0.27 | — |
| tenacity | 429 retry | No (not in requirements.txt) | — | Add to requirements.txt |
| lark-oapi | Card create, IM patch, token manager | Yes | 1.5.3 | — |
| claude-agent-sdk | Streaming events | Yes | 0.1.53 | — |
| asyncio | Flush timer | Yes (stdlib) | 3.10+ | — |
| uuid | sequence_id generation | Yes (stdlib) | — | — |

**Missing dependencies with no fallback:**
- `tenacity` — must add to requirements.txt before implementation

**Missing dependencies with fallback:**
- None

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | none (uses pyproject.toml markers or inline) |
| Quick run command | `pytest tests/test_card_streaming.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CARD-02 | CardKit card created, card_id extracted, sequence lifecycle called | unit | `pytest tests/test_card_streaming.py::TestCardStreamingManager -x` | No — Wave 0 |
| CARD-03 | Buffer accumulates, flush called once per 300-500ms (not per token) | unit | `pytest tests/test_card_streaming.py::TestBatchFlush -x` | No — Wave 0 |
| CARD-04 | Typing indicator present mid-stream, absent after finalize() | unit | `pytest tests/test_card_streaming.py::TestTypingIndicator -x` | No — Wave 0 |
| CARD-05 | ToolUseBlock triggers tool section append | unit | `pytest tests/test_card_streaming.py::TestToolVisibility -x` | No — Wave 0 |
| CARD-06 | ToolResultBlock content extracted | unit | `pytest tests/test_card_streaming.py::TestToolResults -x` | No — Wave 0 |
| CARD-07 | finalize() sends complete text to sequence then calls finish | unit | `pytest tests/test_card_streaming.py::TestFinalize -x` | No — Wave 0 |
| INTER-03 | register_p2_card_action_trigger registered on EventDispatcherHandler | unit | `pytest tests/test_handler.py::TestCardCallbackRegistration -x` | Partial (test_handler.py exists) |

### Sampling Rate

- **Per task commit:** `pytest tests/test_card_streaming.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_card_streaming.py` — covers CARD-02, CARD-03, CARD-04, CARD-05, CARD-06, CARD-07
- [ ] `src/card_streaming.py` — the new module itself (must exist before tests can import)
- [ ] Add `tenacity>=8.0.0` to `requirements.txt`

---

## Sources

### Primary (HIGH confidence)
- lark-oapi 1.5.3 installed package — `lark_oapi.api.cardkit.v1`: `CreateCardRequestBody`, `CreateCardResponseBody`, `Card.acreate()` confirmed by live inspection
- lark-oapi 1.5.3 installed package — `lark_oapi.core.token.TokenManager.get_self_tenant_token()`: confirmed by source inspection
- claude-agent-sdk 0.1.53 installed package — `AssistantMessage`, `TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `ResultMessage`: all confirmed by live inspection

### Secondary (MEDIUM confidence)
- Feishu open platform search snippets (2025-01-15 dated): confirmed sequence sub-resource URLs, request body fields (`sequence_id`, `content`, `elements`), three-step lifecycle
  - https://open.feishu.cn/document/server-docs/cardkit-v1/card-sequence/create
  - https://open.feishu.cn/document/server-docs/cardkit-v1/card-sequence/update
  - https://open.feishu.cn/document/server-docs/cardkit-v1/card-sequence/finish
- Go SDK source (larksuite/oapi-sdk-go) — confirms sequence create/update body structure (`uuid`, `content_type`, `content` fields visible in model.go); note: `content_type` field in Go SDK may differ from Python/direct HTTP API; use `content` string field
- CardKit create card docs via WebFetch: confirmed `card_id` at `data.card_id`, `streaming_mode: true` in config, `streaming_config` parameters
- lark-oapi GitHub / WebSearch: `register_p2_card_action_trigger` + `CardActionTriggerEvent`/`CardActionTriggerResponse` pattern confirmed

### Tertiary (LOW confidence)
- Exact `streaming_config` effect on animation: WebSearch consensus; needs runtime validation
- IM card `apatch` format with `card_id`: derived from Feishu card embedding patterns; needs runtime validation
- `collapsible` component availability in card markdown: not verified against current docs

---

## Project Constraints (from CLAUDE.md)

| Directive | Impact on Phase 3 |
|-----------|------------------|
| No sudo | No impact (no system install needed) |
| Python 3.10+ | All code uses 3.10+ features (union syntax, asyncio) |
| lark-oapi for Feishu SDK | Use lark-oapi for card create + IM patch; httpx for sequences |
| httpx for CardKit PATCH | Confirmed: sequence APIs require direct HTTP |
| 3-second handler limit | Card callback handler must be sync, return immediately |
| Shared app credentials (mi-feishu MCP) | No new impact for Phase 3 |
| GSD workflow enforcement | All edits via /gsd:execute-phase |

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified by live inspection or pypi
- Architecture (CardKit sequence lifecycle): MEDIUM-HIGH — confirmed via official Feishu docs search snippets + Go SDK source; exact HTTP behavior needs runtime smoke test
- Architecture (claude-agent-sdk streaming events): HIGH — verified by direct package inspection
- Architecture (lark-oapi TokenManager pattern): HIGH — verified by source inspection
- Pitfalls: HIGH for items derived from official docs and live inspection; MEDIUM for runtime-behavior pitfalls
- CardKit streaming_config details: LOW — parameters found but exact animation effect needs runtime validation

**Research date:** 2026-04-01
**Valid until:** 2026-05-01 (CardKit sequence API is relatively new; check for updates if planning >30 days out)
