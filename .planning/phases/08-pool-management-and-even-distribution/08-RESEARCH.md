# Phase 8: Pool Management and Even Distribution - Research

**Researched:** 2026-06-23
**Domain:** Multi-sender campaign pool management — attach/detach REST endpoints, even least-loaded distribution, light rebalance-on-attach (backend FastAPI + asyncpg/SQLAlchemy async + PostgreSQL 16; frontend TanStack Start / React / TS in sibling repo)
**Confidence:** HIGH (this is a brownfield reuse phase — every touch point was read in source; the only genuinely-new design, the rebalance algorithm D-09, is specified below from the actual queue/rotation/CCA models)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** attach и detach разрешены на статусах **draft / paused / running** — пул можно менять на ходу, без обязательной паузы.
- **D-02:** attach валидирует нового sender'а через `_validate_workspace_owns_senders` (workspace-изоляция) **и** sender-lock — нельзя прицепить аккаунт, уже привязанный к ДРУГОЙ running-кампании этого workspace. Конфликт → переиспользовать контракт `_check_sender_lock` (409 со списком `{sender_id, campaign_id, campaign_name}`, как на `/start`).
- **D-03:** **min-pool guard:** нельзя отцепить **последний** sender у running-кампании. Detach последнего у running → **409**. Для draft пустой пул допустим.
- **D-04:** detach **блокируется (409)**, если у отцепляемого sender'а есть **неотправленный cold pending** в этой кампании (queue-строки `status='pending'`, никогда не отправлялись, диалог не начат). Сообщение: предложить pause или дождаться слива pending.
- **D-05:** **активные диалоги** не зависят от членства в пуле — продолжают отвечать (replies gated by `ai_enabled`/manager-takeover, не pool-membership). Detach их не трогает.
- **D-06:** Авто-перенос cold backlog отцепленного sender'а на здоровый пул **НЕ делаем в Phase 8** — это Phase 9.
- **⚠ Implication (D-04):** на активно работающей running-кампании sender почти всегда имеет pending → detach живого sender'а на running обычно требует pause/ожидания. Осознанный trade-off ради чистой границы с Phase 9.
- **D-07:** контакты назначаются на sender'а **на enqueue** (sticky `campaign_contact_assignments`), не лениво при отправке. Least-loaded сам по себе НЕ догрузит новый аккаунт, если папка уже полностью заэнкьюена.
- **D-08:** при attach в running-кампанию выполнять **лёгкий ребаланс**: перенести часть **неотправленных cold pending** с перегруженных senders на новый, чтобы распределение приблизилось к least-loaded. Переносим только un-sent cold pending; **активные диалоги не трогаем**; обновлять `campaign_contact_assignments` синхронно с queue-строками.
- **D-09:** точный алгоритм ребаланса — деталь research/plan (специфицирован ниже). Интент: ровный split, идемпотентно, безопасно под нагрузкой.
- **D-10:** управление пулом — **отдельная панель «Senders / Пул» на странице кампании** (не только визард). Работает и для draft, и для running.
- **D-11:** панель: мультиселект/chips добавляемых аккаунтов, add/remove, показ locked-аккаунтов (из `attached_senders[].locked_by_campaign_name`), отображение ошибок 409 человекочитаемо.
- **D-12:** существующий выбор senders в визарде создания остаётся; панель — управление пулом уже созданной кампании.

### Claude's Discretion
- Точные коды/тела ошибок (envelope) — следовать существующему стилю ошибок в `campaigns.py`.
- Алгоритм ребаланса (D-09).
- Раскладка/компоненты фронт-панели — по существующему дизайн-языку `aimly-tg-outreach`.

### Deferred Ideas (OUT OF SCOPE)
- **Авто-реассайн cold backlog при detach/фризе** → Phase 9. В Phase 8 detach с pending просто блокируется (D-04/D-06).
- **Здоровье пула (N active / K limited until T) + бейдж** → Phase 10.
- **Cross-campaign load awareness** (sender в 2 кампаниях) — non-goal блока; sender-lock и так запрещает 2 running.
</user_constraints>

## Summary

Phase 8 — почти целиком **переиспользование уже написанного кода**. Все хелперы валидации (`_validate_workspace_owns_senders`, `_check_sender_lock`, `_build_attached_senders`), модель пула (`campaign_senders` M2M), sticky-назначение (`campaign_contact_assignments`), least-loaded раздача (`rotation._pick_least_loaded`) и round-robin worker (`queue._tick`) **уже существуют и работают**. Phase 8 добавляет два эндпоинта (`POST /campaigns/{id}/senders`, `DELETE /campaigns/{id}/senders/{sid}`), один новый сервис-хелпер (light rebalance) и фронт-панель.

**Миграции НЕ нужны** — обе таблицы (`campaign_senders`, `campaign_contact_assignments`) и все нужные колонки/индексы созданы миграцией `016_phase4.sql` (подтверждено чтением файла). Это согласуется с подозрением CONTEXT.

Единственный по-настоящему новый дизайн — **rebalance-on-attach (D-09)**. Существующий `_pick_least_loaded` непригоден as-is: он считает нагрузку **глобально по всем кампаниям** (нет фильтра по `campaign_id`) и выбирает **один** sender. Для ребаланса нужен новый, **campaign-scoped** even-split проход. Конкурентная безопасность достигается перемещением **только** строк `status='pending'` под `FOR UPDATE SKIP LOCKED` (тот же замок, что использует worker), с синхронным `UPDATE` `campaign_contact_assignments` в одной транзакции.

**Primary recommendation:** Реализовать новый сервис `app/services/rebalance.py::rebalance_on_attach(campaign_id, new_sender_id, db)` с campaign-scoped even-split поверх `message_queue` (status='pending', recipient никогда не отправлялся, нет conversation) + транзакционный sync `campaign_contact_assignments`. Эндпоинты — в `campaigns.py`, переиспользуя существующие хелперы 1:1. После backend — фронт-панель в `campaigns.$id.tsx` поверх уже существующей read-only Senders-секции + мультиселект, скопированный из `campaigns.new.tsx::AccountsStep`. Регенерировать `lovable-handoff/openapi.json` через `scripts/export-handoff.sh`.

