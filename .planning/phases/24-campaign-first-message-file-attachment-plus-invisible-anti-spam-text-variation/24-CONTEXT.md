# Phase 24: Campaign first-message file attachment plus invisible anti-spam text variation - Context

**Gathered:** 2026-07-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Две возможности поверх **опенера кампании** — очередного первого сообщения на пути
`campaign_enqueue.py → message_queue → queue.py worker`:

1. **Файл к первому сообщению.** К кампании можно прикрепить **ровно один** файл; он уходит
   получателю **одним media-сообщением** с текстом опенера в качестве caption (авто-медиа).
2. **Невидимая анти-спам вариация текста.** Каждый исходящий опенер делается
   **байт-уникальным** (обход Telegram-эвристики «N одинаковых массовых сообщений = спам»)
   **без изменения того, что читает получатель**.

**Вне scope:**
- Ручная отправка файла из inbox (это Phase 23).
- Вариация AI-ответов (LLM и так генерит уникальный текст).
- Вариация follow-up-сообщений кампании (Phase 19) — deferred.
- Несколько вложений / альбом (media-group) — deferred.
</domain>

<decisions>
## Implementation Decisions

### A. Прикреплённый файл — хранение и модель
- **D-01:** Ровно **один** файл на кампанию (не альбом). Несколько вложений — deferred.
- **D-02:** Байты хранятся **DB-blob** (PostgreSQL `LargeBinary`) по образцу
  `csv_imports.file_data`. Плюсы: попадает в `pg_dump`-бэкап, workspace-scope бесплатно, нет
  отдельного persistent volume и синхронизации api↔listener. Worker тянет байты из БД →
  temp-файл → Telethon; temp удаляется после отправки (`finally`).
- **D-03:** Лимит размера **~50 МБ** (как Phase 23 D-10), типы **любые**; превышение при
  загрузке → `FILE_TOO_LARGE`.
- **D-04:** Точная физическая модель (отдельная таблица `campaign_attachments` 1-к-1 vs
  blob-колонки прямо в `campaigns`) — на усмотрение планировщика. **Рекомендация:** отдельная
  таблица/1-1, чтобы blob не тянулся в каждый `SELECT` по `campaigns`. ⚠ Учесть ORM
  `default=` vs `server_default=` drift для любых новых NOT NULL колонок (см. память
  `project-orm-default-vs-server-default-drift`): задавать и `server_default`, и ORM-значение.

### B. Доставка файла (опенер)
- **D-05:** Файл + текст уходят **одним сообщением**: media с `caption` = отрендеренный
  опенер. Одна queue-строка `item_type='file'`, `caption` несёт текст, `message_text` пуст
  или дублирует чистый текст (на усмотрение — но источник истины для caption — единый).
- **D-06:** **Авто-медиа — `force_document=False`** (фото приходит фото, видео — видео,
  прочее — документом). Это отличается от текущего queue `send_file` (там
  `force_document=True`), но соответствует Phase 23 D-11. **NB:** generic file-queue путь
  (`enqueue_file` / `item_type='file'`) сейчас **не имеет вызывающих вне `queue.py`** —
  опенер кампании фактически **первый потребитель** `item_type='file'`, поэтому смена
  поведения безопасна. Всё равно не менять дефолт `send_file` вслепую — добавить
  параметр/флаг авто-медиа, а не переопределять сигнатуру.
- **D-07:** `caption` > 1024 симв → **переиспользовать существующий overflow-паттерн**
  `send_file`: файл без caption + полный текст follow-up отдельным сообщением. Ничего не
  теряется.
- **D-08:** Источник байтов для отправки опенера — **DB-blob → temp** (не URL-скачивание).
  Существующий URL-путь в `send_file` не удалять; добавить blob-источник совместимо.

### C. Невидимая анти-спам вариация — техника
- **D-09:** **Комбо:** zero-width вставки (U+200B ZWSP, U+200C ZWNJ, U+2060 WORD JOINER) как
  основа + **изредка** near-invisible джиттер (NBSP `U+00A0` / тонкий пробел вместо обычного
  пробела). **Гомоглифы отвергнуты** (ломают copy-paste/поиск; mixed-script кириллица↔латиница
  сам по себе может быть spam-сигналом Telegram).
- **D-10:** Инвариант «невидимо» — **допускаем near-invisible** (NBSP/тонкий пробел), а не
  только истинно нулевую ширину. Осознанный компромисс ради энтропии и устойчивости к
  возможной нормализации.
- **D-11:** **Принятый риск:** если Telegram нормализует zero-width/пробелы перед сравнением
  на дубли, эффект слабее (но вреда нет). Задокументировать как принятый риск; не давать
  гарантий deliverability на основе одной этой меры.

### D. Вариация — управление
- **D-12:** Область — **только опенер кампании** (включая overflow-follow-up как часть
  опенера). AI-ответы уже уникальны; follow-up Phase 19 — вне scope (deferred).
