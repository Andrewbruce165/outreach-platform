# Phase 15: Account Warmup via Inter-Account AI Chat - Research

**Researched:** 2026-06-29
**Domain:** Brownfield productization of an existing async background worker (warmup engine) + workspace-scoped FastAPI rewrite + listener isolation hardening
**Confidence:** HIGH (all findings come from the live codebase, not external sources; this is an internal-stack phase with zero new libraries)

## Summary

Phase 15 is **not a greenfield build**. The full-mesh AI-warmup engine (`app/services/warmup.py`), its tables (`migrations/005_warmup.sql` + `workspace_id` from `012_workspace.sql`), and a CRUD router (`app/routers/warmup.py`) already exist and work. The phase **wraps** that engine into a product: it closes the isolation holes that killed the same feature in `telegram-api`, rewrites the router from legacy `verify_api_key` onto the project-standard `auth_dep`/`AuthCtx` workspace-scoping, adds a per-workspace `warmup_enabled` flag + content settings, fixes a pool-selection security hole, and ships one new UI tab.

The dominant risk is **isolation (WARM-01..04, WARM-15)**. The root cause of the historical pollution is fully diagnosed in `.planning/debug/dashboard-analytics-warmup-pollution.md`: the listener's inbound warmup filter was *phone-based* and leaked when Telegram hid the phone (`phone="unknown"`), so synthetic warmup traffic between our own 13 accounts flooded `conversations`/`messages` (5382 fake `sent`) and triggered the AI responder. The aimly listener has *already partially* fixed this (a `telegram_id` branch was added at `listener.py:689` and `_EXCLUDE_INTERNAL_CLAUSE` at `analytics.py:135`), but the mechanism is still **cache-dependent and pool-membership-dependent** — exactly the "we hope the cache is fresh" fragility the user explicitly rejected. The fix per D-01/D-02 is a **deterministic short-circuit keyed on `telegram_id ∈ {senders of this workspace}`** (NOT phone, NOT `warmup_pool` membership), placed at the *earliest* point in both incoming and outgoing handlers, dropping the message before any `conversations`/`messages` write and before `schedule_ai_response`.

**Primary recommendation:** Add a deterministic per-workspace internal-sender `telegram_id` set to the listener (replacing the warmup-pool-scoped cache for the isolation decision), short-circuit symmetrically in `handle_incoming_message` (right after the "skip self" check, before the antispam/bot branches) and `handle_outgoing_message` (before the conversation lookup), back it with a `getsource` introspection regression guard (Phase 13 pattern), store warmup control state in a new idempotent `warmup_settings` table keyed by `workspace_id` (migration `037_*.sql`), and add `restriction_status`/`restricted_until` skipping to `_get_active_pool` and `_refresh_warmup_cache` (Phase 14 RESV-05 pattern). No new third-party dependencies.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Изоляция от аутрича:**
- **D-01:** Главный признак изоляции — «свой со своим» = internal. Любой Telegram-трафик между двумя нашими senders ОДНОГО workspace (по `telegram_id ∈ senders` этого workspace) считается internal — НЕ зависит от `phone` (закрывает leak при `phone="unknown"`) и НЕ зависит от членства в `warmup_pool`. Тот же признак уже используется в аналитике (`_EXCLUDE_INTERNAL_CLAUSE`).
- **D-02:** Листенер при детекте internal-трафика дропает его до AI (никогда не вызывает `schedule_ai_response`) и не создаёт строк в `conversations`/`messages`. Весь warmup живёт ТОЛЬКО в таблицах `warmup_*` — чисто на записи, без фильтрации на чтении.
- **D-03:** Лимиты независимые. Warmup шлёт напрямую через Telethon, минуя `message_queue` → не жжёт rate-limits кампаний (4/20/150). Дневные warmup-лимиты по уровням остаются отдельными. Верхний warmup-уровень капается 120/день.
- **D-04:** Регресс-тест-гард изоляции (обязателен) — доказывает, что internal-трафик не триггерит AI-ответчик и не попадает в метрики аналитики. Аналог source-introspection гардов из Phase 13.

**Workspace-scoping / API:**
- **D-05:** Рерайт всех эндпоинтов `/api/v1/warmup` с `verify_api_key` на `AuthDep` + workspace scope (паттерн Phase 3/4/5): все запросы фильтруются по `workspace_id` из токена. Форма ответов существующих эндпоинтов (`/pool`, `/stats`, `/sessions`, ...) сохраняется; добавляем только нужные control-эндпоинты.
- **D-06:** Старт/стоп прогрева = `warmup_enabled` флаг per-workspace в БД (НЕ перезапуск процесса). `WarmupWorker` остаётся глобальным singleton-тиком, но на каждом тике читает флаг и пропускает workspace с выключенным прогревом.

**Контролы UI-вкладки:**
- **D-07:** Гранулярность — master + per-account: одна кнопка «прогрев вкл/выкл» на workspace (D-06 флаг) + существующий per-account toggle в пуле.
- **D-08:** Расписание — оставить 09–20 МСК (захардкожено в воркере). Настраиваемое окно/TZ → deferred.
- **D-09:** Интенсивность — авто по дням (`LEVEL_CONFIG`, 5 уровней, 5→120 msg/день). UI показывает текущий уровень/прогресс, но НЕ даёт ломать безопасную кривую руками (без ручного уровня и без пресета slow/normal/fast в v1).
- **D-10:** Контент прогрева — настраиваемый per-workspace (темы / язык / тон). Сейчас захардкожены 24 RU-темы + `WARMUP_SYSTEM_PROMPT`. Хранить per-workspace вместе с `warmup_enabled` (единый объект настроек прогрева workspace). Дефолт = текущие 24 RU-темы + промпт, чтобы существующее поведение не сломалось при пустых настройках.
- **D-11:** Per-account статус в вкладке — расширенный: на базе `/pool`+`/stats` (уровень, `sent_today`, `enrolled_days`, активен/пауза) + ДОБАВИТЬ `restriction_status` и последнюю ошибку/активность прогрева, чтобы было видно, ПОЧЕМУ аккаунт не греется.

