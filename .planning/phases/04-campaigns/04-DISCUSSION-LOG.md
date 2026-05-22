# Phase 4: Campaigns - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-22
**Phase:** 04-campaigns
**Areas discussed:** Модель кампании и связи, Расписание, Сигналы + webhook + tools, Очередь + досыпание + переменные

---

## Gray Areas Picker

| Option | Description | Selected |
|--------|-------------|----------|
| Модель кампании и связи | CAMP-01..04, 07, 08. Кардинальности, sender lock, lifecycle | ✓ |
| Расписание кампании | CAMP-05, 06. Timezone, окна, дни, start/stop date | ✓ |
| Сигналы + webhook + tools | CAMP-11..16. Detection, payload, tools shape, prompt injection | ✓ |
| Очередь, досыпание, переменные | CAMP-09, 10, 17. campaign_id FK, item generation, var subst | ✓ |

**User's choice:** All 4 (recommended).

---

## Модель кампании и связи

### Q1: Сколько агентов в одной кампании? (CAMP-02)

| Option | Description | Selected |
|--------|-------------|----------|
| 1 агент на кампанию (Recommended) | campaigns.agent_id NOT NULL FK. A/B = v2 ADVN-02 | ✓ |
| Несколько агентов (с правилами выбора) | Through-table + selection logic. Усложняет inbox-attribution | |

**User's choice:** 1 агент на кампанию.

---

### Q2: Сколько папок контактов в одной кампании? (CAMP-03, CAMP-09)

| Option | Description | Selected |
|--------|-------------|----------|
| 1 папка на кампанию (Recommended) | campaigns.folder_id NOT NULL FK. Phase 2 D-04 уже «контакт в одной папке» | ✓ |
| Несколько папок | Through-table campaign_folders. Усложняет CAMP-09 досыпание | |

**User's choice:** 1 папка на кампанию.

---

### Q3: Sender lock per active campaign — как реализуем (CAMP-04)?

| Option | Description | Selected |
|--------|-------------|----------|
| Through-table campaign_senders + derived check (Recommended) | Sender может быть прикреплён к draft/paused/done. Lock на /start через JOIN на status='running' | ✓ |
| Физический FK senders.active_campaign_id NULLable | Жёсткая 1:1 на DB-уровне, но ломает workflow «подготовить заранее» | |

**User's choice:** Through-table + derived check.

---

### Q4: Lifecycle done — кто и когда переводит в done? (CAMP-07, CAMP-08)

| Option | Description | Selected |
|--------|-------------|----------|
| 100% manual + флаг exhausted (Recommended) | Юзер сам жмёт finish; computed is_exhausted в API; done = terminal | ✓ |
| Auto-done когда папка исчерпана | Background tick переводит. Ломает «жду новых контактов от n8n» | |

**User's choice:** 100% manual + флаг exhausted.

---

### Q5: conversations.campaign_id — добавляем колонку на диалог?

| Option | Description | Selected |
|--------|-------------|----------|
| Да, conversations.campaign_id NULLable FK (Recommended) | Phase 5 INBX-05 / ANLX-02 нужно. NULLable для входящих от незнакомых + после hard-delete | ✓ |
| Нет, JOIN через message_queue | Тяжелее запросы, ломает входящих без кампании | |

**User's choice:** Да, NULLable FK.

---

### Q6: context_contact_assignments — эволюция в Phase 4?

| Option | Description | Selected |
|--------|-------------|----------|
| Drop+create новую campaign_contact_assignments (Recommended) | Чистая модель per-campaign rotation. БД чистая — без backfill | ✓ |
| Добавить campaign_id рядом | Два ключа в таблице, запутанная rotation | |

**User's choice:** Drop+create.

---

### Q7: DELETE кампании — какие блокировки и что с историей?

