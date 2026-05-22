# Phase 4: Campaigns - Context

**Gathered:** 2026-05-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 4 строит **Campaign** — первичную бизнес-сущность outreach-платформы, объект-обёртку над рассылкой, который связывает четыре существующих кирпича (agent / TG-аккаунты / папка контактов / расписание) и добавляет три новых (signals / webhook / tools). Campaign — это единица:

- **owners** очереди (`message_queue.campaign_id`)
- **owners** диалогов (`conversations.campaign_id`)
- **owners** rotation-state (`campaign_contact_assignments`)
- **scope** аналитики (Phase 5 ANLX-02)
- **filter** для inbox (Phase 5 INBX-05)
- **источник** расписания (заменяет хардкод 09–20 МСК в `queue.py`)
- **источник** webhook + function calling (переезжает с дропнутого `ai_contexts.webhook_functions` в Phase 3)

**В скоупе:**

1. **Миграция `016_phase4.sql`** — добавляет таблицы и колонки:
   - `campaigns` (workspace_id, agent_id NOT NULL, folder_id NOT NULL, status enum, timezone, work_hour_start/end, work_days_mask, start_date NULL, stop_date NULL, lead_webhook_url, handoff_webhook_url, finish_webhook_url, tools JSONB, lead_trigger_hint TEXT, handoff_trigger_hint TEXT, finish_trigger_hint TEXT, message_template TEXT — текст с {{name}} плейсхолдерами).
   - `campaign_senders` (PK `campaign_id, sender_id`) — through-table для CAMP-04 lock.
   - `campaign_contact_assignments` (workspace_id, campaign_id, contact_phone, sender_id) с UNIQUE(campaign_id, contact_phone) — заменяет `context_contact_assignments`.
   - `conversations.campaign_id` — NULLable FK ON DELETE SET NULL.
   - `message_queue.campaign_id` — NOT NULL FK ON DELETE SET NULL (БД чистая — sentinel-кампанию заводить не нужно, NOT NULL применим сразу).
   - DROP TABLE `context_contact_assignments` (заменяется на campaign-уровневую).

2. **Новый роутер `app/routers/campaigns.py`** под `AuthDep` + workspace-scope:
   - `POST/GET/PATCH/DELETE /api/v1/campaigns` — CRUD (DELETE → 409 если status='running').
   - `POST /api/v1/campaigns/{id}/start` — переход draft|paused → running с derived sender-lock check (CAMP-04).
   - `POST /api/v1/campaigns/{id}/pause` / `/resume` / `/finish` — переходы статусов.
   - `POST /api/v1/campaigns/{id}/duplicate` — копия без queue items (опционально, planner оценит).
   - Все ответы включают computed `is_exhausted: bool` (D-04).

3. **Новый background worker `CampaignEnqueueWorker`** (`app/services/campaign_enqueue.py`) в lifespan API-контейнера — генерирует queue items из контактов папки + обеспечивает досыпание (CAMP-09). Pattern: ContactCheckWorker из Phase 2.

4. **`app/services/queue.py` — выпиливание глобальных захардкоженных констант** `MOSCOW_TZ`, `WORK_HOUR_START`, `WORK_HOUR_END` (строки 62–65 в текущем коде, CONCERNS.md). Замена `_is_working_hours()` / `_next_working_window()` на per-campaign логику (читает `campaigns.timezone`, `work_hour_start`, `work_hour_end`, `work_days_mask`, `stop_date` через JOIN message_queue → campaigns). Эмпирические `MIN_SEND_INTERVAL`, `LONG_PAUSE_*`, `FLOOD_HARD_THRESHOLD` — НЕ ТРОГАЕМ (CLAUDE.md).

5. **`app/routers/send.py` — рерайт под campaign_id**: в body `POST /api/v1/send` вместо `ai_context_id` теперь `campaign_id` (agent выводится из campaign). Workspace API-key push (n8n) продолжает работать тем же endpoint'ом.

6. **`app/services/ai_engine.py` + `app/services/listener.py` — переподключение к Campaign**:
   - `get_context()` (ai_engine.py:50) читает `tools`, `lead/handoff/finish_trigger_hint`, `lead/handoff/finish_webhook_url` из `campaigns` через `conversations.campaign_id` JOIN (вместо хардкод `[]` после Phase 3 D-01).
   - В LLM-prompt добавляются 3 built-in tools: `mark_as_lead(reason)`, `transfer_to_manager(reason)`, `finish_conversation(reason)` — с `description` из соответствующих trigger_hint полей кампании.
   - При срабатывании built-in tool: UPDATE `conversations.status` ('lead'/'handoff'/'finished') + `ai_enabled=false` (для handoff/finish) + POST webhook + ничего в LLM не возвращается как tool result (диалог закрыт).
   - Custom tools (CAMP-15) — через существующий `build_tools` / `execute_webhook` (ai_engine.py:165, 201), источник — `campaigns.tools` JSONB (не `webhook_functions`).
   - Полный document_webhook_url (listener.py:704, дропнут в Phase 3) — НЕ восстанавливается. Если клиент хочет принимать документы наружу — это часть `tools` (custom webhook function с file параметром).

7. **Variable substitution** (`app/services/template.py`, новый утилитарный модуль):
   - `render_template(template: str, contact: dict) -> str` — Mustache-style `{{name}}`, `{{username}}`, `{{phone}}`, `{{source}}`, `{{custom.X}}`.
   - Missing → empty string + `logger.warning`.
   - Вызывается из `CampaignEnqueueWorker` при INSERT в `message_queue.message_text`.

8. **TODO(phase-4) метки закрываются** во всех точках кодовой базы (см. `<canonical_refs>` ниже):
   - `ai_engine.py:88` (webhook_functions → campaigns.tools) ✓
   - `listener.py:707` (document_webhook_url) — не восстанавливаем, см. #6 выше
   - `queue.py:708` (ai_context_id from conversation.campaign_id) — теперь conv имеет campaign_id напрямую
   - `routers/agents.py:246` (block DELETE при active campaign) — добавляем проверку через `campaigns` table
   - `routers/agents.py:64` (campaign_count hardcoded) — реальный SELECT COUNT(*) FROM campaigns WHERE agent_id=...
   - `routers/folders.py:248` (block delete при active campaign) — добавляем проверку
   - `routers/senders.py:82` (sender больше не «знает» agent) — связь через campaigns.agent_id ↔ campaign_senders.sender_id

