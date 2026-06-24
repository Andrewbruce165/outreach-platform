# Phase 10: Pool Visibility & Restriction Audit - Research

**Researched:** 2026-06-24
**Domain:** Brownfield FastAPI + SQLAlchemy 2.0 async + raw-SQL migrations; durable append-only audit log; computed-field campaign response; mini frontend (separate repo)
**Confidence:** HIGH (all findings verified against live code at the exact insertion points; no external library research needed — this phase adds one table + write-hooks + computed fields using patterns already in the codebase)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** «Событие» = смена состояния restriction (`none→spam_limited`, `→frozen`, `→banned`, `→cleared`) **И** продление срока (когда @SpamBot-reconcile сдвинул `restricted_until` вправо). Рядовые reconcile-тики «still limited» **без** сдвига срока событий НЕ порождают (только обновляют `senders.restricted_until`). Цель: чистая хронология без шума 37-тиков-в-сутки.
- **D-02:** Каждое restriction-событие хранит минимум: `sender_id`, тип, источник (`queue_error` / `spambot_reconcile`), `restricted_until` на момент события, сырой текст ошибки/ответа @SpamBot, server timestamp. Append-only, не затирается.
- **D-03:** Ошибки уровня **получателя** (`PRIVACY_PREMIUM_REQUIRED` и подобные privacy) логировать **отдельным классом** через явное поле категории (рабочее: `category='recipient_privacy'` vs `category='restriction'`). Это НЕ ограничение аккаунта; **обязаны** быть исключаемы из restriction-аналитики одним фильтром. Не смешивать классы.
- **D-04 (discretion):** точное имя/значения поля категории и полный перечень не-restriction классов — research/plan. Интент: один столбец, по которому restriction и не-restriction события разделяются без эвристик.
- **D-05:** Срез снимается **снапшотом в момент записи события**, НЕ вычислять позже из `messages_log` (исходные данные эфемерны).
- **D-06:** Обязательные поля среза (все четыре): (1) отправки 1ч/24ч до события; (2) уникальные новые контакты за окно; (3) прокси на момент события (`senders.proxy`); (4) фактический темп vs настроенные лимиты.
- **D-07 (discretion):** точные SQL-окна и источник для каждого поля, формат хранения среза (колонки vs JSONB), как выразить «фактический темп» — research/plan.
- **D-08:** В ответе кампании — и агрегат, и пер-sender: агрегат-объект `pool_health` `{active, paused, total, earliest_resume_at}`; обогатить каждый `attached_senders[]` полями `restriction_status` / `restricted_until`.
- **D-09:** Бейдж пула — 3 состояния: 🟢 весь пул активен; 🟡 частичная пауза (K из N); 🔴 весь пул на паузе.
- **D-10 (discretion):** точная форма/имена `pool_health`, где считается (один проход в `_campaign_to_response`), нейминг — следовать стилю computed-полей.
- **D-11:** В этой фазе: полный бэкенд (event-log + срез + эндпоинт истории по аккаунту) + **мини-UI** (список событий на странице аккаунта + бейдж пула на странице кампании). Агрегат-дашборд → backlog.

### Claude's Discretion
- Схема таблицы event-log (имя, колонки vs JSONB для среза, индексы), миграция (raw SQL `NNN_*.sql`, идемпотентная).
- Точные SQL-окна среза, форма «фактического темпа».
- Имена/форма `pool_health` и категории событий.
- Транзакционные границы записи события (внутри того же UPDATE, что меняет `restriction_status`).
- Деривация требований фазы (pool-visibility reqs, TBD в ROADMAP).

### Deferred Ideas (OUT OF SCOPE)
- **Агрегат-дашборд restriction** (флуд по дням, графики, % пула под ограничением во времени, тренды) → backlog.
- **Real-time алерты по банам/ограничениям** — non-goal блока.
- **Расширенная корреляция прокси↔баны** (агрегаты по прокси) — данные снимаются (D-06.3), аналитика поверх — backlog.
</user_constraints>

<phase_requirements>
## Phase Requirements

Existing IDs (REQUIREMENTS.md L132-134) PLUS derived pool-visibility IDs (ROADMAP marks these "TBD on plan"; derived here the same way Phases 7/8/9 derived theirs). Proposed new IDs: **POOLV-01..04** (pool-visibility) — kept distinct from `POOL-xx` (Phase 8 pool management) to avoid collision.

| ID | Description | Research Support |
|----|-------------|------------------|
| **HLTH-01** | Durable, append-only event-log of every account restriction warning/limit (`spam_limited`/`frozen`/`flood_wait`/`cleared`/`banned`). Each event stores sender, type, source (`queue_error`/`spambot_reconcile`), `restricted_until`-at-event, raw error/@SpamBot text, server_ts. Append-only (not overwritten like `message_queue.error_message`). | New table `sender_restriction_events` (§Standard Stack). Write-points already exist and are inventoried (§Architecture: Write-Point Inventory): queue.py PEER_FLOOD/ACCOUNT_FROZEN/FLOOD_WAIT, listener.py antispam + reconcile cleared/extended/banned. Raw text source confirmed: queue `error.get("message")`, SpamBot `result["raw_text"]`. |
| **HLTH-02** | Each restriction event carries a snapshot of the sender's preceding activity: sends 1h/24h before event, unique new contacts in window, proxy at event time, actual send rate vs configured limits. | Snapshot computed at write time from `messages_log` (`sender_id` + `created_at` + `message_type`; index `idx_messages_log_sender_id` exists). Proxy from `senders.proxy` JSONB. Configured limits from `senders.rate_per_min/hour/day`. Exact SQL windows + storage form proposed (§Activity-Slice Design). |
| **HLTH-03** | Team visibility: per-account event history endpoint + (aggregate dashboard DEFERRED to backlog per D-11). | Read endpoint on senders router keyed by slug (mirrors `GET /senders/{slug}/spambot-check` pattern). Aggregate dashboard explicitly out of scope. |
| **POOLV-01** *(derived)* | `CampaignResponse` exposes an aggregate `pool_health` object `{active, paused, total, earliest_resume_at}` computed in one pass in `_campaign_to_response`. | `_campaign_to_response` (campaigns.py:230) + `CampaignResponse` (schemas:685) — same computed-field pattern as `attached_senders`/`is_exhausted`. |
| **POOLV-02** *(derived)* | Each `attached_senders[]` entry is enriched with `restriction_status` + `restricted_until`. | `CampaignSenderAttach` (schemas:574) + `_build_attached_senders` (campaigns.py:196). `SenderResponse` (schemas:133-134) already carries these two fields verbatim — reuse the names/types. |
| **POOLV-03** *(derived)* | Frontend campaign-page pool badge with 3 states (green=all active, yellow=K/N partial pause, red=all paused), reading `pool_health`. Separate repo `aimly-tg-outreach`. | Mirrors Phase 8 cross-repo panel pattern (08-04). |
| **POOLV-04** *(derived)* | Frontend account-page mini event-list reading the HLTH-03 history endpoint. | Separate repo, read-only list. |
</phase_requirements>

