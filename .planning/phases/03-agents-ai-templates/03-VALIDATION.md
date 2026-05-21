---
phase: 3
slug: agents-ai-templates
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-22
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Source: `03-RESEARCH.md` §"Validation Architecture" (lines 840–898).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 0.23+ |
| **Config file** | `tests/conftest.py` (Phase 1 Wave 0 baseline, Phase 2 extension; Phase 3 adds `test_agent_factory` + migration 015 application in `_setup_database`) |
| **Quick run command** | `pytest tests/test_agents.py tests/test_migration_015.py -x` |
| **Full suite command** | `pytest tests/ -x -v` |
| **Estimated runtime** | ~30 seconds quick, ~2–3 min full |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_agents.py tests/test_migration_015.py -x` (~30 sec)
- **After every plan wave:** Run `pytest tests/ -x -v` (~2–3 min)
- **Before `/gsd:verify-work`:** Full suite green + manual smoke (POST /api/v1/agents → POST /api/v1/send round-trip)
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

> Task IDs are placeholders pending PLAN.md finalisation. Planner MUST keep this table in sync (one row per task, mapped to a test in `tests/` that planner registers in Wave 0).

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 3-01-01 | 01 | 1 | Migration 015 — drop 6 ai_contexts columns | migration smoke | `pytest tests/test_migration_015.py::test_dropped_columns_absent -x` | ❌ W0 | ⬜ pending |
| 3-01-02 | 01 | 1 | Migration 015 — UNIQUE (workspace_id, name) | migration smoke | `pytest tests/test_migration_015.py::test_unique_workspace_name -x` | ❌ W0 | ⬜ pending |
| 3-01-03 | 01 | 1 | Migration 015 — idempotent | migration smoke | `pytest tests/test_migration_015.py::test_idempotent -x` | ❌ W0 | ⬜ pending |
| 3-01-04 | 01 | 1 | AGNT-03 — senders.ai_context_id dropped | smoke/migration | `pytest tests/test_migration_015.py::test_senders_no_ai_context_id -x` | ❌ W0 | ⬜ pending |
| 3-01-05 | 01 | 1 | ai_engine adapter (drop is_active/max_message_length/webhook_functions reads) | unit | `pytest tests/test_ai_engine.py::test_get_context_phase3_schema -x` | ❌ W0 | ⬜ pending |
| 3-01-06 | 01 | 1 | listener adapter (no ai_context_id in get_active_senders) | unit | `pytest tests/test_listener.py::test_get_active_senders_no_ai_context_id -x` | ❌ W0 | ⬜ pending |
| 3-01-07 | 01 | 1 | rotation adapter (no ai_context_id filter) | unit | `pytest tests/test_rotation.py::test_pick_best_sender_workspace_only -x` | ❌ W0 | ⬜ pending |
| 3-01-08 | 01 | 1 | queue.enqueue takes explicit ai_context_id and inserts into conversations | integration | `pytest tests/test_queue_enqueue.py::test_enqueue_with_explicit_ai_context_id -x` | ❌ W0 | ⬜ pending |
| 3-01-09 | 01 | 1 | C-05 — SenderResponse без ai_context_id | unit | `pytest tests/test_senders.py::test_response_has_no_ai_context_id -x` | ❌ W0 | ⬜ pending |
| 3-02-01 | 02 | 1 | AGNT-01 — POST /agents returns 201 | unit/integration | `pytest tests/test_agents.py::test_create_agent_returns_201 -x` | ❌ W0 | ⬜ pending |
| 3-02-02 | 02 | 1 | AGNT-01 — workspace isolation (cross-tenant 404) | integration | `pytest tests/test_agents.py::test_create_agent_workspace_scoped -x` | ❌ W0 | ⬜ pending |
| 3-02-03 | 02 | 1 | AGNT-01 — duplicate name → 409 | integration | `pytest tests/test_agents.py::test_create_agent_duplicate_name_409 -x` | ❌ W0 | ⬜ pending |
| 3-02-04 | 02 | 1 | AGNT-02 — system_prompt/rules/tone_of_voice/faq persist | unit | `pytest tests/test_agents.py::test_create_agent_persists_all_fields -x` | ❌ W0 | ⬜ pending |
| 3-02-05 | 02 | 1 | AGNT-02 — FAQ JSONB shape `[{question,answer}]` validation | unit | `pytest tests/test_agents.py::test_faq_shape_validation -x` | ❌ W0 | ⬜ pending |
| 3-02-06 | 02 | 1 | AGNT-02 — FAQ partial PATCH = full replacement | unit | `pytest tests/test_agents.py::test_patch_faq_replaces_not_merges -x` | ❌ W0 | ⬜ pending |
| 3-02-07 | 02 | 1 | AGNT-03 — один agent_id для нескольких senders | integration | `pytest tests/test_send.py::test_same_agent_id_works_for_multiple_senders -x` | ❌ W0 | ⬜ pending |
| 3-02-08 | 02 | 1 | AGNT-04 — GET /agents с campaign_count=0 | integration | `pytest tests/test_agents.py::test_list_agents_with_campaign_count -x` | ❌ W0 | ⬜ pending |
| 3-02-09 | 02 | 1 | AGNT-04 — PATCH /agents/{id} partial update | integration | `pytest tests/test_agents.py::test_patch_agent_partial -x` | ❌ W0 | ⬜ pending |
| 3-02-10 | 02 | 1 | AGNT-04 — DELETE hard, conversations.ai_context_id → NULL | integration | `pytest tests/test_agents.py::test_delete_agent_sets_conversation_to_null -x` | ❌ W0 | ⬜ pending |
| 3-02-11 | 02 | 1 | AGNT-04 — DELETE cascades context_contact_assignments | integration | `pytest tests/test_agents.py::test_delete_agent_cascades_assignments -x` | ❌ W0 | ⬜ pending |
| 3-02-12 | 02 | 1 | AGNT-04 — POST /agents/{id}/duplicate auto-name (copy)/(copy 2)/(copy 3) | integration | `pytest tests/test_agents.py::test_duplicate_agent_auto_name -x` | ❌ W0 | ⬜ pending |
| 3-02-13 | 02 | 1 | AGNT-04 — duplicate race protection (retry on IntegrityError) | unit | `pytest tests/test_agents.py::test_duplicate_race_handling -x` | ❌ W0 | ⬜ pending |
| 3-02-14 | 02 | 1 | C-04 — POST /api/v1/send requires ai_context_id | integration | `pytest tests/test_send.py::test_send_requires_ai_context_id -x` | ❌ W0 | ⬜ pending |
| 3-02-15 | 02 | 1 | C-04 — POST /api/v1/send 404 если agent в другом workspace | integration | `pytest tests/test_send.py::test_send_cross_workspace_agent_404 -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_agents.py` — covers AGNT-01..04 (CRUD + duplicate + delete + cascade)
- [ ] `tests/test_migration_015.py` — covers migration smoke + idempotent + UNIQUE constraint + senders.ai_context_id dropped
- [ ] `tests/test_send.py` — covers Phase 3 rewrite of `POST /api/v1/send` (explicit ai_context_id, cross-workspace 404)
- [ ] `tests/test_ai_engine.py` — covers worker adapter (`get_context` без is_active / max_message_length / webhook_functions)
- [ ] `tests/test_listener.py` — covers `get_active_senders` adaptation (без ai_context_id)
- [ ] `tests/test_rotation.py` — covers `_pick_best_sender` adaptation (без ai_context_id filter)
- [ ] `tests/test_queue_enqueue.py` — covers `enqueue_message` new explicit `ai_context_id` parameter
- [ ] `tests/test_senders.py` — extend with `test_response_has_no_ai_context_id`
- [ ] `tests/conftest.py` — add `test_agent_factory` fixture + apply migration 015 in `_setup_database`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end smoke: POST /api/v1/agents → POST /api/v1/send → AI отвечает на входящее (через listener debounce) | AGNT-03, AGNT-04 | Требует живой Telethon-аккаунт + OpenAI ключ + 3–5 мин ожидания debounce | 1. Создать workspace + sender через Phase 1 onboarding; 2. `POST /api/v1/agents` с реальными prompts; 3. `POST /api/v1/send` с возвращённым agent_id; 4. Отправить inbound сообщение тестовому контакту; 5. Дождаться ответа AI (через 3–5 мин дебаунса) |
| Lovable UI рендерит /agents список с campaign_count=0 без ошибок | AGNT-04 | Frontend в отдельном репо (Lovable), не покрывается pytest этой phase | Открыть Lovable preview, перейти на /agents, убедиться что отображается список + кнопка «Duplicate» + кнопка «Delete» |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter (after planner aligns task IDs)

**Approval:** pending
