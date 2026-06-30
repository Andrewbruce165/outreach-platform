# Phase 17: Sender-side resolve ladder with username capture and import fallback - Research

**Researched:** 2026-06-30
**Domain:** Telegram MTProto contact resolution (Telethon 1.42.0), per-sender resolve ladder, idempotent Postgres migrations, restriction-event auditing
**Confidence:** HIGH (brownfield — decisions locked, Telethon facts verified against the installed package + official docs)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Лестница резолва на отправителе**
- **D-01:** Лестница: (1) кэш per-sender (access_hash) → (2) `ResolveUsername` по захваченному чекером @username → (3) `ImportContacts`. **Собственный `ResolvePhone` отправителя убирается полностью** — именно он давал ложные «нет» в инциденте (троттл/приватность). Совпадает с tier-списком ROADMAP (cache→username→import).
- **D-02:** Tier-3 = **Import-only** (без ResolvePhone на отправителе). `ImportContacts` — это и есть «phone-резолв фолбэк» из ROADMAP; дополнительно вытаскивает registered-но-приватные номера, которые ResolvePhone не видит.
- **D-03:** Import-гейт: `ImportContacts` пытаемся **только если чекер пометил `registered`**; иначе — skip (не тратим рискованный import на `not_registered`).
- **D-04:** Адресную книгу отправителя после import **НЕ чистим** (контакт остаётся — entity-cache горячий для фоллоу-апов). Принят рост книги при ~50/день; периодическая чистка — Deferred.
- **D-05:** Ленивый + размазанный import — **один import на одну отправку, прямо перед send** (никогда пачкой с утра). Лимит 4/мин сам размазывает под burst-онсет ~47–49. (Переисполнение rate-логики не трогаем.)

**Чекер = чистый фильтр + захват @username**
- **D-06:** Чекер **сохраняет `@username`** из ответа `ResolvePhone` (сейчас `resolve_phone_with_fallback` его выбрасывает — возвращает только `{is_registered, telegram_id}`). Username публичный/переносимый (в отличие от per-account access_hash) → даёт отправителю tier-2.
- **D-07:** Захваченный username хранится **durable на `contacts`** (отдельная колонка, напр. `tg_username_captured`) **+ в `contacts_cache`**. Переживает TTL кэша 7д. **НЕ затирает** пользовательский `contacts.username` из CSV (разная provenance).
- **D-08:** Вердикт чекера **не авторитетен по достижимости**; `access_hash` чекера никогда не переиспользуется отправителем (per-account, Telethon).

**Протухший username**
- **D-09:** `ResolveUsername` по захваченному username падает (`USERNAME_NOT_OCCUPIED`/`USERNAME_INVALID`) → **фолбэк на import-tier** (D-03, если registered), **НИКОГДА не финализировать `not_registered`**. Сейчас `telegram.py::_resolve_username` кэширует `False` и выходит — надо поменять на fall-through.

**Country-gate (НЕ делаем — гипотеза)**
- **D-10:** Country-gate **в этой фазе НЕ реализуем**. «US(+1)/cold не резолвит RU(+79)» — непроверенная гипотеза (страна всегда смешана с cold/throttle). Никакого странового гейта в коде.

**Доверие к вердикту + отравленный кэш**
- **D-11:** Import-гейт **простой** — доверяет вердикту контакта как есть (`registered` → резолв/import; иначе skip). Без отдельной confidence-ветки на слое гейта.
- **D-12:** Чтение кэша **confidence-gated** — строка `is_registered=false` от suspect/low-confidence источника **НЕ отдаётся** из `contacts_cache` (ни чекеру, ни отправителю) → live-перерезолв. Кэш **никогда не удаляем**. `_lookup_cache` (checker.py:175) и `_get_cached_contact` (telegram.py) читаются ДО Telegram — это точки правки.
- **D-13 (композиция D-11+D-12):** confidence-интеллект живёт на **слое чтения кэша**, гейт остаётся «тупой-но-безопасный». **Принятый остаточный риск:** high-confidence-но-ложный `not_registered` от недетектированного country/cold всё ещё заблокирует живого лида — принято (country отложен D-10, измеримый throttle-кейс закрыт D-12).
- **D-14:** Инцидентные контакты (22 Barter-ВЭД `failed` + 176 Igor parked) — реквью/резет = **ops после деплоя**, не в фазе. Фаза строит механизм, не трогает конкретные инцидентные строки.

**Block/report-rate метрика**
- **D-15:** Захват `USER_IS_BLOCKED` на send-пути (durable, per-sender) + read-only per-sender **block-rate** поверх захваченных блоков и существующих `sender_restriction_events` (Phase 10). Репорты ненаблюдаемы; трекаем только наблюдаемое.
- **D-16:** **Только хранить (read-only)** — НЕТ control-loop (auto-pause при высоком rate). Alerting/auto-pause — Deferred.

### Claude's Discretion
- Точная схема хранения confidence на слое кэша для D-12 (reuse `contacts_cache.source` + Phase 14 `contacts.tg_confidence`/`tg_resolved_by`/`tg_probe_state` vs новая колонка на `contacts_cache`).
- Имя колонки/миграция для захваченного username (D-07), идемпотентная по паттерну проекта.
- Где живут block-события (расширить `sender_restriction_events` новым `event_type` vs выделенная лёгкая таблица/счётчик) для D-15.
- Точный класс ошибки блока (`UserIsBlockedError`/`USER_IS_BLOCKED`) и форма выражения/эндпоинт rate (D-15).
- Конкретная механика confidence-порога «suspect» на чтении.

### Deferred Ideas (OUT OF SCOPE)
- Проверка гипотезы country-gate (D-10) — чистый изоляционный тест warmed US vs warmed RU.
- Реквью инцидентных контактов (D-14) — 22 Barter-ВЭД `failed` + 176 Igor parked → ops после деплоя.
- Периодическая чистка адресной книги отправителей (следствие D-04 keep).
- Block-rate alerting / auto-pause control-loop / вывод в UI/analytics (D-16).
- Purge отравленного `contacts_cache` (ROADMAP «не чистим»; D-12 решает контаминацию без удаления).
- block/report-rate как полноценная метрика с дашбордом.
</user_constraints>