## Summary

This is a **pure-additive brownfield phase**: one new append-only table, write-hooks at restriction state-change points that already exist, two computed fields on the campaign response, one read endpoint, and a mini cross-repo UI. **No external libraries, no version research** — everything uses patterns already present in the codebase (raw-SQL idempotent migrations auto-applied at api start; computed `@property` fields on Pydantic response models; `text()` async SQL).

The five restriction write-points are confirmed and stable (Phase 7/9 already converged them): **queue.py** has PEER_FLOOD (L733), ACCOUNT_FROZEN (L783) and FLOOD_WAIT-hard (L704) blocks; **listener.py** has `_handle_antispam_signal` (L881) and the restriction-reconcile tick (`_restriction_reconcile_tick`, L1360) with three terminal branches: `cleared` (free, L1403), `banned` (suspended, L1420), `extended` (still-limited, L1427). D-01's "event only on state-change OR date-shift" maps cleanly onto these branches — the trick is that the `extended` branch **today always writes `restricted_until`** even when the new value differs by milliseconds; the event-write must compare old vs new `restricted_until` and only emit on a real forward shift.

The activity slice (HLTH-02) is sourced from `messages_log` (columns `sender_id`, `recipient_phone`, `message_type` ∈ {`sent`,`failed`}, `created_at` — **not** `timestamp`). A `sender_id` index exists. The slice is computed and stored **inline at event-write time** (D-05) — recommend JSONB column for the four slice fields plus a flat `proxy` snapshot.

**Primary recommendation:** Add migration `030_sender_restriction_events.sql` creating `sender_restriction_events` (event columns + `category` discriminator + `activity_slice` JSONB). Add one `record_restriction_event(...)` helper in a new `app/services/restriction_audit.py` that both writes the event and computes the slice in the **same session/transaction** as the `restriction_status` UPDATE (transactional boundary requirement). Wire it into the five write-points. Add `pool_health` + per-sender enrichment to `_campaign_to_response`. Add `GET /senders/{slug}/restriction-events`. Mini UI in the sibling repo.

## Standard Stack

This phase introduces **no new third-party dependencies**. The "stack" is the existing internal toolset.

### Core
| Component | Version | Purpose | Why Standard |
|-----------|---------|---------|--------------|
| FastAPI | (installed) | Read endpoint + response schema | Already the API framework |
| SQLAlchemy 2.0 async + `text()` | (installed) | Event-write + slice SQL + computed-field queries | Every restriction write-point already uses `text()` raw SQL with `AsyncSessionLocal` |
| asyncpg | (installed) | Migration applier DSN | `app/database.py::_apply_migrations` |
| Pydantic v2 `computed_field` | (installed) | `pool_health` + `attached_senders` enrichment | `CampaignResponse.id`/`is_exhausted`/`attached_senders` already use this exact pattern |
| Raw-SQL idempotent migration | n/a | `030_sender_restriction_events.sql` | CLAUDE.md hard rule — never Alembic; auto-applied at api start |
| pytest + pytest-asyncio (`asyncio_mode=auto`, session loop scope) | (installed) | Validation | All 90+ test files; run ONLY via test-overlay |

### Supporting
| Component | Purpose | When to Use |
|-----------|---------|-------------|
| `messages_log` table | Activity-slice source | Counting sends 1h/24h, unique recipients, actual rate at event time |
| `senders.proxy` (JSONB), `senders.rate_per_min/hour/day` | Slice fields 3 & 4 | Proxy-at-event snapshot; configured-limit denominator |
| `check_spambot()` `result["raw_text"]` | Raw @SpamBot text for `spambot_reconcile` events | listener reconcile + antispam |
| `error.get("message")` from `telegram.py` send result | Raw error text for `queue_error` events | queue PEER_FLOOD/FROZEN/PRIVACY |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| JSONB `activity_slice` | 4 flat columns (`sends_1h`, `sends_24h`, `unique_contacts`, `actual_rate`) | Flat columns are queryable/indexable; JSONB is forward-flexible. **Recommend JSONB** — D-11 defers all aggregation/dashboard, so query-ability is not needed in v1; JSONB lets the slice grow without migrations. Keep `proxy` as its own JSONB column for direct correlation later. |
| New `restriction_audit.py` helper | Inline event-write at each call-site | A shared helper enforces DRY across 5 call-sites and guarantees the slice is computed identically. **Recommend helper** (mirrors `failover_cold_backlog` pattern: `db=None` opens own session, `db` passed = transaction-neutral). |
| Reuse `telemetry_events` | Dedicated `sender_restriction_events` | `telemetry_events` is UI-event-only and append-with-whitelist; mixing restriction audit there violates D-02/D-03 separation. **Recommend dedicated table.** |

