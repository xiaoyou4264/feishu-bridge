# Architecture Patterns

**Domain:** Feishu bot ↔ Claude Agent SDK bridge service
**Researched:** 2026-04-01
**Confidence:** HIGH (Claude Agent SDK docs verified via official platform.claude.com; lark-oapi patterns verified via GitHub/PyPI; CardKit patterns from official Feishu docs via web search; 3-second limit is documented Feishu platform constraint)

---

## Recommended Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     feishu-bridge process                   │
│                                                             │
│  ┌──────────────┐    asyncio.Queue    ┌──────────────────┐  │
│  │  WebSocket   │ ─────────────────► │  Event Router    │  │
│  │  Receiver    │                    │  (async loop)    │  │
│  │  (thread)    │                    └────────┬─────────┘  │
│  └──────────────┘                             │             │
│                                    ┌──────────▼──────────┐  │
│                                    │  Session Manager    │  │
│                                    │  (conversation map) │  │
│                                    └──────────┬──────────┘  │
│                                               │ asyncio.Task │
│                              ┌────────────────▼────────────┐ │
│                              │   Conversation Worker       │ │
│                              │  (one per active chat)      │ │
│                              │  ┌─────────────────────┐   │ │
│                              │  │  Agent SDK Client   │   │ │
│                              │  │  (ClaudeSDKClient)  │   │ │
│                              │  └──────────┬──────────┘   │ │
│                              │             │ streaming     │ │
│                              │  ┌──────────▼──────────┐   │ │
│                              │  │  Card Renderer      │   │ │
│                              │  │  (PATCH accumulator)│   │ │
│                              │  └─────────────────────┘   │ │
│                              └─────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
              │ HTTP                         │ HTTP
              ▼                             ▼
       Feishu CardKit API          Feishu IM API
       (create + PATCH cards)      (reply messages)
```

---

## Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| **WebSocket Receiver** | Maintains `lark.ws.Client` connection in a daemon thread; forwards raw events via `asyncio.Queue` using `loop.call_soon_threadsafe()` | Event Router (via queue) |
| **Event Router** | `async for` loop draining the queue; dispatches `im.message.receive_v1` to Session Manager, card callbacks to Callback Handler | Session Manager, Callback Handler |
| **Session Manager** | Maps `(chat_id, user_id)` → active `asyncio.Task`; deduplicates messages by `message_id`; enforces one-task-per-conversation invariant | Conversation Workers (spawn/cancel) |
| **Conversation Worker** | Runs per active conversation; owns one `ClaudeSDKClient`; streams SDK messages to Card Renderer; handles turn lifecycle | Agent SDK, Card Renderer |
| **Agent SDK Client** | `ClaudeSDKClient` async context manager; multi-turn session with `resume` capability; emits `AssistantMessage`, `ResultMessage`, `SystemMessage` | Claude Agent SDK subprocess |
| **Card Renderer** | Creates a CardKit card via POST, sends it as reply, then accumulates text from `TextBlock`s and PATCHes with monotonically increasing `sequence`; marks stream done on `ResultMessage` | Feishu CardKit API, Feishu IM API |
| **Callback Handler** | Handles `card.action.trigger` events (button clicks); performs immediate ACK within 3 seconds; routes action to relevant conversation | Session Manager |
| **Config** | Loads env vars (`APP_ID`, `APP_SECRET`, `ANTHROPIC_API_KEY`, `WORK_DIR`, `ALLOWED_TOOLS`) at startup | All components |

---

## Data Flow

### Primary Flow: User Message → Streaming Card Reply

```
1. Feishu platform
   └─► lark.ws.Client (daemon thread)
       └─► on_message_receive_v1(data) [sync handler, must return fast]
           └─► loop.call_soon_threadsafe(queue.put_nowait, event)
               [CROSSES THREAD BOUNDARY — thread-safe]

2. asyncio event loop
   └─► Event Router drains queue
       └─► dedup check: message_id seen? → discard
       └─► Session Manager.dispatch(chat_id, user_id, message)
           └─► existing Task running? → queue message for it
           └─► no Task? → spawn new Conversation Worker Task

3. Conversation Worker (asyncio.Task)
   └─► async with ClaudeSDKClient(options=opts) as client:
       └─► Card Renderer.create_card(chat_id, reply_to=message_id)
           └─► POST /cardkit/v1/cards → card_id
           └─► POST /im/v1/messages/{msg_id}/reply (msg_type=interactive, card_id)
       └─► await client.query(user_text)
       └─► async for message in client.receive_response():
           ├─► AssistantMessage → extract TextBlocks → Card Renderer.append(text)
           │   └─► accumulate buffer → PATCH /cardkit/v1/cards/{card_id}
           │       body: {sequence: N, card: {elements: [{tag: markdown, content: buffer}]}}
           └─► ResultMessage → Card Renderer.finalize()
               └─► final PATCH with complete text + done indicator

4. Next user message in same chat
   └─► Session Manager finds existing Task (ClaudeSDKClient still alive)
   └─► await client.query(next_message)   [session context preserved]
