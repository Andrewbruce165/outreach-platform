# Outreach Platform — CLAUDE.md

## Главное правило

**НЕ пиши код сразу.** Перед любым изменением (кроме однострочных правок):
1. Объясни что собираешься делать и зачем — коротко, по-русски, 2-3 предложения
2. Дождись подтверждения
3. Только потом пиши код

Исключения (делай сразу): typo, переименование, добавление docstring, форматирование.

Общение со мной — **русский**. Код и коммиты — английский.

---

## Что это за проект

SaaS-платформа для автоматизации Telegram-аутрича через личные аккаунты менеджеров с AI-ответчиком.

**Базирован на** `/root/apps/telegram-api` — внутреннем инструменте AGS Foods. Код взят за основу, дорабатывается в новый продукт.

**Цель v1:** Первый платящий внешний клиент — подключает свои Telegram-аккаунты, настраивает AI, запускает аутрич самостоятельно.

---

## Стек

- Python 3.11+, FastAPI, SQLAlchemy 2.0 async, PostgreSQL 16
- Telethon (Telegram MTProto), OpenAI (gpt-4o-mini)
- Docker Compose: 3 сервиса — db, api, listener
- Фронт: Lovable (React, отдельный репо)
- Хостинг: VPS DigitalOcean

---

## Текущее состояние кода

### Уже реализовано (унаследовано от telegram-api)

- Отправка сообщений через очередь в PostgreSQL (без Redis/Celery)
- Rate limiting: 4 msg/мин, 20/час, 150/день per sender (подобрано эмпирически — не менять без обсуждения!)
- Рабочие часы: 09:00–20:00 МСК (захардкожено в queue.py — нужно вынести в настройки workspace)
- AI-ответчик: listener в отдельном контейнере, дебаунс 3–5 мин, GPT
- Онбординг Telegram-аккаунтов (телефон/SMS/2FA/QR) через routers/onboarding.py
- AI-контексты: промпты, тон, правила, FAQ, auto_pause_triggers (модель AIContext)
- Прогрев аккаунтов (warmup)
- Proxy pool per-sender
- Проверка телефонов (checker-аккаунт)
- Базовый фронт на Lovable: онбординг, inbox, настройка AI, статистика

### Чего нет — нужно построить

**Мультитенантность (основная работа):**
- Модель Workspace — нет ни одной таблицы с workspace_id
- Auth: логин/пароль, JWT (где хранить — Supabase или FastAPI — не решено, это первое архитектурное решение)
- Workspace API-ключ для n8n/интеграций

**Политика рассылки на уровне workspace:**
- Настраиваемые лимиты (сейчас захардкожены в services/queue.py)
- Расписание (сейчас захардкожено 09–20 МСК)
- Персонализация: переменные {{имя}}, {{компания}} в тексте
- "Зелёный коридор" — рекомендованные безопасные значения + предупреждение при выходе за них

**Флоу входящих контактов (два режима):**
- Загрузка CSV через UI (телефоны + имена + переменные)
- Push через API (текущий n8n-флоу, привязывается к workspace)

**Правила остановки AI:**
- Auto-pause по триггерам (поле auto_pause_triggers уже есть в AIContext — нужен UI)
- Ручной перевод в режим "менеджер" из inbox
- Блокировка AI на системных ботов (SpamBot и др.)
- Per-account: что делать с входящими от незнакомых (AI / игнорировать / уведомить)

---

## Архитектурные правила (наследуются)

- **Async everywhere**: все DB через async/await + AsyncSession
- **Миграции**: только raw SQL в migrations/. Нумерация: 012_, 013_... Всегда идемпотентны (IF NOT EXISTS). Никогда Alembic.
- **Никогда**: time.sleep(), синхронный requests, print() вместо logging
- **Безопасность**: сессии зашифрованы, API_KEY не в логах
- **Очередь**: не трогать интервалы без явного обсуждения — подобраны эмпирически
- **Retry-логика FloodWait**: не ломать без явной просьбы

---

## Git & Deploy

```bash
# Клонировать локально
git clone git@github.com:Andrewbruce165/outreach-platform.git

# Деплой на сервер
cd /root/apps/aimly/tg-outreach && git pull && docker compose up -d --build api
docker compose up -d --build listener
```

**Сервер:** /root/apps/aimly/tg-outreach/ (VPS DigitalOcean, 134.209.239.97)
**Старый продакшн:** /root/apps/telegram-api/ — не трогаем, работает независимо
**GitHub:** git@github.com:Andrewbruce165/outreach-platform.git

### Сетевая топология (важно)

Прод-домен: **`https://aimly.agsventurelab.com`**

Хост-порт `:443` занят stream-блоком nginx (SNI-диспетчер MTProto-camouflage для других сервисов). Поэтому:

- API-контейнер биндится на `127.0.0.1:8005:8000` (порт 8000 занят старым telegram-api)
- nginx vhost для домена слушает `127.0.0.1:8444 ssl proxy_protocol` (за SNI-диспетчером, шаблон funnel-api)
- Цепочка: `:443 → SNI stream → nginx:8444 ssl proxy_protocol → 127.0.0.1:8005 → api:8000`
- TLS выпускается **только** через `certbot certonly --webroot` (НЕ `--nginx`, иначе сломает SNI stream-схему). Автопродление — `certbot.timer`.

При добавлении новых доменов/сервисов: брать конфиг по шаблону `funnel-api` и согласовывать с devops.

---

## Что делать в новой сессии

Контекст собран, PROJECT.md уже продуман. В новой сессии (локально):

1. Открыть папку outreach-platform в Claude Code
2. Запустить /gsd:new-project — сказать агенту что это brownfield SaaS Telegram-аутрич проект, контекст уже есть в этом CLAUDE.md, нужно создать .planning/PROJECT.md → REQUIREMENTS.md → ROADMAP.md
3. Первое решение для обсуждения: auth через Supabase (Lovable-сторона) или FastAPI JWT
