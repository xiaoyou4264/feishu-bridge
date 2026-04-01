# Phase 4: Stability and Operations - Research

**Researched:** 2026-04-01
**Domain:** Python asyncio process lifecycle, systemd user services, structlog contextvars, Feishu card action callbacks
**Confidence:** HIGH — all critical patterns verified against installed libraries and live code inspection

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-30:** Every message handler has independent try/except; exceptions reply error card via send_error_card(), never propagate.
- **D-31:** Use `asyncio.get_event_loop().add_signal_handler(signal.SIGTERM, ...)` to register shutdown.
- **D-32:** SIGTERM sequence: stop new messages → cancel all active asyncio Tasks → wait max 10s → clean Claude SDK subprocesses → exit.
- **D-33:** Create `~/.config/systemd/user/feishu-bridge.service`, manage with `systemctl --user`.
- **D-34:** `Restart=on-failure`, `RestartSec=3`. One-time `loginctl enable-linger` for survive-logout behavior.
- **D-35:** Bind `event_id` to structlog context per message; all sub-calls auto-carry it.
- **D-36:** Production: `structlog.processors.JSONRenderer()`. Switch controlled by `LOG_FORMAT` env var.
- **D-37:** Background asyncio Task scans SessionManager every 60s, destroys sessions idle > SESSION_TTL (default 3600s, env-configurable).
- **D-38:** TTL cleanup calls `SessionManager.destroy()` to disconnect ClaudeSDKClient.
- **D-39:** `/help` replies static card listing `/new` and `/help` commands. Green header.
- **D-40:** Streaming card has "停止" button. On click: cancel the asyncio Task for that message_id, reply "已停止".
- **D-41:** `claude_worker.py` maintains `message_id → Task` mapping; card callback handler looks up and cancels.
- **D-42:** Final card has 👍/👎 buttons. Click records structlog event — no database.
- **D-43:** Buttons use `card.action.trigger` callback. Phase 3 infrastructure already registered.

### Claude's Discretion
- Exact ExecStart path in systemd service file
- Log rotation strategy (journald handles it automatically)
- Stop button 3-second callback window handling strategy
- Feedback log format details

### Deferred Ideas (OUT OF SCOPE)
None — this is the final phase.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STAB-01 | Process-level exception recovery — single message error does not crash service, sends error card | D-30: per-message try/except already exists in claude_worker.py; Phase 4 hardens edge cases in handler.py |
| STAB-02 | SIGTERM graceful exit — cascade cancel all active Tasks, clean subprocesses | D-31/D-32: asyncio signal handler + all_tasks cancellation + asyncio.wait(timeout=10) |
| STAB-03 | systemd user service — autostart, crash restart | D-33/D-34: ~/.config/systemd/user/, Restart=on-failure, loginctl linger already enabled |
| STAB-04 | Structured JSON logging with event_id correlation | D-35/D-36: structlog.contextvars.bind_contextvars() + JSONRenderer, LOG_FORMAT env var |
| SESS-04 | /help command — static card listing available commands | D-39: green header card, handled in handler.py before thinking card dispatch |
| SESS-05 | Idle session TTL cleanup | D-37/D-38: background asyncio.Task every 60s, SESSION_TTL env var, calls destroy() |
| INTER-01 | Stop button — cancel running Claude task from card | D-40/D-41: message_id→Task dict in claude_worker.py, card callback cancels task |
| INTER-02 | Feedback buttons 👍/👎 — log to structlog, no database | D-42/D-43: card action callback, structlog event with action.value |
</phase_requirements>

---

## Summary

Phase 4 is a hardening and operations pass over a fully functional bridge. All eight requirements add resilience, observability, and interactivity without changing the core message-processing pipeline. The most architecturally novel piece is the Stop button (INTER-01), which requires a shared `message_id → asyncio.Task` dict between the worker and the card callback handler — the only bidirectional state coupling introduced this phase.

SIGTERM handling is straightforward with asyncio's built-in `loop.add_signal_handler()`. The key constraint is that the handler callback must be a **plain callable** (not a coroutine), so it schedules an `asyncio.create_task()` that does the actual async cleanup work. The 10-second wait uses `asyncio.wait(tasks, timeout=10)`.

