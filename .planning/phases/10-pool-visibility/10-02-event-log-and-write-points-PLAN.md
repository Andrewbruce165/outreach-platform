---
phase: 10-pool-visibility
plan: 02
type: execute
wave: 2
depends_on: [01]
files_modified:
  - migrations/030_sender_restriction_events.sql
  - app/models/__init__.py
  - app/services/restriction_audit.py
  - app/services/queue.py
  - app/services/listener.py
autonomous: true
requirements: [HLTH-01, HLTH-02]
must_haves:
  truths:
    - "Every restriction state-change (PEER_FLOOD→spam_limited, ACCOUNT_FROZEN→frozen, reconcile→cleared/banned) writes exactly one append-only event row in the SAME transaction as the senders.restriction_status UPDATE"
    - "A reconcile 'still-limited' tick writes an 'extension' event ONLY when restricted_until moves forward by a meaningful margin, compared against the old_until read INSIDE the per-sender reconcile transaction; a pure recheck-interval bump writes NO event (D-01)"
    - "Each restriction event carries an activity_slice snapshot (sends 1h/24h, unique contacts, configured-vs-actual rate) computed at write time from messages_log, plus a proxy snapshot"
    - "Recipient-privacy errors are logged with category='recipient_privacy' on the live queue send-loop session and never flip senders.restriction_status (D-03)"
  artifacts:
    - path: "migrations/030_sender_restriction_events.sql"
      provides: "Idempotent append-only table sender_restriction_events with category CHECK + 2 indexes"
      contains: "CREATE TABLE IF NOT EXISTS sender_restriction_events"
    - path: "app/services/restriction_audit.py"
      provides: "record_restriction_event dual-mode helper + slice computation"
      exports: ["record_restriction_event"]
      min_lines: 60
    - path: "app/models/__init__.py"
      provides: "SenderRestrictionEvent ORM model"
      contains: "class SenderRestrictionEvent"
  key_links:
    - from: "app/services/queue.py"
      to: "record_restriction_event"
      via: "call inside existing db2 block before commit (PEER_FLOOD, ACCOUNT_FROZEN, FLOOD_WAIT); PRIVACY_RESTRICTED on the live send-loop db session"
      pattern: "record_restriction_event\\(.*db=db2"
    - from: "app/services/listener.py"
      to: "record_restriction_event"
      via: "call inside antispam session + reconcile per-verdict db block before commit; reconcile reads old_until intra-transaction"
      pattern: "record_restriction_event\\(.*db="
    - from: "app/services/restriction_audit.py"
      to: "messages_log + senders"
      via: "slice SELECT (sends/unique/rate) + proxy/limits SELECT in same session"
      pattern: "FROM messages_log"
---

<objective>
Построить durable append-only event-log restriction-событий (HLTH-01) со срезом предшествующей активности (HLTH-02) и врезать запись событий во все пять точек смены restriction-статуса. Запись события идёт в ТОЙ ЖЕ транзакции, что и UPDATE `senders.restriction_status`, через dual-mode helper (паттерн `failover_cold_backlog`). Это закрывает слепую зону «что делали → за что получили».

Purpose: Сегодня истории restriction нет — `senders.restriction_status` хранит только текущее состояние, `message_queue.error_message` затирается на reschedule, логи контейнера живут ~18ч (см. account-restriction-audit-gap.md). Append-only лог + снапшот среза в момент события делают историю реконструируемой.
Output: миграция 030, ORM-модель, app/services/restriction_audit.py, врезки в queue.py (3 точки + privacy) и listener.py (антиспам + reconcile 3 ветки).

