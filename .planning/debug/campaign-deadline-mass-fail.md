---
status: fixing
trigger: "Когда кампания достигает дедлайна (stop_date), вся оставшаяся очередь рассылки отбивается как failed, рассылка перестаёт работать полностью — вместо аккуратной остановки/паузы."
created: 2026-07-10
updated: 2026-07-10
---

## Current Focus

hypothesis: CONFIRMED and CODE FIX APPLIED for both variants (user chose C — both).
  Variant 2 (D-11 v2): past-stop_date campaigns now auto-pause
  (pause_reason='past_stop_date'), pending queue left untouched, instead of the old
  fail-the-whole-tail behaviour. Variant 1: live ETA-shortfall forecast added as
  `eta_shortfall` on CampaignResponse. User confirmed no external n8n consumer
  depends on the old failed/past_stop_date callback, so it was safely removed.
test: unit + integration tests written for both variants (queue.py pause paths,
  campaigns.py ETA helper + event log). NOT yet executed — see blocker below.
expecting: `docker compose -f docker-compose.yml -f docker-compose.test.yml run
  --rm api pytest tests/test_queue_lifecycle_fixes.py
  tests/test_queue_per_campaign_hours.py tests/test_campaign_eta_shortfall.py
  tests/test_campaign_events.py -q` → all green
next_action: BLOCKED on environment — this sandbox's shell user (aimly-dev, uid
  1000) has no docker-group membership and no passwordless sudo:
  `docker compose ... run --rm api pytest` fails immediately with "permission
  denied while trying to connect to the Docker daemon socket" before any test
  runs. Per CLAUDE.md tests must ONLY run via the test-overlay (conftest guard
  blocks any other DATABASE_URL) — cannot bypass this safely from here. Need the
  user (or a session with docker access) to run the command above and report
  pass/fail, or grant this session docker group / sudo access to docker.

## Symptoms

expected: Кампания не должна терять контакты молча при дедлайне — либо заранее
  предупредить о нехватке времени, либо мягко встать на паузу без потери pending-очереди,
  чтобы после продления даты рассылка продолжилась без «реанимации».
actual: При достижении stop_date воркер помечает весь хвост pending message_queue как
  failed; на fail (для items с callback_url) уходит n8n-callback error=past_stop_date.
  Рассылка перестаёт работать.
errors: error_message='past_stop_date' на message_queue; callback status="failed",
  error="past_stop_date"
reproduction: Кампания с stop_date в прошлом, status='running' → воркер тикает → массово
  failed.
started: Логика введена в Phase 04-03 (commit 51601c9 «per-campaign scheduling»,
  D-08..D-11) и усилена в quick-260704-buq (commit fbf75e6, IN-07: per-item fail callback).
  Существует с момента появления per-campaign scheduling — не регрессия.

## Eliminated

- hypothesis: баг ломает обработку ДРУГИХ кампаний (полный отказ воркера)
  evidence: `_tick` фейлит только items дохлой кампании (`items_to_fail`), остальные
    сендеры добавляются в eligible и обрабатываются нормально. Воркер не падает.
    Есть лишь ВТОРИЧНЫЙ transient-эффект: глобальный SELECT `LIMIT 500 ORDER BY
    scheduled_at ASC` — большой дохлый хвост с ранним scheduled_at может забивать батч
    несколько тиков, пока не сольётся в failed, притормаживая другие кампании. Само-
    рассасывается. Не отдельный критический баг.
  timestamp: 2026-07-10

## Evidence

- timestamp: 2026-07-10
  checked: knowledge-base.md
  found: нет матча по past_stop_date/stop_date/deadline
  implication: новый паттерн

- timestamp: 2026-07-10
  checked: app/services/queue.py:250-314 (`_tick`) и :495-587 (`_process_next_for_sender`)
  found: оба пути делают Python-side пост-фильтр `if r.c_stop is not None and
    now_utc >= r.c_stop: -> items_to_fail`. Оба вызывают `_fail_past_stop_date_items`.
    Комментарий прямо называет это «D-11 (soft skip) — past stop_date → mark failed».
  implication: механизм fail — на уровне обработки очереди, per-item, каждый тик; НЕ cron.