**Installation:** none.

**Version verification:** N/A — no new packages. Confirmed the existing toolchain in `pyproject.toml` (pytest-asyncio `asyncio_mode=auto`, session-scoped loop) and `app/database.py` (asyncpg applier).

## Architecture Patterns

### Recommended Project Structure (additions only)
```
migrations/
└── 030_sender_restriction_events.sql   # new table (idempotent, auto-applied)

app/
├── models/__init__.py                  # + SenderRestrictionEvent ORM model
├── services/
│   └── restriction_audit.py            # NEW: record_restriction_event(...) + slice computation
├── services/queue.py                   # wire 3 write-points (PEER_FLOOD, ACCOUNT_FROZEN, FLOOD_WAIT-hard)
├── services/listener.py                # wire antispam + reconcile (cleared/banned/extended-on-shift)
├── routers/
│   ├── campaigns.py                    # pool_health + per-sender enrichment in _campaign_to_response
│   └── senders.py                      # GET /senders/{slug}/restriction-events
└── schemas/__init__.py                 # PoolHealth model; CampaignSenderAttach +2 fields; CampaignResponse +pool_health; RestrictionEventResponse

# Separate repo: /root/apps/aimly/aimly-tg-outreach
#   - campaign page: 3-state pool badge (reads pool_health)
#   - account page: mini event-list (reads /senders/{slug}/restriction-events)
#   - lovable-handoff/openapi.json regen
```

### Write-Point Inventory (HLTH-01 / D-01 / D-02) — VERIFIED at current line numbers

> CONTEXT line numbers had drifted (Phase 9 added code). These are the **current** lines as of 2026-06-24.

| # | File:Line | Trigger | event type | source | restricted_until source | raw text source | category |
|---|-----------|---------|-----------|--------|--------------------------|-----------------|----------|
| 1 | `queue.py:733` | PEER_FLOOD | `spam_limited` | `queue_error` | `recheck_at` (set in same block) | `error_msg` = `error.get("message")` | `restriction` |
| 2 | `queue.py:783` | ACCOUNT_FROZEN | `frozen` | `queue_error` | `recheck_at` | `error_msg` | `restriction` |
| 3 | `queue.py:704` | HARD FloodWait (`retry_after >= FLOOD_HARD_THRESHOLD`) | `flood_wait` | `queue_error` | `reschedule_at` (the +retry_after pause; note: this path does NOT set `senders.restriction_status` — it only pauses the queue) | `error_msg` | `restriction` |
| 4 | `listener.py:881` `_handle_antispam_signal` | antispam bot signal | `spam_limited` | `queue_error` (or a distinct `antispam_signal` source — see Open Q) | `recheck_at` | `message_text` (the bot message) | `restriction` |
| 5a | `listener.py:1403` reconcile `verdict=='free'` | clear | `cleared` | `spambot_reconcile` | NULL (cleared) | `result["raw_text"]` | `restriction` |
| 5b | `listener.py:1420` reconcile `verdict=='suspended'` | ban | `banned` | `spambot_reconcile` | (unchanged) | `result["raw_text"]` | `restriction` |
| 5c | `listener.py:1427` reconcile `else` (limited/unknown) | **extension** | `extension` | `spambot_reconcile` | `next_at` | `result["raw_text"]` | `restriction` |

**D-01 nuance (CRITICAL):** branch 5c (`extended`) fires on **every** still-limited tick (37/day in the incident). D-01 says emit an event **only when `next_at` actually moves `restricted_until` forward** — not on every tick. The reconcile already loads `restriction_status` (L1377 SELECT); the SELECT must ALSO load the current `restricted_until` so the helper can compare `old != new` before emitting an `extension` event. The unconditional `UPDATE senders SET restricted_until = :next` stays; only the **event-write** is gated by the diff.

**D-03 recipient-privacy class (separate category):** `telegram.py` returns code `PRIVACY_RESTRICTED` for `UserNotMutualContactError` (queue.py:837 `_fail_item` catch-all path) and the gap note's `PRIVACY_PREMIUM_REQUIRED` (a 403 RPCError) currently falls into the generic `SEND_FAILED` branch. These are **recipient-level** — the account is healthy, `restriction_status` is NOT touched. If logged at all, they go in with `category='recipient_privacy'` and **never** flip a sender flag. See §Category Discriminator for the proposed enum. **Note:** the account-health write-points (1-5) are the HLTH-01 must-have; recipient-privacy logging is the D-03 separation guarantee — recommend logging it but in the explicitly-filterable separate category so restriction analytics never sees it.

### Pattern 1: Transactional event-write (transactional-boundary discretion)
**What:** The event row MUST land in the same transaction as the `restriction_status` UPDATE so state and audit can never diverge.
**When to use:** All five write-points.
**Implementation:**
- **queue.py PEER_FLOOD/FROZEN** open their own `AsyncSessionLocal()` (`db2`) and commit after the `senders` UPDATE. The event-write goes **inside that same `async with db2:` block, before `await db2.commit()`**.
- **listener antispam** already uses one `session` for pause+flag+failover with a single commit (L919-955) — add the event-write to that same session before `session.commit()` (L955). This mirrors how `failover_cold_backlog(sender_id, session)` is called transaction-neutral.
- **listener reconcile** opens a fresh `AsyncSessionLocal()` per sender per verdict (L1402). Add the event-write inside that same `async with db:` block before its `commit()`.

Therefore the helper should follow the **`failover_cold_backlog` dual-mode signature** (verified pattern, failover.py:87-114):
```python
# Source: app/services/failover.py:87-114 (verified live)
async def record_restriction_event(
    sender_id: UUID,
    event_type: str,            # 'spam_limited'|'frozen'|'flood_wait'|'cleared'|'banned'|'extension'
    source: str,                # 'queue_error'|'spambot_reconcile'
    restricted_until: datetime | None,
    raw_text: str | None,
    category: str = "restriction",
    db: AsyncSession | None = None,
) -> None:
    if db is None:
        async with AsyncSessionLocal() as own:
            await _record(..., own); await own.commit()
    else:
        await _record(..., db)   # caller commits
```