9. **REQUIREMENTS.md update** (D-13): CAMP-14 переписать с "один webhook URL" на "три отдельных webhook URL (lead / handoff / finish)" — фиксируется при commit'е CONTEXT.md.

**Не в скоупе:**

- Inbox UI, инбокс-фильтры по кампании, аналитика (ANLX-01..05), LLM request log на conversation — Phase 5.
- AI-фильтр системных ботов (AIRC-04) — Phase 5 (но существующий `_handle_antispam_signal` в `listener.py:813` остаётся работать).
- Admin Master Bot уведомления (ADMN-01..03) — Phase 6.
- Многошаговые follow-up (ADVN-01) — v2.
- A/B тесты текстов или агентов (ADVN-02) — v2 (поэтому в Phase 4 1 agent на campaign, 1 folder на campaign).
- Тайм-зоны контакта (ADVN-03) — v2 (в Phase 4 timezone per-campaign, не per-contact).
- Несколько папок / несколько агентов / несколько message templates в одной кампании — v2.
- Strict-mode variable substitution (error при отсутствии переменной) — v2; в Phase 4 empty fallback (D-19).
- Multi-window расписание (например 09–12 + 14–18) — v2; в Phase 4 одно окно (D-09).
- `senders.role` String(20)+CHECK → SQLEnum — деферрено из Phase 2/3; planner Phase 4 может включить как эстетическую правку либо оставить.

</domain>

<decisions>
## Implementation Decisions

### Campaign модель и связи

- **D-01:** 1 agent на campaign. Колонка `campaigns.agent_id UUID NOT NULL FK ai_contexts(id) ON DELETE RESTRICT` (защита от потери агента при наличии running кампании; для draft/paused/done кампаний при DELETE агента → 409 с ссылкой на кампании — уже задано Phase 3 D-09 TODO). Несколько агентов в одной кампании (A/B) — v2 ADVN-02.

- **D-02:** 1 папка контактов на campaign. Колонка `campaigns.folder_id UUID NOT NULL FK folders(id) ON DELETE RESTRICT`. Phase 2 D-06 уже имеет TODO «block delete folder если есть active campaign» — здесь его реализуем: DELETE folder → 409 если есть `SELECT 1 FROM campaigns WHERE folder_id=? AND status='running'`. Для draft/paused/done — позволить delete folder с CASCADE на contacts (Phase 2 D-06 `?force=true`).

- **D-03:** Sender lock per active campaign — через **through-table + derived check**, не физический FK на sender:
  - Таблица `campaign_senders(campaign_id UUID FK CASCADE, sender_id UUID FK CASCADE, workspace_id UUID NOT NULL, added_at TIMESTAMPTZ, PRIMARY KEY (campaign_id, sender_id))`.
  - Sender может быть прикреплён к нескольким draft / paused / done кампаниям. Lock наступает при `POST /campaigns/{id}/start`: проверяем `SELECT cs.sender_id, c.id FROM campaign_senders cs JOIN campaigns c ON c.id=cs.campaign_id WHERE cs.sender_id = ANY(:requested_senders) AND c.status='running' AND c.id != :starting_campaign`. Если есть — 409 с детализацией конфликтных пар `[{sender_id, campaign_id, campaign_name}]`.
  - UI на форме создания кампании при выборе senders показывает статус «занят кампанией X» (read-only поле в GET response).
  - Plus: history sender'а в разных кампаниях остаётся как есть (через campaign_senders), не теряется при stop+restart другой кампании.

- **D-04:** Lifecycle статусов — **100% manual, done terminal**:
  - Состояния: `draft`, `running`, `paused`, `done` (SQLEnum, не String — Phase 2 D-13 pattern для новых enum-полей).
  - Переходы:
    - `draft → running` (POST /start, требует agent + folder + ≥1 sender + walks sender lock check).
    - `running → paused` (POST /pause).
    - `paused → running` (POST /resume, требует sender lock re-check — sender мог быть взят другой кампанией пока эта стояла).
    - `running → done` (POST /finish).
    - `paused → done` (POST /finish).
    - `done` — terminal, никаких переходов обратно. Чтобы перезапустить — POST /duplicate.
    - `draft → done` — недопустимо (только через running или paused).
  - В API ответе `GET /campaigns/{id}` всегда есть computed `is_exhausted: bool` = true, если `SELECT COUNT(*) FROM contacts WHERE folder_id = ? AND id NOT IN campaign_contact_assignments` = 0 И `SELECT COUNT(*) FROM message_queue WHERE campaign_id = ? AND status IN ('pending','processing')` = 0. UI рендерит подсказку «Mark as done?» когда `is_exhausted && status='running'`.
  - Никаких background-тиков, переводящих статусы автоматически — Phase 2 D-12 lifecycle pattern (юзер сам нажимает кнопки).

- **D-05:** `conversations.campaign_id` — NULLable FK ON DELETE SET NULL. Заполняется CampaignEnqueueWorker'ом при INSERT первого item'а кампании в queue (D-17) — а conversation сам создаётся при первой отправке в queue.py:691 уже сейчас (`INSERT INTO conversations ... RETURNING id`). Расширяем этот INSERT на `campaign_id = :cid` (берётся из `message_queue.campaign_id`). Для legacy входящих от незнакомых (не из кампании) и для conversation, созданных в Phase 3 до миграции — `NULL`. Phase 5 inbox фильтрует `WHERE campaign_id = :id OR campaign_id IS NULL` в зависимости от UI-режима.

- **D-06:** Rotation per-campaign — **drop + create новую таблицу**:
  - DROP TABLE `context_contact_assignments`.
  - CREATE TABLE `campaign_contact_assignments (id UUID PK, workspace_id UUID FK NOT NULL, campaign_id UUID FK CASCADE NOT NULL, contact_phone VARCHAR(20) NOT NULL, sender_id UUID FK CASCADE NOT NULL, created_at TIMESTAMPTZ, UNIQUE(campaign_id, contact_phone))`.
  - `services/rotation.py:get_or_assign_sender()` обновляется: вместо `context_id` принимает `campaign_id` (или resolveит campaign из conversation, если зовётся из листенера). Подбор sender'а — из `campaign_senders` (не глобально из workspace senders). Если в кампании ≥1 sender активен (`auth_status='ok' AND lifecycle_status='active'`) — выбираем round-robin / least-loaded (planner подберёт). Если все senders кампании выпали — пытаемся переподобрать на лету с обновлением assignment row.
  - БД чистая (Phase 1 D-01) — DROP TABLE без backfill.