**Пул и совмещение с кампаниями:**
- **D-12:** Совмещение разрешено — аккаунт может одновременно греться и быть в активной кампании. Авто-паузы прогрева при работе в кампании нет.
- **D-13:** Новые аккаунты — ручное зачисление в пул (НЕ авто-enroll при онбординге).
- **D-14:** ДЫРА БЕЗОПАСНОСТИ (закрыть): текущая выборка пула (`_get_active_pool`) фильтрует `lifecycle_status='active' AND auth_status='ok'`, но НЕ смотрит `restriction_status` → аккаунт с `spam_limited`/`frozen` продолжает греться. Добавить пропуск аккаунтов с `restriction_status != 'none'` ИЛИ `restricted_until` в будущем (паттерн Phase 14 RESV-05).

### Claude's Discretion
- Точная схема хранения `warmup_settings` workspace (новая таблица vs строка vs JSONB-колонка) — реализация D-06/D-10.
- Форма control-эндпоинтов (master toggle, обновление настроек) и их имена в пределах паттерна Phase 3/4/5 — D-05.
- Где именно в `listener.py` ставить internal-short-circuit (до буфера/дебаунса), при сохранении симметрии для incoming и outgoing — D-01/D-02.
- Набор полей и формат «последней ошибки/активности» прогрева для D-11.

### Deferred Ideas (OUT OF SCOPE)
- Настраиваемое окно расписания + таймзона прогрева per-workspace — оставили 09–20 МСК (D-08). → backlog.
- Пресет интенсивности slow/normal/fast или ручной уровень — отклонено для v1 (D-09). → при запросе.
- Observability / алерты на здоровье прогрева (rate деградации, % пула под ограничением, FloodWait-тренды) — отдельная фаза.
- Auto-pause прогрева при активной кампании — отклонено (D-12).
- Многоязычный UI самой вкладки — вне scope (контент диалогов настраиваемый D-10, но интерфейс — нет).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| WARM-01 | Internal-детекция «свой со своим» по `telegram_id ∈ senders` workspace; листенер дропает до AI, не зависит от phone/членства в пуле (D-01) | §Pattern 1 (deterministic short-circuit); listener anchors `listener.py:621-625, 689-697, 1138-1144`; new per-workspace internal-sender set replacing warmup-pool cache for the isolation decision |
| WARM-02 | Internal-трафик не создаёт строк в `conversations`/`messages`; warmup только в `warmup_*` (D-02). Аналитика чиста (`_EXCLUDE_INTERNAL_CLAUSE`) | §Pattern 1 placement (before any DB write / `schedule_ai_response`); `analytics.py:135` clause preserved verbatim |
| WARM-03 | Warmup-лимиты независимы от rate-limits кампаний; отправка минует `message_queue` (D-03) | Already true — `warmup.py:571 _send_via_telethon` calls `client.send_message` directly; `LEVEL_CONFIG` daily caps independent. Verify queue untouched (CLAUDE.md guard) |
| WARM-04 | Регресс-тест-гард: internal не триггерит AI и не попадает в метрики (D-04) | §Validation Architecture + §Pattern 3 (getsource introspection guard, Phase 13 `_assert_pacing_predicate_wired` pattern) |
| WARM-05 | Все `/api/v1/warmup` под `AuthDep` + workspace scope (D-05) | §Pattern 2 (`auth_dep`/`AuthCtx` from `app/utils/auth.py`); full endpoint inventory in §Don't-break-the-response-shape |
| WARM-06 | `warmup_enabled` per-workspace; глобальный воркер honors флаг (D-06) | §Storage Recommendation (`warmup_settings` table, mig 037); `_get_active_pool` JOIN gate; worker stays singleton |
| WARM-07 | UI master toggle + per-account enroll/toggle (D-07) | 15-UI-SPEC.md (approved); existing pool endpoints reused |
| WARM-08 | Расписание 09–20 МСК без UI-настройки (D-08) | `warmup.py:144 _is_working_hours` unchanged; read-only display |
| WARM-09 | Интенсивность авто по дням; UI read-only уровень/прогресс (D-09) | `warmup.py:33 LEVEL_CONFIG` unchanged; `/stats` exposes level/sent_today |
| WARM-10 | Per-workspace настройки контента (темы/язык/тон) с дефолтом = текущие 24 RU-темы + промпт (D-10) | §Storage Recommendation; engine reads settings-or-default at `warmup.py:500/540` |
| WARM-11 | Расширенный per-account статус (+`restriction_status`, +последняя ошибка/активность) (D-11) | `/pool` enrichment; `senders.restriction_status/restricted_until` (mig 028); §Open Question 1 for "last error" source |
| WARM-12 | Совмещение прогрева с активной кампанией разрешено (D-12) | No change needed — engine and queue are independent paths |
| WARM-13 | Новые аккаунты не авто-зачисляются (D-13) | No change — onboarding does NOT touch `warmup_pool`; verified no enroll on onboard |
| WARM-14 | Выборка пула пропускает `restriction_status != 'none'`/future `restricted_until` (D-14) | §Pattern 4 (`_get_active_pool` + `_refresh_warmup_cache` query patch, RESV-05 model from `contact_check_worker.py:259-268`) |
| WARM-15 | Изучить старую `telegram-api` warmup и зафиксировать почему конфликтовала (изоляция) | §State of the Art (root-cause comparison: old phone-only inbound filter, no telegram_id branch) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

These are non-negotiable. The planner must not produce tasks that violate them.

