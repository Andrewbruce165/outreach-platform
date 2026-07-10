---
title: Фактура для дизайнеров — редизайн продукта
date: 2026-07-10
context: >
  Собрано по запросу пользователя для передачи внешним дизайнерам, которые будут
  проектировать новый UX/UI поверх существующей бэкенд-инфраструктуры (не с нуля).
  Нейтральный инвентарь без оценочных суждений о текущем UX.
---

# Фактура для редизайна — Outreach Platform

Три раздела: (1) что видит пользователь сейчас, (2) что хранится в БД и на что можно опираться при проектировании новых экранов, (3) что бэкенд уже умеет, но пока нигде не показано — сырой материал для новых фич.

---

## 1. Экраны продукта

Навигация: левый сайдбар (Dashboard, Campaigns, Inbox, Agents, Knowledge bases, Contacts, TG accounts, Warmup, Settings) + топбар с заголовком/экшенами на каждой странице. Всё под `/login` и `/auth/callback` — публичное, остальное за auth-гвардом (Supabase-сессия).

**Вход и онбординг**
- `/login` — вход по magic-link (email → OTP)
- `/auth/callback` — обмен кода на сессию, редирект на `/onboarding` (нет сендеров) либо на дашборд
- `/onboarding` — подключение первого Telegram-аккаунта (телефон/код/2FA)

**Дашборд `/`** — обзор воркспейса: фильтр периода (24ч/7д/30д/90д) + фильтры по кампании/сендеру. 5 KPI-карточек (отправлено, reply rate, лиды, завершено, расход на LLM) со спарклайнами, воронка конверсии (Sankey), карточка здоровья пула аккаунтов, таблица performance по кампаниям, лента активности, экспорт в CSV.

**Кампании**
- `/campaigns` — список с табами по статусу, поиск, старт/пауза/резюм/стоп из строки, редактирование в модалке
- `/campaigns/new` — мастер в 7 шагов: Бриф → Агент → Сендеры → Аудитория → Расписание → Интеграции → Ревью (редактор диалогового флоу, кастомные тулы/вебхуки)
- `/campaigns/$id` — детальная страница: статус-пилюля, health пула, кнопки жизненного цикла, алерты (нет бэкап-сендеров, пауза по дедлайну, недобор по ETA), метрики, панель сендеров (attach/detach), лента событий кампании

**Inbox `/inbox`** — 3 колонки: список диалогов (фильтры по статусу/кампании/сендеру), тред сообщений (текст, файлы, редактирование/удаление своих сообщений, вкл/выкл AI, ручной перехват, пометка "лид", завершение), правая панель — детали контакта + вкладка Trace (рассуждения AI).

**Контакты `/contacts`** — папки: сайдбар папок, таблица контактов со статусом присутствия в Telegram, массовые операции, "перепроверить Telegram", импорт (CSV/paste с превью), ручное добавление.

**Аккаунты и прогрев**
- `/accounts` — таблица подключённых Telegram-аккаунтов: пауза/резюм, ресинк, грейд качества, проверка @SpamBot, история ограничений, профиль (имя/био/фото, 2FA, username), удаление, повторная авторизация, импорт
- `/warmup` — пул прогрева: настройки (дневные лимиты, расписание), таблица аккаунтов в пуле, живая статистика

**AI-агенты `/agents`** — CRUD список агентов; редактор: кто агент, инфо о компании/продукте, тон, кастомные правила, скорость ответа, лимит длины сообщения, зеркалирование языка, эмодзи.

**Базы знаний**
- `/knowledge-bases` — список/грид с статусами
- `/knowledge-bases/$id` — детали + 5 метрик, 4 таба: Документы (загрузка/paste, реиндекс, удаление), Поиск (тест семантического поиска), Агенты (кто использует базу), Настройки

**Настройки `/settings`** — табы: Workspace, Grade Ladder (правила качества сендеров), AI/LLM (провайдер, ключ, модель), API-ключи, Members (заглушка v2), Профиль, Внешний вид.

