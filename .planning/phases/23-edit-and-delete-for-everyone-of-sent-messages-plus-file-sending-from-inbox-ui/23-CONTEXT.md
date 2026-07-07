# Phase 23: Edit and delete-for-everyone of sent messages plus file sending from inbox UI - Context

**Gathered:** 2026-07-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Из inbox UI менеджер по существующей беседе (`conversation`) может:
1. **Удалить** уже отправленное сообщение у обеих сторон (delete-for-everyone / `revoke=True`).
2. **Отредактировать** уже отправленное текстовое сообщение.
3. **Отправить файл** контакту (multipart-загрузка из браузера).

Все действия — прямой Telethon-вызов вне очереди (как ручная отправка текста в Phase 5,
D-04), с резолвом peer'а по `telegram_id`. Дополнительно (по решению пользователя) фаза
включает **отображение входящих файлов ОТ контакта** в inbox: листенер записывает входящие
медиа как file-бабблы, байты тянутся из Telegram по запросу.

**Вне scope:** синхронизация правок/удалений, сделанных самим контактом (события
`MessageEdited`/`MessageDeleted` от собеседника); редактирование подписей к уже отправленным
файлам; массовые операции над сообщениями.
</domain>

<decisions>
## Implementation Decisions

### Удаление (delete-for-everyone)
- **D-01:** Удалять можно **только наши исходящие** сообщения (`direction='outbound'`,
  `sent_by IN ('ai','human')`). Telegram гарантированно разрешает `revoke` своих сообщений
  в приватном чате. Сообщения контакта из UI не удаляем.
- **D-02:** Telethon: `client.delete_messages(peer, [telegram_message_id], revoke=True)`.
- **D-03:** После успешного revoke — **жёсткое удаление** строки из `messages`
  (`DELETE`, без tombstone/`deleted_at`). Превью `last_message` в списке бесед пересчитается
  из следующего по времени сообщения (существующий LATERAL-подзапрос в conversations.py).
- **D-04:** Удаление **НЕ** делает авто-takeover (не трогает `ai_enabled`/`status`/очередь) —
  это правка прошлого, а не новое вмешательство.

### Редактирование
- **D-05:** Редактируем **только текстовые** исходящие сообщения (подписи к файлам — вне scope v1).
- **D-06:** Telethon: `client.edit_message(peer, telegram_message_id, new_text)`.
- **D-07:** Локально — обновить `message_text` **на месте** + выставить `edited_at = NOW()`.
  Прежние версии текста НЕ храним. UI показывает пометку «(изменено)».
- **D-08:** Редактирование **НЕ** делает авто-takeover (как удаление, D-04).

### Отправка файла (исходящее из inbox)
- **D-09:** Файл приходит **multipart-загрузкой** прямо в API (объектного хранилища нет):
  API → temp-файл → Telethon. Не используем file_url-путь для inbox.
- **D-10:** Лимит размера **~50 МБ**, типы **любые**. Превышение → ошибка `FILE_TOO_LARGE`
  (проверка до/во время приёма, чтобы не держать гигантские файлы в памяти).
- **D-11:** Слать **авто-медиа**: фото/видео — инлайн-медиа, остальное — документом
  (`force_document=False`, полагаться на авто-детект Telethon). Это отличается от текущего
  очередного `send_file()` (там `force_document=True`).
- **D-12:** Отправка файла — **новое исходящее → авто-takeover как `/send` (D-04 Phase 5):**
  `status='manual'`, `ai_enabled=false`, `paused_reason` выставить, погасить pending-очередь
  для `recipient_phone`.
- **D-13:** Подпись (caption) поддерживается; при превышении лимита Telegram для медиа
  (1024 симв.) — досылать overflow отдельным текстовым сообщением (переиспользовать
  существующий паттерн в `send_file()`).
- **D-14:** Гейты `/send` переносятся: sender `lifecycle_status='active'` + `auth_status='ok'`;
  у контакта должен быть `contact_telegram_id` (иначе `NO_TELEGRAM_ID`). Peer резолвится по
  `telegram_id` тем же путём, что `send_message_by_telegram_id` (cache → `get_dialogs(200)` →
  retry). После отправки temp-файл удаляется; байты файла в БД не храним.

### Входящие медиа (ОТ контакта) — в scope
- **D-15:** Листенер (`app/services/listener.py`, `NewMessage` handler) детектит медиа во
  входящих и записывает строку `messages` с `message_type='file'` (или конкретный тип) +
  метаданными (имя, mime, размер) **сразу**. Байты НЕ качаем в момент приёма.
- **D-16:** Байты входящего файла тянутся **из Telegram по запросу** (lazy) через endpoint
  скачивания — когда менеджер жмёт «скачать». Без объектного хранилища и фоновой загрузки.