<phase_requirements>
## Phase Requirements (DERIVED — phase had no mapped IDs; planner should confirm these)

Phase 17 had `phase_req_ids: null`. The following requirement IDs are **derived from the locked decisions** and grounded in code anchors. The planner should formalize these in REQUIREMENTS.md under a new "### Sender-side Resolve Ladder (Phase 17)" section (proposed prefix `SRLD-`). Each maps to a decision and a concrete code change.

| Proposed ID | Description | Decisions | Research Support |
|-------------|-------------|-----------|------------------|
| **SRLD-01** | Checker captures `@username` from `ResolvePhone`/`ImportContacts` result and returns it; `resolve_phone_with_fallback` stops discarding `user.username`. | D-06 | `checker.py:103` returns `{is_registered, telegram_id}` only — `result.users[0].username` is available but dropped. ImportContacts `res.users[0]` (a `User`) also carries `.username`. |
| **SRLD-02** | Captured username persisted durable on `contacts` (new column, e.g. `tg_username_captured`) and on `contacts_cache.username`; never clobbers user-provided `contacts.username` (CSV provenance). | D-07 | `contacts.username` (models __init__.py:484) = CSV provenance. `contacts.tg_username_resolved` (line 491, mig 013) exists but is RESOLVE-provenance written by the worker only on `is_registered` (worker:875) — see §"Captured username storage" for whether to reuse vs add. `contacts_cache.username` (line 196) already exists. |
| **SRLD-03** | Sender resolve becomes a 3-tier ladder: cache(access_hash) → ResolveUsername(captured @username) → ImportContacts; sender's own ResolvePhone removed. | D-01, D-02 | `telegram.py::resolve_contact:494` is currently cache → (username-key branch) → ResolvePhone. The phone branch (521) calls `ResolvePhoneRequest` and has NO import fallback. |
| **SRLD-04** | Tier-3 ImportContacts gated on checker verdict `registered`; `not_registered` → skip (no import). | D-03, D-11 | `contacts.tg_status` ('registered'/'not_registered') is the gate input. `ImportContactsRequest` call shape already exists in `telegram.py::check_contact:612` and `checker.py:125`. |
| **SRLD-05** | Lazy one-at-a-time import right before send; rely on existing 4/min queue limit; do NOT touch queue.py intervals. | D-04, D-05 | `queue.py:809` calls `send_message` per item; import happens inside `resolve_contact`. Rate constants protected (CLAUDE.md). No `DeleteContacts` on sender path (D-04). |
| **SRLD-06** | Stale-username fall-through: `ResolveUsername` raising `UsernameNotOccupiedError`/`UsernameInvalidError` falls through to import-tier (if registered), never finalizes `not_registered`. | D-09 | `telegram.py::_resolve_username:592` currently caches `{is_registered: False}` on username_not_occupied/invalid and returns — must fall through instead. |
| **SRLD-07** | Confidence-gated cache READ: a `is_registered=false` row from a suspect/low-confidence source is NOT served from cache (checker.py AND telegram.py read paths) → forces live re-resolve. Cache never deleted. | D-12, D-13 | Two read sites: `checker.py::_lookup_cache:175` (workspace-wide cross-sender, consulted at :344) and `telegram.py::_get_cached_contact:387` + cross-sender `is_registered=false` shortcut at :442-456. |
| **SRLD-08** | Durable block capture: `UserIsBlockedError` on send → `sender_restriction_events` row (per-sender); read-only per-sender block-rate query/endpoint over captured blocks + Phase 10 events. No control-loop. | D-15, D-16 | `telegram.py::send_message:639` catches FloodWait/PeerFlood/UserNotMutualContact but NOT `UserIsBlockedError`. `record_restriction_event` helper (restriction_audit.py:48) ready to reuse. |
| **SRLD-09** | Docs: soften the country-as-fact wording in `/root/CLAUDE.md` §"Семантика checker'а" to a hypothesis (D-10). | D-10 | `/root/CLAUDE.md` currently states US-cannot-resolve-RU as documented fact; memory reclassified it to hypothesis 2026-06-30. |
</phase_requirements>

## Summary

This is a brownfield phase with all decisions locked (D-01..D-16). Research verifies implementation-critical specifics against the **installed Telethon 1.42.0** (the docker `outreach-platform-api` image — authoritative; the host venv has 1.43.2 but is irrelevant) and authoritative Telethon docs. Every Telethon error class the plan needs (`UsernameNotOccupiedError`, `UsernameInvalidError`, `UserIsBlockedError`, `PhoneNotOccupiedError`, `FloodWaitError`) exists in `telethon.errors` (re-exported from `telethon.errors.rpcerrorlist`). The two MTProto facts the redesign rests on are confirmed verbatim from the docs: **access_hash is per-account** ("trying to reuse the access hash from one account in another will not work") and **a cold phone must be in the contact list before use** ("the phone number must be in your contact list before you can use it").

The code anchors are precise. The checker (`resolve_phone_with_fallback`, checker.py:69) already reads `result.users[0]` — a full `User` carrying `.username` — but returns only `{is_registered, telegram_id}` (D-06 is a 1-line return-shape change). The sender resolve ladder (`telegram.py::resolve_contact:494`) is cache → username-key branch → ResolvePhone with **no import fallback** (D-01/D-02 rebuilds the tail). `_resolve_username:592` finalizes `False` on stale username (D-09 needs fall-through). `send_message:639` catches PeerFlood/FloodWait/UserNotMutualContact but NOT `UserIsBlockedError` (D-15 adds a catch). Both cache READ sites (`checker.py:175/344` and `telegram.py:387/442`) serve `is_registered=false` blind, which is exactly the Igor cross-contamination root cause (D-12).

