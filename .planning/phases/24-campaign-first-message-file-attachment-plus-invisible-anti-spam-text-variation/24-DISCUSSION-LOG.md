# Phase 24: Campaign first-message file attachment plus invisible anti-spam text variation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-07
**Phase:** 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
**Areas discussed:** Хранение файла, Доставка файла, Техника вариации, Управление вариацией

---

## Хранение файла

| Option | Description | Selected |
|--------|-------------|----------|
| DB-blob (LargeBinary) | Байты в PostgreSQL как csv_imports; в pg_dump, workspace-scope, worker тянет в temp | ✓ |
| Диск-volume | Файл на ФС контейнера, в БД только путь; нужен persistent volume + sync api↔listener | |
| Внешний URL | Клиент даёт ссылку, worker качает при каждой отправке; зависимость от внешнего хоста | |

**User's choice:** DB-blob (LargeBinary)

| Option | Description | Selected |
|--------|-------------|----------|
| Ровно один | Одно вложение на кампанию, одна media-отправка с caption | ✓ |
| Несколько (альбом) | media-group, несколько queue-строк/JSONB; сложнее | |

**User's choice:** Ровно один

| Option | Description | Selected |
|--------|-------------|----------|
| ~50 МБ, любые типы | Как Phase 23 inbox (D-10); превышение → FILE_TOO_LARGE | ✓ |
| Меньше (10–20 МБ) | Жёстче для DB-blob + сотни отправок | |
| Только изображения | jpg/png/webp; сужает сценарии (нет PDF/прайсов) | |

**User's choice:** ~50 МБ, любые типы

---

## Доставка файла

| Option | Description | Selected |
|--------|-------------|----------|
| Одним сообщением (media+caption) | Фото/файл с подписью = опенер; одна отправка, меньше следов для антиспама | ✓ |
| Два сообщения (файл + текст) | Файл + отдельный текст; гибче по длине, но два сообщения на старте = больше риска | |

**User's choice:** Одним сообщением (media+caption)

| Option | Description | Selected |
|--------|-------------|----------|
| Авто-медиа (force_document=False) | Фото приходит фото; как Phase 23 D-11 | ✓ |
| Всегда документом (force_document=True) | Как текущий queue send_file; менее «живо» | |

**User's choice:** Авто-медиа (force_document=False)

| Option | Description | Selected |
|--------|-------------|----------|
| Follow-up текстом (как send_file) | Файл без caption + полный текст отдельно; ничего не теряется | ✓ |
| Валидация на вводе (запретить >1024) | Жёстко; длина плавает от переменных | |

**User's choice:** Follow-up текстом (как send_file)

---

## Техника вариации

| Option | Description | Selected |
|--------|-------------|----------|
| Zero-width вставки | U+200B/200C/2060 на границах слов; истинно невидимо, безопасно | |
| Комбо: zero-width + джиттер | Zero-width + изредка NBSP/тонкий пробел; больше энтропии/устойчивости | ✓ |
| Гомоглифы | Кириллица↔латиница-двойники; ломает copy-paste, mixed-script = spam-сигнал | |

**User's choice:** Комбо: zero-width + джиттер

| Option | Description | Selected |
|--------|-------------|----------|
| Только истинно невидимые | Гарантия отсутствия артефактов; ограничивает до zero-width | |
| Допускаем near-invisible | NBSP/тонкий пробел ради энтропии, ценой микрошанса заметности | ✓ |

**User's choice:** Допускаем near-invisible

---

## Управление вариацией

| Option | Description | Selected |
|--------|-------------|----------|
| Только опенер кампании | Первое сообщение (вкл. overflow); AI/follow-up вне scope | ✓ |
| Опенер + follow-up кампании | Также Phase 19 follow-up; шире, но выходит за «first-message» | |
| Опенер + AI-ответы | Всё исходящее; избыточно (AI уникален) + трогает listener/AI | |

**User's choice:** Только опенер кампании

| Option | Description | Selected |
|--------|-------------|----------|
| Per-campaign, default вкл | Флаг на кампании, server_default=true; меняет поведение существующих | ✓ |
| Per-campaign, default выкл (opt-in) | Существующие не меняются; клиент включает | |
| Глобально всегда вкл | Без переключателя; нет аварийного выключения | |

**User's choice:** Per-campaign, default вкл

| Option | Description | Selected |
|--------|-------------|----------|
| При отправке (worker) | Варьируется копия перед Telethon; queue/messages чистые; rerender не затронут | ✓ |
| При enqueue (снапшот) | Вшивается в message_text; rerender должен переприменять; невидимые символы в inbox | |

**User's choice:** При отправке (worker)

| Option | Description | Selected |
|--------|-------------|----------|
| Фикс дефолт (зелёный коридор) | Разумная плотность зашита в код; не настраивается | ✓ |
| Настраиваемая | Клиент регулирует low/med/high; больше UI и шанс навредить | |

**User's choice:** Фикс дефолт (зелёный коридор)

---

## Дополнительные зоны (предложены, но не углублялись)

Пользователь отказался от углубления в: API-форму загрузки файла, разграничение с queue
`send_file`, отображение опенера в inbox, верификацию «невидимости». Ответ: «все ок, давай
контекст делать». Эти зоны зафиксированы в CONTEXT.md как решения по умолчанию (D-19, D-20,
E-раздел) + Claude's Discretion, а не оставлены открытыми.

## Claude's Discretion

- Модель хранения (таблица vs колонки), имена колонок/эндпоинтов/схем.
- Алфавит zero-width и алгоритм вставки; реализация плотности «зелёного коридора».
- Разграничение авто-медиа опенера vs generic send_file.
- Реализация лимита 50 МБ.
- Логирование применённой вариации.
- Тесты невидимости и уникальности.

## Deferred Ideas

- Несколько вложений / альбом.
- Вариация follow-up (Phase 19) и AI-ответов.
- Настраиваемая интенсивность вариации.
- Устойчивость к нормализации Telegram (принятый риск).
- Постоянное объектное хранилище медиа.
