# Phase 1: Feishu Connectivity - Research

**Researched:** 2026-04-01
**Domain:** lark-oapi WebSocket long connection, asyncio bridging, Feishu event model, CardKit initial card
**Confidence:** HIGH (all critical SDK details verified from installed lark-oapi 1.5.3 source code on this machine)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** New independent Feishu app (not reusing `cli_a92d11a974b89bcd`). Avoids event routing competition with mi-feishu MCP.
- **D-02:** Direct lark-oapi Python SDK, no MCP. feishu CLI for auxiliary operations only, not core message pipeline.
- **D-03:** Status card with header + "正在思考" status text + typing animation element. Not plain text reply.
- **D-04:** Handle all message types (text, rich text, image, file, etc.).
- **D-05:** Friendly prompt for unsupported message types; never silently ignore.
- **D-06:** `.env` file with `python-dotenv`; environment variable override supported. Required: APP_ID, APP_SECRET.

### Claude's Discretion

- WebSocket reconnection strategy parameters (backoff time, max retry count)
- Message dedup TTL duration and data structure choice
- Exact bridging pattern between asyncio event loop and lark.ws.Client

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CONN-01 | Receive `im.message.receive_v1` via lark-oapi WebSocket long connection | Threading model, event handler registration pattern, loop architecture confirmed from SDK source |
| CONN-02 | Message dedup — same `message_id` processed only once | `message_id` on `EventMessage`, dedup-set pattern with TTL, bounded LRU |
| CONN-03 | Group chat: respond only when @mentioned; P2P: always respond | `chat_type` field (`"p2p"` / `"group"`), `mentions` list with `id.open_id` comparison |
| CONN-04 | WebSocket auto-reconnect after disconnect | `auto_reconnect=True` (default) in `lark.ws.Client`; reconnect loop in `_reconnect()` |
| CONN-05 | Environment variable config; refuse to start if required vars absent | `python-dotenv` 1.2.2 installed; Pydantic v2 for startup validation |
| CONN-06 | Resolve event competition with mi-feishu MCP | D-01 resolves this: new independent app. Document in deployment notes. |
| CARD-01 | Send "thinking" initial card within 3 seconds of message receipt | Reply via `lark.Client.im.v1.message.areply()` after creating card; 3s is satisfied by async task offload |
</phase_requirements>

---

## Summary

Phase 1 builds the Feishu connectivity pipeline: WebSocket long connection → event receipt → dedup/filter → "thinking" card reply within 3 seconds. No Claude integration yet.

The critical technical challenge is the threading/loop model. `lark.ws.Client` is NOT a daemon thread — it captures `asyncio.get_event_loop()` at module import time and blocks the calling thread with `loop.run_until_complete()`. The event handler registered via `register_p2_im_message_receive_v1()` is called **synchronously** (plain function call, not a coroutine) from within the SDK's internal async task. This means the handler must not block. The correct pattern is: sync handler calls `loop.create_task()` to schedule async work on the same loop that the WS client runs on.

Because lark-oapi 1.5.3 uses httpx internally for async HTTP (`Transport.aexecute` uses `httpx.AsyncClient`), and the lark `Client.areply()` method exists, all Feishu API calls (card create, message reply) can be awaited inside async tasks without blocking the event loop. The token-fetch path (`TokenManager.get_self_tenant_token`) is sync and uses `requests`, so it blocks on first call — accept this for startup/cache-miss scenarios in Phase 1.

**Primary recommendation:** Run `lark.ws.Client.start()` on the main thread (it blocks). Register a sync handler that calls `loop.create_task(handle_event(data))`. All real work (dedup, filter, card reply) happens inside `handle_event` as an async coroutine on the same loop.

---

## Standard Stack

### Verified Installed Versions (on this machine)