---

## 2. Модель данных

### Мультитенантность
- **Workspace** — тенант. **UserWorkspace** (user↔workspace, роль). **WorkspaceApiKey** (name, prefix, revoked_at) — управление ключами есть только на бэкенде, UI нет.

### Sender (Telegram-аккаунты) — `/accounts`
Ключевые поля: `auth_status` (ok/session_expired/session_revoked/deactivated/banned), `lifecycle_status` (active/warmup/paused), `restriction_status` (none/spam_limited/frozen) + `restricted_until`, `checker_rest_until`/`checker_trip_count` (бэкофф для чекеров), `long_pause_until`, `current_level`/`level_updated_at` (грейд 1-3, управляет дневным бюджетом новых чатов), `rate_per_min/hour`, `tg_username/tg_premium/tg_bio/tg_photo` (кэш профиля), `role` = sender vs checker.
- **SenderRestrictionEvent** — append-only лог: `category`, `event_type`, `source`, `restricted_until`, `raw_text`, `activity_slice`, снапшот `proxy`. Частично отображается (accounts/campaign detail), но детали `activity_slice`/`raw_text`/`proxy` не показаны — материал для drill-down "почему аккаунт ограничили".
- **SenderFirstContact** — реестр первых контактов между парами сендеров (бюджет warmup). UI нет.
- **SenderGradeSettings** — грейд-лестница на воркспейс. Частично в Settings/accounts.
- **ProxyPool** — инвентарь прокси (host/port/creds, `assigned_to_sender_id`). Экрана управления нет.
- **OnboardingSession**, **AccountImportStaging/Job/Item** — пайплайн онбординга и bulk-импорта. Покрыто onboarding-страницей и модалкой импорта.

### Контакты — `/contacts`
- **Folder** → **Contact**: `phone/username/full_name/source/custom` (JSON), `tg_status` (pending/registered/not_registered/error/unchecked), `tg_telegram_id/tg_username_resolved/tg_error/tg_checked_at`, и **`tg_confidence`** (high/low) + **`tg_resolved_by`** + **`tg_probe_state`** (clean/suspect) — эти три поля нигде не показаны, хотя это готовый "сигнал доверия" для UI.
- **ContactCache** — внутренний кэш резолва для отправки, не для дизайнеров.
- **CsvImport** — есть UI.

### Campaign — `/campaigns`
Богатое состояние: `status`, `pause_reason` (код авто-паузы, напр. `no_senders_attached`), рабочие часы/дни/таймзона, `message_template`, три legacy webhook URL + trigger hints, `tools` (JSON), `dialogue_flow` (есть UI), `audience_hints`/`primary_goal`, **`objective_preset`/`disclosure_preset`/`authority_preset`/`style_examples`** (пресеты для промпта — только типы, UI нет), `allow_recontact`/`recontact_min_age_days`, follow-up настройки, `auto_finish_hours`, `variation_enabled` (антиспам-вариация текста).
- **CampaignSender** (M:N). **CampaignContactAssignment** (закрепление контакт→сендер).
- **CampaignAttachment** — 1:N вложения-альбом (`position`, `size_bytes`, `content_type`) — **UI не найден нигде** — явный кандидат на менеджер вложений в создании кампании.

### Диалоги и сообщения — `/inbox`
- **Conversation**: Sender + опционально Campaign/AIContext; `status` (active/manual/paused/lead/handoff/finished/bot_ignored/no_reply), `ai_enabled`, `pings_sent`, `paused_at/paused_reason`.
- **MessageLog** — история отправленных/черновых/неудачных сообщений.
- **MessageQueue** — очередь отправки (`status`, `item_type`, `priority`, `scheduled_at`, `attempts`) — служебное, не пользовательский экран, но виджет "здоровье очереди" мог бы на нём строиться.
- **LLMCall** — лог каждого AI-вызова: `prompt` (JSON), `response_text`, `tool_calls` (JSON), токены, `latency_ms`, `provider`/`key_source`. Частично видно в inbox/campaign detail — полные JSON-блобы — материал для более богатого "инспектора рассуждений AI".

