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

> **ВАЖНО:** `/root/apps/telegram-api` и `/root/apps/outreach-platform` — **остановлены** (2026-06-24). Все 13 Telegram-аккаунтов перешли в этот проект. `restart: "no"` в их docker-compose — не запускать, иначе конфликт сессий с нашим листенером. Запланирована задача по удалению этих директорий (см. ниже).

### Задача: удалить старые проекты

**Что делать:**
1. Убедиться что все нужные Telegram-сессии перенесены в `outreach_platform` (проверить что аккаунты работают)
2. Сделать бэкап данных из `telegram_followup` БД если нужна история
3. Удалить `/root/apps/telegram-api/` и `/root/apps/outreach-platform/`
4. Убрать строку про `telegram-api` из `/root/CLAUDE.md`

**Почему:** оба проекта используют те же 13 Telegram-аккаунтов. Если запустить — листенеры начнут перехватывать сообщения друг у друга и дублировать AI-ответы.

**Цель v1:** Первый платящий внешний клиент — подключает свои Telegram-аккаунты, настраивает AI, запускает аутрич самостоятельно.

---

## Стек

- Python 3.11+, FastAPI, SQLAlchemy 2.0 async, PostgreSQL 16
- Telethon (Telegram MTProto), OpenAI (gpt-4o-mini)
- Docker Compose: 3 сервиса — db, api, listener
- Фронт: TanStack Start (React, TypeScript, Vite, bun, shadcn), генерится через Lovable. Отдельный репо `AGS-Venture-Lab/aimly-tg-outreach`, локально склонирован сиблингом в `/root/apps/aimly/aimly-tg-outreach`. Деплой — Cloudflare (`wrangler.jsonc`)
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
- **Pool health**: `GET /campaigns/{id}` → поле `pool_health {active, paused, total, earliest_resume_at}` + каждый `attached_senders[]` несёт `restriction_status` + `restricted_until` (Phase 10)
- **Restriction audit**: append-only лог `sender_restriction_events` — пишется с 2026-06-24, все события ограничений/снятий с activity-срезом (Phase 10)

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
- **Миграции**: только raw SQL в `migrations/`. Нумерация `NNN_short_name.sql`. **Авто-применяются** при старте api через `app/database.py::_apply_migrations` (с 2026-05-26). Трекинг — таблица `schema_migrations`. Файл обязан быть идемпотентным (`IF NOT EXISTS`, `DO $$ … EXCEPTION duplicate_object $$`, `ON CONFLICT DO NOTHING`) — applier повторно запускает при любом drift. Если миграция падает, api **не стартует** (fail-fast, не half-applied). Никогда Alembic.
- **Никогда**: time.sleep(), синхронный requests, print() вместо logging
- **Безопасность**: сессии зашифрованы, API_KEY не в логах
- **Очередь**: не трогать интервалы без явного обсуждения — подобраны эмпирически
- **Retry-логика FloodWait**: не ломать без явной просьбы
- **Тесты**: запускать ТОЛЬКО через test-overlay, иначе conftest guard блокирует:
  ```
  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
  ```
  db-test — эфемерный postgres в tmpfs, после run исчезает. Никогда `docker compose run --rm api pytest` без overlay — DATABASE_URL уйдёт в прод. См. `tests/conftest.py::_setup_database` и memory `feedback_pytest_drop_schema_prod.md`.

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
**GitHub (бэкенд):** git@github.com:Andrewbruce165/outreach-platform.git

**Фронтенд (отдельный репо):** `/root/apps/aimly/aimly-tg-outreach` — origin `https://github.com/AGS-Venture-Lab/aimly-tg-outreach.git`. Это **независимый** репо: коммиты внутри этой папки летят в `AGS-Venture-Lab/aimly-tg-outreach`, коммиты в `tg-outreach` (включая `.planning/`) — в `Andrewbruce165/outreach-platform`. Карта кода `.planning/codebase/` описывает оба репо как единую систему.

### Сетевая топология (важно)

Прод-домен: **`https://aimly.agsventurelab.com`**

Хост-порт `:443` занят stream-блоком nginx (SNI-диспетчер MTProto-camouflage для других сервисов). Поэтому:

- API-контейнер биндится на `127.0.0.1:8005:8000` (порт 8000 занят старым telegram-api)
- nginx vhost для домена слушает `127.0.0.1:8444 ssl proxy_protocol` (за SNI-диспетчером, шаблон funnel-api)
- Цепочка: `:443 → SNI stream → nginx:8444 ssl proxy_protocol → 127.0.0.1:8005 → api:8000`
- TLS выпускается **только** через `certbot certonly --webroot` (НЕ `--nginx`, иначе сломает SNI stream-схему). Автопродление — `certbot.timer`.

