---
phase: 03-streaming-card-renderer
plan: "02"
subsystem: claude_worker + handler + main
tags: [streaming, cardkit, card-action, claude-sdk, inter-03]
dependency_graph:
  requires:
    - 03-01 (CardStreamingManager, create_streaming_card, patch_im_with_card_id)
    - 02-03 (SessionState, single_turn_worker baseline)
  provides:
    - _run_claude_turn_streaming() in src/claude_worker.py
    - create_card_action_handler() in src/handler.py
    - register_p2_card_action_trigger in main.py
  affects:
    - 04-* (Phase 4 button interactions use the registered callback infrastructure)
tech_stack:
  added: []
  patterns:
    - CardStreamingManager.start() + finalize(text) lifecycle (async)
    - Sync card action handler returning P2CardActionTriggerResponse
    - P2CardActionTriggerResponse from lark_oapi.event.callback.model.p2_card_action_trigger
    - TokenManager.get_self_tenant_token() for httpx auth in streaming worker
key_files:
  created: []
  modified:
    - src/claude_worker.py
    - src/handler.py
    - main.py
    - tests/test_claude_worker.py
    - tests/test_handler.py
decisions:
  - "P2CardActionTriggerResponse is the correct class (not CardActionTriggerResponse) — found at lark_oapi.event.callback.model.p2_card_action_trigger"
  - "CardStreamingManager.start() is async (not start_flush_loop()) — Plan 01 built async API, Plan 02 adapted accordingly"
  - "append_text/append_tool_use/append_tool_result are all async in CardStreamingManager — worker awaits them"
  - "single_turn_worker still calls update_card_content() after finalize() as IM fallback for response visibility"
  - "TokenManager.get_self_tenant_token() called inline in single_turn_worker before CardStreamingManager construction"
metrics:
  duration: "8 minutes"
  completed_date: "2026-04-01"
  tasks: 2
  files_modified: 5
---

# Phase 03 Plan 02: Wire Streaming into claude_worker + Card Callback Registration Summary

Streaming connected end-to-end: _run_claude_turn_streaming() feeds TextBlock/ToolUseBlock/ToolResultBlock callbacks into CardStreamingManager, single_turn_worker creates and finalizes the CardKit sequence per response, and card.action.trigger callback infrastructure registered in EventDispatcherHandler.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Refactor claude_worker.py for streaming with CardStreamingManager | 9458bc9 | src/claude_worker.py, tests/test_claude_worker.py |
| 2 | Register card.action.trigger callback in handler.py and main.py | e95e197 | src/handler.py, main.py, tests/test_handler.py |

## What Was Built

### Task 1: Streaming Claude Worker

Added `_run_claude_turn_streaming()` to `src/claude_worker.py`:
- Calls `client.query(prompt)` then iterates `receive_response()`
- For each `AssistantMessage`, dispatches blocks:
  - `TextBlock` → `await manager.append_text(block.text)`
  - `ToolUseBlock` → `await manager.append_tool_use(block.name, block.input)`
  - `ToolResultBlock` → `await manager.append_tool_result(content, is_error)`
- Returns concatenated text from all TextBlocks

Refactored `single_turn_worker()` flow:
1. `create_streaming_card(api_client)` → `card_id`
2. `patch_im_with_card_id(api_client, reply_message_id, card_id)` — links CardKit card to IM message
3. `TokenManager.get_self_tenant_token(api_client._config)` → tenant token
4. `CardStreamingManager(card_id, tenant_token)` + `await manager.start()` — create sequence + flush loop
5. `asyncio.wait_for(_run_claude_turn_streaming(...), timeout=timeout)` — Claude with streaming
6. `await manager.finalize(result_text)` — remove typing indicator, finish sequence
7. `await update_card_content(...)` — IM card fallback update
8. Exception / timeout paths: `await manager.finalize("")` always called (Pitfall 4 compliance)

### Task 2: Card Callback Infrastructure

Added `create_card_action_handler()` factory to `src/handler.py`:
- Returns a sync callable (per Pitfall 6: lark SDK calls handlers synchronously)
- Logs `card_action_received` with `action_tag` and `operator_id`
- Returns `P2CardActionTriggerResponse()` (Phase 3 infrastructure only, D-29)
- Handles missing `action`/`operator` attributes gracefully

Updated `main.py`:
- Imports `create_card_action_handler` from `src.handler`
- Creates `on_card_action = create_card_action_handler()`
- Adds `.register_p2_card_action_trigger(on_card_action)` to EventDispatcherHandler builder chain (INTER-03)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Adapted CardStreamingManager interface to actual Plan 01 implementation**
- **Found during:** Task 1 implementation
- **Issue:** Plan 02 interface spec described `manager.append_text(text)` as sync and `start_flush_loop()` as the start method, but Plan 01 actually built `append_text(text)` as async and `start()` as the start method with different tool block signatures `append_tool_use(name, input)` and `append_tool_result(content, is_error)`
- **Fix:** Implemented claude_worker to match the actual Plan 01 API: awaiting all manager methods, using `start()` instead of `start_flush_loop()`, passing block.name and block.input separately
- **Files modified:** src/claude_worker.py
- **Commit:** 9458bc9

**2. [Rule 1 - Bug] Corrected CardActionTriggerResponse import path**
- **Found during:** Task 2 test execution
- **Issue:** Plan 02 specified `from lark_oapi.card.model.card_action_trigger_response import CardActionTriggerResponse` but this module does not exist in lark-oapi 1.5.3
- **Fix:** Used `P2CardActionTriggerResponse` from `lark_oapi.event.callback.model.p2_card_action_trigger` — the actual class that exists in the installed package
- **Files modified:** src/handler.py, tests/test_handler.py
- **Commit:** e95e197

**3. [Rule 2 - Missing] Added update_card_content() call after finalize()**
- **Found during:** Task 1 review
- **Issue:** The IM message shows a CardKit card, but if the sequence API fails at runtime the IM card might not update. Adding update_card_content() as a fallback ensures the text appears in IM even if CardKit sequence has issues.
- **Fix:** Added `await update_card_content(api_client, reply_message_id, result_text)` after `manager.finalize()`
- **Files modified:** src/claude_worker.py
- **Commit:** 9458bc9

## Test Results

- Total tests: 135 (all passing)
- New tests added: 19 (13 in TestRunClaudeTurnStreaming + TestStreamingWorkerCardManager, 6 in TestCardActionHandler)
- Existing tests preserved: all 116 pre-existing tests continue to pass

## Known Stubs

None — all code paths are wired. The card action handler returns an empty response (D-29) which is intentional for Phase 3 infrastructure only; Phase 4 will add button logic.

## Self-Check: PASSED

All source files exist: src/claude_worker.py, src/handler.py, main.py, tests/test_claude_worker.py, tests/test_handler.py
All commits exist: 9458bc9 (Task 1), e95e197 (Task 2)
Full test suite: 135 passed, 0 failed