Storage discretion resolves cleanly: `contacts.tg_username_resolved` already exists (mig 013) but is RESOLVE-provenance written only on the worker's `is_registered` branch — the planner should decide reuse-vs-new (recommendation below leans reuse + a captured flag, since adding a near-identical column risks confusion). `sender_restriction_events.event_type` is **free-form VARCHAR(20) with NO CHECK** (only `category` is constrained), so a new `event_type='blocked'` needs **no CHECK migration** — but a new `category` value would. Latest migration is `043`, so Phase 17's migration is **`044`**.

**Primary recommendation:** Make the checker capture `user.username` (D-06, return-shape change), persist it to `contacts.tg_username_resolved` + `contacts_cache.username` (reuse existing columns, D-07), rebuild `resolve_contact` as cache→ResolveUsername→ImportContacts dropping the sender's ResolvePhone (D-01/D-02), make `_resolve_username` fall through on stale-username to the import tier (D-09), confidence-gate both `is_registered=false` cache reads against `contacts.tg_probe_state`/`tg_confidence` (D-12), and capture `UserIsBlockedError` via the existing `record_restriction_event` with `event_type='blocked'` (D-15, no CHECK migration). One idempotent migration `044` adds whatever new column the planner picks for D-07/D-12.

## Standard Stack

This phase adds **no new dependencies**. It works entirely within the installed stack.

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Telethon | **1.42.0** (pinned in requirements.txt, installed in `outreach-platform-api`) | MTProto client: `ResolvePhoneRequest`, `ResolveUsernameRequest`, `ImportContactsRequest`, error classes | Already the project's TG client; all ladder primitives present |
| SQLAlchemy (async) | 2.0.x | ORM + raw `text()` writes | Project standard (async everywhere, CLAUDE.md) |
| PostgreSQL | 16 (`pgvector/pgvector:pg16` image since Phase 16) | `contacts`, `contacts_cache`, `sender_restriction_events` | Project standard |
| FastAPI | (project pin) | read-only block-rate endpoint (D-15) | Project standard |

**Version verification:**
```bash
docker exec outreach-platform-api pip show telethon | head -2   # → Telethon 1.42.0  (AUTHORITATIVE — the running container)
grep telethon requirements.txt                                   # → telethon==1.42.0
```
NB: the **host** machine has Telethon 1.43.2 in a local venv — IGNORE it. The plan and tests run inside the container (test-overlay), which is 1.42.0.

### Supporting (all existing, reused)
| Asset | Location | Purpose | Decision |
|-------|----------|---------|----------|
| `resolve_phone_with_fallback` | checker.py:69 | checker's ResolvePhone→ImportContacts+DeleteContacts | D-06 (stop dropping username) |
| `resolve_contact` / `_resolve_username` | telegram.py:494 / :558 | sender resolve ladder | D-01/D-02/D-09 |
| `_get_cached_contact` / `_save_contact_cache` | telegram.py:387 / :460 | sender cache read/write | D-12 |
| `_lookup_cache` / `_save_cache` | checker.py:175 / :204 | checker cache read/write | D-12 |
| `record_restriction_event` | restriction_audit.py:48 | durable append-only event writer (dual-mode db) | D-15 |
| `is_username_key` / `username_from_key` | utils/phone.py:82 / :87 | identity-key branching (`@handle` vs phone) | D-01 tier-2 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff (why NOT) |
|------------|-----------|--------------------|
| Reuse `contacts.tg_username_resolved` for D-07 | New `contacts.tg_username_captured` column | New column adds a near-duplicate of an existing RESOLVE-provenance field; D-07 only forbids clobbering the **CSV** `contacts.username`, which `tg_username_resolved` already doesn't touch. Reuse keeps one resolve-provenance field. **Flag for planner — discretion.** |
| New `event_type='blocked'` on `sender_restriction_events` | Dedicated `sender_block_events` table | Existing table already carries workspace/sender/activity_slice/proxy + has the writer + an HLTH-03 read endpoint. `event_type` is free-form (no CHECK migration needed). A new table duplicates infra. Recommendation: reuse. |
| Confidence-gate read via `contacts.tg_probe_state`/`tg_confidence` | New `contacts_cache.confidence`/`source` column | Phase 14 already records resolve provenance on `contacts`. But the cache read in checker.py joins on `phone` only (no contact_id) — see §Confidence-gated cache read for the join consideration. |

## Architecture Patterns

### Recommended change map (no new project structure — edits to existing services)
```
app/services/
├── checker.py            # D-06: resolve_phone_with_fallback returns username
│                         # D-12: _lookup_cache gates is_registered=false reads
├── telegram.py           # D-01/D-02: resolve_contact ladder (drop sender ResolvePhone, add Import tier)
│                         # D-09: _resolve_username fall-through on stale username
│                         # D-12: _get_cached_contact gates is_registered=false reads
│                         # D-15: send_message catches UserIsBlockedError
├── contact_check_worker.py  # D-06: persist captured username into contacts.tg_username_resolved
│                            # (already writes it on is_registered branch:875 — wire res["username"])
└── restriction_audit.py  # D-15: reused as-is (event_type='blocked')
app/routers/
└── senders.py (or new)   # D-15: GET block-rate (read-only, per-sender)
migrations/
└── 044_*.sql             # D-07 column + (opt) D-12 confidence on cache; idempotent
```

### Pattern 1: 3-tier sender resolve ladder (D-01/D-02/D-09)
**What:** `resolve_contact` is rebuilt as cache → ResolveUsername(captured) → ImportContacts; the sender's own ResolvePhone is removed entirely.
**When to use:** every send from `queue.py:809` / `send_message:653`.
**Current code (telegram.py:494-556) — the tail to replace:**
```python
# Source: app/services/telegram.py:507-556 (CURRENT — D-01 removes the ResolvePhone tail)
cached = await self._get_cached_contact(workspace_id, sender_id, phone)
if cached:
    return cached
if is_username_key(phone):                        # tier-2 path for @username keys
    return await self._resolve_username(client, workspace_id, sender_id, phone)
# tier "ResolvePhone" — THIS IS WHAT D-01 DELETES on the sender:
result = await client(ResolvePhoneRequest(phone=phone))   # <-- removed
...
```
**Target shape (planner builds):** cache hit → return; else if a **captured @username** is known for this contact → `ResolveUsername` (its OWN access_hash, D-08); else / on stale-username fall-through → if checker verdict is `registered` (D-03) → `ImportContacts` (with InputPhoneContact, surfacing privacy-hidden), keep the contact (D-04, no DeleteContacts on the sender). `ImportContactsRequest` call+parse pattern already exists at `telegram.py::check_contact:612` and `checker.py:125` — copy the call, OMIT the `DeleteContactsRequest`.

