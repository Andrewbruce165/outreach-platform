# Phase 14: Reliable Contact Resolution - Context

**Gathered:** 2026-06-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Сделать проверку контактов (`phone → есть ли в Telegram`) надёжной и масштабируемой, чтобы кампании доставали всех достижимых лидов, а не сливали их молча из-за деградировавшего чекера.

Требования RESV-01..07 (см. REQUIREMENTS.md) зафиксированы. Фаза реализует: health-probe на контролях, burst-кап + cooldown, пул чекеров с ротацией, перепроверку контаминированных данных, фикс дыры в selection воркера, confidence/source на `not_registered`, обновление docs.

**Вне scope:** генеральная логика «как кампания реагирует на статусы контакта» сверх правила финализации (см. D-09); UI-визуализация статуса резолва/здоровья пула; observability/алерты — отдельные фазы при необходимости.

</domain>

<decisions>
## Implementation Decisions

### Модель резолва (главная развилка)
- **D-01:** Управляемый пул чекеров — отдельный `contact_check_worker` резолвит контакты батчами через пул выделенных checker-аккаунтов с probe + кап + ротацией. НЕ ленивый резолв при отправке. Даёт предварительное знание «сколько живых» до запуска кампании. (RESV-02/03)
- **D-02:** Метод резолва — `resolvePhone` с **fallback на `importContacts`**, если `resolvePhone` пуст. Дублирует поведение «здоровых сендеров» из диагностического теста ради максимального покрытия. **Заметка:** `importContacts` пишет в адресную книгу чекера — после прогона/периодически адресную книгу чекеров надо чистить (`contacts.DeleteContactsRequest` или аналог), чтобы не копить мусор и не менять поведенческий профиль.

### Пул чекеров
- **D-03:** Пул из **2–3 выделенных** checker-аккаунтов (`role='checker'`), не участвующих в отправке — throttle бьёт только по резолву, не по кампаниям. **Зависимость:** сейчас активных чекеров **0** — аккаунты нужно онбордить/провизионить перед запуском фазы (см. Deferred / open item).
- **D-04:** Ротация (RESV-03) учитывает `restriction_status`, `restricted_until`, `lifecycle_status` и даёт аккаунтам отдых. Дизайн пула/ротации должен работать и при N=1 (один доступный чекер → проверка встаёт на cooldown, не врёт).

### Health-probe (детект троттла)
- **D-05:** Триггер деградации — **≥2 промаха подряд** по контрольному набору заведомо-живых (отсекает стохастический шум; калибровка показала 48/49 live, единичный промах бывает шумом). (RESV-01)
- **D-06:** При детекте — чекер помечается `restriction_status='spam_limited'`, пишется событие в `sender_restriction_events`, выводится из ротации на cooldown.
- **D-07:** Suspect-пачка — **все `not_registered` текущей пачки откатываются в `pending`** (не финализируются), перечекнутся другим чекером. `registered` (true positives) остаются — ложноположительных у throttle не бывает. Батч ≤ ~30 + проба контролей каждый батч ⇒ «текущая пачка» фактически = окно с последней чистой пробы.

### Перечек контаминированных + приоритет
- **D-08:** Перечек 2110+699 контаминированных + 14k pending — через **обычную очередь того же пула**, без отдельного backfill-скрипта. Приоритет — **мобильные первыми** (+79…, ~50% живых) перед стационарными (+73/+74/+78, корректно отсутствуют). (RESV-04)

### Confidence / финализация
- **D-09:** `not_registered` несёт confidence/source — каким чекером и когда получен. (RESV-06) Кампания трактует `not_registered` как **финальный (пропускает контакт) только если он получен от чекера с чистой пробой (high confidence)**. Низкий confidence / результат подозрительного чекера → перечек, никогда не финал. Это закрывает корневой баг (ложные «нет» сливали лиды).

