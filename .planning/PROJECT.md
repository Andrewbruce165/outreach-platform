# Outreach Platform

## What This Is

SaaS-платформа для автоматизации Telegram-аутрича через личные аккаунты менеджеров с AI-ответчиком.
Клиенты (компании) регистрируются, создают workspace, подключают свои Telegram-аккаунты, загружают базу контактов и запускают рассылку — платформа сама рассылает сообщения и отвечает на входящие через GPT.
Brownfield-проект: базовая механика (очередь, rate limiting, AI-ответчик, онбординг) уже реализована, ключевой пробел — мультитенантность.

## Core Value

Клиент подключил аккаунт и через 10 минут первое сообщение ушло — без программистов, без DevOps, без настройки серверов.

## Requirements

### Validated

- ✓ Отправка сообщений через PostgreSQL-очередь с rate limiting (4/мин, 20/час, 150/день) — existing
- ✓ Рабочие часы 09:00–20:00 МСК с блокировкой вне окна — existing
- ✓ AI-ответчик: Telethon listener + дебаунс 3–5 мин + GPT-4o-mini — existing
- ✓ Онбординг Telegram-аккаунта через телефон/SMS/2FA/QR — existing
- ✓ AI-контексты: промпт, тон, правила, FAQ, auto_pause_triggers — existing
- ✓ Прогрев аккаунтов (WarmupWorker) — existing
- ✓ Proxy pool per-sender (Decodo SOCKS5) — existing
- ✓ Проверка телефонов через checker-аккаунт — existing
- ✓ Ротация отправителей: детерминированный маппинг (context_id, phone) → sender — existing
- ✓ Шифрование Telegram-сессий (Fernet) — existing
- ✓ Базовый фронт на Lovable: онбординг, inbox, настройка AI, статистика — existing

### Active

- [ ] Модель Workspace: tenant-изоляция всех данных через workspace_id
- [ ] Вход через magic link (Supabase Auth — без паролей)
- [ ] FastAPI верифицирует Supabase JWT, извлекает workspace_id
- [ ] Workspace API-ключ для n8n/интеграций
- [ ] Полный рерайт API-эндпоинтов (старые привязаны к telegram-api)
- [ ] Загрузка контактов через CSV (телефоны + имя + переменные)
- [ ] Персонализация сообщений: переменные {{имя}}, {{компания}}
- [ ] Per-agent настройки: rate limits, расписание, прокси, AI-контекст
- [ ] Страница агента: все параметры + статус в одном месте
- [ ] UI для auto_pause_triggers и ручного режима "менеджер"
- [ ] Push-контакты через API (n8n-флоу, привязка к workspace)

### Out of Scope

- Биллинг / платёжный шлюз — отдельная интеграция после v1, не блокирует первого клиента
- Мобильное приложение — web-first
- Real-time чат между операторами — Telegram inbox достаточен
- OAuth (Google/GitHub) — email/password через Supabase достаточно для v1
- Собственная инфраструктура доставки (не Telegram) — платформа Telegram-специфична

## Context

**База кода:** унаследована от `/root/apps/telegram-api` — внутреннего инструмента AGS Foods. Вся бизнес-логика работает; кодовая база async-first (asyncio + AsyncSession + Telethon). Миграции — raw SQL, нумерация 012_, 013_..., всегда IF NOT EXISTS.

**Текущая auth:** единственный глобальный API-ключ (`X-API-Key` header). `python-jose` уже в `requirements.txt` — JWT не используется, но библиотека готова.

**Критические эмпирические константы:** rate limits (4/мин, 20/час, 150/день) и рабочие часы (09–20 МСК) подобраны под реальный Telegram anti-spam — менять только после явного обсуждения.

**Фронт:** Lovable (React, отдельный репо). Supabase Auth выбран потому что Lovable нативно с ним интегрируется.

**Хостинг:** DigitalOcean VPS, Docker Compose (3 сервиса: db, api, listener). Деплой ручной через SSH.

## Constraints

- **Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 async / PostgreSQL 16 / Telethon — не менять без явного решения
- **Migrations:** только raw SQL в `migrations/`. Никогда Alembic.
- **Async:** никаких `time.sleep()`, синхронных `requests`, `print()` вместо `logging`
- **Rate Limits:** дефолтные значения (4/мин, 20/час, 150/день) хранятся как default на уровне агента; менять дефолты только после обсуждения
- **Retry / FloodWait:** не ломать логику без явной просьбы
- **API Endpoints:** полный рерайт — старые эндпоинты остаются в telegram-api (prod), в outreach-platform новые
- **Security:** сессии зашифрованы, API_KEY не в логах

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Magic link (Supabase Auth) | Нет паролей — проще для клиента; Lovable нативно с Supabase | — Pending |
| Per-agent настройки вместо per-workspace | Каждый Telegram-аккаунт имеет свои лимиты/расписание — тонкая настройка | — Pending |
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
*Last updated: 2026-04-02 after initialization*