- timestamp: 2026-07-10
  checked: app/services/queue.py:1424-1474 (`_fail_past_stop_date_items`)
  found: UPDATE status='failed', error_message='past_stop_date', finished_at=NOW()
    (guard AND status='pending'). Для каждой failed-строки с непустым callback_url —
    fire-and-forget `_fire_callback(status="failed", error="past_stop_date")`.
  implication: источник n8n-callback найден; callback только у items с callback_url.

- timestamp: 2026-07-10
  checked: campaign never transitions on stop_date — grep по queue.py: нет UPDATE
    campaigns SET status при stop_date
  found: кампания остаётся 'running'. Каждый тик заново подбирает её ещё-pending строки и
    фейлит следующий батч (до 500/тик глобально, 8/сендер) пока весь хвост не сольётся.
  implication: нет пути восстановления — продление stop_date не помогает (строки уже
    'failed', не 'pending'); resume не «реанимирует» failed. Это и есть «рассылка
    перестаёт работать».

- timestamp: 2026-07-10
  checked: кто ставит callback_url — grep по routers
  found: callback_url ставится ТОЛЬКО через legacy `/send` API push (send.py:250,
    n8n-поток). Bulk campaign enqueue (campaign_enqueue.py, INSERT на :451) callback_url
    НЕ ставит.
  implication: для UI/CSV bulk-campaign items n8n-callback НЕ шлётся вообще — они молча
    фейлятся. n8n-callback с past_stop_date шлётся только для items, залитых через /send
    (push в кампанию с callback_url).

- timestamp: 2026-07-10
  checked: потребители строки "past_stop_date" по всему репо (app, frontend, tests, n8n
    json, docs)
  found: "past_stop_date" ПРОИЗВОДИТСЯ только в queue.py. Нигде в репо не матчится как
    потребитель (frontend совпал на "stop_date", не на "past_stop_date"). n8n-workflow
    JSON в репо нет.
  implication: единственные внешние потребители callback error=past_stop_date — n8n-
    флоу вне репо. Их поведение из кода НЕ проверяемо → требуется подтверждение
    пользователя (риск Variant 2). Внутри продукта смена семантики ничего не ломает.

- timestamp: 2026-07-10
  checked: Campaign FSM — models/__init__.py:761-851, routers/campaigns.py:809-954,
    _cancel_pending_queue:107-123
  found: status enum draft/running/paused/done (DB CHECK). Воркер шлёт только для
    running (INNER JOIN + c.status='running') → paused кампания не отправляет.
    `_cancel_pending_queue` НАМЕРЕННО исключает pause («paused items resume when the
    campaign does») → при паузе pending-хвост СОХРАНЯЕТСЯ. pause/resume эндпоинты есть;
    resume ре-чекает sender-lock. На Campaign уже есть колонки pause_reason(String40) +
    paused_at — инфраструктура авто-паузы частично готова. finish/stop (running|paused →
    done) уже зовёт `_cancel_pending_queue` → явный путь закрытия зависшего хвоста
    СУЩЕСТВУЕТ.
  implication: Variant 2 (auto-pause) ложится на существующий FSM почти без нового кода;
    «кто закрывает вечный pending-хвост» — уже решено кнопкой Stop/Finish (→done →
    cancel pending).

- timestamp: 2026-07-10
  checked: данные для ETA (Variant 1) — GET /campaigns/{id} pool_health
    (campaigns.py ~287), Phase 22 grade budget
  found: pool_health {active, paused, total} уже считается. Дневной бюджет per sender
    теперь account-level grade-ladder (per-campaign колонка удалена mig 059; CLAUDE.md
    «150/день» — legacy). work_days_mask + timezone + stop_date дают рабочие дни.
    Остаток контактов — из pending/folder count.
  implication: Variant 1 реализуем; главный нюанс — источник per-sender дневного бюджета
    (grade ladder, не хардкод 150).

## Resolution

root_cause: >
  app/services/queue.py трактует past-stop_date items как «soft skip» = mark FAILED
  (два места: `_tick` :289-314 и `_process_next_for_sender` :556-572 → `_fail_past_stop_date_items`
  :1424). Кампания при этом НЕ переводится из 'running', поэтому каждый тик фейлит
  очередной батч ещё-pending строк, пока весь хвост кампании не окажется 'failed'.
  Продление stop_date/resume не восстанавливает (строки уже 'failed', не 'pending') —
  рассылка «перестаёт работать» без пути возврата. Дизайн-решение (D-11) с Phase 04-03,
  не регрессия.