<phase_requirements>
## Phase Requirements

No fixed REQ-IDs were assigned upstream (`Requirements: TBD (derive on plan)` in ROADMAP). Derive POOL-NN IDs during planning, grounded in the CONTEXT decisions. Suggested mapping for the planner:

| Suggested ID | Behavior (from CONTEXT) | Research Support |
|----|-------------|------------------|
| POOL-01 | `POST /campaigns/{id}/senders` attaches a sender to draft/paused/running (D-01) | New endpoint in campaigns.py; reuse `_validate_workspace_owns_senders` + `_check_sender_lock` + `CampaignSender` insert |
| POOL-02 | Attach rejects sender locked by another running campaign — 409 same contract as /start (D-02) | `_check_sender_lock` (campaigns.py:275) returns `[{sender_id, campaign_id, campaign_name}]`; reuse verbatim |
| POOL-03 | Attach rejects sender not in workspace — 404 (D-02) | `_validate_workspace_owns_senders` (campaigns.py:141) raises SENDER_NOT_FOUND |
| POOL-04 | `DELETE /campaigns/{id}/senders/{sid}` detaches (D-01) | New endpoint; delete `campaign_senders` row |
| POOL-05 | Detach of last sender on running campaign → 409 min-pool guard (D-03) | `SELECT COUNT(*) FROM campaign_senders WHERE campaign_id=:cid` |
| POOL-06 | Detach blocked (409) when sender has un-sent cold pending in this campaign (D-04) | Cold-pending query shape §"Detach Guards" |
| POOL-07 | Light rebalance moves un-sent cold pending from overloaded senders onto newly-attached sender on a running campaign (D-08/D-09) | New `rebalance.py` §"Rebalance Algorithm" |
| POOL-08 | Rebalance is idempotent and concurrency-safe under worker ticks (D-09) | `FOR UPDATE SKIP LOCKED` + status guard §"Concurrency Safety" |
| POOL-09 | Frontend "Senders / Пул" panel: add/remove, locked display, human-readable 409 (D-10/D-11) | §"Frontend" — extend existing read-only panel + reuse AccountsStep toggle |

Mark POOL-01..09 in REQUIREMENTS.md traceability when planning.
</phase_requirements>

## Standard Stack

This phase introduces **no new libraries**. It uses exactly what the codebase already uses.

### Core (already in repo, verified by reading source)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | (repo) | Two new routes in `app/routers/campaigns.py` | Every endpoint already lives here |
| SQLAlchemy async + asyncpg | 2.0 / async | ORM + raw `text()` SQL for the rebalance pass | Codebase uses raw `text()` for all multi-table queries (see rotation.py, campaigns.py) |
| Pydantic v2 | (repo) | Request/response — reuse `CampaignSenderAttach`, `CampaignResponse` | Existing schemas already cover attach shape |
| pytest + pytest-asyncio | (repo) | Tests via **test-overlay only** | conftest fixtures already provide campaign/sender factories |

### Frontend (already in repo)
| Library | Purpose |
|---------|---------|
| TanStack Query (`@tanstack/react-query`) | `useMutation` for attach/detach with `invalidateQueries(["campaign", id])` |
| TanStack Router file-route | Panel lives inside `src/routes/_authenticated/campaigns.$id.tsx` |
| `@/types/api` (openapi-typescript) | Types regenerated from `lovable-handoff/openapi.json` |

**Installation:** None. No `npm install`, no new Python deps.

**Version verification:** N/A — zero new dependencies. The deliberate non-goal is "do NOT rebuild" (proposal §"What already works").

## Architecture Patterns

### Where code goes
```
app/
├── routers/campaigns.py      # + POST /{id}/senders, DELETE /{id}/senders/{sid}  (reuse helpers)
├── services/rebalance.py     # NEW — rebalance_on_attach() campaign-scoped even-split
└── schemas/__init__.py       # reuse CampaignSenderAttach; maybe a thin AttachRequest body

aimly-tg-outreach/ (sibling repo)
└── src/routes/_authenticated/campaigns.$id.tsx   # extend existing read-only Senders section → panel
```

### Pattern 1: Reuse the validation chain verbatim (attach endpoint)
**What:** Attach must reproduce the start/resume validation, no new logic.
**Verified source (campaigns.py:578-633 start_campaign):** start calls `_check_sender_lock(db, ctx, c.id)` → if non-empty list → `409 {code:"SENDER_LOCK_CONFLICT", conflicts:[...]}`.

```python
# Source: app/routers/campaigns.py:621-627 (start_campaign) — reuse identical contract
conflicts = await _check_sender_lock(db, ctx, c.id)   # after inserting the new campaign_senders row OR pass candidate sender
if conflicts:
    raise HTTPException(409, detail={"code": "SENDER_LOCK_CONFLICT", "conflicts": conflicts})
```

**⚠ Subtlety:** `_check_sender_lock(db, ctx, campaign_id)` (campaigns.py:275) checks **all** senders already in `campaign_senders` for `campaign_id` against other running campaigns. For attach you want to check the **incoming** sender BEFORE inserting it. Two correct options:
- (A) Insert the `campaign_senders` row, then call `_check_sender_lock` in the same transaction; if conflict → rollback + 409. Clean, reuses helper unchanged.
- (B) Write a tiny `_check_single_sender_lock(db, ctx, sender_id)` variant that takes one sender_id. Avoids the insert-then-rollback dance.
Recommend **(A)** to keep the 409 contract bit-identical and avoid a second helper. The conflict list will naturally include the just-inserted sender's row.

