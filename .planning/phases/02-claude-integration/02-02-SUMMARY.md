---
phase: 02-claude-integration
plan: "02"
subsystem: session-management
tags: [claude-agent-sdk, asyncio, session, concurrency, semaphore]

requires:
  - phase: 02-01
    provides: "Config with claude_timeout/max_concurrent_tasks/allowed_tools; update_card_content and send_error_card in src/cards.py"
  - phase: 01-feishu-connectivity
    provides: "lark.Client API client, message pipeline, handler structure"

provides:
  - "SessionState dataclass (session_key, client, lock, name_cache, last_activity)"
  - "SessionManager class (get_or_create, destroy, semaphore property)"
  - "get_session_key() — P2P -> open_id, group -> chat_id"
  - "get_display_name() — contact API with cache and open_id[-8:] fallback"
  - "format_prompt() — group gets [name]: prefix, P2P gets no prefix"
  - "single_turn_worker() — semaphore OUTER, lock INNER, wait_for timeout, error card on failure"
  - "_run_claude_turn() — query + receive_response drain, returns concatenated text"

affects: [02-03, handler-wiring, session-lifecycle, concurrency]

tech-stack:
  added: []
  patterns:
    - "Per-session ClaudeSDKClient with manual connect()/disconnect() for session lifetime"
    - "Semaphore OUTER, per-session Lock INNER — deadlock prevention (Pitfall 4)"
    - "asyncio.wait_for for timeout enforcement (CLAUDE_TIMEOUT)"
    - "Exception isolation: single_turn_worker never propagates exceptions"
    - "Display name cache dict per SessionState — avoids repeated contact API calls"
    - "open_id[-8:] fallback when contact API permission not granted"
    - "TDD: RED (failing test) commit -> GREEN (implementation) commit per task"

key-files:
  created:
    - src/session.py
    - src/claude_worker.py
    - tests/test_session.py
    - tests/test_claude_worker.py
  modified: []

key-decisions:
  - "Manual connect()/disconnect() on ClaudeSDKClient (not async with) — session lifetime spans multiple handler calls, context manager would close after each query"
  - "Per-session asyncio.Lock in SessionState — serializes concurrent query() calls within one session; ClaudeSDKClient is not concurrent-safe"
  - "semaphore OUTER, session.lock INNER — reverse order risks deadlock (Task A holds lock + waits semaphore; Task B holds semaphore + needs lock for same session)"
  - "destroy() wraps disconnect() in try/except — cleanup errors are tolerated, session is always removed from dict"
  - "single_turn_worker catches Exception and wraps send_error_card in try/except — card send failures must not re-raise"

patterns-established:
  - "Pattern: SessionManager owns ClaudeSDKClient lifecycle; workers only call query() under lock"
  - "Pattern: get_display_name() result always cached (including fallback) — no repeated API calls on failure"

requirements-completed: [CLAUDE-01, CLAUDE-02, CLAUDE-03, CLAUDE-04, CLAUDE-05, SESS-01, SESS-02, SESS-03, CONC-01, CONC-03]

duration: 4min
completed: 2026-04-01
---

# Phase 2 Plan 02: Session Manager and Claude Worker Summary

**Per-session ClaudeSDKClient lifecycle management with semaphore+lock concurrency control and asyncio.wait_for timeout isolation**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-01T13:55:04Z
- **Completed:** 2026-04-01T13:59:12Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- SessionManager creates and reuses one ClaudeSDKClient per session (P2P keyed by open_id, group by chat_id), preserving multi-turn context (CLAUDE-03)
- Display name fetched from Feishu contact API with per-session cache and open_id[-8:] fallback when permission not granted (Pitfall 6)
- single_turn_worker runs Claude turns with semaphore OUTER + lock INNER ordering (deadlock prevention), asyncio.wait_for timeout, and full exception isolation
- 32 unit tests across test_session.py (20 tests) and test_claude_worker.py (12 tests), all passing; full suite 87/87 green

## Task Commits

Each task was committed atomically via TDD (RED then GREEN):

1. **Task 1 RED: SessionManager tests** - `d4d8aff` (test)
2. **Task 1 GREEN: SessionManager implementation** - `c13613d` (feat)
3. **Task 2 RED: Claude worker tests** - `a470c2e` (test)
4. **Task 2 GREEN: Claude worker implementation** - `e93e2f4` (feat)

**Plan metadata:** (docs: complete plan — see final commit)

_Note: TDD tasks have two commits each (test -> feat)_

## Files Created/Modified

- `src/session.py` (207 lines) — SessionState dataclass, SessionManager class, get_session_key(), get_display_name(), format_prompt()
- `src/claude_worker.py` (118 lines) — single_turn_worker() coroutine, _run_claude_turn() helper
- `tests/test_session.py` (314 lines) — 20 unit tests for session management
- `tests/test_claude_worker.py` (371 lines) — 12 unit tests for Claude worker

## Decisions Made

- **Manual connect()/disconnect() vs async with:** Session lifetime spans multiple handler invocations (multi-turn), so context manager is wrong — it would close the SDK client after one query. Manual lifecycle management required.
- **Semaphore OUTER, lock INNER:** Acquiring lock before semaphore would create circular wait deadlock if a task holds the lock and blocks on the full semaphore while another task holds the semaphore and needs the same session lock.
- **destroy() try/except around disconnect():** Cleanup failures (e.g., subprocess already dead) should never prevent the session from being removed. Session dict is always cleaned up.
- **Single_turn_worker double-wraps send_error_card:** If the error card send itself fails (e.g., rate limit), it must not re-raise — the worker is an isolated asyncio.Task and must never crash the event loop.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None — implementation followed the plan's architecture patterns directly.

## Known Stubs

None — no placeholder data or unimplemented paths. All code paths are fully wired:
- SessionManager creates real ClaudeSDKClient instances (mocked only in tests)
- get_display_name() has real API call + real fallback path
- single_turn_worker() calls real update_card_content() / send_error_card() from src/cards.py

## Next Phase Readiness

- Plan 03 can wire SessionManager + single_turn_worker into the existing handler.py pipeline
- handler.py Step 5 currently just sends thinking card; Plan 03 adds: session key lookup, display name fetch, format_prompt(), asyncio.create_task(single_turn_worker(...))
- Blocker confirmed deferred: contact API permission (contact:user.base:readonly) still needs verification in live Feishu app console before group chat testing

## Self-Check: PASSED

All created files confirmed present:
- src/session.py — FOUND
- src/claude_worker.py — FOUND
- tests/test_session.py — FOUND
- tests/test_claude_worker.py — FOUND
- .planning/phases/02-claude-integration/02-02-SUMMARY.md — FOUND

All task commits confirmed in git log:
- d4d8aff (test: session RED) — FOUND
- c13613d (feat: session GREEN) — FOUND
- a470c2e (test: worker RED) — FOUND
- e93e2f4 (feat: worker GREEN) — FOUND

---
*Phase: 02-claude-integration*
*Completed: 2026-04-01*