### Pattern 2: Username capture in the checker (D-06)
**What:** `resolve_phone_with_fallback` returns `user.username` alongside `is_registered`/`telegram_id`.
**Example:**
```python
# Source: app/services/checker.py:102-103 (CURRENT) — user object is available, username dropped
if result and result.users:
    return {"is_registered": True, "telegram_id": result.users[0].id}   # .username discarded
# Target: return {..., "username": result.users[0].username}            # D-06
# Same for the importContacts fallback path (line 146): imported_user.username
```
Then `contact_check_worker._apply_results` already writes `tg_username_resolved = res.get("username")` (worker:875) on the `is_registered` branch — today `res["username"]` is always None because the checker drops it. D-06 makes that wiring live. **No worker SQL change needed if reusing `tg_username_resolved`.**

### Pattern 3: Confidence-gated cache read (D-12)
**What:** a cached `is_registered=false` from a suspect/low-confidence source is NOT served — fall through to live resolve.
**Two read sites (both serve false blind today):**
```python
# Source: app/services/checker.py:184-194 — _lookup_cache (workspace-wide, cross-sender, NO confidence)
SELECT is_registered, telegram_id FROM contacts_cache
 WHERE workspace_id = :w AND phone = :p AND updated_at > NOW() - INTERVAL '7 days'
 ORDER BY updated_at DESC LIMIT 1
# Source: app/services/telegram.py:442-456 — cross-sender is_registered=false shortcut (NO confidence)
SELECT is_registered FROM contacts_cache
 WHERE workspace_id = :w AND phone = :p AND is_registered = false
   AND updated_at > NOW() - INTERVAL '7 days' LIMIT 1
```
**Recommended lightest approach:** the confidence signal already lives on `contacts` (Phase 14: `tg_probe_state`, `tg_confidence`, `tg_resolved_by`). The cleanest gate is: **do not serve a `is_registered=false` cache row unless the matching `contacts` row for that phone in the same workspace has `tg_probe_state='clean'` AND `tg_confidence='high'`** (or, symmetrically, suppress the false-row when the contact is `suspect`/`tg_confidence` NULL). Because `contacts_cache` is keyed on `phone` (not `contact_id`), the gate is a correlated EXISTS/JOIN on `contacts.phone = contacts_cache.phone AND same workspace`. See Open Questions #1 for the join-cardinality caveat. **Idempotent + no schema change if gating against existing `contacts.*` columns** — preferred over adding a `contacts_cache.confidence` column.

### Anti-Patterns to Avoid
- **Deleting poisoned cache rows.** ROADMAP says "кэш не чистим"; D-12 fixes contamination by gating the READ, not by purging. A plan that DELETEs cache rows violates scope.
- **Reusing the checker's access_hash on the sender.** Verified-impossible (per-account). The sender must obtain its own access_hash via ResolveUsername or ImportContacts (D-08).
- **Touching queue.py rate constants.** D-05 relies on the existing 4/min limit to spread imports under the ~47–49 burst onset. Adding new intervals or changing the constants is forbidden (CLAUDE.md guard).
- **`DeleteContactsRequest` on the sender after import.** That's the checker's pattern (clean profile). On the sender D-04 explicitly KEEPS the contact (hot entity-cache for follow-ups). Do not copy the checker's cleanup onto the sender.
- **Finalizing `not_registered` on stale username.** D-09 — `_resolve_username` must fall through, never cache/return False on `USERNAME_NOT_OCCUPIED`.
- **@username as a fallback resolver on a throttled/burnt account.** Measured DEAD (checker-pool-throttle-spike.md): `@telegram`/`@durov` returned 0 users on burnt checkers. Tier-2 only works because the sender uses its OWN healthy account and a username captured EARLIER by a (then-healthy) checker — it is not a live re-resolve fallback when the captured username is missing.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Durable per-sender block event | New table + writer + endpoint | `record_restriction_event(sender_id, 'blocked', 'queue_error', None, raw, db=db)` (restriction_audit.py:48) | Existing dual-mode writer handles workspace lookup, activity_slice, proxy snapshot, same-TX guarantee, ON DELETE CASCADE safety (`.one_or_none()`). |
| Block-rate aggregate | Custom counter table | SQL aggregate over `sender_restriction_events` (Phase 10 `idx_sre_sender_created`) + `messages_log` activity slice | Phase 10 already stores the activity slice per event; block-rate = blocks / sends over a window, both queryable. |
| ImportContacts call + parse | New helper | Copy `telegram.py::check_contact:612` (InputPhoneContact → `result.users[0]`), OMIT DeleteContacts | Call shape verified; `ImportedContacts.users` empty = not on Telegram. |
| Username-key branching | New parser | `is_username_key` / `username_from_key` (utils/phone.py:82) | Already the canonical `@handle` vs phone discriminator used by send/resolve. |
| Telethon error matching | String-matching `"USER_IS_BLOCKED" in str(e)` | `except UserIsBlockedError` (imported from `telethon.errors`) | Class exists in 1.42.0 (verified); a typed catch is robust vs locale/format. Keep string-fallback only as defence-in-depth, mirroring existing PHONE_NOT_OCCUPIED handling. |

**Key insight:** Phase 10 + Phase 14 already built the durable-audit and confidence-provenance infrastructure. Phase 17 is almost entirely **wiring existing primitives differently** (capture-not-drop, ResolveUsername-not-ResolvePhone, gate-the-read, catch-one-more-error) — it should add ≤1 migration and 0 dependencies.

## Common Pitfalls