### Ошибки
- **D-17:** Отдавать **структурированные коды ошибок** + фронт рисует тост и откатывает
  оптимистичный UI. Коды минимум: `MESSAGE_EDIT_TOO_OLD` (Telegram
  `MessageEditTimeExpiredError`), `MESSAGE_NOT_EDITABLE`, `DELETE_FAILED`, `FILE_TOO_LARGE`,
  плюс переиспользуемые из send-пути (`NO_TELEGRAM_ID`, `RECIPIENT_NOT_IN_TELEGRAM`,
  `FLOOD_WAIT`, `ACCOUNT_FROZEN`, `USER_IS_BLOCKED`). Обновить `lovable-handoff/error-codes.md`.

### API-контракт (REST по message_id)
- **D-18:** Эндпоинты (все под `Depends(auth_dep)` + workspace-scope, префикс
  `/api/v1/conversations`):
  - `PATCH  /{id}/messages/{message_id}` — правка текста.
  - `DELETE /{id}/messages/{message_id}` — delete-for-everyone (revoke).
  - `POST   /{id}/send-file` — отправка файла (multipart/form-data).
  - `GET    /{id}/messages/{message_id}/file` — on-demand скачивание входящего файла
    (стрим из Telegram). Точное имя/форма — на усмотрение планировщика, но по `message_id`.
- **D-19:** Все эндпоинты workspace-scoped, cross-workspace → 404 (паттерн
  `_load_conversation_or_404`). `message_id` должен принадлежать беседе беседы+воркспейсу.

### Модель данных (`messages`)
- **D-20:** Расширить таблицу `messages` (idempotent-миграция `NNN_*.sql`, авто-applier):
  - `message_type` — тип сообщения (напр. `text` | `file` | `photo` | `video` | `document`),
    `NOT NULL DEFAULT 'text'` (не ломает существующие строки).
  - медиа-метаданные: имя файла, mime-тип, размер (nullable).
  - `edited_at TIMESTAMPTZ NULL` — метка правки (D-07).
  - `message_text` → **NULLABLE** (сейчас `NOT NULL`) для file-бабблов без текста.
  - **Нет** `deleted_at` — удаление жёсткое (D-03).
- **D-21:** Миграция обязана быть идемпотентной (`ADD COLUMN IF NOT EXISTS`,
  `ALTER COLUMN ... DROP NOT NULL`). ⚠️ Помнить про ORM `default=` vs `server_default=` drift
  (см. память `project-orm-default-vs-server-default-drift`): для `message_type` задать и
  `server_default`, и ORM-значение, иначе raw-SQL INSERT на fresh/recovered DB упадёт
  `NotNullViolation`. Ослабление `message_text` NOT NULL проверить против всех текущих
  INSERT-путей (send, listener, warmup) — существующие пути всегда пишут текст, так что
  ослабление безопасно.

### Frontend (Lovable, отдельный репо)
- **D-22:** Изменения фронта делаются через **handoff-спеку**: обновить
  `lovable-handoff/openapi.json` (новые эндпоинты/схемы) + `error-codes.md`; Lovable
  регенерит UI в репо `AGS-Venture-Lab/aimly-tg-outreach`. Планирование в ЭТОМ репо =
  backend + handoff. NB: Lovable может слать нестандартные имена полей (см. `SendMessageFromUIRequest`
  с `AliasChoices("message","message_text")`) — для multipart/новых body закладывать
  толерантность к алиасам.

### Claude's Discretion
- Точные имена медиа-колонок и `message_type` enum-значений.
- Реализация лимита 50 МБ (проверка `Content-Length` vs стриминг в temp с ранним обрывом).
- Форма endpoint'а скачивания входящего файла (`GET .../file` vs query-параметр), заголовки
  `Content-Disposition`/`Content-Type` при стриме.
- Как отличать edit-too-old от прочих Telethon-ошибок (маппинг исключений → коды D-17).
- Нужен ли отдельный Telethon-метод-обёртка per операцию (по образцу
  `send_message_by_telegram_id` / профильных методов Phase 20) — вероятно да, но на усмотрение.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

В ROADMAP.md для Phase 23 canonical refs не заданы. Ниже — код и доки, определяющие
паттерны и точки интеграции для этой фазы.

### Inbox / send-паттерн (переиспользовать)
- `app/routers/conversations.py` — существующий inbox-роутер: `/{id}/send` (D-04
  auto-takeover), `_load_conversation_or_404`, workspace-scope, `GET /{id}/messages`.
- `.planning/phases/05-inbox-analytics/05-CONTEXT.md` — решения Phase 5 (D-01..D-04
  manual takeover, отмена очереди, статусы беседы).
- `app/services/telegram.py` — `send_message()`, `send_file()` (существующий очередной
  file-путь: caption-overflow, error-маппинг), `send_message_by_telegram_id()` (резолв peer'а
  по telegram_id: cache → `get_dialogs` → retry), профильные методы Phase 20 (скелет
  client-per-op + `disconnect_client` в finally).