- **D-13:** Переключатель — **per-campaign флаг, default ВКЛ** (`server_default=true`).
  ⚠ Меняет поведение существующих кампаний — учесть при миграции. API-bounds как у прочих
  campaign-полей (Literal/валидация на уровне API), DB CHECK не обязателен.
- **D-14:** Момент применения — **при отправке в worker**: варьируется **копия** текста
  прямо перед Telethon-вызовом. `message_queue.message_text`/`caption` и запись в `messages`
  остаются **чистыми** (без невидимых символов) → inbox/логи читаемы, `rerender_pending_queue`
  не затрагивается, каждая отправка свеже-уникальна. Вариация применяется к тексту опенера
  (и к caption, и к overflow-тексту).
- **D-15:** Интенсивность — **фикс дефолт («зелёный коридор»)**, зашита в код (ориентир:
  1–3 вставки на ~10 слов, с верхним капом), **не настраивается** клиентом в v1.
- **D-16:** Каждая отправка **уникальна**: вариация генерится заново на каждый send
  (per-item рандом; **не** переиспользовать один сид между контактами).

### E. Взаимодействие с очередью / rerender
- **D-17:** Опенер-с-файлом = queue-строка `item_type='file'` с `caption`.
  `rerender_pending_queue` сейчас апдейтит `message_text` **только** для `item_type='message'`
  — **расширить**, чтобы правка `campaign.message_template` также перерендеривала `caption`
  у pending `item_type='file'` строк той же кампании (иначе правка текста опенера не дойдёт
  до уже стоящих в очереди file-строк).
- **D-18:** Опенер-с-файлом считается **одним** новым диалогом / **одной** отправкой (один
  queue item → один rate-limit тик → один new-dialog cap) — как обычный текст-опенер.
  Лимиты/cap не менять.

### F. API / frontend
- **D-19:** Загрузка файла — **отдельный multipart-эндпоинт** (напр.
  `POST /campaigns/{id}/attachment`, `DELETE` для снятия), т.к. create/patch кампании — JSON
  и не несёт байты чисто. **Варьируемый флаг** — часть JSON-схемы кампании (create/patch).
  Точные имена/формы — планировщик. Обновить `lovable-handoff/openapi.json` +
  `error-codes.md` (`FILE_TOO_LARGE` и пр.). NB: Lovable может слать нестандартные имена
  полей — закладывать толерантность к алиасам (как `SendMessageFromUIRequest`).
- **D-20:** `duplicate_campaign` должен копировать **и** attachment, **и** variation-флаг.

### Claude's Discretion
- Точная модель хранения (отдельная таблица vs колонки), имена колонок/эндпоинтов/схем.
- Точный алфавит zero-width символов и алгоритм вставки (позиции/частота); реализация
  «зелёного коридора» плотности.
- Как разграничить авто-медиа для опенера vs generic `send_file` (доп. параметр/флаг).
- Реализация лимита 50 МБ (`Content-Length` vs стриминг в temp с ранним обрывом).
- Логировать ли применённую вариацию (для отладки).
- Тесты «невидимости»: `strip(zero-width + NBSP + тонкий пробел) == оригинал`; два прогона
  на одном тексте дают разные байты.

### Folded Todos
Нет — `todo match-phase 24` вернул 0 совпадений.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

В ROADMAP.md для Phase 24 canonical refs не заданы. Ниже — код и доки, определяющие паттерны
и точки интеграции для этой фазы.

### Опенер / очередь / отправка (переиспользовать)
- `app/services/campaign_enqueue.py` — enqueue опенера (`render_template` → INSERT
  `message_queue` `item_type='message'`); `rerender_pending_queue` (снапшот при enqueue,
  D-17 расширить на file-строки).
- `app/services/queue.py` — worker: ветка `item_type == QueueItemType.file` →
  `telegram_service.send_file(...)` (строки ~881–891) vs `send_message`; здесь применяется
  вариация при отправке (D-14) и меняется источник файла (D-08).
- `app/services/telegram.py` — `send_file()` (~906+): текущий URL-скачивание + caption-overflow
  (1024→follow-up, D-07) + `force_document=True` (D-06 меняем на авто-медиа); `send_message()`.
- `app/services/template.py` — `render_template()` (Mustache-подстановка {{переменных}});
  вариация применяется **после** render, к готовому тексту.

### Модель данных
- `app/models/__init__.py` — `MessageQueue` (уже есть `file_url`/`file_name`/`caption`/
  `item_type`), `Campaign` (`message_template` и прочие поля — куда добавить variation-флаг +
  ссылку на attachment), `CsvImport.file_data` (образец DB-blob хранения, D-02),
  `QueueItemType`, `MessageType`.
- `migrations/` — последняя `052_*.sql`; новые миграции `053+`, идемпотентные (авто-applier
  fail-fast). Phase 23 добавит `message_type`/медиа-мета в `messages` — согласовать нумерацию.

### API / роутер / фронт
- `app/routers/campaigns.py` — `create_campaign` (POST ""), `patch_campaign` (PATCH /{id}),
  `duplicate_campaign` (D-20), `rerender_pending` endpoint; сюда добавляется attachment-эндпоинт
  (D-19). Все JSON — нужен отдельный multipart для файла.
