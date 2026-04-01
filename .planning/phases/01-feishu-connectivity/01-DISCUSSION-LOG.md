# Phase 1: Feishu Connectivity - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-01
**Phase:** 01-feishu-connectivity
**Areas discussed:** Credential strategy, Initial card design, Message filtering, Configuration

---

## Credential Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| New independent app | Create new Feishu app for bridge, avoid event competition | ✓ |
| Reuse existing app | Continue using cli_a92d11a974b89bcd, accept event loss risk | |
| Reuse then switch | MVP reuse, create new app if conflicts arise | |

**User's choice:** New independent app
**Notes:** User also clarified: no MCP dependency, use lark-oapi SDK directly. feishu CLI is for auxiliary operations only.

---

## "Thinking" Initial Card

| Option | Description | Selected |
|--------|-------------|----------|
| Simple text | Just show "Thinking..." text | |
| Status card | Card with header + status + typing animation | ✓ |
| Claude decides | Implementation-time decision | |

**User's choice:** Status card

---

## Message Filtering

| Option | Description | Selected |
|--------|-------------|----------|
| Text only | Only respond to pure text messages | |
| Text + rich text | Handle text and post (rich text) types | |
| Handle all types | Try to process text/rich text/image/file | ✓ |

**User's choice:** Handle all types

| Option | Description | Selected |
|--------|-------------|----------|
| Friendly prompt | Reply "unsupported type, please send text" | ✓ |
| Silent ignore | No reply, no processing | |
| Claude decides | Context-dependent | |

**User's choice:** Friendly prompt for unsupported types

---

## Configuration

| Option | Description | Selected |
|--------|-------------|----------|
| .env file | .env + python-dotenv, env vars override | ✓ |
| YAML/TOML | Structured config file with nesting | |
| Pure env vars | ENV only, no config file | |
| Claude decides | Implementation-time decision | |

**User's choice:** .env file with python-dotenv

---

## Claude's Discretion

- WebSocket reconnection parameters
- Dedup TTL and data structure
- asyncio/thread bridge pattern

## Deferred Ideas

None
