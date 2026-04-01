# Feature Landscape

**Domain:** Feishu bot ↔ AI (Claude Code) bridge service
**Researched:** 2026-04-01
**Confidence:** MEDIUM — core Feishu API behaviors well-documented; Claude Code SDK specifics partially inferred from public docs + training data

---

## Table Stakes

Features users expect from any AI bot bridge. Missing = product feels incomplete or broken.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Plain text message receive + reply | Every bot bridge does this | Low | `im.message.receive_v1` event; reply via `/im/v1/messages/{id}/reply` |
| Streaming card response (typing effect) | AI latency is 5-30s; users need to see progress | Medium | CardKit PATCH API (`/cardkit/v1/cards/{card_id}`); requires initial card creation then incremental patches |
| Per-user session isolation (P2P) | User expects their own conversation context | Low | Key: `open_id`; in-memory dict for MVP (2-5 users) |
| Per-group session isolation | Group members should not bleed into each other's context | Low | Key: `chat_id` for shared group context OR `chat_id:open_id` for per-member |
| Duplicate message deduplication | Feishu retries events on timeout; without dedup, Claude runs twice | Low | Store `message_id` in a seen-set (in-memory TTL dict); 3-second webhook timeout makes this critical |
| Error recovery (no crash per message) | Process must not die on a single bad message or Claude error | Low | Per-message try/except; log and reply error card |
| @mention detection in group chats | In groups, bot should only respond when addressed | Low | Parse `message.mentions[]`, match bot's `open_id` |
| P2P (direct message) handling | All DMs should trigger AI response without @mention | Low | `chat_type == "p2p"` → always process |
| Graceful "processing" card on receive | Without immediate feedback, users think bot is broken | Low | Send initial card immediately, then stream updates into it |
| Config via environment variables | Standard for services; enables deployment without code changes | Low | APP_ID, APP_SECRET, working dir, allowed tools |

---

## Differentiators

Features that elevate this bridge above a basic ChatGPT-style bot. Specific to Claude Code's capabilities.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Tool use visibility in card | Show which tools Claude invoked (bash, read, write) during response | Medium | Parse `tool_use` content blocks from Claude Code SDK events; render as collapsed sections in card |
| Streaming token-by-token card updates | Text appears as Claude generates it, not all at once | Medium | Buffer tokens, batch-patch card every ~500ms to avoid rate-limit hitting CardKit API |
| Typing indicator component | Visual cue that AI is actively generating | Low | Add `"tag": "typing"` element to card while streaming; remove in final patch |
| `/new` command to reset session | Users want to start fresh without leaving the chat | Low | Parse message for `/new`, `/reset`; clear session state for that key |
| Command palette (`/help`) | Discoverable feature for small teams | Low | Static card response listing available commands |
| File operation results in card | When Claude reads/writes files, show paths and summaries in card | Medium | Extract `tool_result` content, format as collapsible card section |
| Working directory scoping | Each session runs in a configured directory; prevents accidents | Low | Pass `--cwd` to Claude Code process; configurable per-user or global |
| Card "Stop" button | Allow user to cancel a long-running Claude task | High | Requires process-level kill signal + card callback within 3s; non-trivial with async subprocess |
| Feedback buttons on response card | Thumbs up/down for each response | Low | Card button callback; log feedback to file; no DB needed for MVP |
| Thread reply mode | Reply in message thread rather than new message | Low | Use `reply_in_thread: true` on message send API |

---

## Anti-Features