| Library | Version | Installed | Purpose |
|---------|---------|-----------|---------|
| lark-oapi | 1.5.3 | YES | Feishu SDK: WS client + all Feishu API calls |
| httpx | 0.28.1 | YES | (used internally by lark-oapi async transport; also available for direct CardKit PATCH) |
| python-dotenv | 1.2.2 | YES | Load .env file |
| pydantic | 2.12.5 | YES | Config validation model |
| structlog | NOT INSTALLED | NO | Need to install |
| tenacity | NOT INSTALLED | NO | Need to install (Phase 3 CardKit rate limit; not needed Phase 1) |
| pytest | NOT INSTALLED | NO | Need to install for tests |
| pytest-asyncio | NOT INSTALLED | NO | Need to install for async tests |

**Installation needed for Phase 1:**

```bash
pip install structlog pytest pytest-asyncio
# tenacity deferred to Phase 3 (CardKit rate limiting)
```

### Core: What Phase 1 Uses

| Component | API / Method | Notes |
|-----------|-------------|-------|
| `lark.ws.Client` | `Client(app_id, app_secret, event_handler=handler)` | `auto_reconnect=True` is default |
| `lark.ws.Client.start()` | blocking call | Runs `loop.run_until_complete(self._connect())` then `loop.run_until_complete(_select())` |
| `EventDispatcherHandler.builder()` | `.register_p2_im_message_receive_v1(fn)` | `fn` must be a **sync** function `(P2ImMessageReceiveV1) -> None` |
| `lark.Client.im.v1.message.areply()` | async | Use for sending "thinking" card reply |
| `lark.Client.arequest()` | async | Generic async API call |

---

## Architecture Patterns

### Verified Threading / Loop Architecture

The WS client source (verified at `/home/mi/.local/lib/python3.10/site-packages/lark_oapi/ws/client.py`) reveals:

```python
# At MODULE IMPORT TIME (ws/client.py top level):
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# start() is BLOCKING — runs the loop until program exits:
def start(self) -> None:
    loop.run_until_complete(self._connect())   # connect
    loop.create_task(self._ping_loop())         # background ping
    loop.run_until_complete(_select())          # block forever (sleep(3600) loop)

# Handler is called SYNCHRONOUSLY from within the async task:
# In _handle_data_frame (async) → self._event_handler.do_without_validation(pl)
#   → processor.do(data) → self.f(data)   ← YOUR HANDLER, called synchronously
```

**Critical implications:**

1. `lark.ws.Client.start()` blocks the calling thread until process exit.
2. The event handler `f` is called synchronously on the WS client's event loop thread.
3. The `loop` variable in `ws/client.py` is the SAME event loop your main code must use.
4. Do NOT create a separate `asyncio.run()` — import lark_oapi.ws before creating any event loop, and use `asyncio.get_event_loop()` to get the same loop.

### Pattern 1: Correct Sync Handler → Async Task Bridge

```python
# Source: verified from lark_oapi/ws/client.py + processor.py installed source

import asyncio
import lark_oapi as lark
from lark_oapi.ws.client import Client as WsClient

# IMPORTANT: get the loop AFTER lark_oapi.ws is imported (it sets the event loop)
# lark_oapi.ws captures the loop at import time via asyncio.get_event_loop()
from lark_oapi.ws import client as ws_client_module  # triggers loop capture
loop = asyncio.get_event_loop()

# The event_handler receives sync function f(data: P2ImMessageReceiveV1) -> None
def on_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    # Handler is called SYNCHRONOUSLY from within the WS async loop
    # Must return fast — just schedule work on the loop
    loop.create_task(handle_event_async(data))

async def handle_event_async(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    # All real work here: dedup, filter, card reply
    event_id = data.header.event_id
    message = data.event.message
    sender = data.event.sender
    # ... dedup check, filter, card reply
```

### Pattern 2: Event Payload Structure

```python
# Source: verified from installed SDK model files
# P2ImMessageReceiveV1:
#   .header.event_id: str           — use for dedup (unique per delivery attempt)
#   .event.sender.sender_id.open_id: str  — sender's open_id
#   .event.sender.sender_type: str   — "user"
#   .event.message.message_id: str   — unique message ID (stable across retries)
#   .event.message.chat_id: str      — chat room ID
#   .event.message.chat_type: str    — "p2p" or "group"
#   .event.message.message_type: str — "text", "post", "image", "file", "audio", "video", "sticker"
#   .event.message.content: str      — JSON string, structure varies by message_type
#   .event.message.mentions: List[MentionEvent]  — @mention list (empty if no mentions)
#     .mentions[i].id.open_id: str   — mentioned user's open_id
#     .mentions[i].key: str          — "@_user_1" etc.
#     .mentions[i].name: str         — display name
```