При добавлении новых доменов/сервисов: брать конфиг по шаблону `funnel-api` и согласовывать с devops.

---

## Operations & Recovery

### Бэкапы

- **Скрипт:** [`/root/apps/aimly/tg-outreach/backup.sh`](backup.sh) — `pg_dump outreach_platform --clean --if-exists | gzip`.
- **Cron:** `5 3 * * *` в `crontab -l` (root). Лог: `/var/log/outreach_backup.log`.
- **Дамп-папка:** `/root/backups/tg-outreach/outreach_YYYYMMDD_HHMMSS.sql.gz`, retention 14 дней.
- **Ручной dump:** `/root/apps/aimly/tg-outreach/backup.sh` — выполняется секунду.
- **Восстановление:**
  ```bash
  gunzip -c /root/backups/tg-outreach/outreach_<TS>.sql.gz | \
    docker exec -i outreach-platform-db psql -U outreach_user -d outreach_platform
  ```
  `--clean --if-exists` в pg_dump делает restore идемпотентным.

### Auto-applier миграций

При каждом старте api `app/database.py::init_db()`:
1. `Base.metadata.create_all` — создаёт ORM-таблицы (no-op если есть).
2. `_apply_migrations()` — прогоняет все `migrations/*.sql` не записанные в `schema_migrations`, в lexical порядке, под `pg_advisory_lock`.

**Чтобы добавить миграцию:** положить файл `NNN_short_name.sql` в `migrations/`, ребилд api (`docker compose up -d --build api`) — applier подхватит. Идемпотентность обязательна (`IF NOT EXISTS`, `DO $$ EXCEPTION duplicate_object $$`). Если миграция падает, api **не стартует** (fail-fast).

**После прод-инцидента (DROP / fresh DB):** просто `docker compose up -d --build api` — applier восстановит схему с нуля.

### log_statement = ddl

`docker-compose.yml::services.db.command` запускает postgres с `log_statement=ddl` + `log_min_duration_statement=1000`. Любой `CREATE/ALTER/DROP/TRUNCATE` и любой запрос >1s виден в `docker logs outreach-platform-db`. После 2026-05-26 инцидента (DROP SCHEMA из pytest конfтеста ушёл в прод и был невидим в логах).

### Тесты

**Никогда не запускай** `docker compose run --rm api pytest` (DATABASE_URL → прод, conftest сделает DROP SCHEMA). Conftest-guard в `tests/conftest.py:46-77` блокирует это с явным RuntimeError, но **правильный путь — через test-overlay**:

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
```

[`docker-compose.test.yml`](docker-compose.test.yml) поднимает эфемерный `db-test` в tmpfs и переопределяет DATABASE_URL на `outreach_test`. После run контейнер удаляется автоматически.

### Диагностика schema drift

Признаки: `column X does not exist`, `relation Y does not exist`.

Проверка физических timestamps таблиц (видно когда был DROP+CREATE):
```sql
SELECT c.relname,
       (pg_stat_file('base/'||d.oid||'/'||c.relfilenode)).modification AS file_mtime
  FROM pg_class c
  JOIN pg_database d ON d.datname = current_database()
 WHERE c.relkind = 'r'
   AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
 ORDER BY file_mtime DESC;
```
Одинаковая секунда у всех relations → был DROP SCHEMA + create_all. **`n_tup_ins=0` в `pg_stat_user_tables` НЕ означает «никогда не вставлялось»** — это «после последнего DROP+CREATE»; счётчик сбрасывается.

### Lovable-фронт quirks

- **Frontend в отдельном репо**, генерится через Lovable из openapi.json (`lovable-handoff/openapi.json`). Иногда расходится со спецификацией.
- **`/conversations/{id}/send`** — Lovable шлёт `{"message_text": "..."}` вместо канонического `{"message": "..."}`. Pydantic-schema `SendMessageFromUIRequest` принимает оба варианта через `validation_alias=AliasChoices("message", "message_text")`.
- **`/telemetry/events`** — UI событие может прийти с unknown event-name → 400 `UNKNOWN_EVENT`. Whitelist в `app/routers/telemetry.py::_EVENT_WHITELIST` (17 событий). Если фронт добавляет новое событие — обновить whitelist + UI-SPEC §9.

### Telethon entity-cache cold start

При ручной отправке сообщения через `/conversations/{id}/send` (или любой другой путь по `telegram_id`) Telethon может упасть с `ValueError: Could not find the input entity for PeerUser(user_id=...)`. Это значит entity-cache в SQLite-сессии не содержит `access_hash` для этого peer'а.

Фикс в `TelegramService.send_message_by_telegram_id`: при `ValueError` от `get_input_entity` подгружаем `get_dialogs(limit=200)` — Telethon заполняет access_hash для всех recent dialogs. Дальше повторный `get_input_entity` находит peer. Стоит ~500ms первый раз, далее кеш горячий.

### Restriction Audit (Phase 10, с 2026-06-24)

Таблица `sender_restriction_events` — append-only лог всех ограничений аккаунтов.

```sql
-- Кто попал на ограничения за последние 7 дней и за что
SELECT s.slug, e.event_type, e.source, e.restricted_until,
       e.sends_1h, e.sends_24h, e.created_at