- **D-07:** DELETE /campaigns/{id} — **hard delete с 409 на running**:
  - `running` → 409 `{code: "CAMPAIGN_RUNNING", message: "Stop campaign before deleting"}`.
  - `draft` / `paused` / `done` → 204, hard delete.
  - FK на удаление: `campaign_senders` CASCADE (связь бессмысленна), `campaign_contact_assignments` CASCADE (rotation state привязан), `conversations.campaign_id` SET NULL (история диалогов сохраняется), `message_queue.campaign_id` SET NULL (история очереди сохраняется — items с finished_at в прошлом не трогаем).
  - Pattern из Phase 3 D-08 (hard delete + SET NULL на исторические связи).

### Schedule

- **D-08:** **Timezone per-campaign**. Колонка `campaigns.timezone TEXT NOT NULL DEFAULT 'Europe/Moscow'`. Валидация на API: значение должно резолвиться через `zoneinfo.ZoneInfo(...)` (raise 422 если нет). Хранится IANA-имя зоны. Закрывает CONCERNS.md «Hardcoded Moscow timezone для working hours» — Phase 4 выпиливает глобальные `MOSCOW_TZ`, `WORK_HOUR_START/END` из `queue.py:62-65` и `warmup.py` (warmup'у timezone не нужна — он внутренний между senders'ами одного workspace, остаётся global или wsp-level).

- **D-09:** **Одно окно** рабочих часов. Колонки `campaigns.work_hour_start INT NOT NULL DEFAULT 9` (0-23) и `campaigns.work_hour_end INT NOT NULL DEFAULT 20` (1-24, не включительно). CHECK constraint `work_hour_start < work_hour_end`. Multi-window deferred to v2.

- **D-10:** **Дни недели — INT bitmask** `campaigns.work_days_mask INT NOT NULL DEFAULT 31` (Mo=1, Tu=2, We=4, Th=8, Fr=16, Sa=32, Su=64 — Mo-Fri = 31). CHECK `work_days_mask BETWEEN 1 AND 127`. Проверка: `(work_days_mask & (1 << datetime.now(tz).weekday())) != 0`. UI рендерит 7 чекбоксов.

- **D-11:** **Stop_date soft skip**:
  - `campaigns.start_date TIMESTAMPTZ NULLABLE`, `campaigns.stop_date TIMESTAMPTZ NULLABLE`.
  - `start_date`: CampaignEnqueueWorker не вставляет в queue items раньше `start_date` (либо вставляет с `scheduled_at = MAX(now, start_date)`).
  - `stop_date`: queue worker пропускает items с `NOW() >= campaign.stop_date` — помечает как `status='failed', error_message='past_stop_date'`. Уже стартовавшие диалоги (conversations) продолжают жить, AI отвечает на входящие (логика как D-15: pause не молчит). Campaign status остаётся `running` (или `paused`) — юзер вручную пометит done после того как удостоверится в is_exhausted.
  - Не делаем background-тик «auto-pause при stop_date» — это нарушает D-04 (100% manual lifecycle).

### Signals + webhook + tools

- **D-12:** **Сигналы детектируются через LLM tool call** — 3 built-in tools, добавляются к custom tools (CAMP-15) при каждом вызове `ai_engine.generate_response()`:
  - `mark_as_lead(reason: string)` — lead identified.
  - `transfer_to_manager(reason: string)` — manager handoff requested.
  - `finish_conversation(reason: string)` — conversation closed.
  - Описание (description) каждого built-in tool'а собирается из соответствующего поля кампании: `lead_trigger_hint`, `handoff_trigger_hint`, `finish_trigger_hint` (TEXT, NULLable). Пример: hint='Когда клиент явно подтвердил готовность купить или согласился на встречу' → tool description = 'Mark contact as a qualified lead. Use when: {hint}'. Если hint NULL — generic default description.
  - Built-in tools идут **перед** custom tools в массиве `tools` параметра OpenAI API (порядок важен только для логов — OpenAI tool-call dispatch не зависит от порядка).
  - Когда LLM вызывает built-in tool:
    - `mark_as_lead` → UPDATE conversations SET status='lead'. AI продолжает работать (lead — это маркер, не конец диалога). POST на `campaigns.lead_webhook_url`.
    - `transfer_to_manager` → UPDATE conversations SET status='handoff', ai_enabled=false, paused_at=NOW(), paused_reason=reason. POST на `campaigns.handoff_webhook_url`. AI больше не отвечает в этом диалоге (соответствует CAMP-12 «передать на менеджера»).
    - `finish_conversation` → UPDATE conversations SET status='finished', ai_enabled=false, paused_at=NOW(), paused_reason=reason. POST на `campaigns.finish_webhook_url`. AI больше не отвечает (CAMP-13).
  - В ответе LLM tool result не возвращается (для built-in) — событие зафиксировано и веб-хук вызван; в чат может уйти финальная фраза которую LLM сгенерировал параллельно с tool call (если text_content есть в response).
  - Хорошо переиспользует существующий поток `ai_engine.generate_response()` (строка 267) который уже умеет обрабатывать tool_calls (строка 327).

- **D-13:** **Три отдельных webhook URL** — расхождение с REQUIREMENTS.md CAMP-14 «один webhook»:
  - `campaigns.lead_webhook_url TEXT NULLABLE`
  - `campaigns.handoff_webhook_url TEXT NULLABLE`
  - `campaigns.finish_webhook_url TEXT NULLABLE`
  - Любой из них может быть NULL — тогда событие просто не вызывает webhook (но conversation.status всё равно обновляется).
  - Payload (одинаковый для всех 3 типов): `{event_type, campaign_id, campaign_name, conversation_id, contact: {phone, name, telegram_id, source, custom}, reason: string, message_history_excerpt: array, timestamp}`. Конкретный shape — Claude's Discretion (C-01).
  - Fire-and-forget httpx — паттерн `_fire_callback` в `queue.py:731` (existing). Не блокирует AI-response.
  - **REQUIREMENTS.md CAMP-14 будет обновлён** на «3 отдельных webhook URL» в составе коммита CONTEXT.md.
  - Также добавляется поле `campaigns.tool_webhook_default_url TEXT NULLABLE` (опционально, planner оценит) — fallback для custom tools у которых в их spec не указан собственный webhook_url.