### Pattern 3: @Mention Detection for Group Chats

```python
# Source: verified from EventMessage model (chat_type, mentions fields)

def should_respond(message: lark.im.v1.EventMessage, bot_open_id: str) -> bool:
    """Return True if the bot should respond to this message."""
    if message.chat_type == "p2p":
        return True  # always respond in DM
    if message.chat_type == "group":
        # Only respond when explicitly @mentioned
        if not message.mentions:
            return False
        return any(m.id.open_id == bot_open_id for m in message.mentions)
    return False
```

### Pattern 4: Message Type Content Parsing

```python
import json

def parse_message_content(message: lark.im.v1.EventMessage) -> tuple[str, str]:
    """
    Returns (user_text, message_type_label) or raises ValueError for unsupported.
    content field is always a JSON string.
    """
    content = json.loads(message.content)
    msg_type = message.message_type

    if msg_type == "text":
        # {"text": "hello @bot"}
        # Strip @mentions from text before sending to Claude
        text = content.get("text", "")
        return text, "text"

    elif msg_type == "post":
        # Rich text: {"zh_cn": {"title": "...", "content": [[{"tag": "text", "text": "..."}]]}}
        # Extract plain text from all text nodes
        lang_key = next(iter(content), None)
        if lang_key:
            post = content[lang_key]
            parts = []
            for line in post.get("content", []):
                for node in line:
                    if node.get("tag") == "text":
                        parts.append(node.get("text", ""))
            return " ".join(parts), "post"
        return "", "post"

    else:
        # image, file, audio, video, sticker, etc.
        raise ValueError(f"unsupported_type:{msg_type}")
```

### Pattern 5: Message Deduplication

```python
# Source: architecture pattern, verified against event model
# Use event_id (data.header.event_id) as dedup key
# Feishu retries same event with same event_id
# message_id is stable per message (also valid for dedup but event_id is more precise)

from collections import OrderedDict
import time

class DeduplicationCache:
    """Bounded LRU cache for event deduplication. Thread-safe via asyncio single-loop."""

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 60):
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def is_duplicate(self, event_id: str) -> bool:
        """Returns True if event_id was seen recently (duplicate). Marks as seen."""
        now = time.monotonic()
        if event_id in self._cache:
            return True  # duplicate
        # Evict expired entries (simple: evict oldest if over max_size)
        if len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
        self._cache[event_id] = now
        return False
```

**Recommendation:** Use `event_id` from `data.header.event_id` (not `message_id`) as dedup key. `event_id` is unique per delivery attempt, `message_id` is stable per message. Both work; `event_id` is the tighter guard.