- `lovable-handoff/openapi.json` — источник правды API для генерации фронта (обновить).
- `lovable-handoff/error-codes.md` — реестр кодов ошибок (обновить: `FILE_TOO_LARGE` и пр.).

### Смежные фазы (границы scope)
- `.planning/phases/23-.../23-CONTEXT.md` — file-send паттерны, `messages` медиа-схема
  (`message_type`, медиа-мета), прецедент `force_document=False` (D-11 Phase 23).
- `.planning/phases/19-.../19-CONTEXT.md` — no-reply follow-up (вариация follow-up — deferred).

### Правила проекта
- `CLAUDE.md` (`/root/apps/aimly/tg-outreach/CLAUDE.md`) — миграции только raw SQL +
  идемпотентность + авто-applier fail-fast; async everywhere; **очередь/rate-limits не трогать
  без обсуждения**; тесты только через test-overlay.
- `.planning/codebase/STRUCTURE.md` — двух-репозиторная топология (backend vs Lovable-фронт).
- `.planning/codebase/INTEGRATIONS.md` — точки интеграции backend↔Telethon↔фронт.

### Память (учесть при реализации)
- `queue-snapshots-template-at-enqueue` — очередь снапшотит отрендеренный опенер при enqueue;
  вариация при отправке (D-14) специально НЕ трогает снапшот.
- `project-orm-default-vs-server-default-drift` — новые NOT NULL колонки: и `server_default`,
  и ORM-значение (D-04, D-13).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`MessageQueue` уже несёт `file_url`/`file_name`/`caption`/`item_type`** — DDL под file-опенер
  почти готов; добавить только источник-blob и variation-флаг на кампании.
- **`send_file()` (telegram.py)** — готовый caption-overflow (1024→follow-up, D-07) и полный
  error-маппинг (FloodWait/PeerFlood/UserIsBlocked/frozen). Переиспользовать логику; изменить
  источник (URL→DB-blob, D-08) и `force_document` (D-06).
- **`CsvImport.file_data` (LargeBinary)** — рабочий образец DB-blob + `expires_at`-паттерн
  (D-02).
- **`render_template()` (template.py)** — рендер опенера; вариация навешивается пост-рендер.
- **`rerender_pending_queue()`** — существующий count-guarded UPDATE pending-строк; расширить
  на `item_type='file'` caption (D-17).

### Established Patterns
- Очередь снапшотит текст при enqueue; правки шаблона доходят до pending только через
  `rerender_pending_queue` (вызывается из PATCH и `/rerender-pending`).
- Клиент-на-операцию: `get_client(...)` → операция → `disconnect_client()`; temp-файл в
  `finally`.
- Ошибки Telethon → структурированные `{code, message}` (не пробрасывать raw).
- Новые NOT NULL колонки: `server_default` + ORM-значение (drift-память).

### Integration Points
- **`item_type='file'` в очереди — фактически новый живой путь:** `enqueue_file` /
  `item_type='file'` НЕ имеют вызывающих вне `queue.py` (grep подтвердил) → опенер кампании
  первый потребитель, смена на авто-медиа безопасна.
- Enqueue опенера-с-файлом: `campaign_enqueue.py` создаёт `item_type='file'` строку (сейчас
  всегда `'message'`) когда у кампании есть attachment.
- Вариация: точка применения — worker `queue.py` прямо перед `send_message`/`send_file`.
- Миграции — `migrations/053+_*.sql` (авто-applier при старте api).
- Деплой: `docker compose up -d --build api` (+ `listener`, если трогаем listener; для этой
  фазы — вероятно только api + worker в api-контейнере).

</code_context>

<specifics>
## Specific Ideas

- Файл+текст должны ощущаться как обычное живое первое сообщение (фото приходит фото, одним
  сообщением) — отсюда авто-медиа + single-message.
- Вариация — «невидимый watermark»: получатель не должен замечать разницы; в inbox и логах
  хранится **чистый** текст (без невидимых символов), вариация живёт только в исходящем байте.

</specifics>

<deferred>
## Deferred Ideas

- **Несколько вложений / альбом** (media-group) — v1 = ровно один файл (D-01).
- **Вариация follow-up-сообщений** (Phase 19) и **AI-ответов** — вне scope (D-12); при росте
  анти-спам-требований пересмотреть.
- **Настраиваемая интенсивность вариации** (low/med/high) — v1 фикс дефолт (D-15).
- **Устойчивость к нормализации Telegram** — если zero-width+джиттер окажется недостаточным
  против дедупа Telegram, пересмотреть технику (напр. видимые near-synonym варианты — но это
  уже НЕ «невидимо»). Принятый риск D-11.
- **Постоянное объектное хранилище медиа** — v1 = DB-blob (D-02); при росте объёма пересмотреть.

### Reviewed Todos (not folded)
Нет — `todo match-phase 24` вернул 0 совпадений.

</deferred>

---

*Phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation*
*Context gathered: 2026-07-07*
