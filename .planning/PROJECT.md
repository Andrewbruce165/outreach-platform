# Outreach Platform

## What This Is

SaaS-платформа для автоматизации Telegram-аутрича через личные аккаунты менеджеров с AI-ответчиком.
Клиенты (компании) регистрируются, создают workspace, подключают свои Telegram-аккаунты, загружают базу контактов, настраивают AI-агентов и запускают **кампании** — платформа сама рассылает сообщения и отвечает на входящие через GPT, передаёт лиды наружу через webhook.
Brownfield-проект: базовая механика (очередь, rate limiting, AI-ответчик, онбординг) уже реализована, ключевой пробел — мультитенантность и модель кампании.

## Core Value

Клиент подключил аккаунт и через 10 минут первая кампания запущена — без программистов, без DevOps, без настройки серверов.

## Current Milestone: v1.0 First External Client

**Goal:** Первый платящий внешний клиент самостоятельно регистрируется, настраивает workspace, запускает кампанию и видит результаты.

**Target features:**
- Мультитенантная схема + magic-link auth (Supabase)
- TG-аккаунты + база контактов с папками + проверка в TG
- AI-агенты как переиспользуемые шаблоны (контекст / задача / тон / FAQ)
- **Кампании** как первичная сущность: связывают агента + аккаунты + папку + сигналы + webhook + tools + расписание
- Inbox с фильтром по кампании + аналитика по уровням (workspace / campaign / agent / TG-account)
- Telegram-бот для админских уведомлений (ручник, ошибки аккаунтов)

## Requirements

### Validated

- ✓ Отправка сообщений через PostgreSQL-очередь с rate limiting (4/мин, 20/час, 150/день) — existing
- ✓ AI-ответчик: Telethon listener + дебаунс 3–5 мин + GPT-4o-mini — existing
- ✓ Онбординг Telegram-аккаунта через телефон/SMS/2FA/QR — existing
- ✓ AI-контексты: промпт, тон, правила, FAQ, auto_pause_triggers (модель в БД) — existing
- ✓ Прогрев аккаунтов (WarmupWorker) — existing
- ✓ Proxy pool per-sender (Decodo SOCKS5) — existing
- ✓ Проверка телефонов через checker-аккаунт — existing
- ✓ Ротация отправителей: детерминированный маппинг (context_id, phone) → sender — existing
- ✓ Шифрование Telegram-сессий (Fernet) — existing
- ✓ Базовый фронт на Lovable: онбординг, inbox, настройка AI, статистика — existing
- ✓ Webhook + function calling (частично) — existing, требует аудита и переноса на уровень кампании

### Active

**Multitenancy & Auth (Phase 1):**
- [ ] Модель Workspace: tenant-изоляция всех данных через workspace_id
- [ ] Вход через magic link (Supabase Auth)
- [ ] FastAPI верифицирует Supabase JWT, извлекает workspace_id
- [ ] Workspace API-ключ для n8n/интеграций
- [ ] Полный рерайт API-эндпоинтов

**TG Accounts & Contacts (Phase 2):**
- [ ] Онбординг TG-аккаунта в workspace (sender)
- [ ] Per-sender настройки: rate limits, прокси, статус
- [ ] База контактов с папками (несколько списков внутри workspace)
- [ ] Проверка контакта в Telegram при импорте (через checker)
- [ ] Поля контакта: phone, username, full_name, source, custom (JSONB)
- [ ] Загрузка CSV + push через Workspace API

**Agents (Phase 3): ✓ Complete (2026-05-21)**
- [x] Агент как переиспользуемый AI-шаблон workspace-level
- [x] Настройка агента: контекст, задача, тон, FAQ
- [x] CRUD списка агентов workspace
  - Validated in Phase 3: migration 015 cleaned `ai_contexts` schema (D-02), `app/routers/agents.py` exposes 6 workspace-scoped endpoints under `/api/v1/agents` (incl. duplicate + hard delete), `app/routers/send.py` rewritten under AuthDep with explicit `ai_context_id` in body. Phase 4 carry-overs: real `campaign_count` query and DELETE-block on active-campaign attachment (Phase 4 Campaign FK).

**Campaigns (Phase 4):**
- [ ] Модель Campaign: agent + senders + folder + status
- [ ] Расписание кампании (рабочие часы + старт/стоп даты)
- [ ] Сигналы кампании: «передать лид», «передать на менеджера», «финиш диалога»
- [ ] **Webhook кампании** — URL для передачи событий (лид/финиш/ручник)
- [ ] **Tools кампании** — function calling спецификация
- [ ] Запуск / пауза / стоп кампании + досыпание контактов
- [ ] Переменные `{{имя}}, {{username}}, {{source}}, {{custom.X}}` в тексте
- [ ] Очередь учитывает campaign_id

**Inbox & Analytics (Phase 5):**
- [ ] Inbox с фильтром по кампании / агенту / аккаунту
- [ ] Ручной перевод диалога в режим «менеджер»
- [ ] AI не отвечает системным ботам (SpamBot и др.)
- [ ] Метрики по уровням: workspace / campaign / agent / TG-account
- [ ] Лог запросов в OpenAI на уровне диалога

**Admin Master Bot (Phase 6):**
- [ ] TG-бот workspace для админских уведомлений
- [ ] Уведомление при срабатывании «передать на менеджера»
- [ ] Уведомление при ошибке аккаунта (logout / flood / etc.)