**TTL recommendation (Claude's Discretion):** 60 seconds covers Feishu's retry window (retries at ~3s, ~10s, ~30s). Max size 1000 entries is generous for a 2-5 user service.

### Pattern 6: Config Validation at Startup

```python
# Source: pydantic v2 (verified installed 2.12.5)
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings  # requires pydantic-settings package
# OR use pydantic BaseModel + manual env loading with python-dotenv

from dotenv import load_dotenv
import os

load_dotenv()  # loads .env file

class Config(BaseModel):
    app_id: str
    app_secret: str
    log_level: str = "INFO"
    working_dir: str = "."

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            app_id=os.environ["APP_ID"],        # raises KeyError if missing
            app_secret=os.environ["APP_SECRET"],
        )

# Startup guard: fail fast if required vars missing
try:
    config = Config.from_env()
except (KeyError, ValueError) as e:
    print(f"FATAL: Missing required config: {e}")
    sys.exit(1)
```

**Note:** `pydantic-settings` is a separate package (not included with pydantic v2). Use plain `os.environ` + pydantic BaseModel validation to avoid extra dependencies.

### Pattern 7: Initial "Thinking" Card

CardKit v2 card with status header. For Phase 1 (initial card only, no streaming), the card JSON is:

```json
{
  "type": "card",
  "data": {
    "schema": "2.0",
    "header": {
      "title": {
        "tag": "plain_text",
        "content": "AI 助手"
      },
      "template": "blue"
    },
    "body": {
      "elements": [
        {
          "tag": "markdown",
          "content": "**正在思考中...**"
        }
      ]
    }
  }
}
```

**How to send the card as a reply (Phase 1 approach — simpler, no CardKit pre-creation):**

For Phase 1 (CARD-01 only), use the IM reply API directly with `msg_type: interactive` and inline card content. No need to use CardKit POST API yet (that is for streaming in Phase 3).

```python
# Source: lark_oapi/api/im/v1/resource/message.py — areply() method verified
import json
import lark_oapi as lark

async def send_thinking_card(client: lark.Client, message_id: str) -> None:
    card_content = json.dumps({
        "type": "card",
        "data": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "AI 助手"},
                "template": "blue"
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": "**正在思考中...**\n\n_请稍候_"}
                ]
            }
        }
    }, ensure_ascii=False)

    request = (
        lark.im.v1.ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            lark.im.v1.ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(card_content)
            .build()
        )
        .build()
    )
    resp = await client.im.v1.message.areply(request)
    if not resp.success():
        raise RuntimeError(f"Card reply failed: {resp.code} {resp.msg}")
```

**Phase 1 decision:** Use inline card content via `im.v1.message.areply()`. CardKit pre-creation (POST to `/cardkit/v1/cards`) is deferred to Phase 3 when streaming PATCH updates are needed.

### Pattern 8: WS Client Startup

```python
# Source: lark_oapi/ws/client.py — start() method verified

import lark_oapi as lark
from lark_oapi.ws import client as _ws_init  # ensures loop is captured at import time

# Build event handler (sync handler ONLY — SDK does not support async handlers)
event_handler = (
    lark.EventDispatcherHandler.builder("", "")  # no encrypt_key, no verification_token needed for WS
    .register_p2_im_message_receive_v1(on_message)  # on_message is sync
    .build()
)

# Build WS client
ws_client = lark.ws.Client(
    app_id=config.app_id,
    app_secret=config.app_secret,
    event_handler=event_handler,
    log_level=lark.LogLevel.DEBUG,
    auto_reconnect=True,  # default True — built-in reconnect
)

# This BLOCKS the calling thread until process exit
# Call from main thread
ws_client.start()
```

### Recommended Project Structure

```
feishu-bridge/
├── .env                    # APP_ID, APP_SECRET, LOG_LEVEL
├── .env.example            # committed to git, no real secrets
├── requirements.txt        # pinned dependencies
├── main.py                 # entry point: load config, start WS client
└── src/
    ├── config.py           # Config model, load_dotenv, startup validation
    ├── dedup.py            # DeduplicationCache
    ├── filters.py          # should_respond(), parse_message_content()
    ├── cards.py            # send_thinking_card(), card JSON builders
    └── handler.py          # on_message() sync handler + handle_event_async()
```

### Anti-Patterns to Avoid

- **Async handler registration:** The SDK calls `self.f(data)` synchronously. Registering a coroutine instead of a sync function will pass the coroutine object to `do()` which returns it without awaiting — events silently dropped.
- **Blocking in handler:** Any I/O in the sync handler blocks the WS event loop. Use `loop.create_task()` only.
- **Creating a separate event loop:** Calling `asyncio.run()` after importing lark_oapi.ws creates a new loop, different from the WS client's loop. `loop.create_task()` will post to the wrong loop and the task never runs.
- **Using `message_id` for dedup when using inline card replies:** Message ID is the right key for "don't process the same message twice". Event ID is the right key for "don't even start processing this delivery twice". Use `event_id` at the earliest possible point.
- **Sending card as `msg_type: "text"` with card JSON:** Only `msg_type: "interactive"` renders as a card. Using `"text"` will display the raw JSON string.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WebSocket long connection | Custom WS client | `lark.ws.Client` | Handles reconnect, ping/pong, protobuf framing, token refresh |
| Token caching | In-memory token dict | `lark.Client` (SDK handles it) | `TokenManager` already caches with 10-min-early expiry |
| Event deserialization | JSON parsing | `lark.EventDispatcherHandler` | SDK deserializes `P2ImMessageReceiveV1` with typed fields |
| HTTP retry | Manual retry loop | `tenacity` (Phase 3) | Handles jitter, backoff, exception types correctly |
| .env loading | `os.getenv()` calls | `python-dotenv` | Handles quoting, comments, override logic correctly |

---

## Common Pitfalls

### Pitfall 1: Async Handler Not Called (Silent Event Drop)

**What goes wrong:** Developer registers an `async def` function with `register_p2_im_message_receive_v1`. The SDK calls `self.f(data)` which returns a coroutine object. The coroutine is never awaited — events are silently dropped with no error.

**Why it happens:** `P2ImMessageReceiveV1Processor.do()` calls `self.f(data)` without checking if the result is a coroutine.

**How to avoid:** Register a **sync** function only. Schedule async work with `loop.create_task()` inside the sync handler.

**Warning signs:** No errors, bot receives no messages despite WS connection showing "connected".

---

### Pitfall 2: Wrong Event Loop for create_task

**What goes wrong:** Code does `asyncio.run(main())` or `asyncio.get_event_loop()` BEFORE importing `lark_oapi.ws`. The WS module captures a different loop at import time. `loop.create_task()` inside the handler posts to the WS client's loop, but async tasks (lark API calls, card sending) run in the wrong loop.

**Why it happens:** `lark_oapi/ws/client.py` captures `loop = asyncio.get_event_loop()` at module import time (top-level module code).

**How to avoid:** Import `lark_oapi.ws` (or `import lark_oapi`) before creating any event loop. Then call `asyncio.get_event_loop()` to get the same captured loop for use in your handler.

**Warning signs:** `RuntimeError: Task attached to a different loop` or tasks scheduled but never executing.

---

### Pitfall 3: Group Messages Processed Without @Mention Check

**What goes wrong:** Bot responds to every message in a group chat, not just @mentions. Users are annoyed; bot spams the channel.

**Why it happens:** CONN-03 check (`mentions` list) is missing.

**How to avoid:** Check `message.chat_type == "group"` and verify `any(m.id.open_id == bot_open_id for m in (message.mentions or []))`.

**Warning signs:** Bot replies to group messages that don't @mention it.

---

### Pitfall 4: Card Reply Blocks 3-Second Window

**What goes wrong:** Card reply HTTP call (`areply`) is awaited directly inside the sync handler instead of inside an async task. The sync handler blocks for the duration of the HTTP call (100-500ms + latency), potentially causing other events to queue up or Feishu to retry.

**Why it happens:** Developer calls `asyncio.run(send_thinking_card(...))` inside the sync handler.

**How to avoid:** Sync handler calls `loop.create_task(handle_event_async(data))` only. Card sending happens inside `handle_event_async`.

---

### Pitfall 5: Bot Open ID Not Known at Startup

**What goes wrong:** @mention check requires knowing the bot's own `open_id` to compare against `mentions[i].id.open_id`. If this is not fetched at startup, group chat filtering fails.

**How to avoid:** Fetch bot info at startup using `lark.Client.bot.v3.bot.get()` or `application.v6` API to get the bot's `open_id`. Store in config. Alternatively, use `sender_type == "user"` and check if the bot's `app_id` matches a mention — but `open_id` comparison is cleaner.

**Warning signs:** Bot either ignores all group messages or responds to all.

---

### Pitfall 6: `lark.ws.Client` Reconnect Parameters

**What goes wrong:** Default reconnect interval is 120 seconds (`_reconnect_interval: int = 120`). After a network drop, the bot is unreachable for up to 120 seconds.

**Why it happens:** Default config from Feishu's server-side `ClientConfig` (sent via PONG frame). The server controls actual intervals after initial connection; the 120s is the fallback if server config is not received.

**How to avoid:** Accept the 120s default for Phase 1. The `_reconnect_count: int = -1` (unlimited retries) means the bot will eventually reconnect. CONN-04 is satisfied with `auto_reconnect=True`. Document the 120s window in deployment notes.

---

## Code Examples

### Complete Handler Registration (Minimal Phase 1)

```python
# Source: verified from lark_oapi/ws/client.py + processor.py source
import asyncio
import json
import os
import sys
import lark_oapi as lark
from dotenv import load_dotenv

# STEP 1: Load env before anything else
load_dotenv()
APP_ID = os.environ.get("APP_ID", "")
APP_SECRET = os.environ.get("APP_SECRET", "")
if not APP_ID or not APP_SECRET:
    print("FATAL: APP_ID and APP_SECRET required", file=sys.stderr)
    sys.exit(1)

# STEP 2: Import lark_oapi.ws to capture event loop, then get the same loop
import lark_oapi.ws  # ensures module-level loop capture
loop = asyncio.get_event_loop()

# STEP 3: Build lark API client (for card/message sending)
api_client = lark.Client.builder() \
    .app_id(APP_ID) \
    .app_secret(APP_SECRET) \
    .build()

# STEP 4: Dedup cache (module-level singleton)
seen_event_ids: set[str] = set()  # simple set for Phase 1; replace with LRU for production

# STEP 5: Sync handler (called by SDK synchronously on WS loop)
def on_message_receive(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    loop.create_task(handle_message(data))

async def handle_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    event_id = data.header.event_id
    if event_id in seen_event_ids:
        return  # duplicate delivery
    seen_event_ids.add(event_id)

    message = data.event.message
    sender = data.event.sender

    # Group chat: only respond if @mentioned
    if message.chat_type == "group":
        bot_open_id = "..."  # fetched at startup
        mentions = message.mentions or []
        if not any(m.id.open_id == bot_open_id for m in mentions):
            return

    # Send thinking card
    await send_thinking_card(api_client, message.message_id)

async def send_thinking_card(client: lark.Client, message_id: str) -> None:
    card_json = json.dumps({
        "type": "card",
        "data": {
            "schema": "2.0",
            "header": {"title": {"tag": "plain_text", "content": "AI 助手"}, "template": "blue"},
            "body": {"elements": [{"tag": "markdown", "content": "**正在思考中...**"}]}
        }
    }, ensure_ascii=False)
    req = (lark.im.v1.ReplyMessageRequest.builder()
           .message_id(message_id)
           .request_body(lark.im.v1.ReplyMessageRequestBody.builder()
                         .msg_type("interactive")
                         .content(card_json)
                         .build())
           .build())
    resp = await client.im.v1.message.areply(req)
    if not resp.success():
        raise RuntimeError(f"reply failed: {resp.code} {resp.msg}")

# STEP 6: Build event handler and WS client, then start (blocks)
handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(on_message_receive)
    .build()
)

ws = lark.ws.Client(
    app_id=APP_ID,
    app_secret=APP_SECRET,
    event_handler=handler,
    auto_reconnect=True,
)
ws.start()  # blocks until process exit
```

### Fetch Bot Open ID at Startup

```python
# Need bot's own open_id for group @mention detection
# Use lark.Client.bot.v3 (botinfo API)
# This is a sync call acceptable at startup

import lark_oapi as lark

def get_bot_open_id(client: lark.Client) -> str:
    from lark_oapi.api.bot.v3.model import GetBotRequest
    req = GetBotRequest.builder().build()
    resp = client.bot.v3.bot.get(req)  # sync
    if not resp.success():
        raise RuntimeError(f"bot info failed: {resp.code} {resp.msg}")
    return resp.data.open_id
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Webhook HTTP server | WebSocket long connection (`lark.ws.Client`) | 2023+ | No public IP needed; simpler deployment |
| `claude-code-sdk` | `claude-agent-sdk >= 0.1.53` | Sept 2025 | SDK renamed; old package deprecated and unmaintained |
| Per-token CardKit PATCH | Batched PATCH at 300-500ms | Always recommended | Rate limit compliance; smooth animation |
| Polling for bot responses | Streaming via `claude-agent-sdk` | Phase 2 | Not relevant for Phase 1 |

---

## Open Questions

1. **CardKit `streaming_config` exact parameters for Phase 3**
   - What we know: `print_step`, `print_frequency_ms`, `print_strategy` mentioned in research
   - What's unclear: Official docs behind JS rendering; exact field names and effect unverified
   - Recommendation: Accept MEDIUM confidence for Phase 3. Phase 1 uses inline card (no CardKit streaming needed).

2. **Bot open_id retrieval API exact method name**
   - What we know: `lark.Client.bot.v3` exists; `GetBotRequest` is the model
   - What's unclear: exact import path and method name not verified against SDK source (bot v3 resource not checked)
   - Recommendation: Verify at implementation time with `python3 -c "from lark_oapi.api.bot.v3.resource.bot import *; help(BotResource.get)"` or similar.

3. **`EventDispatcherHandler.builder("", "")` — empty encrypt_key and verification_token**
   - What we know: For WebSocket long connection, signature verification is not needed (handled at transport layer)
   - What's unclear: Whether empty strings cause warnings or errors
   - Recommendation: Acceptable for Phase 1; if errors occur, pass `None` instead of `""`.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.10 | All code | YES | 3.10.12 | — |
| lark-oapi | CONN-01, CONN-04, CARD-01 | YES | 1.5.3 | — |
| httpx | lark-oapi async transport | YES | 0.28.1 | — |
| python-dotenv | CONN-05 | YES | 1.2.2 | — |
| pydantic v2 | CONN-05 config validation | YES | 2.12.5 | — |
| structlog | logging | NO | — | Use Python `logging` stdlib; install structlog in Wave 0 |
| pytest | testing | NO | — | Install in Wave 0 |
| pytest-asyncio | async test support | NO | — | Install in Wave 0 |
| tenacity | CardKit rate limit retry | NO | — | Not needed Phase 1 (no streaming) |
| New Feishu app credentials | D-01, all features | UNKNOWN | — | Must be created before any testing possible |

**Missing dependencies with no fallback:**

- **New Feishu app (APP_ID + APP_SECRET):** Cannot test any Phase 1 functionality without a registered Feishu app that has IM subscription permissions. This is a human action required before Phase 1 can be verified end-to-end.

**Missing dependencies with fallback:**

- `structlog`: Fall back to stdlib `logging` in Wave 0; can install structlog and switch later. Install recommended before Wave 1.
- `pytest`, `pytest-asyncio`: Wave 0 installs these.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (neither installed yet) |
| Config file | `pytest.ini` or `pyproject.toml` — created in Wave 0 |
| Quick run command | `python3.10 -m pytest tests/ -x -q` |
| Full suite command | `python3.10 -m pytest tests/ -v` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CONN-01 | WS event received and handler called | unit (mock WS) | `pytest tests/test_handler.py -x` | No — Wave 0 |
| CONN-02 | Duplicate event_id returns without processing | unit | `pytest tests/test_dedup.py -x` | No — Wave 0 |
| CONN-03 | Group msg without @mention ignored; with @mention handled | unit | `pytest tests/test_filters.py::test_group_filter -x` | No — Wave 0 |
| CONN-03 | P2P message always handled | unit | `pytest tests/test_filters.py::test_p2p_always -x` | No — Wave 0 |
| CONN-04 | WS client has auto_reconnect=True | unit (constructor check) | `pytest tests/test_ws_client.py::test_reconnect_config -x` | No — Wave 0 |
| CONN-05 | Process exits if APP_ID missing | unit | `pytest tests/test_config.py::test_missing_required -x` | No — Wave 0 |
| CONN-06 | Deployment doc notes isolation requirement | manual | — | manual |
| CARD-01 | Thinking card sent after message received | unit (mock areply) | `pytest tests/test_handler.py::test_card_sent -x` | No — Wave 0 |

### Sampling Rate

- Per task commit: `python3.10 -m pytest tests/ -x -q`
- Per wave merge: `python3.10 -m pytest tests/ -v`
- Phase gate: Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/__init__.py` — package marker
- [ ] `tests/conftest.py` — shared fixtures (mock lark Client, mock WS data, sample event payloads)
- [ ] `tests/test_dedup.py` — covers CONN-02
- [ ] `tests/test_filters.py` — covers CONN-03
- [ ] `tests/test_config.py` — covers CONN-05
- [ ] `tests/test_handler.py` — covers CONN-01, CARD-01
- [ ] `tests/test_ws_client.py` — covers CONN-04
- [ ] `pytest.ini` or `[tool.pytest.ini_options]` in `pyproject.toml`
- [ ] Framework install: `pip install pytest pytest-asyncio structlog` — Wave 0 task

---

## Project Constraints (from CLAUDE.md)

| Constraint | Impact on Phase 1 |
|------------|------------------|
| Python 3.10+ / lark-oapi / asyncio | Use only these; no alternative runtimes |
| 3-second callback window (hard limit) | Sync handler MUST delegate to async task immediately |
| Max 50 WS connections, cluster mode, no broadcast | Only one process per app ID; new app (D-01) resolves this |
| CardKit PATCH not wrapped by lark-oapi | Phase 1 avoids CardKit entirely — use inline card reply via IM API |
| No sudo | `pip install --user` or venv; systemd `--user` for deployment |
| Shared app with mi-feishu MCP | D-01 (new app) resolves; document in deployment notes |
| No direct repo edits outside GSD workflow | All code written via GSD execute-phase |

---

## Sources

### Primary (HIGH confidence — verified from installed SDK source)

- `lark_oapi/ws/client.py` v1.5.3 — loop capture model, `start()` blocking behavior, `_handle_data_frame` sync handler call
- `lark_oapi/api/im/v1/processor.py` v1.5.3 — `P2ImMessageReceiveV1Processor.do()` is sync
- `lark_oapi/api/im/v1/model/p2_im_message_receive_v1.py` — event payload structure
- `lark_oapi/api/im/v1/model/event_message.py` — `EventMessage` fields: `message_id`, `chat_id`, `chat_type`, `message_type`, `content`, `mentions`
- `lark_oapi/api/im/v1/model/mention_event.py` — `MentionEvent.id.open_id` for @mention detection
- `lark_oapi/api/im/v1/model/event_sender.py` — `EventSender` fields
- `lark_oapi/event/context.py` — `EventHeader.event_id` confirmed
- `lark_oapi/event/dispatcher_handler.py` — `do_without_validation()` dispatcher logic
- `lark_oapi/client.py` — `arequest()` async method confirmed; `areply()` method on `im.v1.message`
- `lark_oapi/core/token/manager.py` — `get_self_tenant_token()` is sync HTTP (requests library)
- `lark_oapi/core/http/transport.py` — `aexecute()` uses `httpx.AsyncClient` (confirmed async)

### Secondary (MEDIUM confidence)

- `.planning/research/STACK.md` — stack decisions confirmed still accurate
- `.planning/research/ARCHITECTURE.md` — architecture patterns confirmed; minor correction: "aiohttp" references in doc should be "httpx" (lark-oapi uses httpx internally, not aiohttp)
- `.planning/research/PITFALLS.md` — all Phase 1 pitfalls remain valid

### Tertiary (LOW confidence)

- CardKit `streaming_config` parameters — deferred to Phase 3; not needed for Phase 1

---

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH — all library versions verified from installed packages on this machine
- Threading model: HIGH — confirmed from reading actual SDK source code (ws/client.py, processor.py)
- Event payload structure: HIGH — confirmed from installed model files
- Architecture patterns: HIGH — patterns derived directly from SDK source, not documentation
- Initial card JSON: MEDIUM — card schema 2.0 syntax verified against known Feishu docs patterns; exact rendering depends on live Feishu app config
- Bot open_id retrieval API path: MEDIUM — bot.v3 module existence confirmed, exact method not source-checked

**Research date:** 2026-04-01
**Valid until:** 2026-07-01 (lark-oapi 1.5.3 stable; SDK source directly verified)