structlog 25.5.0 (installed) has a clean `contextvars` module. Binding `event_id` at message entry and clearing at exit gives automatic correlation through all downstream calls. The JSONRenderer switch via `LOG_FORMAT` env var is a one-liner in `main.py`. The systemd user service is ready to deploy: `loginctl show-user mi` confirms `Linger=yes` is already set, so no manual `loginctl enable-linger` step is needed.

**Primary recommendation:** Implement in wave order: STAB-04 (logging) first since it benefits all subsequent debugging; then STAB-01/STAB-02 (stability); then SESS-04/SESS-05 (session ops); then STAB-03 (service file); finally INTER-01/INTER-02 (buttons, most complex).

---

## Standard Stack

### Core (already installed — no new dependencies needed)

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| structlog | 25.5.0 | Structured logging + contextvars | Already installed; `merge_contextvars` processor + `JSONRenderer` covers all Phase 4 needs |
| asyncio | stdlib | SIGTERM handler, Task cancellation, cleanup Task | `loop.add_signal_handler()`, `asyncio.all_tasks()`, `asyncio.wait()` |
| systemd | 249.11 (OS) | User service management | Already on system; `~/.config/systemd/user/` dir exists |
| lark_oapi | 1.5.3 | Card action callback data structures | `P2CardActionTriggerResponse`, `CallBackToast`, `CallBackAction.value` |

**No new pip installs required for Phase 4.** All needed libraries are in requirements.txt and installed.

**Version verification (confirmed 2026-04-01):**
- structlog 25.5.0 via `pip show structlog`
- lark_oapi 1.5.3 per requirements.txt
- systemd 249 via `systemctl --version`

---

## Architecture Patterns

### Pattern 1: asyncio SIGTERM Handler

The handler MUST be a plain callable — `loop.add_signal_handler()` does not accept coroutines. The standard pattern is to call `loop.create_task()` inside the callback to schedule async cleanup.

```python
# Source: Python docs + live verification (2026-04-01)
import signal
import asyncio

def setup_sigterm_handler(loop: asyncio.AbstractEventLoop, shutdown_event: asyncio.Event) -> None:
    def _on_sigterm():
        logger.info("sigterm_received")
        shutdown_event.set()
        loop.create_task(_shutdown(loop))

    loop.add_signal_handler(signal.SIGTERM, _on_sigterm)
    loop.add_signal_handler(signal.SIGINT, _on_sigterm)  # Ctrl-C too


async def _shutdown(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel all tasks, wait up to 10s, then stop the loop."""
    tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=10)
    loop.stop()
```

**Critical:** `asyncio.all_tasks(loop)` returns ALL tasks including the lark WS client task. Cancellation cascades correctly through `asyncio.wait()` — tasks that handle `CancelledError` and re-raise will finish promptly.

**Where to add:** `main.py` — after `loop = asyncio.get_event_loop()`, before `ws_client.start()`.

**Note on main.py architecture:** `ws_client.start()` is a blocking call on the current thread. The SIGTERM handler's `loop.stop()` will interrupt it. The lark WS client should handle this gracefully since it uses the event loop internally.

### Pattern 2: structlog contextvars for event_id Correlation

```python
# Source: structlog 25.5.0 installed, verified via python3 -c (2026-04-01)
import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

# Processor chain for Phase 4 (replaces ConsoleRenderer in main.py):
import logging

def configure_logging(log_level: str, log_format: str) -> None:
    use_json = log_format.upper() == "JSON"
    processors = [
        structlog.contextvars.merge_contextvars,   # MUST be first
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer() if use_json else structlog.dev.ConsoleRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
    )

# In handle_message() — bind at entry, clear at exit:
async def handle_message(data, ...) -> None:
    event_id = data.header.event_id
    bind_contextvars(event_id=event_id)
    try:
        # ... all downstream calls auto-carry event_id in logs
        pass
    finally:
        clear_contextvars()
```

**Why `merge_contextvars` must be first:** It reads from `contextvars.ContextVar` storage and injects into the log event dict before other processors run. If placed after processors that examine the dict, event_id will be missing.

**asyncio isolation:** Each asyncio Task has its own `contextvars.Context` copy (Python 3.7+ behavior). `bind_contextvars()` in Task A does NOT leak into Task B. This means `clear_contextvars()` in the `finally` block is good practice but does not affect other concurrent Tasks.

### Pattern 3: Session TTL Cleanup Background Task

