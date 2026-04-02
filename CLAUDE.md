# Telegram Followup API — CLAUDE.md

## Главное правило

**НЕ пиши код сразу.** Перед любым изменением (кроме однострочных правок):
1. Объясни что собираешься делать и зачем — коротко, по-русски, 2-3 предложения
2. Дождись подтверждения
3. Только потом пиши код

Это касается: новых эндпоинтов, изменений моделей/схемы, рефакторинга, изменений в очереди или listener.

Исключения (делай сразу): typo, переименование, добавление docstring, форматирование.

---

## Проект

FastAPI-сервис для отправки Telegram-сообщений через несколько аккаунтов с AI-ответчиком.

- **Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 async, PostgreSQL 16, Telethon, OpenAI
- **Порт:** 8000
- **Docker Compose:** 3 сервиса — `db`, `api`, `listener`
- **Хостинг:** VPS на DigitalOcean, деплой вручную
- **Авторизация:** API_KEY в заголовке `X-API-Key`

---

## Структура проекта

```
app/
├── main.py              — FastAPI app, lifespan, подключение роутеров
├── config.py            — pydantic Settings из .env
├── database.py          — async engine, Base, get_db()
├── models/
│   └── __init__.py      — Sender, MessageLog, ContactCache, AIContext, MessageQueue, Conversation
├── schemas/             — Pydantic-схемы запросов/ответов
├── routers/
│   ├── auth.py          — авторизация Telegram-аккаунтов
│   ├── senders.py       — CRUD sender-аккаунтов
│   ├── send.py          — отправка сообщений
│   ├── queue.py         — управление очередью
│   ├── conversations.py — диалоги
│   ├── contexts.py      — AI-контексты (промпты, настройки)
│   ├── check_contacts.py— проверка контактов в Telegram
│   ├── onboarding.py    — онбординг новых аккаунтов
│   └── health.py        — healthcheck
└── services/
    ├── ai_engine.py     — OpenAI интеграция (gpt-5-mini, Whisper, function calling)
    ├── telegram.py      — отправка через Telethon
    ├── queue.py         — воркер очереди (asyncio loop в API-процессе)
    ├── listener.py      — приём входящих сообщений (отдельный контейнер)
    ├── checker.py       — проверка контактов
    └── encryption.py    — шифрование/дешифрование сессий
migrations/              — raw SQL файлы (НЕ Alembic!)
docker-compose.yml
Dockerfile               — API
Dockerfile.listener      — Listener (отдельный процесс)
```

---

## Как работает система

### Отправка (Queue Worker)

- Живёт внутри API-процесса как `asyncio.create_task`, бесконечный цикл, `asyncio.sleep(3)` между тиками
- Очередь в PostgreSQL, **без Redis/Celery**
- `SELECT ... FOR UPDATE SKIP LOCKED` против двойной обработки
- Лимиты на sender: **4 msg/min, 20 msg/hour, 150 msg/day**
- Интервал между отправками: **20–55 сек рандом + fatigue factor**
- Длинные паузы каждые 12–25 сообщений: **3–10 минут**
- Работает только **09:00–20:00 МСК**
- Обрабатывает все активные sender_id параллельно (~4 аккаунта сейчас)

### Caption к файлам (send_file)

- Telegram лимит caption для медиафайлов: **1024 символа** (не 4096 как для текста)
- Pydantic-схема `SendFileRequest.caption` разрешает до **4096** — валидация на уровне API намеренно мягче
- Если `caption > 1024`: файл отправляется **без подписи**, затем сразу отдельным сообщением — полный текст
- Логика в `services/telegram.py:send_file`, константа `CAPTION_LIMIT = 1024`
- Если follow-up сообщение с текстом упало — логируем warning, файл считается отправленным успешно

### Приём (Listener)

- Отдельный Docker-контейнер (`Dockerfile.listener`)
- Telethon event handlers: `events.NewMessage(incoming=True)` и `outgoing=True`
- **Debounce 3 минуты** — ждёт, не пишет ли человек ещё
- **Максимум 5 минут** — объединяет несколько сообщений в одно перед AI
- `ResilientTelegramClient` обходит `TypeNotFoundError` в GetDifference
- `catch_up()` каждые 15 секунд

### AI-ответчик

