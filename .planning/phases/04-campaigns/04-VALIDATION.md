---
phase: 4
slug: campaigns
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-22
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (async via pytest-asyncio) |
| **Config file** | `pyproject.toml` / `pytest.ini` (existing) |
| **Quick run command** | `pytest tests/test_campaigns_*.py tests/test_campaign_*.py -x -q` |
| **Full suite command** | `pytest -q` |
| **Estimated runtime** | ~45–90 seconds full suite |

---

## Sampling Rate

- **After every task commit:** Run quick command (subset relevant to plan)
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-* | 01 | 1 | CAMP audit | doc | `cat .planning/phases/04-campaigns/04-01-AUDIT.md` (output artifact) | ❌ W0 | ⬜ pending |
| 04-02-* | 02 | 2 | CAMP-01,02,03,04,16,17 | unit+int | `pytest tests/test_campaigns_model.py tests/test_campaign_router.py tests/test_sender_lock.py -x -q` | ❌ W0 | ⬜ pending |
| 04-03-* | 03 | 2 | CAMP-05,06,07,08 | unit | `pytest tests/test_campaign_schedule.py tests/test_queue_per_campaign_hours.py -x -q` | ❌ W0 | ⬜ pending |
| 04-04-* | 04 | 3 | CAMP-09,10,11 | unit+int | `pytest tests/test_campaign_enqueue_worker.py tests/test_template_render.py tests/test_queue_campaign_id.py -x -q` | ❌ W0 | ⬜ pending |
| 04-05-* | 05 | 4 | CAMP-12,13,14,15 | unit+int | `pytest tests/test_builtin_tools.py tests/test_campaign_webhooks.py tests/test_custom_tools_wiring.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Wave 0 устанавливает тестовые stubs и общие фикстуры перед началом execution каждого плана. Все файлы создаются как пустые модули с `pytest.skip("Wave 0 stub")` в каждом тесте, чтобы pytest collection прошёл.

- [ ] `tests/test_campaigns_model.py` — stubs для CAMP-01 (CRUD), CAMP-02 (agent attach), CAMP-03 (folder attach), CAMP-16 (workspace isolation), CAMP-17 (lifecycle transitions)
- [ ] `tests/test_campaign_router.py` — stubs для POST/GET/PATCH/DELETE /api/v1/campaigns, 409 на DELETE running
- [ ] `tests/test_sender_lock.py` — stubs для CAMP-04 (sender lock на start, conflict detection)
- [ ] `tests/test_campaign_schedule.py` — stubs для CAMP-05 (work_hour_start/end), CAMP-06 (timezone), CAMP-07 (work_days_mask), CAMP-08 (start_date/stop_date)
- [ ] `tests/test_queue_per_campaign_hours.py` — stubs замены глобального _is_working_hours на per-campaign
- [ ] `tests/test_campaign_enqueue_worker.py` — stubs для CampaignEnqueueWorker tick (CAMP-09 досыпание, CAMP-10 enqueue из folder contacts)
- [ ] `tests/test_template_render.py` — stubs для render_template ({{name}}, {{username}}, {{phone}}, {{source}}, {{custom.X}}, {{имя}} алиас, empty fallback) — CAMP-11
- [ ] `tests/test_queue_campaign_id.py` — stubs для message_queue.campaign_id NOT NULL FK SET NULL (или NULLable, см. Open Q1 RESEARCH.md)
- [ ] `tests/test_builtin_tools.py` — stubs для CAMP-12 (transfer_to_manager), CAMP-13 (finish_conversation), mark_as_lead (CAMP-14 параллельное событие). Tool-call dispatch, conversation.status UPDATE, ai_enabled flip, parallel tool_calls
- [ ] `tests/test_campaign_webhooks.py` — stubs для CAMP-14 (3 отдельных webhook URL: lead/handoff/finish), payload shape, fire-and-forget
- [ ] `tests/test_custom_tools_wiring.py` — stubs для CAMP-15 (custom tools через campaigns.tools JSONB, переезд с дропнутого ai_contexts.webhook_functions)
- [ ] `tests/conftest.py` — расширение существующих фикстур: `campaign_factory`, `running_campaign_factory`, `campaign_with_senders`, `campaign_with_contacts`, `mock_openai_tool_call_response`, `webhook_capture_server`
- [ ] `tests/utils/openai_mocks.py` — helper для генерации OpenAI tool_call response payload (built-in vs custom tool_call mocks)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Lovable UI рендерит campaign-форму корректно | CAMP-01 UI side | UI в отдельном репо (Lovable), Phase 4 — только API contract | После execute: дёрнуть POST /api/v1/campaigns руками через curl/Postman с full payload (agent_id, folder_id, senders[], timezone, work_hours, all webhooks, tools, trigger_hints), убедиться 201 и GET возвращает то же |
| Реальный Telegram-аккаунт получает сообщение с подставленными переменными | CAMP-10, CAMP-11 | Telethon отправляет в реальный TG — нельзя автотестировать без живых credentials | После execute: создать кампанию с message_template='Привет, {{name}}!', 1 контактом (свой тестовый TG-номер), запустить, убедиться что приходит «Привет, <моё имя>!» |
| LLM реально вызывает built-in tool по trigger_hint | CAMP-12, CAMP-13 | OpenAI inference — non-deterministic; интеграционно нужны реальные OpenAI API-ключи | После execute: установить trigger_hint='Когда клиент явно подтвердил готовность купить', отправить сообщение «согласен, покупаю», убедиться что conversation.status='lead' + webhook вызван |
| Sender lock при concurrent /start race | CAMP-04 | TOCTOU race возможен в production при высоком concurrency — единичный тест не доказывает absence | После execute: вручную или k6/locust: 2 параллельных POST /campaigns/{id1}/start и /start{id2} с пересекающимся sender, убедиться что одна получает 201 и одна 409 (или принять acceptable race per RESEARCH.md Pitfall 4) |
| Webhook payload получается корректно во внешний приёмник | CAMP-14 | Внешний URL — это интеграция с n8n / клиентским сервисом | После execute: настроить webhook URL на webhook.site, спровоцировать lead/handoff/finish, проверить визуально что payload содержит event_type, campaign_id, conversation_id, contact, reason, timestamp |
| Корректно отображается «sender занят кампанией X» в UI | CAMP-04 UI hint | UI в Lovable | GET /api/v1/campaigns/{id} возвращает в `available_senders` массив с полем `locked_by_campaign_id` — проверить руками что Lovable рендерит warning |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