| Option | Description | Selected |
|--------|-------------|----------|
| Hard delete + 409 если status='running' (Recommended) | FK: campaign_senders/contact_assignments CASCADE; conversations/queue SET NULL. Phase 3 D-08 pattern | ✓ |
| Блокируем удаление если есть история | Archive-only. Против Phase 3 D-08 | |

**User's choice:** Hard delete + 409 на running.

---

## Расписание кампании

### Q8: Timezone расписания — где хранится? (CAMP-05)

| Option | Description | Selected |
|--------|-------------|----------|
| Campaign-level (campaigns.timezone TEXT) (Recommended) | Мультирегиональные кампании в одном workspace. Default 'Europe/Moscow' | ✓ |
| Workspace-level (workspaces.timezone) | Одна tz на весь workspace, проще, но мешает мультирегион | |

**User's choice:** Campaign-level.

---

### Q9: Рабочие часы — одно окно или несколько?

| Option | Description | Selected |
|--------|-------------|----------|
| Одно окно: start_hour + end_hour (Recommended) | INT start/end, default 9/20. B2B outreach: 99% хватает | ✓ |
| JSONB windows array | Multi-window (обеденный перерыв). Deferred to v2 | |

**User's choice:** Одно окно.

---

### Q10: Рабочие дни недели — как храним?

| Option | Description | Selected |
|--------|-------------|----------|
| INT bitmask 0-127 (Recommended) | work_days_mask INT, default 31 (Mo-Fri). Cheap, 1 поле | ✓ |
| TEXT[] массив | Читаемее, но overkill для фикс 7 элементов | |

**User's choice:** INT bitmask.

---

### Q11: Stop_date какое поведение при наступлении? (CAMP-06, CAMP-08)

| Option | Description | Selected |
|--------|-------------|----------|
| Soft skip в queue + AI продолжает (Recommended) | queue worker фильтрует по NOW() < stop_date, AI отвечает существующим. Согласуется с D-04 manual lifecycle | ✓ |
| Auto-перевод в paused + AI замолкает | Нарушает D-04 (auto-transition статуса) | |

**User's choice:** Soft skip + AI продолжает.

---

## Сигналы + webhook + tools

### Q12: Где детектируются сигналы «передать лид» / «ручник» / «финиш»? (CAMP-11..13, 16)

| Option | Description | Selected |
|--------|-------------|----------|
| LLM tool call (встроенные tools) (Recommended) | 3 built-in: mark_as_lead, transfer_to_manager, finish_conversation. Trigger_hint как description. Семантика > regex | ✓ |
| Python regex/LIKE на тексте ответа | Грубее, требует от юзера регексов, не работает для семантики | |
| Гибрид: tools + hard-fail regex на вход | Сложнее, больше UI. В v2 если LLM tools мало | |

**User's choice:** LLM tool call.

---

### Q13: Webhook URL кампании — один на все события или по полю на событие? (CAMP-14)

| Option | Description | Selected |
|--------|-------------|----------|
| 1 URL + event_type в payload (Recommended) | webhook_url + payload.event_type. CAMP-14 формально это | |
| Отдельные поля: lead_webhook_url / handoff_webhook_url / finish_webhook_url | Гибче — разные эндпоинты под разные интеграции | ✓ |

**User's choice:** Отдельные поля. **Расхождение с CAMP-14** — REQUIREMENTS.md будет обновлён на «3 отдельных webhook URL».

---

### Q14: Tools spec shape (CAMP-15) — переезжаем старый webhook_functions JSONB или редизайн?

| Option | Description | Selected |
|--------|-------------|----------|
| Переезд as-is на campaigns.tools JSONB (Recommended) | Reuse ai_engine.build_tools + execute_webhook. Plan 04-01 audit подтвердит shape | ✓ |
| Редизайн под OpenAI native strict tools | Чище архитектурно, но выходит из скоупа Phase 4 | |

**User's choice:** Переезд as-is.

---

### Q15: Campaign.status='paused' — AI продолжает отвечать на входящие? (CAMP-08)

