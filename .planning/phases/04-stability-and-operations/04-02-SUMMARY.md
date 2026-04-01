---
phase: 04-stability-and-operations
plan: "02"
subsystem: claude-worker,handler,cards
tags: [stop-button, feedback, task-registry, cancelled-error, card-callback]
dependency_graph:
  requires: [03-02]
  provides: [_active_tasks registry, cancel_task_for_message, CancelledError handling, stop button, feedback buttons, card callback expansion]
  affects: [src/claude_worker.py, src/handler.py, src/cards.py]
tech_stack:
  added: []
  patterns: [asyncio task registry, sync callback with task cancellation, CardKit v2 action elements]
key_files:
  created: []
  modified:
    - src/claude_worker.py
    - src/handler.py
    - src/cards.py
    - tests/test_claude_worker.py
    - tests/test_handler.py
decisions:
  - "CancelledError handler placed between TimeoutError and Exception to ensure correct exception precedence"
  - "Stop button included in streaming card initial template via stop_message_id parameter"
  - "Feedback buttons added as optional buttons= parameter to update_card_content"
  - "cancel_task_for_message() is sync O(1) dict pop — safe within 3-second callback window"
metrics:
  duration: "~15 min"
  completed_date: "2026-04-01"
  tasks_completed: 2
  files_modified: 5
---

# Phase 4 Plan 02: Stop button, feedback buttons, task registry, and exception hardening Summary

Task registry with programmatic cancellation, CancelledError hardening in worker, and full card callback with stop + feedback button logic.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Task registry + CancelledError handling + button helpers | c003675 | src/claude_worker.py, src/cards.py, tests/test_claude_worker.py |
| 2 | Card callback expansion + wire buttons into worker | 47765f9 | src/handler.py, tests/test_handler.py |

## What Was Built

### Task Registry (src/claude_worker.py)

- `_active_tasks: dict[str, asyncio.Task]` — module-level registry mapping reply_message_id to running Task
- `cancel_task_for_message(message_id: str) -> bool` — sync function, O(1) dict pop + task.cancel()
- Worker registers itself at function entry, deregisters in `finally` block (no memory leak)

### CancelledError Handling (src/claude_worker.py)

- New `except asyncio.CancelledError` clause placed between `TimeoutError` and `Exception`
- On cancellation: finalizes CardStreamingManager with empty string, patches IM card with "**已停止** - 用户取消了请求", then re-raises so asyncio marks task as cancelled
- CancelledError is a `BaseException` in Python 3.8+ — placing BEFORE `except Exception` is critical

### Button Helpers (src/cards.py)

- `build_stop_button(reply_message_id)` — CardKit v2 action element with danger-type Stop button, action_type=callback
- `build_feedback_buttons(reply_message_id)` — CardKit v2 action element with thumbs_up/thumbs_down buttons
- `_build_card_with_buttons(header_template, body_text, buttons)` — card builder with action element appended to body
- `update_card_content()` gains optional `buttons: dict | None = None` parameter
- `create_streaming_card()` gains optional `stop_message_id: str | None = None` — adds stop button to initial streaming card

### Card Callback Expansion (src/handler.py)

- `create_card_action_handler()` now handles three paths: stop, thumbs_up/thumbs_down, unknown
- Stop: calls `cancel_task_for_message(message_id)`, returns "已停止" or "任务已完成" toast
- Feedback: logs `feedback_received` structlog event with feedback type, message_id, operator_id; returns "感谢反馈！" toast
- Unknown: logs `unknown_card_action` debug event
- All logic is sync — compliant with 3-second Feishu callback window (Pitfall 6)

## Success Criteria Verification

- [x] Stop button callback cancels the corresponding asyncio Task and returns "已停止" toast
- [x] Feedback button callback logs structlog event with feedback type, message_id, and operator_id
- [x] CancelledError in worker updates card with "已停止" instead of error card
- [x] Task registry cleans up in finally block — no memory leak on cancelled tasks
- [x] All 135 existing tests pass

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_worker_calls_update_card_on_success to match new signature**
- **Found during:** Task 1 test run
- **Issue:** Existing test called `mock_update.assert_awaited_once_with(api_client, "msg_reply_001", "Hello from Claude")` but new code passes `buttons=feedback_buttons` as kwarg
- **Fix:** Updated assertion to check positional args individually and allow buttons kwarg
- **Files modified:** tests/test_claude_worker.py
- **Commit:** c003675

**2. [Rule 1 - Bug] Updated test_streaming_worker_calls_create_streaming_card to match new signature**
- **Found during:** Task 1 test run
- **Issue:** Test checked `mock_create.assert_awaited_once_with(api_client)` but new code passes `stop_message_id=reply_message_id`
- **Fix:** Changed to `assert_awaited_once()` + verify positional arg
- **Files modified:** tests/test_claude_worker.py
- **Commit:** c003675

**3. [Rule 1 - Bug] Updated test_on_card_action_logs_card_action_received for new behavior**
- **Found during:** Task 2 test run
- **Issue:** Old test expected `logger.info("card_action_received", ...)` but new handler logs unknown actions via `logger.debug("unknown_card_action", ...)`
- **Fix:** Renamed test to `test_on_card_action_logs_unknown_card_action`, updated assertion
- **Files modified:** tests/test_handler.py
- **Commit:** 47765f9

## Known Stubs

None — all features are fully wired. Stop button appears in streaming card initial template. Feedback buttons appear on final card. Card callback handles both stop and feedback actions.

## Self-Check: PASSED

- src/claude_worker.py: FOUND
- src/cards.py: FOUND
- src/handler.py: FOUND
- .planning/phases/04-stability-and-operations/04-02-SUMMARY.md: FOUND
- Commit c003675: FOUND
- Commit 47765f9: FOUND
