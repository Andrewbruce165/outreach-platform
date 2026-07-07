# Phase 23: Edit & delete-for-everyone of sent messages + file sending from inbox UI — Research

**Researched:** 2026-07-07
**Domain:** Telethon 1.42.0 message mutations (edit / delete-revoke / send_file / media download), FastAPI multipart, brownfield inbox router extension
**Confidence:** HIGH (grounded in the installed Telethon source + the actual repo code; live-smoke is the only remaining unknown)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions (D-01..D-22, verbatim)

**Удаление (delete-for-everyone)**
- **D-01:** Удалять можно **только наши исходящие** сообщения (`direction='outbound'`, `sent_by IN ('ai','human')`). Telegram гарантированно разрешает `revoke` своих сообщений в приватном чате. Сообщения контакта из UI не удаляем.
- **D-02:** Telethon: `client.delete_messages(peer, [telegram_message_id], revoke=True)`.
- **D-03:** После успешного revoke — **жёсткое удаление** строки из `messages` (`DELETE`, без tombstone/`deleted_at`). Превью `last_message` в списке бесед пересчитается из следующего по времени сообщения (существующий LATERAL-подзапрос в conversations.py).
- **D-04:** Удаление **НЕ** делает авто-takeover (не трогает `ai_enabled`/`status`/очередь) — это правка прошлого, а не новое вмешательство.

**Редактирование**
- **D-05:** Редактируем **только текстовые** исходящие сообщения (подписи к файлам — вне scope v1).
- **D-06:** Telethon: `client.edit_message(peer, telegram_message_id, new_text)`.
- **D-07:** Локально — обновить `message_text` **на месте** + выставить `edited_at = NOW()`. Прежние версии текста НЕ храним. UI показывает пометку «(изменено)».
- **D-08:** Редактирование **НЕ** делает авто-takeover (как удаление, D-04).

**Отправка файла (исходящее из inbox)**
- **D-09:** Файл приходит **multipart-загрузкой** прямо в API (объектного хранилища нет): API → temp-файл → Telethon. Не используем file_url-путь для inbox.
- **D-10:** Лимит размера **~50 МБ**, типы **любые**. Превышение → ошибка `FILE_TOO_LARGE` (проверка до/во время приёма, чтобы не держать гигантские файлы в памяти).
- **D-11:** Слать **авто-медиа**: фото/видео — инлайн-медиа, остальное — документом (`force_document=False`, полагаться на авто-детект Telethon). Отличается от текущего очередного `send_file()` (там `force_document=True`).
- **D-12:** Отправка файла — **новое исходящее → авто-takeover как `/send` (D-04 Phase 5):** `status='manual'`, `ai_enabled=false`, `paused_reason` выставить, погасить pending-очередь для `recipient_phone`.
- **D-13:** Подпись (caption) поддерживается; при превышении лимита Telegram для медиа (1024 симв.) — досылать overflow отдельным текстовым сообщением (переиспользовать существующий паттерн в `send_file()`).
- **D-14:** Гейты `/send` переносятся: sender `lifecycle_status='active'` + `auth_status='ok'`; у контакта должен быть `contact_telegram_id` (иначе `NO_TELEGRAM_ID`). Peer резолвится по `telegram_id` тем же путём, что `send_message_by_telegram_id` (cache → `get_dialogs(200)` → retry). После отправки temp-файл удаляется; байты файла в БД не храним.

**Входящие медиа (ОТ контакта) — в scope**
- **D-15:** Листенер (`NewMessage` handler) детектит медиа во входящих и записывает строку `messages` с `message_type='file'` (или конкретный тип) + метаданными (имя, mime, размер) **сразу**. Байты НЕ качаем в момент приёма.
- **D-16:** Байты входящего файла тянутся **из Telegram по запросу** (lazy) через endpoint скачивания — когда менеджер жмёт «скачать». Без объектного хранилища и фоновой загрузки.

**Ошибки**
- **D-17:** Структурированные коды ошибок + фронт рисует тост и откатывает оптимистичный UI. Коды минимум: `MESSAGE_EDIT_TOO_OLD` (Telegram `MessageEditTimeExpiredError`), `MESSAGE_NOT_EDITABLE`, `DELETE_FAILED`, `FILE_TOO_LARGE`, плюс переиспользуемые из send-пути (`NO_TELEGRAM_ID`, `RECIPIENT_NOT_IN_TELEGRAM`, `FLOOD_WAIT`, `ACCOUNT_FROZEN`, `USER_IS_BLOCKED`). Обновить `lovable-handoff/error-codes.md`.

**API-контракт (REST по message_id)**
- **D-18:** Эндпоинты (все под `Depends(auth_dep)` + workspace-scope, префикс `/api/v1/conversations`):
  - `PATCH  /{id}/messages/{message_id}` — правка текста.
  - `DELETE /{id}/messages/{message_id}` — delete-for-everyone (revoke).
  - `POST   /{id}/send-file` — отправка файла (multipart/form-data).
  - `GET    /{id}/messages/{message_id}/file` — on-demand скачивание входящего файла (стрим из Telegram). Точное имя/форма — на усмотрение планировщика, но по `message_id`.
- **D-19:** Все эндпоинты workspace-scoped, cross-workspace → 404 (паттерн `_load_conversation_or_404`). `message_id` должен принадлежать беседе+воркспейсу.

**Модель данных (`messages`)**
- **D-20:** Расширить таблицу `messages` (idempotent-миграция `NNN_*.sql`, авто-applier):
  - `message_type` — тип сообщения (напр. `text` | `file` | `photo` | `video` | `document`), `NOT NULL DEFAULT 'text'`.
  - медиа-метаданные: имя файла, mime-тип, размер (nullable).
  - `edited_at TIMESTAMPTZ NULL` — метка правки (D-07).
  - `message_text` → **NULLABLE** (сейчас `NOT NULL`) для file-бабблов без текста.
  - **Нет** `deleted_at` — удаление жёсткое (D-03).