```

### 3-Second Limit: How It Is Satisfied

The `lark.ws.Client` sync handler MUST return within 3 seconds. The bridge satisfies this by:

1. The sync handler does exactly one thing: `loop.call_soon_threadsafe(queue.put_nowait, event)` — this is microseconds, never times out.
2. All actual work (Claude API calls, CardKit HTTP) happens in asyncio Tasks — completely decoupled from the WebSocket event delivery.
3. There is no HTTP webhook server; the long-connection model means the 3-second limit applies to the sync callback, not to HTTP response time. The queue bridge is the correct pattern.

### Card Callback Flow: Button Click → Action

```
1. Feishu platform sends card.action.trigger event
2. Sync handler → queue.put_nowait(callback_event)
3. Callback Handler receives event
4. Immediately acknowledges (returns {} within 3 seconds — same queue bridge)
5. Routes action payload to appropriate Conversation Worker
```

---

## Session / Conversation State Management

### Per-Conversation State (in `ConversationWorker`)

```python
@dataclass
class ConversationState:
    chat_id: str
    user_id: str                    # or group chat id
    sdk_client: ClaudeSDKClient     # owns the session
    current_card_id: str | None     # active card being streamed
    sequence_counter: int           # monotonically increasing, per card
    message_queue: asyncio.Queue    # buffered incoming messages
    last_activity: float            # for idle timeout
```

### Session Manager (global registry)

```python
# Key: (chat_id, user_id) for 1:1 DMs, (chat_id,) for group chats
active_sessions: dict[str, asyncio.Task] = {}
message_id_cache: set[str] = set()   # dedup, bounded LRU ~1000 entries
```

### Multi-Turn Context Preservation

Use `ClaudeSDKClient` (not bare `query()`) because it:
- Maintains session context across multiple `await client.query()` calls automatically
- Handles the `resume` / session ID tracking internally
- Supports `interrupt()` if a user sends a new message while Claude is processing

Session lifetime matches conversation lifetime — one `ClaudeSDKClient` context manager per conversation worker. When the worker is idle for N minutes (configurable), it gracefully exits, releasing the session.

---

## Concurrency Model

### asyncio Task Per Conversation

```
main loop
├── ws_thread (daemon thread — lark.ws.Client.start())
├── Event Router Task (single, drains queue)
└── Conversation Worker Tasks (one per active chat)
    ├── Worker[chat_id=A]  — running Claude query
    ├── Worker[chat_id=B]  — waiting for next message
    └── Worker[chat_id=C]  — streaming card PATCH updates
```

**Why asyncio Tasks (not threads):**
- Card PATCH HTTP calls are I/O-bound — asyncio yields during `aiohttp` awaits
- Claude SDK `query()` / `receive_response()` are async generators — native asyncio
- Python GIL is not a bottleneck for I/O-heavy work
- One thread per conversation would be wasteful for 2-5 users

**Error Isolation:**
Each Conversation Worker is a separate `asyncio.Task`. An unhandled exception in Worker[A] does NOT affect Worker[B] or Worker[C].

```python
async def spawn_worker(chat_id, ...):
    task = asyncio.create_task(
        conversation_worker(chat_id, ...),
        name=f"conv-{chat_id}"
    )
    task.add_done_callback(lambda t: handle_worker_exit(t, chat_id))
    active_sessions[chat_id] = task

def handle_worker_exit(task, chat_id):
    active_sessions.pop(chat_id, None)
    if task.exception():
        logger.error(f"Worker {chat_id} failed: {task.exception()}")
        # Optionally send error card to user
```

---

## CardKit Streaming Pattern (Verified)

Three-phase lifecycle per assistant response:

### Phase 1: Create Card

```http
POST /open-apis/cardkit/v1/cards
Authorization: Bearer {tenant_access_token}
Content-Type: application/json

