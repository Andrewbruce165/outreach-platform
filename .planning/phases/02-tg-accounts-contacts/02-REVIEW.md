---
phase: 02-tg-accounts-contacts
reviewed: 2026-05-21T00:00:00Z
depth: standard
files_reviewed: 31
files_reviewed_list:
  - app/main.py
  - app/models/__init__.py
  - app/routers/check_contacts.py
  - app/routers/contacts.py
  - app/routers/folders.py
  - app/routers/health.py
  - app/routers/onboarding.py
  - app/routers/senders.py
  - app/routers/workspace.py
  - app/schemas/__init__.py
  - app/services/contact_check_worker.py
  - app/services/csv_import.py
  - app/services/listener.py
  - app/services/onboarding_state.py
  - app/services/queue.py
  - app/services/rotation.py
  - app/services/warmup.py
  - app/utils/auth.py
  - app/utils/phone.py
  - docker-compose.yml
  - migrations/013_phase2.sql
  - tests/conftest.py
  - tests/test_check_contacts.py
  - tests/test_contact_check_worker.py
  - tests/test_contacts.py
  - tests/test_csv_import.py
  - tests/test_folders.py
  - tests/test_listener_reconcile.py
  - tests/test_migration_013.py
  - tests/test_onboarding.py
  - tests/test_onboarding_state.py
  - tests/test_phone_normalization.py
  - tests/test_senders.py
findings:
  critical: 9
  warning: 11
  info: 6
  total: 26
status: issues_found
---

# Phase 02: Code Review Report — TG Accounts & Contacts

**Reviewed:** 2026-05-21
**Depth:** standard
**Files Reviewed:** 31
**Status:** issues_found

## Summary

Phase 2 routers (senders, folders, contacts, onboarding, check_contacts) и новая инфраструктура (CsvImport service, ContactCheckWorker, OnboardingState) выглядят аккуратно: workspace-isolation в новых endpoint'ах закрыта через `Depends(auth_dep)` + `WHERE workspace_id = ctx.workspace_id`. Phone normalization и CSV import написаны без `pandas`/`phonenumbers` — корректно для v1, edge cases покрыты тестами.

Однако обнаружено **9 BLOCKER-уровня дефектов**, большая часть которых — **сбои мультитенантной изоляции в унаследованных worker'ах** (queue, listener, warmup, rotation): после миграции 012 эти таблицы получили `workspace_id NOT NULL`, но воркеры всё ещё делают `INSERT` без него и `SELECT` без фильтрации. Это значит:

1. Любая успешная отправка через `queue_worker` упадёт с `NotNullViolation` при `INSERT INTO messages_log`/`conversations` — продакшен-блокер.
2. `ContactCheckWorker` (новый, Phase 2) считывает sender'ов по `JOIN LATERAL` без `workspace_id` фильтра в JOIN — изоляция держится только на матчинге `s.workspace_id = c.workspace_id` через JOIN-условие, формально OK, но при ошибочной правке легко сломать (см. CR-08).
3. `listener.py` пишет `conversations` без workspace_id — те же `NotNullViolation`.

Также найдены ключевые баги в reauth (всегда пытается создать нового sender со slug-конфликтом), SQL precedence error в warmup, неправильное приведение `last_used_at = func.now()` (даст SQL-выражение в Python-поле), небезопасный `/health` endpoint без workspace-фильтра.

Phase 2 как изолированная feature — близка к готовности, но без устранения BLOCKER-ов унаследованных worker'ов **отправка сообщений и AI-listener в multi-tenant режиме не работают**.

---

## Critical Issues

### CR-01: queue.py пишет MessageLog/conversations без workspace_id — NotNullViolation на первом успешном send

**Файл:** `app/services/queue.py:434-442`, `:652-660`, `:685-698`
**Issue:** После миграции 012 (`migrations/012_workspace.sql:64-69, 85-97`) колонки `messages_log.workspace_id` и `conversations.workspace_id` имеют `NOT NULL` без default. ORM-модель `MessageLog` (`app/models/__init__.py:102-120`) тоже декларирует `workspace_id` как `nullable=False`. Однако `queue.py` создаёт `MessageLog(...)` без `workspace_id` (строки 434-442 и 652-660), и `INSERT INTO conversations` (строка 685-698) тоже без него. Результат: первый успешный send в multi-tenant БД упадёт с `NotNullViolationError`, queue item уйдёт в retry-loop. Это полный продакшен-блокер: ни одно сообщение не дойдёт до получателя.

