# Changelog — 2026-02-18

## Контекст
Telegram API для массовой отправки сообщений от нескольких аккаунтов (logistic, logistic2, logistic3, procurement).
Stack: FastAPI + SQLAlchemy async + Telethon + PostgreSQL + Docker Compose.

---

## 1. Rate limiting — очередь сообщений (Queue Worker)

**Проблема:** Прямая отправка 204 сообщений/час вызвала заморозку аккаунтов Telegram (FloodWait, затем бан).

**Решение:** Очередь на базе PostgreSQL + фоновый воркер.

### Что сделано:
- Новая таблица `message_queue` (модель `MessageQueue` в `app/models/__init__.py`)
  - Статусы: `pending → processing → sent / failed / cancelled`
  - Поля: `sender_slug`, `phone`, `text`, `callback_url`, `scheduled_at`, `attempt_count`, `result_*`
- Новый сервис `app/services/queue.py` — класс `QueueWorker`
  - Rate limits: **12 сек между сообщениями**, **5 msg/мин**, **30 msg/час** на аккаунт
  - `FOR UPDATE SKIP LOCKED` — безопасная параллельная обработка
  - Retry логика: до 3 попыток с экспоненциальным backoff
  - Webhook callback: POST на `callback_url` при успехе/ошибке (fire-and-forget)
- Новый роутер `app/routers/queue.py`
  - `GET /api/v1/queue/{queue_id}` — статус элемента
  - `GET /api/v1/queue/stats/{sender_slug}` — rate limit статистика
  - `DELETE /api/v1/queue/{queue_id}` — отмена
- Эндпоинты `/send` и `/send-file` теперь возвращают `EnqueueResponse` вместо прямого результата

**Итог:** Вместо мгновенной отправки запросы ставятся в очередь и отправляются с безопасным темпом ~5 сообщений/мин.

---

## 2. Замена ImportContactsRequest → ResolvePhoneRequest

**Проблема:** `ImportContactsRequest` добавляет контакт в адресную книгу Telegram — это сигнал для спам-детектора. При массовом использовании ускоряет бан.

**Решение:** `ResolvePhoneRequest` — только резолвит номер в user_id без импорта в контакты.

### Что сделано (`app/services/telegram.py`):
- `resolve_contact()`: заменён `ImportContactsRequest` на `ResolvePhoneRequest`
- Добавлено сохранение `access_hash` из результата резолвинга
- Новый столбец `access_hash BIGINT` в таблице `contacts_cache`
- `send_message()` и `send_file()`: используют `InputPeerUser(user_id, access_hash)` вместо голого `telegram_id`

**Итог:** Telegram больше не видит массовый импорт контактов. Исправлена ошибка `PeerUser entity not found` при отправке новым контактам.

---

## 3. Рабочие часы (09:00–20:00 МСК)

**Проблема:** Сообщения могли отправляться ночью, что выглядит подозрительно и неприятно для получателей.

**Решение:** `QueueWorker` проверяет московское время перед каждой отправкой.

### Что сделано (`app/services/queue.py`):
- `_is_working_hours()` — проверка текущего времени МСК
- `_next_working_window()` — расчёт ожидания до открытия окна
- Если вне рабочих часов — воркер спит до 09:00 МСК

**Итог:** Сообщения отправляются только с 9 утра до 8 вечера по Москве.

---

## 4. Callback URL (webhook вместо polling)

**Проблема:** n8n делал polling каждые 5 секунд для проверки статуса — избыточная нагрузка.

**Решение:** Передаём `callback_url` в запросе — API сам вызывает его когда готово.

### Что сделано:
- `callback_url: Optional[str]` добавлен в `SendMessageRequest` и `SendFileRequest`
- `QueueWorker._fire_callback()` — async HTTP POST при завершении (успех или ошибка)
- Payload включает: `queue_id`, `status`, `result_telegram_id`, `result_name`, `result_username`, `error`

**Итог:** n8n получает результат автоматически без polling. Задержка — только время обработки очереди.

---

## 5. Увеличение задержки AI-агента до 3 минут

**Проблема:** Агент отвечал через 5 секунд — слишком быстро, выглядит как бот.

**Решение:** Увеличены параметры дебаунса в `app/services/listener.py`.

### Что сделано:
- `DEBOUNCE_DELAY`: 5.0 → **180.0 сек** (3 минуты)
- `MAX_BUFFER_TIME`: 15.0 → **300.0 сек** (5 минут)

**Итог:** Агент отвечает через 3 минуты после последнего сообщения в диалоге, накапливая все сообщения в буфер.

---

## 6. Исправление ошибок

| Ошибка | Причина | Фикс |
|--------|---------|------|
| `AttributeError: 'TelegramService' has no attribute 'close_all'` | Несуществующий метод в shutdown | Удалён вызов из `app/main.py` |
| `UndefinedColumnError: column "callback_url" does not exist` | `init_db()` не делает ALTER TABLE | `ALTER TABLE message_queue ADD COLUMN IF NOT EXISTS callback_url TEXT` |
| `UndefinedColumnError: column "access_hash" does not exist` | То же | `ALTER TABLE contacts_cache ADD COLUMN IF NOT EXISTS access_hash BIGINT` |
| `PeerUser entity not found` | Telethon не кешировал пользователя из ResolvePhone | `InputPeerUser(user_id, access_hash)` вместо голого id |

---

## 7. Capacity (пропускная способность)

На каждый аккаунт:
- ~5 сообщений/мин, ~30 сообщений/час
- Рабочее окно: 11 часов/день (09:00–20:00 МСК)
- **~330 первых сообщений/день на аккаунт**

На 3 рабочих аккаунта (logistic, logistic3, procurement):
- **~990 первых сообщений/день**

---

## Файлы изменены/созданы

```
app/models/__init__.py       — MessageQueue модель, access_hash в ContactCache
app/schemas/__init__.py      — EnqueueResponse, QueueItemResponse, callback_url поля
app/services/queue.py        — NEW: QueueWorker, enqueue_message(), enqueue_file()
app/services/telegram.py     — ResolvePhoneRequest, InputPeerUser, access_hash
app/services/listener.py     — DEBOUNCE_DELAY 180s, MAX_BUFFER_TIME 300s
app/routers/send.py          — возврат EnqueueResponse, вызов enqueue_*()
app/routers/queue.py         — NEW: /queue/* эндпоинты
app/main.py                  — queue_worker start/stop, queue_router регистрация
```