{
  "type": "card",
  "data": {
    "schema": "2.0",
    "body": {"elements": [{"tag": "markdown", "content": "..."}]},
    "streaming": true,
    "streaming_config": {
      "print_step": 2,
      "print_frequency_ms": 30,
      "print_strategy": "fast"
    }
  }
}
```

Response: `{"data": {"card_id": "crd_xxx"}}`

### Phase 2: Send Card as Reply

```http
POST /open-apis/im/v1/messages/{message_id}/reply
{
  "msg_type": "interactive",
  "content": "{\"type\": \"card\", \"data\": {\"card_id\": \"crd_xxx\"}}"
}
```

### Phase 3: Stream PATCH Updates

```http
PATCH /open-apis/cardkit/v1/cards/{card_id}
{
  "sequence": 1,          # monotonically increasing per card
  "uuid": "uuid-v4",      # dedup guard
  "card": {
    "elements": [{"tag": "markdown", "content": "accumulated text so far"}]
  }
}
```

Repeat with `sequence: 2, 3, ...N` as Claude streams tokens. Final PATCH marks stream complete.

**Implementation note:** lark-oapi does NOT wrap the CardKit PATCH API. Use `aiohttp.ClientSession` (async) for PATCH calls with `tenant_access_token`. The token is obtained via `lark.Client` (standard SDK method) and cached.

**Rate limit:** PATCH interval should be >= 30ms. Buffer Claude tokens for ~100ms chunks rather than PATCHing every token to stay well within limits and reduce noise.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Blocking the WebSocket Handler Thread

**What:** Running any I/O (HTTP requests, SDK calls) inside the `on_message_receive_v1` sync handler.

**Why bad:** The handler must return within 3 seconds. Claude queries take 10-60+ seconds. The entire WebSocket connection blocks.

**Instead:** Queue bridge — sync handler posts to asyncio Queue; all work happens in Tasks.

---

### Anti-Pattern 2: One `query()` Call Per Message (Losing Session Context)

**What:** Using bare `query(prompt=msg, options=ClaudeAgentOptions())` for each incoming message, creating a fresh session each time.

**Why bad:** Claude has no memory of previous turns in the same chat. User asks "do that again" → Claude has no idea what "that" is.

**Instead:** Use `ClaudeSDKClient` per conversation, which automatically continues the session across `await client.query()` calls.

---

### Anti-Pattern 3: Global Sequence Counter for CardKit

**What:** Sharing one `sequence` integer across all conversations.

**Why bad:** Sequence numbers are per-card, not per-process. A counter shared globally will emit wildly wrong sequence values and cause PATCH requests to be silently dropped by the API.

**Instead:** Each `ConversationWorker` owns its own `sequence_counter`, reset to 0 when a new card is created.

---

### Anti-Pattern 4: Thread-per-Conversation

**What:** Using `threading.Thread` for each conversation worker to avoid the asyncio complexity.

**Why bad:** 5 conversations = 5 threads is fine, but threads are heavier than Tasks, and the Claude SDK + CardKit HTTP is all I/O-bound, so threads provide no benefit while adding synchronization complexity.

**Instead:** `asyncio.Task` per conversation with `aiohttp` for async HTTP.

---

### Anti-Pattern 5: Not Deduplicating Message IDs

**What:** Processing every `im.message.receive_v1` event without checking `message_id`.

**Why bad:** Feishu retries event delivery if it suspects the handler didn't respond. Without dedup, the same user message triggers two Claude queries.

**Instead:** Keep a bounded LRU set of seen `message_id` values in the Session Manager.

---

## Scalability Considerations

This service targets 2-5 users. Scalability analysis is academic but informs design decisions:

| Concern | At 2-5 users | At 50 users | At 500 users |
|---------|-------------|-------------|--------------|
| asyncio Tasks | Fine — trivial | Fine — I/O bound tasks yield well | Fine — asyncio handles thousands |
| lark-oapi connections | 1 WS connection sufficient | 1 connection, 50 concurrent events | Multiple WS connections needed (50 max per connection) |
| Session memory | Negligible | ~10MB (50 active ClaudeSDKClients) | Need session eviction policy |
| CardKit PATCH rate | No concern | Monitor rate limits | Rate limit batching critical |
| Claude Agent SDK processes | 1 subprocess per session | Resource concern | Need subprocess pooling |

For MVP (2-5 users): single WS connection, no process pooling, simple in-memory session map.

---

## Build Order (Dependency Graph)

Components have strict dependencies. Build in this order:

```
1. Config loader
   (no deps — reads env vars, validates required keys)

2. WebSocket Receiver + asyncio.Queue bridge
   (deps: Config)
   Validate: can receive im.message.receive_v1 events

3. Card Renderer (CardKit POST + PATCH)
   (deps: Config, tenant_access_token fetch)
   Validate: can create a card and PATCH text updates

4. Agent SDK integration (single-turn)
   (deps: Config, Card Renderer)
   Validate: can query Claude and stream TextBlocks to a card

5. Session Manager + Conversation Worker lifecycle
   (deps: WebSocket Receiver, Agent SDK, Card Renderer)
   Validate: multi-turn conversation preserves context; worker dies cleanly

6. Event Router (wires it all together)
   (deps: WebSocket Receiver, Session Manager)
   Validate: end-to-end message → card reply

7. Error handling + idle session cleanup
   (deps: all above)
   Validate: exception in one conversation doesn't affect others

8. Card callback handler (button interactions)
   (deps: Event Router, Session Manager)
   Validate: button click routes to correct conversation
```

---

## Sources

- Claude Agent SDK sessions (HIGH confidence — official): https://platform.claude.com/docs/en/agent-sdk/sessions
- Claude Agent SDK Python reference (HIGH confidence — official): https://platform.claude.com/docs/en/agent-sdk/python
- Claude Agent SDK overview (HIGH confidence — official): https://platform.claude.com/docs/en/agent-sdk/overview
- lark-oapi WebSocket + asyncio integration (MEDIUM confidence — GitHub issues + PyPI): https://github.com/larksuite/oapi-sdk-python
- Feishu 3-second callback limit (HIGH confidence — documented platform constraint): https://open.feishu.cn/document
- CardKit streaming card PATCH API (MEDIUM confidence — web search synthesis, official doc URLs blocked by bot protection): https://open.feishu.cn/document/cardkit/v1/card/overview
