---
phase: 01-feishu-connectivity
plan: "01"
subsystem: infra
tags: [python, pydantic, python-dotenv, pytest, lark-oapi, dedup, message-filter]

# Dependency graph
requires: []
provides:
  - "Config model with from_env() that validates required env vars and exits on missing (src/config.py)"
  - "DeduplicationCache with bounded FIFO LRU eviction using OrderedDict (src/dedup.py)"
  - "should_respond() for P2P/group @mention filtering (src/filters.py)"
  - "parse_message_content() for text/post parsing with ValueError on unsupported types (src/filters.py)"
  - "Test fixtures in tests/conftest.py: make_event_message, make_event_data"
  - "Project scaffolding: requirements.txt, pyproject.toml, .env.example, .gitignore"
affects:
  - 01-02  # event handler consumes Config, DeduplicationCache, should_respond, parse_message_content
  - 01-03  # main.py entry point uses Config.from_env()

# Tech tracking
tech-stack:
  added:
    - "pydantic==2.12.5 (config validation)"
    - "python-dotenv==1.2.2 (env file loading)"
    - "structlog>=24.0.0 (structured logging — installed, used in future plans)"
    - "pytest>=8.0.0 (test framework)"
    - "pytest-asyncio>=0.24.0 (async test support)"
  patterns:
    - "TDD red-green cycle: write failing tests first, implement to pass"
    - "Config.from_env() classmethod: load_dotenv() + os.environ[] + sys.exit(1) on KeyError"
    - "DeduplicationCache: OrderedDict for O(1) FIFO eviction at max_size boundary"
    - "should_respond(): chat_type branch — p2p always True, group checks mentions list"
    - "parse_message_content(): msg_type dispatch — text/post handled, others raise ValueError"
    - "SimpleNamespace for mock objects in tests (no lark SDK import needed)"

key-files:
  created:
    - src/config.py
    - src/dedup.py
    - src/filters.py
    - src/__init__.py
    - tests/__init__.py
    - tests/conftest.py
    - tests/test_config.py
    - tests/test_dedup.py
    - tests/test_filters.py
    - requirements.txt
    - pyproject.toml
    - .env.example
    - .gitignore
  modified: []

key-decisions:
  - "CONN-06: .env.example explicitly warns against reusing cli_a92d11a974b89bcd app — new Feishu app required"
  - "event_id used for dedup (not message_id) — tighter guard, unique per delivery attempt"
  - "DeduplicationCache uses FIFO size-based eviction (not TTL-based) for simplicity; ttl_seconds param stored for future use"
  - "test_cache_eviction_fifo corrected: re-inserting an evicted key counts as a new insertion that can cause further eviction"

patterns-established:
  - "Config pattern: pydantic BaseModel + classmethod from_env() with load_dotenv() and sys.exit(1) guard"
  - "Dedup pattern: OrderedDict FIFO eviction, max_size=1000, no threading locks (single asyncio loop)"
  - "Filter pattern: chat_type dispatch first, then mentions check; ValueError for unsupported types"
  - "Test pattern: SimpleNamespace mock objects for Feishu event model, shared factories in conftest.py"

requirements-completed:
  - CONN-05
  - CONN-06
  - CONN-02
  - CONN-03

# Metrics
duration: 15min
completed: 2026-04-01
---

# Phase 1 Plan 01: Project Foundation Summary

**Config validation with sys.exit(1) on missing env vars, bounded FIFO dedup cache, and P2P/group @mention filter — 24 tests green**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-04-01T13:00:00Z
- **Completed:** 2026-04-01T13:15:00Z
- **Tasks:** 2 of 2
- **Files modified:** 13 created

## Accomplishments

- Config module with pydantic BaseModel that refuses to start if APP_ID or APP_SECRET missing from environment
- DeduplicationCache with OrderedDict FIFO LRU eviction — detects duplicate event_ids within max_size bound
- Message filters: should_respond() (P2P always / group only with @mention), parse_message_content() (text/post handled, unsupported types raise ValueError per D-05)
- Test infrastructure: 24 tests total, all green; conftest.py fixtures reusable by Plan 02 and 03

## Task Commits

Each task was committed atomically:

1. **Task 1: Project scaffolding + Config module** - `af77649` (feat)
2. **Task 2: Dedup cache and message filters** - `a3f21fe` (feat)

## Files Created/Modified

- `src/config.py` — Config pydantic model with from_env() classmethod and sys.exit(1) guard
- `src/dedup.py` — DeduplicationCache with OrderedDict FIFO bounded LRU eviction
- `src/filters.py` — should_respond() and parse_message_content() functions
- `src/__init__.py` — Package marker
- `tests/__init__.py` — Package marker
- `tests/conftest.py` — make_event_message and make_event_data factory fixtures using SimpleNamespace
- `tests/test_config.py` — 6 tests for Config.from_env() behavior
- `tests/test_dedup.py` — 5 tests for DeduplicationCache dedup and FIFO eviction
- `tests/test_filters.py` — 13 tests for should_respond() and parse_message_content()
- `requirements.txt` — Pinned deps: lark-oapi==1.5.3, pydantic==2.12.5, python-dotenv==1.2.2, structlog>=24.0.0
- `pyproject.toml` — pytest asyncio_mode=auto config
- `.env.example` — Documents CONN-06 isolation requirement (new app, not cli_a92d11a974b89bcd)
- `.gitignore` — Python standard ignores

## Decisions Made

- Used FIFO size-based eviction for DeduplicationCache (not TTL-based) — simpler, sufficient for 2-5 user scale
- event_id chosen for dedup key (not message_id) per CONN-02 update — more precise guard at event delivery level
- .env.example documents CONN-06 explicitly with DO NOT reuse warning

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_cache_eviction_fifo test logic**
- **Found during:** Task 2 (Dedup cache)
- **Issue:** Test asserted `is_duplicate("second") is True` after re-inserting "first" into a max_size=2 cache. Re-inserting "first" evicts "second" (FIFO), so the assertion was incorrect — the implementation was correct but the test was wrong.
- **Fix:** Updated test to correctly trace the eviction sequence: after re-adding "first", cache is {"third", "first"}; checking "second" returns False (correct), checking "third" returns True (still in cache).
- **Files modified:** tests/test_dedup.py
- **Verification:** All 5 dedup tests pass
- **Committed in:** a3f21fe (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — incorrect test assertion, not production code bug)
**Impact on plan:** Minimal. Test logic corrected to match correct implementation behavior. No scope creep.

## Issues Encountered

None — implementation straightforward following research patterns from 01-RESEARCH.md.

## User Setup Required

None — no external service configuration required for this plan. Feishu app credentials are needed before Plan 02 can be tested end-to-end (CONN-06 — see .env.example).

## Known Stubs

None — all modules are fully implemented and testable without a running Feishu connection.

## Next Phase Readiness

- Plan 02 (event handler) can import Config, DeduplicationCache, should_respond, parse_message_content immediately
- conftest.py fixtures (make_event_message, make_event_data) available for handler tests
- Dependencies installed: structlog, pytest, pytest-asyncio, pydantic, python-dotenv, lark-oapi
- Blocker (unchanged from STATE.md): New Feishu app credentials (APP_ID + APP_SECRET) required before any end-to-end testing

---
*Phase: 01-feishu-connectivity*
*Completed: 2026-04-01*