### Pattern 2: campaign-scoped least-loaded (NEW — `_pick_least_loaded` is NOT reusable as-is)
**What:** `rotation._pick_least_loaded` (rotation.py:198-217) counts `campaign_contact_assignments` **globally per sender across all campaigns** — no `campaign_id` filter. For an even split *within one campaign's pending backlog*, you need per-campaign counts.
**Verified source (rotation.py:204-214):**
```sql
SELECT s.id AS sid, COUNT(cca.id) AS cnt
FROM senders s
LEFT JOIN campaign_contact_assignments cca ON cca.sender_id = s.id   -- NOTE: no campaign filter!
WHERE s.id = ANY(:ids)
GROUP BY s.id ORDER BY cnt ASC, s.created_at ASC LIMIT 1
```
**Implication:** Do NOT call `_pick_least_loaded` in a loop for the rebalance. Write a fresh campaign-scoped even-split pass (§"Rebalance Algorithm").

### Anti-Patterns to Avoid
- **Looping `_pick_least_loaded` per moved row** — global count, wrong scope, and N queries. Use one set-based pass.
- **Touching empirical rate-limit constants or queue intervals** — CLAUDE.md hard guard. Rebalance must NOT change `scheduled_at` semantics or rate windows.
- **Moving rows that have already been sent or have a started dialogue** — violates D-05/D-08. Filter strictly to cold pending.
- **A new migration** — tables already exist (016). Adding one risks drift; none is needed.
- **Inventing a new 409 error code for sender-lock** — reuse `SENDER_LOCK_CONFLICT` (frontend `error-codes.ts` already maps it).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Workspace ownership check for sender_ids | New validator | `_validate_workspace_owns_senders` (campaigns.py:141) | Already raises SENDER_NOT_FOUND with `missing_sender_ids` |
| "Sender locked by another running campaign" | New cross-campaign query | `_check_sender_lock` (campaigns.py:275) | Exact `{sender_id, campaign_id, campaign_name}` contract; frontend already renders it |
| attached_senders + locked flags in response | New serializer | `_build_attached_senders` (campaigns.py:194) + `_campaign_to_response` | Already powers GET response; new endpoints return the same shape |
| Concurrency-safe row claim | New advisory lock scheme | `FOR UPDATE SKIP LOCKED` on `message_queue` (queue.py:313) | Worker already uses this; rebalance must use the same lock to not race it |
| Race-safe assignment upsert | Custom dedup | `ON CONFLICT (campaign_id, contact_phone) DO NOTHING` (rotation.py:155; UNIQUE idx_cca_campaign_phone) | Constraint exists; UPDATE path for rebalance |
| Frontend sender toggle UI | New component | Copy `AccountsStep` toggle block (campaigns.new.tsx:1036-1095) | Same design tokens, checkbox/avatar/pill already styled |
| 409 → human text | New error formatter | `error-codes.ts::SENDER_LOCK_CONFLICT` + add MIN_POOL / DETACH_BLOCKED keys | `api.ts` already routes `detail.code` through `errorMessageFromEnvelope` |

**Key insight:** This phase's risk is NOT writing new code — it's *accidentally diverging* from the existing contracts. The grep-confirmed helpers must be reused byte-for-byte where the contract is shared (lock 409, attached_senders shape).

## Rebalance Algorithm (D-09 — the primary research output)

### Definitions (from the actual models)
- **Pool** = rows in `campaign_senders` for `campaign_id` whose sender is **eligible**: `lifecycle_status='active' AND auth_status='ok' AND role='sender' AND restriction_status='none'` (mirror rotation.py:118-121 candidate filter exactly — a `spam_limited` new sender should not receive moved rows).
- **Cold pending row** = `message_queue` row where `campaign_id=:cid AND status='pending'` AND the recipient was **never sent to** AND has **no started conversation**. Identity key = `message_queue.recipient_phone` (VARCHAR(40): phone or `@username`, migration 025) — same key as `campaign_contact_assignments.contact_phone` and `conversations.contact_phone`.
- **Overloaded sender** = sender holding more cold-pending rows than the post-rebalance fair share.
- **Fair share** target after adding sender N+1: `ceil(total_movable_cold_pending_in_pool / pool_size)` per sender is the *cap*; the new sender should rise toward `floor(total / pool_size)`.

### "Never sent / no dialog" predicate (verified against models)
A pending queue row is movable cold-pending iff:
```sql
mq.status = 'pending'
AND mq.campaign_id = :cid
-- never successfully sent to this recipient in this campaign:
AND NOT EXISTS (
    SELECT 1 FROM message_queue s
    WHERE s.campaign_id = mq.campaign_id
      AND s.recipient_phone = mq.recipient_phone
      AND s.status = 'sent'
)
-- no dialogue started (Conversation upserted on send/reply; D-05 active dialogs untouched):
AND NOT EXISTS (
    SELECT 1 FROM conversations cv
    WHERE cv.workspace_id = mq.workspace_id
      AND cv.contact_phone = mq.recipient_phone
)
```
Rationale: per ARCHITECTURE "On success ... Conversation upserted". So absence of a `conversations` row ≡ no dialogue yet. The `sent` self-EXISTS is belt-and-suspenders against partial states (e.g. a re-queued row). Both predicates use `recipient_phone` as the join key, consistent with rotation/CCA/conversations.

