# Deferred Items — Phase 03 Streaming Card Renderer

## Pre-existing Test Failures (Out of Scope for 03-01)

Discovered during 03-01 execution. These failures existed in the codebase before this plan was executed (confirmed via git stash).

**File:** `tests/test_claude_worker.py`

### 1. TestRunClaudeTurnStreaming::test_calls_append_text_for_each_text_block

**Issue:** `_run_claude_turn_streaming` in `claude_worker.py` currently only processes `StreamEvent` for text, not `TextBlock` in `AssistantMessage`. The test mocks `AssistantMessage` with `TextBlock` objects.

**Fix needed:** Handle `TextBlock` within `AssistantMessage.content` in `_run_claude_turn_streaming`.

### 2. TestRunClaudeTurnStreaming::test_returns_concatenated_text

**Issue:** Same root cause as above — text blocks not processed from `AssistantMessage`.

### 3. TestSingleTurnWorker::test_worker_calls_update_card_on_success

**Issue:** The worker now uses streaming path for all responses. Test expects `update_card_content` to be called for the non-streaming fallback.

### 4-9. TestStreamingWorkerCardManager::*

**Issue:** Tests expect `single_turn_worker` to internally call `create_streaming_card` and `patch_im_with_card_id`. Current implementation receives `card_id` from the caller (handler.py) and skips these calls when card_id is already provided.

**Fix needed:** Either rewrite `claude_worker.py` to call `create_streaming_card` internally, or update the tests to match the current interface.

**Note:** This is likely addressed by Plan 03-02 (`claude_worker.py` is listed in its `files_modified`).
