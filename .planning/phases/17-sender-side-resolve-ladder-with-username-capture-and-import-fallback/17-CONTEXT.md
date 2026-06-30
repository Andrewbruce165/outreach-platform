# Phase 17: Sender-side resolve ladder with username capture and import fallback - Context

**Gathered:** 2026-06-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Перестроить резолв так, чтобы **отправитель сам резолвил и дотягивался** до получателя, а **чекер стал чистым фильтром «есть/нет» + захватом `@username`**. На отправителе — лестница резолва: кэш per-sender → `ResolveUsername` (по захваченному чекером @username) → `ImportContacts` (лениво, по одному перед отправкой). Чужой `access_hash` не переиспользуется (per-account, Telethon). Чинит структурный класс инцидента «Barter - ВЭД хук» (живые РФ-номера терминально падали на `ResolvePhone`).

**Вне scope (fixed boundary из ROADMAP + решено в обсуждении):**
- Очистка/purge `contacts_cache` (роудмап: «кэш не чистим») — вместо этого confidence-gated **чтение** (D-12).
- Перестройка checker-pool health-машинерии (сделано в Phase 14).
- Прогрев новых РФ-аккаунтов (операционный трек).
- Country-gate — **гипотеза, не факт**; в коде НЕ гейтим (D-10).
- Реквью инцидентных контактов — ops после деплоя, не в фазе (D-14).
- Control-loop / alerting / UI по block-rate (D-16).

</domain>

<decisions>
## Implementation Decisions

### Лестница резолва на отправителе
- **D-01:** Лестница: (1) кэш per-sender (access_hash) → (2) `ResolveUsername` по захваченному чекером @username → (3) `ImportContacts`. **Собственный `ResolvePhone` отправителя убирается полностью** — именно он давал ложные «нет» в инциденте (троттл/приватность). Совпадает с tier-списком ROADMAP (cache→username→import).
- **D-02:** Tier-3 = **Import-only** (без ResolvePhone на отправителе). `ImportContacts` — это и есть «phone-резолв фолбэк» из ROADMAP (дизайн-док: «Import is also a resolve»); дополнительно вытаскивает registered-но-приватные номера, которые ResolvePhone не видит.
- **D-03:** Import-гейт: `ImportContacts` пытаемся **только если чекер пометил `registered`**; иначе — skip (не тратим рискованный import на `not_registered`).
- **D-04:** Адресную книгу отправителя после import **НЕ чистим** (контакт остаётся — entity-cache горячий для фоллоу-апов). Принят рост книги при ~50/день; периодическая чистка — Deferred.
- **D-05:** Ленивый + размазанный import — **один import на одну отправку, прямо перед send** (никогда пачкой с утра). Лимит 4/мин сам размазывает под burst-онсет ~47–49. (Зафиксировано ROADMAP, переисполнение rate-логики не трогаем.)

### Чекер = чистый фильтр + захват @username
- **D-06:** Чекер **сохраняет `@username`** из ответа `ResolvePhone` (сейчас `resolve_phone_with_fallback` его выбрасывает — возвращает только `{is_registered, telegram_id}`). Username публичный/переносимый (в отличие от per-account access_hash) → даёт отправителю tier-2.
- **D-07:** Захваченный username хранится **durable на `contacts`** (отдельная колонка, напр. `tg_username_captured`) **+ в `contacts_cache`**. Переживает TTL кэша 7д. **НЕ затирает** пользовательский `contacts.username` из CSV (разная provenance).
- **D-08:** Вердикт чекера **не авторитетен по достижимости**; `access_hash` чекера никогда не переиспользуется отправителем (per-account, Telethon). (Locked дизайн-доком, переисполнение.)

### Протухший username
- **D-09:** `ResolveUsername` по захваченному username падает (`USERNAME_NOT_OCCUPIED`/`USERNAME_INVALID` — юзернейм сменён/удалён между check и send) → **фолбэк на import-tier** (D-03, если registered), **НИКОГДА не финализировать `not_registered`**. Сейчас `telegram.py::_resolve_username` кэширует `False` и выходит — это поведение надо поменять на fall-through.