### Pitfall 1: Cache cross-contamination defeats the fix (D-12 — THE root cause)
**What goes wrong:** A poisoned `is_registered=false` cache row (written by a US/throttled checker) is served to the sender/other-checker BEFORE Telegram is called, so a re-check returns the stale false without ever hitting Telegram — the live re-resolve never happens.
**Why it happens:** `_lookup_cache` (checker.py:175, consulted at :344) and the cross-sender shortcut (telegram.py:442) read `contacts_cache` workspace-wide on `phone` only, with no confidence filter.
**How to avoid:** D-12 — gate the READ against `contacts.tg_probe_state='clean'`/`tg_confidence='high'`. A false row from a suspect resolver must NOT short-circuit. (This is the Igor incident — `.planning/debug/checker-fn-igor-base.md`: "a re-check returns the cached false-negative without calling Telegram → must fix the READ.")
**Warning signs:** re-checking a base reproduces the same low registered-rate instantly (cache-served), with no Telegram calls in logs.

### Pitfall 2: Address-book growth on the sender (D-04 keep)
**What goes wrong:** ImportContacts on the sender accumulates contacts (no DeleteContacts per D-04). At scale the book grows ~50/day/account.
**Why it happens:** D-04 intentionally keeps imports (hot entity-cache for follow-ups), unlike the checker which deletes immediately.
**How to avoid:** Accept it for v1 (decision). Lazy one-at-a-time import (D-05) means the sender is NOT a "mass importer" (~50/day spread out — design-doc truth). Periodic book cleanup is **Deferred** — do NOT add it to this phase.
**Warning signs:** none expected at v1 volume; flag only if a sender's book grows into the thousands.

### Pitfall 3: Import burst onset ~47–49 (D-05 — never batch)
**What goes wrong:** Importing/resolving 50 contacts in a morning batch sits exactly on the measured contacts-API burst onset (~45–50 consecutive lookups → throttle), poisoning the account.
**Why it happens:** Telegram penalizes bursts of low-yield contacts-API calls (the same mechanism that burnt the checker pool — checker-false-negatives.md).
**How to avoid:** D-05 — one import per send, right before sending, never up front. The existing 4/min send limit (queue.py) naturally spreads imports well under the onset. Do NOT touch queue.py intervals.
**Warning signs:** a send path that pre-resolves/pre-imports a campaign's contacts in a loop.

### Pitfall 4: @username resolve is DEAD on a throttled account (NOT a live fallback)
**What goes wrong:** Treating tier-2 ResolveUsername as a live fallback that always works for privacy-hidden numbers.
**Why it happens:** Measured (checker-pool-throttle-spike.md): on burnt accounts, `ResolveUsername('@telegram')` returns 0 users — username-resolve is also shadow-throttled.
**How to avoid:** tier-2 works because (a) the **sender** uses its own (healthy, not bulk-resolving) account, and (b) the username was **captured earlier** by a then-healthy checker and stored durable. tier-2 is NOT a re-resolve-by-username when no username was captured — that case goes to tier-3 import. Do not present ResolveUsername as a privacy-bypass fallback for arbitrary numbers.
**Warning signs:** a design where the sender does a live ResolveUsername to *discover* a handle it never captured.

### Pitfall 5: Clobbering CSV `contacts.username` (D-07 provenance)
**What goes wrong:** Writing the checker-captured username into `contacts.username` overwrites the user-provided CSV handle (different provenance, may differ).
**Why it happens:** Both are "a username" but `contacts.username` (models:484) is import-provenance (CSV) and the captured one is resolve-provenance.
**How to avoid:** D-07 — captured username goes to a DISTINCT column (`tg_username_resolved` already exists, mig 013, and is resolve-provenance) + `contacts_cache.username`. Never touch `contacts.username`.
**Warning signs:** an UPDATE that sets `contacts.username = :captured`.

### Pitfall 6: New `category` on sender_restriction_events needs a CHECK migration (D-15)
**What goes wrong:** Writing a block event with a NEW `category` value (e.g. `category='block'`) fails the `sre_category_chk` CHECK (only 'restriction'/'recipient_privacy'/'flood_wait' allowed — mig 030/031).
**Why it happens:** `category` IS CHECK-constrained; `event_type` and `source` are NOT (free-form VARCHAR(20)).
**How to avoid:** Record the block under an EXISTING category (recommended `category='restriction'`, since blocks are the proxy for accumulated reports→PeerFlood per the design doc) with a NEW `event_type='blocked'` — **no CHECK migration needed**. Only if the planner wants a dedicated `category` does mig 044 need to widen `sre_category_chk` (idempotent drop+recreate, mirror mig 031).
**Warning signs:** a CHECK-violation error at the first block event.

### Pitfall 7: Running the migration with the wrong number / non-idempotent
**What goes wrong:** Picking an already-used migration number, or a non-idempotent migration that fails on re-apply → api fails to start (fail-fast).
**How to avoid:** Latest is `043_kb_chunks_fts_index.sql` → use **`044`**. Every statement idempotent: `ADD COLUMN IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS` before `ADD CONSTRAINT`. Mirror the existing migration headers (e.g. 034). Also mirror the ORM (`app/models/__init__.py`) so the test-overlay's `create_all` builds the same schema.
**Warning signs:** api container restart-loops after deploy; `column already exists` on re-apply.

## Code Examples

### Telethon error import (verified, 1.42.0)
```python
# Source: verified in outreach-platform-api container — all re-exported from telethon.errors.rpcerrorlist
from telethon.errors import (
    UsernameNotOccupiedError,   # @username was removed/never existed
    UsernameInvalidError,       # @username malformed
    UserIsBlockedError,         # recipient blocked the sender (D-15 send-path capture)
    PhoneNotOccupiedError,
    FloodWaitError,
)
```