- **D-21:** Миграция идемпотентна (`ADD COLUMN IF NOT EXISTS`, `ALTER COLUMN ... DROP NOT NULL`). ⚠️ Помнить про ORM `default=` vs `server_default=` drift: для `message_type` задать и `server_default`, и ORM-значение. Ослабление `message_text` NOT NULL проверить против всех текущих INSERT-путей.

**Frontend (Lovable, отдельный репо)**
- **D-22:** Изменения фронта через **handoff-спеку**: обновить `lovable-handoff/openapi.json` (новые эндпоинты/схемы) + `error-codes.md`; Lovable регенерит UI. NB: Lovable может слать нестандартные имена полей — закладывать толерантность к алиасам.

### Claude's Discretion
- Точные имена медиа-колонок и `message_type` enum-значений.
- Реализация лимита 50 МБ (проверка `Content-Length` vs стриминг в temp с ранним обрывом).
- Форма endpoint'а скачивания входящего файла (`GET .../file` vs query-параметр), заголовки `Content-Disposition`/`Content-Type`.
- Как отличать edit-too-old от прочих Telethon-ошибок (маппинг исключений → коды D-17).
- Нужен ли отдельный Telethon-метод-обёртка per операцию — вероятно да, но на усмотрение.

### Deferred Ideas (OUT OF SCOPE)
- Синхронизация правок/удалений от контакта (`MessageEdited`/`MessageDeleted` события) — отдельная фаза.
- Редактирование подписей к уже отправленным файлам — вне v1 (D-05).
- Массовые операции над сообщениями (bulk-delete в треде).
- Постоянное хранилище медиа (объектное хранилище + фоновая предзагрузка) — сейчас lazy on-demand (D-16).
</user_constraints>

<phase_requirements>
## Phase Requirements

**No REQ-IDs assigned.** ROADMAP.md maps Phase 23 requirements as **TBD** — they have not yet been derived. The prompt explicitly instructs: *do not fabricate REQ-IDs*. The planner should derive the phase's requirement IDs from the 22 locked decisions during `/gsd:plan-phase` (the established pattern used for NDLG/PACE/WARM/KB/SRLD/LLMP/PROF/IMPT), then append them to REQUIREMENTS.md with a `Phase 23:` header and a plan→requirement map.

Suggested requirement clusters (planner to formalise & number):

| Cluster | Backed by decisions | Research support |
|---------|--------------------|------------------|
| Delete-for-everyone (revoke + hard-delete row, no takeover) | D-01, D-02, D-03, D-04 | §Code Example 2, §Pitfall 4 |
| Edit sent text (edit_message + edited_at, no takeover) | D-05, D-06, D-07, D-08 | §Code Example 1, §Pitfall 1 |
| Send file from inbox (multipart→temp→auto-media, takeover, caption overflow) | D-09..D-14 | §Code Example 3, §Code Example 6, §Pitfall 3 |
| Incoming media recording (listener tags message_type + metadata, no bytes) | D-15 | §Code Example 4, §Pitfall 5 |
| Lazy media download endpoint | D-16, D-18 | §Code Example 5, §Pitfall 6 |
| Structured error codes | D-17 | §Common Pitfalls, §error-codes |
| REST-by-message_id API + workspace gate | D-18, D-19 | §Architecture Patterns |
| `messages` schema extension (migration 053) | D-20, D-21 | §Standard Stack, §Pitfall 2 |
| Frontend handoff (openapi + error-codes) | D-22 | §Architecture Patterns |
</phase_requirements>

## Summary

This phase adds four inbox capabilities on top of the **existing, proven send path** — it is almost entirely a matter of copying established in-repo patterns, not learning new library territory. The library (`telethon==1.42.0`, pinned in `requirements.txt`) is already the workhorse; every method this phase needs (`edit_message`, `delete_messages`, `send_file`, `get_messages`, `download_media`) exists and is stable. **No new runtime dependency is required.** `python-multipart==0.0.6` (needed for FastAPI `UploadFile`) is already installed.

The strongest guidance is: **mirror three existing seams.** (1) The `send_message_by_telegram_id` client-per-op skeleton in `app/services/telegram.py` (get_client → op → `disconnect_client` in `finally`, peer resolve cache→`get_dialogs(200)`→retry) is the template for four new service methods. (2) The `POST /{id}/send` handler in `app/routers/conversations.py` is the template for `send-file` (auto-takeover ordering) and the workspace-gate for all endpoints. (3) The `_raise_profile_telegram_error` mapping table in `app/routers/senders.py` (matches on `type(e).__name__` + message text) is the template for the D-17 error codes. The listener already detects incoming photo/video/voice/document (`handle_incoming_message`, lines 858-916) — D-15 is a matter of *persisting the type + metadata into new columns* rather than only a text label.

Two brownfield gotchas dominate risk. **(a)** The `messages` table has **no ORM model** — it is raw-SQL only (created by `migrations/017_phase5.sql`). Therefore the D-21 ORM-drift concern is moot for `messages` (create_all never builds it), but the flip side is that **`tests/conftest.py` must be taught to apply the new migration 053** (its migration list is a hardcoded list, NOT a glob), or integration tests will hit `UndefinedColumn`. **(b)** There is an existing `MessageType` enum in `app/models/__init__.py` (`sent`/`draft`/`failed`) that belongs to a **different** table (`messages_log`) — D-20's `message_type` column is a **different concept**; do not reuse that enum.

**Primary recommendation:** Add migration `053_phase23_messages_media.sql` (extend `messages`), add four `*_by_telegram_id` methods to `TelegramService` (edit / delete / send_file / download_media) each cloning the `send_message_by_telegram_id` skeleton + a shared peer-resolve helper, add four endpoints to `conversations.py`, extend the listener's incoming-media branch to write `message_type` + metadata, extend `MessageResponse` (+ the `GET /messages` SELECT), map errors via a Phase-23 clone of `_raise_profile_telegram_error`, and regenerate the Lovable handoff.