```python
# Source: existing session.py patterns + asyncio stdlib (verified 2026-04-01)
import asyncio
import time

async def _session_cleanup_loop(
    session_manager: SessionManager,
    ttl_seconds: float,
    interval_seconds: float = 60.0,
) -> None:
    """Background task: scan every interval, destroy idle sessions."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            now = time.time()
            expired = [
                key for key, state in session_manager._sessions.items()
                if now - state.last_activity > ttl_seconds
            ]
            for key in expired:
                await session_manager.destroy(key)
                logger.info("session_ttl_expired", session_key=key)
        except asyncio.CancelledError:
            break  # Shutdown: exit cleanly
        except Exception as exc:
            logger.warning("session_cleanup_error", error=str(exc))
            # Never crash the cleanup loop on non-cancellation errors
```

**Where to start it:** `main.py` after session_manager creation:
```python
cleanup_task = loop.create_task(
    _session_cleanup_loop(session_manager, ttl_seconds=config.session_ttl)
)
```

**Where to add `session_ttl` to Config:** `src/config.py` — add `session_ttl: float = 3600.0` and read from `SESSION_TTL` env var.

### Pattern 4: message_id → Task Registry for Stop Button

The registry is a module-level dict in `claude_worker.py`. The card callback handler imports it.

```python
# src/claude_worker.py (additions)
import asyncio

# Module-level registry: reply_message_id → asyncio.Task
_active_tasks: dict[str, asyncio.Task] = {}


async def single_turn_worker(
    session, prompt, reply_message_id, api_client, semaphore, timeout
) -> None:
    # Register current task
    current_task = asyncio.current_task()
    _active_tasks[reply_message_id] = current_task
    try:
        # ... existing worker code ...
        pass
    finally:
        _active_tasks.pop(reply_message_id, None)  # Always clean up


def cancel_task_for_message(message_id: str) -> bool:
    """Cancel the task for a given message_id. Returns True if found."""
    task = _active_tasks.pop(message_id, None)
    if task and not task.done():
        task.cancel()
        return True
    return False
```

**The card callback handler** (in `src/handler.py`) calls `cancel_task_for_message()` and returns a toast response:

```python
# src/handler.py — expanded on_card_action
from src.claude_worker import cancel_task_for_message

def on_card_action(data) -> "P2CardActionTriggerResponse":
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTriggerResponse, CallBackToast
    )
    action_value = {}
    try:
        action_value = data.event.action.value or {}
    except Exception:
        pass

    action_type = action_value.get("action")
    message_id = action_value.get("message_id")

    resp = P2CardActionTriggerResponse()

    if action_type == "stop" and message_id:
        cancelled = cancel_task_for_message(message_id)
        toast = CallBackToast()
        toast.type = "info"
        toast.content = "已停止" if cancelled else "任务已完成"
        resp.toast = toast

    elif action_type in ("thumbs_up", "thumbs_down"):
        operator_id = None
        try:
            operator_id = data.event.operator.open_id
        except Exception:
            pass
        logger.info(
            "feedback_received",
            feedback=action_type,
            message_id=message_id,
            operator_id=operator_id,
        )
        toast = CallBackToast()
        toast.type = "success"
        toast.content = "感谢反馈！"
        resp.toast = toast

    return resp
```

**CRITICAL:** The card callback handler is sync (Pitfall 6 from Phase 3 — lark SDK calls it synchronously). `cancel_task_for_message()` is sync and thread-safe for asyncio single-threaded use: calling `task.cancel()` on an asyncio.Task is safe from sync code on the same thread. The lark WS callback runs in the same thread as the event loop (via `loop.call_soon_threadsafe()` or direct call), so this is safe.

**3-second window concern (D-41 discretion area):** If the user clicks Stop after the task has already completed (common race condition), `_active_tasks.pop()` returns None, `cancel_task_for_message()` returns False, and the toast says "任务已完成". This is the correct graceful fallback — no error state.

### Pattern 5: CardKit v2 Button Elements for Stop/Feedback

Feishu CardKit v2 uses `actions` array with `tag: "button"` elements. Buttons have an `action` field with `type: "callback"` and a `value` dict that is passed back in `CallBackAction.value`.

```json
// CardKit v2 body element — action button
{
  "tag": "action",
  "actions": [
    {
      "tag": "button",
      "text": {"tag": "plain_text", "content": "停止"},
      "type": "danger",
      "action": {
        "type": "callback",
        "value": {"action": "stop", "message_id": "<reply_message_id>"}
      }
    }
  ]
}
```