### D-09 stale-username fall-through (target for telegram.py:592)
```python
# CURRENT (telegram.py:592-599) — finalizes False, WRONG per D-09:
except Exception as e:
    if "username_not_occupied" in str(e).lower() or "username_invalid" in str(e).lower():
        contact_info = {"is_registered": False}
        await self._save_contact_cache(...)   # caches False — D-09 forbids
        return {"is_registered": False}
# TARGET (D-09): on UsernameNotOccupiedError/UsernameInvalidError, do NOT cache/return False —
# signal "stale, try import tier" so resolve_contact falls through to tier-3 (if registered).
except (UsernameNotOccupiedError, UsernameInvalidError):
    return {"stale_username": True}   # caller (resolve_contact) routes to ImportContacts per D-03
```

### D-15 block capture on send (target for telegram.py:639 send_message except-chain)
```python
# Add to the except chain in send_message (after PeerFloodError, before generic Exception):
# Source pattern: existing PeerFloodError branch (telegram.py:714) returns a structured error code.
except UserIsBlockedError:
    # D-15: durable per-sender block capture. The queue worker (queue.py) owns the
    # record_restriction_event call in-TX (mirrors PEER_FLOOD at queue.py:947) — telegram.py
    # returns the code, queue.py records the event so it lands in the same transaction.
    return {"success": False, "error": {"code": "USER_IS_BLOCKED",
            "message": "Получатель заблокировал отправителя"}}
# Then in queue.py (mirror the PEER_FLOOD elif at :924): on error_code == "USER_IS_BLOCKED",
# await record_restriction_event(sender.id, "blocked", "queue_error", None, error_msg, db=db).
# NB: a block does NOT pause the sender (one recipient blocked != account restriction) — D-16
# is read-only, no auto-pause. Just record + fail this one item.
```

### D-15 read-only per-sender block-rate query
```sql
-- Source: sender_restriction_events (mig 030) + messages_log activity. Read-only (D-16).
SELECT s.slug,
       COUNT(*) FILTER (WHERE e.event_type = 'blocked'
                          AND e.created_at > NOW() - INTERVAL '7 days') AS blocks_7d,
       (SELECT COUNT(*) FROM messages_log m
         WHERE m.sender_id = s.id AND m.message_type = 'sent'
           AND m.created_at > NOW() - INTERVAL '7 days')               AS sends_7d
  FROM senders s
 WHERE s.workspace_id = :workspace_id
 GROUP BY s.id, s.slug;
-- block_rate = blocks_7d::float / NULLIF(sends_7d, 0)  (compute in Python / SQL as the planner prefers)
```

## State of the Art

| Old Approach | Current Approach (Phase 17) | When Changed | Impact |
|--------------|------------------------------|--------------|--------|
| Sender resolves cold phones via its own `ResolvePhoneRequest` | Sender resolve = cache → ResolveUsername(captured) → ImportContacts; sender ResolvePhone removed | This phase (D-01) | Fixes the 22-failed Barter-ВЭД class (live RU mobiles that ResolvePhone falsely rejected) |
| Checker returns only `{is_registered, telegram_id}` (drops username) | Checker captures `@username` (transferable, unlike access_hash) | This phase (D-06) | Enables the cheap/safe tier-2 ResolveUsername on the sender |
| `contacts_cache` `is_registered=false` served blind before Telegram | Confidence-gated read (suspect false → live re-resolve) | This phase (D-12) | Fixes Igor cross-contamination without purging cache |
| Block-on-send unobserved | `UserIsBlockedError` captured durable per-sender; read-only block-rate | This phase (D-15) | Surfaces the dominant account-killer (blocks→PeerFlood→freeze) as a metric |
| Phase 14: dedicated checker pool drains 14k | Sender-side ladder removes the hard dependency on a separate healthy RU pool | Phase 14 → 17 handoff (STATE.md:162) | Phase 14 SC#3/#4 (re-activation + 14k drain) deferred into 17 |

**Deprecated/outdated (this phase reverses):**
- Country-as-fact claim in `/root/CLAUDE.md` §"Семантика checker'а" — reclassified to HYPOTHESIS 2026-06-30 (D-10 / SRLD-09 doc task). The plan must NOT add a country gate.

## Open Questions

1. **D-12 cache-read gate join cardinality.**
   - What we know: confidence lives on `contacts` (tg_probe_state/tg_confidence/tg_resolved_by); `contacts_cache` is keyed on `phone` (no contact_id FK).
   - What's unclear: a phone can map to multiple `contacts` rows across folders in one workspace. The gate "suppress false cache row when the matching contact is suspect" needs a defined rule when contacts disagree (one clean, one suspect).
   - Recommendation: gate conservatively — serve a `is_registered=false` cache row ONLY if NO matching `contacts` row in the workspace is suspect/low-confidence (i.e. `NOT EXISTS (... tg_probe_state='suspect' OR tg_confidence IS DISTINCT FROM 'high' ...)`). When in doubt, fall through to live resolve (safe: the sender resolves itself anyway). Planner to confirm the exact predicate; it is Claude's-discretion per CONTEXT.md.

2. **D-07 reuse `tg_username_resolved` vs new `tg_username_captured` column.**
   - What we know: `tg_username_resolved` (mig 013) exists, is resolve-provenance, written by the worker on the `is_registered` branch (worker:875), currently always NULL because the checker drops username.
   - What's unclear: whether the planner wants to distinguish "captured by checker at check time" from "resolved by sender at send time" as separate provenance.
   - Recommendation: reuse `tg_username_resolved` (it is exactly resolve-provenance and already wired) and let D-06 make it live; add a new column ONLY if the planner needs the check-time/send-time distinction. Either way mig 044 is trivial (the reuse case may need NO migration at all — verify the column is already in prod via mig 013).

3. **D-15 block event category — `restriction` vs new.**
   - What we know: `category` IS CHECK-constrained; treating a block as `category='restriction'` includes it in restriction analytics; the design doc frames blocks as the proxy for accumulated reports→PeerFlood.
   - Recommendation: `category='restriction'`, `event_type='blocked'` (no migration). If the planner wants block-rate excluded from the restriction-status analytics filter, widen the CHECK in mig 044 (idempotent). Flagged as discretion.

## Runtime State Inventory