- **Async everywhere.** All DB access via `async`/`await` + `AsyncSession`. No sync DB calls.
- **Migrations: raw SQL only**, `NNN_short_name.sql` in `migrations/`, **auto-applied on api start** via `app/database.py::_apply_migrations` behind `pg_advisory_lock`. **Must be idempotent** (`IF NOT EXISTS`, `DO $$ … EXCEPTION duplicate_object $$`, `ON CONFLICT DO NOTHING`). If a migration fails, the api does NOT start (fail-fast). **Never Alembic.** Next free number is **037** (036 is the highest existing).
- **Never:** `time.sleep()`, synchronous `requests`, `print()` instead of `logging`.
- **Queue rate-limit intervals are PROTECTED** (empirically tuned 4/20/150 + the `queue.py` constants). Do not touch them. Warmup is a *separate* path (D-03) — it must stay separate and never route through `message_queue`.
- **FloodWait retry logic** must not be broken — `warmup.py:610` already handles it; preserve.
- **Sessions encrypted; `API_KEY` never logged.** Warmup logs must never emit prompts/payloads beyond the existing `[:60]` truncation.
- **Tests ONLY via test-overlay:** `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. NEVER bare `docker compose run --rm api pytest` (conftest guard does `DROP SCHEMA` if DSN is prod — see the 2026-05-26 incident).
- **Russian** for user-facing copy and conversation with the user; English for code/commits.
- **Don't run `docker compose down -v`** on tg-outreach — it deletes the prod postgres volume (MEMORY.md).
- **Do NOT run / restart `/root/apps/telegram-api`** — it shares the same 13 Telegram accounts; starting its listener re-creates the exact session-conflict that caused the original pollution. Read it only as a reference.

## Standard Stack

This is an internal-stack phase. **No new third-party libraries are required or recommended.** Everything is built on the already-pinned project stack.

### Core (already installed — do not add/upgrade)
| Library | Version (verified `requirements.txt`) | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fastapi | 0.109.0 | Router + `Depends(auth_dep)` | Project standard for every router |
| sqlalchemy | 2.0.25 (async) | `AsyncSession`, raw `text()` SQL | Project standard; all warmup code already uses `text()` |
| telethon | 1.42.0 | Direct `client.send_message` (bypasses queue, D-03) | Already used by `warmup._send_via_telethon` |
| pydantic / pydantic-settings | >=2.8,<3 / >=2.3,<3 | Request/response schemas, env knobs in `config.py` | Project standard; `validation_alias` env pattern |
| openai | >=1.40.0,<2.0.0 | `AsyncOpenAI` for warmup message generation | Already used by `WarmupWorker._generate_message` |

**Installation:** None. `pip install` is not part of this phase.

**Version verification note:** Versions read from `/root/apps/aimly/tg-outreach/requirements.txt` (the source of truth in the container). No registry check needed — the phase pins to what is already deployed; introducing a new version would violate the "wrap, don't rewrite" boundary.

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| New `warmup_settings` table | JSONB column on `workspaces` | Table is cleaner for the multi-field content object + future per-workspace knobs; a row keyed by `workspace_id` with `ON CONFLICT` upsert is the established pattern (e.g. `workspace_api_keys`). JSONB on `workspaces` couples warmup to the core tenant table. **Recommend table.** |
| Per-workspace internal-sender cache in listener | Per-message `SELECT EXISTS` | The set is tiny (tens of senders) and refreshes on a TTL; a per-message SELECT adds latency to every inbound. But the *current* TTL-cache fragility is what leaked. **Recommend: keep a TTL cache but key it on workspace senders, NOT warmup_pool, and back it with the deterministic introspection guard.** See Pattern 1. |

## Architecture Patterns

### Recommended file touch-map (no new top-level structure)
```
app/services/warmup.py      # _get_active_pool (D-14), settings read (D-10), enabled gate (D-06)
app/routers/warmup.py       # rewrite verify_api_key → auth_dep (D-05), +master toggle/settings (D-06/D-07/D-10)
app/services/listener.py    # deterministic internal short-circuit (D-01/D-02), workspace-sender set
app/routers/analytics.py    # _EXCLUDE_INTERNAL_CLAUSE — PRESERVE, do not edit
migrations/037_warmup_settings.sql   # new: warmup_settings table (idempotent)
tests/test_warmup_isolation.py       # new: D-04 regression guard (Wave 0)
tests/test_warmup_router.py          # new: workspace-scoping + endpoint shapes
```

### Pattern 1: Deterministic internal-traffic short-circuit (D-01/D-02 — THE core risk)

**What:** A message is `internal` iff its counterparty `telegram_id` belongs to *another sender of the same workspace as the listening sender*. Drop it before any `conversations`/`messages` write and before `schedule_ai_response`.

**Why the current code is not yet deterministic (the gap to close):**
- `sender_info` loaded by `_load_active_senders` (`listener.py:405-426`) carries `id, slug, phone, session_string, proxy` — **NOT `workspace_id`**. The isolation decision today borrows `_warmup_*` caches that are scoped to `warmup_pool` membership, so an internal pair that is *not enrolled in the pool* (or whose cache is stale) leaks. D-01 explicitly says the signal must NOT depend on pool membership.
- Inbound (`listener.py:689-697`): primary `telegram_id` branch checks `sender.id in warmup_tg_ids` (pool-scoped), then a phone branch. Pool-scoped + phone-fallback = the exact fragility.
- Outgoing (`listener.py:1138-1144`): gated on `sender_info["id"] in self._warmup_sender_ids` (pool-scoped) AND `chat.id in warmup_tg_ids` (pool-scoped).

**Recommended implementation (discretion area — concrete proposal):**
1. Add `workspace_id` to the `_load_active_senders` SELECT and to each `sender_info` dict (`listener.py:407`). This makes the listening sender's workspace known at handler entry.
2. Add a new TTL cache to the listener: `_workspace_sender_tg_ids: dict[workspace_id -> set[telegram_id]]` populated from **all** `senders WHERE role='sender' AND telegram_id IS NOT NULL` grouped by `workspace_id` (NOT joined to `warmup_pool`). Reuse the existing `WARMUP_CACHE_TTL = 60.0` machinery shape (`_refresh_warmup_cache` at `listener.py:561`). This is the D-01 source of truth.
3. **Inbound short-circuit** — place it right after the "skip self" check (`listener.py:623-625`) and BEFORE the bot/antispam branches (so internal traffic never even reaches the antispam delegation). Condition: `sender.id in self._workspace_sender_tg_ids.get(sender_info["workspace_id"], set())`. On match: `logger.debug(...)`; `return`. No DB write, no `schedule_ai_response`.
4. **Outgoing short-circuit** — place it before the conversation lookup (`listener.py:1148`), replacing the pool-scoped block at `1138-1144`. Condition: `chat.id in self._workspace_sender_tg_ids.get(sender_info["workspace_id"], set())`. On match: `return` before any `save_message`/`get_or_create_conversation`.

**Example (shape, from current handler entry):**
```python
# Source: app/services/listener.py:621-625 (existing "skip self") — anchor for inbound short-circuit
me = await event.client.get_me()
if sender.id == me.id:
    logger.info(f"📨 Пропускаем своё сообщение от {name}")
    return