FROM sender_restriction_events e
JOIN senders s ON s.id = e.sender_id
WHERE e.created_at > NOW() - INTERVAL '7 days'
ORDER BY e.created_at DESC;

-- Сколько сообщений слали до заморозки (activity slice)
SELECT s.slug, e.event_type, e.sends_1h, e.sends_24h, e.created_at
FROM sender_restriction_events e
JOIN senders s ON s.id = e.sender_id
WHERE e.event_type IN ('frozen', 'spam_limited')
ORDER BY e.created_at DESC LIMIT 20;
```

**API:** `GET /senders/{slug}/restriction-events` — workspace-scoped, newest-first, limit 200.

**Поля event_type:** `spam_limited`, `frozen`, `cleared`, `extension`, `recipient_privacy`
**Поля source:** `queue_error`, `antispam_signal`, `reconcile`, `privacy_check`
**Важно:** данные только с 2026-06-24 (до этой даты лога нет, бэкфилла не было). Миграции: 030 (таблица), 031 (flood_wait category в CHECK).

### Семантика checker'а (is_registered)

- `contacts_cache.is_registered=false` = «номер НЕ резолвится по телефону сторонним (checker) аккаунтом», **НЕ** «нет Telegram-аккаунта».
- `PhoneNotOccupiedError` / пустой `ImportContacts` срабатывает и когда у владельца стоит «Кто может найти меня по номеру» = Контакты/Никто (приватность) → ложноотрицательный результат для зарегистрированных-но-приватных номеров. **Эта приватностная причина false-negative по-прежнему верна** (та же семантика продублирована в docstring `app/services/checker.py`).
- **Shadow-ban чекера (диагноз 2026-06-26, отменяет прежнюю запись «checker здоров» от 2026-06-23):** checker `sender-8428118140` получил **теневое ограничение Telegram contacts-API** за объёмный bulk-resolve и начал систематически возвращать ложноотрицательные на реальных, достижимых номерах. Рапортовал **2.5%** живых (53/2148) против настоящих **~26%** в целом и **~50%+** среди мобильных — занижение в **15–20×**, тысячи живых лидов молча списывались в мусор. Два режима троттла: мягкий burst (~45–50 быстрых резолвов подряд → редкие ложные «нет», восстановление минуты) и жёсткий shadow-ban (тысячи/день → почти всё ложное «нет» ~0.07%, восстановление дни). Полный диагноз, доказательства и калибровка онсета — `.planning/notes/checker-false-negatives.md`. Аккаунт запаркован (`restriction_status='spam_limited'`, `lifecycle_status='paused'`).
- Бакет `not_registered` содержит неизвестную долю false negatives (приватность + троттл чекера). Для холодного phone-import аутрича приватностные FN приемлемы (по телефону им всё равно не написать), но имя поля вводит в заблуждение — не строить на нём аналитику/дедуп/«мёртвый номер».
- **Phase 14 (Reliable Contact Resolution) перестроил резолв так, что false-negatives больше не финализируются вслепую:** выделенный пул checker-аккаунтов с health-probe на 49 заведомо-живых контролях (детект троттла по ≥2 промахам подряд → `spam_limited` + вывод из ротации), burst-кап + cooldown, restriction-gated selection (воркер пропускает чекеры с `restriction_status != 'none'` или `lifecycle_status='paused'` — закрыта дыра, из-за которой битый чекер продолжал врать), confidence/source на каждом резолве (`tg_confidence`/`tg_resolved_by`/`tg_probe_state`), и suspect-rollback: `not_registered` от подозрительного чекера откатывается в `pending` для перечека, а не финализируется. Кампании финализируют (пропускают контакт) `not_registered` только при high-confidence от чекера с чистой пробой.
- Единственный способ подтвердить приватный аккаунт — по `@username` (`ResolveUsernameRequest`, см. `check_usernames` в `app/services/checker.py`).

---

## Что делать в новой сессии

Контекст собран, PROJECT.md уже продуман. В новой сессии (локально):

1. Открыть папку outreach-platform в Claude Code
2. Запустить /gsd:new-project — сказать агенту что это brownfield SaaS Telegram-аутрич проект, контекст уже есть в этом CLAUDE.md, нужно создать .planning/PROJECT.md → REQUIREMENTS.md → ROADMAP.md
3. Первое решение для обсуждения: auth через Supabase (Lovable-сторона) или FastAPI JWT