### Pattern 2: Activity-slice snapshot (HLTH-02 / D-05/D-06) — see §Activity-Slice Design

### Pattern 3: Computed pool_health (POOLV-01 / D-08/D-10)
**What:** One aggregate query over the campaign's attached senders, plus per-sender restriction fields, both built inside `_campaign_to_response`.
**Where:** `campaigns.py:230` `_campaign_to_response` already calls `_build_attached_senders` then constructs `CampaignResponse`. Extend `_build_attached_senders` to SELECT `s.restriction_status, s.restricted_until` (JOIN `senders s ON s.id = cs.sender_id`) and compute `pool_health` in the same pass (or a sibling query).
**Aggregate SQL (recommended, single pass):**
```sql
-- Source: pattern of _build_attached_senders (campaigns.py:200) + senders.restriction_status (model L93)
SELECT
  COUNT(*)                                            AS total,
  COUNT(*) FILTER (WHERE s.restriction_status = 'none') AS active,
  COUNT(*) FILTER (WHERE s.restriction_status <> 'none') AS paused,
  MIN(s.restricted_until) FILTER (WHERE s.restriction_status <> 'none') AS earliest_resume_at
FROM campaign_senders cs
JOIN senders s ON s.id = cs.sender_id
WHERE cs.campaign_id = :cid;
```
Map to `pool_health = {active, paused, total, earliest_resume_at}` (exact D-08 names). The 3-state badge (D-09) is derived **on the frontend** from these numbers: `paused==0` → green; `0<paused<total` → yellow; `paused==total && total>0` → red. (No badge-state field needed in the API; keep the response numeric and let UI decide — matches "UI decides presentation" elsewhere.)

### Anti-Patterns to Avoid
- **Emitting an `extension` event on every reconcile tick** — violates D-01, recreates the 37/day noise the phase exists to eliminate. Gate on a real forward shift of `restricted_until`.
- **Recomputing the slice later from `messages_log`** — violates D-05; `message_queue.error_message` is overwritten on reschedule and the slice's "actual rate at that instant" cannot be reconstructed. Snapshot at write time.
- **Mixing `recipient_privacy` into restriction analytics** — violates D-03. One `category` column, one filter.
- **Touching empirical rate-limit constants or the +24h queue pause** — CLAUDE.md hard rule. The slice's *configured* limits READ `senders.rate_per_min/hour/day`; do not change them.
- **Writing the event in a separate session from the status UPDATE** — they can diverge on crash. Same transaction (discretion decision, resolved here: same session).
- **Putting the audit write on the hot send path latency-critically** — it's one INSERT + a couple of COUNTs at a rare event (a freeze), not per-message. Acceptable inline.

## Activity-Slice Design (HLTH-02 / D-06 / D-07 — discretion resolved)