Параллельно: `message_queue.workspace_id` тоже `NOT NULL` (migration 012), но `enqueue_message`/`enqueue_file` (`queue.py:762-797`, `:800-837`) создают `MessageQueue(...)` без workspace_id — упадёт при `enqueue`.

**Fix:**
```python
# enqueue_message / enqueue_file — добавить параметр и передать в MessageQueue:
async def enqueue_message(db, workspace_id, sender_id, sender_slug, ...):
    item = MessageQueue(
        workspace_id=workspace_id,
        sender_id=sender_id,
        ...
    )

# _send_item: log_entry & INSERT conversations — пробрасывать sender.workspace_id:
log_entry = MessageLog(
    workspace_id=sender.workspace_id,
    sender_id=sender.id,
    ...
)

# _upsert_conversation: добавить :wid в SQL INSERT и оба SELECT
INSERT INTO conversations
    (workspace_id, sender_id, contact_phone, ...)
VALUES (:wid, :sid, :phone, ...)
```

---

### CR-02: listener.py пишет conversations без workspace_id — NotNullViolation для каждого входящего

**Файл:** `app/services/listener.py:407-414`
**Issue:** Аналогично CR-01: `INSERT INTO conversations (sender_id, contact_phone, contact_name, contact_telegram_id, ai_enabled, ai_context_id) VALUES (...)` не указывает `workspace_id`, но колонка `NOT NULL`. Каждое входящее сообщение для нового контакта будет упасть → AI-listener не сможет создать ни один диалог в multi-tenant БД.

Также: `messages` (отдельная таблица, не `messages_log`) скорее всего тоже получила `workspace_id` через миграции (см. шаблон 012 — `messages_log.workspace_id NOT NULL`). Файла `messages` я не вижу в моделях, но `INSERT INTO messages` (`listener.py:449-460`, `queue.py:705-714`) использует raw SQL — если таблица расширена через миграцию 012/прошлые миграции workspace, эти inserts тоже упадут.

**Fix:**
```python
# get_or_create_conversation: пробросить workspace_id из senders
SELECT ..., s.workspace_id FROM senders s WHERE s.id = :sender_id
# затем
INSERT INTO conversations (workspace_id, sender_id, ...)
VALUES (:wid, :sender_id, ...)
```

---

### CR-03: rotation.py не передаёт workspace_id в INSERT context_contact_assignments

**Файл:** `app/services/rotation.py:96-103`
**Issue:** `context_contact_assignments.workspace_id` стал `NOT NULL` в миграции 012 (`migrations/012_workspace.sql:127-129`), но `INSERT INTO context_contact_assignments (context_id, contact_phone, sender_id) VALUES (...)` не указывает workspace_id. Каждое **первое** обращение rotation для пары (context, contact) сломает БД-INSERT → ротация аккаунтов на основе AI-контекста не работает в multi-tenant.

Дополнительно: `_pick_best_sender` (`rotation.py:132-164`) не фильтрует по `s.workspace_id = :wid` — отдаёт senders из любого workspace, который привязан к данному context_id. Если context_id из workspace A — это OK через `s.ai_context_id = :ctx_id`, но миграция 012 не гарантирует консистентности workspace_id между AIContext и Sender. Нужен явный guard.

**Fix:**
```python
# get_or_assign_sender: принять workspace_id как параметр, передать вниз
INSERT INTO context_contact_assignments
    (workspace_id, context_id, contact_phone, sender_id)
VALUES (:wid, :ctx_id, :phone, :sid)
ON CONFLICT (workspace_id, context_id, contact_phone) DO NOTHING

# _pick_best_sender: WHERE s.workspace_id = :wid AND s.ai_context_id = :ctx_id ...
```

---

### CR-04: warmup.py — все INSERT'ы без workspace_id; SQL precedence bug