Features to explicitly NOT build for this project's scope (small team, MVP mindset).

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Multi-tenant / user permission system | 2-5 person team; everyone is trusted | Document who has access in README; no code needed |
| Web UI / management dashboard | Adds infra complexity; team uses Feishu already | Manage via config file + log tailing |
| Message history database | Adds storage dependency; Claude Code maintains its own context | Rely on in-memory session + Claude's conversation memory |
| Redis for session storage | Overkill for 2-5 users; adds operational burden | In-memory dict with TTL is fine at this scale |
| Multi-LLM routing (GPT, Gemini, etc.) | Project is specifically Claude Code, not a generic gateway | Hard-code Claude Code SDK as the backend |
| Voice/audio message handling | Not relevant to developer workflows | Log "unsupported message type" and skip gracefully |
| Image analysis (vision) | Claude Code SDK doesn't expose vision through its subprocess interface for MVP | Acknowledge limitation; potentially defer |
| Docker containerization | PROJECT.md explicitly out of scope for MVP | Run directly as Python process; systemd unit for stability |
| Webhook mode (public IP required) | Long-connection (WebSocket) is already chosen; no public IP available | Use `lark-oapi` WebSocket long connection exclusively |
| Conversation branching / multiple sessions per user | Complex UX for small team; `/new` command covers reset need | Single active session per user key |

---

## Feature Dependencies

```
[Feishu WebSocket long connection] → receives all message events
        |
        ├── [Duplicate dedup] → MUST happen before any processing
        |
        ├── [@mention / P2P filter] → MUST happen before sending to Claude
        |
        ├── [Initial "processing" card] → MUST send before calling Claude (user feedback)
        |        |
        |        └── [Streaming card updates] → depends on card_id from initial card
        |                 |
        |                 ├── [Typing indicator] → part of streaming updates
        |                 └── [Tool use visibility] → part of streaming updates
        |
        ├── [Session isolation] → must be established before passing messages to Claude
        |        |
        |        └── [/new command] → modifies session state
        |
        └── [Card callback handler] → independent path for button interactions
                 |
                 └── [Feedback buttons] → depends on card callback handler
```

Key ordering constraints:
- Dedup before session lookup (avoid creating orphan sessions for repeated events)
- Initial card send before Claude invocation (3-second Feishu response window)
- Card `card_id` from initial send is required for all subsequent streaming PATCH calls
- Session key established from `chat_type + chat_id + open_id` before message dispatch

---

## MVP Recommendation

**Phase 1 (Core loop — must work end-to-end):**
1. WebSocket long connection receive → dedup → @mention/P2P filter
2. Session isolation (in-memory, `open_id` for P2P, `chat_id` for groups)
3. Send initial "thinking" card → get `card_id`
4. Stream Claude Code SDK output → batch-patch card with token deltas
5. Remove typing indicator on completion; add error card on failure

**Phase 2 (Polish — makes it usable daily):**
6. Tool use visibility in card (show `bash`, `read`, `write` calls)
7. `/new` and `/help` commands
8. Thread reply mode
9. Working directory config per session

**Defer:**
- "Stop" button: High complexity, non-critical for MVP (session timeout handles runaway tasks)
- Feedback buttons: Nice-to-have, add in Phase 2 or 3
- File operation result cards: Included in tool visibility work

---

## Platform Constraints Affecting Features

| Constraint | Impact on Features |
|------------|-------------------|
| Feishu 3-second callback window | Initial card must be sent synchronously before heavy Claude processing; use async task for actual Claude call |
| CardKit PATCH not in `lark-oapi` | Streaming updates require raw HTTP (httpx/aiohttp) or `BaseRequest` wrapper; extra implementation work |
| Long connection max 50 concurrent | Not a concern for 2-5 users |
| Long connection no cluster broadcast | Single-process deployment; no distributed session needed |
| No sudo / no root | Process management via systemd user unit or nohup |
| Claude Code SDK subprocess-based | Tool outputs arrive as `tool_use`/`tool_result` JSON events in stdout stream; parse line-by-line |

---

## Sources

- Feishu Open Platform: https://open.feishu.cn/document/ (MEDIUM confidence — page content not directly scraped; inferred from JS config and known API patterns)
- lark-oapi SDK: https://github.com/larksuite/oapi-sdk-python (HIGH confidence — well-documented official SDK)
- PROJECT.md constraints and out-of-scope decisions (HIGH confidence — authoritative for this project)
- WebSearch synthesis: Feishu bot session management, dedup, card callback patterns (MEDIUM confidence — multiple sources agree on patterns)
- Claude Code SDK subprocess/streaming behavior (MEDIUM confidence — consistent across multiple search results; aligns with training data)
