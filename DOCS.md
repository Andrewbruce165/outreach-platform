# Документация: Telegram AI Assistant API

**AGS Venture Lab** · v1.1.0
**Base URL:** `https://telegram-api.agsventurelab.com`
**Swagger UI:** `https://telegram-api.agsventurelab.com/docs`

---

## Содержание

1. [Обзор архитектуры](#1-обзор-архитектуры)
2. [Аутентификация](#2-аутентификация)
3. [Эндпоинты API](#3-эндпоинты-api)
4. [Модели данных](#4-модели-данных)
5. [Бизнес-логика](#5-бизнес-логика)
6. [Онбординг нового агента](#6-онбординг-нового-агента)
7. [Интеграция с Telegram](#7-интеграция-с-telegram)
8. [Для разработчика](#8-для-разработчика)

---

## 1. Обзор архитектуры

### Что делает сервис

Telegram AI Assistant API — это сервис, который позволяет:

1. **Отправлять сообщения и файлы в Telegram** от лица нескольких корпоративных аккаунтов (агентов/отправителей) с соблюдением rate limit, чтобы не получить бан.
2. **Автоматически отвечать на входящие сообщения через AI** (GPT-4o) с поддержкой контекста переписки, голосовых сообщений и вызова внешних функций (webhook functions).
3. **Управлять диалогами**: просматривать историю переписки, включать/выключать AI для конкретного контакта, отправлять сообщения вручную.

### Технологический стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.11 |
| Веб-фреймворк | FastAPI 0.109.0 + Uvicorn |
| База данных | PostgreSQL 16 (async через asyncpg) |
| ORM | SQLAlchemy 2.0.25 (async) |
| Telegram-клиент | Telethon 1.42.0 (MTProto) |
| AI | OpenAI GPT-4o (chat) + Whisper (аудио) |
| Шифрование | cryptography (Fernet) |
| Контейнеризация | Docker + Docker Compose |

### Компоненты системы

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Compose                           │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   telegram   │    │  telegram-   │    │  telegram-   │  │
│  │     -api     │    │   listener   │    │   api-db     │  │
│  │  (FastAPI)   │    │  (Telethon)  │    │ (PostgreSQL) │  │
│  │  port: 8000  │    │              │    │  port: 5432  │  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
│         │                  │                   │           │
│         └──────────────────┴───────────────────┘           │
└─────────────────────────────────────────────────────────────┘
```

**telegram-api** — основной сервис (FastAPI):
- Обрабатывает HTTP-запросы от внешних клиентов
- Ставит сообщения в очередь и отправляет их через Telegram
- Управляет сущностями (senders, conversations, contexts)
- Запускает внутренний `QueueWorker` (asyncio task)

**telegram-listener** — отдельный контейнер:
- Постоянно слушает входящие сообщения через Telethon (MTProto)
- При получении сообщения сохраняет его в БД
- Если AI включён для диалога — запускает debounce-таймер и передаёт сообщение AI-движку
- Отправляет ответ AI обратно в Telegram

**telegram-api-db** — PostgreSQL 16, хранит все данные.

### Поток данных

#### Исходящие сообщения (от клиента к контакту)
```
Клиент (HTTP POST /api/v1/send)
    → FastAPI роутер
    → Проверка sender (активен?)
    → enqueue_message() — запись в таблицу message_queue
    → Ответ клиенту: {queue_id, queue_position, estimated_send_at}

QueueWorker (каждые 3 сек):
    → Проверка рабочего времени (9:00–20:00 МСК)
    → Проверка rate limits (5/мин, 30/час, интервал 12с)
    → Telegram API (Telethon) → отправка
    → Обновление статуса в message_queue → 'sent'
    → Запись в messages_log
    → Создание/обновление Conversation
    → Вызов callback_url (если указан)
```

#### Входящие сообщения (от контакта к AI)
```
Telegram MTProto event (NewMessage incoming)
    → TelegramListener.handle_incoming_message()
    → Определение типа: текст / голосовое (→ Whisper) / документ (→ webhook)
    → get_or_create_conversation()
    → save_message() → таблица messages
    → Проверка: ai_enabled == true AND status == 'active'?

    Если да:
    → add_to_buffer() + schedule_ai_response() (debounce 3 мин)
    → После таймера: process_buffered_messages()
    → ai_engine.generate_response() → GPT-4o
    → Если AI вызвал функцию → execute_webhook() → внешний URL
    → client.send_message() → ответ в Telegram
    → save_message(sent_by='ai')
```

### Ограничения

- **Rate limiting:** отправка только в рабочее время (9:00–20:00 МСК). Максимум 5 сообщений в минуту, 30 в час, пауза между отправками 12 секунд на каждого отправителя.
- **Debounce AI:** AI отвечает не сразу, а ждёт 3 минуты после последнего сообщения (или 5 минут с первого). Это позволяет собрать несколько сообщений подряд в один запрос.
- **Listener не перезапускается динамически:** при добавлении нового sender контейнер `telegram-listener` перезапускается через `docker restart telegram-listener`. До перезапуска новый агент сообщения не слушает.
- **Onboarding сессии в памяти:** временные сессии при онбординге хранятся в dict в памяти процесса. При перезапуске API все незавершённые онбординги теряются.
- **Whisper:** язык транскрипции голосовых зафиксирован как `ru`. Если контакт говорит на другом языке — качество будет хуже.

---

## 2. Аутентификация

### Механизм

Все эндпоинты (кроме `GET /api/v1/health`) требуют API-ключ.

**Заголовок:** `X-API-Key: <ключ>`

### Где хранится и как проверяется

Ключ задаётся в переменной окружения `API_KEY` (файл `.env`).

Проверка реализована в `app/routers/auth.py`:

```python
# app/routers/auth.py
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    settings = get_settings()
    if not api_key:
        raise HTTPException(status_code=401, detail="API key is missing.")
    if api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key
```

Каждый роутер подключает `verify_api_key` как зависимость FastAPI (`Depends(verify_api_key)`).

### Ошибки аутентификации

| HTTP | Причина |
|------|---------|
| 401 | Заголовок `X-API-Key` отсутствует |
| 403 | Ключ не совпадает со значением в `API_KEY` |

### Пример запроса

```bash
curl -H "X-API-Key: tg-followup-ags-2025-secret-key" \
     https://telegram-api.agsventurelab.com/api/v1/health
```

---

## 3. Эндпоинты API

### Senders (Агенты)

#### `GET /api/v1/senders`
Список всех агентов.

**Response:**
```json
{
  "senders": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "slug": "andrew",
      "name": "Andrew",
      "phone": "+79001234567",
      "is_active": true,
      "ai_context_id": "660e8400-e29b-41d4-a716-446655440001",
      "ai_context_name": "AGS Foods Supplier Bot",
      "last_used_at": "2025-01-20T10:30:00Z",
      "created_at": "2025-01-01T00:00:00Z"
    }
  ]
}
```

---

#### `POST /api/v1/senders`
Создать нового агента. После создания автоматически перезапускает контейнер `telegram-listener`.

**Request body:**
```json
{
  "slug": "andrew",
  "name": "Andrew",
  "phone": "+79001234567",
  "session_string": "1BVtsOK8Bu...base64...",
  "ai_context_id": "660e8400-e29b-41d4-a716-446655440001"
}
```

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `slug` | string (2–50) | да | Уникальный идентификатор (используется в запросах) |
| `name` | string (max 100) | да | Отображаемое имя |
| `phone` | string (max 20) | да | Номер телефона аккаунта |
| `session_string` | string | да | Telethon session string (будет зашифрован) |
| `ai_context_id` | UUID | нет | ID AI-контекста для этого агента |

**Response:** объект `SenderResponse` (как в GET)

**Ошибки:**
- `400` — slug уже существует

---

#### `GET /api/v1/senders/{slug}`
Получить агента по slug.

**Ошибки:**
- `404` — агент не найден

---

#### `PATCH /api/v1/senders/{slug}`
Обновить агента. После обновления перезапускает `telegram-listener`.

**Request body** (все поля опциональны):
```json
{
  "name": "Andrew Updated",
  "phone": "+79009999999",
  "session_string": "новая сессия",
  "is_active": false,
  "ai_context_id": "новый-uuid"
}
```

---

#### `DELETE /api/v1/senders/{slug}`
Полное удаление агента (hard delete). Удаляются также все диалоги, сообщения, кэш контактов и логи этого агента.

**Response:** `204 No Content`

**Ошибки:**
- `404` — агент не найден

---

### Conversations (Диалоги)

#### `GET /api/v1/conversations`
Список диалогов с последним сообщением.

**Query параметры:**
| Параметр | Тип | Описание |
|----------|-----|----------|
| `status` | string | Фильтр: `active`, `manual`, `paused` |
| `ai_enabled` | bool | Фильтр по статусу AI |
| `limit` | int (max 100) | По умолчанию 50 |
| `offset` | int | По умолчанию 0 |

**Response:**
```json
{
  "conversations": [
    {
      "id": "770e8400-e29b-41d4-a716-446655440002",
      "sender_slug": "andrew",
      "contact_phone": "+79005556677",
      "contact_name": "Иван Иванов",
      "contact_telegram_id": 123456789,
      "ai_enabled": true,
      "ai_context_id": "660e8400-e29b-41d4-a716-446655440001",
      "status": "active",
      "paused_at": null,
      "paused_reason": null,
      "created_at": "2025-01-15T09:00:00Z",
      "updated_at": "2025-01-20T14:30:00Z",
      "last_message": "Здравствуйте, нас интересует...",
      "last_message_at": "2025-01-20T14:30:00Z",
      "unread_count": 3
    }
  ],
  "total": 42
}
```

**Примечание:** `last_message` обрезается до 50 символов + `...`

---

#### `GET /api/v1/conversations/{conversation_id}`
Получить детали диалога.

**Ошибки:**
- `404` — диалог не найден

---

#### `PATCH /api/v1/conversations/{conversation_id}`
Обновить настройки диалога.

**Request body** (все поля опциональны):
```json
{
  "ai_enabled": true,
  "ai_context_id": "новый-uuid",
  "status": "active"
}
```

**Поведение при `ai_enabled: true`:** автоматически устанавливает `status = 'active'`, сбрасывает `paused_at` и `paused_reason`.

---

#### `GET /api/v1/conversations/{conversation_id}/messages`
История сообщений диалога (от новых к старым).

**Query параметры:** `limit` (max 200, по умолчанию 50), `offset`

**Response:**
```json
{
  "messages": [
    {
      "id": "880e8400-e29b-41d4-a716-446655440003",
      "direction": "inbound",
      "message_text": "Добрый день! Интересует цена на пшеницу",
      "sent_by": "contact",
      "telegram_message_id": 456789,
      "created_at": "2025-01-20T14:28:00Z"
    },
    {
      "id": "880e8400-e29b-41d4-a716-446655440004",
      "direction": "outbound",
      "message_text": "Добрый день! Уточню у коллег и вернусь к вам.",
      "sent_by": "ai",
      "telegram_message_id": 456790,
      "created_at": "2025-01-20T14:31:05Z"
    }
  ],
  "total": 15
}
```

---

#### `POST /api/v1/conversations/{conversation_id}/send`
Отправить сообщение от имени менеджера (не AI). AI при этом не отключается.

**Request body:**
```json
{
  "message": "Добрый день, уточнил — цена на пшеницу 18 руб/кг"
}
```

**Response:**
```json
{
  "success": true,
  "message_id": "990e8400-e29b-41d4-a716-446655440005",
  "telegram_message_id": 456791,
  "error": null
}
```

**Ошибки:**
- `404` — диалог не найден или sender неактивен
- `400` — у контакта нет Telegram ID

---

#### `POST /api/v1/conversations/{conversation_id}/enable-ai`
Быстрое включение AI для диалога. Устанавливает `ai_enabled = true`, `status = 'active'`, сбрасывает `paused_at`.

**Response:**
```json
{"success": true, "message": "AI enabled"}
```

---

#### `POST /api/v1/conversations/{conversation_id}/disable-ai`
Быстрое выключение AI. Устанавливает `ai_enabled = false`, `status = 'manual'`, `paused_reason = 'Manually disabled'`.

**Response:**
```json
{"success": true, "message": "AI disabled"}
```

---

#### `DELETE /api/v1/conversations/{conversation_id}`
Удалить диалог и все его сообщения (hard delete, необратимо).

**Response:** `204 No Content`

---

### Messages (через раздел Conversations)

Сообщения не имеют отдельных роутов для создания через API (кроме отправки внутри диалога). Входящие сообщения создаются автоматически listener-сервисом.

---

### AI Contexts (Контексты AI)

#### `GET /api/v1/contexts`
Список всех активных AI-контекстов.

**Response:**
```json
{
  "contexts": [
    {
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "name": "AGS Foods Supplier Bot",
      "system_prompt": "Ты — вежливый ассистент компании AGS Foods...",
      "tone_of_voice": "Профессиональный, но дружелюбный",
      "rules": "Не называть конкретные цены без согласования",
      "company_info": "AGS Foods — поставщик продуктов питания",
      "product_info": "Пшеница, кукуруза, подсолнечник",
      "max_message_length": 500,
      "response_delay_seconds": 5,
      "is_active": true,
      "webhook_functions": [...],
      "document_webhook_url": "https://example.com/webhook/docs",
      "created_at": "2025-01-01T00:00:00Z",
      "updated_at": "2025-01-20T10:00:00Z"
    }
  ],
  "total": 3
}
```

---

#### `POST /api/v1/contexts`
Создать новый AI-контекст.

**Request body:**
```json
{
  "name": "AGS Foods Supplier Bot",
  "system_prompt": "Ты — вежливый ассистент компании AGS Foods...",
  "tone_of_voice": "Профессиональный, но дружелюбный",
  "rules": "Не называй конкретные цены без согласования",
  "company_info": "AGS Foods — поставщик продуктов питания",
  "product_info": "Пшеница, кукуруза, подсолнечник",
  "max_message_length": 500,
  "response_delay_seconds": 5,
  "webhook_functions": [
    {
      "name": "save_price_offer",
      "description": "Сохранить предложение о цене от поставщика",
      "webhook_url": "https://n8n.example.com/webhook/price",
      "parameters": [
        {
          "name": "product",
          "type": "string",
          "description": "Название продукта",
          "required": true
        },
        {
          "name": "price",
          "type": "number",
          "description": "Цена за единицу",
          "required": true
        }
      ]
    }
  ],
  "document_webhook_url": "https://n8n.example.com/webhook/documents"
}
```

| Поле | Тип | По умолчанию | Описание |
|------|-----|--------------|----------|
| `name` | string | — | Название контекста |
| `system_prompt` | string | null | Основной системный промпт |
| `tone_of_voice` | string | null | Описание тона общения |
| `rules` | string | null | Дополнительные правила |
| `company_info` | string | null | Информация о компании |
| `product_info` | string | null | Информация о продуктах |
| `max_message_length` | int | 500 | Макс. длина ответа AI в токенах |
| `response_delay_seconds` | int | 5 | Пока не используется (TODO) |
| `webhook_functions` | array | [] | Функции для function calling |
| `document_webhook_url` | string | null | URL для отправки входящих документов |

**Response:** объект `ContextResponse`, статус `201`

---

#### `GET /api/v1/contexts/{context_id}`
Получить контекст по ID.

**Ошибки:**
- `404` — контекст не найден

---

#### `PATCH /api/v1/contexts/{context_id}`
Обновить контекст. Все поля опциональны (те же, что в POST).

**Ошибки:**
- `400` — нет полей для обновления
- `404` — контекст не найден

---

#### `DELETE /api/v1/contexts/{context_id}`
Удалить контекст (hard delete). Перед удалением отвязывает контекст от всех senders и conversations (устанавливает `ai_context_id = NULL`).

**Response:** `204 No Content`

---

### Onboarding (Подключение Telegram-аккаунта)

Процесс получения `session_string` для нового агента через SMS-верификацию Telegram.

#### `POST /api/v1/onboarding/start`
Начать онбординг: отправить код подтверждения на телефон.

**Request body:**
```json
{
  "phone": "+79001234567"
}
```

**Response:**
```json
{
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "phone_code_hash": "abc123...",
  "status": "code_sent"
}
```

Сохраните `session_id` — он нужен на следующих шагах.

**Ошибки:**
- `400` — неверный формат номера (`PHONE_NUMBER_INVALID`) или номер заблокирован (`PHONE_NUMBER_BANNED`)
- `429` — слишком много попыток, включает `retry_after` (секунды)

---

#### `POST /api/v1/onboarding/verify-code`
Проверить 5-значный код из Telegram.

**Request body:**
```json
{
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "code": "12345"
}
```

**Response (успех, нет 2FA):**
```json
{
  "status": "success",
  "session_string": "1BVtsOK8Bu...длинная строка..."
}
```

**Response (требуется 2FA):**
```json
{
  "status": "2fa_required",
  "session_string": null
}
```

**Ошибки:**
- `400` — неверный код (`PHONE_CODE_INVALID`) или код истёк (`PHONE_CODE_EXPIRED`)
- `404` — `session_id` не найден или истёк

---

#### `POST /api/v1/onboarding/verify-2fa`
Проверить пароль двухфакторной аутентификации.

**Request body:**
```json
{
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "password": "мой2фапароль"
}
```

**Response:**
```json
{
  "status": "success",
  "session_string": "1BVtsOK8Bu...длинная строка..."
}
```

**Ошибки:**
- `400` — неверный пароль (`PASSWORD_INVALID`) или 2FA не требовалась (`2FA_NOT_REQUIRED`)
- `404` — `session_id` не найден

---

#### `DELETE /api/v1/onboarding/cancel/{session_id}`
Отменить незавершённый онбординг и очистить временную сессию.

**Response:**
```json
{"status": "cancelled"}
```

---

### Send (Отправка)

#### `POST /api/v1/send`
Поставить сообщение в очередь на отправку.

**Request body:**
```json
{
  "sender": "andrew",
  "recipient_phone": "+79005556677",
  "recipient_name": "Иван Иванов",
  "message": "Добрый день! Хотим уточнить условия сотрудничества.",
  "as_draft": false,
  "metadata": {"deal_id": "DEAL-123"},
  "callback_url": "https://my-crm.example.com/webhook/telegram-result"
}
```

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `sender` | string | да | Slug агента |
| `recipient_phone` | string | да | Номер получателя с кодом страны |
| `recipient_name` | string | нет | Имя получателя |
| `message` | string (max 4096) | да | Текст сообщения |
| `as_draft` | bool | нет (false) | Сохранить как черновик вместо отправки |
| `metadata` | object | нет | Произвольные данные, вернутся в callback |
| `callback_url` | string | нет | URL для уведомления о результате |

**Response:**
```json
{
  "success": true,
  "queued": true,
  "queue_id": "aa1b2c3d-e5f6-7890-abcd-ef1234567890",
  "queue_position": 3,
  "sender_slug": "andrew",
  "estimated_send_at": "2025-01-20T10:00:36Z",
  "timestamp": "2025-01-20T10:00:00Z",
  "error": null
}
```

**Ошибки (в теле ответа, `success: false`):**
- `SENDER_NOT_FOUND` — агент с таким slug не найден
- `SENDER_INACTIVE` — агент деактивирован
- `ENQUEUE_FAILED` — ошибка при добавлении в очередь

---

#### `POST /api/v1/send-file`
Поставить отправку файла в очередь.

**Request body:**
```json
{
  "sender": "andrew",
  "recipient_phone": "+79005556677",
  "recipient_name": "Иван Иванов",
  "file_url": "https://storage.example.com/files/contract.pdf",
  "file_name": "Договор_AGS_2025.pdf",
  "caption": "Добрый день! Прикладываю договор для ознакомления.",
  "metadata": {"deal_id": "DEAL-123"},
  "callback_url": "https://my-crm.example.com/webhook/telegram-result"
}
```

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `file_url` | string | да | Публичный URL файла для скачивания |
| `file_name` | string | нет | Имя файла (если не указано — из URL) |
| `caption` | string (max 1024) | нет | Подпись к файлу |

**Response:** аналогично `/api/v1/send`

---

### Queue (Очередь)

#### `GET /api/v1/queue/{queue_id}`
Проверить статус элемента очереди.

**Response:**
```json
{
  "id": "aa1b2c3d-e5f6-7890-abcd-ef1234567890",
  "sender_slug": "andrew",
  "item_type": "message",
  "status": "sent",
  "recipient_phone": "+79005556677",
  "recipient_name": "Иван Иванов",
  "message_text": "Добрый день!",
  "file_url": null,
  "queue_position": null,
  "scheduled_at": "2025-01-20T10:00:00Z",
  "created_at": "2025-01-20T10:00:00Z",
  "finished_at": "2025-01-20T10:00:36Z",
  "result_message_id": "456791",
  "result_recipient_telegram_id": 123456789,
  "result_recipient_name": "Иван Иванов",
  "result_recipient_username": "ivan_ivanov",
  "error_message": null
}
```

Статусы: `pending`, `processing`, `sent`, `failed`, `cancelled`

---

#### `GET /api/v1/queue/stats/{sender_slug}`
Статистика очереди по отправителю.

**Response:**
```json
{
  "sender_slug": "andrew",
  "pending": 5,
  "processing": 1,
  "sent_last_hour": 12,
  "sent_last_minute": 2,
  "next_send_at": "2025-01-20T10:00:48Z"
}
```

---

#### `DELETE /api/v1/queue/{queue_id}`
Отменить элемент очереди. Можно отменить только `pending`. Нельзя отменить `processing` или `sent`.

**Response:**
```json
{"success": true, "queue_id": "aa1b2c3d...", "status": "cancelled"}
```

**Ошибки:**
- `404` — элемент не найден
- `409` — нельзя отменить в текущем статусе

---

### Health

#### `GET /api/v1/health`
Проверка состояния сервиса. Не требует аутентификации.

**Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "senders": {
    "total": 3,
    "active": 3,
    "sessions_valid": 3
  },
  "version": "1.0.0",
  "uptime_seconds": 86400
}
```

---

## 4. Модели данных

### Sender (Агент)

Таблица: `senders`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID PK | Уникальный ID |
| `slug` | VARCHAR(50) UNIQUE | Короткий идентификатор (например: `andrew`) |
| `name` | VARCHAR(100) | Отображаемое имя |
| `phone` | VARCHAR(20) | Номер телефона Telegram-аккаунта |
| `session_string` | TEXT | Зашифрованный Telethon session string |
| `is_active` | BOOLEAN | Активен ли агент |
| `ai_context_id` | UUID FK → ai_contexts | Привязанный AI-контекст (nullable) |
| `created_at` | TIMESTAMPTZ | Дата создания |
| `last_used_at` | TIMESTAMPTZ | Дата последнего использования |

`session_string` хранится в зашифрованном виде (Fernet). Расшифровывается только при подключении к Telegram.

---

### Conversation (Диалог)

Таблица: `conversations`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID PK | Уникальный ID |
| `sender_id` | UUID FK → senders | Агент, ведущий диалог |
| `contact_phone` | VARCHAR(20) | Телефон контакта |
| `contact_name` | VARCHAR(100) | Имя контакта |
| `contact_telegram_id` | BIGINT | Telegram ID контакта |
| `ai_enabled` | BOOLEAN | Включён ли AI для этого диалога |
| `ai_context_id` | UUID FK → ai_contexts | AI-контекст диалога (может отличаться от контекста sender'а) |
| `status` | VARCHAR(20) | Статус: `active`, `manual`, `paused` |
| `paused_at` | TIMESTAMPTZ | Когда был поставлен на паузу |
| `paused_reason` | TEXT | Причина паузы |
| `created_at` | TIMESTAMPTZ | Дата создания |
| `updated_at` | TIMESTAMPTZ | Дата последнего обновления |

Диалог создаётся автоматически при первом входящем или исходящем сообщении.

---

### Message (Сообщение)

Таблица: `messages`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID PK | Уникальный ID |
| `conversation_id` | UUID FK → conversations | Диалог |
| `direction` | VARCHAR | `inbound` (входящее) / `outbound` (исходящее) |
| `message_text` | TEXT | Текст сообщения |
| `sent_by` | VARCHAR | Кто отправил: `contact`, `ai`, `human` |
| `telegram_message_id` | INTEGER | ID сообщения в Telegram |
| `created_at` | TIMESTAMPTZ | Дата создания |

Уникальный constraint: `(conversation_id, telegram_message_id)` — защита от дублей.

---

### AIContext (Контекст AI)

Таблица: `ai_contexts`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID PK | Уникальный ID |
| `name` | VARCHAR(100) | Название контекста |
| `system_prompt` | TEXT | Основные инструкции для AI |
| `tone_of_voice` | TEXT | Описание желаемого тона |
| `rules` | TEXT | Дополнительные правила поведения |
| `company_info` | TEXT | Информация о компании для контекста |
| `product_info` | TEXT | Информация о продуктах/услугах |
| `faq` | JSONB | FAQ (в коде не используется) |
| `max_message_length` | BIGINT | Максимальная длина ответа (передаётся в `max_tokens`) |
| `response_delay_seconds` | BIGINT | Задержка перед ответом (TODO, не реализована) |
| `auto_pause_triggers` | JSONB | Триггеры авто-паузы (TODO, не реализованы) |
| `is_active` | BOOLEAN | Активен ли контекст |
| `webhook_functions` | JSONB | Список функций для Function Calling |
| `document_webhook_url` | TEXT | URL для отправки входящих файлов/фото |
| `created_at` | TIMESTAMPTZ | Дата создания |
| `updated_at` | TIMESTAMPTZ | Дата обновления |

#### Структура `webhook_functions`

```json
[
  {
    "name": "save_price_offer",
    "description": "Сохранить предложение о цене от поставщика",
    "webhook_url": "https://n8n.example.com/webhook/price",
    "parameters": [
      {
        "name": "product",
        "type": "string",
        "description": "Название продукта",
        "required": true
      },
      {
        "name": "price",
        "type": "number",
        "description": "Цена за единицу",
        "required": true
      },
      {
        "name": "volume",
        "type": "number",
        "description": "Объём поставки в тоннах",
        "required": false
      }
    ]
  }
]
```

---

### MessageQueue (Очередь)

Таблица: `message_queue`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID PK | Уникальный ID |
| `sender_id` | UUID FK → senders | Агент-отправитель |
| `item_type` | ENUM | `message` / `file` |
| `status` | ENUM | `pending` / `processing` / `sent` / `failed` / `cancelled` |
| `recipient_phone` | VARCHAR(20) | Телефон получателя |
| `recipient_name` | VARCHAR(100) | Имя получателя |
| `message_text` | TEXT | Текст (для type=message) |
| `as_draft` | BOOLEAN | Отправить как черновик |
| `file_url` | TEXT | URL файла (для type=file) |
| `file_name` | VARCHAR(255) | Имя файла |
| `caption` | TEXT | Подпись к файлу |
| `extra_data` | JSONB | Метаданные из запроса |
| `callback_url` | TEXT | URL для уведомления о результате |
| `priority` | INTEGER | Приоритет (выше = раньше) |
| `scheduled_at` | TIMESTAMPTZ | Время запланированной отправки |
| `attempts` | INTEGER | Количество попыток |
| `result_message_id` | VARCHAR(50) | ID сообщения в Telegram (после отправки) |
| `result_recipient_telegram_id` | BIGINT | Telegram ID получателя |
| `result_recipient_name` | VARCHAR(100) | Имя получателя из Telegram |
| `result_recipient_username` | VARCHAR(50) | Username получателя |
| `error_message` | TEXT | Сообщение об ошибке |

---

### MessageLog (Лог сообщений)

Таблица: `messages_log`

Упрощённый лог для аудита. Создаётся при успешной отправке и при окончательном сбое.

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID PK | Уникальный ID |
| `sender_id` | UUID FK → senders | Агент |
| `recipient_phone` | VARCHAR(20) | Телефон |
| `recipient_name` | VARCHAR(100) | Имя |
| `recipient_telegram_id` | BIGINT | Telegram ID |
| `message_text` | TEXT | Текст |
| `message_type` | ENUM | `sent` / `draft` / `failed` |
| `error_message` | TEXT | Ошибка (при failed) |
| `extra_data` | JSONB | Метаданные |
| `created_at` | TIMESTAMPTZ | Дата |

---

### ContactCache (Кэш контактов)

Таблица: `contacts_cache`

Кэш разрешения номеров телефонов в Telegram ID, чтобы не делать повторные запросы к Telegram API.

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUID PK | Уникальный ID |
| `sender_id` | UUID FK → senders | Для какого агента |
| `phone` | VARCHAR(20) | Номер телефона |
| `telegram_id` | BIGINT | Telegram ID |
| `access_hash` | BIGINT | Telegram access hash |
| `first_name` | VARCHAR(100) | Имя |
| `last_name` | VARCHAR(100) | Фамилия |
| `username` | VARCHAR(50) | Username |
| `is_registered` | BOOLEAN | Зарегистрирован ли в Telegram |
| `updated_at` | TIMESTAMPTZ | Дата обновления кэша |

---

## 5. Бизнес-логика

### Статусы диалогов

| Статус | Описание |
|--------|----------|
| `active` | AI включён и отвечает на входящие сообщения |
| `manual` | AI отключён вручную (через disable-ai), менеджер ведёт переписку |
| `paused` | Диалог на паузе (зарезервировано, в текущем коде не устанавливается автоматически) |

Переходы:
- `active → manual`: вызов `POST /conversations/{id}/disable-ai`
- `manual → active`: вызов `POST /conversations/{id}/enable-ai`
- При обновлении через `PATCH /conversations/{id}` с `ai_enabled: true` → автоматически становится `active`

### Логика AI-ответа

AI отвечает на входящее сообщение только если:
1. `conversation.ai_enabled == true`
2. `conversation.status == 'active'`
3. У sender есть `ai_context_id`

**Алгоритм debounce:**

```
Пришло сообщение от контакта
    → Сохранить в БД
    → Добавить текст в буфер для conversation_id
    → Сбросить debounce таймер (3 минуты)

Если пришло ещё одно сообщение — таймер сбрасывается заново.
Если прошло 3 минуты с последнего сообщения ИЛИ 5 минут с первого:
    → Объединить все накопленные тексты через '\n'
    → Передать в ai_engine.generate_response()
```

**Построение запроса к GPT-4o:**
1. Загружается AI-контекст из БД (`ai_contexts`)
2. Формируется системный промпт: `system_prompt` + `tone_of_voice` + `rules` + `company_info` + имя контакта + лимит длины
3. Загружается история последних 20 сообщений диалога
4. Добавляется текущее сообщение пользователя
5. Если есть `webhook_functions` — они передаются как `tools` в OpenAI

Если AI не удалось сгенерировать ответ (ошибка API, rate limit) — сообщение не отправляется, ошибка логируется.

### enable-ai / disable-ai

**enable-ai** (`POST /conversations/{id}/enable-ai`):
```sql
UPDATE conversations
SET ai_enabled = true, status = 'active', paused_at = NULL, paused_reason = NULL
```

**disable-ai** (`POST /conversations/{id}/disable-ai`):
```sql
UPDATE conversations
SET ai_enabled = false, status = 'manual', paused_at = NOW(), paused_reason = 'Manually disabled'
```

Отправка сообщения менеджером через `/conversations/{id}/send` **не отключает AI** — это поведение было намеренно изменено. Управление AI только через переключатель.

### Webhook Functions (Function Calling)

Webhook Functions позволяют AI автоматически фиксировать данные из переписки и передавать их во внешние системы (CRM, n8n, Zapier и т.д.).

**Как работает:**

1. В AIContext создаётся функция с параметрами и `webhook_url`
2. При генерации ответа функции передаются в OpenAI как `tools`
3. Если AI решает вызвать функцию — он возвращает `tool_calls` вместо текста
4. Сервис парсит аргументы и вызывает `webhook_url` с payload:

```json
{
  "arguments": {
    "product": "Пшеница",
    "price": 18.5,
    "volume": 100
  },
  "callId": "770e8400-e29b-41d4-a716-446655440002",
  "agentId": "save_price_offer",
  "context": {
    "conversation_id": "770e8400-...",
    "contact_phone": "+79005556677",
    "contact_name": "Иван Иванов",
    "contact_telegram_id": 123456789,
    "sender_id": "550e8400-...",
    "sender_slug": "andrew",
    "sender_name": "Andrew",
    "ai_context_id": "660e8400-..."
  },
  "timestamp": "2025-01-20T14:31:05.123456+00:00"
}
```

5. После получения ответа от webhook — делается второй запрос к GPT-4o для генерации текстового ответа пользователю
6. AI отвечает, опираясь на результат вызова функции

**Таймаут webhook:** 10 секунд. При ошибке AI продолжает работу и генерирует ответ без данных из функции.

**Важно:** имя функции в payload находится в поле `agentId`, а не `function_name`.

### Документы (document_webhook_url)

Если в AIContext задан `document_webhook_url`:
- При получении фото, видео или документа файл скачивается во временную директорию
- Отправляется POST на `document_webhook_url` с payload:

```json
{
  "file_name": "contract.pdf",
  "file_type": "application/pdf",
  "file_base64": "JVBERi0x...",
  "conversation_id": "770e8400-...",
  "contact_name": "Иван Иванов",
  "contact_telegram_id": 123456789,
  "timestamp": "2025-01-20T14:31:05+00:00"
}
```

- Файл удаляется через 60 секунд после отправки
- Это fire-and-forget (не блокирует обработку)

---

## 6. Онбординг нового агента

Пошаговый процесс добавления нового Telegram-аккаунта в систему.

### Шаг 1: Запрос кода

```bash
curl -X POST https://telegram-api.agsventurelab.com/api/v1/onboarding/start \
  -H "X-API-Key: <ключ>" \
  -H "Content-Type: application/json" \
  -d '{"phone": "+79001234567"}'
```

Ответ:
```json
{
  "session_id": "a1b2c3d4-...",
  "phone_code_hash": "abc123...",
  "status": "code_sent"
}
```

Сохраняем `session_id`.

### Шаг 2а: Ввод кода (без 2FA)

```bash
curl -X POST https://telegram-api.agsventurelab.com/api/v1/onboarding/verify-code \
  -H "X-API-Key: <ключ>" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "a1b2c3d4-...", "code": "12345"}'
```

Ответ при успехе:
```json
{
  "status": "success",
  "session_string": "1BVtsOK8Bu..."
}
```

### Шаг 2б: Ввод кода (требуется 2FA)

Если verify-code вернул `"status": "2fa_required"`:

```bash
curl -X POST https://telegram-api.agsventurelab.com/api/v1/onboarding/verify-2fa \
  -H "X-API-Key: <ключ>" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "a1b2c3d4-...", "password": "мойпароль"}'
```

Ответ:
```json
{
  "status": "success",
  "session_string": "1BVtsOK8Bu..."
}
```

### Шаг 3: Создание sender

Используем полученный `session_string`:

```bash
curl -X POST https://telegram-api.agsventurelab.com/api/v1/senders \
  -H "X-API-Key: <ключ>" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "andrew",
    "name": "Andrew",
    "phone": "+79001234567",
    "session_string": "1BVtsOK8Bu...",
    "ai_context_id": "660e8400-..."
  }'
```

### Что происходит с session_string

- **Онбординг** возвращает **нешифрованный** session_string (raw Telethon string)
- При создании sender через `POST /api/v1/senders` — `encrypt_session()` шифрует строку алгоритмом Fernet
- В таблице `senders.session_string` хранится **зашифрованная** строка
- При использовании (отправка, слушание) — `decrypt_session()` расшифровывает перед передачей в Telethon

**Алгоритм шифрования:**
```python
key = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()  # 32 байта
key_b64 = base64.urlsafe_b64encode(key)
fernet = Fernet(key_b64)
encrypted = fernet.encrypt(session_string.encode())
```

### После создания sender

API автоматически вызывает `docker restart telegram-listener`. Это необходимо, так как listener загружает список активных sender'ов при старте. До перезапуска новый агент не будет получать входящие сообщения.

---

## 7. Интеграция с Telegram

### Как сервис читает входящие сообщения

Контейнер `telegram-listener` при запуске:
1. Загружает всех активных sender'ов из БД
2. Для каждого создаёт `TelegramClient(StringSession(session_string))`
3. Регистрирует обработчики событий:
   - `events.NewMessage(incoming=True)` → `handle_incoming_message()`
   - `events.NewMessage(outgoing=True)` → `handle_outgoing_message()`
4. Запускает `client.run_until_disconnected()`
5. Каждые 15 секунд вызывает `client.catch_up()` для подхватывания пропущенных обновлений

При отключении клиент автоматически переподключается с паузой между попытками (нарастающей, максимум 60 секунд).

Используется `ResilientTelegramClient` — подкласс `TelegramClient`, который обрабатывает `TypeNotFoundError` в `GetDifference` (возникает когда Telegram присылает новые типы конструкторов, неизвестные текущей версии Telethon).

### Как отправляет ответы

Ответы AI отправляются через тот же `TelegramClient`, что используется для прослушивания:

```python
sent_message = await client.send_message(recipient_telegram_id, reply_text)
```

### Хранение сессий Telegram

Сессии хранятся как строки в `StringSession` формате Telethon. В БД хранятся зашифрованными через Fernet. Каждый раз при подключении:
1. Из БД читается зашифрованная строка
2. Расшифровывается в памяти
3. Передаётся в `TelegramClient(StringSession(decrypted))`

### Обработка входящих медиа

| Тип | Обработка |
|-----|-----------|
| Текст | Сохраняется как есть |
| Голосовое | Скачивается как .ogg → Whisper API → текст с префиксом `[🎤 Голосовое]: ` |
| Фото/видео/документ | Скачивается во временный файл → POST на `document_webhook_url` (base64) → файл удаляется через 60с |
| Групповые чаты/каналы | Игнорируются |
| Сообщения от себя | Игнорируются |

---

## 8. Для разработчика

### Как запустить локально

```bash
# 1. Клонировать репозиторий и перейти в директорию
cd telegram-api

# 2. Создать .env файл
cp .env.example .env  # или создать вручную (см. переменные ниже)

# 3. Запустить через Docker Compose
docker compose up --build

# API будет доступен на http://localhost:8000
# Swagger UI: http://localhost:8000/docs
```

Для локальной разработки без Docker:
```bash
pip install -r requirements.txt

# Запустить только БД через Docker
docker compose up db -d

# Запустить API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Запустить listener отдельно
python -m app.services.listener
```

### Переменные окружения

Все переменные хранятся в `.env` в корне проекта:

| Переменная | Обязательна | Описание |
|------------|-------------|----------|
| `DATABASE_URL` | да | PostgreSQL строка подключения. Формат: `postgresql+asyncpg://user:pass@host:5432/db` |
| `TELEGRAM_API_ID` | да | API ID приложения Telegram (получить на [my.telegram.org](https://my.telegram.org)) |
| `TELEGRAM_API_HASH` | да | API Hash приложения Telegram |
| `API_KEY` | да | Ключ для аутентификации API запросов (заголовок `X-API-Key`) |
| `ENCRYPTION_KEY` | да | Ключ шифрования session strings (произвольная строка, хешируется SHA-256) |
| `OPENAI_API_KEY` | да | API ключ OpenAI для GPT-4o и Whisper |
| `LOG_LEVEL` | нет | Уровень логирования (по умолчанию `INFO`) |
| `MAX_POOL_SIZE` | нет | Размер пула БД (по умолчанию `10`) |

### Структура проекта

```
app/
├── main.py              # Точка входа FastAPI, lifespan, регистрация роутеров
├── config.py            # Настройки через pydantic-settings
├── database.py          # Инициализация движка и сессии SQLAlchemy
│
├── models/
│   └── __init__.py      # Все SQLAlchemy ORM модели
│
├── schemas/
│   └── __init__.py      # Pydantic схемы запросов/ответов
│
├── routers/
│   ├── auth.py          # Зависимость verify_api_key
│   ├── send.py          # POST /send, POST /send-file
│   ├── senders.py       # CRUD /senders
│   ├── conversations.py # CRUD /conversations + enable/disable AI
│   ├── contexts.py      # CRUD /contexts
│   ├── onboarding.py    # /onboarding/start, verify-code, verify-2fa
│   ├── queue.py         # GET/DELETE /queue
│   └── health.py        # GET /health
│
└── services/
    ├── ai_engine.py     # GPT-4o + Whisper + webhook function calling
    ├── telegram.py      # Telethon wrapper (send_message, send_file, get_client)
    ├── queue.py         # QueueWorker + enqueue_message/file + rate limits
    ├── listener.py      # TelegramListener (запускается в отдельном контейнере)
    └── encryption.py    # Fernet шифрование/расшифрование session strings
```

### Как добавить новый эндпоинт

1. Создать файл роутера или добавить в существующий:

```python
# app/routers/my_feature.py
from fastapi import APIRouter, Depends
from app.routers.auth import verify_api_key

router = APIRouter(prefix="/api/v1/my-feature", tags=["my_feature"])

@router.get("")
async def my_endpoint(_: str = Depends(verify_api_key)):
    return {"hello": "world"}
```

2. Зарегистрировать в `app/main.py`:

```python
from app.routers import my_feature
app.include_router(my_feature.router)
```

3. При необходимости добавить новую модель в `app/models/__init__.py` и Pydantic схему в `app/schemas/__init__.py`. Таблица создастся автоматически при следующем старте (через `Base.metadata.create_all`).

### Известные ограничения и TODO

- `response_delay_seconds` в AIContext не реализован (TODO в коде)
- `auto_pause_triggers` в AIContext не реализован (TODO в коде)
- `faq` в AIContext хранится в JSONB, но не передаётся в контекст AI
- Onboarding сессии хранятся в памяти (dict), а не в Redis — при перезапуске API теряются
- `sessions_valid` в `/health` всегда равен количеству активных sender'ов (упрощение, не проверяет реальную валидность сессий)
- Listener не подхватывает новых sender'ов динамически — требуется `docker restart telegram-listener`
