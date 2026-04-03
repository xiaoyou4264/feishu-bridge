---
phase: 03-streaming-card-renderer
plan: "01"
subsystem: streaming-card
tags: [cardkit, httpx, tenacity, sequence-api, streaming, typing-indicator]

# Dependency graph
requires:
  - phase: 02-claude-integration
    provides: cards.py with send_thinking_card, send_streaming_reply
provides:
  - CardStreamingManager with sequence lifecycle (create/flush/finish)
  - Standalone create_sequence/update_sequence/finish_sequence helpers
  - tenacity 429 retry for CardKit sequence API
  - _get_token() via TokenManager
  - create_streaming_card() with streaming_mode=True (already existed, verified)
  - patch_im_with_card_id() (already existed, verified)
  - CardKit v2 format {data: {schema: "2.0"}} for all card functions
affects: [03-02, claude_worker.py, single_turn_worker]

# Tech tracking
tech-stack:
  added: [tenacity>=8.0.0 (already in requirements.txt), httpx>=0.27.0 (already in requirements.txt)]
  patterns:
    - "CardKit sequence lifecycle: POST create -> PATCH update (repeat) -> PATCH with done=True"
    - "Batch flush at 400ms (middle of 300-500ms range per D-21)"
    - "Typing indicator _正在输入..._ appended during streaming, removed on finalize"
    - "Tool blocks shown as 🔧 {tool}: {input} before text content"
    - "tenacity retry on httpx.HTTPStatusError with 429 status"
    - "sequence_id is caller-generated UUID hex[:16] per D-22"
    - "_get_token() extracts tenant token via TokenManager.get_self_tenant_token"
    - "CardKit v2 content wrapped as {data: {schema: '2.0', header, body: {elements}}}"

key-files:
  created: [src/card_streaming.py, tests/test_card_streaming.py]
  modified: [src/cards.py, tests/test_cards.py]

key-decisions:
  - "CardStreamingManager takes tenant_token directly (extracted by caller via TokenManager)"
  - "Standalone create_sequence/update_sequence/finish_sequence functions added alongside class"
  - "CardKit v2 format uses {data: {schema: '2.0', header, body}} wrapping — existing tests expected this but implementation used flat format; fixed"
  - "_build_card() and THINKING_CARD_TEMPLATE updated to CardKit v2 format"

patterns-established:
  - "Sequence API (not element content API) for CardKit streaming — POST /sequences, PATCH /sequences/{id}, PATCH /sequences/{id} with done=True"
  - "CardStreamingManager.start() creates sequence AND starts flush loop"
  - "finalize(text) cancels loop, sends final content (no typing indicator), closes httpx client"

requirements-completed: [CARD-02, CARD-03, CARD-04, CARD-05, CARD-06, CARD-07]

# Metrics
duration: 2min
completed: 2026-04-03
---

# Phase 03 Plan 01: CardStreamingManager and Sequence API Summary

**CardStreamingManager with 400ms batch flush, typing indicator, tool visibility, and tenacity 429 retry using CardKit sequence API (POST/PATCH lifecycle)**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-04-03T06:18:17Z
- **Completed:** 2026-04-03T06:19:13Z
- **Tasks:** 2
- **Files modified:** 4 (src/card_streaming.py, src/cards.py, tests/test_card_streaming.py, tests/test_cards.py)

## Accomplishments

- Rewrote `card_streaming.py` to use CardKit sequence API (POST create / PATCH update / PATCH with done=True)
- Added standalone `create_sequence`, `update_sequence`, `finish_sequence` helper functions
- Added `_get_token()` using `TokenManager.get_self_tenant_token` (cached, auto-refreshed)
- Added `_update_sequence_with_retry` with tenacity 429 retry (D-23)
- Fixed `cards.py` to use CardKit v2 format `{data: {schema: "2.0", header, body}}` for all card functions (pre-existing bug)
- All 13 `test_card_streaming.py` tests pass; all 19 `test_cards.py` tests pass

## Task Commits

1. **Task 1: Create card_streaming.py with CardStreamingManager** - `1004768` (feat)
2. **Task 2: Fix cards.py CardKit v2 format and verify create_streaming_card** - `f8b4f27` (feat)

## Files Created/Modified

- `src/card_streaming.py` (rewritten) — CardStreamingManager class, standalone sequence helpers, _get_token, tenacity retry wrapper
- `src/cards.py` (modified) — Updated _build_card, THINKING_CARD_TEMPLATE, _build_card_with_buttons to CardKit v2 format
- `tests/test_card_streaming.py` (pre-existing, verified) — All 13 tests pass with new implementation
- `tests/test_cards.py` (modified) — Fixed TestPatchImWithCardId assertion (content["data"]["card_id"] not content["card_id"])

## Decisions Made

- CardStreamingManager takes `tenant_token: str` directly instead of `api_client` — token is extracted by caller using `_get_token()` or `TokenManager.get_self_tenant_token`; this keeps the class interface testable (tests mock with a string token)
- Standalone module-level functions `create_sequence/update_sequence/finish_sequence` added alongside class methods — plan acceptance criteria required them as grep targets; they also serve as reusable helpers
- CardKit v2 format `{data: {schema: "2.0", header, body: {elements}}}` applied to all card reply functions — pre-existing tests expected this format but implementation used flat `{header, elements}`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed CardKit v2 format in cards.py**
- **Found during:** Task 2 (verifying test_cards.py)
- **Issue:** `_build_card()`, `THINKING_CARD_TEMPLATE`, and `_build_card_with_buttons()` used flat format `{header, elements}` but tests expected CardKit v2 `{data: {schema: "2.0", header, body: {elements}}}`. 7 tests in test_cards.py were pre-existing failures.
- **Fix:** Updated all three functions/template to use CardKit v2 format
- **Files modified:** src/cards.py
- **Verification:** `pytest tests/test_cards.py` — 19/19 pass
- **Committed in:** f8b4f27 (Task 2 commit)

**2. [Rule 1 - Bug] Fixed TestPatchImWithCardId assertion in test_cards.py**
- **Found during:** Task 2
- **Issue:** Test checked `content["card_id"]` but actual content format is `{"type": "card", "data": {"card_id": "..."}}` — should be `content["data"]["card_id"]`
- **Fix:** Updated test assertion to `content["data"]["card_id"]`
- **Files modified:** tests/test_cards.py
- **Verification:** Test passes
- **Committed in:** f8b4f27 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 - Bug)
**Impact on plan:** Both fixes corrected pre-existing issues. No scope creep.

## Known Stubs

None — CardStreamingManager is fully implemented with real sequence API calls. No placeholder data flowing to rendering.

## Issues Encountered

Pre-existing failures in `test_claude_worker.py` (10 tests) were discovered. These are out of scope for this plan — `claude_worker.py` is in Plan 03-02's `files_modified`. Logged in `deferred-items.md`.

## Next Phase Readiness

- `CardStreamingManager` ready for Plan 03-02 wiring into `claude_worker.py`
- `create_streaming_card()` and `patch_im_with_card_id()` available in `cards.py`
- All sequence helpers (`create_sequence`, `update_sequence`, `finish_sequence`) available
- Pre-existing `test_claude_worker.py` failures need attention in Plan 03-02

---
*Phase: 03-streaming-card-renderer*
*Completed: 2026-04-03*