### Burst-кап (env-knobs)
- **D-10:** Per-account burst-кап ≤ ~30 резолвов/пачку (под эмпирическим онсетом ~45–50), темп 2–3с между резолвами, cooldown между пачками, дневной кап. Все значения — env-knobs по паттерну `CONTACT_CHECK_*`, калибруемы (точные числа — на усмотрение planner'а в пределах этих границ). (RESV-02)

### Фикс корневой дыры
- **D-11:** `contact_check_worker` selection пропускает чекеры с `restriction_status != 'none'` **ИЛИ** `lifecycle_status='paused'` (сейчас фильтрует только `role='checker' AND auth_status='ok'` — эта дыра позволила битому чекеру продолжать врать). (RESV-05)

### Claude's Discretion
- Точные значения env-knobs `CONTACT_CHECK_*` (кап, темп, cooldown, дневной лимит) в границах D-10.
- Частота интерливинга проб контролей внутри батча и размер пробы (в логике D-05/D-07: проба каждый батч).
- Схема хранения confidence/source (новые колонки `contacts_cache` / `contacts` vs JSONB) — реализация D-09; `source` уже есть в `contacts_cache`.
- Механика cooldown/восстановления чекера (когда возвращать в ротацию после spam_limited).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Диагноз и калибровка (читать первым)
- `.planning/notes/checker-false-negatives.md` — полный диагноз false-negatives, доказательства, два режима троттла (мягкий burst ~45–50 / жёсткий shadow-ban), калибровка онсета, что уже выполнено вручную в Части 1.
- `.planning/phases/14-reliable-contact-resolution/control-set-known-live.txt` — контрольный набор 49 заведомо-живых номеров (registered из папки «Barter», `folder_id 4ecdde17-f454-4a1b-b4ba-732fd6b9449f`) для health-probe.

### Требования
- `.planning/REQUIREMENTS.md` — RESV-01..RESV-07 (строки ~164–170).
- `.planning/ROADMAP.md` §«Phase 14» — Success Criteria (5 пунктов).

### Код, который правим/расширяем
- `app/services/contact_check_worker.py` — selection JOIN LATERAL `role='checker' AND auth_status='ok'` (дыра RESV-05); `FOR UPDATE OF c SKIP LOCKED` + `tg_checked_at` claim-окно (паттерн для масштабирования пула).
- `app/services/checker.py` — `check_phones`/`_check_phones_locked` (`resolvePhone` + `PhoneNotOccupiedError` семантика), `check_usernames` (`ResolveUsernameRequest`), `_save_cache` (пишет `is_registered`, `source`).
- `app/models/*.py` — модель `ContactsCache` (`source`, `is_registered`); поля для confidence/source RESV-06.

### Phase 10 (restriction audit — переиспользуем)
- Раздел «Restriction Audit (Phase 10)» в `CLAUDE.md` проекта — `sender_restriction_events` (append-only лог, миграции 030/031). Сюда пишем события throttle из health-probe (D-06).

### Docs для правки (RESV-07)
- `/root/CLAUDE.md` §«Семантика checker'а (is_registered)» — сейчас утверждает, что checker здоров на 2026-06-23; обновить под диагноз false-negatives.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `contact_check_worker._tick()` — батч-резолв с `FOR UPDATE OF c SKIP LOCKED` + `tg_checked_at` claim уже безопасен при горизонтальном масштабе → пул из 2–3 чекеров ложится сюда.
- `checker.py` методы (`resolvePhone` + `importContacts`, `ResolveUsername`) — fallback D-02 строится на готовых вызовах.
- `sender_restriction_events` + `restriction_status`/`restricted_until` (Phase 10) — готовая инфраструктура для D-04/D-06 (ротация по статусу + лог событий).
- `senders.role='checker'` + lifecycle/restriction поля — основа выделенного пула.

### Established Patterns
- Selection воркера фильтрует чекеры одним WHERE — добавление `restriction_status`/`lifecycle_status` (D-11) минимально-инвазивно.
- Env-knobs через `app/config.py` (паттерн `CONTACT_CHECK_*`) — для D-10.
- Миграции — raw SQL `NNN_short_name.sql`, идемпотентные, авто-применяются на старте api.

### Integration Points
- `contacts_cache` / `contacts` статусы (`pending`/`registered`/`not_registered`) — точка записи confidence/source (D-09).
- Кампании читают статус контакта для исключения `not_registered` — точка применения правила финализации (D-09).

</code_context>

<specifics>
## Specific Ideas

- Часть 1 уже выполнена вручную (2026-06-26): битый чекер `sender-8428118140` на паузе (`auth_status='restricted'`, `restriction_status='spam_limited'`, `lifecycle_status='paused'`, `restricted_until='2030-01-01'`), удалено 2216 ложных строк `contacts_cache`, 2110 контактов → `pending`. Итог: 14489 pending, 53 registered, 0 not_registered, 0 активных чекеров. Откат битого чекера — см. note.
- Истинная доля живых: ~26% в целом, ~50%+ среди мобильных (битый чекер давал 2.5%). По 14489 контактам реально достижимых ~4000+.

</specifics>

<deferred>
## Deferred Ideas

- **Онбординг 2–3 checker-аккаунтов** — операционная предпосылка фазы (сейчас активных 0). Не код фазы, но без неё пул не запустить. Зафиксировать как первый шаг плана / ручную задачу владельца.
- **Чистка адресной книги чекеров** после прогонов с `importContacts` fallback (D-02) — может стать периодической задачей; уточнить в plan.
- **Observability/алерты** на здоровье пула и rate деградации, **UI-видимость** статуса резолва/здоровья пула — отдельные фазы при необходимости.

None — discussion stayed within phase scope (помимо явно отложенного выше).

</deferred>

---

*Phase: 14-reliable-contact-resolution*
*Context gathered: 2026-06-26*
