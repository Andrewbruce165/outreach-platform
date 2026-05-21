# Phase 3: Agents (AI Templates) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-22
**Phase:** 03-agents-ai-templates
**Areas discussed:** Финальные колонки Agent, Старая AI-runtime цепочка, Delete & duplicate семантика, Кол-во кампаний в списке

---

## Area 1 — Финальные колонки Agent

### Q1.1: Поля, относящиеся к кампании (auto_pause_triggers, webhook_functions, document_webhook_url)

| Option | Description | Selected |
|--------|-------------|----------|
| Дропнуть всё прямо сейчас | Миграция 015 ALTER TABLE ai_contexts DROP COLUMN auto_pause_triggers, webhook_functions, document_webhook_url. БД чистая — данных нет. В Phase 4 эти поля переедут на campaigns с правильными схемами. | ✓ |
| Оставить, не использовать | Колонки остаются в БД, но новые роутеры/схемы их игнорируют. Drop придёт в Phase 4 вместе с созданием правильных полей на campaigns. | |
| Перенести в Phase 4 вместе с campaigns | Оставить колонки как есть, в Phase 4 миграция перенесёт их на campaigns с правильными именами. Но БД чистая, переносить нечего — это формально равно варианту 1+2. | |

**User's choice:** Дропнуть всё прямо сейчас
**Notes:** Чистка БД делается одной миграцией 015, без накопления tech debt.

### Q1.2: max_message_length и response_delay_seconds — где живут?

| Option | Description | Selected |
|--------|-------------|----------|
| Drop с agent, перенести на campaign (Phase 4) | Дропаем из ai_contexts в миграции 015. В Phase 4 эти лимиты переедут на campaigns. Сейчас никто в новом коде их не читает. | ✓ |
| Оставить на agent как hard cap / default | Сидят на agent как по умолчанию/жёсткий лимит. Кампания может переопределить в своём расписании. | |
| Drop сейчас, без плана на Phase 4 | Drop полностью. Rate limit уже per-sender (D-13 Phase 2). response_delay — логика дебаунса в listener (3-5 мин) — хардкожена и работает. | |

**User's choice:** Drop с agent, перенести на campaign (Phase 4)
**Notes:** Agent — только template (text content). Limits / timing — концерн runtime'а (campaign).

### Q1.3: Маппинг «контекст / задача / тон / FAQ» на колонки

| Option | Description | Selected |
|--------|-------------|----------|
| Контекст→system_prompt, задача→rules, тон→tone, FAQ→faq | Не добавляем новых колонок. Используем существующие 4 поля + faq JSONB. Lovable рендерит 4 текстовых поля + FAQ-редактор. | ✓ |
| Добавить отдельный «task» / «goal» колонку | Новая колонка task TEXT NULLABLE, явно для задачи. system_prompt остаётся под контекст, rules берёт ограничения/запреты. Больше ясности в UI, но новая колонка. | |
| Объединить всё в system_prompt | Дропнуть rules, tone_of_voice, company_info, product_info — всё в system_prompt одним блоком (Markdown). Проще для LLM (один промпт), проще для UI. Но ломает success criterion #1. | |

**User's choice:** Контекст→system_prompt, задача→rules, тон→tone, FAQ→faq
**Notes:** Без новых колонок, переиспользуем существующие.

### Q1.4: company_info и product_info — оставляем или дропаем?

| Option | Description | Selected |
|--------|-------------|----------|
| Оставить оба как отдельные поля | Сохраняем company_info, product_info как TEXT NULLABLE. UI рендерит 6 текстовых полей + faq. Плюс: структура для юзера. | ✓ |
| Дропнуть оба | ALTER TABLE DROP COLUMN company_info, product_info. Юзер пишет это в system_prompt. Минимализм. | |
| Оставить company_info, дроп product_info | company_info полезен как «О компании» (переиспользуемый блок), product_info часто логично в «задаче» для конкретной кампании — дроп. | |