Open-question decisions adopted in this plan (CONTEXT grants discretion):
- OQ#1: hard-FloodWait (queue.py:704) пишет ИНФОРМАЦИОННОЕ событие event_type='flood_wait' (restricted_until=reschedule_at, category='restriction'), НО НЕ трогает senders.restriction_status и НЕ влияет на pool_health.
- OQ#2: добавить третье значение source='antispam_signal' для listener-антиспам-пути. Это расширение иллюстративного перечня источников D-02, допустимое под D-04 discretion — `source` это free-form VARCHAR БЕЗ CHECK-ограничения (CHECK навешен только на `category`). Не нарушает D-02.
- OQ#3: PRIVACY_RESTRICTED логируется category='recipient_privacy' и в этой фазе ОБЯЗАТЕЛЕН (его требует контракт Wave 1 — `test_recipient_privacy_separate_category`). PRIVACY_PREMIUM_REQUIRED detection — явно ВНЕ scope (остаётся в SEND_FAILED catch-all; добавление новой RPCError-ветки отложено), не блокирует HLTH-01.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/10-pool-visibility/10-RESEARCH.md
@.planning/phases/10-pool-visibility/10-PATTERNS.md
@.planning/phases/10-pool-visibility/10-CONTEXT.md
@.planning/notes/account-restriction-audit-gap.md

<interfaces>
<!-- Contracts produced by this plan (consumed by Wave 3 + the Wave 1 tests). -->

Helper signature (mirror failover.py:87-114 dual-mode):
  async def record_restriction_event(sender_id, event_type, source,
      restricted_until, raw_text, category="restriction", db=None) -> None
db is None → open own AsyncSessionLocal() + commit; db passed → core write, caller commits.

Table sender_restriction_events columns:
  id UUID PK, workspace_id UUID FK→workspaces CASCADE, sender_id UUID FK→senders CASCADE,
  category VARCHAR(20) DEFAULT 'restriction' CHECK IN ('restriction','recipient_privacy'),
  event_type VARCHAR(20), source VARCHAR(20), restricted_until TIMESTAMPTZ NULL,
  raw_text TEXT NULL, activity_slice JSONB NULL, proxy JSONB NULL,
  created_at TIMESTAMPTZ DEFAULT now().

