---
phase: 1
slug: feishu-connectivity
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-01
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | none — Wave 0 installs |
| **Quick run command** | `python -m pytest tests/ -x -q` |
| **Full suite command** | `python -m pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | CONN-05 | unit | `pytest tests/test_config.py -v` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | CONN-02, CONN-03 | unit | `pytest tests/test_dedup.py tests/test_filters.py -v` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 2 | CARD-01 | unit | `pytest tests/test_cards.py -v` | ❌ W0 | ⬜ pending |
| 01-02-02 | 02 | 2 | CONN-01, CONN-04 | unit | `pytest tests/test_handler.py -v` | ❌ W0 | ⬜ pending |
| 01-03-01 | 03 | 3 | ALL | manual | End-to-end with real Feishu app | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/conftest.py` — shared fixtures (mock lark client, event factory)
- [ ] `tests/test_config.py` — config loading tests
- [ ] `tests/test_dedup.py` — dedup logic tests
- [ ] `tests/test_filters.py` — message filtering tests
- [ ] `tests/test_cards.py` — card reply tests
- [ ] `tests/test_handler.py` — event handler pipeline tests
- [ ] `pip install pytest pytest-asyncio structlog` — test framework install

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| WebSocket reconnection after network drop | CONN-04 | Requires simulating network failure | 1. Start bridge 2. Kill network 3. Restore network 4. Verify reconnection in logs |
| 3-second card response time | CARD-01 | Requires real Feishu API timing | 1. Send DM to bot 2. Observe card appears within 3s |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