### Agent (AIContext) — `/agents`
Есть UI: `system_prompt`, `rules`, `company_info/product_info`, `who_is_agent`, `company_knowledge`, `knowledge_base` (статический текст, отдельно от RAG), `tone_preset`, `response_speed`/`response_delay_seconds`, `max_message_length`, `mirror_language`, `allow_emoji`.
Нет UI: **`faq`/`qa_pairs`** (JSON Q&A), **`banlist`**/**`auto_pause_triggers`**/**`auto_pause_scope`** — поля есть в модели, но не найдены в agents.tsx.

### Базы знаний (RAG) — `/knowledge-bases`
KnowledgeBase → KbDocument (status, `chunk_count`, `error`) → KbChunk (эмбеддинги, внутреннее). AgentKnowledgeBase (M:N). Всё покрыто UI.

### Warmup — `/warmup`
WarmupPool → WarmupSession (пара sender_a/b, `topic`, `status`, `messages_sent/target_messages`) → WarmupMessage. WarmupSettings (per-workspace). Покрыто страницей.

### Настройки / платформа
LLMSettings (провайдер/модель/BYO-ключ/temperature/reasoning_effort) — в Settings. TelemetryEvent — внутренняя аналитика, UI не нужен.

### Сводка пробелов в UI
Нет экрана/поля нигде: **CampaignAttachment**, **ProxyPool**, **SenderFirstContact**, **WorkspaceApiKey** (управление, не сами ключи), Contact `tg_confidence`/`tg_resolved_by`/`tg_probe_state`, Agent `qa_pairs`/`banlist`/`auto_pause_triggers`, Campaign `objective_preset`/`disclosure_preset`/`authority_preset`/`style_examples`, MessageQueue как видимый "queue health".

---

## 3. Нераскрытые возможности API (есть на бэкенде, не подключены к UI)

- **Campaign finish** — явное терминальное действие "завершить кампанию" (отдельно от stop).
- **Campaign requeue-failed / rerender-pending** — массовое восстановление зависших/неудачных элементов очереди после сбоя.
- **Campaign senders add/remove post-launch** — attach/detach сендеров к уже запущенной кампании (сейчас сендеры задаются только при создании) — *важно: панель на `/campaigns/$id` уже частично это использует, но полный набор действий может быть не покрыт*.
- **Sender block-rate** — `GET /senders/{slug}/block-rate` — 7-дневное отношение блокировок к отправкам (health-сигнал), сама цифра нигде не показана.
- **Proxy pool management** — `GET/POST /workspace/proxies`, `DELETE /workspace/proxies/{id}`, `POST /senders/{slug}/assign-proxy` — экрана управления прокси нет вообще.
- **Onboarding cancel/reauth-специфичные роуты** — выделенные cancel и reauth (код + QR) эндпоинты есть, но UI переиспользует общий start-флоу.
- **Warmup session history** — список/детали/сообщения отдельных warmup-сессий (сейчас видна только агрегированная статистика пула).
- **Analytics per-agent и LLM-cost агрегаты** — `/analytics/agents/{id}`, `/analytics/llm` — нет дашборда по агентам или расходам на LLM.
- **Telemetry core-value** — вычисляемая метрика вовлечённости/ценности без плитки на дашборде.
- **Contacts single move/delete** — в UI только bulk-операции, точечные эндпоинты не задействованы.
- **`POST /send`** — низкоуровневая ручная постановка сообщения в очередь — похоже на API/интеграционную поверхность (n8n), не для SPA.

**Чисто служебное, не для UI:** `health.py` (healthcheck), `telemetry.py` events POST (write-only инструментация).

**Пользовательские, но не подключены — кандидаты на новые экраны:** управление прокси, отображение block-rate, drill-down по warmup-сессиям, аналитика по агентам/LLM-расходам, инструменты восстановления кампании (requeue/rerender), полный набор post-launch управления сендерами кампании.