For feedback buttons on the final card:
```json
{
  "tag": "action",
  "actions": [
    {
      "tag": "button",
      "text": {"tag": "plain_text", "content": "👍"},
      "type": "default",
      "action": {
        "type": "callback",
        "value": {"action": "thumbs_up", "message_id": "<reply_message_id>"}
      }
    },
    {
      "tag": "button",
      "text": {"tag": "plain_text", "content": "👎"},
      "type": "default",
      "action": {
        "type": "callback",
        "value": {"action": "thumbs_down", "message_id": "<reply_message_id>"}
      }
    }
  ]
}
```

**Confidence:** MEDIUM — CardKit v2 button structure inferred from lark-oapi SDK callback data model (`CallBackAction.value: Dict[str, Any]`) and general Feishu card documentation patterns. The exact JSON field names (`"type": "callback"`, `"action"` wrapper) should be verified against live Feishu card builder or open.feishu.cn at implementation time if buttons don't appear.

**Alternative if "callback" type doesn't work:** Try `"behaviors": [{"type": "callback", "value": {...}}]` — some Feishu card versions use `behaviors` instead of `action`.

### Pattern 6: /help Card

```python
# src/cards.py — add build_help_card()
def build_help_card() -> str:
    card = {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": "AI 助手帮助"},
            "template": "green",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        "**可用命令**\n\n"
                        "- `/new` — 重置会话，开始新对话\n"
                        "- `/help` — 显示此帮助信息\n"
                    ),
                }
            ]
        },
    }
    return json.dumps({"data": card}, ensure_ascii=False)
```

Handled in `handle_message()` in `src/handler.py`, alongside the `/new` check (Step 5).

### Pattern 7: systemd User Service File

