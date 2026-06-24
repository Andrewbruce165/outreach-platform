# Phase 9: Cold-Contact Failover - Research

**Researched:** 2026-06-24
**Domain:** Queue/rotation backend — atomic reassignment of cold-pending queue rows off a frozen sender onto healthy pool senders (FastAPI + SQLAlchemy 2.0 async + PostgreSQL 16)
**Confidence:** HIGH (grounded entirely in this repo's code; closest analog `rebalance.py` is already shipped and tested)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Триггер failover (когда/где)**
- **D-01:** Один **shared-хелпер** `failover_cold_backlog(sender_id)` (рабочее имя), вызывается **inline сразу после** того как sender помечен restricted и его pending поставлен на паузу. Нулевая задержка, без нового воркера, DRY через одну функцию.
- **D-02:** Точки вызова — **все** пути фриза, которые паузят pending:
  - `queue.py` PEER_FLOOD блок — после UPDATE `restriction_status='spam_limited'` + pause pending.
  - `queue.py` ACCOUNT_FROZEN блок — после UPDATE `restriction_status='frozen'` + pause pending (D-07).
  - `listener.py::_handle_antispam_signal` — после pause pending + флага spam_limited.
- **D-03:** **Осознанное ограничение:** inline-триггер НЕ подхватывает senders, замёрзших до деплоя, и pending, осевшее позже. Safety-net sweep НЕ делаем в этой фазе.

**Предикат «safe-to-failover» (что переносим)**
- **D-04:** Переносим queue-строку, если ВСЁ: (1) `status='pending'` AND `item_type=message`; (2) нет ни одной `'sent'`/`'processing'` queue-строки по (`campaign_id`, `recipient_phone`); (3) **диалог не начат**: нет `conversations`-строки по (`workspace_id`, `contact_phone`) **ИЛИ** строка есть, но в сообщениях нет ни одной строки (пустой диалог).
- **D-05:** Пустой Conversation (создан, но без сообщений) — всё ещё холодный контакт, безопасно перенести. Континуити ломается только если уже был обмен сообщениями.
- **D-06 (discretion на plan):** точная SQL-форма проверки «0 сообщений»; интент: «ни одного отправленного И ни одного полученного сообщения по этому контакту».

**Hard-freeze vs soft (объём срабатывания)**
- **D-07:** Failover применяется к **обоим** состояниям: **soft** `spam_limited` **и** **hard** `frozen`.
- **D-08:** Правило «отвечать с того же аккаунта» к hard не относится — но это про активные диалоги, которые не трогаем. Для cold backlog hard и soft эквивалентны.

**Анти-dogpile / распределение**
- **D-09:** Для **каждой** safe-строки заново вызывать `get_or_assign_sender` / `_pick_least_loaded` → ровный спред. Кандидат-фильтр rotation уже исключает restricted.
- **D-10:** Перенос = сменить `sender_id` queue-строки на нового + `scheduled_at=NOW()` (status остаётся `pending`) + **синхронно обновить** sticky `campaign_contact_assignments`.
- **D-11:** **Без явного cap** — rate-limiter (4/20/150) тротлит на отправке.
- **D-12:** **Логировать** что перенесено: сколько строк, с какого sender'а, на каких приёмников.

**Fallback**
- **D-13:** Нет здоровых приёмников → строки **остаются paused** на замёрзшем sender'е, ждут reconcile-resume. Failover = **best-effort**. Залогировать «некуда переносить».

### Claude's Discretion
- Точная SQL-форма предиката «0 сообщений» (D-06).
- Транзакционные границы хелпера (атомарный UPDATE queue + CCA), идемпотентность под параллельным воркером (по образцу Phase 8 rebalance: `FOR UPDATE SKIP LOCKED`).
- Сигнатура/имя `failover_cold_backlog`, где он живёт (`app/services/failover.py` vs. `rotation.py`/`queue.py`).
- Формат лог-сообщений (D-12) и уровень.
- Деривация требований фазы (FAIL-0x).

### Deferred Ideas (OUT OF SCOPE)
- **Safety-net sweep** (периодический воркер) — отвергнут (D-03).
- **Per-receiver day-headroom cap / batch-cap** — отвергнуты (D-11).
- **Видимость «cold backlog застрял»** (флаг/бейдж) → **Phase 10** (Pool Visibility). Здесь только лог.
- Failover **активных** диалогов — non-goal всего блока.
</user_constraints>

<phase_requirements>
## Phase Requirements (derived FAIL-0x — map to CONTEXT decisions)

| ID | Description | Decision | Research Support |
|----|-------------|----------|------------------|
| FAIL-01 | On freeze, the frozen sender's cold-pending backlog is reassigned to healthy pool senders via per-item `get_or_assign_sender`, inline, with zero added worker | D-01, D-09 | `rotation.get_or_assign_sender` already filters `restriction_status='none'` (rotation.py:121); the just-flagged sender is excluded automatically |
| FAIL-02 | Failover is invoked from ALL three freeze paths that pause pending: PEER_FLOOD (queue.py:733), ACCOUNT_FROZEN (queue.py:776), antispam-signal (listener.py:881) | D-02, D-07 | Three insertion points identified with exact line numbers below |
| FAIL-03 | A queue row is movable iff: `status='pending'` AND `item_type='message'` AND no `sent`/`processing` row for `(campaign_id, recipient_phone)` AND (no conversation OR conversation has zero messages) | D-04, D-05, D-06 | Predicate form resolved below; extends `rebalance._COLD_PENDING_PREDICATE` with the empty-conversation widening |
| FAIL-04 | Moving a row updates `message_queue.sender_id` + `scheduled_at=NOW()` (status stays `pending`) AND `campaign_contact_assignments.sender_id` in the SAME transaction | D-10 | Lock-step pattern lifted verbatim from rebalance.py:191-205 |
| FAIL-05 | Failover never moves engaged-dialog rows; engaged dialogs stay on the frozen sender and keep replying | D-04, D-08 | Replies not gated by restriction (verified: ai_engine has no restriction check); predicate excludes engaged via message-existence |
| FAIL-06 | Idempotent and concurrency-safe under the parallel queue worker (`FOR UPDATE OF mq SKIP LOCKED` + `status='pending'` guard) | discretion | Same discipline as rebalance.py:183 and worker queue.py claim |
| FAIL-07 | When no healthy receiver exists, rows stay paused on the frozen sender; nothing is lost or failed; logged "nowhere to move" | D-13 | reconcile-resume loop (listener.py:1402-1407) re-resumes them when the sender clears |
| FAIL-08 | Failover logs COUNT moved, source sender, and receiver senders — never recipient phones/payloads | D-12 | mirrors rebalance.py:209-213 logging discipline (CLAUDE.md: API_KEY/PII not in logs) |
| FAIL-09 | No migration: failover operates on existing columns only | code_context | Verified — all columns pre-exist (see Migration section) |
</phase_requirements>

## Summary

Phase 9 is a near-clone of the already-shipped, already-tested `app/services/rebalance.py` (Phase 8). Both do the same physical operation: a set-based, campaign-scoped, transaction-neutral move of *cold-pending* `message_queue` rows from one sender to another, keeping `campaign_contact_assignments` in lock-step, under `FOR UPDATE OF mq SKIP LOCKED` to avoid racing the worker. The differences are (a) the trigger (inline on freeze, not on attach), (b) the destination is chosen *per row* via `get_or_assign_sender` for an even spread across the whole healthy pool rather than back-filling one named sender, and (c) the "movable" predicate is intentionally **wider** — an *empty* conversation (created, zero messages) is still movable (D-05), whereas rebalance treats any conversation row as engaged.

The single most important code-grounded fact: **a real cold contact has NO conversation row at all.** The worker creates the `conversations` row only *after* a successful Telegram send (queue.py:1022-1043), in the same block where it inserts the first `outbound` row into the `messages` table. Inbound replies create the conversation via the listener (listener.py:798) but always with a saved inbound `messages` row. Therefore the "empty conversation" case (D-05) is a genuine edge (recontact fresh-start / deleted history) and the predicate must check message existence in the **`messages`** table by `conversation_id`, NOT in `messages_log`.

**Primary recommendation:** Create `app/services/failover.py::failover_cold_backlog(sender_id, db)` modeled directly on `rebalance.py`. Reuse `rotation.get_or_assign_sender(commit=False)` per row. Make it transaction-neutral (caller commits) at the antispam path; at the two queue.py paths it must own its own session (see Transaction Boundaries). No migration.

## Standard Stack

No new libraries. Everything is already in the repo:

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| SQLAlchemy | 2.0 async | raw `text()` SQL + `AsyncSession` | project rule: async everywhere, raw SQL for set-based moves |
| asyncpg / PostgreSQL | 16 | `FOR UPDATE ... SKIP LOCKED`, `ON CONFLICT` | concurrency primitives the whole queue is built on |
| Python `logging` | stdlib | COUNT-only audit log (D-12) | CLAUDE.md: never `print()`, no PII in logs |

**Installation:** none.

## Architecture Patterns

### Recommended location

```
app/services/
├── rebalance.py     # Phase 8 — attach-time even-split (THE TEMPLATE)
├── failover.py      # Phase 9 — NEW: freeze-time cold-backlog failover
├── rotation.py      # get_or_assign_sender / _pick_least_loaded (reused per-row)
└── queue.py         # 2 of 3 call sites (PEER_FLOOD, ACCOUNT_FROZEN)
```

**Decision: new file `app/services/failover.py` (recommended).** Rationale: it imports `rotation.get_or_assign_sender`; putting it in `rotation.py` muddies that module's single responsibility, and putting it in `queue.py` creates a circular import risk (queue.py is large and listener.py would import from it). A standalone module mirrors how `rebalance.py` was carved out for the same reason. The antispam call site lives in `listener.py`, so a neutral third module is the only place all three callers can import from without cycles.

### Pattern 1: Transaction-neutral set-based move (lifted from rebalance.py)

**What:** The helper does NOT commit; the caller owns the transaction. But Phase 9's three callers differ from Phase 8's single caller — see Transaction Boundaries below for the per-caller wiring.

**The movable-row reassignment, in the same TX (rebalance.py:191-205, copy this exactly):**
```python
# Source: app/services/rebalance.py:191-205 (shipped, tested)
for row in moved_rows:
    await db.execute(
        text("UPDATE message_queue SET sender_id = :new, scheduled_at = NOW() "
             "WHERE id = :rid"),
        {"new": new_sid, "rid": str(row.id)},
    )
    await db.execute(
        text("""
            UPDATE campaign_contact_assignments
            SET sender_id = :new
            WHERE campaign_id = :cid AND contact_phone = :phone
        """),
        {"new": new_sid, "cid": cid, "phone": row.phone},
    )
```
Note: rebalance.py does NOT touch `scheduled_at` (the row was already at NOW()). Phase 9 MUST add `scheduled_at = NOW()` (D-10) because the freeze path just pushed every pending row +24h (queue.py:745, listener.py:926); a moved row must be sendable immediately by its new healthy sender.

### Pattern 2: Per-row destination via rotation (the Phase 9-specific part)

**What:** Phase 8 back-fills ONE named sender. Phase 9 spreads each row across the whole healthy pool. Per D-09, call `get_or_assign_sender` per movable row so `_pick_least_loaded` picks the currently-least-loaded healthy candidate each time.

**Key subtlety — the existing-assignment short-circuit (rotation.py:71-97):** `get_or_assign_sender` first looks up the existing `campaign_contact_assignments` row. For a frozen sender's backlog, that CCA row still points at the **frozen** sender. At rotation.py:76 eligibility is computed as `lifecycle_status='active' AND auth_status='ok'` — which does **NOT** include `restriction_status`. A `spam_limited`/`frozen` sender is still `active/ok`, so `is_eligible=true`, so **`get_or_assign_sender` would return the frozen sender unchanged** (rotation.py:91-96). This is a landmine — see Pitfall 1. The helper must NOT naively delegate row selection to `get_or_assign_sender`; instead it should (a) select movable rows itself, then (b) for each, pick a fresh healthy sender and UPDATE both tables directly, OR (c) delete/repoint the stale CCA before calling rotation. The cleanest, lowest-risk approach: replicate rebalance's structure (select pool, count load, pick least-loaded among the *candidate filter that includes `restriction_status='none'`*, rotation.py:112-124) and move rows directly — i.e. reuse `_pick_least_loaded` over the healthy candidate set, NOT `get_or_assign_sender`'s short-circuit path.

> **Planner note:** This is the biggest deviation from the CONTEXT's literal wording. CONTEXT D-09 says "call `get_or_assign_sender` per row". Grounded in the actual code, calling it verbatim returns the frozen sender for its own backlog (because the stale CCA short-circuits before the restriction-aware candidate filter). Recommended resolution: honor the *intent* of D-09 (even spread over healthy pool via `_pick_least_loaded` + the restriction-aware candidate filter at rotation.py:112-124) while updating the CCA in lock-step (D-10) — which is exactly what `get_or_assign_sender` does at its Step 5 for the reassign branch. Either rewrite the stale CCA first then call `get_or_assign_sender`, or inline the candidate-filter + `_pick_least_loaded` + dual-UPDATE (rebalance pattern). The latter is lower-risk and already proven.

### Anti-Patterns to Avoid
- **Calling `get_or_assign_sender` without clearing the stale CCA** → returns the frozen sender (Pitfall 1).
- **Committing inside the helper at the antispam path** → the freeze write (pause + flag) and the failover move should land atomically; let the caller commit (rebalance CR-01).
- **Reusing `_pick_least_loaded` GLOBALLY without the campaign pool filter** → it counts assignments across all campaigns; you must first resolve the campaign's healthy pool (rebalance.py:99-114 does this).
- **Touching `processing` rows** → the worker flips to `processing` and commits before hitting Telegram; the `status='pending'` guard + `SKIP LOCKED` is what keeps you off in-flight sends.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Worker-safe row claim | custom advisory locks / SELECT-then-UPDATE | `FOR UPDATE OF mq SKIP LOCKED` + `status='pending'` | exact discipline the worker uses (queue.py); proven in rebalance.py:183 |
| Even spread over pool | round-robin counter | `_pick_least_loaded` over the campaign healthy-candidate set | rotation.py:198, already load-balances by live CCA count |
| Healthy-pool resolution | re-derive eligibility inline | candidate filter rotation.py:112-124 (`restriction_status='none'` etc.) | single source of truth; copied verbatim into rebalance.py:99-114 |
| CCA + queue sync | two separate transactions | one TX, queue UPDATE + CCA UPDATE back-to-back | observer never sees the two disagree (rebalance.py:191-205) |
| Re-resume leftover paused rows | new background job | existing restriction-reconcile loop | listener.py:1402-1407 already pulls paused pending back to NOW() on `free` verdict |

**Key insight:** Phase 9 is a behavioral twin of Phase 8 rebalance. The entire transactional/concurrency machinery is already written, shipped, and covered by `tests/test_rebalance.py`. Reuse it; do not reinvent.

## D-06 Resolution: exact "0 messages" predicate

**Schema facts (grounded):**
- Two message tables exist:
  - **`messages_log`** (models __init__.py:108) — `recipient_phone VARCHAR(40)`, `sender_id`, `workspace_id`, `message_type` enum = **`sent`/`draft`/`failed`** (models __init__.py:10-13). This is the legacy *outbound* send log. It has **no** inbound/incoming concept.
  - **`messages`** (migration 017) — `conversation_id` NOT NULL, `direction VARCHAR(20)` = `'inbound'|'outbound'`, `sent_by` = `'contact'|'ai'|'human'`. This is the Phase 5 inbox conversation history holding **both** directions. This is the table to anchor on.
- A cold contact (never sent) has **no `conversations` row** — the worker creates it only after a successful send (queue.py:1022-1043) and inserts the first `outbound` `messages` row in the same block.
- An inbound reply creates the conversation (listener.py:798) always together with a saved inbound `messages` row (listener.py:811-817).

**Recommended predicate (extends rebalance.py:50-64):**
```sql
mq.status = 'pending'
AND mq.item_type = 'message'
AND mq.sender_id = :frozen_sid
AND NOT EXISTS (                              -- (D-04.2) never sent in this campaign
    SELECT 1 FROM message_queue s
    WHERE s.campaign_id = mq.campaign_id
      AND s.recipient_phone = mq.recipient_phone
      AND s.status IN ('sent', 'processing')  -- WIDER than rebalance: include processing
)
AND NOT EXISTS (                              -- (D-04.3 + D-05) no STARTED dialog
    SELECT 1 FROM conversations cv
    JOIN messages m ON m.conversation_id = cv.id
    WHERE cv.workspace_id = mq.workspace_id
      AND cv.contact_phone = mq.recipient_phone
)
```

**Why this form (decisions resolved):**
- **`messages` not `messages_log`** for the "0 messages" check: `messages_log` only logs outbound sends and lacks an inbound concept, so it cannot satisfy the intent "no message sent AND none received." `messages` covers both directions via `direction`.
- **Anchor by `conversation_id` (JOIN), not by phone in `messages`:** `messages` has no `recipient_phone` column — it is keyed only by `conversation_id`. So you must join `conversations` on `(workspace_id, contact_phone)` then check `messages.conversation_id`. The `JOIN messages` form makes the whole `NOT EXISTS` true ("movable") when either (a) no conversation row exists at all, OR (b) a conversation exists but has zero messages — which is exactly the D-05 widening ("empty conversation is still cold"). A bare `NOT EXISTS conversations` (rebalance's form) would wrongly block an empty conversation.
- **`message_type` incoming/outgoing not treated separately:** in `messages` the column is `direction`. The intent (D-06) is "no message of EITHER direction" → do not filter by `direction` at all; any `messages` row for the conversation means engaged. Confirmed against ai_engine.py:550 (`direction == "inbound"` → user, else assistant) — both directions are real traffic.
- **Include `processing` in the "never sent" check** (D-04.2 says `'sent'`/`'processing'`): rebalance.py only checks `'sent'`. Phase 9's CONTEXT explicitly lists both. Include `processing` so a row mid-send (claimed by the worker on another, non-frozen sender for the same phone — rare but possible across recontact) is not double-handled.

> **Identity-key caveat (from CLAUDE.md / migration 025):** `recipient_phone`, `conversations.contact_phone`, `messages_log.recipient_phone`, and `campaign_contact_assignments.contact_phone` are all `VARCHAR(40)` and may hold either a `+phone` or an `@username`. All four use the same key, so the joins above are consistent. No normalization needed — they were written by the same enqueue/rotation code paths.

## Transaction Boundaries (discretion resolved)

The three call sites differ in session ownership — this is the trickiest part of the wiring.

| Call site | Current session shape | Recommended failover wiring |
|-----------|----------------------|-----------------------------|
| `listener.py::_handle_antispam_signal` (881) | opens `async with AsyncSessionLocal() as session:` (919), does pause+flag UPDATEs, `session.commit()` (946) | Call `failover_cold_backlog(sender_id, session)` **before** `session.commit()`, transaction-neutral (no commit in helper). Pause+flag+failover land in ONE commit — same as rebalance/attach. |
| `queue.py` PEER_FLOOD (733) | uses a **separate** short-lived `async with AsyncSessionLocal() as db2:` (743) that commits at 754; the outer `db` is the worker's per-item session | After `db2.commit()`, either (a) reuse `db2` before its commit, or (b) open a fresh session inside the helper. **Recommended:** make the helper open its OWN session when called from queue.py (pass `db=None` → helper does `async with AsyncSessionLocal()`), because the freeze write here is already committed in `db2` and the worker's `db` is mid-item (it will `_fail_item` + return right after). Keeping failover in its own committed transaction is safe and matches "best-effort." |
| `queue.py` ACCOUNT_FROZEN (776) | identical `db2` pattern (784), commits at 795 | Same as PEER_FLOOD: helper opens its own session. |

**Recommended helper signature (supports both modes):**
```python
async def failover_cold_backlog(
    frozen_sender_id: UUID,
    db: AsyncSession | None = None,
) -> int:
    """Move the frozen sender's cold-pending backlog onto healthy pool senders.
    If db is None, opens+commits its own session (queue.py callers). If a session
    is passed (listener antispam path), runs transaction-neutral (caller commits).
    Returns total rows moved (0 if nothing movable or no healthy receiver — D-13).
    """
```

**Idempotency / worker race (FAIL-06):** identical guarantees to rebalance — `status='pending'` guard excludes rows the worker already flipped to `processing`; `FOR UPDATE OF mq SKIP LOCKED` skips rows the worker is locking. A second failover call moves 0 (the frozen sender's rows already moved away; its remaining rows are engaged/non-movable). The frozen sender must be excluded as a *receiver* — see Pitfall 1.

**Cross-campaign scope:** `failover_cold_backlog` is keyed on `sender_id`, not `campaign_id`. A sender can theoretically belong to >1 campaign, though sender-lock prevents 2 *running* campaigns. The movable predicate is per-`(campaign_id, recipient_phone)`. Resolve the per-campaign healthy pool for each campaign the frozen sender has cold-pending in (group the backlog by `campaign_id`, resolve pool per campaign). At v1 scale every campaign has 1–few senders; iterating campaigns is cheap.

## Common Pitfalls

### Pitfall 1: `get_or_assign_sender` returns the frozen sender for its own backlog
**What goes wrong:** The frozen sender's `campaign_contact_assignments` rows still point at it. `get_or_assign_sender` short-circuits on the existing CCA (rotation.py:71-97) and its eligibility check (rotation.py:76) is `active AND ok` — it does NOT consider `restriction_status`. So it returns the frozen sender, moving nothing.
**Why it happens:** The restriction-aware filter is only in the *fresh-assignment* candidate query (rotation.py:121), not in the existing-assignment short-circuit.
**How to avoid:** Do not delegate selection to `get_or_assign_sender`'s short-circuit. Either (a) inline the rebalance pattern (resolve healthy pool via rotation.py:112-124 filter → `_pick_least_loaded` → dual UPDATE), or (b) repoint/clear the stale CCA before calling rotation. Recommended: option (a) — proven, lowest-risk, no rotation.py change.
**Warning signs:** a test where the frozen sender still holds its backlog after failover; `moved == 0` when a healthy receiver exists.

### Pitfall 2: forgetting `scheduled_at = NOW()` on the moved row
**What goes wrong:** The freeze path pushed every pending row +24h (queue.py:745 / listener.py:926). If failover only changes `sender_id`, the row sits idle for 24h on the healthy sender too — defeating "zero idle wait."
**How to avoid:** D-10 requires `scheduled_at = NOW()` in the same UPDATE (rebalance does NOT do this — it's the one place you must diverge from the template).
**Warning signs:** moved rows not picked up by the worker until tomorrow.

### Pitfall 3: ordering of freeze-flag write vs failover call
**What goes wrong:** If `failover_cold_backlog` runs *before* the sender is flagged `restriction_status != 'none'`, the candidate filter (rotation.py:121) still includes the frozen sender as a valid *receiver*, so it can hand the backlog right back to itself.
**How to avoid:** D-01 already mandates "inline сразу после" the flag write. At all three sites the `UPDATE senders SET restriction_status=...` is committed (queue.py:753/794, listener.py:946) before the failover call. Keep that order. If the listener path runs transaction-neutral in the same TX, the UPDATE is visible to the subsequent SELECT within the session — verify the flag UPDATE precedes the failover call in statement order.
**Warning signs:** a moved row's new `sender_id` equals the frozen sender.

### Pitfall 4: empty-conversation predicate regressing engaged dialogs
**What goes wrong:** Over-widening the predicate (e.g. dropping the `messages` JOIN) would move rows whose contact already replied → breaks continuity (the whole block's prime non-goal).
**How to avoid:** the `JOIN messages` form is exact: movable iff zero `messages` rows. `tests/test_rebalance.py::test_rebalance_skips_non_cold` is the regression template — Phase 9 needs a variant that distinguishes empty-conversation (movable, D-05) from has-message conversation (NOT movable).
**Warning signs:** a contact who replied gets a duplicate first-touch from a second account.

### Pitfall 5: logging PII
**What goes wrong:** logging recipient phones violates CLAUDE.md ("API_KEY не в логах", PII discipline).
**How to avoid:** log COUNT + sender UUIDs only (rebalance.py:209-213 is the exact template). D-12 asks for "сколько строк, с какого sender'а, на каких приёмников" — all UUIDs/counts, no phones.

## Code Examples

### Healthy-pool resolution + least-loaded (reuse, do not rebuild)
```python
# Source: app/services/rebalance.py:99-114 (candidate filter is rotation.py:112-124 verbatim)
pool_rows = (await db.execute(text("""
    SELECT s.id AS sid
    FROM campaign_senders cs
    JOIN senders s ON s.id = cs.sender_id
    JOIN campaigns c ON c.id = cs.campaign_id
    WHERE cs.campaign_id = :cid
      AND s.lifecycle_status = 'active'
      AND s.auth_status = 'ok'
      AND s.role = 'sender'
      AND s.restriction_status = 'none'   -- excludes the just-frozen sender (Pitfall 1/3)
      AND s.workspace_id = c.workspace_id
"""), {"cid": cid})).fetchall()
```

### Reconcile-resume fallback already handles D-13 (no code needed)
```python
# Source: app/services/listener.py:1402-1407 — on SpamBot 'free' verdict
await db.execute(text("""
    UPDATE message_queue SET scheduled_at = NOW()
    WHERE sender_id = :sid AND status = 'pending'
      AND scheduled_at > NOW()
"""), {"sid": str(r[0])})
```
Rows that could NOT be moved (no healthy receiver, D-13) keep `sender_id = frozen_sender` and `scheduled_at = +24h`; when the frozen sender clears, this loop pulls them back. Rows that WERE moved have a different `sender_id`, so this loop won't touch them — no double-resume. Confirmed correct.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Antispam → terminal `failed`, AI disabled everywhere | soft `spam_limited` + pause pending, replies keep flowing | Phase 7 (2026-06) | Phase 9 builds on the Phase 7 soft-restriction model; the backlog to fail over is `pending` (not `failed`) |
| Single sender per campaign, backlog waits for own recovery | pool + even spread + attach-time rebalance | Phase 8 (2026-06) | rebalance.py is the direct template for Phase 9 |

**Deprecated/outdated:**
- `messages_log` as a "did we contact them" signal: it only logs outbound sends, lacks inbound. Use `messages` (both directions) for the engaged check.
- rebalance's `NOT EXISTS conversations` predicate: too strict for Phase 9 (would block empty conversations that D-05 wants movable). Phase 9 must use the `JOIN messages` form.

## Open Questions

1. **Cross-campaign backlog grouping**
   - What we know: helper is keyed on `sender_id`; movable predicate is per-`(campaign_id, recipient_phone)`; sender-lock prevents 2 *running* campaigns but a sender could have pending in a paused campaign.
   - What's unclear: whether to fail over backlog only for the campaign(s) where the sender is currently a running-pool member, or all.
   - Recommendation: group the frozen sender's movable rows by `campaign_id`, resolve the healthy pool per campaign, skip campaigns where the pool has <2 eligible. Cheap at v1 scale. Plan should state the chosen scope explicitly.

2. **`item_type='file'` rows**
   - What we know: D-04.1 restricts to `item_type='message'`. File items exist (`QueueItemType.file`).
   - What's unclear: nothing — file cold-pending is simply not moved (stays paused, resumes on recovery).
   - Recommendation: include `mq.item_type = 'message'` in the predicate exactly as D-04 says; file backlog falls to the D-13 fallback path. Note it in the plan so it's a conscious limit, not an oversight.

## Environment Availability

Step 2.6: SKIPPED (no external dependencies — pure backend code/SQL on existing schema; tests run on the existing ephemeral-postgres test-overlay).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`pytestmark = pytest.mark.asyncio`) |
| Config file | `tests/conftest.py` (ephemeral postgres via `docker-compose.test.yml`) |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_failover.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

> **CLAUDE.md hard rule:** NEVER `docker compose run --rm api pytest` without the test-overlay — the conftest guard (tests/conftest.py:46-77) blocks it, but the correct path is always the overlay (DATABASE_URL → ephemeral `outreach_test`, tmpfs, auto-removed). A 2026-05-26 incident wiped prod via this exact mistake.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FAIL-01 | frozen backlog spreads to healthy pool | unit | `pytest tests/test_failover.py::test_failover_spreads_to_healthy_pool -x` | ❌ Wave 0 |
| FAIL-03 | predicate moves only cold-pending | unit | `pytest tests/test_failover.py::test_failover_skips_engaged -x` | ❌ Wave 0 |
| FAIL-03/D-05 | empty conversation IS movable | unit | `pytest tests/test_failover.py::test_failover_moves_empty_conversation -x` | ❌ Wave 0 |
| FAIL-04 | queue + CCA in sync after move | unit | `pytest tests/test_failover.py::test_failover_cca_in_sync -x` | ❌ Wave 0 |
| FAIL-05 | engaged dialog stays on frozen sender | unit | `pytest tests/test_failover.py::test_failover_leaves_engaged -x` | ❌ Wave 0 |
| FAIL-06 | idempotent (2nd call moves 0) | unit | `pytest tests/test_failover.py::test_failover_idempotent -x` | ❌ Wave 0 |
| FAIL-07 | no healthy receiver → rows stay paused | unit | `pytest tests/test_failover.py::test_failover_no_receiver_keeps_paused -x` | ❌ Wave 0 |
| FAIL-01/D-09 | frozen sender excluded as receiver (Pitfall 1) | unit | `pytest tests/test_failover.py::test_failover_excludes_frozen_as_receiver -x` | ❌ Wave 0 |
| FAIL-02 | each of 3 call sites invokes failover | integration | `pytest tests/test_failover.py::test_peer_flood_triggers_failover -x` (+ frozen + antispam) | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_failover.py -x` (overlay)
- **Per wave merge:** full suite (overlay) — currently ~683 collected; must stay green
- **Phase gate:** full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_failover.py` — covers FAIL-01..FAIL-09 (RED stubs, import-inside-body pattern from `tests/test_rebalance.py:51` so `--collect-only` stays clean)
- [ ] Fixture extension: `test_queue_item_factory` (conftest.py:600) supports `with_conversation` but inserts NO `messages` row. To test D-05 (empty conversation movable) vs engaged (has message), add an optional `with_message=True` flag (or use `test_conversation_factory` at conftest.py:696 + a `messages` insert). The empty-conversation case is already producible (`with_conversation=True, with_message=False`); only the has-message case needs new fixture support.
- [ ] Reuse `test_running_campaign_factory(sender_count=N)` (conftest.py:680) and `_pending_counts`/`_cca_sender_for` helpers (test_rebalance.py:26-41) — copy them.
- Framework install: none (pytest-asyncio + overlay already in place).

## Project Constraints (from CLAUDE.md)

- **Async everywhere:** all DB via `async/await` + `AsyncSession`. No `time.sleep()`, no sync `requests`, no `print()`.
- **Migrations:** raw SQL `NNN_short_name.sql` in `migrations/`, idempotent, auto-applied on api start. **Phase 9 needs NONE** — all columns pre-exist. If one were ever needed, next number is `030_`.
- **Rate-limiter intervals (4/min, 20/hr, 150/day) and FloodWait retry: DO NOT TOUCH** without explicit discussion. Failover relies on them as the natural throttle (D-11) — must not modify them.
- **Tests ONLY via test-overlay** (`docker-compose.yml` + `docker-compose.test.yml`). Never the bare command.
- **No PII / API_KEY in logs.** Failover logs COUNT + sender UUIDs only (D-12).
- **Empirical 24h queue pause on freeze:** untouched. Failover sets moved rows to `NOW()` on the *new* sender; it does not alter the pause applied to non-movable rows on the frozen sender.
- **Communicate in Russian, code/commits in English.** (process rule for the implementer)

## Sources

### Primary (HIGH confidence — this repo, read directly)
- `app/services/rebalance.py:1-215` — the direct template (transaction-neutral, FOR UPDATE SKIP LOCKED, dual-UPDATE, COUNT-only log)
- `app/services/rotation.py:35-217` — `get_or_assign_sender` short-circuit (71-97), candidate filter incl. `restriction_status='none'` (112-124), `_pick_least_loaded` (198)
- `app/services/queue.py:397-406` (worker skip restricted), `:700-812` (FLOOD_WAIT/PEER_FLOOD@733/ACCOUNT_FROZEN@776 call sites), `:1000-1058` (conversation+message created at send time)
- `app/services/listener.py:881-957` (`_handle_antispam_signal` call site), `:1351-1448` (restriction-reconcile resume — D-13 fallback)
- `app/models/__init__.py:10-26` (enums: MessageType=sent/draft/failed, QueueItemStatus, QueueItemType), `:108-127` (messages_log), `:190-273` (MessageQueue, Conversation), `:533-571` (CampaignSender, CampaignContactAssignment)
- `migrations/017_phase5.sql` (`messages` table: conversation_id/direction/sent_by) — anchor for the "0 messages" check
- `tests/conftest.py:600-691` (test_queue_item_factory, test_running_campaign_factory), `tests/test_rebalance.py:1-204` (test patterns to clone)
- `.planning/phases/09-cold-contact-failover/09-CONTEXT.md` (D-01..D-13), `.planning/proposals/sender-pool-resilience.md` (Phase C), `.planning/phases/08-.../08-CONTEXT.md` (D-08 sync pattern), `.planning/REQUIREMENTS.md` (POOL-06/07)

### Secondary / Tertiary
- None — no web research needed; phase is fully internal.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new deps; reuses shipped, tested machinery.
- Architecture: HIGH — `rebalance.py` is a working, tested twin; deviations (per-row spread, empty-conversation predicate, `scheduled_at=NOW()`, multi-caller transaction wiring) are precisely identified and grounded.
- D-06 predicate: HIGH — schema and send-path code read directly; two-table (`messages` vs `messages_log`) distinction confirmed.
- Pitfalls: HIGH — Pitfall 1 (rotation short-circuit returns frozen sender) is the key non-obvious landmine, verified at rotation.py:71-97 + 76.

**Research date:** 2026-06-24
**Valid until:** 2026-07-24 (stable internal codebase; re-verify if rotation.py/rebalance.py/queue.py freeze blocks change)