### Листенер (входящие медиа)
- `app/services/listener.py` — `NewMessage(incoming/outgoing)` handlers; сюда добавляется
  детект/запись входящих медиа (D-15/D-16).

### Модель данных
- `migrations/017_phase5.sql` — текущий DDL таблицы `messages` (колонки, unique-констрейнт
  `(conversation_id, telegram_message_id)`, индексы).
- `app/models/__init__.py` — ORM `Message*`/`MessageType` enum, `QueueItemType`.
- `app/schemas/__init__.py` — `MessageResponse`, `SendMessageFromUIRequest/Response`,
  `SendFileRequest` (образец полей file-запроса).

### Frontend handoff
- `lovable-handoff/openapi.json` — источник правды API для генерации фронта (обновить).
- `lovable-handoff/error-codes.md` — реестр кодов ошибок (обновить, D-17).

### Правила проекта
- `CLAUDE.md` (`/root/apps/aimly/tg-outreach/CLAUDE.md`) — миграции: только raw SQL,
  идемпотентность, авто-applier fail-fast; async everywhere; очередь/rate-limits не трогать
  без обсуждения; тесты только через test-overlay.
- `.planning/codebase/STRUCTURE.md` — двух-репозиторная топология (backend vs Lovable-фронт).
- `.planning/codebase/INTEGRATIONS.md` — точки интеграции backend↔Telethon↔фронт.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`send_message_by_telegram_id()`** (telegram.py) — peer-резолв по `telegram_id`
  (cache → `get_dialogs(200)` → retry). Edit/delete/file-send из inbox нуждаются в том же
  резолве; вынести общий хелпер получения peer'а или добавить методы `edit_message_by_...`,
  `delete_message_by_...`, `send_file_by_telegram_id` по этому образцу.
- **`send_file()`** (telegram.py) — готовый паттерн: caption-overflow (1024→follow-up),
  полный error-маппинг (FloodWait/PeerFlood/UserIsBlocked/frozen). Переиспользовать
  логику, но: (а) источник — temp-файл из multipart, а не URL; (б) `force_document=False`
  (авто-медиа, D-11); (в) резолв по `telegram_id`, а не по phone/ImportContacts.
- **`/{id}/send` (conversations.py)** — эталон auto-takeover + отмены очереди для file-send (D-12).
- **`_load_conversation_or_404`** — workspace-gate для всех новых эндпоинтов (D-19).

### Established Patterns
- Мутации из inbox: 1) load+gate в транзакции, 2) commit takeover/cancel, 3) Telethon
  **вне** транзакции, 4) INSERT/UPDATE `messages` после успеха. Edit/delete инвертируют:
  Telethon-операция, затем UPDATE/DELETE строки `messages`.
- Клиент-на-операцию: `get_client(...)` → операция → `disconnect_client()` в `finally`.
- Ошибки Telethon маппятся в структурированные `{code, message}` (не пробрасываем raw).
- `messages` unique `(conversation_id, telegram_message_id)` — использовать для точечного
  UPDATE/DELETE и идемпотентности INSERT входящих (`ON CONFLICT DO NOTHING`).

### Integration Points
- Новые роуты — в `app/routers/conversations.py` (тот же префикс/auth).
- Миграция — `migrations/NNN_phase23_*.sql` (авто-applier при старте api).
- Листенер — расширить `NewMessage` handler для входящих медиа (D-15).
- Handoff — `lovable-handoff/openapi.json` + `error-codes.md`.
- Деплой: `docker compose up -d --build api` (и `listener`, т.к. трогаем листенер).
</code_context>

<specifics>
## Specific Ideas

- Разделение takeover-семантики осознанное: **новое исходящее** (текст/файл) → takeover;
  **правка/удаление прошлого** → без takeover. Это ключевой инвариант поведения фазы.
- Отправка файла должна ощущаться как обычная отправка в чате (фото приходит фото, не
  документом) — отсюда авто-медиа вместо `force_document`.
</specifics>

<deferred>
## Deferred Ideas

- **Синхронизация правок/удалений от контакта** (события `MessageEdited`/`MessageDeleted`
  в листенере) — отдельная фаза. Сейчас входящие только записываются как новые, изменения
  собеседника не отслеживаются.
- **Редактирование подписей к уже отправленным файлам** — вне v1 (D-05).
- **Массовые операции** над сообщениями (bulk-delete в треде) — не поднимались, но логичное
  продолжение; в backlog при необходимости.
- **Постоянное хранилище медиа** (объектное хранилище + фоновая предзагрузка входящих
  файлов) — сейчас lazy on-demand из Telegram (D-16); при росте нагрузки пересмотреть.

None-of-the-above todos: нет (todo match-phase вернул 0).
</deferred>

---

*Phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui*
*Context gathered: 2026-07-07*