**User's choice:** Оставить оба как отдельные поля
**Notes:** Структурированный ввод важнее минимизма UI.

---

## Area 2 — Старая AI-runtime цепочка

### Q2.1: senders.ai_context_id FK — что делаем?

| Option | Description | Selected |
|--------|-------------|----------|
| DROP COLUMN в миграции 015 | ALTER TABLE senders DROP COLUMN ai_context_id. Рвём отвязку чисто и явно. ORM модель Sender больше не имеет этого поля. | ✓ |
| Оставить nullable, не читать | Колонка остаётся в БД, ORM модель её выпиляет. Drop придёт в Phase 4 миграции вместе с campaigns. | |

**User's choice:** DROP COLUMN в миграции 015
**Notes:** Чистый разрыв связи sender↔agent — соответствует success criterion #2.

### Q2.2: conversations.ai_context_id и context_contact_assignments — что с ними?

| Option | Description | Selected |
|--------|-------------|----------|
| Оставить оба | conversations.ai_context_id и вся таблица context_contact_assignments остаются как есть. Это история и rotation — оба понадобятся в Phase 4/5 (просто писать в них будет campaign-flow). В Phase 3 не трогаем. | ✓ |
| DROP оба сейчас | Полная зачистка. DROP COLUMN conversations.ai_context_id, DROP TABLE context_contact_assignments. В Phase 4 conversations всё равно перерисуется под campaigns. Но ломаем rotation-логику сейчас. | |
| DROP только context_contact_assignments | Оставить conversations.ai_context_id, DROP TABLE context_contact_assignments. | |

**User's choice:** Оставить оба
**Notes:** Сохраняет работу existing AI runtime цепочки (services/queue.py, listener.py, rotation.py) — Phase 3 не ломает рассылку.

### Q2.3: Старые роутеры queue.py / send.py / conversations.py / contexts.py — как переписываем?

| Option | Description | Selected |
|--------|-------------|----------|
| Только contexts.py в Phase 3 | Phase 3 переписывает и регистрирует в main.py только contexts.py (под AuthDep + workspace_id, CRUD + duplicate). queue.py/send.py/conversations.py остаются выпилены из main.py до Phase 4. | |
| contexts.py + queue.py в Phase 3 | Переписываем contexts.py и queue.py (под AuthDep, явный ai_context_id в request). send.py/conversations.py ждут Phase 4. | ✓ |
| Всё в Phase 3 под «временный ai_context_id явно» | Переписываем все роутеры/сервисы. AI всё равно заработает в Phase 3. Но Phase 3 раздувается до рерайта всего бизнес-слоя. | |

**User's choice:** contexts.py + queue.py в Phase 3
**Notes:** n8n продолжает работать через явный ai_context_id в payload отправки.

### Q2.4: Что конкретно в queue.py переписываем?

| Option | Description | Selected |
|--------|-------------|----------|
| Роутер queue (CRUD очереди + send) под AuthDep+ai_context_id | Переписываем app/routers/queue.py или app/routers/send.py под AuthDep + workspace_id + явный ai_context_id в body. n8n сможет шлёпать сообщения. Background QueueWorker и listener.py не трогаем. | ✓ |
| Background QueueWorker (сервис) + AI listener | Без роутеров. Только перевести services/queue.py + services/listener.py + services/rotation.py на новый «agent явно в conversation» паттерн. | |
| Оба пункта выше | И роутеры + background workers переписать. Полный возврат старого AI flow без campaign-сущности. | |

**User's choice:** Роутер queue (CRUD очереди + send) под AuthDep+ai_context_id
**Notes:** Минимальный набор изменений: возврат endpoint'а отправки в main.py + workspace-scoped. Workers (services/) не трогаем.

---

## Area 3 — Delete & duplicate семантика

### Q3.1: Delete агента — soft (is_active=false) или hard?