- Модель: `gpt-5-mini-2025-08-07` (в `ai_engine.py:288`)
- **Fallback-а нет** — при ошибке API возвращает `None`, сообщение не отправляется
- История: последние **20 сообщений** из таблицы `messages` (НЕ `messages_log`)
- Системный промпт: дефолтный захардкожен в `ai_engine.py:29–40` (AGS Foods). Кастомный — из `AIContext.system_prompt` в БД
- В промпт добавляются: `tone_of_voice`, `rules`, `company_info`, имя контакта, лимит символов
- **Function Calling:** `webhook_functions` из AIContext → OpenAI tools → POST на настроенный URL
- **Голосовые:** транскрибируются через Whisper-1

### Retry-логика (ВАЖНО — не ломай!)

- `FloodWaitError` → reschedule на указанное время, `attempts` НЕ увеличивается
- `FloodWait ≥ 300s` (HARD) → все pending задачи этого sender'а тоже откладываются
- `PEER_FLOOD` → все задачи sender'а на **24 часа**, нужна ручная проверка аккаунта
- Обычные ошибки → `MAX_ATTEMPTS=3`, `RETRY_DELAY_SECONDS=60 × attempts`

### ResolvePhoneRequest и проверка контакта

- Если `ResolvePhoneRequest` вернул `PHONE_NOT_OCCUPIED` → `is_registered=False`, кэшируем
- Если вернул **любое другое исключение** (frozen, FloodWait, сеть) → **raise**, воркер пишет реальную ошибку и делает retry
- **Не возвращай `is_registered=False` при неизвестных исключениях** — это маскирует настоящую причину отказа
- Логика в `services/telegram.py:resolve_contact`

### Checker-аккаунты

- Аккаунты с `role='checker'` используются **только** через `/api/v1/check-contacts`
- В процессе отправки воркер НЕ использует checker — sender сам делает `ResolvePhoneRequest`
- Рекомендуемый флоу в n8n: сначала `/check-contacts` → фильтрация незарегистрированных → `/send-file`
- Результаты кэшируются в `contacts_cache` (7 дней) — повторные проверки бесплатны
- Текущий checker: **logist10** (`+77051011685`, `role='checker'`)

---

## Быстрые команды

```bash
# Запуск
docker compose up -d

# Логи
docker compose logs -f api
docker compose logs -f listener

# Применить миграцию
docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB -f /migrations/NNN_name.sql

# Перезапуск без пересборки
docker compose restart api
docker compose restart listener

# Пересборка после изменения кода
docker compose up -d --build api
docker compose up -d --build listener
```

---

## Протокол перед изменениями

Два уровня проверки в зависимости от сложности задачи.

### Уровень 1 — Быстрый чеклист

**Когда:** рефакторинг одного файла, мелкий баг в одном месте, добавление поля в схему.

Пройди мысленно, покажи вывод в 3-4 строки:
- Код в правильном слое? (роутер → тонкий, сервис → логика, модель → данные)
- Не дублирует существующее в `services/` или `routers/`?
- Edge cases: пустые данные, None, невалидный формат?
- Безопасность: токены не в логах, ошибки обёрнуты в HTTPException?

```
Место: services/telegram.py
Проверил: дублирования нет, edge case на пустой username добавлю
Безопасность: ОК
→ План: добавляю валидацию username перед отправкой
```

### Уровень 2 — Совет агентов

**Когда:** новая фича, баг затрагивающий >2 файлов, изменение архитектуры, работа с очередью/listener/retry, новый эндпоинт, изменение схемы БД.

Перед написанием кода проведи внутренний совет из трёх перспектив. Каждый агент знает стек проекта и его ограничения.

**🏗 Architect** — оценивает решение с точки зрения архитектуры:
- Правильное место для кода? (роутер / сервис / модель)
- Не ломает ли существующие паттерны? (async SQLAlchemy, шифрование сессий, очередь в PostgreSQL)
- Есть ли более простой способ? Не дублирует ли существующую логику?
- Нужна ли миграция? Если да — raw SQL, `IF NOT EXISTS`
- Предлагает **где** и **как** разместить код

**🔍 Critic** — ищет проблемы до того как код написан:
- Edge cases: пустые данные, None, невалидные форматы?
- Race conditions в очереди? Нужен `FOR UPDATE SKIP LOCKED`?
- N+1 запросы? Нужен `selectinload()` / `joinedload()`?
- Telegram API: `FloodWaitError` обработан? Не сломает ли retry-логику?
- OpenAI: что если вернёт ошибку? (fallback-а нет — `None` и молчим)
- Безопасность: сессии/ключи не утекут в логи или ответы?
- Новый эндпоинт защищён API_KEY?