**Файл:** `app/services/warmup.py:327-338, 421-434, 528-534`
**Issue 1 (NotNullViolation):** `INSERT INTO warmup_messages` и `INSERT INTO warmup_sessions` не указывают workspace_id, но миграция 012 добавила колонку как `NOT NULL` (`migrations/012_workspace.sql:106-118`). Warmup-worker сломает любую попытку создать сессию или сохранить сообщение.

**Issue 2 (SQL precedence bug — функциональный баг):** строки 528-534:
```sql
UPDATE warmup_sessions
SET next_message_at = :t, updated_at = NOW()
WHERE sender_a_id = :sid OR sender_b_id = :sid
  AND status = 'active'
```
`AND` имеет приоритет выше `OR`, поэтому это интерпретируется как `sender_a_id = :sid OR (sender_b_id = :sid AND status = 'active')` — то есть для sender_a будут обновлены и **завершённые** сессии тоже. При FloodWait отложатся сессии в статусе 'completed' — мусор и неверная семантика.

**Issue 3 (workspace isolation в `_get_active_pool`):** `warmup.py:167-176` SELECT по warmup_pool + senders НЕ фильтрует по workspace_id — worker процессит ВСЕ workspaces, парит контакты разных tenants между собой. Это утечка между tenants на уровне Telegram-сообщений (sender из workspace A пишет sender'у из workspace B!). Должно быть partitioning по workspace_id.

**Fix:**
```python
# warmup_messages INSERT:
INSERT INTO warmup_messages (workspace_id, session_id, from_sender_id, to_sender_id, message_text)
VALUES (:wid, :session_id, :from_id, :to_id, :text)

# warmup_sessions INSERT — workspace_id из first sender (оба должны быть из одного workspace, проверить!).

# SQL precedence — обернуть в скобки:
WHERE (sender_a_id = :sid OR sender_b_id = :sid)
  AND status = 'active'

# _create_new_sessions / _get_active_pool: партиционировать по workspace_id —
# pairs создавать только внутри одного workspace.
```

---

### CR-05: Reauth flow всегда пытается создать НОВОГО sender'а — глобально-уникальный slug ловит конфликт

**Файл:** `app/routers/onboarding.py:447-458, 502-513, 616-618` (reauth + qr-reauth + _wait_for_qr)
**Issue:** `_create_sender_from_session` всегда выполняет `Sender(...) → db.add(sender) → db.commit()` с slug=`sender-{tg_id}` (`onboarding.py:223-224`). Endpoint `/reauth/{sender_slug}` и `/reauth/qr/{sender_slug}` нужны **именно** для обновления session_string существующего sender'а — но они вызывают `_create_sender_from_session` после `verify-code`. Поскольку `Sender.slug` — globally unique (`models/__init__.py:80`, `unique=True`), второй INSERT с тем же telegram_id даст `IntegrityError`. Reauth не работает.

Также: даже если slug вычислялся бы из `sender_slug` (URL-параметра), `_create_sender_from_session` всё равно делает INSERT, а не UPDATE — старая запись остаётся со старым (мёртвым) `session_string`. Это блокер для всего auth-recovery UX.

**Fix:**
```python
# Разделить _create_sender_from_session на create_path и update_path:
async def _refresh_sender_session(db, sender: Sender, client, session_row):
    """Reauth: обновить только session_string + auth_status, оставить slug/name/role."""
    sender.session_string = encrypt_session(client.session.save())
    sender.auth_status = "ok"
    sender.proxy = session_row.proxy  # если поменялся
    await db.commit()

# reauth_start/qr — в verify-code branch отдельная ветка:
if request.session_id matches reauth-session:
    await _refresh_sender_session(db, original_sender, client, session_row)
else:
    await _create_sender_from_session(...)

# Для этого session_row нужно хранить либо sender_id (новая колонка),
# либо `is_reauth=True` флаг.
```

Эта ветка вообще не покрыта тестами в `tests/test_onboarding.py` — нужно добавить интеграционный тест reauth.

---

### CR-06: workspace_id отсутствует во всех SELECT и UPDATE в `queue.py`, `warmup.py`, `rotation.py`

**Файл:** `app/services/queue.py` (весь), `app/services/warmup.py` (весь), `app/services/rotation.py` (весь)
**Issue:** Сквозной поиск (`grep "workspace_id" app/services/{queue,warmup,rotation}.py`) **не возвращает ни одной строки**. Это значит:

1. `queue.py::_tick`: `SELECT DISTINCT sender_id FROM message_queue WHERE status='pending'` — собирает всё, не разделяя по workspace. OK для процессинга (sender_id уникален), но при ошибочном sender-id из другого tenant — данные могут пересечься.
2. `queue.py::_check_rate_limits`: считает messages из ВСЕХ workspaces (хотя по sender_id это и так одна сторона; sender принадлежит одному workspace, поэтому это формально безопасно — но защита-в-глубину отсутствует).
3. `warmup.py::_get_active_pool` (CR-04) — реальная утечка между tenants.
4. `rotation.py::_pick_best_sender` (CR-03) — может выбрать sender'а из чужого workspace.

В целом — отсутствие workspace_id в worker-слое нарушает **главный security model** проекта (см. CLAUDE.md: "Мультитенантность (основная работа)"). Каждый worker должен либо: (a) партиционировать обработку по workspace_id, (b) добавить явные WHERE-guards.

**Fix:** см. CR-01..CR-04 для конкретных query. Общий принцип: в каждом SELECT/UPDATE workers'ов добавить `AND workspace_id = :wid` либо JOIN-guard, как в `contact_check_worker.py:115-122` (`JOIN LATERAL ... WHERE s.workspace_id = c.workspace_id`).

---

### CR-07: `/api/v1/health` показывает sender-counts всех workspaces без auth

**Файл:** `app/routers/health.py:34-42`
**Issue:** `select(Sender)` без workspace-filter, endpoint без `Depends(auth_dep)`. Любой анонимный пользователь, дернув `/api/v1/health`, увидит общее число sender'ов в системе и сколько из них активны. Для конкурента/злоумышленника — это business intelligence (масштаб платформы, прирост клиентов). Information disclosure уровня multi-tenant SaaS.

Также: `total = len(senders)` тащит ВСЕ строки в Python — растёт O(N) с числом sender'ов в системе.

**Fix:**
```python
# Вариант 1 (минимальный — Phase 1 фикс): убрать senders-stats из public health.
@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    db_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "disconnected"
    return {"status": "healthy" if db_status == "connected" else "unhealthy",
            "database": db_status, "version": VERSION, ...}

# Вариант 2: вернуть подробности только за auth_dep + workspace-scoped (как и
# было в legacy /health/detailed, см. TODO в health.py:61).
```

---

### CR-08: ContactCheckWorker — потенциальный race на _apply_results между двумя контейнерами

**Файл:** `app/services/contact_check_worker.py:97-173, 175-244`
**Issue:** Worker SELECTит pending контакты с `LIMIT :n` без `FOR UPDATE SKIP LOCKED`. При горизонтальном масштабировании (несколько api-контейнеров) или просто двух экземплярах одного процесса (тесты + продакшен) два worker'а возьмут одни и те же `tg_status='pending'` контакты в SELECT, оба позовут CheckerService (с lock на checker_slug — это спасает Telethon-уровень от двойного вызова), но оба сделают UPDATE → tg_checked_at будет переписан дважды, лишний расход checker rate-limit.

Менее критично сейчас (один api-container в v1), но архитектурно — race заложен.

Также: `_apply_results` использует **другую** AsyncSessionLocal-сессию (`:189`), чем SELECT в `_tick` (`:97`). Между ними никакого lock'а: контакт мог быть удалён юзером, и UPDATE по contact_id перепишет... ничего (0 rows). OK для idempotency, но `tg_checked_at = NOW()` для несуществующих строк — log shows `checked=N` но реально обновлено меньше. Хотя бы залогировать `rowcount`.

**Fix:**
```sql
-- В SELECT добавить FOR UPDATE SKIP LOCKED на contacts:
SELECT c.id AS contact_id, ...
FROM contacts c
JOIN LATERAL (...) s ON TRUE
WHERE c.tg_status = 'pending' AND c.phone IS NOT NULL
ORDER BY c.created_at ASC
LIMIT :n
FOR UPDATE OF c SKIP LOCKED  -- ← добавить

-- Или сначала пометить контакты status='processing' и потом резолвить
-- (как делает queue.py для message_queue).
```

---

### CR-09: AuthCtx._verify_api_key не сбрасывает rate / не имеет защиты от timing attack

**Файл:** `app/utils/auth.py:191-246`
**Issue 1 (timing attack):** `_verify_api_key` итерирует по `candidates` и зовёт `bcrypt.checkpw` для каждого. Время ответа зависит от того, на каком кандидате matched. Учитывая `prefix = raw_token[:12]` (4 char "wsk_" + 8 random), prefix space = 62^8 ≈ 2e14 — collision крайне маловероятна, кандидатов обычно один. НО: атакующий может подсчитать timing разницу между "prefix не существует" (один SELECT, нет bcrypt) и "prefix существует, но pass неверный" (SELECT + 1 bcrypt) → enumerate валидных prefix'ов. С учётом 8 случайных chars это всё равно секьюрно, но защита-в-глубину требует константного timing.

**Issue 2 (rate-limit):** Эндпоинт принимает любую wsk_* строку и тратит CPU на bcrypt (~100ms каждый) — никакого rate-limiting. Brute force на знакомый prefix → 10 RPS = 600 bcrypt/мин = заметная CPU-нагрузка на api-container.

**Issue 3 (last_used_at):** `candidate.last_used_at = func.now()` (`auth.py:222`) присваивает SQL-expression объекту Python, потом `await db.commit()`. SQLAlchemy запишет `func.now()` как scalar expression при flush — но это **анти-pattern**: правильно `last_used_at=datetime.now(timezone.utc)`. Сейчас работает (SQLAlchemy умеет), но грязно и зависит от того, что db-сессия живёт до commit'а.

**Fix:**
```python
# 1. Использовать hmac.compare_digest на prefix-only stage (constant-time лookup).
# 2. Добавить rate-limit middleware (fastapi-limiter или slowapi) per IP.
# 3. last_used_at — заменить на datetime.now(timezone.utc):
candidate.last_used_at = datetime.now(timezone.utc)
await db.commit()
```

---

## Warnings

### WR-01: Sender model lacks `telegram_id` column — divergence with migration 006

**Файл:** `app/models/__init__.py:73-99`
**Issue:** Миграция 006 добавляет `senders.telegram_id BIGINT`, и `listener.py:1053-1058` пишет туда через raw SQL `UPDATE senders SET telegram_id = :tg_id WHERE id = :sid`. Но ORM-модель Sender (строки 73-99) не декларирует этот столбец. Это значит:
- ORM не может прочитать telegram_id sender'а (придётся всегда через raw SQL).
- `selectinload(Sender.contacts)` или другие eager-loads не подтянут telegram_id.
- `warmup.py:491-495` и `listener.py:500-502` читают `senders.telegram_id` через raw SQL — OK сейчас, но если кто-то добавит `from app.models import Sender` и попытается `sender.telegram_id` — `AttributeError`.

**Fix:**
```python
class Sender(Base):
    ...
    telegram_id = Column(BigInteger, nullable=True, index=True)
```

---

### WR-02: Sender.slug globally unique — нарушение multi-tenant изоляции

**Файл:** `app/models/__init__.py:80`, `app/routers/senders.py:206-215`
**Issue:** `slug = Column(String(50), unique=True, ...)` означает: workspace B не может создать sender с тем же slug, что и workspace A. Information disclosure: пытаясь создать `sender-john`, attacker узнаёт, что такой slug уже занят в **другом** tenant. Также — мешает legitimate use case (sender "main" в каждом workspace).

В Phase 2 это явно противоречит multi-tenant model: должен быть `UNIQUE (workspace_id, slug)`.

**Fix:**
```python
# models/__init__.py:
slug = Column(String(50), nullable=False)  # убрать unique=True
# + добавить в миграции:
ALTER TABLE senders DROP CONSTRAINT IF EXISTS senders_slug_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_senders_workspace_slug
    ON senders(workspace_id, slug);

# routers/senders.py::create_sender: добавить workspace_id фильтр:
existing = await db.execute(
    select(Sender).where(
        Sender.slug == request.slug,
        Sender.workspace_id == ctx.workspace_id,
    )
)
```

---

### WR-03: `MAX_NEW_CONTACTS_PER_HOUR` захардкожен и не учитывает D-13 per-sender rate

**Файл:** `app/services/queue.py:46, 298-313`
**Issue:** В Phase 2 (D-13) `senders.rate_per_*` пер-sender. Но `MAX_NEW_CONTACTS_PER_HOUR=15` остался глобальной константой, не выносится в senders-таблицу. Все sender'ы одного workspace используют один global cap → ломает кастомизацию per-sender, заявленную в D-13.

**Fix:** добавить колонку `senders.unique_contacts_per_hour INT DEFAULT 15` в Phase 4-миграцию или сейчас в 013. Сейчас как минимум залогировать TODO рядом с константой.

---

### WR-04: `senders.py::delete_sender` — TX safety: cascade-DELETE батчем без транзакции

**Файл:** `app/routers/senders.py:354-389`
**Issue:** Endpoint выполняет 4 раздельных DELETE через `db.execute(text(...))` + финальный `db.delete(sender)` + единственный `await db.commit()`. Если падает на 3-й DELETE (например, БД timeout), первые 2 уже зафиксированы? Нет — `db.commit()` в конце, поэтому всё в одной TX. **Однако**: один из DELETE — `DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE sender_id = :sid)` — обращается к таблице `messages`, которой нет в моделях/миграциях, представленных в этом review (есть только `messages_log`). Если таблицы нет — будет `UndefinedTable`, и весь DELETE-чейн упадёт с rollback (sender НЕ будет удалён).

Связано с CR-02 (упоминание INSERT INTO messages в queue/listener) — таблица `messages` где-то определена в более ранней миграции (вероятно `001_add_unique_constraint_messages.sql`), но не отражена в Phase 2 моделях. Это рассинхрон ORM ↔ DB schema.

**Fix:** проверить наличие таблицы `messages` через `information_schema` в новом тесте. Если есть — добавить в `app/models/__init__.py` модель Message с `workspace_id`. Если нет — удалить DELETE из senders.py.

---

### WR-05: Workspaces deletion cascades to workspace_api_keys but bcrypt hashes are bcrypt, not BLAKE — bcrypt(12-round) на каждом /push

**Файл:** `app/utils/auth.py:213-220`
**Issue:** Для каждого `X-Workspace-Key` запроса (включая high-throughput n8n push на `/api/v1/contacts`) делается `bcrypt.checkpw` в `asyncio.to_thread` (≈ 60-150 мс CPU на 12 rounds). На потоке push в 100 RPS это съест 1-2 CPU полностью. Нет кеша валидных токенов.

В Phase 2 это особенно критично — `POST /api/v1/contacts` (push) — основной integration entry point. n8n будет валить туда тысячи запросов за CSV-импорт.

**Fix:**
```python
# Add in-process LRU cache for validated tokens (5-min TTL):
from functools import lru_cache
import time

_TOKEN_CACHE: dict[str, tuple[AuthCtx, float]] = {}

async def _verify_api_key(db, raw_token):
    cached = _TOKEN_CACHE.get(raw_token)
    if cached and (time.time() - cached[1]) < 300:
        return cached[0]
    # ... existing bcrypt path ...
    _TOKEN_CACHE[raw_token] = (ctx, time.time())
    return ctx
```

---

### WR-06: csv_import.apply_import не валидирует encoding fallback

**Файл:** `app/services/csv_import.py:150-186`
**Issue:** `apply_import` принимает `encoding: str = "utf-8-sig"` и **слепо** делает `text = file_bytes.decode(encoding)` (строка 184). Если CSV preview сохранил `encoding="cp1251"` в БД, но юзер успел изменить файл (хотя файл уже в БД — но мало ли) или encoding передаётся через mapping — может упасть с UnicodeDecodeError. Это контракт: encoding из БД должен быть валиден. Но нет fallback на cp1251 как в `parse_preview`, и UnicodeDecodeError не маппится в `MAPPING_INVALID` 422 — поднимется как 500.

**Fix:**
```python
def apply_import(file_bytes, mapping, delimiter=",", encoding="utf-8-sig"):
    try:
        text = file_bytes.decode(encoding)
    except UnicodeDecodeError:
        # Fallback на cp1251 — same chain as parse_preview.
        try:
            text = file_bytes.decode("cp1251")
        except UnicodeDecodeError:
            raise ValueError("INVALID_ENCODING")
```

---

### WR-07: Onboarding `_normalize_phone` дублирует логику `phone.normalize_to_e164`

**Файл:** `app/routers/onboarding.py:116-131` vs `app/utils/phone.py:18-54`
**Issue:** Two implementations of phone normalization: одна "light" в `onboarding.py` (только strip+`+`), другая полноценная E.164 в `app/utils/phone.py`. Комментарий в onboarding.py: "Full normaliser will live in app/utils/phone.py (plan 02-04)" — но plan 02-04 уже мерджен, файл `app/utils/phone.py` существует. Не консолидировано. Это значит:
- Пользователь шлёт `89001234567` → onboarding оставляет `+89001234567` → Telethon вернёт `PhoneNumberInvalidError` → юзер видит 400, хотя CSV-импорт того же номера через `/contacts` нормализуется в `+79001234567`.

**Fix:**
```python
# onboarding.py: убрать _normalize_phone, импортировать normalize_to_e164:
from app.utils.phone import normalize_to_e164

# в start_onboarding / reauth_start заменить _normalize_phone(request.phone) на
# normalize_to_e164(request.phone)
```

---

### WR-08: ContactCheckWorker — нет workspace-id partitioning при batch'е через groupby

**Файл:** `app/services/contact_check_worker.py:136-173`
**Issue:** `groupby(rows_sorted, key=lambda r: r.checker_id)` — если в одном tick'е по разным workspace_id попало пара checker'ов с одинаковыми UUID (теоретически возможно через collision атаку, или человеческая ошибка при ручной правке через psql), то группа объединит контакты из РАЗНЫХ workspace и отправит их в один `check_phones(checker_slug=...)`. UUID collision крайне маловероятна — поэтому это WR, не CR. Но защита-в-глубину рекомендует groupby по `(workspace_id, checker_id)`.

**Fix:**
```python
rows_sorted = sorted(rows, key=lambda r: (str(r.workspace_id), str(r.checker_id)))
for (wsid, checker_id), items_iter in groupby(rows_sorted, key=lambda r: (r.workspace_id, r.checker_id)):
    ...
```

---

### WR-09: Onboarding QR `_wait_for_qr` использует _Ctx-stand-in, теряет role

**Файл:** `app/routers/onboarding.py:611-618`
**Issue:** В QR-флоу background task вызывает `_create_sender_from_session(db, _Ctx(workspace_id), row, client)`. `_Ctx` имеет только workspace_id. `_create_sender_from_session` читает `session_row.role` (строка 233) — OK. Но если в `_create_sender_from_session` появится новая зависимость от ctx (например, `ctx.user_id` для audit log) — qr-flow её не получит. Это технический долг.

**Fix:** ввести `OnboardingCtx = namedtuple("OnboardingCtx", ["workspace_id", "user_id", "source"])` и для QR использовать `OnboardingCtx(workspace_id=workspace_id, user_id=None, source="qr-background")`.

---

### WR-10: `senders.py::list_senders` order_by Sender.name without index — slow on N≥1000

**Файл:** `app/routers/senders.py:182-192`
**Issue:** `ORDER BY senders.name` без индекса на `(workspace_id, name)`. При больших workspace (1k+ sender'ов) это полный sequential scan + сортировка. Out of scope для v1, но для деманд-страниц UI пагинация необходима. Также нет `LIMIT/OFFSET` — UI получает все sender'ы сразу.

**Fix:** add pagination params + index on `(workspace_id, name)`. Не критично для v1.

---

### WR-11: `_apply_results` не использует bulk update — N запросов вместо одного

**Файл:** `app/services/contact_check_worker.py:188-244`
**Issue:** Для batch=5 контактов делается до 5 индивидуальных UPDATE'ов (`for item in items: await db.execute(UPDATE ... WHERE id = :cid)`). При увеличении CONTACT_CHECK_BATCH_SIZE (env override) это O(N) round-trip'ов к БД. Можно одним CASE-WHEN или unnest'ом.

Не v1-блокер, но workers — основная нагрузка БД.

**Fix:** обновлять одним statement через `UPDATE ... FROM (VALUES (...)) AS data(cid, status, ...) WHERE contacts.id = data.cid`.

---

## Info

### IN-01: TODO(v2-rls) comments scattered — нет единого трекера

**Файл:** многие роутеры (`contacts.py:188, 466, 519, 549`, `folders.py:88, 145, 169, 222`, `senders.py:161, 186, 222, 413, 525, 586`, `workspace.py:137, 166, 203, 287, 322`, `onboarding.py:146, 650, 691`, `auth.py:138, 209`)
**Issue:** Стилистическое: ~20 разбросанных `# TODO(v2-rls): replaced by RLS policy`. Можно консолидировать в одну строку в DOC `app/utils/auth.py` или в migration plan — иначе при переходе на RLS будет нужен большой `grep`-passing.
**Fix:** Создать `.planning/RLS_MIGRATION_TODO.md` с перечнем endpoint'ов, где сейчас app-level filter.

---

### IN-02: `MessageLog.extra_data = Column(JSONB, default={})` — mutable default arg антипаттерн

**Файл:** `app/models/__init__.py:116, 160, 195`
**Issue:** `default={}` или `default=[]` в SQLAlchemy column — формально SQLAlchemy сериализует один раз и не разделяет ссылки, но это всё равно scary. Лучше `default=dict` (callable) или `server_default='{}'`.
**Fix:**
```python
extra_data = Column(JSONB, default=dict)  # или server_default=text("'{}'::jsonb")
```

---

### IN-03: `app/main.py` lifespan не делает graceful shutdown listener-контейнера

**Файл:** `app/main.py:33-59`
**Issue:** API lifespan корректно стартует и стопает queue/warmup/onboarding/contact_check workers, но `listener` живёт отдельным контейнером. Если api-container перезапускается — listener теряет связь с API DB-pool, но reconcile_loop (`listener.py:1194`) должен подхватить (D-18). Не баг, но в shutdown лога нет проверки, что reconcile knows about it.

---

### IN-04: Pydantic ContactBase has mutable default `custom: dict = {}`

**Файл:** `app/schemas/__init__.py:206`
**Issue:** Аналогично IN-02 для Pydantic — `custom: dict = {}` shared между instances. Pydantic v2 справляется через свой механизм, но лучше явно через `Field(default_factory=dict)`.
**Fix:** `custom: dict = Field(default_factory=dict)`.

---

### IN-05: csv_import._heuristic_no_header — heuristic comment vs реальность

**Файл:** `app/services/csv_import.py:117-130`
**Issue:** Docstring: "Возвращает True только когда 100% непустых ячеек проходят PHONE_LIKE_RE". Реализация согласна. НО `_PHONE_LIKE_RE = r"^[+\d][\d\s()-]{6,}$"` — match'ит много чего, что не телефон (например `+12345678` — 9-длинная строка из цифр). Если первая строка реального CSV — массив из числовых ID, heuristic сработает ложно-положительно. На UX уровне — юзер увидит, что предлагается no-header, и подтвердит вручную, поэтому не критично.

---

### IN-06: `_create_sender_from_session` оставляет client.disconnect() работу на caller

**Файл:** `app/routers/onboarding.py:202-247`
**Issue:** В `verify_code` (`onboarding.py:451`) сразу после `_create_sender_from_session` делается `await _safe_disconnect(client)`. ОК. Но в `_wait_for_qr` (`onboarding.py:616`) после `_create_sender_from_session` идёт `update_status → delete_session`, и только в `finally` (строка 626-628) — disconnect. Если update_status выбросит исключение, disconnect сработает. OK.

Стилистически — лучше делать disconnect внутри `_create_sender_from_session`'s `finally`, чтобы исключить ошибки в caller. Но это small.

---

_Reviewed: 2026-05-21_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
