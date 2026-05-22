---
phase: 05
slug: inbox-analytics
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-22
---

# Phase 05 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Source: `05-RESEARCH.md` §"Validation Architecture" (36 test cases across 11 requirements).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio (already installed) |
| **Config file** | `tests/conftest.py` (extended with `conversation_factory`, `inbox_state_helpers`) |
| **Quick run command** | `pytest tests/test_phase5_*.py -x -q` |
| **Full suite command** | `pytest tests/ -x` |
| **Estimated runtime** | ~60–90 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_phase5_*.py -x -q`
- **After every plan wave:** Run `pytest tests/ -x`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

> Populated by planner during PLAN.md generation. Each task references the test that proves its acceptance criteria.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 05-XX-YY | TBD | TBD | INBX/AIRC/ANLX | T-05-XX | TBD | unit/integration | `pytest tests/test_phase5_XXX.py::test_YYY` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_phase5_inbox.py` — stubs for INBX-01..05 (list/detail/messages/filter/manager-toggle)
- [ ] `tests/test_phase5_bot_filter.py` — stubs for AIRC-04 (proactive Telethon `event.sender.bot` filter)
- [ ] `tests/test_phase5_analytics.py` — stubs for ANLX-01..04 (4-level cards, all-time counts)
- [ ] `tests/test_phase5_llm_log.py` — stubs for ANLX-05 (llm_calls INSERT around generate_response)
- [ ] `tests/test_phase5_migration_017.py` — idempotency test (apply 017 twice, no failure)
- [ ] `tests/conftest.py` — extend with `conversation_factory(workspace, sender, campaign?)`, `inbox_state_helpers` (seed all 7 status values), `llm_call_factory`, Telethon `event.sender` mock with `.bot` boolean
- [ ] No framework install needed (pytest + pytest-asyncio + httpx already in `requirements.txt`)

---

## Test Pyramid (from RESEARCH §"Validation Architecture")

### Unit
- Pydantic schemas: `ConversationResponse`, `ConversationFilter`, `AnalyticsCards`, `LLMCallResponse` shape validation
- `llm_logger.log_call()` error path: simulate DB failure → response still returned, warning logged
- Bot filter predicate: `getattr(event.sender, 'bot', False) is True` truth table (User vs Channel vs None)
- Auto-takeover idempotency: POST /send twice → conversation stays in `'manual'`
- Status enum: all 7 values pass `ConversationUpdate.status` validation; invalid value → 422

### Integration (workspace-scope critical path)
- All 5+ inbox endpoints reject cross-workspace access (403)
- `GET /conversations` filters: `?campaign_id=&agent_id=&sender_id=&status=&ai_enabled=&search=` independently and combined
- `GET /conversations` default hides `status='bot_ignored'` (D-17), explicit filter shows them
- Manager-mode toggle: POST /disable-ai → `ai_enabled=false, status='manual', paused_at set`; cancel-queue side-effect on pending items
- Reverse switch (D-03): POST /enable-ai → only `ai_enabled=true`, `status` unchanged if was `lead/finished`
- POST /send auto-takeover (D-04): atomically flips status to `'manual'`, inserts message with `sent_by='human'`, telegram call mocked
- Bot filter (D-05/D-06): incoming message from `User(bot=True)` → INSERT messages + conversations(status='bot_ignored', ai_enabled=false), AI NOT called
- Bot filter does NOT break `_handle_antispam_signal` (D-08): antispam IDs in delegation list still trigger sender-pause
- LLM logger (D-09..D-12): every `ai_engine.generate_response` call writes exactly one row to `llm_calls` with workspace_id, conversation_id, prompt JSONB, response, tokens, latency_ms
- LLM logger failure does NOT block response: simulate INSERT exception → caller gets normal AI response, warning logged
- Analytics: 4 endpoints return identical schema (`AnalyticsCards`) with `{sent, replied:{conversation_count, message_count}, leads, finishes}`
- Analytics correctness: seed 10 sent / 3 replied (2 conversations, 5 messages) / 1 lead / 1 finished → counts match
- Analytics scope: workspace-level sums all; campaign/agent/sender filter narrows correctly
- Composite indexes on `conversations` (C-04): EXPLAIN confirms index hit for `(workspace_id, status, campaign_id/agent_id/sender_id)` queries

### E2E smoke (single happy-path)
- Login → list inbox → open dialog → disable-ai → send manual message → analytics card `sent` increments → re-enable-ai

### Migration
- `migrations/017_phase5.sql` idempotency: apply twice via test fixture, no SQL errors, schema stable
- `conversations.status` CHECK accepts all 7 values; rejects unknown value with constraint error
- `llm_calls` FK CASCADE: delete workspace → llm_calls rows cascade-deleted

### Property tests (optional, recommended)
- Pagination: `limit/offset` combinations return non-overlapping pages, totals match
- Filter combinations: every subset of `{campaign_id, agent_id, sender_id, status, search}` returns valid result

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Lovable inbox renders 7 status badges | INBX-01 (UI side) | Frontend in separate Lovable repo | Open Lovable preview, seed each status, verify badge color/label |
| Lovable analytics dashboard renders 4 levels | ANLX-01..04 (UI side) | Frontend in separate Lovable repo | Open Lovable preview, verify card layout on workspace/campaign/agent/sender pages |
| LLM debug panel renders prompt/response expandable | ANLX-05 (UI side) | Frontend in separate Lovable repo | Open dialog in Lovable, expand llm_calls row, verify JSON pretty-print |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