# NEW (D-01/D-02): drop any traffic from another sender of THIS workspace, before AI / DB.
internal_ids = await self._get_workspace_sender_tg_ids(sender_info["workspace_id"])
if sender.id in internal_ids:
    logger.debug(f"🔥 internal warmup traffic dropped (tg_id={sender.id})")
    return
```

**When to use:** Both handlers, symmetric, always-on (not gated by warmup_pool, not gated by `warmup_enabled` — a stopped-warmup workspace still must never leak any residual internal traffic).

### Pattern 2: Workspace-scoped router rewrite (D-05)

**What:** Replace `Depends(verify_api_key)` (legacy, no workspace) with `Depends(auth_dep)` returning `AuthCtx`, and filter every query by `ctx.workspace_id`.

**Source pattern:** `app/routers/senders.py` (header doc lines 1-25) and `app/utils/auth.py:170-209`. Canonical signature:
```python
# Source: app/routers/senders.py + app/utils/auth.py
from app.utils.auth import AuthCtx, auth_dep

@router.get("/pool")
async def list_pool(
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    result = await db.execute(text("""
        SELECT ... FROM senders s
        LEFT JOIN warmup_pool wp ON wp.sender_id = s.id
        WHERE s.role = 'sender'
          AND s.workspace_id = :wid          -- NEW: workspace scope
        ORDER BY s.name
    """), {"wid": str(ctx.workspace_id)})
```

**Every existing query in `warmup.py` router must gain `AND <table>.workspace_id = :wid`** (or a JOIN-through to `senders.workspace_id`). `warmup_pool`, `warmup_sessions`, `warmup_messages` all carry `workspace_id` since migration 012 — use it directly. Add-to-pool / delete / toggle must also verify the target sender belongs to `ctx.workspace_id` (404 if not — mirror `senders.py` `_validate_workspace_owns_*`).

### Pattern 3: Source-introspection regression guard (D-04)

**What:** A test that asserts the isolation short-circuit is *actually wired* into the handler source, so a future refactor can't silently remove it and pass by coincidence.

**Source pattern:** `tests/test_queue_even_pacing.py:290-309` `_assert_pacing_predicate_wired()` uses `inspect.getsource(QueueWorker._process_next_for_sender)` and asserts marker tokens are present.

```python
# Source: tests/test_queue_even_pacing.py:290 pattern, adapted
import inspect
from app.services.listener import TelegramListener  # or whatever the class is named

def _assert_internal_shortcircuit_wired():
    inc = inspect.getsource(TelegramListener.handle_incoming_message)
    out = inspect.getsource(TelegramListener.handle_outgoing_message)
    assert "_get_workspace_sender_tg_ids" in inc, "inbound internal short-circuit missing"
    assert "_get_workspace_sender_tg_ids" in out, "outgoing internal short-circuit missing"
```

Pair this with a **behavioural** test: feed a fake inbound event whose `sender.id` is a known workspace-sender `telegram_id`, assert `schedule_ai_response` is NOT called (patch it with `AsyncMock`) and zero rows land in `conversations`/`messages`. Plus an analytics test mirroring the existing `test_internal_warmup_conversation_excluded` (already in `test_phase5_analytics*.py`) to lock `_EXCLUDE_INTERNAL_CLAUSE`.

### Pattern 4: Restriction-gated pool selection (D-14, RESV-05 model)

**What:** Skip senders that are restricted/frozen or have a future `restricted_until` from warmup selection.

**Source pattern:** `app/services/contact_check_worker.py:259-268`:
```sql
-- Source: app/services/contact_check_worker.py:259-268 (RESV-05/D-11)
AND restriction_status = 'none'
AND lifecycle_status <> 'paused'
AND (restricted_until IS NULL OR restricted_until <= NOW())
```

**Apply to two places** (both currently filter only `lifecycle_status='active' AND auth_status='ok'`):
- `warmup.py:178-189` `_get_active_pool` (selection of who warms)
- `listener.py:574-582` `_refresh_warmup_cache` (the warmup-skip set) — keep consistent so a restricted account that stops warming also stops being treated specially; but note the **new** workspace-sender internal-set in Pattern 1 is intentionally NOT restriction-gated (isolation must hold even for restricted accounts).

Also gate `_process_session` eligibility (`warmup.py:302`) the same way (it currently checks only lifecycle+auth via `is_eligible`).

### Anti-Patterns to Avoid
- **Phone-based isolation** — the original leak. Never reintroduce a phone branch as the primary signal; phone is `"unknown"` whenever the counterparty hides it.
- **Pool-membership-based isolation** — D-01 forbids it; an internal pair not in `warmup_pool` must still be dropped.
- **Read-time analytics filtering as the only defense** — D-02 mandates write-side prevention (never create the rows). `_EXCLUDE_INTERNAL_CLAUSE` stays as defense-in-depth but is not the primary mechanism.
- **Restarting the worker to stop warmup** — D-06 forbids it; use the `warmup_enabled` flag read per tick.
- **Routing warmup through `message_queue`** — D-03 forbids it; would burn campaign rate-limits.
- **Touching `queue.py` empirical constants** — CLAUDE.md guard; warmup is a separate path.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Workspace auth / JWT verify | New auth check in warmup router | `app/utils/auth.py::auth_dep` + `AuthCtx` | ES256/JWKS + HS256 + API-key + token cache already solved; reinventing risks a tenant leak |
| Restriction-skip query | New restriction logic | Copy `contact_check_worker.py:259-268` clause | RESV-05 already calibrated the exact predicate incl. future `restricted_until` |
| Internal-traffic detection | A second phone/heuristic scheme | `telegram_id ∈ workspace senders` set | This is the diagnosed root-cause fix; anything fuzzier re-opens the leak |
| Settings upsert | Bespoke insert/update branching | `INSERT ... ON CONFLICT (workspace_id) DO UPDATE` | Idempotent, matches migration-applier + auth workspace-create patterns |
| Direct Telethon send w/ FloodWait | New send wrapper | `warmup._send_via_telethon` (already handles FloodWait, blocked, RPC, resolve) | Working; D-03 path; preserve |
| Daily-limit pacing | New scheduler | `LEVEL_CONFIG` + `_last_sent_at` cross-session pacing guard | Already enforces ≥MIN_DELAY between a sender's messages |

**Key insight:** Every sub-problem in this phase already has a *deployed, tested* solution elsewhere in this same repo. The phase's job is to reuse them consistently and close two specific gaps (deterministic isolation; restriction-gated selection), not to invent.

## Storage Recommendation (D-06/D-10 — discretion resolved)

**Recommend: a new `warmup_settings` table keyed by `workspace_id`, migration `037_warmup_settings.sql`.**

Next free migration number is **037** (verified: `036_checker_probe_burn.sql` is the highest in `migrations/`).

```sql
-- migrations/037_warmup_settings.sql  (idempotent, auto-applied)
BEGIN;

CREATE TABLE IF NOT EXISTS warmup_settings (
    workspace_id   UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    enabled        BOOLEAN NOT NULL DEFAULT FALSE,           -- D-06 master switch (default OFF: new tenants don't auto-warm)
    topics         JSONB   NOT NULL DEFAULT '[]'::jsonb,     -- D-10 empty = use code default WARMUP_TOPICS
    system_prompt  TEXT,                                      -- D-10 NULL = use code default WARMUP_SYSTEM_PROMPT
    language       TEXT    NOT NULL DEFAULT 'ru',             -- D-10 (tone/lang)
    tone           TEXT,                                      -- D-10 optional tone override
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
```

**Default semantics (critical — D-10):** absence of a row OR empty `topics`/NULL `system_prompt` MUST resolve to the current hard-coded `WARMUP_TOPICS` (24 RU topics, `warmup.py:41`) and `WARMUP_SYSTEM_PROMPT` (`warmup.py:68`). Implement this as a read-helper in `warmup.py` that COALESCEs to the constants, so existing behaviour is byte-identical when nothing is configured.

**`enabled` default = FALSE** is the safe choice: D-13 says new accounts are not auto-enrolled, and the master switch starting OFF means no surprise traffic for a workspace that hasn't opted in. (If the planner wants existing-behaviour parity for the single live workspace, a one-line `INSERT ... ON CONFLICT DO NOTHING` seed for the current workspace can set `enabled=true` — flag this as a decision for the planner, not a silent default.)

**Worker honoring the flag (D-06):** `_get_active_pool` (`warmup.py:165`) JOINs `warmup_settings ws ON ws.workspace_id = wp.workspace_id AND ws.enabled = true` (LEFT JOIN + `COALESCE(ws.enabled, false)` if you want "no row = off"). The worker stays a single global tick; the SQL filter drops disabled workspaces. `_create_new_sessions` (`warmup.py:445`) already partitions by `workspace_id`, so disabled workspaces simply produce no pairs.

**ORM:** add a `WarmupSettings` model to `app/models.py` (the file referenced by `Base.metadata.create_all`; note `app/models.py` is imported as a package — confirm path during planning, the import in routers is `from app.models import ...`).

## Common Pitfalls

### Pitfall 1: `sender_info` lacks `workspace_id` at handler entry
**What goes wrong:** The D-01 isolation decision needs the listening sender's workspace, but `_load_active_senders` (`listener.py:405-426`) doesn't SELECT it.
**Why it happens:** Historical — warmup isolation borrowed the pool cache instead of being workspace-aware.
**How to avoid:** Add `workspace_id` to the SELECT and to `sender_info` first. Every downstream short-circuit reads `sender_info["workspace_id"]`. (Note: outgoing/bot handlers at `listener.py:1080,1109` already read `sender_info["workspace_id"]` — so it IS available in *some* paths; verify it is populated consistently for ALL handler entry points, not just where conversations are written.)
**Warning sign:** `KeyError: 'workspace_id'` in the inbound handler, or the cache lookup silently returning empty set → leak.

### Pitfall 2: Stale cache re-opens the leak
**What goes wrong:** A newly-onboarded sender's `telegram_id` isn't in the cache yet (60s TTL), and during that window its internal traffic leaks.
**Why it happens:** `telegram_id` is written lazily on listener connect (`listener.py:1271-1279`), and the cache has a TTL.
**How to avoid:** Keep TTL short (reuse 60s); the deterministic guard test proves the *mechanism* is present, but acknowledge the bounded window. For absolute determinism, the planner may consider a per-message `SELECT EXISTS(... senders WHERE workspace_id=:wid AND telegram_id=:cid)` on cache-miss only. Document the chosen tradeoff.
**Warning sign:** Internal rows appearing in `conversations` immediately after onboarding a new account into a warming workspace.

### Pitfall 3: Workspace-scoping breaks existing response shapes
**What goes wrong:** The frontend (sibling repo, generated from `openapi.json`) depends on exact field names of `/pool`, `/stats`, `/sessions`. Adding scoping must not rename/drop fields.
**Why it happens:** Lovable client is generated; field drift = 422/runtime errors (see CLAUDE.md Lovable quirks).
**How to avoid:** Preserve every key in the response dicts (see §Don't-break-the-response-shape inventory). Only ADD fields (`restriction_status`, `restricted_until`, last-error for D-11). Regenerate `lovable-handoff/openapi.json` via the export-handoff flow (rebuild API container first), no hand-editing — same as Phase 10/11/14.
**Warning sign:** `tsc` errors in sibling repo; missing-field runtime errors in the warmup tab.

### Pitfall 4: Migration not idempotent → api won't start
**What goes wrong:** A non-idempotent `037` fails on re-apply, and fail-fast keeps the whole api down (CLAUDE.md).
**How to avoid:** `CREATE TABLE IF NOT EXISTS`, `ON CONFLICT DO NOTHING` for any seed. Test round-trip via test-overlay before deploy.
**Warning sign:** api container restart loop after deploy; `_apply_migrations` raises.

### Pitfall 5: Cross-tenant pair leak in pairing
**What goes wrong:** Senders from different workspaces get paired and message each other.
**Why it happens:** A pairing bug.
**How to avoid:** Already guarded — `_create_new_sessions` partitions by `workspace_id` and asserts (`warmup.py:480-498`). Do NOT remove that assertion when adding the `enabled` filter.

## Code Examples

### Read settings with code-default fallback (D-10)
```python
# In warmup.py — COALESCE to existing constants so empty settings = current behaviour.
async def _get_warmup_content(self, db, workspace_id: str) -> tuple[list[str], str]:
    row = (await db.execute(text("""
        SELECT topics, system_prompt FROM warmup_settings WHERE workspace_id = :wid
    """), {"wid": workspace_id})).fetchone()
    topics = (row[0] if row and row[0] else None) or WARMUP_TOPICS          # 24 RU defaults
    prompt = (row[1] if row and row[1] else None) or WARMUP_SYSTEM_PROMPT
    return topics, prompt
```

### Master toggle endpoint (D-06 — discretion shape)
```python
# app/routers/warmup.py — workspace-scoped enable/disable.
@router.put("/settings")
async def update_settings(
    body: WarmupSettingsUpdate,                    # pydantic: enabled?, topics?, system_prompt?, language?, tone?
    db: AsyncSession = Depends(get_db),
    ctx: AuthCtx = Depends(auth_dep),
):
    await db.execute(text("""
        INSERT INTO warmup_settings (workspace_id, enabled, topics, system_prompt, language, tone, updated_at)
        VALUES (:wid, :enabled, :topics, :prompt, :lang, :tone, NOW())
        ON CONFLICT (workspace_id) DO UPDATE SET
            enabled = EXCLUDED.enabled, topics = EXCLUDED.topics,
            system_prompt = EXCLUDED.system_prompt, language = EXCLUDED.language,
            tone = EXCLUDED.tone, updated_at = NOW()
    """), {...})
    await db.commit()
    return {"status": "saved"}
```

## Don't-break-the-response-shape: existing endpoint inventory (D-05)

All currently on `verify_api_key`; rewrite to `auth_dep` + `workspace_id` filter, **preserving these response keys verbatim**:

| Method | Path | Response keys (must preserve) |
|--------|------|-------------------------------|
| GET | `/api/v1/warmup/pool` | `senders[]`: `id, slug, name, phone, is_active, in_pool, warmup_active, enrolled_at, enrolled_days, level, sent_today` — **ADD** `restriction_status, restricted_until, last_warmup_error?` (D-11) |
| POST | `/api/v1/warmup/pool/{sender_id}` (201) | `status, sender_id, slug` — **ADD** workspace-ownership 404 |
| DELETE | `/api/v1/warmup/pool/{sender_id}` (204) | (no body) — **ADD** workspace-ownership 404 |
| PATCH | `/api/v1/warmup/pool/{sender_id}/toggle` | `sender_id, warmup_active` |
| GET | `/api/v1/warmup/stats` | `active_accounts, active_sessions, messages_today, sessions_completed_today, accounts[]: {slug, name, sent_today, enrolled_days, level}` |
| GET | `/api/v1/warmup/sessions?status=` | `sessions[]: {id, topic, status, messages_sent, target_messages, next_message_at, created_at, updated_at, sender_a, sender_b, progress_pct}` |
| GET | `/api/v1/warmup/sessions/{id}` | `id, topic, status, messages_sent, target_messages, progress_pct, next_message_at, created_at, updated_at, sender_a{slug,name}, sender_b{slug,name}` |
| GET | `/api/v1/warmup/sessions/{id}/messages` | `total, messages[]: {id, text, sent_at, from_slug, from_name, direction}` |

**New endpoints to add (D-06/D-10):** `GET /api/v1/warmup/settings` (read settings + resolved defaults), `PUT /api/v1/warmup/settings` (master toggle + content). The UI-SPEC names the master CTA and the settings save — align endpoint names within the Phase 3/4/5 convention.

**Note:** `/pool` currently SELECTs `s.is_active` (`warmup.py` router line 47-48) — but `senders.is_active` was DROPPED in migration 013 (per STATE.md Phase 02). **This is a latent bug**: the live query references a non-existent column and would error, OR the column still exists in prod from incomplete drop. The planner MUST verify whether `senders.is_active` exists before the rewrite and remove/replace the reference (use `lifecycle_status`/derived status instead). Flagged as Open Question 2.

## State of the Art — why the old feature conflicted (WARM-15)

| Old approach (`telegram-api`) | Current approach (aimly) | When changed | Impact |
|-------------------------------|--------------------------|--------------|--------|
| Inbound warmup filter **phone-only** (`telegram-api/listener.py:553-555`, no `telegram_id` branch) | Inbound has a `telegram_id` primary branch + phone fallback (`listener.py:689-697`) | aimly fork + 2026-06-25 fix | Phone-only leaked at `phone="unknown"` → 5382 fake `sent`, AI triggered on our own accounts |
| Isolation scoped to `warmup_pool` membership | Still pool-scoped (the gap D-01 closes) | this phase | Internal pairs not in the pool, or stale cache, still leak today |
| No analytics exclusion | `_EXCLUDE_INTERNAL_CLAUSE` (`analytics.py:135`) excludes `contact_telegram_id ∈ workspace senders` | 2026-06-25 fix | Read-time defense-in-depth; D-02 adds write-side prevention |
| Both `telegram-api` + aimly listeners on the SAME 13 accounts | `telegram-api` STOPPED (`restart: "no"`) | 2026-06-24 | Running both = duplicate AI replies + the original pollution; do NOT restart telegram-api |

**Deprecated/outdated:**
- `telegram-api` warmup (`/root/apps/telegram-api/app/services/warmup.py`, `bot_chat.py`) — reference only, do not run. Its `bot_chat.py` is a *different* feature (synchronous bot Q&A on checker accounts) and is NOT part of warmup — note its docstring explicitly relies on "listener only listens on role='sender'" for isolation, a fragile assumption this phase replaces with the telegram_id rule.

## Runtime State Inventory

Warmup is enabled/disabled via DB flag and writes only to `warmup_*` tables; this phase is code + one migration. Still, the rename/state checklist:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `warmup_pool`/`warmup_sessions`/`warmup_messages` already carry `workspace_id` (mig 012). Historical 26 polluted `conversations` (5327 msgs) from the 2026-06-23/24 incident remain in prod but are filtered by `_EXCLUDE_INTERNAL_CLAUSE` (left in place by the debug-resolution; NOT deleted). | None required for the data; the new write-side guard prevents *new* pollution. The planner may optionally propose a one-time quarantine of the 26 historical dialogs — out of scope unless requested. |
| Live service config | None — warmup has no external service registration. n8n integrations use `X-Workspace-Key`, which `auth_dep` already supports (no change). | None. |
| OS-registered state | None — `WarmupWorker` is an in-process asyncio task started in `app/main.py:55`, stopped at `:72`. No cron/systemd/Task Scheduler. | None. |
| Secrets/env vars | `OPENAI_API_KEY` (read by `AsyncOpenAI()` in `warmup.py:99`) and `openai_model` (config.py:57) already exist. New `warmup_settings` content lives in DB, not env. | None — unless the planner adds env knobs for warmup pacing (would follow the `CONTACT_CHECK_*` pattern in `config.py:95+`). |
| Build artifacts / installed packages | None — no package rename, no egg-info. | `docker compose up -d --build api` (applies mig 037) + `... listener` (picks up isolation code) on deploy. |

## Common Pitfalls — deployment

- The isolation fix lives in **both** `api` (router/migration) and `listener` (short-circuit). Deploy requires rebuilding **both** containers: `docker compose up -d --build api && docker compose up -d --build listener`. A partial deploy (api only) leaves the listener leaking.
- Tests run ONLY via test-overlay. The isolation behavioural test must not connect to real Telegram — patch `event`/`get_me` and `schedule_ai_response`.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL 16 | `warmup_settings` table, all queries | ✓ | 16 (container `outreach-platform-db`) | — |
| OpenAI API | warmup message generation | ✓ (key in env) | SDK >=1.40,<2 | — |
| Telethon / Telegram MTProto | direct warmup send (D-03) | ✓ (existing sessions) | 1.42.0 | — |
| Test overlay (`docker-compose.test.yml`) | running pytest safely | ✓ | — | none — mandatory |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** None.

## Validation Architecture

`nyquist_validation` is **enabled** (`.planning/config.json` → `workflow.nyquist_validation: true`).

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`pytestmark = pytest.mark.asyncio`) |
| Config file | `tests/conftest.py` (DSN guard at lines 46-77; `_setup_database`) |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_warmup_isolation.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| WARM-01 | Internal sender tg_id detected from workspace senders (not pool) | unit/integration | `pytest tests/test_warmup_isolation.py::test_internal_detected_by_workspace_telegram_id -x` | ❌ Wave 0 |
| WARM-02 | Internal inbound → no `conversations`/`messages` row, no `schedule_ai_response` | integration | `pytest tests/test_warmup_isolation.py::test_internal_inbound_no_dbwrite_no_ai -x` | ❌ Wave 0 |
| WARM-02 | Analytics `_EXCLUDE_INTERNAL_CLAUSE` still excludes internal | integration | `pytest tests/test_phase5_analytics.py::test_internal_warmup_conversation_excluded -x` | ✅ (exists, keep green) |
| WARM-04 | Source-introspection guard: short-circuit wired in both handlers | unit | `pytest tests/test_warmup_isolation.py::test_shortcircuit_wired -x` | ❌ Wave 0 |
| WARM-05 | Router workspace-scoped: cross-workspace pool/sessions invisible | integration | `pytest tests/test_warmup_router.py::test_pool_workspace_scoped -x` | ❌ Wave 0 |
| WARM-05 | Existing response shapes preserved | integration | `pytest tests/test_warmup_router.py::test_response_shapes_preserved -x` | ❌ Wave 0 |
| WARM-06 | Disabled workspace produces no new sessions | integration | `pytest tests/test_warmup_worker.py::test_disabled_workspace_skipped -x` | ❌ Wave 0 |
| WARM-10 | Empty settings resolve to 24 RU topics + default prompt | unit | `pytest tests/test_warmup_worker.py::test_content_defaults_when_empty -x` | ❌ Wave 0 |
| WARM-14 | Restricted/frozen/future-restricted sender excluded from pool selection | integration | `pytest tests/test_warmup_worker.py::test_restricted_sender_excluded -x` | ❌ Wave 0 |
| migration 037 | Idempotent re-apply | integration | (runs in `_setup_database`; round-trip via existing migration-test DB) | ✅ harness |

### Sampling Rate
- **Per task commit:** `pytest tests/test_warmup_isolation.py tests/test_warmup_router.py tests/test_warmup_worker.py -x` (via test-overlay)
- **Per wave merge:** full suite (`pytest`) — baseline is GREEN (786 passed as of 2026-06-29, MEMORY.md), so regressions are unambiguous.
- **Phase gate:** Full suite green before `/gsd:verify-work`.

### Wave 0 Gaps
- [ ] `tests/test_warmup_isolation.py` — covers WARM-01, WARM-02, WARM-04 (the core risk; build RED first)
- [ ] `tests/test_warmup_router.py` — covers WARM-05 (workspace scoping + shape preservation)
- [ ] `tests/test_warmup_worker.py` — covers WARM-06, WARM-10, WARM-14
- [ ] Shared fixtures: a two-sender-same-workspace factory + a fake-inbound-event helper (patch `get_me`, `get_sender`, `schedule_ai_response` with `AsyncMock`). Reuse `async_db_session` + workspace/sender factories already in `conftest.py`.
- Framework install: none — pytest infra exists.

## Open Questions

1. **D-11 "last warmup error / activity" source.**
   - What we know: `warmup_messages.sent_at` gives last successful activity; `senders.restriction_status`/`restricted_until` give the "why paused" reason for restricted accounts.
   - What's unclear: There is no durable per-sender *warmup send-error* log today (`_send_via_telethon` logs but does not persist a last-error). FloodWait/blocked/RPC failures are not stored.
   - Recommendation: Either (a) derive the displayed reason from `restriction_status` + last `sent_at` (cheap, no schema change, covers the common "why not warming" cases), or (b) add a nullable `last_warmup_error TEXT` + `last_warmup_error_at` to `warmup_pool` in mig 037 if the user wants raw send-error visibility. Default to (a) for v1 unless the planner/user wants (b); the UI-SPEC restriction-reason copy (D-11) is satisfied by (a).

2. **`senders.is_active` referenced in `/pool` query but DROPPED in migration 013.**
   - What we know: STATE.md says "Migration 013 drops senders.is_active"; but `app/routers/warmup.py:47-48` and the SELECT alias still reference `s.is_active`. The warmup router is on legacy `verify_api_key` and may not have been exercised since the drop.
   - What's unclear: whether the column physically still exists in prod (drop may have been incomplete) or the endpoint currently 500s.
   - Recommendation: Planner must run `\d senders` / a column check at plan time and remove/replace `s.is_active` (use derived status / `lifecycle_status`) during the D-05 rewrite. Do not assume it works.

3. **Seed `enabled=true` for the existing live workspace?**
   - What we know: `warmup_settings.enabled` defaults FALSE; the single live workspace currently has warmup behaviour implicitly "on" via the always-running worker + pool membership.
   - Recommendation: Surface to the user/planner — either seed the live workspace `enabled=true` (preserve current behaviour) or treat Phase 15 as "warmup is now opt-in, the user flips it on in the new tab." The UI-SPEC's master toggle makes opt-in natural; recommend the latter (explicit opt-in) but make it a planner decision, not a silent default.

## Sources

### Primary (HIGH confidence — live codebase)
- `app/services/warmup.py` (engine: `_get_active_pool:165`, `_process_session:264`, `_send_via_telethon:571`, `LEVEL_CONFIG:33`, `WARMUP_TOPICS:41`, `WARMUP_SYSTEM_PROMPT:68`, `_is_working_hours:144`)
- `app/routers/warmup.py` (all 8 endpoints, current `verify_api_key`, response shapes)
- `app/services/listener.py` (`_load_active_senders:405`, `handle_incoming_message:605`, skip-self:621, inbound warmup filter:689, `_refresh_warmup_cache:561`, `handle_outgoing_message:1123`, outgoing filter:1138, `telegram_id` persist:1271, `WARMUP_CACHE_TTL:139`)
- `app/routers/analytics.py` (`_EXCLUDE_INTERNAL_CLAUSE:135`, applied at 177/383)
- `app/utils/auth.py` (`auth_dep:179`, `AuthCtx:170`, dual JWT/API-key)
- `app/routers/senders.py` (workspace-scoped router header pattern)
- `app/services/contact_check_worker.py:259-268` (RESV-05 restriction-skip clause)
- `tests/test_queue_even_pacing.py:290` (`_assert_pacing_predicate_wired` introspection-guard pattern)
- `migrations/005_warmup.sql`, `migrations/012_workspace.sql` (warmup tables + workspace_id), migration list (037 is next free)
- `.planning/debug/dashboard-analytics-warmup-pollution.md` (full root-cause + applied fix)
- `requirements.txt` (pinned versions), `app/config.py:91-149` (env-knob pattern), `app/main.py:55,72` (worker lifecycle)
- `/root/apps/telegram-api/app/services/listener.py:553-555`, `bot_chat.py` (reference: phone-only inbound filter = the leak)
- `/root/CLAUDE.md`, `/root/apps/aimly/tg-outreach/CLAUDE.md`, `.planning/STATE.md`, MEMORY.md

### Secondary (MEDIUM)
- None — no external sources needed.

### Tertiary (LOW)
- None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — internal-only, versions read from `requirements.txt`; no new deps.
- Architecture (isolation/scoping/storage): HIGH — every pattern has a deployed precedent in this repo; root cause is fully documented.
- Pitfalls: HIGH — two (is_active drop, sender_info missing workspace_id) found directly in source; both flagged as Open Questions for plan-time verification.

**Research date:** 2026-06-29
**Valid until:** 2026-07-29 (stable internal stack; re-verify only if `warmup.py`/`listener.py`/migrations change before planning)

**Skills check:** No `.claude/skills/` or `.agents/skills/` directory present; no project skill rules to load.