### Algorithm (pseudocode)
```text
rebalance_on_attach(campaign_id, new_sender_id, db):
    # Only run for RUNNING campaigns; draft/paused will enqueue evenly later (D-07).
    # (Idempotent: if already even, moves 0 rows.)

    pool = eligible senders in campaign_senders(campaign_id)   # includes new_sender_id
    if new_sender_id not in pool: return   # ineligible new sender → nothing to do
    P = len(pool)
    if P < 2: return

    # 1. Count current cold-pending load per sender (campaign-scoped).
    load[s] = COUNT(movable cold-pending rows currently assigned to s)   # via mq.sender_id
    total = sum(load)
    if total == 0: return                  # nothing to move (idempotent no-op)

    target = total // P                     # floor — new sender wants ~target rows
    cap_each = ceil(total / P)              # max any sender should keep

    need = target - load[new_sender_id]     # how many to pull onto the new sender
    if need <= 0: return                    # already balanced (idempotent)

    BATCH_CAP = 500                          # discretion; protect one TX. tune in plan.
    need = min(need, BATCH_CAP)

    # 2. Select donor rows: from senders ABOVE cap_each, take their surplus.
    #    Lock with FOR UPDATE SKIP LOCKED so we never grab a row the worker is sending.
    moved = SELECT movable-cold-pending rows
            WHERE sender_id IN (senders with load > target)
            ORDER BY (donor load DESC), mq.scheduled_at DESC   # move the *latest* scheduled first
            LIMIT need
            FOR UPDATE OF mq SKIP LOCKED

    if not moved: return

    # 3. Reassign IN ONE TRANSACTION (queue rows + sticky assignment in sync):
    for row in moved:
        UPDATE message_queue SET sender_id = new_sender_id WHERE id = row.id
        UPDATE campaign_contact_assignments
           SET sender_id = new_sender_id
         WHERE campaign_id = :cid AND contact_phone = row.recipient_phone
    COMMIT
    log("rebalance: moved {len(moved)} cold-pending rows to {new_sender_id}")
```

### Why this shape
- **Single set-based pass**, not a loop over `_pick_least_loaded` (which is global-scoped and single-pick).
- **Idempotent:** re-running computes `need = target - load[new]`; once balanced, `need <= 0` → 0 moves. Safe to call on every attach, even if the same sender is attached twice (the second attach would already be in the pool / be a no-op via min-pool/`ON CONFLICT`).
- **`ORDER BY mq.scheduled_at DESC`** moves the rows that will be sent *latest*, minimizing the chance of racing an imminent send and preserving send order on the donor.
- **Synchronous CCA update** keeps `campaign_contact_assignments` (sticky source of truth used by `rotation.get_or_assign_sender` step 1) consistent with the queue, so a later worker tick / re-enqueue won't undo the move.

### Exact SQL/ORM touch points
- READ pool: `campaign_senders JOIN senders` with the rotation.py:118-121 filter (copy it).
- READ load + donor rows: `message_queue` filtered by the cold-pending predicate above, grouped by `sender_id`.
- WRITE: `UPDATE message_queue SET sender_id` + `UPDATE campaign_contact_assignments SET sender_id` (UNIQUE on `(campaign_id, contact_phone)` guarantees one CCA row per recipient).
- All raw `text()` (codebase convention), one `AsyncSessionLocal()` transaction, `await db.commit()` once.

### Where it's invoked
Call `rebalance_on_attach(...)` from the attach endpoint **only when `campaign.status == 'running'`** AND the attach succeeded (after the `campaign_senders` insert + lock check pass). For draft/paused, skip — D-07 enqueue will distribute evenly on the next worker pass (or on resume). Document this branch explicitly.

## Concurrency Safety

The worker (`queue._process_next_for_sender`, queue.py:294-313) claims pending rows with:
```sql
... WHERE mq.sender_id = :sid AND mq.status = 'pending' AND mq.scheduled_at <= NOW() ...
ORDER BY mq.priority DESC, mq.created_at ASC LIMIT 8 FOR UPDATE OF mq SKIP LOCKED
```
then flips them to `status='processing'` and commits *before* hitting Telegram (queue.py:351-360).

**Safe rebalance approach (no race):**
1. The rebalance SELECT also uses `FOR UPDATE OF mq SKIP LOCKED`. If the worker currently holds a row's lock (it's mid-claim), `SKIP LOCKED` makes rebalance skip it — never moves an in-flight row. If rebalance holds it first, the worker skips it for that tick. Either way no double-processing.
2. The rebalance filter is `status='pending'`. The instant the worker flips a row to `processing`, it is **excluded** from the rebalance donor set (status guard). So a row mid-send can never be moved.
3. Moving a row only rewrites `sender_id` while it is still `pending` — the worker will pick it up under the *new* sender on a subsequent tick. No lost or duplicated sends.
4. The entire reassign (queue UPDATE + CCA UPDATE) is **one transaction** → an observer never sees a queue row pointing at sender A while CCA points at sender B.

**This mirrors the existing Phase 9 safety note (proposal C2)** and the worker's own SKIP-LOCKED discipline — no new locking primitive is introduced.

## Detach Guards (D-03 / D-04)

### Min-pool guard (D-03) — "last sender of a running campaign"
```sql
SELECT COUNT(*) FROM campaign_senders WHERE campaign_id = :cid;
```
If `campaign.status == 'running'` AND `count == 1` (i.e. detaching the only remaining sender) → `409 {code:"MIN_POOL_GUARD", message:"Cannot detach the last sender of a running campaign. Pause it first."}`. For `draft`/`paused`, empty pool is allowed (consistent with create where `sender_ids` default `[]` and start re-checks `NO_SENDERS_ATTACHED`).

### Un-sent cold pending guard (D-04) — block detach when sender still owns cold backlog
Reuse the cold-pending predicate from the rebalance section, scoped to the detached sender:
```sql
SELECT EXISTS (
  SELECT 1
  FROM message_queue mq
  WHERE mq.campaign_id = :cid
    AND mq.sender_id   = :sid
    AND mq.status      = 'pending'
    AND NOT EXISTS (SELECT 1 FROM message_queue s
                    WHERE s.campaign_id = mq.campaign_id
                      AND s.recipient_phone = mq.recipient_phone
                      AND s.status = 'sent')
    AND NOT EXISTS (SELECT 1 FROM conversations cv
                    WHERE cv.workspace_id = mq.workspace_id
                      AND cv.contact_phone = mq.recipient_phone)
) AS has_cold_pending;
```
If `has_cold_pending` → `409 {code:"DETACH_BLOCKED_PENDING", message:"This sender still has un-sent contacts in the campaign. Pause the campaign or wait for the queue to drain, then detach."}`.