| Option | Description | Selected |
|--------|-------------|----------|
| Hard delete + TODO блокировки в Phase 4 | DELETE FROM ai_contexts WHERE id=...; conversations.ai_context_id SET NULL (FK уже SET NULL), context_contact_assignments CASCADE. TODO в Phase 4: блокировать если привязан к active campaigns. is_active дропается вообще. | ✓ |
| Soft delete (is_active=false) | PATCH is_active=false. Колонка is_active остаётся. Список фильтрует WHERE is_active=true. История conversations сохраняет ссылку на агента видимо. | |
| Hard delete + блок по ссылкам сейчас | DELETE возвращает 409 Conflict если есть conversations или context_contact_assignments с этим агентом. Юзер подтверждает force=true. Приближено к folders D-06. | |

**User's choice:** Hard delete + TODO блокировки в Phase 4
**Notes:** Чистая семантика — hard delete + FK SET NULL у conversations сохраняет историю с nullable agent, CASCADE у rotation чистит мусор.

### Q3.2: Duplicate агента — как именуем?

| Option | Description | Selected |
|--------|-------------|----------|
| Авто: «{name} (copy)» / «{name} (copy N)» | POST /agents/{id}/duplicate без body. Backend копирует поля, имя = «{original} (copy)» или «{original} (copy 2)» если уже есть. Один клик. | ✓ |
| Юзер явно вводит имя | POST /agents/{id}/duplicate с body {name: ...}. UI показывает prompt перед вызовом. Больше контроля, но два клика. | |

**User's choice:** Авто: «{name} (copy)» / «{name} (copy N)»
**Notes:** Один клик в UI, юзер потом PATCH'ит имя если хочет.

---

## Area 4 — Кол-во кампаний в списке

### Q4.1: campaign_count в API-ответе

| Option | Description | Selected |
|--------|-------------|----------|
| API возвращает campaign_count: 0 | GET /api/v1/agents и /agents/{id} возвращают campaign_count: 0 (хардкод в Phase 3). Lovable рендерит «In 0 campaigns». В Phase 4 хардкод заменяется на реальный COUNT(*) FROM campaigns WHERE agent_id=... — контракт API стабилен для UI. | ✓ |
| Поле вообще не возвращаем | Схема AgentResponse без campaign_count. Lovable рендерит «—». Phase 4 добавляет поле (backward compat OK). | |
| API возвращает usage_count по общему флагу | usage_count = COUNT(*) WHERE conversations.ai_context_id=agent.id. Полезно даже до Phase 4. В Phase 4 добавим отдельное campaign_count. | |

**User's choice:** API возвращает campaign_count: 0
**Notes:** Стабильный контракт с Lovable, заглушка теперь — реальный COUNT в Phase 4.

---

## Claude's Discretion

- C-01: shape JSONB поля faq — массив объектов vs dict (рекомендация — массив).
- C-02: точные имена endpoint'ов и Pydantic-схем.
- C-03: shape AgentUpdate (полный PUT vs partial PATCH).
- C-04: какой именно файл становится entry point отправки — send.py или queue.py.
- C-05: адаптация senders.py под удаление sender.ai_context_id.
- C-06: расширение conftest.py фикстурами.
- C-07: мелкие правки services/queue.py и listener.py чтобы не падали после DROP COLUMN sender.ai_context_id.

## Deferred Ideas

- Реальный campaign_count в Phase 4.
- Блокировка DELETE если есть active campaigns в Phase 4.
- Переезд auto_pause_triggers, webhook_functions, document_webhook_url, max_message_length, response_delay_seconds на campaigns в Phase 4.
- Sender lock per active campaign в Phase 4.
- usage_count и log запросов в OpenAI — Phase 5.
- Перевод senders.role с String(20)+CHECK на SQLEnum — Phase 4 или v2 (не блокер Phase 3).
- Tech debt: Base.metadata.create_all в app/database.py — продолжает висеть.
- v2: soft-delete агентов с deleted_at, версионирование, шаблоны для маркетплейса.