## Standard Stack

### Core (all already present — nothing to install)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| telethon | **1.42.0** (pinned, `requirements.txt:12`) | `edit_message`, `delete_messages`, `send_file`, `get_messages`, `download_media` | Already the only Telegram client in the codebase; all methods stable across 1.4x |
| fastapi | 0.109.0 | routing, `UploadFile`/`File`, `Response` for byte download | Existing router framework |
| python-multipart | 0.0.6 | multipart/form-data parsing for `UploadFile` | **Already installed** — required for D-09 file upload; without it FastAPI 500s on multipart |
| sqlalchemy (async) | 2.0 | raw-SQL `text()` execute (repo convention) | Repo uses raw SQL for the `messages` table (no ORM model) |

### Supporting (optional — evaluate, do not assume)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| cryptg | latest | C-accelerated MTProto decryption → faster `download_media` | **NOT currently installed.** D-16 downloads media byte-for-byte in pure Python. For 50 MB files this is noticeably slow. Consider adding `cryptg` to speed up the download endpoint; not blocking. Telethon prints a hint recommending it. |
| aiofiles | latest | async temp-file writes | **NOT installed.** The existing `send_file` uses **sync** `tempfile.NamedTemporaryFile` + `f.write()`. Local-disk temp writes are fast; `asyncio.to_thread(...)` around the sync write satisfies "async everywhere" without a new dep. Prefer `to_thread` over adding aiofiles. |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Download-to-bytes then `Response(content=...)` for D-16 | `StreamingResponse` + `client.iter_download(media)` | Streaming keeps peak memory low but **fights the per-op `disconnect_client` pattern** — the client must stay connected for the whole response body. For a 50 MB cap, download-to-bytes (or to a temp file) within one client-per-op then return `Response` is simpler and matches PROF-07's `GET /senders/{slug}/photo`. Recommend download-to-bytes for v1; revisit streaming only if very large files become common. |
| New `message_type` values as a Postgres ENUM type | `VARCHAR + CHECK` | Repo convention is **VARCHAR + CHECK** (see `conversations_status_check`, `campaigns.status`). `ALTER TYPE ADD VALUE` cannot run in a transaction (Phase 4 Q6 lesson, STATE.md). Use `VARCHAR(20) NOT NULL DEFAULT 'text'` + `CHECK (message_type IN (...))`. |

**Installation:** none required. Optionally: `echo "cryptg==0.4.0" >> requirements.txt` (download speed) — verify current version with `pip index versions cryptg` before pinning.

## Architecture Patterns

### Recommended integration points (all existing files)
```
app/services/telegram.py    # +4 methods (edit/delete/send_file/download by telegram_id)
                            #  + 1 shared _resolve_peer_by_telegram_id helper
app/routers/conversations.py# +4 endpoints (PATCH/DELETE messages, POST send-file, GET file)
                            #  + a Phase-23 error-mapping helper (clone of senders._raise_profile_telegram_error)
app/services/listener.py    # extend handle_incoming_message media branch (D-15): persist type + metadata
app/schemas/__init__.py     # extend MessageResponse; add SendFileFromUIResponse / EditMessageRequest
app/models/__init__.py      # NO Message ORM model exists — leave messages raw-SQL; DO NOT add ENUM collision
migrations/053_phase23_messages_media.sql  # extend messages (idempotent)
tests/conftest.py           # add 053 to the migration list (exists-guard) — MANDATORY (list is not a glob)
lovable-handoff/openapi.json + error-codes.md  # D-22 handoff regen
```

### Pattern 1: Client-per-op service method (clone `send_message_by_telegram_id`)
**What:** Every Telethon operation creates a temporary client via `get_client`, does one op, and **always** `disconnect_client` in `finally` (persistent connections steal updates from the listener container — `TelegramService` docstring, telegram.py:274-279).
**When to use:** All four new methods (edit / delete / send_file / download).
**Peer resolve:** reuse the exact ladder from `send_message_by_telegram_id` (telegram.py:1110-1120): `get_input_entity(telegram_id)` → on `ValueError` `get_dialogs(limit=200)` → retry. Extract this into a shared helper so all four methods share it.
```python
# Source: app/services/telegram.py:1082-1142 (send_message_by_telegram_id) — proven skeleton
async def _resolve_peer_by_telegram_id(self, client, telegram_id: int):
    try:
        return await client.get_input_entity(telegram_id)
    except ValueError:
        await client.get_dialogs(limit=200)   # warms access_hash for recent dialogs
        return await client.get_input_entity(telegram_id)
```

### Pattern 2: Mutation ordering (differs by op — this is the key behavioural invariant)
**Send-file (new outbound → takeover, D-12):** mirror `POST /{id}/send` (conversations.py:393-503) EXACTLY:
1. Load conversation+sender in one SELECT gated on `workspace_id` + `s.lifecycle_status='active'` + `s.auth_status='ok'` → 404 if none; `contact_telegram_id IS NULL` → 400 `NO_TELEGRAM_ID`.
2. Auto-takeover UPDATE (`ai_enabled=false, status='manual', paused_reason='Manager sent file via UI'`).
3. Cancel pending queue items for `recipient_phone` (`status='failed'`, `error_message='Conversation taken over manually'`).
4. `await db.commit()`.
5. Telethon send **OUTSIDE** the transaction.
6. On success, `INSERT` a `messages` row (`direction='outbound'`, `sent_by='human'`, `message_type=<detected>`, metadata, `telegram_message_id`) with `ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING`.