### Distinguishing cold-pending from active dialogs (D-05)
- **Active dialog** = a `conversations` row exists for `(workspace_id, contact_phone)` → the `NOT EXISTS conversations` clause above **excludes** it from the guard. Active dialogs do NOT block detach and are NOT moved — they keep replying from their own account (replies gated only by `ai_enabled`, ARCHITECTURE "Manager Takeover" + proposal §"Replies not gated").
- Therefore a sender whose only remaining work is engaged dialogues **can** be detached on a running campaign — exactly the D-05 intent. The detach removes pool membership but leaves the conversations and their `conversations.sender_id` untouched.

## Attach Validation Reuse (D-02) — exact contract

Verified signatures:
- `_check_sender_lock(db, ctx, campaign_id) -> list[dict]` returns `[{"sender_id": str, "campaign_id": str, "campaign_name": str}, ...]` (campaigns.py:275-298). Empty list = OK.
- start (campaigns.py:621) and resume (campaigns.py:671) both do: `conflicts = await _check_sender_lock(...)` → `raise HTTPException(409, detail={"code":"SENDER_LOCK_CONFLICT","conflicts":conflicts})`.
- `_validate_workspace_owns_senders(db, ctx, sender_ids: list[UUID])` (campaigns.py:141) → raises `404 {code:"SENDER_NOT_FOUND", missing_sender_ids:[...]}`.

**Attach endpoint validation order (recommended):**
1. `_load_campaign(db, ctx, campaign_id)` → 404 CAMPAIGN_NOT_FOUND if not in workspace.
2. `_validate_workspace_owns_senders(db, ctx, [sender_id])` → 404 if not owned.
3. Idempotency: if `campaign_senders` row already exists → return current response (or 200 no-op) — avoids PK violation on `(campaign_id, sender_id)`.
4. Insert `CampaignSender(campaign_id, sender_id, workspace_id)`.
5. `conflicts = _check_sender_lock(db, ctx, campaign_id)`; if non-empty → rollback insert + `409 SENDER_LOCK_CONFLICT`.
6. If `campaign.status == 'running'`: `await rebalance_on_attach(campaign_id, sender_id, db)`.
7. `commit`; return `_campaign_to_response(db, ctx, campaign)`.

**Frontend note:** `error-codes.ts::SENDER_LOCK_CONFLICT` currently reads `d.name` / `d.other` (single-conflict phrasing), but the backend emits `conflicts: [...]`. This is a pre-existing minor mismatch on the start path; planner should fix the formatter to read `detail.conflicts[]` (or have the panel render the array) so attach errors are accurate.

## Migration Need

**No new migration required.** Verified by reading `migrations/016_phase4.sql`:
- `campaign_senders(campaign_id, sender_id, workspace_id, added_at)`, PK `(campaign_id, sender_id)`, indexes on `sender_id` and `workspace_id` (016:55-66).
- `campaign_contact_assignments(id, workspace_id, campaign_id, contact_phone, sender_id, created_at)`, UNIQUE `idx_cca_campaign_phone (campaign_id, contact_phone)`, index on `sender_id`, `workspace_id` (016:68-84).
- `message_queue.campaign_id` FK + composite index `(workspace_id, campaign_id, status, scheduled_at)` (016:89-109) — covers the rebalance/guard queries efficiently.

The rebalance and guards are **pure reads + UPDATEs** on existing columns. ORM models (`CampaignSender`, `CampaignContactAssignment`, `MessageQueue.recipient_phone`, `Conversation.contact_phone`) all already present in `app/models/__init__.py`. State the negative explicitly in the plan: *"Phase 8 adds NO migration — 016 covers all schema."*

## Frontend (D-10 / D-11)

**File:** `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx` — there is **already a read-only "Senders ({n})" `<section className="card">`** (lines ~363-407) that maps `c.attached_senders[]` and renders `s.locked_by_campaign_name` in danger color. Phase 8 upgrades this into an interactive panel.

### What to add (reuse, don't reinvent)
1. **Add/remove controls** in the existing Senders card:
   - A workspace senders list comes from `api<{senders:Sender[]}>("/api/v1/senders")` (same query the wizard uses, campaigns.new.tsx:141-143).
   - Multiselect/chips UI: **copy the toggle block from `AccountsStep`** (campaigns.new.tsx:1036-1095) — checkbox + avatar + status pill, already styled with `--tg-blue` tokens. Filter out already-attached and `status==='error'` senders.
   - Each attached row gets a remove button (the existing `<li>` is the slot).
2. **Mutations** (TanStack Query, mirror `lifecycleMut` at campaigns.$id.tsx:89-103):
   ```ts
   const attachMut = useMutation({
     mutationFn: (sid: string) =>
       api<Campaign>(`/api/v1/campaigns/${id}/senders`, { method: "POST", body: { sender_id: sid } }),
     onSuccess: () => void qc.invalidateQueries({ queryKey: ["campaign", id] }),
     onError: (e) => setActionError(errMsg(e)),
   });
   const detachMut = useMutation({
     mutationFn: (sid: string) =>
       api<Campaign>(`/api/v1/campaigns/${id}/senders/${sid}`, { method: "DELETE" }),
     onSuccess: () => void qc.invalidateQueries({ queryKey: ["campaign", id] }),
     onError: (e) => setActionError(errMsg(e)),
   });
   ```
3. **Locked display (D-11):** already surfaced — `attached_senders[].locked_by_campaign_name` is rendered. Keep it; disable the "remove"/"add" affordance or show the lock reason.
4. **Human-readable 409 (D-11):** `api.ts` already turns `{detail:{code,message}}` into `ApiError` and routes through `error-codes.ts`. **Add three keys** to `error-codes.ts`:
   - `MIN_POOL_GUARD` → "Can't remove the last account from a running campaign. Pause it first."
   - `DETACH_BLOCKED_PENDING` → "This account still has un-sent contacts. Pause the campaign or wait for the queue to drain."
   - Fix `SENDER_LOCK_CONFLICT` to read `detail.conflicts[]` (array) instead of `d.name`/`d.other`.
   The existing `actionError` banner (campaigns.$id.tsx:57, 103) displays `errMsg(e)` — no new error surface needed.