| Option | Description | Selected |
|--------|-------------|----------|
| Продолжает (pause = только отправка) (Recommended) | Кому уже написал — держишь линию. Full freeze через ai_enabled на conversation | ✓ |
| Замолкает (pause = full freeze) | Подрывает UX лидов, тишина после «Привет!» | |

**User's choice:** Продолжает.

---

## Очередь, досыпание, переменные

### Q16: message_queue.campaign_id — NOT NULL или NULLable? (CAMP-17)

| Option | Description | Selected |
|--------|-------------|----------|
| NOT NULL — любой send требует campaign_id (Recommended) | /send переписывается под campaign_id. Direct push — через campaign "Direct" 24/7 | ✓ |
| NULLable — legacy /send без campaign_id тоже работает | Analytics gap, fallback на хардкод 9-20 неясен | |

**User's choice:** NOT NULL.

---

### Q17: Кто генерирует queue items из папки + обеспечивает досыпание? (CAMP-09)

| Option | Description | Selected |
|--------|-------------|----------|
| Background worker CampaignEnqueueWorker (Recommended) | Pattern ContactCheckWorker Phase 2. 30s tick, batch INSERT, досыпание auto через следующий tick | ✓ |
| Sync inline при POST /campaigns/{id}/start | Блокирует endpoint на больших папках, нужен отдельный mechanism для CAMP-09 | |

**User's choice:** Background worker.

---

### Q18: Variable substitution — когда подставляем {{имя}}? (CAMP-10)

| Option | Description | Selected |
|--------|-------------|----------|
| На enqueue (CampaignEnqueueWorker пишет final text) (Recommended) | Inbox видит фактический текст; меньше DB-reads на send; valid после delete контакта | ✓ |
| На send (queue worker resolves) | Свежее значение custom.X, но больше DB-нагрузка, audit сложнее | |

**User's choice:** На enqueue.

---

### Q19: Синтаксис переменных + что с отсутствующими?

| Option | Description | Selected |
|--------|-------------|----------|
| {{name}} Mustache + empty fallback (Recommended) | Согласуется с PROJECT.md / REQUIREMENTS.md ({{имя}}). Empty + warning не блокирует | ✓ |
| {{name}} + default fallback из var_defaults JSONB | Гибче, больше UI. В v2 | |
| {name} Python-style + empty fallback | Конфликт с обычными {} в тексте | |

**User's choice:** {{name}} Mustache + empty fallback.

---

## Final check

### Q20: Покрыли 4 области. Что дальше?

| Option | Description | Selected |
|--------|-------------|----------|
| Пиши CONTEXT.md (Recommended) | Все ключевые решения зафиксированы | ✓ |
| Ещё вопросы | Есть серые зоны которые я пропустил | |

**User's choice:** Пиши CONTEXT.md.

---

## Claude's Discretion

User не сделал явных «you decide» выборов — все 20 вопросов получили конкретные ответы. Claude's Discretion items C-01..C-13 в CONTEXT.md — это технические уточнения уровня planner'а (точные regex, имена endpoint-методов, JSON schema формы), не решения уровня founder/visionary.

## Deferred Ideas

Сгенерированы из обсуждения и сохранены в CONTEXT.md `<deferred>`:
- Multi-window расписание (v2)
- Multi-folder / multi-agent кампании (ADVN-02 v2)
- Strict-mode variable substitution (v2)
- {{name | upper}} Mustache filters (v2)
- HMAC webhook signature (v2)
- Per-contact timezone scheduling (ADVN-03 v2)
- Multi-step follow-up sequences (ADVN-01 v2)
- POST /campaigns/{id}/duplicate (планер решит, Phase 4 или v2)
- Audit log lifecycle transitions (v2)
- Horizontal scaling CampaignEnqueueWorker (v2)
- NOTIFY/LISTEN моментальность досыпания (v2)

User-flagged divergence: REQUIREMENTS.md CAMP-14 будет обновлён на «3 отдельных webhook URL» в составе коммита CONTEXT.md.