**Source table:** `messages_log` (verified columns: `sender_id` UUID, `recipient_phone` VARCHAR(40), `message_type` enum {`sent`,`failed`}, `created_at` TIMESTAMPTZ). Index `idx_messages_log_sender_id` exists (migration 010/019). **Use `created_at`, NOT `timestamp`** (there is no `timestamp` column — CONTEXT's "ts" = `created_at`).

> Source choice rationale: `message_queue` is mutable/transient (rows go back to `pending`, `error_message` overwritten). `messages_log` is the durable append-only record of actual send attempts — the correct slice source for "what the account was doing".

**Storage form (D-07):** one JSONB column `activity_slice` holding the four computed fields + a flat `proxy` JSONB column (own column so future proxy↔ban correlation is a direct query, not JSON extraction).

| Slice field (D-06) | SQL window (anchored at event time `:now`) | Source |
|--------------------|---------------------------------------------|--------|
| `sends_1h` | `COUNT(*) FROM messages_log WHERE sender_id=:sid AND message_type='sent' AND created_at >= :now - interval '1 hour'` | messages_log |
| `sends_24h` | same with `interval '24 hours'` | messages_log |
| `unique_new_contacts_1h` (and 24h) | `COUNT(DISTINCT recipient_phone) FROM messages_log WHERE sender_id=:sid AND message_type='sent' AND created_at >= :now - interval '<W>'` | messages_log. ("new" = distinct recipients sent-to in window; first-contact is the dominant PEER_FLOOD trigger per gap note.) |
| `proxy` (separate column) | `senders.proxy` JSONB at event time | senders |
| `actual_rate` | derived from `sends_1h` / 60 (msgs/min over the trailing hour) compared to `senders.rate_per_min` | computed |

**"Actual send rate" formula (D-07 resolved):** store both the measured and configured so the slice is self-describing:
```json
"rate": {
  "configured_per_min": 4, "configured_per_hour": 20, "configured_per_day": 150,
  "actual_per_hour": <sends_1h>, "actual_per_day": <sends_24h>,
  "actual_per_min_peak": <max sends in any 60s window over last hour — OPTIONAL, see note>
}
```
Recommend the simple, robust version: `actual_per_hour = sends_1h`, `actual_per_day = sends_24h`, plus the three configured limits. The per-minute peak is a window-function nicety (`COUNT() OVER` bucketed) — mark OPTIONAL; sends_1h/24h vs configured already shows "темп превышен". Keep it cheap (two COUNTs already computed above).

**Recommended `activity_slice` JSONB shape:**
```json
{
  "sends_1h": 12, "sends_24h": 140,
  "unique_contacts_1h": 12, "unique_contacts_24h": 138,
  "rate": { "configured_per_min": 4, "configured_per_hour": 20, "configured_per_day": 150,
            "actual_per_hour": 12, "actual_per_day": 140 }
}
```
`proxy` lives in its own `proxy JSONB` column (D-06.3). All computed in the helper, in the **same transaction**, BEFORE commit.

## Proposed Table & Schema (discretion — D-04/D-07/D-10 resolved)

### Migration `030_sender_restriction_events.sql` (idempotent, auto-applied)
```sql
-- 030: durable append-only restriction event-log (HLTH-01/HLTH-02).
-- Why: senders.restriction_status holds only CURRENT state; message_queue.error_message
-- is overwritten on reschedule; telemetry_events never records restriction changes;
-- container logs live ~18h. So "how often did this account hit PEER_FLOOD this month"
-- is unrecoverable today (see .planning/notes/account-restriction-audit-gap.md).
-- Append-only: one row per restriction state-change OR forward shift of restricted_until
-- (D-01). Ordinary "still limited" reconcile ticks WITHOUT a date shift produce NO row.

CREATE TABLE IF NOT EXISTS sender_restriction_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    sender_id        UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    -- D-04: one discriminator column. 'restriction' = account-level audit;
    -- 'recipient_privacy' = recipient-level error, account healthy, EXCLUDED from
    -- restriction analytics by a single WHERE category='restriction'.
    category         VARCHAR(20)  NOT NULL DEFAULT 'restriction',
    event_type       VARCHAR(20)  NOT NULL,  -- spam_limited|frozen|flood_wait|cleared|banned|extension (restriction) | privacy_restricted|privacy_premium_required (recipient_privacy)
    source           VARCHAR(20)  NOT NULL,  -- queue_error | spambot_reconcile | antispam_signal
    restricted_until TIMESTAMPTZ  NULL,      -- value at the moment of the event (D-02); NULL for cleared/recipient
    raw_text         TEXT         NULL,      -- raw send-error message or @SpamBot reply (D-02)
    activity_slice   JSONB        NULL,      -- HLTH-02 snapshot (D-05/D-06); NULL for recipient_privacy rows
    proxy            JSONB        NULL,      -- senders.proxy at event time (D-06.3)
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()  -- server timestamp (D-02)
);

CREATE INDEX IF NOT EXISTS idx_sre_sender_created
    ON sender_restriction_events (sender_id, created_at DESC);          -- per-account history (HLTH-03)
CREATE INDEX IF NOT EXISTS idx_sre_workspace_category
    ON sender_restriction_events (workspace_id, category, created_at DESC);  -- future analytics, category filter (D-03)

-- Optional CHECK guards against typos (drop+recreate = idempotent, mirrors 028).
ALTER TABLE sender_restriction_events DROP CONSTRAINT IF EXISTS sre_category_chk;
ALTER TABLE sender_restriction_events ADD CONSTRAINT sre_category_chk
    CHECK (category IN ('restriction', 'recipient_privacy'));
```
- `gen_random_uuid()` default matches migration-021 convention (UUID defaults on prod tables).
- `workspace_id` denormalized onto the event so the HLTH-03 endpoint and any future analytics stay workspace-scoped without a join (every write-point has the sender → can SELECT its `workspace_id`, or pass it in).
- File is idempotent (`IF NOT EXISTS`, drop+recreate constraint) per the applier contract (`app/database.py`).

### Category Discriminator (D-04 resolved)
- **Column:** `category VARCHAR(20)` with CHECK.
- **Values:** `restriction` (account health — the audit subject) | `recipient_privacy` (recipient-level, account healthy).
- **Full list of non-restriction (`recipient_privacy`) classes** observed in code:
  - `PRIVACY_RESTRICTED` — `UserNotMutualContactError` (telegram.py:699) → `event_type='privacy_restricted'`.
  - `PRIVACY_PREMIUM_REQUIRED` — 403 RPCError, requires recipient to allow non-Premium DMs (gap note) → `event_type='privacy_premium_required'`. Currently falls into the `SEND_FAILED` catch-all; to log it the plan must detect the RPCError name in telegram.py (add a branch alongside `is_frozen_error`).
  - Any other recipient-side send refusal (e.g. `USER_IS_BLOCKED`, `YOU_BLOCKED_USER`) — recommend the SAME `recipient_privacy` category if logged. **These are OPTIONAL to log** in v1; the HLTH-01 must-have is the account-level restriction events. The D-03 guarantee is the *separation*: restriction analytics filter `WHERE category='restriction'`.

### Pydantic schema additions
- **`PoolHealth`** model: `{active: int, paused: int, total: int, earliest_resume_at: datetime | None}`.
- **`CampaignSenderAttach`** (schemas:574): add `restriction_status: Literal["none","spam_limited","frozen"] = "none"` and `restricted_until: Optional[datetime] = None` — copy the exact field defs from `SenderResponse` (schemas:133-134) so naming/types are consistent.
- **`CampaignResponse`** (schemas:685): add `pool_health: PoolHealth` (built in `_campaign_to_response`).
- **`RestrictionEventResponse`** for the history endpoint: mirror the table columns (id, event_type, source, category, restricted_until, raw_text, activity_slice, proxy, created_at). `model_config = ConfigDict(from_attributes=True)`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Dual-session helper (own-session vs transaction-neutral) | A bespoke session-juggling block at each call-site | Copy the `failover_cold_backlog(db=None)` pattern verbatim (failover.py:87-114) | Already verified, already used by the same call-sites; consistency + atomicity |
| Per-sender derived restriction status | New status logic | `senders.restriction_status`/`restricted_until` columns (migration 028) + `_derive_status` (senders.py:68) | Already orthogonal to auth_status, already the single source of truth |
| Raw @SpamBot text capture | Re-parse SpamBot replies | `check_spambot()` already returns `result["raw_text"]` (telegram.py:336) | The raw text is already in hand at the reconcile write-point |
| Migration application | Manual `psql -f` | Drop file in `migrations/030_*.sql`, rebuild api — auto-applier picks it up | CLAUDE.md / `_apply_migrations` contract |
| Slice timestamp column | Inventing a `timestamp`/`ts` column | `messages_log.created_at` | The actual column name; "ts" in CONTEXT is informal |
| 3-state badge enum in API | A `badge_state` server field | Numeric `pool_health`; UI derives green/yellow/red | Keeps API presentation-free; matches existing computed-field philosophy |

**Key insight:** Every primitive this phase needs already exists — the restriction columns, the write-points, the raw-text sources, the activity source table with the right index, the computed-field response pattern, and the dual-mode session helper pattern. The phase is *wiring + one table*, not new machinery.

## Common Pitfalls

### Pitfall 1: `extension` event noise (D-01 violation)
**What goes wrong:** The reconcile `else` branch (listener.py:1427) writes `restricted_until` on every still-limited tick. Naively emitting an event there reproduces 37 events/day.
**Why it happens:** The branch always bumps the recheck time, even when the actual release date didn't move.
**How to avoid:** Load the **current** `restricted_until` in the reconcile SELECT (L1377 currently selects `id, slug, restriction_status` — add `restricted_until`). In the helper, emit an `extension` event ONLY when `new_restricted_until > old_restricted_until` by a meaningful margin (e.g. SpamBot quoted a later release date). A pure recheck-interval bump (`next_recheck` from the fixed interval, no `limit_until` from SpamBot) is NOT a real extension — suppress it.
**Warning signs:** event count per sender per day > a handful; rows with near-identical `restricted_until`.

### Pitfall 2: Slice computed in a different session than the event row
**What goes wrong:** Counts read from a session that doesn't see the just-paused queue / or the event lands without its slice on a partial failure.
**Why it happens:** Opening a second session for the COUNTs.
**How to avoid:** Compute the slice and INSERT the event in the SAME session/transaction as the `senders` UPDATE (Pattern 1). For queue.py, that's the existing `db2` block; for listener, the existing `session`/`db`.
**Warning signs:** events with NULL `activity_slice` where state changed; off-by-a-few counts vs `messages_log`.

### Pitfall 3: `message_type` filter wrong
**What goes wrong:** Slice counts include `failed` rows or misses the enum.
**Why it happens:** `messages_log.message_type` is a `SQLEnum(MessageType)` with values `sent`/`failed`; "sends" means `message_type='sent'`.
**How to avoid:** Filter `message_type='sent'` for the "what we did" counts. Decide explicitly whether `failed` attempts count toward "темп" (recommend: sends only; failures are a separate signal). In raw SQL the enum compares as its string value `'sent'`.
**Warning signs:** sends_1h higher than physically possible at 4/min.

### Pitfall 4: FLOOD_WAIT-hard path doesn't set `restriction_status`
**What goes wrong:** Treating the hard-FloodWait block (queue.py:704) as a state-change event when it only pauses the queue (no `senders` UPDATE).
**Why it happens:** It pauses pending like PEER_FLOOD but does NOT flip `restriction_status`.
**How to avoid:** If logging a `flood_wait` event here (HLTH-01 lists `flood_wait` as a type), record it as a `flood_wait` event with `restricted_until = reschedule_at` and category `restriction`, but DO NOT pretend `restriction_status` changed. This event has no state-transition; it's an informational flood marker. (Confirm with planner whether `flood_wait` belongs in the same table or is informational-only — see Open Q.)
**Warning signs:** `pool_health.paused` counting senders that aren't actually `restriction_status<>'none'`.

### Pitfall 5: Cross-repo openapi drift
**What goes wrong:** Frontend (separate repo, Lovable-generated) diverges from the new `pool_health`/event-endpoint shapes.
**Why it happens:** `lovable-handoff/openapi.json` is regenerated separately; Lovable sometimes diverges (CLAUDE.md quirks).
**How to avoid:** Regenerate `lovable-handoff/openapi.json` via the export-handoff script (NOT hand-edit), as Phase 08-04 did. Keep the badge logic (green/yellow/red) on the frontend from numeric `pool_health`.
**Warning signs:** UI shows missing/undefined pool fields.

### Pitfall 6: Test against prod DB
**What goes wrong:** `docker compose run --rm api pytest` runs conftest `DROP SCHEMA` against prod (the 2026-05-26 incident).
**How to avoid:** ALWAYS the test-overlay: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. Never `down -v` on tg-outreach.

## Code Examples

### Helper skeleton (verified patterns)
```python
# Source: mirror of app/services/failover.py:87-114 + app/services/queue.py:743-754 (verified live)
# app/services/restriction_audit.py
async def record_restriction_event(sender_id, event_type, source,
                                    restricted_until, raw_text,
                                    category="restriction", db=None):
    if db is None:
        async with AsyncSessionLocal() as own:
            await _record(own, sender_id, event_type, source,
                          restricted_until, raw_text, category)
            await own.commit()
    else:
        await _record(db, sender_id, event_type, source,
                      restricted_until, raw_text, category)

async def _record(db, sender_id, event_type, source, restricted_until, raw_text, category):
    # workspace_id + proxy + configured limits from the sender row
    s = (await db.execute(text("""
        SELECT workspace_id, proxy, rate_per_min, rate_per_hour, rate_per_day
        FROM senders WHERE id = :sid
    """), {"sid": str(sender_id)})).one()
    slice_ = None
    if category == "restriction":
        counts = (await db.execute(text("""
            SELECT
              COUNT(*) FILTER (WHERE created_at >= now() - interval '1 hour')  AS s1,
              COUNT(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS s24,
              COUNT(DISTINCT recipient_phone) FILTER (WHERE created_at >= now() - interval '1 hour')  AS u1,
              COUNT(DISTINCT recipient_phone) FILTER (WHERE created_at >= now() - interval '24 hours') AS u24
            FROM messages_log
            WHERE sender_id = :sid AND message_type = 'sent'
        """), {"sid": str(sender_id)})).one()
        slice_ = {
            "sends_1h": counts.s1, "sends_24h": counts.s24,
            "unique_contacts_1h": counts.u1, "unique_contacts_24h": counts.u24,
            "rate": {"configured_per_min": s.rate_per_min,
                     "configured_per_hour": s.rate_per_hour,
                     "configured_per_day": s.rate_per_day,
                     "actual_per_hour": counts.s1, "actual_per_day": counts.s24},
        }
    await db.execute(text("""
        INSERT INTO sender_restriction_events
          (workspace_id, sender_id, category, event_type, source,
           restricted_until, raw_text, activity_slice, proxy)
        VALUES (:wid, :sid, :cat, :etype, :src, :ru, :raw,
                CAST(:slice AS JSONB), CAST(:proxy AS JSONB))
    """), {"wid": str(s.workspace_id), "sid": str(sender_id), "cat": category,
           "etype": event_type, "src": source, "ru": restricted_until, "raw": raw_text,
           "slice": json.dumps(slice_) if slice_ else None,
           "proxy": json.dumps(s.proxy) if s.proxy else None})
```

### Wiring at PEER_FLOOD (queue.py:743 db2 block)
```python
# Source: app/services/queue.py:743-754 (verified) — add inside the existing db2 block, before commit
async with AsyncSessionLocal() as db2:
    await db2.execute(text("UPDATE message_queue SET scheduled_at = :pause_until ..."), ...)
    await db2.execute(text("UPDATE senders SET restriction_status='spam_limited', restricted_until=:recheck_at WHERE id=:sid"), ...)
    await record_restriction_event(sender.id, "spam_limited", "queue_error",
                                   recheck_at, error_msg, db=db2)   # same TX
    await db2.commit()
```

### Reconcile extension gate (listener.py:1427 else-branch)
```python
# Source: app/services/listener.py:1427-1447 (verified). old_until added to the L1377 SELECT.
# emit ONLY on a real forward shift (Pitfall 1 / D-01)
if next_at > old_until + timedelta(minutes=1):   # meaningful forward shift
    await record_restriction_event(r[0], "extension", "spambot_reconcile",
                                   next_at, result.get("raw_text"), db=db)
await db.execute(text("UPDATE senders SET restricted_until = :next WHERE id = :sid"), ...)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Restriction state only (`senders.restriction_status`) | + durable append-only event-log | This phase | History reconstructable |
| `message_queue.error_message` (overwritten) | `sender_restriction_events.raw_text` (append-only) | This phase | Raw cause preserved |
| Failover logs "what moved" to container logs only (~18h, Phase 9 D-12) | Durable event-log complements it | This phase | Audit survives log rotation |

**Deprecated/outdated:** none — this is additive.

## Environment Availability

Step 2.6: Phase is code + one migration only; the migration auto-applies on api rebuild. Dependencies already running in prod.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL (`outreach-platform-db`) | new table + queries | ✓ | 16 | — |
| Migration auto-applier | apply `030_*.sql` | ✓ | `app/database.py::_apply_migrations` | manual psql (discouraged) |
| Sibling frontend repo `/root/apps/aimly/aimly-tg-outreach` | POOLV-03/04 UI | ✓ | — | backend ships independently; UI can follow |

No missing dependencies.

## Validation Architecture

`nyquist_validation: true` in `.planning/config.json` → section included.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode=auto`, session-scoped loop) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_restriction_audit.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