**🔨 Implementer** — предлагает конкретную реализацию:
- Учитывает замечания Architect и Critic
- Описывает реализацию с привязкой к конкретным файлам и функциям
- Указывает какие файлы меняются и зачем
- Если есть конфликт между Architect и Critic — объясняет какой компромисс выбрал и почему

**✅ Итог** — одно финальное решение:

```
🏗 Architect: новый эндпоинт в routers/stats.py, логика в services/stats.py, нужна миграция для таблицы daily_stats
🔍 Critic: JOIN на messages может быть тяжёлым — нужен индекс на sender_id+created_at. FloodWait не затрагивается
🔨 Implementer: делаю агрегацию через SQL GROUP BY, не тащу в Python. Миграция добавляет таблицу + индекс
→ План: [1-2 предложения что именно делаю]
→ Жду подтверждение перед кодом
```

**ВАЖНО:** при конфликте между агентами — явно покажи в чём разногласие и какое решение выбрал. Не прячь компромиссы.

---

## Чего НЕ делать

| ❌ Не делай | ✅ Делай вместо этого |
|---|---|
| Писать код без объяснения | Объясни план → дождись ОК → пиши |
| Alembic миграции | Raw SQL в `migrations/` |
| `time.sleep()` | `asyncio.sleep()` |
| Синхронный `requests` | `httpx.AsyncClient` |
| `print()` для отладки | `logging.getLogger(__name__)` |
| Хардкод конфигурации | `app/config.py` → Settings |
| `db.execute(text(f"...{var}..."))` | `db.execute(text("... :var"), {"var": var})` |
| Логирование токенов/сессий | Маскируй: `token[:8]...` |
| Менять интервалы очереди без спроса | Спроси — лимиты подобраны под Telegram API |
| Менять retry-логику FloodWait | Она работает — не трогай без явной просьбы |
| Менять debounce listener (3/5 мин) | Подобрано эмпирически |
| Один большой файл роутера | Разбивай по доменам в `routers/` |

---

## Архитектурные правила

### Async everywhere

- Все DB-операции через `async/await` + `AsyncSession`
- Dependency injection: `db: AsyncSession = Depends(get_db)`
- **Никогда** синхронные вызовы — ни `time.sleep()`, ни `requests`

### Telegram-сессии

- Хранятся в БД зашифрованными (`encryption_key` в .env, обработка в `services/encryption.py`)
- При работе с сессиями: try/except для `SessionPasswordNeededError`, `AuthKeyError`
- Не логируй содержимое сессий и encryption_key

### Очередь

- Очередь в PostgreSQL — **без Redis, без Celery**
- Воркер — asyncio task внутри API-процесса
- Несколько воркеров могут читать → `FOR UPDATE SKIP LOCKED`
- Лимиты, интервалы, fatigue factor — **не меняй без согласования**

### Миграции

- **Только raw SQL** в `migrations/`. Нумерация: `001_`, `002_`, ...
- Каждая миграция идемпотентна (`IF NOT EXISTS`, `IF EXISTS`)
- Не используй Alembic

### Безопасность

- API_KEY не попадает в логи
- SQLAlchemy ошибки → HTTPException (без stack trace наружу)
- Пароли и токены только через .env

---

## Code style

- Type hints обязательны на публичных функциях
- Docstrings для сервисных функций
- Именование: `snake_case`, модели — `CamelCase`
- Импорты: stdlib → third-party → local, разделены пустой строкой
- Commit messages: английский, формат `type: description` (feat, fix, refactor, docs, chore)
- Общение в коммитах и коде — английский. Общение со мной — русский.

---

## Шаблоны частых задач

### Новый эндпоинт

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

router = APIRouter(prefix="/something", tags=["something"])

@router.get("/")
async def get_items(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Model))
    return result.scalars().all()
```

### Новая миграция

```sql
-- migrations/NNN_description.sql
BEGIN;

CREATE TABLE IF NOT EXISTS new_table (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE existing_table
    ADD COLUMN IF NOT EXISTS new_col VARCHAR(255);

COMMIT;
```

### Работа с очередью

```python
from app.models import MessageQueue, QueueItemStatus

stmt = (
    select(MessageQueue)
    .where(MessageQueue.status == QueueItemStatus.pending)
    .order_by(MessageQueue.created_at)
    .limit(1)
    .with_for_update(skip_locked=True)
)
```