> Phase 17 is a code/data-semantics change, not a rename. There is no string-rename runtime state to chase. The relevant "runtime state" is **the poisoned `contacts_cache` data**, which D-12 handles by gating the read and D-14 explicitly defers as ops:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `contacts_cache` rows with `is_registered=false` written by US/throttled checkers (Igor: 176 phones; Barter-ВЭД: 22). `contacts` rows finalized `not_registered`/`failed` from suspect resolvers. | **Code:** D-12 gates the cache READ so these no longer short-circuit live resolve. **Data migration:** NONE in this phase — D-14 defers incident-row reset/review to ops after deploy. |
| Live service config | None — no external service stores this phase's strings. | None — verified (no n8n/Datadog/Tailscale dependency in resolve path). |
| OS-registered state | None — no Task Scheduler / pm2 / systemd state involved. | None — verified. |
| Secrets/env vars | New optional `CONTACT_CHECK_*`-style knobs are NOT required by this phase; existing knobs unchanged. No secret renames. | None. |
| Build artifacts | None — pure Python edits + one SQL migration (044), auto-applied at api start. | Rebuild api container (`docker compose up -d --build api`) to apply mig 044 and code — standard deploy. |

**Canonical question answer:** After the code lands, the only stale runtime state is the historical poisoned cache rows — and by design (D-12 + D-14) the code stops trusting them rather than deleting them; physical cleanup is deferred ops.

## Common Pitfalls → Verification (cross-ref for the planner)

- Pitfall 1 (D-12) → assert a suspect false cache row triggers a live Telegram call (mock checker confidence=suspect, assert ResolvePhone/ResolveUsername/Import called).
- Pitfall 5 (D-07) → assert capture writes `tg_username_resolved`, leaves `contacts.username` (CSV) untouched.
- Pitfall 6 (D-15) → assert block event inserts with the chosen category WITHOUT a CHECK violation.
- Pitfall 4 → assert tier-2 only fires when a captured username exists; missing username → tier-3 import (if registered) → skip (if not_registered).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Telethon | resolve ladder, error classes | ✓ (container) | 1.42.0 | — |
| `telethon.errors.{UsernameNotOccupied,UsernameInvalid,UserIsBlocked,PhoneNotOccupied}Error` | D-09, D-15 | ✓ | all present in 1.42.0 | string-match fallback (defence-in-depth) |
| PostgreSQL 16 + `sender_restriction_events` (mig 030/031) | D-15 | ✓ | mig applied | — |
| `contacts.tg_confidence/tg_resolved_by/tg_probe_state` (mig 034) | D-12 | ✓ | mig applied | — |
| `record_restriction_event` helper | D-15 | ✓ | restriction_audit.py:48 | — |
| Healthy RU checker pool | D-06 username capture at scale (operational) | ✗ (all 3 parked, throttled — STATE.md) | — | **Sender-side ladder (this phase) is itself the fallback** — it removes the hard dependency on a healthy separate pool (STATE.md:162). Capture happens opportunistically when a checker IS healthy; the sender ladder still works (tier-3 import) without it. |

**Missing dependencies with no fallback:** none blocking — the phase BUILDS the mechanism; data drain (14k / incident rows) is deferred ops (D-14).
**Missing dependencies with fallback:** healthy checker pool — the sender ladder is the structural fallback; tier-2 username capture is a best-effort optimization that degrades gracefully to tier-3 import.

## Validation Architecture