```ini
# ~/.config/systemd/user/feishu-bridge.service
[Unit]
Description=Feishu Bridge — Claude Code bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/mi/feishu-bridge
ExecStart=/usr/bin/python3 /home/mi/feishu-bridge/main.py
EnvironmentFile=/home/mi/feishu-bridge/.env
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

**ExecStart path:** `/usr/bin/python3` (confirmed by `which python3`). No virtualenv is present — packages installed to user site-packages (`~/.local/lib/python3.10/site-packages/`).

**EnvironmentFile:** Loads `.env` for `APP_ID`, `APP_SECRET`, `LOG_FORMAT=JSON`, etc. This replaces python-dotenv's `.env` loading for service context (where `load_dotenv()` may not find `.env` relative to CWD).

**Linger:** Already enabled (`loginctl show-user mi` returns `Linger=yes`). No action needed.

**Activation commands:**
```bash
systemctl --user daemon-reload
systemctl --user enable feishu-bridge.service
systemctl --user start feishu-bridge.service
systemctl --user status feishu-bridge.service
journalctl --user -u feishu-bridge.service -f
```

### Anti-Patterns to Avoid

- **Async SIGTERM callback:** `loop.add_signal_handler()` requires a plain callable, not a coroutine. Use `loop.create_task()` inside the callback.
- **Cancelling current_task:** `asyncio.all_tasks()` includes the currently running coroutine. Filter it out with `if t is not asyncio.current_task()`.
- **Missing `clear_contextvars()` in finally:** Without `finally: clear_contextvars()`, if a message handler Task is cancelled between `bind_contextvars()` and the end of processing, the context leaks (though asyncio Task isolation limits the damage).
- **`asyncio.gather()` instead of `asyncio.wait()` for shutdown:** `gather()` raises on first cancellation. `wait()` with `timeout=10` waits for all tasks up to the timeout, which is correct for graceful shutdown.
- **Blocking in card callback handler:** The `on_card_action()` sync callback runs on the WS thread. Never await or block in it. `cancel_task_for_message()` is sync and safe.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| event_id log correlation | Custom logging wrapper or thread-local | `structlog.contextvars.bind_contextvars()` | Built-in isolation per asyncio Task context; zero boilerplate |
| Background cleanup loop | Cron job or external scheduler | `asyncio.create_task()` inside the same event loop | Already on the right loop; no cross-process IPC needed |
| Task cancellation for Stop button | Subprocess kill or flag polling | `asyncio.Task.cancel()` | Raises `CancelledError` in the awaiting coroutine; already handled by claude_worker.py's try/except |
| Log rotation | Custom file rotation code | journald (automatic via `StandardOutput=journal`) | systemd journals rotate automatically; zero configuration |

---

## Common Pitfalls

### Pitfall 1: SIGTERM Handler as Coroutine
**What goes wrong:** `loop.add_signal_handler(signal.SIGTERM, async_fn)` silently fails or raises TypeError. The signal is never handled.
**Why it happens:** The API requires a plain callable. This is different from `loop.run_until_complete()`.
**How to avoid:** Always wrap async cleanup in `loop.create_task(...)` inside a plain `def` callback.
**Warning signs:** SIGTERM sent, process does not exit cleanly; orphaned Claude subprocesses.

### Pitfall 2: Leaking _active_tasks on Task Cancellation
**What goes wrong:** `single_turn_worker` is cancelled mid-execution. The `finally` block is not reached if code doesn't use try/finally.
**Why it happens:** `asyncio.CancelledError` propagates through await points unless caught. Without `finally`, the dict entry stays forever, growing unbounded.
**How to avoid:** Use `try: ... finally: _active_tasks.pop(reply_message_id, None)` in `single_turn_worker`. The existing worker already has this structure — just add the pop.
**Warning signs:** `_active_tasks` dict grows monotonically; memory leak over hours.

### Pitfall 3: contextvars and asyncio.create_task
**What goes wrong:** `bind_contextvars(event_id=...)` in the parent coroutine does NOT propagate to tasks created with `asyncio.create_task()`.
**Why it happens:** `asyncio.create_task()` copies the current context snapshot at creation time (Python 3.7+). This is actually a feature: the child task inherits context values that exist at creation time, but new bindings after `create_task()` don't propagate. The reverse — bindings in the child — don't propagate to the parent.
**Impact for this phase:** `single_turn_worker` is called via `asyncio.create_task()` in `handle_message()`. If `bind_contextvars(event_id=...)` is called BEFORE `create_task()`, the worker task inherits `event_id` automatically. The clear in `handle_message()`'s finally only clears the parent context — the worker's context copy is independent.
**How to avoid:** Call `bind_contextvars(event_id=event_id)` early in `handle_message()`, before `asyncio.create_task()`. The worker gets the binding for free.

### Pitfall 4: Stop Button message_id Mismatch
**What goes wrong:** The Stop button's `value.message_id` contains the wrong message ID, so `cancel_task_for_message()` never finds the task.
**Why it happens:** `reply_message_id` (the ID of the "thinking" card that was replied) is different from the original user `message.message_id`. The button must embed `reply_message_id` (the card's message ID), which is the key used in `_active_tasks`.
**How to avoid:** Pass `reply_message_id` into the card builder function explicitly. Do not use `message.message_id` in the button value.
**Warning signs:** Stop button click returns "任务已完成" toast even when task is clearly running.

### Pitfall 5: EnvironmentFile Parsing in systemd
**What goes wrong:** `.env` file has shell syntax (`export VAR=value`, quoted values, comments). systemd's `EnvironmentFile` does NOT support all shell syntax.
**Why it happens:** systemd `EnvironmentFile` is not a shell — it does not expand `$VAR`, handle `export`, or support multiline values.
**How to avoid:** Use simple `KEY=VALUE` format in `.env`. Remove `export` prefixes. No shell quoting needed for simple values. For values with spaces, use `KEY="value with spaces"` (double quotes supported).
**Warning signs:** Service fails to start with `Status: 203/EXEC` or env vars are empty despite `.env` being correct.

---

## Code Examples

### SIGTERM Handler Registration in main.py
```python
# Source: asyncio stdlib + live test (2026-04-01)
import signal
import asyncio

# After: loop = asyncio.get_event_loop()
# Before: ws_client.start()

_shutdown_requested = False

def _handle_sigterm():
    global _shutdown_requested
    if _shutdown_requested:
        return
    _shutdown_requested = True
    logger.info("sigterm_received_initiating_shutdown")
    loop.create_task(_graceful_shutdown(loop))

loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
loop.add_signal_handler(signal.SIGINT, _handle_sigterm)


async def _graceful_shutdown(loop: asyncio.AbstractEventLoop) -> None:
    tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
    logger.info("cancelling_tasks", count=len(tasks))
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=10)
    logger.info("shutdown_complete")
    loop.stop()