### Design language
- Reuse `card`, `pill`, `pill--green/red`, `avatar avatar--sm`, `--tg-blue*`, `--bg-soft`, `--danger` tokens (all used in the existing panel + AccountsStep). No new CSS system.
- `EditCampaignModal` is an option if a modal is preferred over inline, but CONTEXT D-10 says "separate panel **on the campaign page**" → inline card section is the closer match. Recommend inline.

## OpenAPI / Lovable Handoff

**Yes — regenerate after backend lands.** `lovable-handoff/openapi.json` currently lists campaign paths but NOT `/{campaign_id}/senders` (verified: only post/get/patch/delete/duplicate/finish/pause/resume/start/stop present). The two new endpoints must appear so Lovable can regenerate `src/types/api.ts`.

**Mechanism (do not hand-edit):** run `scripts/export-handoff.sh` — it boots `docker compose up -d db api`, pulls `/openapi.json` from inside the api container, writes `lovable-handoff/openapi.json` via `jq`, regenerates types via `npx openapi-typescript@7`, and validates the project title. Plan a task: "after the two endpoints are merged, run `scripts/export-handoff.sh` and commit the regenerated `openapi.json` + frontend `src/types/api.ts`."

If the attach body is a typed Pydantic model (e.g. `CampaignSenderAttachRequest{sender_id: UUID}`), it auto-appears in the schema. The existing `CampaignSenderAttach` is the *response* sub-object; consider a distinct request schema to keep the spec clean (Claude's discretion per CONTEXT).

## State of the Art

No external "current vs old approach" applies — this is internal reuse. The only internal evolution note:

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `sender_ids` set only at campaign create; PATCH ignores it (schemas:625) | Dedicated attach/detach endpoints (Phase 8) | This phase | Pool becomes mutable on running campaigns; PATCH still ignores sender_ids by design (D-12) |
| `_pick_least_loaded` global-scoped single-pick (rotation.py:198) | Add campaign-scoped even-split for rebalance | This phase | Keep rotation.py as-is for enqueue; new `rebalance.py` for on-attach |

**Deprecated/outdated:** Nothing removed. `CampaignUpdate` docstring (schemas:625) saying "delete → create a new one" is superseded by the new endpoints — update that comment.

## Common Pitfalls

### Pitfall 1: Reusing `_pick_least_loaded` for rebalance
**What goes wrong:** It counts assignments across ALL campaigns and returns one sender → wrong target and N+1 queries.
**How to avoid:** Write a campaign-scoped set-based pass (§"Rebalance Algorithm"). Leave `rotation.py` untouched.
**Warning sign:** rebalance importing from `rotation` other than the candidate-filter SQL snippet.

### Pitfall 2: Racing the worker when moving rows
**What goes wrong:** Moving a row the worker just flipped to `processing`, or that it holds locked → double send or lost send.
**How to avoid:** Filter `status='pending'` AND `FOR UPDATE OF mq SKIP LOCKED` (§"Concurrency Safety"). Both queue rows and CCA updated in one TX.
**Warning sign:** rebalance SELECT without `SKIP LOCKED` or without the `status='pending'` guard.

### Pitfall 3: Forgetting to keep CCA in sync (D-08)
**What goes wrong:** Move queue row's `sender_id` but not `campaign_contact_assignments` → next worker re-enqueue / `get_or_assign_sender` reads stale sticky assignment and reverts the move.
**How to avoid:** UPDATE both tables in the same transaction, keyed by `recipient_phone`/`contact_phone`.
**Warning sign:** rebalance UPDATE that touches only `message_queue`.

### Pitfall 4: Min-pool guard skipped for draft (D-03)
**What goes wrong:** Blocking detach of last sender on a draft → contradicts create allowing empty `sender_ids`.
**How to avoid:** Guard only when `status='running'`. draft/paused may go to 0 senders.

### Pitfall 5: Detach guard misclassifies engaged dialogs as cold (D-04/D-05)
**What goes wrong:** Blocking detach because a sender has pending rows that are actually for already-engaged contacts.
**How to avoid:** Use the `NOT EXISTS conversations` + `NOT EXISTS sent` predicate. Only truly cold pending blocks detach.

### Pitfall 6: Running tests without the test-overlay
**What goes wrong:** CLAUDE.md / 2026-05-26 incident — `DATABASE_URL` → prod, conftest `DROP SCHEMA` wipes production.
**How to avoid:** ALWAYS `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. conftest guard (lines 46-77) also blocks it, but use the overlay.

### Pitfall 7: Hand-editing `openapi.json` or `src/types/api.ts`
**What goes wrong:** Drift between spec and backend; Lovable overwrites manual edits.
**How to avoid:** Run `scripts/export-handoff.sh`; never hand-edit generated files.

### Pitfall 8: Attach lock check ordering
**What goes wrong:** Calling `_check_sender_lock` before inserting the row checks the *old* pool and misses the incoming sender's conflict.
**How to avoid:** Insert-then-check-then-maybe-rollback (Pattern 1, option A), or a single-sender variant (option B).

## Code Examples

### Attach endpoint skeleton (reuses every existing helper)
```python
# Source pattern: app/routers/campaigns.py start_campaign:578-633 + create_campaign:387-394
@router.post("/{campaign_id}/senders", response_model=CampaignResponse)
async def attach_sender(campaign_id: UUID, payload: CampaignSenderAttachRequest,
                        ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, ctx, campaign_id)            # 404 if not in workspace
    await _validate_workspace_owns_senders(db, ctx, [payload.sender_id])  # 404 SENDER_NOT_FOUND
    exists = (await db.execute(select(CampaignSender).where(
        CampaignSender.campaign_id == c.id,
        CampaignSender.sender_id == payload.sender_id))).first()
    if not exists:
        db.add(CampaignSender(campaign_id=c.id, sender_id=payload.sender_id,
                              workspace_id=ctx.workspace_id))
        await db.flush()
        conflicts = await _check_sender_lock(db, ctx, c.id)  # same 409 as /start
        if conflicts:
            await db.rollback()
            raise HTTPException(409, detail={"code": "SENDER_LOCK_CONFLICT", "conflicts": conflicts})
        if c.status == "running":
            await rebalance_on_attach(c.id, payload.sender_id, db)  # D-08
    await db.commit()
    await db.refresh(c)
    return await _campaign_to_response(db, ctx, c)
```

### Detach endpoint skeleton (D-03 + D-04 guards)
```python
@router.delete("/{campaign_id}/senders/{sender_id}", response_model=CampaignResponse)
async def detach_sender(campaign_id: UUID, sender_id: UUID,
                        ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    c = await _load_campaign(db, ctx, campaign_id)
    cnt = (await db.execute(select(sql_func.count()).select_from(CampaignSender)
                            .where(CampaignSender.campaign_id == c.id))).scalar()
    if c.status == "running" and cnt <= 1:
        raise HTTPException(409, detail={"code": "MIN_POOL_GUARD",
            "message": "Cannot detach the last sender of a running campaign."})
    has_cold = (await db.execute(text(COLD_PENDING_EXISTS_SQL),
                                 {"cid": str(c.id), "sid": str(sender_id)})).scalar()
    if has_cold:
        raise HTTPException(409, detail={"code": "DETACH_BLOCKED_PENDING",
            "message": "Sender still has un-sent contacts; pause or drain first."})
    await db.execute(delete(CampaignSender).where(
        CampaignSender.campaign_id == c.id, CampaignSender.sender_id == sender_id))
    await db.commit(); await db.refresh(c)
    return await _campaign_to_response(db, ctx, c)
```

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL 16 (`outreach-platform-db`) | All queries, rebalance, guards | ✓ | 16 | — |
| Docker Compose (api/listener/db) | Build + test-overlay | ✓ | — | — |
| Test overlay (`docker-compose.test.yml`, db-test tmpfs) | pytest | ✓ | — | — |
| `scripts/export-handoff.sh` (jq, node18+, rsync, docker) | OpenAPI regen | ✓ (script present) | — | hand-run on dev machine post-merge |
| Frontend repo `aimly-tg-outreach` | Senders panel | ✓ | — | — |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** None — all infra exists. (No external network/service calls added by this phase; rebalance is DB-only, no Telegram/OpenAI calls.)

## Validation Architecture

`nyquist_validation` is not disabled in config → section included. Distribution evenness and rebalance idempotency are directly measurable.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (repo) |
| Config file | `tests/conftest.py` (session DB setup + factories) + `docker-compose.test.yml` overlay |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_endpoints.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

### Phase Requirements → Test Map
| Req | Behavior | Type | Automated Command | File Exists? |
|-----|----------|------|-------------------|-------------|
| POOL-01 | attach to draft/paused/running 200 + row in campaign_senders | integration | `pytest tests/test_pool_endpoints.py::test_attach_adds_sender -x` | ❌ Wave 0 |
| POOL-02 | attach sender in other running campaign → 409 SENDER_LOCK_CONFLICT | integration | `...::test_attach_locked_sender_409 -x` | ❌ Wave 0 |
| POOL-03 | attach foreign-workspace sender → 404 | integration | `...::test_attach_foreign_sender_404 -x` | ❌ Wave 0 |
| POOL-04 | detach removes campaign_senders row | integration | `...::test_detach_removes_sender -x` | ❌ Wave 0 |
| POOL-05 | detach last sender of running → 409 MIN_POOL_GUARD | integration | `...::test_detach_last_running_409 -x` | ❌ Wave 0 |
| POOL-06 | detach with cold pending → 409 DETACH_BLOCKED_PENDING | integration | `...::test_detach_cold_pending_409 -x` | ❌ Wave 0 |
| POOL-06b | detach allowed when only engaged dialogs remain (D-05) | integration | `...::test_detach_engaged_only_ok -x` | ❌ Wave 0 |
| POOL-07 | attach to running with skewed backlog moves rows toward even split | integration | `pytest tests/test_rebalance.py::test_rebalance_evens_cold_pending -x` | ❌ Wave 0 |
| POOL-08 | rebalance idempotent (second call moves 0) | unit/integration | `...::test_rebalance_idempotent -x` | ❌ Wave 0 |
| POOL-08b | rebalance never moves sent/processing/engaged rows | integration | `...::test_rebalance_skips_non_cold -x` | ❌ Wave 0 |
| POOL-09 | (frontend) panel attach/detach + 409 render | manual-only | UAT in `campaigns.$id` panel | manual |

### Measurable acceptance signals
- **Evenness:** after attaching sender N to a running campaign with `total` movable cold-pending, assert each pool sender holds within `±1` of `total/P` (assert via `SELECT sender_id, COUNT(*) FROM message_queue WHERE campaign_id=:cid AND status='pending' GROUP BY sender_id`).
- **Idempotency:** call `rebalance_on_attach` twice; second call's moved-count == 0 (log/return value).
- **Safety:** seed a `sent` row + a `conversations` row for one recipient; assert that recipient's pending (if any) is never moved.

### Sampling Rate
- **Per task commit:** `pytest tests/test_pool_endpoints.py tests/test_rebalance.py -x`
- **Per wave merge:** full suite (includes existing `test_sender_lock.py`, `test_campaign_router.py`, `test_rotation_campaign.py` to catch contract regressions).
- **Phase gate:** full suite green before `/gsd:verify-work`.

### Wave 0 Gaps
- [ ] `tests/test_pool_endpoints.py` — POOL-01..06b (attach/detach + guards). Use existing fixtures: `async_client`, `valid_supabase_jwt`, `test_running_campaign_factory`, `attach_sender_to_campaign`, `test_sender_factory`, `test_campaign_factory`.
- [ ] `tests/test_rebalance.py` — POOL-07/08/08b. Needs a fixture to seed N cold-pending `message_queue` rows + CCA rows for a running campaign (extend `test_contacts_factory` / add a `queue_row_factory`).
- [ ] No new `conftest` fixture for queue rows currently exists — add `test_queue_item_factory(campaign_id, sender_id, recipient_phone, status='pending')`.
- Framework install: none — pytest infra already present and used by 13+ campaign test files.

*(Existing infra covers auth, campaign, sender factories. Only queue-row seeding + the two new test files are gaps.)*

## Open Questions

1. **BATCH_CAP for rebalance**
   - What we know: one transaction; large campaigns could have thousands of pending rows.
   - What's unclear: exact cap (500 proposed) and whether to loop until balanced across multiple calls.
   - Recommendation: cap at 500 per call (Claude's discretion per D-09); the move is a cheap UPDATE so a single pass is fine for v1's scale (all 4 current campaigns have 1 sender). Revisit if a campaign exceeds ~5k pending.

2. **Lock-check ordering: insert-then-rollback vs single-sender variant**
   - Recommendation: option A (insert → `_check_sender_lock` → rollback on conflict) to keep the 409 contract byte-identical. Document the rollback.

3. **`SENDER_LOCK_CONFLICT` frontend formatter mismatch (pre-existing)**
   - Backend sends `conflicts:[...]`; `error-codes.ts` reads `d.name`/`d.other`. Fix while touching the panel so attach errors are accurate.

4. **Should attach also be allowed via PATCH?**
   - CONTEXT D-12: no — PATCH ignores `sender_ids` by design. Update the stale `CampaignUpdate` docstring (schemas:625) but do not wire PATCH.

## Sources

### Primary (HIGH confidence — read in source this session)
- `app/routers/campaigns.py` — `_validate_workspace_owns_senders`:141, `_build_attached_senders`:194, `_campaign_to_response`:228, `_check_sender_lock`:275, `create_campaign`:304, `start_campaign`:578, `pause`:636, `resume`:657, `finish`:685, `stop`:707
- `app/services/rotation.py` — `get_or_assign_sender`:35, candidate filter:112-125, `_pick_least_loaded`:198 (global scope confirmed)
- `app/services/queue.py` — `_tick`:155, `_process_next_for_sender`:272 (`FOR UPDATE OF mq SKIP LOCKED`:313), `_check_rate_limits`:362
- `app/models/__init__.py` — `MessageQueue` (recipient_phone:204, status enum:17-21, campaign_id:SET NULL), `Conversation.contact_phone`, `CampaignContactAssignment`, `CampaignSender`
- `app/schemas/__init__.py` — `CampaignSenderAttach`:566, `CampaignCreate.sender_ids`:587, `CampaignUpdate` note:622-627
- `migrations/016_phase4.sql` — campaign_senders:55-66, campaign_contact_assignments:68-84, message_queue index:109 (confirms NO new migration)
- `aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx` — existing read-only Senders panel:363-407, `lifecycleMut`:89-103, `errMsg`:29
- `aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx` — `AccountsStep` toggle UI:1036-1095, senders query:141
- `aimly-tg-outreach/src/lib/api.ts` — ApiError envelope:88-115; `src/lib/error-codes.ts` — SENDER_LOCK_CONFLICT:16-19
- `lovable-handoff/openapi.json` — current campaign paths (no /senders endpoint yet)
- `scripts/export-handoff.sh` — openapi regen mechanism
- `tests/conftest.py` — `test_running_campaign_factory`:600, `attach_sender_to_campaign`:583, `test_campaign_factory`:476, `test_sender_factory`:359; `tests/test_campaign_router.py` helpers
- `.planning/proposals/sender-pool-resilience.md`, `.planning/codebase/ARCHITECTURE.md`, `INTEGRATIONS.md`
- `/root/CLAUDE.md`, `/root/apps/aimly/tg-outreach/CLAUDE.md` (constraints below)

### Secondary / Tertiary
- None — no external web sources needed; entirely internal reuse phase.

## Project Constraints (from CLAUDE.md)

- **Async everywhere** — all DB via `async/await` + `AsyncSession`. Rebalance is one async TX.
- **Migrations: raw SQL `NNN_short.sql`, idempotent, auto-applied at api start.** Phase 8 adds NONE (016 covers schema). If any micro-migration were ever needed it must be idempotent (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).
- **Never** `time.sleep()`, synchronous `requests`, `print()` (use `logging`).
- **Do NOT touch empirical rate-limit constants or queue intervals** (4/20/150, 3s poll) — CLAUDE.md hard guard. Rebalance must not alter `scheduled_at` rate semantics.
- **Do NOT break FloodWait/retry logic.**
- **Tests ONLY via test-overlay:** `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. Never bare `docker compose run --rm api pytest` (prod DROP SCHEMA risk — 2026-05-26 incident).
- **Sessions encrypted, API_KEY not in logs** (not directly touched this phase, but keep logging clean — log moved-row counts, not payloads).
- **Two-repo discipline:** backend commits → `Andrewbruce165/outreach-platform`; frontend (`aimly-tg-outreach`) commits → `AGS-Venture-Lab/aimly-tg-outreach`. `.planning/` lives in backend repo.
- **Russian for prose/communication, English for code & commits.**

## Metadata

**Confidence breakdown:**
- Standard stack / reuse points: HIGH — every helper, table, and column read in source this session.
- Rebalance algorithm (D-09): HIGH on data model + concurrency (verified against queue.py SKIP-LOCKED + 016 schema); MEDIUM on exact BATCH_CAP tuning (scale-dependent, deferred to plan).
- Detach guards: HIGH — predicate verified against Conversation/MessageQueue models.
- Migration need (none): HIGH — 016 read directly.
- Frontend: HIGH — existing panel + AccountsStep + api/error-codes read directly.

**Research date:** 2026-06-23
**Valid until:** ~2026-07-23 (stable internal codebase; re-verify if rotation.py or queue.py worker locking changes, or if Phase 7's rotation filter is revised).