> nyquist_validation = true in .planning/config.json → section included.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode = "auto"`, session-scoped loop) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_checker.py tests/test_send.py tests/test_contact_check_worker.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

**CRITICAL (CLAUDE.md):** NEVER run `docker compose run --rm api pytest` without the test-overlay — the conftest guard blocks it, but the canonical path is the overlay (ephemeral `db-test` in tmpfs). NEVER `down -v` afterward (wipes prod `postgres_data`).

### Phase Requirements → Test Map
| Req | Behavior | Test Type | Automated Command | File Exists? |
|-----|----------|-----------|-------------------|-------------|
| SRLD-01 | `resolve_phone_with_fallback` returns `username` (ResolvePhone + Import paths) | unit | `pytest tests/test_checker.py -k username_capture -x` | ❌ Wave 0 (extend test_checker.py — `mock_telethon_client` fixture exists) |
| SRLD-02 | capture persists to `tg_username_resolved`, not `contacts.username` | integration | `pytest tests/test_contact_check_worker.py -k captured_username -x` | ❌ Wave 0 |
| SRLD-03 | resolve ladder: cache→ResolveUsername→Import, sender ResolvePhone NOT called | unit | `pytest tests/test_send.py -k resolve_ladder -x` | ❌ Wave 0 (assert ResolvePhoneRequest never in client.calls) |
| SRLD-04 | Import gated on `tg_status='registered'`; `not_registered` → no import | unit | `pytest tests/test_send.py -k import_gate -x` | ❌ Wave 0 |
| SRLD-05 | one import per send, no DeleteContacts on sender, queue intervals untouched | unit + grep | `pytest tests/test_send.py -k lazy_import -x` + `grep -n "DeleteContacts" app/services/telegram.py` (expect 0 in send path) | ❌ Wave 0 |
| SRLD-06 | stale username (`UsernameNotOccupiedError`) → fall through to import, never finalize not_registered | unit | `pytest tests/test_send.py -k stale_username_fallthrough -x` | ❌ Wave 0 |
| SRLD-07 | suspect `is_registered=false` cache row NOT served → live resolve (both checker.py + telegram.py reads) | integration | `pytest tests/test_checker.py tests/test_send.py -k confidence_gated_cache -x` | ❌ Wave 0 |
| SRLD-08 | `UserIsBlockedError` → `sender_restriction_events` row (event_type='blocked'); block-rate query | integration | `pytest tests/test_restriction_audit.py -k blocked -x` + `pytest tests/test_send.py -k user_blocked -x` | ❌ Wave 0 (extend test_restriction_audit.py) |
| SRLD-09 | CLAUDE.md country wording softened to hypothesis | manual/grep | `grep -n "гипотеза\|hypothesis" /root/CLAUDE.md` (doc task) | manual |
| mig 044 | idempotent re-apply; ORM mirror builds same schema | integration | `pytest tests/ -k migration -x` (mirror test_migration_032 pattern) | ❌ Wave 0 if a column is added |

### Sampling Rate
- **Per task commit:** the quick run command above (checker + send + worker tests, `-x`).
- **Per wave merge:** full suite (currently ~837 collected per Phase 16; must stay green).
- **Phase gate:** full suite GREEN before `/gsd:verify-work`. Baseline is GREEN (memory `project-test-baseline-red` — was 81 failing, now green; TEST_EXIT==0 trustworthy).

### Wave 0 Gaps
- [ ] `tests/test_checker.py` — extend for SRLD-01 (username capture) + SRLD-07 (confidence-gated `_lookup_cache`). Fixture `mock_telethon_client` already exists (conftest, Plan 14-01) with `.set_response()`/`.calls`.
- [ ] `tests/test_send.py` — SRLD-03/04/05/06/07 (resolve ladder, import gate, lazy import, stale fall-through, gated read on sender). Existing file uses `pytestmark = pytest.mark.asyncio` + `async_client`/`async_db_session` fixtures.
- [ ] `tests/test_contact_check_worker.py` — SRLD-02 (captured username persisted to `tg_username_resolved`).
- [ ] `tests/test_restriction_audit.py` — SRLD-08 (`event_type='blocked'` insert, no CHECK violation, block-rate aggregate).
- [ ] Migration round-trip test (if mig 044 adds a column) — mirror `tests/test_migration_032.py`.
- [ ] Framework install: none — pytest/pytest-asyncio already configured.

*Note: the throttle-burst behavior (~47–49 onset) is NOT unit-testable (it's a live Telegram server-side mechanism). Validation for D-05 is structural: assert one-import-per-send + grep that queue.py intervals are untouched + assert no batch import loop. The block-rate (D-15) is observable and testable; the report-rate is explicitly NOT trackable (design-doc truth).*

## Sources

### Primary (HIGH confidence)
- **Installed Telethon 1.42.0** (`outreach-platform-api` container) — verified all error classes exist in `telethon.errors`/`rpcerrorlist`; verified `ResolvedPeer(peer, chats, users)` and `ImportedContacts(imported, popular_invites, retry_contacts, users)` return shapes by introspection.
- **Telethon docs** https://docs.telethon.dev/en/stable/concepts/entities.html — confirmed verbatim: access_hash per-account ("trying to reuse the access hash from one account in another will not work"); cold phone must be in contact list ("the phone number must be in your contact list before you can use it").
- **Code anchors (read in full):** `app/services/checker.py`, `app/services/telegram.py` (resolve/send paths), `app/services/contact_check_worker.py` (finalization), `app/services/restriction_audit.py`, `app/models/__init__.py` (Contact/ContactCache/Sender/SenderRestrictionEvent), `app/config.py` (CONTACT_CHECK_* knobs), `migrations/030/031/034/039`, `app/utils/phone.py`.
- **Latest migration:** `043_kb_chunks_fts_index.sql` → next is `044`.

### Secondary (MEDIUM confidence — project design docs, internally consistent + measured)
- `.planning/notes/sender-side-resolve-redesign.md` — phase design doc, Telethon facts, 3-tier ladder.
- `.planning/notes/checker-problem-and-history.md` — 4-cause conflation of `is_registered=false`.
- `.planning/notes/checker-false-negatives.md` + `checker-pool-throttle-spike.md` — burst onset ~47–49, @username dead on burnt accounts (measured).
- `.planning/debug/checker-fn-igor-base.md` — cache cross-contamination root cause.
- `.planning/phases/14-reliable-contact-resolution/14-CONTEXT.md` + `.planning/REQUIREMENTS.md` (RESV-01..07) — confidence/probe semantics inherited.
- `.planning/STATE.md` — Phase 14 → 17 handoff (SC#3/#4 deferred into 17).

### Tertiary (LOW confidence)
- None — every implementation-critical claim is verified against the installed package, official docs, or the actual code.

## Project Constraints (from CLAUDE.md)

- **Migrations:** raw idempotent SQL `NNN_short_name.sql`, auto-applied at api start (`app/database.py::_apply_migrations`). Must be idempotent (`IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS` before ADD). NEVER Alembic. api fail-fasts if a migration raises. **Phase 17 migration = 044.** Mirror new columns in the ORM (`app/models/__init__.py`) so the test-overlay `create_all` builds the same schema (memory `project-orm-default-vs-server-default-drift`).
- **Async everywhere:** all DB via async/await + AsyncSession. No `time.sleep()`, no sync `requests`, no `print()`.
- **Queue rate constants are protected:** 4/min, 20/hour, 150/day per sender; 09:00–20:00 window logic — do NOT change without explicit discussion. D-05 RELIES on the 4/min limit; introduce no new intervals.
- **FloodWait retry logic:** do not break without explicit ask.
- **Security:** sessions encrypted; API_KEY/session strings never logged; restriction `raw_text` carries only human-facing error text.
- **Tests:** ONLY via test-overlay (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`). NEVER plain `docker compose run --rm api pytest` (DROP SCHEMA → prod). NEVER `down -v` (wipes prod volume).
- **Communication:** explain-before-code in Russian for non-trivial changes; code/commits in English.
- **Parallel-agent caution (memory):** another agent may touch the repo — stage specific files, never `git add -A`.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new deps; Telethon 1.42.0 verified in the running container.
- Architecture / code anchors: HIGH — every cited line read directly; method signatures and gaps confirmed.
- Telethon API specifics (errors, return shapes, MTProto facts): HIGH — introspected the installed package + verbatim official docs.
- Pitfalls: HIGH — sourced from measured incident reports (Igor, Barter-ВЭД, throttle spike) + verified code.
- D-07/D-12/D-15 storage discretion: MEDIUM — recommendations given, but final column/category choice is the planner's discretion (flagged in Open Questions).

**Research date:** 2026-06-30
**Valid until:** 2026-07-30 (stable — Telethon pinned, internal code; re-verify only if Telethon pin changes or migrations advance past 044)
