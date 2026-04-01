# Roadmap: Feishu Bridge

## Overview

Four phases that build strictly on each other: first make the Feishu connection reliable and the event pipeline sound (Phase 1), then integrate the Claude Agent SDK with per-conversation isolation (Phase 2), then add the streaming CardKit renderer that makes responses feel alive (Phase 3), then harden the whole system for unattended production use (Phase 4). Each phase is independently testable and directly unblocks the next.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Feishu Connectivity** - WebSocket long connection, event pipeline, asyncio queue bridge, dedup, @mention filter, "thinking" card
- [x] **Phase 2: Claude Integration** - Claude Agent SDK, per-conversation asyncio Tasks, session isolation, multi-turn context, watchdog, concurrency control, group chat user attribution (completed 2026-04-01)
- [ ] **Phase 3: Streaming Card Renderer** - CardKit POST/PATCH lifecycle, 300-500ms batched flush, tool use visibility, typing indicator
- [ ] **Phase 4: Stability and Operations** - systemd service, graceful shutdown, structured logging, idle TTL cleanup, user commands, interaction buttons

## Phase Details

### Phase 1: Feishu Connectivity
**Goal**: The bot is alive — it receives Feishu messages reliably and sends an immediate acknowledgement card within the 3-second callback window
**Depends on**: Nothing (first phase)
**Requirements**: CONN-01, CONN-02, CONN-03, CONN-04, CONN-05, CONN-06, CARD-01
**Success Criteria** (what must be TRUE):
  1. Sending a direct message to the bot produces an immediate "thinking" card reply within 3 seconds
  2. Sending the same message twice (simulated Feishu retry) causes the bot to respond exactly once
  3. In a group chat the bot responds only when @mentioned, never to other messages
  4. The bot reconnects automatically after a simulated network drop without manual restart
  5. All configuration (APP_ID, APP_SECRET, log level) is read from environment variables at startup; the process refuses to start if required vars are absent
**Plans:** 2/3 plans executed

Plans:
- [x] 01-01-PLAN.md — Project scaffolding, config validation, dedup cache, message filters
- [x] 01-02-PLAN.md — Card reply functions, event handler pipeline, main entry point
- [ ] 01-03-PLAN.md — End-to-end verification with real Feishu app (checkpoint)

### Phase 2: Claude Integration
**Goal**: Users receive real Claude responses — the full message pipeline from Feishu message to Claude reply works end-to-end, with each conversation isolated in its own asyncio Task
**Depends on**: Phase 1
**Requirements**: CLAUDE-01, CLAUDE-02, CLAUDE-03, CLAUDE-04, CLAUDE-05, CLAUDE-06, SESS-01, SESS-02, SESS-03, CONC-01, CONC-02, CONC-03
**Success Criteria** (what must be TRUE):
  1. A user's message is processed by Claude and a text reply appears in Feishu (even if not yet streaming)
  2. Follow-up messages in the same conversation maintain context from earlier turns
  3. Two different users sending messages simultaneously each receive independent responses without interference
  4. In a group chat, messages from different users share context but each message clearly attributes its sender (e.g. `[张三]: ...`)
  5. A message that causes Claude to hang is automatically cancelled after the watchdog timeout and the user receives an error card; other conversations are unaffected
  6. Typing `/new` in a conversation resets context so the next reply has no memory of prior turns
  7. When MAX_CONCURRENT_TASKS is reached, new messages queue instead of being dropped
  8. In a group chat, two users sending messages simultaneously both receive independent parallel responses (not queued behind each other)
**Plans:** 3/3 plans complete

Plans:
- [x] 02-01-PLAN.md — Config extension (timeout, concurrency, tools) + card update/error functions
- [x] 02-02-PLAN.md — SessionManager (per-session ClaudeSDKClient) + Claude single-turn worker
- [x] 02-03-PLAN.md — Handler Claude dispatch, /new command, main.py SessionManager init
**UI hint**: yes

### Phase 3: Streaming Card Renderer
**Goal**: Responses stream live — users see text appearing token-by-token in a Feishu card, with tool invocations visible as they happen
**Depends on**: Phase 2
**Requirements**: CARD-02, CARD-03, CARD-04, CARD-05, CARD-06, CARD-07, INTER-03
**Success Criteria** (what must be TRUE):
  1. Claude's response text appears progressively in the card as it is generated (typing effect visible to user)
  2. When Claude invokes a tool (bash, read, write), the card shows which tool was called and with what summary before the final text arrives
  3. The card displays a typing indicator during generation and removes it once the response is complete
  4. Sending a rapid sequence of messages does not produce HTTP 429 errors; batching absorbs the burst
  5. The final card contains the complete formatted Markdown response and no typing indicator
**Plans**: TBD
**UI hint**: yes

### Phase 4: Stability and Operations
**Goal**: The bridge runs unattended — it survives crashes, restarts on boot, shuts down cleanly, and gives operators enough signal to diagnose problems
**Depends on**: Phase 3
**Requirements**: STAB-01, STAB-02, STAB-03, STAB-04, SESS-04, SESS-05, INTER-01, INTER-02
**Success Criteria** (what must be TRUE):
  1. A malformed or exception-causing message results in an error card to that user; the service continues handling other messages normally
  2. After system reboot the bridge is running and responsive without manual intervention
  3. Sending SIGTERM causes all active Claude tasks to cancel gracefully and the process exits cleanly within 10 seconds
  4. Log output is structured JSON with event_id correlation, making it possible to trace a single message end-to-end
  5. A conversation session idle for longer than the configured TTL is automatically cleaned up; `/help` lists available commands
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Feishu Connectivity | 2/3 | In Progress|  |
| 2. Claude Integration | 3/3 | Complete   | 2026-04-01 |
| 3. Streaming Card Renderer | 0/TBD | Not started | - |
| 4. Stability and Operations | 0/TBD | Not started | - |
