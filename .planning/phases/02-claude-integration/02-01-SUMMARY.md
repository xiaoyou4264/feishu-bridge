---
phase: 02-claude-integration
plan: "01"
subsystem: config
tags: [claude-agent-sdk, pydantic, lark-oapi, cards, config, tdd]

# Dependency graph
requires:
  - phase: 01-feishu-connectivity
    provides: Config base class (app_id, app_secret, log_level, working_dir), cards.py with send_thinking_card and send_unsupported_type_card, lark-oapi 1.5.3 integration
provides:
  - Config extended with claude_timeout (float, default 120.0), max_concurrent_tasks (int, default 5), allowed_tools (list[str], default [])
  - update_card_content() — patches existing card with Claude response text (blue header)
  - send_error_card() — patches existing card with error message (red header, best-effort)
  - claude-agent-sdk==0.1.53 in requirements.txt
  - CardKit v2 format (schema=2.0, data wrapper) for all card functions
affects: [02-02-session-worker, 02-03-handler-wiring, 03-streaming-cards]

# Tech tracking
tech-stack:
  added: [claude-agent-sdk==0.1.53]
  patterns:
    - "_build_card() helper extracts card JSON building into reusable function"
    - "CardKit v2 format: {data: {schema: 2.0, header: {...}, body: {elements: [...]}}} for all interactive cards"
    - "apatch used for card updates (in-place); areply used for new card replies"
    - "send_error_card is best-effort (logs warning, no raise); update_card_content raises RuntimeError on failure"
    - "load_dotenv mocked in tests to prevent .env file from overriding monkeypatched env vars"

key-files:
  created: []
  modified:
    - src/config.py
    - src/cards.py
    - tests/test_config.py
    - tests/test_cards.py
    - requirements.txt
    - .env.example

key-decisions:
  - "Use lark.im.v1.PatchMessageRequest + apatch (not BaseRequest) — apatch confirmed present in lark-oapi 1.5.3"
  - "CardKit v2 format {data: {schema: 2.0, ...}} applied to ALL card functions (not just new ones) for consistency"
  - "send_error_card does not raise on failure — best-effort pattern matching send_unsupported_type_card"
  - "Mock load_dotenv in config tests — prevents .env file from re-populating env vars deleted via monkeypatch"

patterns-established:
  - "TDD with RED/GREEN commits per task"
  - "_build_card(header_template, body_text) -> str: central card JSON builder for all card functions"
  - "Config.from_env() pattern: load_dotenv() first, then read env vars with defaults"

requirements-completed: [CLAUDE-06, CONC-02, CLAUDE-05]

# Metrics
duration: 10min
completed: 2026-04-01
---

# Phase 2 Plan 01: Claude Integration Config and Card Functions Summary

**Extended Config with claude_timeout/max_concurrent_tasks/allowed_tools fields, added update_card_content() and send_error_card() using lark apatch API with CardKit v2 format, installed claude-agent-sdk==0.1.53**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-04-01T21:44:00Z
- **Completed:** 2026-04-01T21:54:00Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Config model extended with 3 new Claude-specific fields, all reading from env vars with correct defaults
- update_card_content() and send_error_card() implemented using lark-oapi apatch API
- All card functions unified to CardKit v2 format (schema=2.0 with data wrapper)
- claude-agent-sdk==0.1.53 added to requirements.txt and installed
- 27 tests total pass (12 config + 15 cards), including 13 new tests

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend Config and requirements.txt** - `5c621dc` (feat)
2. **Task 2: Card update and error functions** - `719190c` (feat)

## Files Created/Modified
- `src/config.py` - Added claude_timeout, max_concurrent_tasks, allowed_tools fields to Config
- `src/cards.py` - Added _build_card() helper, update_card_content(), send_error_card(); fixed card format to CardKit v2
- `tests/test_config.py` - Added TestConfigClaudeFields (6 tests); fixed load_dotenv mock in existing tests
- `tests/test_cards.py` - Added TestUpdateCardContent (2 tests) and TestSendErrorCard (3 tests)
- `requirements.txt` - Added claude-agent-sdk==0.1.53
- `.env.example` - Documented CLAUDE_TIMEOUT, MAX_CONCURRENT_TASKS, ALLOWED_TOOLS

## Decisions Made
- Used `lark.im.v1.PatchMessageRequest` + `apatch` (not BaseRequest) after confirming `apatch` exists in lark-oapi 1.5.3
- Applied CardKit v2 format `{"data": {"schema": "2.0", ...}}` to ALL card functions for consistency — pre-existing code was using flat format but tests expected v2
- `send_error_card` does not raise on failure — best-effort pattern matching `send_unsupported_type_card` behavior
- Mock `load_dotenv` in config tests — `load_dotenv()` inside `from_env()` was loading the real `.env` file and repopulating env vars removed by `monkeypatch.delenv()`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed pre-existing test isolation bug in TestConfigFromEnv**
- **Found during:** Task 1 (Extend Config and requirements.txt)
- **Issue:** `load_dotenv()` inside `Config.from_env()` reads the real `.env` file, re-populating `APP_ID`/`APP_SECRET` even after `monkeypatch.delenv()`. Tests `test_from_env_raises_on_missing_app_id` and `test_from_env_raises_on_missing_app_secret` were failing.
- **Fix:** Added `with patch("src.config.load_dotenv"):` to all 6 existing `TestConfigFromEnv` tests so the real `.env` file is not loaded during testing.
- **Files modified:** tests/test_config.py
- **Verification:** All 6 pre-existing config tests pass with the mock
- **Committed in:** `5c621dc` (Task 1 commit)

**2. [Rule 1 - Bug] Fixed card JSON format to CardKit v2 across all card functions**
- **Found during:** Task 2 (Card update and error functions)
- **Issue:** Pre-existing `send_thinking_card` and `send_unsupported_type_card` used flat card format `{"header": ..., "elements": [...]}` but existing tests expected CardKit v2 format `{"data": {"schema": "2.0", "header": ..., "body": {...}}}`. 7 pre-existing tests were failing.
- **Fix:** Updated all card functions to use the v2 format via new `_build_card()` helper. Updated `THINKING_CARD_TEMPLATE` constant to v2 format.
- **Files modified:** src/cards.py
- **Verification:** All 15 card tests pass including the 7 pre-existing ones
- **Committed in:** `719190c` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2x Rule 1 - Bug)
**Impact on plan:** Both fixes corrected pre-existing test/implementation mismatches. No scope creep.

## Issues Encountered
None beyond the two auto-fixed deviations above.

## Next Phase Readiness
- Config fields (claude_timeout, max_concurrent_tasks, allowed_tools) ready for Plan 02 session/worker implementation
- Card functions (update_card_content, send_error_card) ready for Plan 03 handler wiring
- claude-agent-sdk installed and available for import

---
*Phase: 02-claude-integration*
*Completed: 2026-04-01*