Note: `category` is the ONLY CHECK-constrained column. `source` is free-form VARCHAR — so adding
'antispam_signal' as a source value (OQ#2) is permitted under D-04 discretion and is NOT a D-02 violation.

activity_slice shape: {sends_1h, sends_24h, unique_contacts_1h, unique_contacts_24h,
  rate:{configured_per_min, configured_per_hour, configured_per_day, actual_per_hour, actual_per_day}}
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Migration 030 + SenderRestrictionEvent ORM model</name>
  <read_first>
    - migrations/028_sender_restriction.sql (full file — idempotent CHECK drop+recreate L20-28, leading WHY comment L1-16, gen_random_uuid default) — EXACT analog per 10-PATTERNS.md
    - migrations/029_campaign_pause_reason.sql (confirms 030 is next number)
    - app/database.py (_apply_migrations contract: lexical order, idempotent, fail-fast)
    - app/models/__init__.py L108-127 (MessageLog: workspace_id + sender_id FK + JSONB + created_at server_default — column-style analog) and L87/L93-97 (Sender.proxy JSONB, restriction_status, restricted_until, rate_per_min/hour/day)
    - .planning/phases/10-pool-visibility/10-RESEARCH.md §Proposed Table & Schema (full CREATE TABLE body + index defs + category enum + recipient classes)
  </read_first>
  <action>
    Create migrations/030_sender_restriction_events.sql. Lead with a comment block (mirror 028 L1-16) explaining WHY: durable append-only restriction event-log because senders.restriction_status is current-only, message_queue.error_message is overwritten on reschedule, telemetry_events never records restriction changes, container logs live ~18h. Create table `sender_restriction_events` with `CREATE TABLE IF NOT EXISTS`: columns id (UUID PK DEFAULT gen_random_uuid()), workspace_id (UUID NOT NULL FK→workspaces ON DELETE CASCADE), sender_id (UUID NOT NULL FK→senders ON DELETE CASCADE), category (VARCHAR(20) NOT NULL DEFAULT 'restriction'), event_type (VARCHAR(20) NOT NULL), source (VARCHAR(20) NOT NULL — free-form, NO CHECK), restricted_until (TIMESTAMPTZ NULL), raw_text (TEXT NULL), activity_slice (JSONB NULL), proxy (JSONB NULL), created_at (TIMESTAMPTZ NOT NULL DEFAULT now()). Add two indexes with `CREATE INDEX IF NOT EXISTS`: idx_sre_sender_created on (sender_id, created_at DESC) and idx_sre_workspace_category on (workspace_id, category, created_at DESC). Add the category CHECK using the 028 drop+recreate idiom: `ALTER TABLE ... DROP CONSTRAINT IF EXISTS sre_category_chk` then `ADD CONSTRAINT sre_category_chk CHECK (category IN ('restriction','recipient_privacy'))`. Do NOT add a CHECK on `source` — it is intentionally free-form (OQ#2 'antispam_signal' is a valid value). File must be fully idempotent (re-runs on drift; api won't start if it errors).

    In app/models/__init__.py add `class SenderRestrictionEvent(Base)` with `__tablename__ = "sender_restriction_events"`, mirroring MessageLog column style: id (UUID PK default uuid.uuid4), workspace_id + sender_id as Column(UUID(as_uuid=True), ForeignKey(..., ondelete="CASCADE"), nullable=False), category/event_type/source as String, restricted_until as DateTime(timezone=True) nullable, raw_text as Text nullable, activity_slice + proxy as Column(JSONB) nullable, created_at as Column(DateTime(timezone=True), server_default=func.now()). Model is for ORM reads in the Wave 3 history endpoint (from_attributes); migration is DDL source of truth (do not rely on create_all).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "from app.models import SenderRestrictionEvent; print(SenderRestrictionEvent.__tablename__)"</automated>
  </verify>
  <acceptance_criteria>
    - `migrations/030_sender_restriction_events.sql` exists; `grep -q "CREATE TABLE IF NOT EXISTS sender_restriction_events" migrations/030_sender_restriction_events.sql` and `grep -q "sre_category_chk" migrations/030_sender_restriction_events.sql` both succeed.
    - Migration is idempotent: applying it twice (the applier runs on api start; a second start is a no-op) does not error.
    - Importing `SenderRestrictionEvent` from app.models succeeds and `__tablename__ == "sender_restriction_events"`.
    - Under test-overlay, `SELECT to_regclass('sender_restriction_events')` returns non-NULL after api/applier start.
  </acceptance_criteria>
  <done>Table sender_restriction_events created idempotently with category CHECK + 2 indexes; ORM model importable.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: restriction_audit.py helper (dual-mode write + activity-slice snapshot)</name>
  <behavior>
    - record_restriction_event(db=None) opens its own session and commits; record_restriction_event(db=session) writes on the caller's session without committing.
    - For category='restriction': computes activity_slice from messages_log (sends_1h, sends_24h via COUNT(*) FILTER message_type='sent'; unique_contacts_1h/24h via COUNT(DISTINCT recipient_phone) FILTER; rate with configured_per_min/hour/day from senders + actual_per_hour=sends_1h, actual_per_day=sends_24h) and stores senders.proxy snapshot.
    - For category='recipient_privacy': activity_slice is NULL; the row is inserted but no senders.restriction_status is touched (the helper never UPDATEs senders).
    - Slice counts only message_type='sent' and windows by created_at (rows older than 1h excluded from sends_1h).
    - workspace_id is read from the sender row and denormalized onto the event.
  </behavior>
  <read_first>
    - app/services/failover.py L1-45 (module docstring convention) + L87-114 (dual-mode db=None dispatcher + _failover core "no commit — caller decides") — EXACT analog to COPY
    - .planning/phases/10-pool-visibility/10-RESEARCH.md §Code Examples (helper skeleton L337-387: slice SELECT over messages_log + INSERT) and §Activity-Slice Design (windows, sent-only filter, rate formula)
    - app/services/queue.py L743-754 (db2 block context the helper is called within)
    - app/models/__init__.py L108-124 (messages_log columns) + L87/L93-97 (senders.proxy, rate_per_*)
    - tests/test_restriction_audit.py (Wave 1 RED stubs — the contract this task turns GREEN)
  </read_first>
  <action>
    Create app/services/restriction_audit.py. Module docstring (mirror failover.py L1-45): Phase 10 tag + "Why this exists" (durable audit over ephemeral sources) + "Session ownership" (same-TX guarantee) + note D-01 (event only on state-change/forward-shift — the GATE lives in the listener call-site, not here) and D-05 (slice snapshot at write time). Imports per failover.py:47-56 plus `import json` and `from datetime import datetime`.

    Public `record_restriction_event(sender_id, event_type, source, restricted_until, raw_text, category="restriction", db=None)` dispatches on `db is None` (open `AsyncSessionLocal()`, call `_record`, commit) vs passed (call `_record`, caller commits) — copy failover.py:87-114 shape verbatim. Private `_record(db, sender_id, event_type, source, restricted_until, raw_text, category)`: (1) SELECT workspace_id, proxy, rate_per_min, rate_per_hour, rate_per_day FROM senders WHERE id=:sid. (2) If category=='restriction', compute the slice with ONE SELECT over messages_log (COUNT(*) FILTER WHERE created_at >= now()-interval '1 hour' / '24 hours' AND message_type='sent'; COUNT(DISTINCT recipient_phone) FILTER for unique_contacts_1h/24h) and build the activity_slice dict {sends_1h, sends_24h, unique_contacts_1h, unique_contacts_24h, rate:{configured_per_min/hour/day, actual_per_hour=sends_1h, actual_per_day=sends_24h}}; else slice is None. (3) INSERT INTO sender_restriction_events (workspace_id, sender_id, category, event_type, source, restricted_until, raw_text, activity_slice, proxy) VALUES (..., CAST(:slice AS JSONB), CAST(:proxy AS JSONB)) — json.dumps the slice/proxy or pass NULL. The helper NEVER UPDATEs senders (restriction state changes belong to the call-sites). Body shape is in RESEARCH §Code Examples — do not re-derive; copy from read_first.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_restriction_audit.py::test_event_carries_activity_slice tests/test_restriction_audit.py::test_event_carries_proxy_snapshot tests/test_restriction_audit.py::test_slice_windows_sent_only tests/test_restriction_audit.py::test_recipient_privacy_separate_category tests/test_restriction_audit.py::test_events_append_only -x</automated>
  </verify>
  <acceptance_criteria>
    - `pytest tests/test_restriction_audit.py::test_event_carries_activity_slice -x` (test-overlay) exits 0 — slice has correct sends_1h + rate object.
    - `pytest tests/test_restriction_audit.py::test_event_carries_proxy_snapshot -x` exits 0 — proxy column matches senders.proxy.
    - `pytest tests/test_restriction_audit.py::test_slice_windows_sent_only -x` exits 0 — only message_type='sent' counted; 1h/24h windowing correct.
    - `pytest tests/test_restriction_audit.py::test_recipient_privacy_separate_category -x` exits 0 — recipient_privacy row inserted, senders.restriction_status unchanged, filterable by category.
    - `pytest tests/test_restriction_audit.py::test_events_append_only -x` exits 0 — two events coexist (no overwrite).
    - `grep -q "FROM messages_log" app/services/restriction_audit.py` and `grep -q "message_type = 'sent'" app/services/restriction_audit.py` both succeed.
  </acceptance_criteria>
  <done>record_restriction_event writes append-only events with a correct activity_slice + proxy snapshot in the caller's transaction (or its own); recipient_privacy rows never touch senders; the 5 slice/append-only/category tests GREEN.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Wire all 5 write-points (queue.py PEER_FLOOD/FROZEN/FLOOD_WAIT + PRIVACY_RESTRICTED + listener antispam + reconcile cleared/banned/extension-gated)</name>
  <behavior>
    - PEER_FLOOD: after the senders UPDATE in the db2 block, before db2.commit(), write spam_limited / queue_error / restricted_until=recheck_at / raw_text=error_msg / db=db2.
    - ACCOUNT_FROZEN: same db2 block, event_type=frozen.
    - HARD FloodWait (no restriction_status change): write flood_wait / queue_error / restricted_until=reschedule_at / category=restriction / db=db2 — senders.restriction_status NOT changed, pool_health unaffected.
    - PRIVACY_RESTRICTED (MANDATORY this phase): detected at the telegram.py error-dispatch (~L699/L860, UserNotMutualContactError → code 'PRIVACY_RESTRICTED'), surfaced in queue.py inside the existing OUTER `async with db:` send-loop session. Write privacy_restricted / queue_error / restricted_until=None / raw_text=error_msg / category='recipient_privacy' / db=db (THAT existing send-loop session, NOT a new one). Account is healthy — must NOT touch senders.restriction_status (D-03); writes an event row only. PRIVACY_PREMIUM_REQUIRED is explicitly OUT of scope (stays in the SEND_FAILED catch-all; no new RPCError branch this phase).
    - antispam: in the existing session block before session.commit(), write spam_limited / antispam_signal / restricted_until=recheck_at / raw_text=message_text / db=session.
    - reconcile free: cleared / spambot_reconcile / restricted_until=None / raw_text=result['raw_text'] / db=db.
    - reconcile suspended: banned / spambot_reconcile / restricted_until=(unchanged) / raw_text=result['raw_text'] / db=db.
    - reconcile else (still-limited): emit extension ONLY when next_at > old_until + 1 minute (D-01 gate), where old_until is read INSIDE the per-sender reconcile transaction (NOT from the outer batch SELECT — see action). No-shift recheck writes NO event. The unconditional UPDATE senders SET restricted_until=:next STAYS.
  </behavior>
  <read_first>
    - app/services/queue.py L704-715 (HARD FloodWait db2 block), L733-781 (PEER_FLOOD db2 block + error_msg L697), L783-824 (ACCOUNT_FROZEN db2 block), L837 (_fail_item catch-all; the outer `async with db:` send-loop session that the PRIVACY_RESTRICTED branch lives in)
    - app/services/listener.py L881-965 (_handle_antispam_signal session block L919-955; failover_cold_backlog(...,session) at L953 = transaction-neutral precedent), L1360-1456 (_restriction_reconcile_tick; outer batch SELECT L1376-1382 decides WHICH senders to recheck — a separate session; per-sender `async with AsyncSessionLocal() as db:` block at ~L1402 where the verdict UPDATE happens; free L1403, suspended L1420, else L1427; commits per branch)
    - .planning/phases/10-pool-visibility/10-PATTERNS.md §MOD queue.py + §MOD listener.py (exact insertion lines + D-01 extension gate; NOTE: reconcile branch line numbers there are CURRENT-as-of-2026-06-24 and authoritative over RESEARCH/CONTEXT)
    - .planning/phases/10-pool-visibility/10-RESEARCH.md §Write-Point Inventory (trigger→event_type/source/restricted_until/raw_text table) + Pitfall 1 (extension gate) + Pitfall 4 (FloodWait no state change)
    - app/services/telegram.py L691-724 (error codes incl. PRIVACY_RESTRICTED at L699), L336 (check_spambot raw_text)
    - tests/test_restriction_audit.py (the RED stubs this task turns GREEN — including test_recipient_privacy_separate_category, mandatory)
  </read_first>
  <action>
    Import `from app.services.restriction_audit import record_restriction_event` in queue.py and listener.py. Wiring is ADDITIVE to the restriction logic: the reconcile SELECT gains a `restricted_until` projection to support the D-01 gate; all existing queue/listener pause/freeze/reschedule control flow — the +24h queue pause, empirical rate-limit constants, FloodWait retry logic (CLAUDE.md guard) — is UNTOUCHED.

    queue.py: inside the PEER_FLOOD db2 block (L733-781), after the UPDATE senders SET restriction_status='spam_limited' and before await db2.commit(), add `await record_restriction_event(sender.id, "spam_limited", "queue_error", recheck_at, error_msg, db=db2)`. Same for the ACCOUNT_FROZEN db2 block (L783-824): event_type="frozen". For the HARD FloodWait db2 block (L704-715) — which only pauses the queue and does NOT touch restriction_status — add `await record_restriction_event(sender.id, "flood_wait", "queue_error", reschedule_at, error_msg, db=db2)` (informational; restriction_status untouched, pool_health unaffected per OQ#1).

    PRIVACY_RESTRICTED (MANDATORY — the Wave 1 contract requires it). It is detected at the telegram.py error-dispatch (~L699 / surfaced ~L860 in the send path) as code 'PRIVACY_RESTRICTED' and surfaces in queue.py inside the EXISTING OUTER `async with db:` send-loop session (the session already open in the send loop where _fail_item / the error-code branch runs). Add `await record_restriction_event(sender.id, "privacy_restricted", "queue_error", None, error_msg, category="recipient_privacy", db=db)` passing THAT existing `db` session (NOT a new AsyncSessionLocal). This path must NOT touch senders.restriction_status — the account is healthy, it only writes one event row with category='recipient_privacy' (D-03). Keep PRIVACY_PREMIUM_REQUIRED OUT of scope (no new RPCError branch; it stays in the SEND_FAILED catch-all).

    listener.py: in _handle_antispam_signal session block (L919-955), before await session.commit() (L955), add `await record_restriction_event(sender_id, "spam_limited", "antispam_signal", recheck_at, message_text, db=session)` (OQ#2 source — free-form, no CHECK). In _restriction_reconcile_tick: the OUTER batch SELECT at L1376-1382 (which runs in its OWN session and only decides WHICH senders to recheck) may carry `restricted_until` for efficiency, BUT the D-01 gate MUST NOT use that batch value — between the batch read and the per-sender UPDATE a concurrent reconcile tick can move restricted_until, so the batch value is stale. Instead, INSIDE the per-sender `async with AsyncSessionLocal() as db:` block (~L1402), atomically with the verdict UPDATE and BEFORE the verdict-branch UPDATE, read `SELECT restricted_until FROM senders WHERE id = :sid` (FOR UPDATE acceptable) into `old_until` on THAT same `db` transaction, and use THAT value as old_until in the gate. In the free branch (L1403, before commit) add `record_restriction_event(r[0], "cleared", "spambot_reconcile", None, result.get("raw_text"), db=db)`. In suspended (L1420) add event_type="banned" with the current restricted_until. In the else/extension branch (L1427), GATE on the intra-transaction old_until: `if next_at > old_until + timedelta(minutes=1): await record_restriction_event(r[0], "extension", "spambot_reconcile", next_at, result.get("raw_text"), db=db)` — then the unconditional UPDATE senders SET restricted_until=:next stays as-is. No fenced bodies — copy exact insertion shapes from 10-PATTERNS.md §MOD listener.py per read_first.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_restriction_audit.py::test_peer_flood_writes_event tests/test_restriction_audit.py::test_reconcile_cleared_writes_event tests/test_restriction_audit.py::test_reconcile_no_shift_no_event tests/test_restriction_audit.py::test_reconcile_shift_writes_extension tests/test_restriction_audit.py::test_recipient_privacy_separate_category -x</automated>
  </verify>
  <acceptance_criteria>
    - `pytest tests/test_restriction_audit.py::test_peer_flood_writes_event -x` (test-overlay) exits 0 — one spam_limited/queue_error event in the same TX as the senders UPDATE.
    - `pytest tests/test_restriction_audit.py::test_reconcile_cleared_writes_event -x` exits 0 — cleared/spambot_reconcile event, restricted_until NULL.
    - `pytest tests/test_restriction_audit.py::test_reconcile_no_shift_no_event -x` exits 0 — D-01 gate: no extension event on a no-shift recheck.
    - `pytest tests/test_restriction_audit.py::test_reconcile_shift_writes_extension -x` exits 0 — exactly one extension event on a real forward shift.
    - `pytest tests/test_restriction_audit.py::test_recipient_privacy_separate_category -x` exits 0 — PRIVACY_RESTRICTED writes a category='recipient_privacy' row, senders.restriction_status unchanged.
    - `grep -Eq "record_restriction_event\(.*db=db2" app/services/queue.py` succeeds (event written in the existing db2 TX, not a separate session).
    - D-01 gate atomicity (B-1): the reconcile `old_until` is read INSIDE the per-sender transaction, not only in the batch query. Assert via source check: a `SELECT restricted_until FROM senders WHERE id = :sid` (or equivalent `:sid`/`id` filter) appears inside the per-sender `async with AsyncSessionLocal()` reconcile block in app/services/listener.py — e.g. `grep -q "old_until" app/services/listener.py` AND the SELECT of restricted_until by sender id is located within the reconcile `async with AsyncSessionLocal()` block (not solely in the outer batch SELECT at L1376-1382). Confirm by inspecting the diff: the per-sender block contains both the `old_until` read and the gated record_restriction_event call.
    - No change to empirical rate-limit constants / +24h pause: `git diff app/services/queue.py` shows additions only around the existing db2 blocks and the PRIVACY_RESTRICTED branch (no edits to the rate-limit constants or the pause_until arithmetic).
  </acceptance_criteria>
  <done>All 5 account-level write-points + the mandatory recipient_privacy write-point emit events in-transaction; the D-01 extension gate reads old_until intra-transaction and suppresses no-shift ticks; recipient_privacy stays separate and never flips restriction_status; the 5 write-point tests + full suite GREEN.</done>
</task>

</tasks>

<threat_model>
ASVS L1 (block_on=high). Focus areas for this plan:
- **Append-only / no UPDATE-DELETE path:** the helper only INSERTs into sender_restriction_events. No code path UPDATEs or DELETEs event rows (CASCADE on FK is the only deletion, tied to sender/workspace teardown). `test_events_append_only` enforces this.
- **No secret/session leakage into raw_text:** raw_text stores `error.get("message")` (Telegram send-error text) or `result['raw_text']` (@SpamBot reply) or the antispam `message_text` — none contain API_KEY, session strings, or proxy credentials. Do NOT log the proxy URL into raw_text; the proxy snapshot lives in its own structured `proxy` JSONB column (which is workspace-internal and never returned cross-tenant). Reviewer must confirm raw_text is only the human-facing error/bot text.
- **Workspace denormalization integrity:** workspace_id on the event is read from the sender row (same workspace as the sender) — never client-supplied — so the Wave 3 read endpoint's workspace filter cannot be bypassed.
- **Transactional atomicity:** event-write shares the session/transaction with the restriction_status UPDATE so audit and state cannot diverge on crash (Pitfall 2). The D-01 extension gate's `old_until` is read in the SAME per-sender transaction as the verdict UPDATE so a concurrent reconcile tick cannot make the gate compare against a stale value (B-1).
- **Prod safety:** all tests via test-overlay only; NEVER `down -v`; rebuild api/listener after code change (restart does not pick up code).
</threat_model>

<verification>
- Migration 030 applied idempotently under test-overlay; table + indexes + CHECK present.
- restriction_audit.py exports record_restriction_event (dual-mode) with correct slice + proxy snapshot.
- All 5 account-level write-points + the mandatory recipient_privacy write-point wired in the existing transactions (queue.py db2 + outer send-loop session; listener session/db); D-01 extension gate reads old_until intra-transaction.
- `pytest tests/test_restriction_audit.py -x` (test-overlay) — all 9 non-endpoint tests GREEN (including test_recipient_privacy_separate_category; history endpoint test remains RED until Wave 3).
- Full suite green at wave merge (test-overlay).
</verification>

<success_criteria>
- HLTH-01: durable append-only events at every restriction state-change + the D-01 forward-shift gate (old_until read intra-transaction).
- HLTH-02: activity_slice (sends 1h/24h, unique contacts, configured-vs-actual rate) + proxy snapshot, computed at write time, same TX.
- D-03: recipient_privacy (PRIVACY_RESTRICTED, mandatory) logged in a separate category on the live send-loop session, never flipping restriction_status.
- Empirical rate-limit / FloodWait logic untouched (CLAUDE.md guard); wiring additive to the restriction logic.
</success_criteria>

<output>
After completion, create `.planning/phases/10-pool-visibility/10-02-SUMMARY.md`
</output>