### Country-gate (НЕ делаем — гипотеза)
- **D-10:** Country-gate **в этой фазе НЕ реализуем**. «US(+1)/cold аккаунт не резолвит RU(+79)» — **непроверенная гипотеза**: страна всегда была смешана с cold/throttle (ни одного чистого изоляционного теста). Память `project-us-senders-cannot-resolve-ru-phones` переклассифицирована в гипотезу (2026-06-30). Никакого странового гейта в коде. Проверка гипотезы (warmed+rested US vs warmed RU на тех же живых номерах в один момент) — Deferred.

### Доверие к вердикту + отравленный кэш
- **D-11:** Import-гейт **простой** — доверяет вердикту контакта как есть (`registered` → резолв/import; иначе skip). Без отдельной confidence-ветки на слое гейта.
- **D-12:** Чтение кэша **confidence-gated** — строка `is_registered=false` от suspect/low-confidence источника **НЕ отдаётся** из `contacts_cache` (ни чекеру, ни отправителю) → live-перерезолв. Кэш **никогда не удаляем** (ROADMAP «не чистим» соблюдён). Чинит cross-contamination без purge. `_lookup_cache` (checker.py:175) и `_get_cached_contact` (telegram.py) читаются ДО Telegram — это точки правки.
- **D-13 (композиция D-11+D-12):** confidence-интеллект живёт на **слое чтения кэша**, гейт остаётся «тупой-но-безопасный», т.к. suspect-негативы перерезолвятся живьём до того, как дойдут до гейта. **Принятый остаточный риск:** high-confidence-но-ложный `not_registered` от недетектированного country/cold всё ещё заблокирует живого лида — принято, т.к. country отложен (D-10), а измеримый throttle-кейс закрыт D-12.
- **D-14:** Инцидентные контакты (22 Barter-ВЭД `failed` + 176 Igor parked) — реквью/резет = **ops после деплоя**, не в фазе (зеркалит отложенный Phase 14 14k drain). Фаза строит механизм, не трогает конкретные инцидентные строки.

### Block/report-rate метрика
- **D-15:** Захват `USER_IS_BLOCKED` на send-пути (durable, per-sender) + read-only per-sender **block-rate** поверх захваченных блоков и существующих `sender_restriction_events` (Phase 10, activity slice). Репорты ненаблюдаемы; трекаем только наблюдаемое (block-on-send + restriction-агрегат как прокси накопленных репортов → PeerFlood).
- **D-16:** **Только хранить (read-only)** — НЕТ control-loop (auto-pause при высоком rate) в этой фазе. Alerting/auto-pause — Deferred (Phase 10 non-goal: real-time алерты отложены).

### Claude's Discretion
- Точная схема хранения confidence на слое кэша для D-12 (reuse `contacts_cache.source` + Phase 14 `contacts.tg_confidence`/`tg_resolved_by`/`tg_probe_state` vs новая колонка на `contacts_cache`).
- Имя колонки/миграция для захваченного username (D-07), идемпотентная по паттерну проекта.
- Где живут block-события (расширить `sender_restriction_events` новым `event_type` vs выделенная лёгкая таблица/счётчик) для D-15.
- Точный класс ошибки блока (проверить Telethon: `UserIsBlockedError`/`USER_IS_BLOCKED`) и форма выражения/эндпоинт rate (D-15).
- Конкретная механика confidence-порога «suspect» на чтении (что считать low-confidence: `source` подозрительного чекера / отсутствие clean-probe / Phase 14 `tg_confidence != high`).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Дизайн и решения (читать первым)
- `.planning/notes/sender-side-resolve-redesign.md` — **первичный дизайн-документ фазы**: триггер-инцидент, Telethon-факты (access_hash per-account, нет shortcut для cold phone), 3-tier лестница, username = cheap transferable top tier, established truths, open sub-decisions.
- `.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-CONTEXT.md` — этот файл (D-01..D-16).