```

### structlog Processor Chain (main.py configure_logging replacement)
```python
# Source: structlog 25.5.0 live verification (2026-04-01)
import logging
import os
import structlog

def configure_structlog(log_level: str = "INFO", log_format: str = "console") -> None:
    use_json = log_format.upper() == "JSON"
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer() if use_json
        else structlog.dev.ConsoleRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
    )
```

### event_id Binding in handle_message
```python
# Source: existing handler.py + structlog.contextvars API (2026-04-01)
from structlog.contextvars import bind_contextvars, clear_contextvars

async def handle_message(data, ...) -> None:
    event_id = data.header.event_id
    bind_contextvars(event_id=event_id)  # BEFORE create_task — child inherits
    try:
        # ... existing pipeline ...
        asyncio.create_task(single_turn_worker(...))  # inherits event_id context
    except Exception as exc:
        logger.error("handle_message_error", error=str(exc), error_type=type(exc).__name__)
    finally:
        clear_contextvars()
```

### session_ttl Config Addition
```python
# src/config.py addition
session_ttl: float = 3600.0  # SESSION_TTL env var

# In from_env():
session_ttl=float(os.environ.get("SESSION_TTL", "3600")),
```

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| `structlog.dev.ConsoleRenderer()` (main.py L79) | `JSONRenderer()` via LOG_FORMAT=JSON | JSON enables log aggregation (journald → grep, future ELK) |
| No task registry | `_active_tasks: dict[str, Task]` | Enables Stop button; also useful for monitoring active tasks count |
| No session TTL | 60s scan, SESSION_TTL=3600 | Prevents ClaudeSDKClient accumulation over days of operation |

**Already handled / no change needed:**
- `asyncio.CancelledError` handling: claude_worker.py's except clause catches it because it is a subclass of `BaseException` (Python 3.8+), not `Exception`. Verify the except clause catches `BaseException` or explicitly catches `CancelledError` — currently catches `Exception` which does NOT catch `CancelledError`. This must be addressed in STAB-02.

**CancelledError gap:** In the current `claude_worker.py`, the outer `except Exception` does NOT catch `asyncio.CancelledError` (which inherits from `BaseException` since Python 3.8). When the Stop button cancels a task, the `CancelledError` will propagate out of `single_turn_worker` without sending an error card. This is actually correct behavior (we don't want an error card on intentional Stop), but the card must be updated to show "已停止" state. The worker should catch `CancelledError` specifically to finalize the card with a "stopped" message before re-raising.

---

## Open Questions

1. **CardKit v2 button JSON format — `action` vs `behaviors`**
   - What we know: `CallBackAction.value` is `Dict[str, Any]`, meaning the SDK receives the value dict we put in the card JSON
   - What's unclear: Whether the button action field is `"action": {"type": "callback", "value": {...}}` or `"behaviors": [{"type": "callback", "value": {...}}]`
   - Recommendation: Implement with `"action": {"type": "callback", "value": {...}}` first. If buttons don't register callbacks in testing, try the `behaviors` array format. Feishu card builder at open.feishu.cn can generate reference JSON.

2. **asyncio.Task.cancel() and claudeSDKClient subprocess cleanup**
   - What we know: Cancelling a task raises CancelledError at the next await point. `ClaudeSDKClient.disconnect()` should be called for cleanup.
   - What's unclear: Whether the claude_worker's cancelled task properly disconnects the ClaudeSDKClient before the CancelledError propagates.
   - Recommendation: In the Stop button path, catch `CancelledError` in `single_turn_worker`, call `manager.finalize("")` and `session.client.disconnect()` before re-raising. Or: handle Stop by setting a flag and letting the worker exit cleanly rather than hard-cancelling.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| systemd --user | STAB-03 | Yes | 249.11 | — |
| loginctl enable-linger | STAB-03 | Already active | Linger=yes | — |
| Python 3.10 | All | Yes | 3.10.12 | — |
| structlog | STAB-04 | Yes | 25.5.0 | — |
| lark_oapi P2CardActionTriggerResponse | INTER-01/02 | Yes | 1.5.3 | — |

**No missing dependencies.** All Phase 4 requirements can be implemented with currently installed software.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8+ with pytest-asyncio |
| Config file | `pyproject.toml` — `asyncio_mode = "auto"`, `testpaths = ["tests"]` |
| Quick run command | `python3 -m pytest tests/ -x -q` |
| Full suite command | `python3 -m pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STAB-01 | Exception in worker does not propagate | unit | `pytest tests/test_claude_worker.py -x -k "test_error"` | Yes (test_claude_worker.py) |
| STAB-02 | SIGTERM triggers task cancellation + loop stop | unit | `pytest tests/test_main.py -x -k "test_sigterm"` | No — Wave 0 |
| STAB-04 | event_id appears in all log lines for a message | unit | `pytest tests/test_handler.py -x -k "test_event_id"` | Partial (test_handler.py exists) |
| SESS-04 | /help command sends green card | unit | `pytest tests/test_handler.py -x -k "test_help"` | No — Wave 0 |
| SESS-05 | Session destroyed after TTL | unit | `pytest tests/test_session.py -x -k "test_ttl"` | No — Wave 0 |
| INTER-01 | Stop button callback cancels task | unit | `pytest tests/test_handler.py -x -k "test_stop"` | No — Wave 0 |
| INTER-02 | Feedback buttons log structlog event | unit | `pytest tests/test_handler.py -x -k "test_feedback"` | No — Wave 0 |
| STAB-03 | Service file syntax valid | manual | `systemd-analyze verify ~/.config/systemd/user/feishu-bridge.service` | No — Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/ -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_main.py` — covers STAB-02 (SIGTERM handler registration and shutdown logic)
- [ ] SESS-04 test cases in `tests/test_handler.py` — `/help` command response
- [ ] SESS-05 test cases in `tests/test_session.py` — TTL cleanup loop
- [ ] INTER-01 test cases in `tests/test_handler.py` — Stop button cancel
- [ ] INTER-02 test cases in `tests/test_handler.py` — Feedback button logging

Existing `tests/test_claude_worker.py` covers STAB-01 partially; needs test for `CancelledError` handling specifically.

---

## Project Constraints (from CLAUDE.md)

| Directive | Impact on Phase 4 |
|-----------|------------------|
| No sudo | systemd `--user` only; `loginctl enable-linger` already set — no action needed |
| Python 3.10+ | All asyncio patterns confirmed working on 3.10.12 |
| No blocking HTTP in event loop | Card callback handler is sync — must not call httpx directly; any async card update from callback must be scheduled via `loop.create_task()` |
| Feishu 3s callback deadline | `on_card_action()` must return within 3 seconds; `cancel_task_for_message()` is O(1) sync — safe |
| CardKit PATCH not wrapped by lark-oapi | Stop button result card update uses existing `send_error_card()` / `update_card_content()` patterns, not a new HTTP path |
| GSD Workflow Enforcement | All changes go through `/gsd:execute-phase` |
| `~/.claude.json` forbidden | Do not touch |

---

## Sources

### Primary (HIGH confidence)
- Python stdlib asyncio docs — `loop.add_signal_handler()`, `asyncio.all_tasks()`, `asyncio.wait()` — verified via live Python 3.10.12 REPL
- structlog 25.5.0 source at `/home/mi/.local/lib/python3.10/site-packages/structlog/contextvars.py` — `bind_contextvars`, `clear_contextvars`, `merge_contextvars` API confirmed
- lark_oapi 1.5.3 source at `/home/mi/.local/lib/python3.10/site-packages/lark_oapi/event/callback/model/p2_card_action_trigger.py` — `CallBackAction.value`, `CallBackToast`, `P2CardActionTriggerResponse` fields confirmed
- systemd 249.11 on target machine — `systemctl --user` available, `~/.config/systemd/user/` exists, Linger=yes confirmed
- Existing codebase — `src/claude_worker.py`, `src/handler.py`, `src/session.py`, `src/cards.py`, `main.py` — all read and understood

### Secondary (MEDIUM confidence)
- CardKit v2 button JSON format — inferred from SDK callback data model; exact `action` vs `behaviors` field name needs live verification

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- SIGTERM handler pattern: HIGH — verified via live Python REPL
- structlog contextvars: HIGH — verified against installed 25.5.0 source
- Card action callback data model: HIGH — SDK source inspected directly
- CardKit v2 button JSON format: MEDIUM — inferred from callback data model; live card builder verification recommended
- systemd user service: HIGH — systemd 249 available, patterns well-documented, linger already set

**Research date:** 2026-04-01
**Valid until:** 2026-05-01 (stable ecosystem; lark-oapi + structlog APIs won't change materially)