- **D-14:** **Tools spec переезжает as-is** — `campaigns.tools JSONB NOT NULL DEFAULT '[]'`. Shape тот же что был в дропнутой `ai_contexts.webhook_functions` (см. `ai_engine.build_tools()` в строке 165): массив объектов `{name: string, description: string, parameters: OpenAI function-call JSON schema, webhook_url: string, webhook_method: 'POST' | 'GET' (default POST)}`. Существующие `ai_engine.build_tools()` и `ai_engine.execute_webhook()` (строки 165, 201) переиспользуются без изменений в API — меняется только источник чтения (через JOIN на campaigns вместо чтения колонки ai_contexts.webhook_functions). Plan 04-01 (audit) перепроверит точный shape по живому коду и зафиксирует JSON schema в Pydantic-моделях.

- **D-15:** **Pause = только отправка, AI продолжает отвечать**:
  - `campaigns.status='paused'` — queue worker SKIP'ает items этой кампании (`WHERE campaign_id NOT IN (SELECT id FROM campaigns WHERE status='paused' OR status='done')` в SELECT очереди).
  - Listener / `ai_engine.generate_response()` НЕ проверяет campaign.status — AI отвечает на входящие, если `conversations.ai_enabled=true`. Логика: «кому уже написал — продолжаешь линию».
  - Full freeze: юзер вручную в Phase 5 inbox флипает `ai_enabled=false` на конкретные conversations (INBX-04) ИЛИ настраивает handoff_trigger_hint так чтобы LLM сам передал на менеджера.
  - Соответствует D-11 stop_date soft semantics.

### Очередь, досыпание, переменные

- **D-16:** **`message_queue.campaign_id` — NOT NULL FK ON DELETE SET NULL**:
  - БД чистая (Phase 1 D-01) — NOT NULL применим сразу без backfill.
  - ON DELETE SET NULL — позволяет hard delete done кампаний без потери queue history (D-07).
  - `/api/v1/send` endpoint (текущий `app/routers/send.py` Phase 3) **рерайт**: в body требуется `campaign_id` вместо `ai_context_id`. Agent выводится через `SELECT agent_id FROM campaigns WHERE id=:cid AND workspace_id=:wid`. Workspace API-key push (n8n) продолжает работать тем же endpoint'ом — n8n должен передавать `campaign_id` в payload. Существующие n8n-flow клиентов AGS Foods остаются в legacy `/root/apps/telegram-api/` (PROJECT.md context) — новых endpoints с обратной совместимостью не делаем.
  - Если клиент хочет direct push без расписания — создаёт кампанию "Direct" с work_hour_start=0, work_hour_end=24, work_days_mask=127 (24/7).
  - Composite index `(workspace_id, campaign_id, status, scheduled_at)` для эффективных queue-tick'ов.

- **D-17:** **CampaignEnqueueWorker** — новый background worker в lifespan API-контейнера (паттерн ContactCheckWorker из Phase 2):
  - Tick каждые ~30 сек (env-конфигурируемый, default 30).
  - Цикл:
    1. SELECT `running` campaigns. Для каждой:
    2. SELECT contacts из `folders.id = campaign.folder_id` где (a) `tg_status='registered'` (D-20 Phase 2 — не slать в unchecked/not_registered), (b) `contact_phone NOT IN (SELECT contact_phone FROM campaign_contact_assignments WHERE campaign_id=:cid)`. LIMIT N (например 500 per tick per campaign — конфигурируемо).
    3. Для каждого контакта: вызываем `services/rotation.get_or_assign_sender(campaign_id, contact_phone)` — выбирает sender из campaign_senders, INSERT в campaign_contact_assignments (UNIQUE constraint защищает от race).
    4. `render_template(campaign.message_template, contact)` → final text (variable substitution на enqueue, D-18).
    5. Bulk INSERT в `message_queue`: `(workspace_id, campaign_id, sender_id, recipient_phone, recipient_name, message_text, scheduled_at, status='pending')`. `scheduled_at = MAX(now, campaign.start_date)`.
  - Досыпание (CAMP-09): когда юзер добавляет контакт в папку, следующий tick'а worker'а через 30 сек подхватит и сделает enqueue. Без LISTEN/NOTIFY, без триггеров. Acceptable lag для outreach (юзер не ожидает что секунда после CSV-импорта = первое сообщение уйдёт).
  - Concurrency: SKIP LOCKED не нужен для SELECT campaigns (workers — один процесс API-контейнера, не horizontal-scale в v1). Если в v2 будет horizontal-scale — добавим `FOR UPDATE SKIP LOCKED` на SELECT campaigns row (паттерн ContactCheckWorker Phase 2.1).
  - Singleton instance в module: `campaign_enqueue_worker = CampaignEnqueueWorker()`, `start()` / `stop()` в FastAPI lifespan (по аналогии с QueueWorker, WarmupWorker, ContactCheckWorker).

- **D-18:** **Variable substitution на enqueue**, не на send:
  - `CampaignEnqueueWorker` вызывает `services/template.render_template(template, contact)` → final text. INSERT в `message_queue.message_text` уже подставленным.
  - queue worker (`services/queue.py:_process_next_for_sender`) НЕ знает о шаблонах — просто берёт `message_text` и отправляет.
  - Плюсы: (a) Phase 5 inbox показывает фактически отправленный текст, (b) не нужно дёргать contact на каждой send-операции, (c) при delete контакта queue item остаётся валидным.
  - Минус (приемлемый): если контакт обновил `full_name` или `custom.X` между enqueue и send (между tick'ами worker'а и собственно отправкой), используется значение, которое было на момент enqueue. Это логично — «сообщение запланировано».

