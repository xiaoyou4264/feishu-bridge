---
phase: 01-feishu-connectivity
plan: 02
subsystem: feishu-integration
tags: [lark-oapi, asyncio, websocket, cardkit, structlog, tdd]

# Dependency graph
requires:
  - phase: 01-feishu-connectivity/01-01
    provides: Config, DeduplicationCache, should_respond, parse_message_content

provides:
  - send_thinking_card() async function with CardKit v2 blue header card
  - send_unsupported_type_card() async function with orange header card (D-05)
  - create_handler() sync handler factory for lark SDK registration
  - handle_message() async pipeline: dedup -> filter -> parse -> card
  - main.py entry point with get_bot_open_id(), WS client startup, auto_reconnect=True

affects: [02-claude-integration, 03-streaming-cards]

# Tech tracking
tech-stack:
  added: [structlog (logging in cards/handler)]
  patterns:
    - "Sync-to-async bridge: sync def on_message() calls loop.create_task(handle_message())"
    - "lark_oapi.ws imported before asyncio.get_event_loop() to capture same loop (Pitfall 2)"
    - "Bot open_id fetched at startup via raw /bot/v3/info request (Pitfall 5)"
    - "CardKit v2 interactive card via im.v1.message.areply() with msg_type='interactive'"

key-files:
  created:
    - src/cards.py
    - src/handler.py
    - main.py
    - tests/test_cards.py
    - tests/test_handler.py
  modified: []

key-decisions:
  - "Used raw BaseRequest for get_bot_open_id() — lark-oapi 1.5.3 has no bot.v3 module (only application.v6.bot with empty resource)"
  - "send_unsupported_type_card() logs warning but does not raise on failure — best effort for D-05"
  - "Exceptions in handle_message() are caught at outermost level to prevent coroutine crash"

patterns-established:
  - "Pattern: TDD RED->GREEN with AsyncMock for lark areply()"
  - "Pattern: lark ReplyMessageRequest.builder() -> request_body.content = json.dumps(card_dict)"

requirements-completed: [CONN-01, CONN-04, CARD-01]

# Metrics
duration: 6min
completed: 2026-04-01
---

# Phase 1 Plan 02: Event Handler Pipeline and Main Entry Point Summary

**Sync-to-async event pipeline wiring dedup/filter/card-reply via lark-oapi WS long connection with CardKit v2 interactive cards**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-01T12:58:21Z
- **Completed:** 2026-04-01T13:04:21Z
- **Tasks:** 2
- **Files modified:** 5 created

## Accomplishments

- `src/cards.py`: `send_thinking_card()` sends CardKit v2 blue header card with "正在思考中" body via `im.v1.message.areply()`; returns reply message_id for Phase 3 PATCH streaming
- `src/handler.py`: `create_handler()` factory returns a SYNC handler (CRITICAL — Pitfall 1 avoided); `handle_message()` runs dedup → filter → parse → thinking/unsupported card async pipeline with full exception safety
- `main.py`: Entry point with Config validation, structlog setup, bot_open_id fetch at startup (Pitfall 5), auto_reconnect=True (CONN-04), correct lark_oapi.ws import order (Pitfall 2)

## Task Commits

Each task was committed atomically:

1. **Task 1: Card reply functions** - `0094d41` (feat — TDD RED->GREEN)
2. **Task 2: Event handler + main entry point** - `0b11f03` (feat — TDD RED->GREEN)

**Plan metadata:** (docs commit follows)

_Note: Both tasks used TDD with RED (failing tests) confirmed before GREEN (implementation)._

## Files Created/Modified

- `/home/mi/feishu-bridge/src/cards.py` — CardKit v2 thinking card and unsupported type card via areply
- `/home/mi/feishu-bridge/src/handler.py` — Sync/async bridge handler factory and message pipeline
- `/home/mi/feishu-bridge/main.py` — Entry point: config load, bot_open_id fetch, WS client startup
- `/home/mi/feishu-bridge/tests/test_cards.py` — 10 tests for card reply functions
- `/home/mi/feishu-bridge/tests/test_handler.py` — 10 tests for handler pipeline and main structure

## Decisions Made

- **Bot open_id via raw request:** `lark-oapi 1.5.3` does NOT have `client.bot.v3` (research was wrong about this path). Used `BaseRequest` with `/open-apis/bot/v3/info` instead. The `application.v6.bot` resource exists but only has a `config` attribute (no `get()` method).
- **send_unsupported_type_card() failure handling:** Logs warning but does not raise — the unsupported type prompt is best-effort; no cascading error makes sense.
- **Outer try/except in handle_message():** Catches all unexpected errors to prevent coroutine crash. Structlog `error()` ensures visibility without bringing down the handler.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Bot API path correction: no `bot.v3` module in lark-oapi 1.5.3**

- **Found during:** Task 2 (main.py implementation)
- **Issue:** Plan referenced `lark.Client.bot.v3.bot.get()` (from research Open Question #2). This path does not exist — lark-oapi 1.5.3 has no `bot` top-level API. The `application.v6.bot` resource exists but only has a `config` property (no methods).
- **Fix:** Used `client.request(BaseRequest(http_method="GET", uri="/open-apis/bot/v3/info", token_types={"tenant_access_token"}))` raw API call to fetch bot info.
- **Files modified:** `main.py`
- **Verification:** Import check passes; logic follows same pattern as research's fallback suggestion
- **Committed in:** `0b11f03` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug, API path)
**Impact on plan:** Necessary correction; no scope creep. Bot open_id is still fetched at startup as required.

## Issues Encountered

- lark-oapi `bot.v3` path from research PITFALLS.md did not exist at implementation time — resolved via raw API call (plan had a fallback note for exactly this scenario)
- `websockets` deprecation warnings in tests (harmless — lark-oapi 1.5.3 using legacy websockets API, not our code)

## User Setup Required

None - no external service configuration required for this plan's code. End-to-end testing requires a registered Feishu app (already documented as Phase 1 blocker in STATE.md).

## Next Phase Readiness

- Full event pipeline complete: WS receive → dedup → filter → thinking card reply
- All 44 tests pass (Plan 01-01 baseline 24 + Plan 01-02 new 20)
- `main.py` is runnable with valid APP_ID/APP_SECRET in `.env`
- Phase 2 (Claude integration) can consume `handle_message()` by adding Claude call after card send

---
*Phase: 01-feishu-connectivity*
*Completed: 2026-04-01*