**Edit / delete (past edit → NO takeover, D-04/D-08):** the CONTEXT `code_context` note says these **invert** the ordering — do the Telethon op FIRST, then the DB write:
1. Load the message gated on `messages.id=:mid AND conversation_id=:cid AND conversations.workspace_id=:wid` AND `direction='outbound'` AND `sent_by IN ('ai','human')` (D-01/D-05) AND (for edit) `message_type='text'` (D-05) → 404 if none.
2. Also pull the sender session/proxy/fingerprint + `telegram_message_id` + `contact_telegram_id` in that SELECT.
3. Telethon `edit_message` / `delete_messages` OUTSIDE any txn.
4. On success: edit → `UPDATE messages SET message_text=:new, edited_at=NOW()`; delete → `DELETE FROM messages WHERE id=:mid`.
5. **No** conversation/queue mutation.

### Pattern 3: Workspace + message-id gate (D-19)
Reuse `_load_conversation_or_404` for the conversation, then a second guard that the `message_id` belongs to that conversation. A single JOINed SELECT is cleanest:
```sql
SELECT m.telegram_message_id, m.direction, m.sent_by, m.message_type,
       c.contact_telegram_id, s.id AS sender_id, s.slug, s.session_string, s.proxy, s.client_fingerprint
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
JOIN senders s ON s.id = c.sender_id
WHERE m.id = :mid AND m.conversation_id = :cid AND c.workspace_id = :wid
```
Cross-workspace or wrong-conversation → 0 rows → 404 (silent isolation, same as everywhere else).

### Pattern 4: Structured Telethon-error mapping (clone senders.py:315-380)
Add a Phase-23 mapping helper that matches on `f"{type(e).__name__} {e}".upper()` so both real Telethon exceptions and test-raised bare `Exception("MESSAGE_EDIT_TIME_EXPIRED")` resolve identically. Map to the D-17 codes (table in §Common Pitfalls).

### Anti-Patterns to Avoid
- **Reusing the `MessageType` enum** (`sent`/`draft`/`failed`) — it is `messages_log`'s, a different table. Define new values.
- **Adding a `Message` ORM model** just to hang `message_type` on it — would trigger the real ORM-drift bug (create_all builds the table without the DB default). Keep `messages` raw-SQL.
- **Pre-gating edit by a client-side time check** — the edit window is server-controlled; catch `MessageEditTimeExpiredError`, don't compute it.
- **`await file.read()` (whole file into memory) for a 50 MB upload** — the repo does this for 5 MB photos / 20 MB KB docs, but D-10 explicitly says stream-and-count. Use chunked read (§Code Example 6).
- **Persisting downloaded media bytes** (D-16 forbids it) — the download endpoint fetches and streams, never writes to `messages`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Photo-vs-video-vs-document decision on send | Custom mime sniffing | `client.send_file(peer, path, force_document=False)` | Telethon's `_file_to_media` already does `is_image = utils.is_image(file)`; images→inline photo, video/*→playable video, else→document (uploads.py:765-868) |
| Detecting incoming media type | Manual `media.__class__` switch | `event.message.photo / .video / .voice / .document` convenience props | Listener already uses exactly these (listener.py:858-889) |
| Reading filename/mime/size before download | Parsing `DocumentAttribute*` by hand | `message.file.name / .mime_type / .size` (`telethon.tl.custom.file.File`) | Wrapper already resolves attributes; available pre-download (file.py:42-137) |
| Caption > 1024 chars | New overflow logic | Copy the `CAPTION_LIMIT = 1024` follow-up-message block from `send_file` | Already implemented (telegram.py:960-987) |
| Peer resolve by telegram_id | New resolution code | `get_input_entity → get_dialogs(200) → retry` | Proven cold-cache fix (telegram.py:1110-1120; CLAUDE.md "Telethon entity-cache cold start") |
| last_message preview after delete | Trigger / recompute job | Nothing — the list/detail LATERAL subquery already recomputes from newest message | conversations.py:152-156, confirmed (D-03) |
| Serving bytes with correct headers | Manual header assembly | `Response(content=data, media_type=mime, headers={"Content-Disposition": ...})` | Mirror PROF-07 `GET /senders/{slug}/photo` (senders.py, returns `Response(content=..., media_type=...)`) |

**Key insight:** Every hard part of this phase already exists somewhere in the repo. The phase is 80% "wire existing patterns to four new endpoints + one migration + one listener branch," 20% new error codes.

## Common Pitfalls

