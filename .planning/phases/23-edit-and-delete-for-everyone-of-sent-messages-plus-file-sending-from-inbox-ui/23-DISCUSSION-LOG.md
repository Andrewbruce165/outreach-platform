# Phase 23: Edit and delete-for-everyone of sent messages plus file sending from inbox UI - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-07
**Phase:** 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
**Areas discussed:** Удаление у всех, Редактирование, Отправка файла, Модель записи и рендер, Ошибки, Форма API, Входящие медиа

---

## Область выбора (present_gray_areas)

Пользователь выбрал все 4 предложенные области: Удаление у всех, Редактирование, Отправка файла, Модель записи и рендер.

---

## Удаление у всех

| Option | Description | Selected |
|--------|-------------|----------|
| Только наши | Удалять только наши исходящие (sent_by ai/human). Telegram гарантированно разрешает revoke своих. | ✓ |
| Наши + контакта | Также удалять сообщения контакта (в приватных чатах Telegram позволяет). | |

**User's choice:** Только наши

| Option | Description | Selected |
|--------|-------------|----------|
| Tombstone | Оставить строку, deleted_at, плашка «удалено». | |
| Жёсткое удаление | Удалить строку из messages совсем. | ✓ |

**User's choice:** Жёсткое удаление
**Notes:** Значит, `deleted_at` не нужен; превью last_message пересчитывается из следующего сообщения.

---

## Редактирование

| Option | Description | Selected |
|--------|-------------|----------|
| Только текст | Редактировать текстовые сообщения; подписи к файлам — позже. | ✓ |
| Текст + подписи | Также подписи к отправленным файлам. | |

**User's choice:** Только текст

| Option | Description | Selected |
|--------|-------------|----------|
| На месте + метка | Обновить message_text, выставить edited_at, «(изменено)». Без истории. | ✓ |
| След правок | Хранить прежние версии текста. | |

**User's choice:** На месте + метка

---

## Takeover (правка/удаление)

| Option | Description | Selected |
|--------|-------------|----------|
| Нет | Правка/удаление НЕ трогают ai_enabled/status/очередь. | ✓ |
| Да, как /send | Любое вмешательство → manual + гасит очередь. | |

**User's choice:** Нет
**Notes:** Производное решение — отправка файла (новое исходящее) НЕ подпадает под это «нет»: файл-send делает takeover как /send (D-12).

---

## Отправка файла

| Option | Description | Selected |
|--------|-------------|----------|
| Multipart upload | Менеджер грузит локальный файл прямо в API. | ✓ |
| Готовый URL | Как текущий send_file (качает по URL). | |
| Оба | Принимать и upload, и URL. | |

**User's choice:** Multipart upload

| Option | Description | Selected |
|--------|-------------|----------|
| Авто-медиа | Фото/видео инлайн, остальное документом. | ✓ |
| Всегда документ | force_document, как текущий send_file. | |

**User's choice:** Авто-медиа

| Option | Description | Selected |
|--------|-------------|----------|
| Лимит ~50МБ, любой тип | Отклонять >капа (FILE_TOO_LARGE), типы любые. | ✓ |
| Без явного лимита | Полагаться на лимиты Telegram/сервера. | |
| Whitelist типов + лимит | Только разрешённые MIME + лимит. | |

**User's choice:** Лимит ~50МБ, любой тип

---

## Модель БД

| Option | Description | Selected |
|--------|-------------|----------|
| Добавить колонки | message_type + медиа-поля + edited_at; message_text → nullable; без deleted_at. | ✓ |
| Минимум | Только строго необходимое. | |

**User's choice:** Добавить колонки

---

## Ошибки

| Option | Description | Selected |
|--------|-------------|----------|
| Коды ошибок + тост | Структурированные коды (MESSAGE_EDIT_TOO_OLD, DELETE_FAILED, FILE_TOO_LARGE...), тост в UI, откат оптимистичного UI. Обновить error-codes.md. | ✓ |
| Один generic error | Один общий код на всё. | |

**User's choice:** Коды ошибок + тост

---

## Форма endpoint'ов

| Option | Description | Selected |
|--------|-------------|----------|
| REST по message_id | PATCH/DELETE /conversations/{id}/messages/{mid}, POST /conversations/{id}/send-file. | ✓ |
| Действия-эндпоинты | POST .../messages/{mid}/edit, .../delete. | |

**User's choice:** REST по message_id

---

## Входящие медиа от контакта

| Option | Description | Selected |
|--------|-------------|----------|
| Вне scope | Листенер пишет только текст входящих; входящие медиа — отдельная работа. | |
| Включить входящие медиа | Расширить листенер, входящие файлы как file-баблы. | ✓ |

**User's choice:** Включить входящие медиа (расширяет scope фазы)

| Option | Description | Selected |
|--------|-------------|----------|
| Метаданные + скачать по запросу | file-баббл с именем/типом сразу; байты из Telegram on-demand. | ✓ |
| Скачивать сразу на диск | Листенер качает на диск, отдаёт по URL. | |
| Только плейсхолдер | «📎 файл: name» без скачивания. | |

**User's choice:** Метаданные + скачать по запросу

---

## Claude's Discretion

- Точные имена медиа-колонок и message_type enum-значений.
- Реализация лимита 50 МБ (Content-Length vs стриминг с ранним обрывом).
- Форма endpoint'а скачивания входящего файла + заголовки стрима.
- Маппинг Telethon-исключений → коды ошибок.

## Deferred Ideas

- Синхронизация правок/удалений от контакта (MessageEdited/MessageDeleted в листенере).
- Редактирование подписей к отправленным файлам.
- Массовые операции над сообщениями.
- Постоянное объектное хранилище медиа (сейчас lazy on-demand).
