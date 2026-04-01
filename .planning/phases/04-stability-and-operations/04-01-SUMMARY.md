---
phase: 04-stability-and-operations
plan: "01"
subsystem: observability-and-session
tags: [structlog, event_id, contextvars, /help, session-ttl, cleanup]
dependency_graph:
  requires: []
  provides: [structured-logging, event_id-correlation, help-command, session-ttl-cleanup]
  affects: [src/config.py, main.py, src/handler.py, src/cards.py, src/session.py]
tech_stack:
  added: [structlog.contextvars]
  patterns: [bind_contextvars/clear_contextvars, background-cleanup-loop, configure_structlog-factory]
key_files:
  created: []
  modified:
    - src/config.py
    - main.py
    - src/handler.py
    - src/cards.py
    - src/session.py
decisions:
  - "merge_contextvars must be first structlog processor — injects event_id from contextvars into every log line"
  - "bind_contextvars called before asyncio.create_task so worker task inherits event_id context snapshot"
  - "clear_contextvars in finally block prevents event_id leaking to next event on the same coroutine"
  - "/help check placed before /new check — consistent with plan order, both return early"
  - "session_cleanup_loop uses list() copy of _sessions.items() to avoid dict-changed-size-during-iteration"
metrics:
  duration: "2 minutes"
  completed_date: "2026-04-01"
  tasks_completed: 2
  files_modified: 5
requirements_satisfied: [STAB-04, SESS-04, SESS-05]
---

# Phase 4 Plan 01: Structured Logging, /help Command, and Session TTL Cleanup Summary

**One-liner:** Structlog with event_id contextvars correlation, /help green card, and idle session TTL background cleanup loop.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Config + structlog reconfigure + event_id binding | b8aba17 | src/config.py, main.py, src/handler.py |
| 2 | /help command + session TTL cleanup loop | 209135b | src/cards.py, src/handler.py, src/session.py, main.py |

## What Was Built

### Task 1: Structured Logging Infrastructure

**src/config.py** — Added `session_ttl: float = 3600.0` (from `SESSION_TTL` env) and `log_format: str = "console"` (from `LOG_FORMAT` env).

**main.py** — Replaced bare `structlog.configure` block with `configure_structlog(log_level, log_format)` function. `merge_contextvars` is first processor so `event_id` bound in handler propagates to every log line. `JSONRenderer` activates when `LOG_FORMAT=JSON`.

**src/handler.py** — Added `bind_contextvars(event_id=event_id)` at pipeline entry, BEFORE `asyncio.create_task()` so the worker task inherits the event_id context snapshot. Wrapped pipeline body in try/finally with `clear_contextvars()` to prevent context leak between events.

### Task 2: /help Command and Session TTL Cleanup

**src/cards.py** — Added `build_help_card()` returning a green CardKit v2 card with `/new` and `/help` command descriptions.

**src/handler.py** — Added `/help` command check before `/new` check. Sends green card via `build_help_card()` and returns early, skipping Claude dispatch.

**src/session.py** — Added `session_cleanup_loop(session_manager, ttl_seconds, interval_seconds=60)` async background task. Scans every 60s, destroys sessions where `time.time() - state.last_activity > ttl_seconds`. Exits cleanly on `CancelledError`.

**main.py** — Starts `cleanup_task = loop.create_task(session_cleanup_loop(...))` after `SessionManager` init. Task runs for process lifetime.

## Verification

- All 135 tests pass: `python3 -m pytest tests/ -x -q`
- `grep -c "merge_contextvars" main.py` → 2 (import + usage in processor list)
- `grep -c "bind_contextvars" src/handler.py` → 1
- `grep -c "session_cleanup_loop" src/session.py` → 1
- `grep -c "build_help_card" src/cards.py` → 1

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

**Minor implementation note (not a deviation):** `session_cleanup_loop` uses `list(session_manager._sessions.items())` instead of direct `.items()` iteration to avoid `RuntimeError: dictionary changed size during iteration` if multiple sessions expire simultaneously. This is a correctness fix (Rule 2) at zero cost.

## Known Stubs

None — all features are fully wired. `build_help_card()` returns real card content. `session_cleanup_loop` actually calls `session_manager.destroy()`.

## Self-Check: PASSED

- src/config.py: FOUND
- src/handler.py: FOUND
- src/cards.py: FOUND
- src/session.py: FOUND
- main.py: FOUND
- Commit b8aba17: FOUND
- Commit 209135b: FOUND
