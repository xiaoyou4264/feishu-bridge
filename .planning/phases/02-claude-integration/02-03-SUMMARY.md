---
phase: 02-claude-integration
plan: "03"
subsystem: handler-integration
tags: [handler, session-manager, claude-dispatch, /new-command, asyncio]
dependency_graph:
  requires: [02-01, 02-02]
  provides: [end-to-end message flow, Claude dispatch, /new command]
  affects: [main.py, src/handler.py]
tech_stack:
  added: []
  patterns: [asyncio.create_task for fire-and-forget, /new command session reset, group prompt prefix injection]
key_files:
  created: []
  modified:
    - src/handler.py
    - main.py
    - tests/test_handler.py
decisions:
  - "/new command handled BEFORE thinking card — avoids sending thinking card for resets"
  - "asyncio.create_task fires-and-forgets worker — handler returns immediately, never blocks WS loop"
  - "CardKit v2 format used for /new confirmation card — consistent with rest of cards.py"
metrics:
  duration: 3min
  completed_date: "2026-04-01"
  tasks_completed: 2
  files_modified: 3
---

# Phase 2 Plan 03: Handler-Claude Integration Summary

**One-liner:** Wired SessionManager and single_turn_worker into handle_message() with fire-and-forget asyncio.Task dispatch plus /new session-reset command.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (TDD) | Wire handler.py with Claude dispatch and /new command | d14293a (RED), 3799cad (GREEN) | src/handler.py, tests/test_handler.py |
| 2 | Wire main.py with SessionManager initialization | 8966a44 | main.py |

## What Was Built

### Task 1: handler.py — Claude dispatch and /new command

`create_handler()` now accepts `session_manager` and `config` parameters, passing them through the closure to `handle_message()`.

`handle_message()` pipeline now:
1. Dedup check
2. Filter check (group @mention)
3. Parse message content
4. `/new` command check — destroy session + send green confirmation card, return early
5. Send thinking card
6. Resolve session key (P2P: open_id, group: chat_id)
7. Get or create SessionState via `session_manager.get_or_create()`
8. Group: fetch display_name + inject `[name]:` prefix; P2P: pass text directly
9. `asyncio.create_task(single_turn_worker(...))` — fire and forget

Key behaviors:
- `/new` is case-insensitive and trims whitespace
- asyncio.Task is used (not await) — handler returns quickly, never blocks WS loop
- Group messages always fetch display_name from Feishu contact API (cached per session)

### Task 2: main.py — SessionManager initialization

Added at startup (between dedup cache and event loop):
- `ClaudeAgentOptions(permission_mode="acceptEdits", cwd=config.working_dir)` — non-interactive mode
- `allowed_tools` set only when non-empty (empty = all tools allowed)
- `asyncio.Semaphore(config.max_concurrent_tasks)` — global concurrency cap (CONC-02)
- `SessionManager(options=claude_options, semaphore=semaphore)` — bootstrapped once
- `create_handler()` call updated to pass `session_manager` and `config`

## Decisions Made

1. `/new` handled before thinking card — cleaner UX, no thinking card flash on reset
2. Fire-and-forget via `asyncio.create_task` — consistent with handler sync→async bridge pattern
3. `CardKit v2 {"data": card}` format used for /new confirmation card — consistent with `_build_card()`

## Test Coverage

20 tests in `tests/test_handler.py` (added 11 new):
- `TestCreateHandler` (3 tests): factory callable, sync check, new params
- `TestHandleMessage` (6 tests): dedup skip, filter skip, thinking card, unsupported type, exception resilience
- `TestHandleMessageClaudeDispatch` (5 tests): task creation, P2P session key, group session key, display name fetch, group prompt prefix
- `TestNewCommand` (3 tests): destroy session, no Claude call, case-insensitive
- `TestHandleMessageIntegration` (1 test): dedup before card
- `TestMainImportable` (1 test): main.py structure

Full suite: **97/97 passed**

## Deviations from Plan

None — plan executed exactly as written. The `ClaudeAgentOptions` constructor was verified at implementation time and matched the plan's interface spec (supports `permission_mode`, `cwd`, `allowed_tools` as kwargs).

## Known Stubs

None — handler now dispatches to real `single_turn_worker` which calls Claude SDK. Full end-to-end pipeline is wired.

## Self-Check: PASSED