### Механизм резолва и история (контекст «почему»)
- `.planning/notes/checker-problem-and-history.md` — методы Telegram (ResolvePhone/ImportContacts/ResolveUsername), как `is_registered=false` склеивает 4 причины (dead / privacy / checker-throttle / US-cold), что измерено vs гипотеза.
- `.planning/notes/checker-false-negatives.md` — диагноз false-negatives, два режима троттла (burst ~47–49 / shadow-ban), калибровка.
- `.planning/notes/checker-pool-throttle-spike.md` — обратимость троттла; @username-резолв МЁРТВ на затроттленных чекерах (не фолбэк); скорость = число чекеров + отдых.
- `.planning/debug/checker-fn-igor-base.md` — инцидент Igor (176 false-negatives), **корень cross-contamination кэша** (`_lookup_cache` читает workspace-wide cross-sender ДО Telegram → re-check бесполезен без правки чтения).

### Зависимость Phase 14 (надстраиваемся)
- `.planning/phases/14-reliable-contact-resolution/14-CONTEXT.md` — D-01 (управляемый пул), D-02 (resolvePhone+import fallback на чекере), D-09 (confidence/source, suspect → re-check не финал), D-11 (selection-гейт). **NB:** gap-closure 14-05/14-06 ещё не завершён — D-11/D-13 этой фазы вынесли confidence-обработку на слой чтения кэша (D-12), а не на гейт, чтобы не блокироваться незавершённым Phase 14.
- `.planning/ROADMAP.md` §«Phase 17» — fixed boundary (лестница cache→username→import, «кэш не чистим»).

### Требования
- `.planning/REQUIREMENTS.md` — RESV-01..07 (Phase 14, переиспользуемая семантика confidence). Requirements Phase 17 — **TBD, derive в /gsd:plan-phase 17**.

### Код, который правим/расширяем
- `app/services/checker.py` — `resolve_phone_with_fallback` (L69, **выбрасывает username** — D-06), `check_phones`/`_check_phones_locked`, `check_usernames` (`ResolveUsernameRequest`), `_save_cache` (L204), `_lookup_cache` (L175, **cross-sender чтение ДО Telegram** — D-12).
- `app/services/telegram.py` — `resolve_contact` (L494, лестница cache→ResolveUsername→ResolvePhone, **нет import-фолбэка** — D-01/D-02), `_resolve_username` (L558, **финализирует False на USERNAME_NOT_OCCUPIED** — D-09 fall-through), `send_message` (L639, точка захвата `USER_IS_BLOCKED` — D-15), `send_message_by_telegram_id` (L910), `_get_cached_contact`/`_save_contact_cache` (D-12).
- `app/services/contact_check_worker.py` — checker selection + finalization (Phase 14), точка применения confidence (D-12).
- `app/models/*.py` — `Contact` (`username`, `tg_status`, Phase 14 `tg_confidence`/`tg_resolved_by`/`tg_probe_state`; новая колонка captured username — D-07), `ContactsCache` (`is_registered`, `source`).
- `migrations/NNN_*.sql` — новая колонка captured username + (опц.) confidence на cache; идемпотентная, авто-применяется.

### Phase 10 (restriction audit — переиспользуем для D-15)
- `/root/CLAUDE.md` §«Restriction Audit (Phase 10)» — `sender_restriction_events` (append-only, activity slice). Источник агрегата для block-rate (D-15).
- `/root/CLAUDE.md` §«Семантика checker'а (is_registered)» — содержит country-as-fact формулировки; **смягчить до гипотезы** (doc-task под D-10).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `telegram.py::_resolve_username` (L558) — `ResolveUsernameRequest` путь уже реализован для username-ключей; tier-2 (D-01) строится на нём, нужен лишь вход «по захваченному username» + fall-through (D-09).
- `telegram.py::check_contact`/`ImportContactsRequest` блоки — готовые вызовы import; tier-3 (D-02) собирается из них (+ нет DeleteContacts по D-04).
- `checker.py::resolve_phone_with_fallback` — уже читает `result.users[0]` (полный User с `username`); D-06 = перестать выбрасывать `.username`.
- `sender_restriction_events` + Phase 10 helper `record_restriction_event` — инфраструктура для durable block-событий (D-15).
- Phase 14 `tg_confidence`/`tg_resolved_by`/`tg_probe_state` на `contacts` — основа «suspect» для confidence-gated чтения (D-12).