### Out of Scope

| Feature | Reason |
|---------|--------|
| Биллинг / платёжный шлюз | Отдельная интеграция после v1, не блокирует первого клиента |
| Мобильное приложение | Web-first |
| OAuth (Google/GitHub) | Magic link через Supabase достаточно для v1 |
| Real-time чат между операторами | Telegram inbox достаточен |
| Другие мессенджеры (WhatsApp, Instagram) | Платформа Telegram-специфична |
| Собственный AI (fine-tuning) | GPT-4o-mini достаточно для v1 |
| Многошаговые follow-up последовательности | v2 — ADVN-01 |
| A/B тестирование текстов | v2 — ADVN-02 |
| Расписание по тайм-зонам контакта | v2 — ADVN-03 |
| Несколько пользователей в одном workspace | v2 — TEAM-01..02 |
| Экспорт аналитики в CSV | v2 — карточек метрик в v1 достаточно |

## Context

**База кода:** унаследована от `/root/apps/telegram-api` — внутреннего инструмента AGS Foods. Вся бизнес-логика работает; кодовая база async-first (asyncio + AsyncSession + Telethon). Миграции — raw SQL, нумерация 012_, 013_..., всегда IF NOT EXISTS.

**Терминология:**
- **Sender** (БД термин) = **TG-аккаунт** (UI термин) — физический подключенный Telegram-аккаунт с сессией
- **Agent** (UI термин) = AI-шаблон workspace-level (БД: `ai_contexts` — не переименовываем, чтобы не тащить миграцию)
- **Campaign** — объект-обёртка над рассылкой: связывает агента + аккаунты + папку + сигналы + webhook + tools + расписание

**Текущая auth:** единственный глобальный API-ключ (`X-API-Key` header). `python-jose` уже в `requirements.txt` — JWT не используется, но библиотека готова.

**Критические эмпирические константы:** rate limits (4/мин, 20/час, 150/день) подобраны под реальный Telegram anti-spam — менять только после явного обсуждения. Рабочие часы (09–20 МСК) переезжают с уровня сервиса на уровень кампании — клиент задаёт сам.

**Фронт:** Lovable (React, отдельный репо). Supabase Auth выбран потому что Lovable нативно с ним интегрируется.

**Хостинг:** DigitalOcean VPS, Docker Compose (3 сервиса: db, api, listener). Деплой ручной через SSH.

**Существующий webhook + tools:** в коде есть `webhook_functions` — частичная реализация function calling и передачи данных наружу. На уровне модели сейчас привязано к sender/AIContext. В v1 переезжает на уровень кампании (Phase 4 начинается с аудита).

## Constraints

- **Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 async / PostgreSQL 16 / Telethon — не менять без явного решения
- **Migrations:** только raw SQL в `migrations/`. Никогда Alembic.
- **Async:** никаких `time.sleep()`, синхронных `requests`, `print()` вместо `logging`
- **Rate Limits:** дефолтные значения (4/мин, 20/час, 150/день) хранятся как default на уровне sender'а; менять дефолты только после обсуждения
- **Retry / FloodWait:** не ломать логику без явной просьбы
- **API Endpoints:** полный рерайт — старые эндпоинты остаются в telegram-api (prod), в outreach-platform новые
- **Security:** сессии зашифрованы, API_KEY не в логах

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Magic link (Supabase Auth) | Нет паролей — проще для клиента; Lovable нативно с Supabase | — Pending |
| **Campaign как первичная сущность** | Запуск рассылки = объект с состоянием (статус / расписание / связи). Без него рассылка «глобальная», нельзя изолировать аналитику и сигналы. | — Pending |
| **Agent отвязан от sender'а** | Один AI-шаблон переиспользуется в разных кампаниях с разными аккаунтами. Раньше AIContext был привязан к sender — мешает переиспользованию. | — Pending |
| **Webhook + tools на уровне кампании, не агента** | Агент описывает «как говорить», кампания решает «куда передавать данные и какими инструментами пользоваться». Тот же агент в разных кампаниях может иметь разные webhook'и. | — Pending |
| **Сигналы (лид/менеджер/финиш) на уровне кампании** | Сигналы зависят от бизнес-цели рассылки, а не от стиля разговора. В LLM-промпт передаются вместе с агентским контекстом. | — Pending |
| **Папки в базе контактов** | Клиенты ведут несколько списков (по проектам / источникам / городам). Папка — таргет кампании. | — Pending |
| **Rate limits per-sender** | Telegram anti-spam смотрит на аккаунт. Sender в одной кампании за раз — лочится. | — Pending |
| **Расписание на уровне кампании** | Рабочие часы и дни — бизнес-параметр конкретной рассылки, не sender'а. | — Pending |
| Per-agent настройки вместо per-workspace | Каждый TG-аккаунт имеет свои лимиты/прокси/статус — тонкая настройка | — Pending |
| Полный рерайт API-эндпоинтов | Старые используются в telegram-api; нельзя менять — пишем новые с нуля | — Pending |
| PostgreSQL-очередь вместо Redis/Celery | Упрощает деплой (один меньше сервис), достаточно для текущих объёмов | ✓ Good |
| Brownfield: не переписывать логику, добавить тенантность | Рабочий код уже есть; переписываем только слой API и модели | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-21 — Phase 3 (Agents) complete: ai_contexts schema clean, workspace-scoped /api/v1/agents CRUD live, send.py rewritten under AuthDep*