### Pitfall 1: Edit errors — distinguishing "too old" from "not editable" from "unchanged"
**What goes wrong:** Treating every `edit_message` failure as one error, or pre-computing the edit window.
**Root cause / facts (from installed Telethon source):**
- `MESSAGE_EDIT_TIME_EXPIRED` → `telethon.errors.MessageEditTimeExpiredError(BadRequestError)` (rpcerrorlist.py:2344, :5070) → map to **`MESSAGE_EDIT_TOO_OLD`**.
- `MESSAGE_AUTHOR_REQUIRED` → `MessageAuthorRequiredError(ForbiddenError)` (:2326) → **`MESSAGE_NOT_EDITABLE`** (shouldn't hit under D-01 outbound-only, map defensively).
- `MESSAGE_ID_INVALID` → `MessageIdInvalidError(BadRequestError)` (:2371, raised e.g. for messages with reply markup, or wrong id) → **`MESSAGE_NOT_EDITABLE`**.
- `MESSAGE_NOT_MODIFIED` → `MessageNotModifiedError(BadRequestError)` (:2380) → **treat as success no-op** (idempotent), exactly like `set_username` treats `UsernameNotModifiedError` (telegram.py:1231). Re-editing to identical text raises this; the UI edit should still succeed and set `edited_at`.
**How to avoid:** Catch these specific classes; do NOT gate on a client-side 48h timer.
**Edit-window note (MEDIUM confidence):** Telegram historically allows editing **your own** messages for ~48h; behaviour is server-controlled and has loosened over time (Saved Messages / some private chats effectively unlimited). Since it can change server-side, the robust contract is catch-and-map, not pre-check.

### Pitfall 2: `messages` has no ORM model → migration + conftest, not create_all
**What goes wrong:** Assuming the D-21 ORM-drift fix (add `server_default` to the ORM column) applies here, or assuming a fresh/test DB gets the new columns automatically.
**Root cause:** There is **no `Message` ORM class** (grep of `__tablename__` shows only `messages_log`, `message_queue`). The `messages` table is created by `migrations/017_phase5.sql` (`CREATE TABLE IF NOT EXISTS messages`), not by `Base.metadata.create_all`. (The comment at conftest.py:82 claiming create_all "defines messages" is **stale/inaccurate** — verify by grep.)
**Consequences for the plan:**
1. Migration `053` must `ALTER TABLE messages ADD COLUMN ... IF NOT EXISTS` with `NOT NULL DEFAULT 'text'` on `message_type` and `DROP NOT NULL` on `message_text`. Because there is no ORM column, the DB `DEFAULT` is the *only* source of the default — this is correct and sufficient on prod/fresh/test (no drift possible). The D-21 "set server_default AND ORM value" advice is **only** relevant if the planner chooses to add a `Message` ORM model — **recommend NOT adding one**.
2. `tests/conftest.py`'s migration application is a **hardcoded list** (conftest.py:155-186) with exists-guards for later slots (038/041/044/045/046). **You MUST add an exists-guarded apply of `053_phase23_messages_media.sql`** or every inbox integration test will fail with `UndefinedColumn` on the new columns (the repo calls this out at conftest.py:181: "hardcoded list does NOT glob").
**Migration slot:** latest committed is **052** (`052_sender_tg_premium.sql`) → use **`053`**.

### Pitfall 3: `send_file` for inbox differs from the existing queue `send_file` in three ways
**What goes wrong:** Reusing `TelegramService.send_file` as-is.
**Facts:** the existing `send_file` (telegram.py:906-1080) (a) downloads from a **URL** via httpx, (b) uses **`force_document=True`**, (c) resolves peer by **phone/ImportContacts**. The inbox send-file needs (a) a **temp file from multipart**, (b) **`force_document=False`** (D-11 auto-media), (c) peer by **`telegram_id`** (D-14). **Write a new `send_file_by_telegram_id`** rather than overloading the old one; copy only the caption-overflow block (telegram.py:960-987) and the error mapping.

### Pitfall 4: `delete_messages` never raises for a stale/own message → the DB row is the source of truth
**What goes wrong:** Waiting for a "can't revoke" exception that doesn't come.
**Facts (from source, messages.py:1263-1335):** signature is `delete_messages(entity, message_ids, *, revoke=True)` (revoke **defaults True** — opposite of official clients). Returns a list of `AffectedMessages`. The docstring warns it does **not** validate IDs belong to the chat, and for private chats `entity` can even be `None`. Deleting an already-deleted or non-existent id in a private chat is a **silent no-op** (Telegram returns `AffectedMessages` with `pts_count` reflecting real deletions; no error). Revoking **your own** message always succeeds. `MessageDeleteForbiddenError` exists but is for channel/forbidden cases, not private-chat own-message deletes.
**Implication:** `DELETE_FAILED` should be reserved for connection/`FloodWait`/frozen/session-auth failures — not "message already gone." After a successful (or no-op) revoke, **always** `DELETE` the row (D-03). Resolve `entity` via the peer helper for correctness (pass the resolved peer, not `None`).

### Pitfall 5: Incoming media — persist type+metadata without regressing voice-transcription or AI dispatch
**What goes wrong:** Overwriting the listener's existing media handling and breaking voice→AI or the `[📎 ...]` label.
**Facts:** `handle_incoming_message` (listener.py:812-916) already: transcribes **voice** (feeds `[🎤 Голосовое]: <text>` to AI), and for photo/video/document builds a `document_info` label and stores it as `message_text`. `save_message` (listener.py:531-572) is the shared INSERT and currently writes only `message_text`.
**How to avoid:** Extend `save_message` (or add params) to accept `message_type` + `file_name`/`mime`/`size`, and set them in the media branch. Keep the caption in `message_text` (now nullable) and set `message_type` to the concrete type (`photo`/`video`/`document`/`voice`). **Preserve** the voice-transcription path (voice is BOTH a `voice` bubble AND transcribed for the AI) — don't early-return before the AI dispatch for voice. The unique constraint `(conversation_id, telegram_message_id)` keeps the INSERT idempotent (`ON CONFLICT DO NOTHING`).

### Pitfall 6: Lazy download needs the peer AND the message may be gone; `message.file.id` is NOT a reliable key
**What goes wrong:** Storing `message.file.id` and trying to download by it later.
**Facts (file.py:22-33):** `File.id` is the "old bot-API style file_id" and the docstring **warns**: "has not been maintained… may not work under user accounts… will be removed." **Do not use it.** The reliable key is the already-stored `telegram_message_id` + the peer.
**Correct lazy download (D-16):** resolve peer (contact_telegram_id) → `msg = await client.get_messages(peer, ids=telegram_message_id)` (returns a single `Message`, or `None` if deleted — get_messages returns one object when `ids` is a scalar, messages.py:557-575) → `data = await msg.download_media(file=bytes)` (returns bytes in-memory; downloads.py:315 `return result if file is bytes else file`). If `msg is None` or has no media → 404/410 `MEDIA_UNAVAILABLE` (contact deleted it on their side). The sender account is `conversation.sender_id`'s session/proxy/`client_fingerprint`.

### Pitfall 7: 50 MB multipart guard — Content-Length is spoofable
**What goes wrong:** Trusting the `Content-Length` header, or buffering the whole upload in RAM.
**How to avoid:** Stream-and-count into a temp file with an early abort (§Code Example 6). Validate size during receipt, then hand the temp path to `send_file_by_telegram_id`, and `os.unlink` in `finally` (D-14). The existing 5 MB photo / 20 MB doc handlers use `await file.read()` — acceptable at those sizes, but D-10 mandates streaming at 50 MB.

## Code Examples

### Code Example 1 — Edit sent text (D-06/D-07/D-17)
```python
# Service (app/services/telegram.py) — client-per-op skeleton (clone of send_message_by_telegram_id)
# Telethon: edit_message(entity, message, text). Source: messages.py:1085-1110 signature/docstring.
async def edit_message_by_telegram_id(self, sender_slug, sender_id, encrypted_session,
                                       telegram_id: int, telegram_message_id: int, new_text: str,
                                       proxy=None, fingerprint=None) -> dict:
    client = None
    try:
        client = await self.get_client(sender_slug, sender_id, encrypted_session,
                                        proxy=proxy, fingerprint=fingerprint)
        peer = await self._resolve_peer_by_telegram_id(client, telegram_id)
        await client.edit_message(peer, telegram_message_id, new_text)   # raises MessageEditTimeExpiredError etc.
        return {"success": True}
    finally:
        if client:
            await self.disconnect_client(client)
# Router maps MessageNotModifiedError → success no-op; MessageEditTimeExpiredError → MESSAGE_EDIT_TOO_OLD;
# MessageAuthorRequiredError/MessageIdInvalidError → MESSAGE_NOT_EDITABLE (see Pitfall 1).
```

### Code Example 2 — Delete-for-everyone (D-02/D-03)
```python
# Telethon: delete_messages(entity, message_ids, *, revoke=True). Source: messages.py:1263-1335.
async def delete_message_by_telegram_id(self, sender_slug, sender_id, encrypted_session,
                                        telegram_id: int, telegram_message_id: int,
                                        proxy=None, fingerprint=None) -> dict:
    client = None
    try:
        client = await self.get_client(...)
        peer = await self._resolve_peer_by_telegram_id(client, telegram_id)
        await client.delete_messages(peer, [telegram_message_id], revoke=True)  # no-op if already gone
        return {"success": True}
    finally:
        if client:
            await self.disconnect_client(client)
# Router: on success → DELETE FROM messages WHERE id=:mid (D-03). last_message preview auto-recomputes.
```

### Code Example 3 — Send file, auto-media, caption overflow (D-09/D-11/D-13)
```python
# force_document=False → photo inline / video playable / else document (uploads.py:765-868).
CAPTION_LIMIT = 1024   # copy the overflow block verbatim from send_file (telegram.py:960-987)
async def send_file_by_telegram_id(self, ..., telegram_id, tmp_path, file_name, caption=None, ...):
    client = None
    try:
        client = await self.get_client(...)
        peer = await self._resolve_peer_by_telegram_id(client, telegram_id)
        file_caption, overflow = (caption, None) if not caption or len(caption) <= CAPTION_LIMIT else (None, caption)
        sent = await client.send_file(peer, tmp_path, caption=file_caption,
                                      file_name=file_name, force_document=False)   # D-11
        if overflow:
            await client.send_message(peer, overflow)
        return {"success": True, "telegram_message_id": sent.id}
    # except FloodWaitError/PeerFloodError/UserIsBlockedError/UserNotMutualContactError + is_frozen_error(e)
    #   → same structured codes as send_message (telegram.py:838-902) — copy that mapping
    finally:
        if client:
            await self.disconnect_client(client)
```

### Code Example 4 — Incoming media classification + pre-download metadata (D-15)
```python
# In listener handle_incoming_message media branch (extends listener.py:858-916).
m = event.message
if m.photo:      message_type = "photo"
elif m.video:    message_type = "video"
elif m.voice:    message_type = "voice"
elif m.document: message_type = "document"
else:            message_type = "text"
f = m.file  # telethon.tl.custom.File — attributes available WITHOUT downloading (file.py)
file_name = f.name if f else None          # DocumentAttributeFilename.file_name
mime_type = f.mime_type if f else None      # 'image/jpeg' for photos, doc.mime_type otherwise
size_bytes = f.size if f else None          # bytes; for photos = heaviest thumbnail
# Persist via save_message(..., message_type=message_type, file_name=..., mime_type=..., size_bytes=...)
# NEVER read f.id (deprecated, unreliable for user accounts — Pitfall 6). Do NOT download bytes here (D-15).
```

### Code Example 5 — Lazy download endpoint (D-16/D-18)
```python
# GET /api/v1/conversations/{id}/messages/{message_id}/file
# Service method (client-per-op): resolve peer, re-fetch message, download bytes in-memory.
async def download_media_by_telegram_id(self, ..., telegram_id, telegram_message_id) -> dict:
    client = None
    try:
        client = await self.get_client(...)
        peer = await self._resolve_peer_by_telegram_id(client, telegram_id)
        msg = await client.get_messages(peer, ids=telegram_message_id)   # single Message | None
        if not msg or not msg.media:
            return {"success": False, "error": {"code": "MEDIA_UNAVAILABLE"}}
        data = await msg.download_media(file=bytes)                      # bytes (downloads.py:315)
        return {"success": True, "data": data,
                "mime": (msg.file.mime_type if msg.file else "application/octet-stream"),
                "name": (msg.file.name if msg.file else "file")}
    finally:
        if client:
            await self.disconnect_client(client)
# Router returns Response(content=data, media_type=mime,
#   headers={"Content-Disposition": f'attachment; filename="{name}"'})  — mirror PROF-07 photo GET.
```

### Code Example 6 — Streaming multipart size guard into a temp file (D-09/D-10)
```python
# app/routers/conversations.py — POST /{id}/send-file
import os, tempfile, asyncio
MAX_FILE_BYTES = 50 * 1024 * 1024
async def _spool_upload_with_cap(upload) -> tuple[str, int]:
    fd, tmp_path = tempfile.mkstemp()
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)   # UploadFile.read is async
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise HTTPException(status_code=413,
                        detail={"code": "FILE_TOO_LARGE", "message": "Файл больше 50 МБ"})
                await asyncio.to_thread(out.write, chunk)   # keep the event loop free (async-everywhere)
        return tmp_path, total
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
# After send: os.unlink(tmp_path) in finally (D-14 — bytes never persisted to DB).
```

## State of the Art

| Old Approach (current repo) | This-phase Approach | Impact |
|-----------------------------|---------------------|--------|
| Inbox is send-only + read history | Inbox gains edit/delete/send-file mutations + incoming file bubbles | Manager can correct/retract and share files |
| Incoming media stored as a text label `[📎 …]` only | Media stored with `message_type` + name/mime/size, bytes lazy-fetched | Real file bubbles in UI (D-15/D-16) |
| `send_file` uses `force_document=True` + URL source | inbox uses `force_document=False` + multipart temp file | Photos arrive as photos, not documents (D-11) |
| `MessageResponse.message_text: str` (required) | `Optional[str]` + new media fields + `edited_at` | file bubbles have no text; edited flag surfaced |

**Deprecated/outdated:**
- `File.id` (bot-API file_id) — deprecated in Telethon, unreliable for user accounts; do not use as a media key.

## Open Questions

1. **`message_type` enum value set**
   - Known: D-20 suggests `text | file | photo | video | document`; the listener already distinguishes voice.
   - Unclear: whether to add `voice` as a distinct value (voice is both transcribed-for-AI and a media bubble) and whether `file` is a catch-all synonym of `document`.
   - Recommendation: `text | photo | video | voice | document` (drop the generic `file`; map the listener's voice branch to `voice`). Keep it a `VARCHAR + CHECK` so a future value is one idempotent migration.

2. **Voice bubble vs transcription text**
   - Known: voice is transcribed to `[🎤 Голосовое]: …` and fed to AI today.
   - Unclear: whether the persisted `message_text` for voice should keep the transcription (useful for inbox reading) or be nulled with type=`voice`.
   - Recommendation: keep the transcription in `message_text` AND set `message_type='voice'` — no information loss, AI path unchanged.

3. **Download endpoint content-disposition mode**
   - Known: D-16 streams from Telegram; Claude's discretion on `attachment` vs `inline`.
   - Recommendation: `attachment; filename="<name>"` for documents; the frontend can request `inline` for images if it wants preview — leave a query param `?disposition=inline` optional. Default `attachment`.

4. **Edit window exact value (LOW confidence)**
   - Known: `MessageEditTimeExpiredError` is the signal; historically ~48h for own messages.
   - Unclear: the current server-side window for private chats (may be effectively unlimited).
   - Recommendation: don't hard-code — catch-and-map (Pitfall 1). Live-smoke will reveal actual behaviour.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| telethon | all four ops | ✓ (pinned) | 1.42.0 (requirements.txt) | — |
| python-multipart | D-09 multipart upload | ✓ | 0.0.6 | — (without it FastAPI 500s on multipart) |
| httpx | (not needed — inbox uses multipart, not URL) | ✓ | 0.26.0 | — |
| cryptg | faster `download_media` (D-16) | ✗ | — | pure-Python download (works, slower) |
| aiofiles | async temp write | ✗ | — | `asyncio.to_thread(f.write, chunk)` (recommended, no new dep) |
| Live Telegram connection + a real account with sent+received media | live-smoke of all four ops | ✓ (13 imported accounts) | — | unit/integration mock at the `TelegramService` method boundary (see Validation Architecture) |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** `cryptg` (download speed) and `aiofiles` (use `to_thread`) — both optional.

## Validation Architecture

*(nyquist_validation = true in `.planning/config.json` → section included.)*

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`pytestmark = pytest.mark.asyncio`) |
| Config / harness | `tests/conftest.py` builds an ephemeral `outreach_test` DB (create_all + hardcoded migration list); **test-overlay only** |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| MANDATORY | Never `docker compose run --rm api pytest` without the test overlay (conftest guard + CLAUDE.md; DROP-SCHEMA-to-prod hazard) |

### Mock seam (how to test Telethon-dependent code without a live connection)
Mock at the **`TelegramService` method boundary** — the exact pattern in `tests/test_phase5_inbox_send_takeover.py:55-60`:
```python
monkeypatch.setattr(
    "app.services.telegram.telegram_service.<method>",
    AsyncMock(return_value={"success": True, "telegram_message_id": 123}),
)
```
The four new methods (`edit_message_by_telegram_id`, `delete_message_by_telegram_id`, `send_file_by_telegram_id`, `download_media_by_telegram_id`) are each a single mockable async method returning a structured dict — the router logic (gates, ordering, DB writes) is fully testable with the mock; the Telethon call itself is proven by live-smoke.
Factories available: `test_sender_factory`, `test_conversation_factory`, `test_campaign_factory`, `async_client`, `valid_supabase_jwt`, `_bind` (user_workspaces).

### Phase capability → test map
| Capability | Behaviour to assert | Type | Automated command | File exists? |
|-----------|--------------------|------|-------------------|--------------|
| Edit | success → `message_text` updated + `edited_at` set; mock raises `MessageEditTimeExpiredError` → 4xx `MESSAGE_EDIT_TOO_OLD`; `MessageNotModifiedError` → success no-op; editing an inbound/contact msg → 404; cross-ws → 404 (Telethon not called) | integration | `pytest tests/test_phase23_inbox_mutations.py -k edit -x` | ❌ Wave 0 |
| Delete | success → row DELETEd; `revoke=True` passed; no takeover (conversation `status`/`ai_enabled` unchanged); deleting inbound → 404; cross-ws → 404 (not called); connection error → `DELETE_FAILED` | integration | `-k delete` | ❌ Wave 0 |
| Send-file | success → auto-takeover (`status='manual'`, `ai_enabled=false`, pending queue → failed), new `messages` row with correct `message_type`; >50 MB → 413 `FILE_TOO_LARGE` (Telethon not called); no `contact_telegram_id` → 400 `NO_TELEGRAM_ID`; inactive sender → 404; temp file cleaned up | integration | `-k send_file` | ❌ Wave 0 |
| Incoming media | listener writes a `messages` row with `message_type` + name/mime/size; no bytes downloaded; voice still transcribed + AI-dispatched; idempotent on duplicate `telegram_message_id` | integration (listener handler unit) | `-k incoming_media` | ❌ Wave 0 |
| Download | success → `Response` bytes + mime + Content-Disposition; deleted-on-Telegram (mock returns None) → 404/410 `MEDIA_UNAVAILABLE`; cross-ws → 404 | integration | `-k download` | ❌ Wave 0 |
| Migration/schema | fresh test DB has new columns; `message_text` NULL insert accepted; `message_type` defaults to `'text'`; existing rows unaffected | integration | `-k schema` | ❌ Wave 0 |

### Sampling rate
- **Per task commit:** the targeted `-k` subset above.
- **Per wave merge:** `pytest tests/test_phase23_inbox_mutations.py tests/test_phase5_inbox*.py` (guard no inbox regression).
- **Phase gate:** full suite green **before** `/gsd:verify-work`. Note STATE.md memory: full-suite has order-dependent flakiness — run the targeted subset AND a clean-tree diff, don't trust a green full-suite alone.

### Wave 0 gaps
- [ ] `tests/test_phase23_inbox_mutations.py` — new file, covers all six clusters above (RED first).
- [ ] `tests/conftest.py` — **add an exists-guarded apply of `migrations/053_phase23_messages_media.sql`** (mirror the 045/046 exists-guard blocks) — without it the new columns are absent in the test DB (Pitfall 2). This is the single most important Wave-0 task.
- [ ] Possibly extend `test_sender_factory`/`test_conversation_factory` to seed a `messages` row with a media `message_type` for edit/delete/download fixtures (or seed inline per-test as `test_phase5_inbox_send_takeover.py` does).
- [ ] Live-smoke checklist (human, post-merge): edit a just-sent text; delete-for-everyone; send a photo (arrives inline) + a >1024-char caption (overflow follow-up) + a .pdf (document); receive a photo/doc from the contact and download it. Confirms the Telethon calls the mocks stand in for.

## Sources

### Primary (HIGH confidence)
- **Installed Telethon 1.43.2 source** (`/usr/local/lib/python3.13/dist-packages/telethon`; API-identical to pinned 1.42.0 for these methods):
  - `client/messages.py:1085` `edit_message` signature + Raises (MessageAuthorRequired/NotModified/IdInvalid); `:1263` `delete_messages` (revoke=True default, AffectedMessages, silent no-op); `:557` `get_messages` (scalar `ids` → single Message).
  - `client/uploads.py:765-868` `_file_to_media` (`is_image and not force_document` → inline photo; else document/video).
  - `client/downloads.py:315` `download_media(file=bytes)` returns bytes.
  - `tl/custom/file.py:22-137` `File.id` (deprecated), `.name`, `.mime_type`, `.size`.
  - `errors/rpcerrorlist.py:2326-2380,5068-5074` MESSAGE_* error class mapping incl. `MessageEditTimeExpiredError`.
- **Repo code (this project, HIGH):**
  - `app/services/telegram.py` — `send_message_by_telegram_id` (:1082, peer-resolve ladder), `send_file` (:906, caption overflow + error mapping), client-per-op skeleton + Phase-20 profile methods.
  - `app/routers/conversations.py` — `POST /{id}/send` (:393 takeover ordering), `_load_conversation_or_404` (:67), `GET /{id}/messages` (:235), last_message LATERAL (:152).
  - `app/routers/senders.py:315` `_raise_profile_telegram_error` (error-mapping template), `:1165` photo multipart upload + `GET /senders/{slug}/photo` bytes `Response`.
  - `app/services/listener.py:812-916` incoming media detection; `:531` `save_message`; `:1321` outgoing handler.
  - `migrations/017_phase5.sql` — `messages` DDL + unique `(conversation_id, telegram_message_id)`.
  - `app/models/__init__.py:11` `MessageType` (belongs to `messages_log`); no `messages` ORM model.
  - `app/schemas/__init__.py:1084` `MessageResponse`, `:1101` `SendMessageFromUIRequest`.
  - `tests/conftest.py:76-249` migration application (hardcoded list + exists-guards); `tests/test_phase5_inbox_send_takeover.py` mock pattern.
  - `requirements.txt` (telethon 1.42.0, python-multipart 0.0.6, httpx 0.26.0); `.planning/config.json` (nyquist_validation=true).
- **CLAUDE.md** — migrations raw-SQL+idempotent+fail-fast; async-everywhere; test-overlay only; Telethon entity-cache cold-start note; ORM default vs server_default drift.

### Secondary (MEDIUM confidence)
- Telegram edit-window (~48h, server-controlled, has loosened) — training knowledge; treat as catch-and-map, verify via live-smoke.

### Tertiary (LOW confidence)
- None relied upon.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions read from `requirements.txt`; no new deps.
- Telethon method behaviour: HIGH — read from installed source (signatures, defaults, error classes, return types).
- Architecture / integration points: HIGH — grounded in the actual repo files this phase touches.
- Pitfalls: HIGH — each tied to a source line or a STATE.md/memory lesson.
- Edit-window numeric value: LOW/MEDIUM — server-controlled; mitigated by catch-and-map (no code dependence on the number).

**Research date:** 2026-07-07
**Valid until:** ~2026-08-07 (stable; re-verify only if Telethon is bumped past 1.42.x or the `messages` table gains an ORM model)
</content>
</invoke>