### Established Patterns
- Кэш-резолв: `contacts_cache` читается ДО Telegram, workspace-wide cross-sender (checker.py:344, telegram.py:442-456) — D-12 правит именно семантику чтения, не структуру.
- Миграции — raw SQL `NNN_short_name.sql`, идемпотентные, авто-применяются на старте api.
- Эмпирические rate-константы queue.py под защитой CLAUDE.md — D-05 опирается на существующий 4/мин, новых интервалов не вводит.
- `username`-контакты уже → `registered` при импорте (quick 260629-kn4) и резолвятся через ResolveUsername — captured-username (D-06) расширяет это на phone-контакты.

### Integration Points
- `contacts.tg_status` (`pending`/`registered`/`not_registered`) — вход import-гейта (D-03/D-11).
- `contacts_cache` (`is_registered`, `source`) + `contacts` confidence — точка confidence-gated чтения (D-12).
- send-путь `telegram.py::send_message` / queue worker — точка ленивого import (D-05) + захвата block-сигнала (D-15).
- Кампании читают `tg_status` для исключения `not_registered` — поведение финализации наследуется от Phase 14 D-09.

</code_context>

<specifics>
## Specific Ideas

- **Живой триггер (Barter - ВЭД хук):** 22 живых РФ-мобильных терминально упали на `ResolvePhone` несмотря на registered/high/clean. Чинится структурно: убрать собственный ResolvePhone отправителя (D-01) + import-фолбэк (D-02) + захват username (D-06).
- **«Import is also a resolve»** (дизайн-док truths): нет версии, где отправитель избегает per-recipient lookup для cold-номера — выбор только в *каком* lookup. Import-only (D-02) — осознанный выбор тяжёлой, но покрывающей приватность операции.
- **При ~50/день размазанно import НЕ «массовый импортёр»** (дизайн-док) — подпирает D-04 (keep) как приемлемое.
- **Доминантный убийца аккаунтов** — блоки/репорты получателей → PeerFlood → freeze, независимо от resolve vs import; метрика block-rate (D-15) трекает именно это (второго порядка к выбору резолва, но «метрика, которая реально важна»).
- **Country — конфаунд:** все наблюдавшиеся US-аккаунты были одновременно cold/throttled; «warmed beats cold», НЕ доказано «RU beats US» (D-10).

</specifics>

<deferred>
## Deferred Ideas

- **Проверка гипотезы country-gate** (D-10) — чистый изоляционный тест: warmed+rested US(+1) vs warmed RU на тех же живых номерах в один момент. Только дивергенция там изолирует страну.
- **Реквью инцидентных контактов** (D-14) — 22 Barter-ВЭД `failed` + 176 Igor parked → ops после деплоя механизма.
- **Периодическая чистка адресной книги отправителей** (следствие D-04 keep) — может стать ops-задачей при долгом росте книги.
- **Block-rate alerting / auto-pause control-loop / вывод в UI/analytics** (D-16) — отдельная observability-фаза (Phase 10 non-goal: real-time алерты отложены).
- **Purge отравленного `contacts_cache`** — вне scope (ROADMAP «не чистим»); D-12 решает контаминацию без удаления.
- **block/report-rate как полноценная метрика с дашбордом** (рассмотрена, не взята) — read-only захват сейчас (D-15), агрегация/вывод позже.

</deferred>

---

*Phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback*
*Context gathered: 2026-06-30*