fix: >
  Variant 2 (root-cause fix): app/services/queue.py — both `_tick` and
  `_process_next_for_sender` now collect distinct campaign ids whose stop_date has
  passed (instead of collecting item ids to fail) and call new
  `_pause_expired_campaigns(db, campaign_ids)`, which does
  `UPDATE campaigns SET status='paused', pause_reason='past_stop_date',
  paused_at=NOW() WHERE id=ANY(:ids) AND status='running'`. The `status='running'`
  guard makes it idempotent — once paused, the campaign drops out of both
  SELECTs' `c.status='running'` filter, so it never re-fires and never clobbers a
  manual pause or the 029 no-sender auto-pause. The pending queue tail is left
  completely untouched (no UPDATE on message_queue at all) — extending stop_date
  + Resume continues sending with zero reanimation. Old `_fail_past_stop_date_items`
  (mass-fail + per-item n8n callback error=past_stop_date) removed entirely — user
  confirmed no external consumer depends on it. Closing a permanently-abandoned
  pending tail remains the existing explicit Stop/Finish path
  (`_cancel_pending_queue`), unchanged.

  Campaign event log (GET /campaigns/{id}/events): added Source 3 — a synthetic
  `campaign_paused` event sourced directly from
  `campaigns.paused_at`/`pause_reason='past_stop_date'` (current pause state only,
  not full history, since those columns are overwritten on each pause/resume —
  same limitation the other sources already accept for MVP).

  Variant 1 (ETA warning): new `_compute_eta_shortfall` + `_count_work_days` in
  app/routers/campaigns.py, exposed as `eta_shortfall` on `CampaignResponse`
  (flows through GET /{id}, POST /start, list, etc. via `_campaign_to_response`).
  None when stop_date or folder_id is unset. `daily_capacity` sums the Phase 22
  grade-ladder budget (`grade_ladder.budget_for_level`) over the pool's CURRENTLY
  eligible senders (same predicate as PoolHealth.active) — NOT the legacy
  hardcoded 150/day. `work_days_left` counts campaign-timezone calendar days
  (today inclusive) whose weekday bit is set in work_days_mask, up to stop_date.
  Recomputed live on every read (nothing stored) so it tracks the pool/backlog in
  real time as senders freeze/thaw.

  Frontend (campaigns.$id.tsx): `DeadlinePausedNotice` banner when
  status='paused' && pause_reason='past_stop_date' (explains queue was preserved,
  points at Resume/Stop); `EtaShortfallNotice` banner when `eta_shortfall` is
  present and not on_track (shown persistently on the campaign page, not just at
  launch, since senders/backlog change over time); `campaign_paused` event
  rendered in the event log timeline with a RU explanation. `frontend/src/types/
  api.ts` (hand-maintained, not regenerated) updated with `EtaShortfall` schema +
  `eta_shortfall` field.
verification: >
  Self-verification via test-overlay BLOCKED by sandbox docker permissions (see
  next_action). Manual code-read verification done: traced both `_tick` and
  `_process_next_for_sender` — after the fix, a past-stop_date item is added to
  `deadline_campaign_ids` and `continue`d (never reaches `items_to_fail`/pick
  logic), the campaign UPDATE fires once per newly-expired campaign, and on the
  NEXT tick the campaign no longer matches `c.status='running'` in either SELECT
  so no further churn occurs. `grep -rn _fail_past_stop_date_items app/` confirms
  zero remaining references. Awaiting test-overlay run for automated
  confirmation — 9 new/rewritten tests target exactly this behaviour (see
  files_changed).
files_changed:
  - app/services/queue.py
  - app/schemas/__init__.py
  - app/routers/campaigns.py
  - frontend/src/types/api.ts
  - frontend/src/routes/_authenticated/campaigns.$id.tsx
  - tests/test_queue_lifecycle_fixes.py
  - tests/test_queue_per_campaign_hours.py
  - tests/test_campaign_eta_shortfall.py (new)
  - tests/test_campaign_events.py