> NEVER `docker compose run --rm api pytest` without the test-overlay (prod DROP SCHEMA). NEVER `down -v`.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HLTH-01 | PEER_FLOOD write-point inserts a `spam_limited`/`queue_error` event in the same TX | unit | `pytest tests/test_restriction_audit.py::test_peer_flood_writes_event -x` | ❌ Wave 0 |
| HLTH-01 | reconcile `free` writes a `cleared` event | unit | `...::test_reconcile_cleared_writes_event` | ❌ Wave 0 |
| HLTH-01 | reconcile still-limited WITHOUT date shift writes NO event (D-01) | unit | `...::test_reconcile_no_shift_no_event` | ❌ Wave 0 |
| HLTH-01 | reconcile with forward date shift writes ONE `extension` event (D-01) | unit | `...::test_reconcile_shift_writes_extension` | ❌ Wave 0 |
| HLTH-01 | event is append-only / survives a subsequent state change | unit | `...::test_events_append_only` | ❌ Wave 0 |
| HLTH-02 | event row carries `activity_slice` with sends_1h/24h, unique contacts, rate; computed at write time | unit | `...::test_event_carries_activity_slice` | ❌ Wave 0 |
| HLTH-02 | `proxy` snapshot stored from `senders.proxy` | unit | `...::test_event_carries_proxy_snapshot` | ❌ Wave 0 |
| HLTH-02 | slice counts only `message_type='sent'`, windowed correctly | unit | `...::test_slice_windows_sent_only` | ❌ Wave 0 |
| D-03 | recipient-privacy logged with `category='recipient_privacy'`, never flips `restriction_status`, filterable out | unit | `...::test_recipient_privacy_separate_category` | ❌ Wave 0 |
| HLTH-03 | `GET /senders/{slug}/restriction-events` returns workspace-scoped history newest-first | integration | `pytest tests/test_restriction_audit.py::test_history_endpoint -x` | ❌ Wave 0 |
| POOLV-01 | `pool_health` aggregate correct for all-active / partial / all-paused pools | integration | `pytest tests/test_pool_health.py::test_pool_health_states -x` | ❌ Wave 0 |
| POOLV-02 | `attached_senders[]` carry `restriction_status`/`restricted_until` | integration | `pytest tests/test_pool_health.py::test_attached_senders_enriched -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_restriction_audit.py tests/test_pool_health.py -x`
- **Per wave merge:** full suite (test-overlay)
- **Phase gate:** full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_restriction_audit.py` — HLTH-01/HLTH-02/D-03 (event writes, slice, append-only, no-shift suppression, history endpoint). Stub RED with import-inside-body (pattern from `test_failover.py:1` / `test_rebalance.py:51`).
- [ ] `tests/test_pool_health.py` — POOLV-01/02 (`pool_health` 3-state arithmetic + per-sender enrichment in `CampaignResponse`).
- [ ] Reuse the queue-item factory from `tests/test_pool_endpoints.py`/`test_rebalance.py` (message_queue + sticky CCA + optional conversation) and seed `messages_log` rows for slice assertions.
- [ ] No framework install needed — pytest infra exists.

*Frontend (POOLV-03/04) is human-verified in the sibling repo (no backend pytest), matching Phase 08-04's cross-repo UAT pattern.*

## Open Questions

1. **`flood_wait` event at the hard-FloodWait path (queue.py:704)?**
   - Known: HLTH-01 lists `flood_wait` as a type, but that block pauses the queue WITHOUT setting `senders.restriction_status` (Pitfall 4).
   - Unclear: should it emit an event (informational, `restricted_until=reschedule_at`, no state change) or is `flood_wait` only meaningful when it co-occurs with a restriction flag?
   - Recommendation: log it as an informational `flood_wait`/`restriction` event (it IS a throttle signal worth auditing), but do not let it affect `pool_health` (which reads `restriction_status`). Confirm with planner.

2. **Distinct `source='antispam_signal'` vs reusing `queue_error` for the listener antispam path?**
   - Known: D-02 enumerates `queue_error | spambot_reconcile`. The antispam-bot path (listener.py:881) is neither a queue send-error nor a SpamBot self-check reply — it's an unsolicited bot warning.
   - Recommendation: add a third `source` value `antispam_signal` for fidelity (the table column is free-form VARCHAR; cheap). The plan should confirm whether D-02's two-value enum is exhaustive or illustrative. Defaulting to `queue_error` is acceptable if the planner prefers strict D-02 adherence.

3. **Whether to log recipient-privacy at all in v1 (D-03 separation vs effort).**
   - Known: D-03 mandates the *separation mechanism* (category column) and that privacy NOT pollute restriction analytics. It does not strictly require recipient-privacy rows to exist.
   - Recommendation: ship the `category` column + CHECK now (the separation guarantee), and log `PRIVACY_RESTRICTED` (already detected, telegram.py:699) as `recipient_privacy`. Treat `PRIVACY_PREMIUM_REQUIRED` detection (currently in the `SEND_FAILED` catch-all) as OPTIONAL — needs an extra RPCError-name branch in telegram.py. Planner decides scope.

4. **`earliest_resume_at` when a sender is `frozen` (no auto-resume date)?**
   - Known: `frozen` rows have a `restricted_until` recheck time, but a hard freeze needs an appeal — the recheck time is when SpamBot is re-pinged, not a guaranteed resume.
   - Recommendation: still surface `MIN(restricted_until)` as the recheck horizon; the UI badge copy should say "до проверки в T" not "возобновится в T". Frontend wording detail.

## Sources

### Primary (HIGH confidence — verified against live code 2026-06-24)
- `app/models/__init__.py` — `Sender` L73 (restriction_status L93, restricted_until L94, proxy L87, rate_per_* L95-97); `MessageLog`/`messages_log` L108 (sender_id, recipient_phone, message_type {sent,failed}, created_at); `MessageQueue` L190.
- `app/services/queue.py` — PEER_FLOOD L733-781, ACCOUNT_FROZEN L783-824, FLOOD_WAIT-hard L704-715, `_fail_item` L913, messages_log write L656-666/945-951.
- `app/services/listener.py` — `_handle_antispam_signal` L881-965, `_restriction_reconcile_tick` L1360-1456 (free/suspended/else branches L1403/1420/1427).
- `app/services/telegram.py` — error codes L691-724 (PEER_FLOOD, PRIVACY_RESTRICTED, ACCOUNT_FROZEN, SEND_FAILED), `check_spambot` L308-362 (`raw_text`).
- `app/services/failover.py` — dual-mode `db=None` helper pattern L87-114.
- `app/routers/campaigns.py` — `_build_attached_senders` L196, `_campaign_to_response` L230-277.
- `app/routers/senders.py` — `_derive_status` L68, endpoint patterns (`/senders/{slug}/spambot-check` L626).
- `app/schemas/__init__.py` — `SenderResponse` restriction fields L133-134, `CampaignSenderAttach` L574, `CampaignResponse` L685 (computed fields L726-727).
- `app/database.py` — `_apply_migrations` contract (idempotent, lexical, NNN naming).
- `migrations/028_sender_restriction.sql`, `029_campaign_pause_reason.sql` — idempotent style + next number = 030.
- `pyproject.toml` — pytest-asyncio config; `.planning/config.json` — nyquist enabled.
- `.planning/notes/account-restriction-audit-gap.md`, `.planning/proposals/sender-pool-resilience.md`, CONTEXT/REQUIREMENTS/ROADMAP/STATE.

### Secondary / Tertiary
- None — no external/web research required for this phase.

## Metadata

**Confidence breakdown:**
- Write-point inventory & line numbers: HIGH — read live, lines re-verified post-Phase-9 drift.
- Activity-slice source/columns: HIGH — `messages_log` columns + index confirmed in model and migrations.
- Table/schema proposal: HIGH (mechanics) — follows verified 028/021 conventions; field names per D-08 verbatim.
- Category enum & recipient classes: MEDIUM — `PRIVACY_RESTRICTED` confirmed in code; `PRIVACY_PREMIUM_REQUIRED` only in gap-note (falls into catch-all), needs a detection branch.
- D-01 extension-gate: HIGH — reconcile else-branch behavior read directly; the diff-gate is the documented fix.

**Research date:** 2026-06-24
**Valid until:** 2026-07-24 (stable internal codebase; re-verify line numbers if Phase 11 lands first)