- **D-19:** **`{{name}}` Mustache-style + empty fallback**:
  - Поддерживаемые переменные: `{{name}}` (= contact.full_name), `{{username}}` (= contact.username с префиксом `@` если есть), `{{phone}}` (= contact.phone), `{{source}}` (= contact.source), `{{custom.X}}` (= contact.custom.get(X, '')).
  - PROJECT.md и REQUIREMENTS.md уже используют синтаксис `{{имя}}` — поддерживаем русские алиасы как маппинг: `{{имя}}`→name, `{{компания}}`→custom.company (если в CSV колонка «компания» замаплена в `custom.company` — Phase 2 D-07). Алиасы — Claude's Discretion (C-02): planner подберёт точную таблицу алиасов под Lovable UI.
  - Отсутствующая переменная или ключ → empty string + `logger.warning(f"Template variable {{X}} missing for contact {phone}, campaign {cid}")`. Не блокирует отправку (юзер видит товар отправленным, в inbox увидит пустоту вместо имени — это даёт быстрый фидбэк "у меня в CSV не оказалось имени, поправил, послал заново").
  - Strict mode (error при отсутствии) — v2.
  - Regex `r"\{\{\s*([a-zа-я_]+(\.[a-z_0-9]+)?)\s*\}\}"` для нечувствительности к пробелам внутри `{{ name }}` (Claude's Discretion C-03).

### Claude's Discretion

- **C-01:** Точный shape webhook payload (D-13) — какие именно поля включать (message_history_excerpt — сколько последних сообщений? в каком формате?), нужен ли HMAC signature header. Planner подберёт.
- **C-02:** Точная таблица алиасов переменных (русск/англ для `{{name}}` / `{{имя}}` и др., D-19) — synchronize с Lovable UI. Planner подберёт.
- **C-03:** Regex для парсинга `{{...}}` (D-19) — допускать ли пробелы внутри (`{{ name }}`), допускать ли пайпы как Mustache filters (`{{name | upper}}`) или strictly `{{var}}`. Рекомендация: strict без filters, с пробелами внутри.
- **C-04:** Built-in tool names (D-12) — `mark_as_lead` / `transfer_to_manager` / `finish_conversation` могут быть переименованы под convention OpenAI (snake_case OK, но точные имена planner может уточнить).
- **C-05:** Точные имена endpoint'ов и Pydantic-схем (`CampaignCreate`, `CampaignResponse`, `CampaignStatusUpdate` и т.д.) — planner подберёт под существующие конвенции `app/schemas/__init__.py`.
- **C-06:** Composite indexes на `campaigns` (status partial index `WHERE status='running'`), `message_queue` (composite `(workspace_id, campaign_id, status, scheduled_at)`), `campaign_contact_assignments` (`(campaign_id, contact_phone)` уже UNIQUE) — planner оценит реальные запросы и подберёт.
- **C-07:** Распределение фич по 5 планам ROADMAP (04-01 audit / 04-02 model / 04-03 schedule / 04-04 queue / 04-05 signals): возможно фоллдинг 04-03 schedule в 04-02 model (schedule = +4 поля в campaigns). Planner решит, нужно ли держать отдельный plan'ом 04-03 или раздавать поля по другим планам. Если 04-01 audit покажет, что webhook_functions shape в коде сильно разошёлся с D-14 — может потребоваться отдельный plan 04-01.5 на чистку.
- **C-08:** `senders.role` String(20)+CHECK → SQLEnum (деферрено из Phase 2 D-21 / Phase 3 deferred) — planner может включить как мелкую правку в Plan 04-02 (миграция 016) либо снова отложить. Не блокер.
- **C-09:** `app/database.py` `Base.metadata.create_all` (Phase 1 C-04, Phase 2/3 carry-over) — всё ещё нерешён. Planner Phase 4 может закрыть либо отложить.
- **C-10:** Точная shape поля `campaigns.tools` Pydantic-схемы (D-14) — JSON schema валидация на API-уровне (POST /campaigns принимает только валидный shape) либо raw JSONB. Рекомендация: pydantic-валидация shape (имя, description, parameters, webhook_url, webhook_method).
- **C-11:** Семантика `POST /campaigns/{id}/duplicate` — копировать campaign_senders? copy queue items? Рекомендация: копировать campaigns row + campaign_senders. НЕ копировать queue items и НЕ копировать campaign_contact_assignments (дубликат — это новая кампания с тем же шаблоном).
- **C-12:** Точная shape `lifecycle_status` транзит-логирования (для audit log будущего, ANLX-05 Phase 5). В Phase 4 — не вводим audit log, planner может оставить TODO.
- **C-13:** Conversation.status enum новые значения `'lead'`, `'handoff'`, `'finished'` — добавляются к существующим `'active'`, `'manual'`, `'paused'` (в models/__init__.py:230 сейчас `String(20)` без CHECK). Phase 4 может либо расширить String + CHECK, либо превратить в SQLEnum.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level
- `CLAUDE.md` — главные правила: raw SQL миграции 016_+, async everywhere, общение на русском, **НЕ трогать** rate-limit/debounce/long-pause/flood-threshold интервалы в `queue.py` (эмпирически подобраны), AI-engine и rotation остаются в legacy режиме там, где не блокируют переключение.
- `.planning/PROJECT.md` — Key Decisions: Campaign как первичная сущность (рассматривается здесь), agent отвязан от sender (Phase 3 done), webhook/tools принадлежат кампании (Phase 4 переезд), расписание per-campaign (заменяет хардкод 09–20 МСК), сигналы на уровне кампании передаются в LLM-prompt вместе с агентским контекстом.
- `.planning/REQUIREMENTS.md` §"Campaigns (Phase 4)" — CAMP-01..17 (17 требований). **CAMP-14 будет обновлён** на «3 отдельных webhook URL: lead / handoff / finish» в составе коммита (D-13).
- `.planning/ROADMAP.md` §"Phase 4: Campaigns" — Success Criteria (6 пунктов) и состав плана (5 plan'ов: 04-01 audit, 04-02 model+lifecycle, 04-03 schedule+start/stop, 04-04 queue rewrite, 04-05 signals+webhook+tools). C-07 предупреждает что 04-03 может фоллдиться в 04-02.

### Phase 1 / 2 / 3 контекст (must read)
- `.planning/phases/01-workspace-foundation/01-CONTEXT.md` — D-01 (БД чистая, без backfill), D-04 (workspace isolation pattern `.where(workspace_id == ctx.workspace_id)` + TODO(v2-rls) метки), D-11..D-14 (AuthDep / AuthCtx / dual auth JWT+API-key), D-15 (`app/services/` не трогался — AI-engine и queue работают в legacy с минимальными правками).
- `.planning/phases/02-tg-accounts-contacts/02-CONTEXT.md` — D-04 (контакт в одной папке, move-operation), D-06 (DELETE folder с проверкой active campaign — **здесь реализуем**), D-13 (rate limits per-sender — Phase 4 НЕ дублирует на campaign), D-20 (контакты с tg_status='registered' / 'pending' / 'unchecked' — CampaignEnqueueWorker D-17 фильтрует только 'registered'), D-21 (senders.role String+CHECK — деферрено в Phase 4 C-08).
- `.planning/phases/02.1-worker-hardening` — CR-01..09 BLOCKER findings и их fixes. Phase 4 продолжает паттерны (workspace_id в каждом INSERT, FOR UPDATE SKIP LOCKED — если будет horizontal-scale в v2).
- `.planning/phases/03-agents-ai-templates/03-CONTEXT.md` — D-01 (миграция 015 дропнула `webhook_functions`, `document_webhook_url`, `auto_pause_triggers`, `max_message_length`, `response_delay_seconds`, `is_active` из `ai_contexts` — **в Phase 4 переезжают на campaigns**), D-04 (`senders.ai_context_id` дропнута — связь sender↔agent через campaigns в Phase 4), D-05 (`context_contact_assignments` остаётся в Phase 3, **в Phase 4 drop+create как campaign_contact_assignments**), D-09 (`TODO(phase-4): block DELETE agent при active campaign attachment` — **здесь реализуем**), D-10 (`campaign_count: 0` хардкод — **здесь заменяется реальным SELECT COUNT**).

### Codebase intel
- `.planning/codebase/ARCHITECTURE.md` — слойная разбивка router→service→data; новый роутер `campaigns.py` живёт под `app/routers/`; CampaignEnqueueWorker — под `app/services/` рядом с QueueWorker / WarmupWorker / ContactCheckWorker.
- `.planning/codebase/STRUCTURE.md` — миграции `migrations/016_*.sql`, новые роутеры `app/routers/campaigns.py`, новый сервис `app/services/campaign_enqueue.py` + `app/services/template.py` (для variable substitution).
- `.planning/codebase/CONCERNS.md` — главные tech-debt, которые Phase 4 закрывает: **Hardcoded Moscow timezone** (D-08+D-09 выпиливают), **Duplicate send resolution logic** (рерайт `send.py` под campaign_id), **DEFAULT_SYSTEM_PROMPT references AGS Foods** (НЕ трогаем в Phase 4 — clean-up в отдельной фазе), **OpenAI model name hardcoded as non-existent model ID** (НЕ трогаем — отдельный bug, см. CONCERNS.md «Known Bugs»; Phase 4 ассумит работу с любым валидным моделем через `app/config.py`).
- `.planning/codebase/INTEGRATIONS.md` — Telethon abstraction (НЕ трогаем), AI engine (touch points: `get_context` source switch from ai_contexts to campaigns).

### Существующий код (читать перед изменением)
- `app/models/__init__.py` — добавляются `Campaign`, `CampaignSender`, `CampaignContactAssignment` ORM-модели; обновляются `Conversation` (+campaign_id NULLable), `MessageQueue` (+campaign_id NOT NULL); удаляется `ContextContactAssignment` (строки 330-349). Status enum для Conversation расширяется значениями `lead`, `handoff`, `finished` (см. C-13).
- `app/routers/send.py` (`Phase 3` re-write) — **полный рерайт** под `campaign_id` вместо `ai_context_id` в body (D-16). Existing rotation+validation flow остаётся, источник agent — JOIN на campaigns.
- `app/routers/agents.py:64, 246` — заменяем хардкод `campaign_count=0` на реальный COUNT (D-10 Phase 3 → реализация здесь); реализуем block DELETE agent при active campaign (D-09 Phase 3).
- `app/routers/folders.py:248` — реализуем block DELETE folder при active campaign (D-06 Phase 2 carry-over).
- `app/routers/senders.py` — добавляется проверка active campaign при DELETE / PATCH lifecycle_status (по аналогии с agents/folders).
- `app/services/queue.py:62-65, 112-125` — выпиливаем `MOSCOW_TZ`, `WORK_HOUR_START/END`, `_is_working_hours()`, `_next_working_window()` глобальные; заменяем на per-campaign check внутри `_process_next_for_sender`. SELECT очереди делает JOIN на campaigns для timezone/work_hour/days/stop_date. **НЕ трогаем** MIN_SEND_INTERVAL/MAX_SEND_INTERVAL, LONG_PAUSE_*, FLOOD_HARD_THRESHOLD (CLAUDE.md).
- `app/services/queue.py:691-712` — INSERT в conversations расширяется на `campaign_id` (D-05).
- `app/services/queue.py:705-712` — TODO(phase-4) на `ai_context_id JOIN` закрыт — campaign_id напрямую на queue item.
- `app/services/listener.py:247, 350, 707` — TODO(phase-4) на pull `ai_context_id from conversation.campaign_id JOIN` — теперь conversation имеет ai_context_id напрямую (через campaign.agent_id если нужно — но ai_engine.get_context переключается на campaign-источник, не conversation.ai_context_id).
- `app/services/ai_engine.py:50-100, 165-199, 201-265, 267-430` — `get_context` переписывается на чтение из `campaigns` через `conversations.campaign_id` JOIN (источник tools, lead/handoff/finish hints, webhook URLs); `build_tools` дополняется built-in tools (D-12); `generate_response` обрабатывает tool calls — добавляется обработка built-in (UPDATE conversation.status + webhook). Webhook payload утилитарно вынесен в `services/webhook_notify.py` (рекомендация, planner может оставить inline).
- `app/services/rotation.py` — `get_or_assign_sender()` сигнатура меняется: `context_id` → `campaign_id`. Источник senders — `campaign_senders` (не глобально по workspace). Реализация UNIQUE conflict resolution та же.
- `app/services/listener.py:813-879` — существующий `_handle_antispam_signal` остаётся как fail-safe для SpamBot и др. Параллельно работает с новым signal-flow через LLM tool calls (D-12). НЕ ломаем antispam handler — это последняя линия защиты аккаунта.
- `app/services/listener.py:543-803` (`handle_incoming_message`) — изменения минимальны: AI-генерация продолжает работать как сейчас, source контекста через `ai_engine.get_context(conversation.ai_context_id)` сейчас → через `ai_engine.get_context_for_conversation(conversation_id)` который сам резолвит campaign и tools.
- `app/services/warmup.py` — НЕ трогаем (warmup не зависит от campaigns; timezone в warmup может остаться MSK или wsp-level).
- `migrations/015_phase3.sql` — последняя миграция, следующая `016_phase4.sql`.

### AI Engine / OpenAI (внешний)
- `app/services/ai_engine.py` — точка переключения источника tools и signal-trigger-hints. НЕ переписываем сам OpenAI-call flow.

### Lovable UI contract (downstream)
- Lovable рендерит форму CRUD кампании: имя, описание, выбор agent (из workspace), выбор folder (из workspace), выбор senders (из workspace, с предупреждением «занят кампанией X» если sender в running кампании), timezone-picker, work_hour_start/end (sliders), work_days_mask (7 чекбоксов Пн-Вс), start_date/stop_date (datetime pickers, optional), 3 webhook URL inputs (lead / handoff / finish), 3 trigger_hint textareas (свободный текст в стиле «когда клиент говорит X»), tools-editor (массив custom function specs, аналог существующего в Lovable webhook_functions редактора), message_template (textarea с подсветкой `{{name}}` плейсхолдеров).
- Phase 5 inbox-фильтр по `campaign_id` (INBX-05) использует те же endpoint-paths `GET /api/v1/campaigns` для dropdown'а.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **AuthDep / AuthCtx** (Phase 1 D-11..14, `app/utils/auth.py`) — все новые endpoint'ы под `Depends(auth_dep)` + workspace_id фильтр.
- **`ai_engine.build_tools()`** (строка 165) — уже конвертирует webhook_functions JSONB в OpenAI tools format. Reuse без изменений, источник чтения меняется (через JOIN на campaigns).
- **`ai_engine.execute_webhook()`** (строка 201) — fire-and-forget httpx-вызов с timeout-handling. Reuse для tool calls; новый webhook notification (lead/handoff/finish) использует тот же httpx pattern, но с другим payload shape — вынести в `services/webhook_notify.py` либо inline в ai_engine (planner решит).
- **`queue.py:_fire_callback()`** (строка 731) — паттерн fire-and-forget POST для notifications. Reuse для campaigns webhook'ов (либо как утилита `app/services/webhook_notify.py`).
- **`queue.py:enqueue_message()`** — уже принимает `ai_context_id` через extra_data (Phase 3 D-06). Phase 4 расширяет на `campaign_id` (хранится отдельно или внутри extra_data — Claude's Discretion).
- **`queue.py:_process_next_for_sender()`** (строка 215+) — SELECT очереди для sender'а; уже знает про rate-limit/long-pause/fatigue. Phase 4 добавляет JOIN на campaigns для проверки stop_date / working hours / paused — но НЕ ТРОГАЕТ внутренние эмпирические интервалы.
- **`_handle_antispam_signal()`** (`listener.py:813`) — паттерн «UPDATE conversations SET ai_enabled=false + UPDATE message_queue SET status='failed'» — шаблон для CAMP-12 (transfer_to_manager built-in tool action, D-12). Существующий antispam продолжает работать параллельно как safety net.
- **`ContactCheckWorker`** (`app/services/contact_check_worker.py`) — паттерн background worker с tick'ами и async-обработкой. Шаблон для `CampaignEnqueueWorker` (D-17).
- **Phase 2 `csv_import.render_template`-подобные** утилиты — нет прямого аналога, но `csv_import.py` имеет column mapping логику; функционально не используется здесь.
- **`tests/conftest.py`** (Phase 1+2+3 фикстуры) — расширяется `campaign_factory`, `running_campaign_factory`, `campaign_sender_attachment` для интеграционных тестов.

### Established Patterns

- Все таблицы: UUID PK, `workspace_id UUID NOT NULL FK CASCADE`, server_default для timestamp'ов.
- Миграции — raw SQL, идемпотентные (`IF NOT EXISTS`, `DROP COLUMN IF EXISTS`), нумерация `016_`.
- Запросы к новым таблицам: `.where(... .workspace_id == ctx.workspace_id)` (Phase 1 D-04) + TODO(v2-rls) метки.
- Pydantic v2: `model_config = ConfigDict(from_attributes=True)`, partial PATCH с Optional полями.
- API endpoints под `/api/v1/campaigns`. Status переходы — отдельные endpoints `POST /campaigns/{id}/start`, `/pause`, `/resume`, `/finish` (а не PATCH со status — explicit actions яснее для UX).
- HTTP коды: 201 на create, 200 на read/update, 204 на delete, 404 на отсутствие, 409 на conflict (running при DELETE; sender lock при start; existing draft duplicate при PATCH name), 422 на pydantic validation, 403 на чужой workspace.
- Enum-поля: SQLEnum, не String+CHECK (Phase 2 D-21 pattern).
- Background workers: singleton instance в module + start/stop в FastAPI lifespan.

### Integration Points

- **`app/main.py`** — `app.include_router(campaigns.router)`, `app.include_router(send.router)` (восстанавливается с обновлённым body). Запускаем `campaign_enqueue_worker.start()` / `stop()` в lifespan рядом с queue_worker, contact_check_worker, warmup_worker.
- **`app/models/__init__.py`** — добавляются `Campaign`, `CampaignSender`, `CampaignContactAssignment`; обновляются `Conversation` (+campaign_id), `MessageQueue` (+campaign_id); удаляется `ContextContactAssignment`. `Conversation.status` enum расширяется (D-12, C-13).
- **`app/schemas/__init__.py`** — добавляются `CampaignCreate`, `CampaignUpdate`, `CampaignResponse`, `CampaignListResponse`, `CampaignStatusUpdate`, `ToolSpec` (Pydantic-модель webhook_functions shape, см. C-10). `SendMessageRequest` обновляется: `campaign_id: UUID` вместо `ai_context_id: UUID`.
- **`docker-compose.yml`** — без изменений в Phase 4 (новых сервисов не добавляется; CampaignEnqueueWorker — в API-контейнере).
- **`app/config.py`** — добавляется `CAMPAIGN_ENQUEUE_TICK_SECONDS: int = 30`, `CAMPAIGN_ENQUEUE_BATCH_SIZE: int = 500` (env-конфигурируемо).
- **`migrations/`** — следующая `016_phase4.sql`. Идемпотентна как обычно.

### Anti-patterns, которые НЕ повторять

- НЕ трогать `MIN_SEND_INTERVAL`, `MAX_SEND_INTERVAL`, `LONG_PAUSE_*`, `FLOOD_HARD_THRESHOLD`, `MAX_NEW_CONTACTS_PER_HOUR` в `queue.py` (CLAUDE.md — эмпирически подобраны).
- НЕ дублировать `DEFAULT_SYSTEM_PROMPT` AGS Foods хардкод в ai_engine.py (CONCERNS.md фиксирует это как brand-leak — clean-up НЕ часть Phase 4, отдельная фаза).
- НЕ изменять `gpt-5-mini-2025-08-07` строку в ai_engine.py (CONCERNS.md «Known Bugs» — отдельный bug fix, не Phase 4).
- НЕ автоматически менять campaign.status в background tick'ах (D-04 — 100% manual lifecycle; `is_exhausted` computed на чтение).
- НЕ хардкодить fallback "Europe/Moscow" в новом коде — читать из `campaign.timezone` (default уже в DB-уровне D-08).
- НЕ возвращать workspace-данные без `where(workspace_id == ctx.workspace_id)` — оставлять `# TODO(v2-rls): replaced by RLS policy app.workspace_id`.
- НЕ создавать новые таблицы без `workspace_id UUID NOT NULL FK CASCADE`.
- НЕ использовать выпиленный `verify_api_key` (Phase 1 D-14).

</code_context>

<specifics>
## Specific Ideas

- **БД чистая (Phase 1 D-01)**: миграция 016 не делает backfill / data migration. NOT NULL FK применяем напрямую (`message_queue.campaign_id`), DROP TABLE без копирования (`context_contact_assignments`).
- **«Direct send» юзкейс через campaign**: если клиент в n8n хочет direct push без расписания — заводит кампанию "Direct" с work_hour_start=0, work_hour_end=24, work_days_mask=127 (24/7). Не специальный API-flag, не nullable campaign_id. Унифицирует analytics и signal-handling.
- **Сигналы как LLM tools — это семантический подход, не лексический**: trigger_hint описывает СУТЬ когда вызвать (например «когда клиент явно сказал что готов купить»), а не паттерн «купить|приобрести». LLM решает по контексту всего диалога. Это работает только если выбран хороший model (см. CONCERNS.md «Known Bugs» — model ID hardcoded broken; Phase 4 ассумит что bug пофиксен в config).
- **3 webhook URL вместо 1** (D-13): user предпочёл явное разделение endpoint'ов под разные интеграции. REQUIREMENTS.md CAMP-14 обновляем синхронно при commit'е этого CONTEXT.md. UI рендерит 3 поля.
- **Pause не молчит** (D-15): семантика «pause = только отправка». Это важно для UX — клиент в Telegram пишет «здравствуйте», а у нас тишина — это репутационный удар. Pause → AI продолжает поддерживать диалог, новые контакты не получают первое сообщение.
- **Built-in tools всегда инжектятся** (D-12): даже если у кампании пустой `tools` JSONB, всё равно 3 built-in (mark_as_lead/transfer_to_manager/finish_conversation) добавляются — каждая campaign по дефолту умеет ставить лида.
- **`CampaignEnqueueWorker` tick = 30s**: для outreach acceptable lag. Не SSE, не triggers, не LISTEN/NOTIFY — простой polling. Если в v2 будет нужна моментальность — добавим NOTIFY на contact INSERT.
- **`render_template` empty fallback не блокирует отправку** (D-19): юзер увидит сообщение «, добрый день» в inbox — это сигнал что в CSV не было имени для этого контакта. Лучше чем тишина и непонятная ошибка.
- **`conversations.campaign_id` NULLable** (D-05): для входящих от незнакомых (когда контакт сам написал sender'у без участия кампании) и для legacy conversations созданных в Phase 3 до Phase 4-миграции. Phase 5 inbox знает оба состояния.
- **`message_queue.campaign_id` NOT NULL** (D-16) vs **`conversations.campaign_id` NULLable** (D-05) — асимметрично сознательно: outbound message всегда из кампании; inbound conversation может возникнуть и без кампании.

</specifics>

<deferred>
## Deferred Ideas

### Для Phase 5 (Inbox & Analytics)
- Inbox-фильтр по кампании (INBX-05) — рендерит dropdown'ом GET /api/v1/campaigns.
- Аналитика метрик per-campaign (ANLX-02) — SELECT с GROUP BY campaign_id из message_queue, conversations, messages_log.
- LLM request log per conversation (ANLX-05) — таблица llm_calls(conversation_id, prompt, response, timestamp). Сейчас не вводим — Phase 5 решит shape.
- AI-фильтр системных ботов (AIRC-04) — Phase 5; существующий `_handle_antispam_signal` остаётся в Phase 4 как safety net.
- Conversation.status новые значения 'lead' / 'handoff' / 'finished' (Phase 4 D-12) — будут визуализироваться в inbox в Phase 5; Phase 4 пишет статус, Phase 5 рисует.

### Для Phase 6 (Admin Master Bot)
- ADMN-02: бот шлёт уведомление в admin-канал при срабатывании transfer_to_manager (CAMP-12). Phase 4 пишет conversation.status='handoff' + вызывает webhook → Phase 6 добавляет вторичный канал уведомления (admin chat).
- ADMN-03: уведомление при ошибке sender'а — переиспользует существующий `_handle_antispam_signal` flow.

### Для v2
- **Multi-window расписание** (D-09): несколько окон в день (например 09–12 + 14–18). Сейчас одно окно.
- **Multi-folder, multi-agent кампании** (ADVN-02 A/B): сейчас 1:1. Через through-table.
- **Strict-mode variable substitution** (D-19): error/skip при отсутствии переменной — сейчас empty + warning.
- **`{{name | upper}}` Mustache filters** (C-03): сейчас strict без filters.
- **HMAC signature на webhook'ах** (D-13 C-01): сейчас raw POST.
- **Tool webhook timeout / retry** (CAMP-15): сейчас fire-and-forget с timeout (existing `ai_engine.execute_webhook` имеет 30s timeout, без retry).
- **Per-contact timezone scheduling** (ADVN-03): сейчас per-campaign.
- **Multi-step follow-up последовательности** (ADVN-01): сейчас одна message_template.
- **`POST /campaigns/{id}/duplicate`** опциональный endpoint (C-11) — может быть из этой фазы, может быть deferred.
- **Audit log переходов lifecycle** (C-12) — сейчас нет; в v2 для compliance.
- **Background scaling**: CampaignEnqueueWorker как horizontal-scale (несколько API-контейнеров читают одну очередь) — потребует FOR UPDATE SKIP LOCKED.
- **NOTIFY/LISTEN моментальность досыпания**: сейчас 30s tick polling.

### Tech debt из Phase 1 / 2 / 3, продолжающий висеть
- `senders.role` String(20)+CHECK → SQLEnum (C-08) — planner Phase 4 может закрыть в Plan 04-02 (migration 016) либо снова отложить.
- `app/database.py` `Base.metadata.create_all` (Phase 1 C-04, C-09) — всё ещё нерешён.
- `DEFAULT_SYSTEM_PROMPT` AGS Foods хардкод в ai_engine.py (CONCERNS.md brand-leak) — НЕ закрывается в Phase 4, отдельная clean-up фаза или v2.
- OpenAI model ID `gpt-5-mini-2025-08-07` (CONCERNS.md «Known Bugs») — отдельный bug, НЕ часть Phase 4 (но без фикса AI-engine не работает; ассумим что пофикшен в config до Phase 4 execute).

### Reviewed Todos (not folded)
Phase 4 todo match вернул 0 совпадений — нечего деферить.

</deferred>

---

*Phase: 04-campaigns*
*Context gathered: 2026-05-22*
