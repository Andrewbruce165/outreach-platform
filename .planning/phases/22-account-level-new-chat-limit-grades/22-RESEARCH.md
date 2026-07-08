# Phase 22: Account-level new-chat limit grades - Research

**Researched:** 2026-07-08
**Domain:** Brownfield refactor of send-throttling — moving the daily new-dialog cap from campaign to account (sender), adding an auto-progressing per-workspace grade ladder, sharing the budget across outreach + warmup, and removing the daily-message cap.
**Confidence:** HIGH (all findings VERIFIED against current source; no external/library research required — this is an internal-code phase with locked decisions)

## Summary

Every plan-relevant integration point cited in CONTEXT.md was read against the **current** code. The line references in CONTEXT.md are close but have drifted by a few lines in `queue.py`; the exact current locations are given below. All decisions D-01..D-17 are locked — this research supplies the precise, current code shape and the pitfalls the planner needs to write concrete tasks.

Three findings materially affect the plan and are called out as pitfalls:
1. **`tests/conftest.py` does NOT glob the migrations directory** — it builds the test schema from ORM `create_all` plus a *hardcoded, exists-guarded list* of SQL-only migrations. New tables/columns land automatically via ORM; SQL-only operations (backfill, CHECK constraints, `DROP COLUMN`) need a manually-added exists-guarded block.
2. **The current outreach "new dialog" predicate keys on `campaign_id`, not `sender_id`** — D-13 (sender-wide dedup) is a real semantic rewrite of three SQL subqueries in `_process_next_for_sender`, not a one-token change.
3. **Two different day-windows coexist** — the queue new-dialog cap counts a *trailing 24h* (`INTERVAL '24 hours'`), while warmup's `_count_sent_today` counts a *calendar day* (`>= CURRENT_DATE`). The shared account budget (D-03/D-09) must pick ONE window and apply it in both places or outreach and warmup will disagree on "spent today."

**Primary recommendation:** Store the grade as `senders.current_level` + `senders.level_updated_at` (D-14) with mandatory ORM `server_default` mirroring the DB default (project memory: ORM-default-vs-server-default-drift). Store the ladder in a **new** `sender_grade_settings` table (workspace PK, code-defaults on absent row) modeled byte-for-byte on the `warmup_settings` GET/PUT pattern — do NOT overload `warmup_settings` (the ladder governs all senders including non-warmup ones). Compute the effective budget in Python and pass it as a bind into the existing pacing/cap subqueries (the proven Phase 13 `expected_now` pattern), keeping `LIMIT 8` / `FOR UPDATE OF mq SKIP LOCKED` intact.

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Daily new-chat limit lives on the **account (sender)**, not campaign. Replaces `campaigns.max_new_dialogs_per_day` as the primary new-dialog gate.
- **D-02 → D-14/D-15:** Grade auto-progresses; user can also manually override from the UI.
- **D-03:** Limit is **global** — shared by outreach send AND warmup pairing (both spend the same daily account budget).
- **D-04:** Remove the daily-message cap (`senders.rate_per_day` / default 150) entirely — from the backend gate (`queue.py::_check_rate_limits`) and from the UI. Do NOT touch `rate_per_min` (4/min), `rate_per_hour` (20/hr), or `MAX_NEW_CONTACTS_PER_HOUR` (15/hr).
- **D-05:** Single active campaign → Phase-13 pacing keeps THAT campaign's working window; only the numerator changes (account grade budget instead of `c.max_new_dialogs_per_day`).
- **D-06:** 2+ active campaigns → shared account budget, FIFO by existing `ORDER BY priority DESC, created_at ASC`. No new campaign-priority logic. The spend counter moves from `(sender_id, campaign_id)` to `sender_id`.
- **D-07:** `campaigns.max_new_dialogs_per_day` column is **dropped entirely** (migration + API/schemas/router/UI/SQL cleanup).
- **D-08:** Warmup spends budget only for a **NEW pair** (senders that never warmed together). Registry `sender_first_contacts`; first session between two senders charges the initiator, repeat sessions of the same pair are free. Idempotent backfill from existing `warmup_sessions`/`warmup_messages`.
- **D-09:** Outreach has **priority** on the shared budget: warmup takes only the remainder after reserving pending cold openers (reserve = budget − spent_in_window − pending).
- **D-10:** Bulk-imported (Phase 21) accounts start at grade 1 (`level_updated_at` = row creation). Manual UI override (D-15) is the escape hatch.
- **D-13:** "New dialog" is defined **sender-wide** (any campaign), not per-campaign. If the sender already sent `status='sent'` to this `recipient_phone` in ANY of its campaigns, it is NOT a new chat.
- **D-14:** Schema `senders.current_level` (int 1..3, default 1) + `senders.level_updated_at` (timestamptz, default = `created_at` on backfill). Auto-progress when `NOW() - level_updated_at >= step_days(current_level)` → `current_level += 1`, `level_updated_at = NOW()`. No progression at level 3.
- **D-15:** Manual override writes `current_level = <chosen>`, `level_updated_at = NOW()` — same operation as auto-progress, resets the timer. No separate "frozen" flag.
- **D-16:** Ladder configured at **workspace** level (like `warmup_settings`), **fixed 3 levels**, each editable (chats/day + step days). Default 5/30d, 9/30d, 13 (cap). New table/row + GET/PUT endpoints + openapi regen.
- **D-17:** Top (3rd) level is permanent — no further transition.

### UI Contract
- **D-11 (UI):** Accounts page gets: (1) workspace grade-ladder editor (3 rows: chats/day + days-to-next, with green-corridor warnings like existing rate-limit warnings); (2) per-account grade/level display + (ideally) progress-to-next + remaining daily budget; (3) manual grade override control.
- **D-12:** Regenerate `lovable-handoff/openapi.json` + types after API changes (GET/PUT ladder settings, PATCH sender grade, extended `SenderResponse` with `current_level`/`level_updated_at`/remaining budget).

### Claude's Discretion
- Auto-progression mechanism (background tick vs lazy recompute) — only correctness at gate time matters.
- `sender_first_contacts` schema (pair structure, indexes, idempotent backfill).
- Which account is charged as "initiator" of a new warmup pair (per [SUPERSEDED] D-12: the older/more-warmed account writes first).
- Exact SQL shape of the sender-wide dedup (D-13) and account-level cap/pace predicates — preserve `LIMIT 8` / `FOR UPDATE OF mq SKIP LOCKED`.
- Green-corridor warning wording for the ladder editor.
- Where to store the ladder (new `sender_grade_settings` table vs columns on `warmup_settings`).

### Deferred Ideas (OUT OF SCOPE)
- Arbitrary number of ladder levels (fixed at 3 this phase).
- Grade analytics/dashboard.
- Adding real account age at import time.

## Project Constraints (from CLAUDE.md)
- **Async everywhere** — all DB via async/await + `AsyncSession`.
- **Migrations:** raw SQL only, `NNN_short_name.sql`, **idempotent** (`IF NOT EXISTS`, `DO $$ … EXCEPTION duplicate_object $$`, `ON CONFLICT DO NOTHING`), auto-applied at api startup via `app/database.py::_apply_migrations` under `pg_advisory_lock`. Never Alembic. Fail-fast: a failing migration stops api boot.
- **Never touch** `rate_per_min`/`rate_per_hour` intervals, the 20–55s per-send base gate, fatigue factor, long pauses, or FloodWait/retry logic without explicit discussion. (D-04 removes ONLY `rate_per_day`; the base 20–55s interval floor in `_check_rate_limits` stays the structural floor for pacing.)
- **Tests ONLY via test-overlay:** `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. Never bare `docker compose run --rm api pytest` (conftest guard DROP SCHEMA hits prod). **Never `down -v`** (wipes prod `postgres_data`).
- **Discuss before coding** (non-trivial changes): explain intent in Russian, await confirmation. (Applies to the executor, noted for the planner.)
- **ORM `server_default` must duplicate DB default** for every new NOT NULL column (project memory `project-orm-default-vs-server-default-drift`): `create_all` builds the test/fresh-DB schema from the ORM, not the migration.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| New-dialog budget enforcement (outreach) | API / Backend (`app/services/queue.py`) | Database (message_queue counts) | Single enforcement point already lives in the queue worker's pick SELECT |
| New-pair budget enforcement (warmup) | API / Backend (`app/services/warmup.py`) | Database (`sender_first_contacts`) | Warmup worker owns session creation; registry gates it |
| Grade storage + auto-progression | Database (`senders.current_level`/`level_updated_at`) | API / Backend (periodic sweep or lazy recompute) | State is durable; progression is a timestamp comparison |
| Grade ladder config (per workspace) | Database (`sender_grade_settings`) | API / Backend (GET/PUT, code-defaults) | Mirror of the `warmup_settings` workspace-scoped pattern |
| Manual grade override | API / Backend (PATCH `/senders/{slug}`) | Frontend (account card control) | Workspace-scoped mutation via existing AuthDep |
| Ladder editor + grade display | Frontend (sibling `aimly-tg-outreach`) | API (openapi contract) | UI-only; cross-repo human UAT |

## Standard Stack

No new packages. This phase is pure application code + raw-SQL migrations + tests on the existing stack (Python 3.11, FastAPI, SQLAlchemy 2.0 async, asyncpg, PostgreSQL 16, Telethon). **No Package Legitimacy Audit required** — nothing is installed.

## Current Code — Verified Integration Points

> All line numbers verified against working-tree source on 2026-07-08. Where CONTEXT.md drifted, the corrected location is noted.

### 1. `app/services/queue.py` — the single outreach enforcement point

**`_process_next_for_sender`** spans **lines 359–564** `[VERIFIED: app/services/queue.py]`.

**Phase 13 pacing pre-query (lines 385–440):**
- SELECT at 402–420 pulls the next eligible item's campaign: `c.timezone`, `c.work_hour_start`, `c.work_hour_end`, and **`c.max_new_dialogs_per_day AS c_cap`** (line **407**).
- `expected_now` computed at line **438–440**: `camp_row.c_cap * frac * random.uniform(PACE_JITTER_LOW, PACE_JITTER_HIGH)`.
- **D-05/D-07 change:** replace `c.max_new_dialogs_per_day` (line 407) with the sender's **account grade budget**. Because the budget is per-sender (not per-campaign), the pre-query must resolve the budget from `senders.current_level` + the workspace ladder. Recommended shape: compute the budget in Python (read `current_level` + ladder in one small query, mirroring how `expected_now` is already a Python-computed bind) and keep the campaign window from this same next-item query. The `CAST(:expected_now AS DOUBLE PRECISION)` fractional-gate note at lines 434–437 MUST be preserved (an untyped bind truncates to bigint and silently blocks all new dialogs).

**Main pick SELECT (lines 442–529):**
- The candidate SELECT JOINs `campaigns c` and `senders s` (lines 483–484) and locks only `mq` (`FOR UPDATE OF mq SKIP LOCKED`, line 522), `LIMIT 8` (line 521).
- **EXISTS / follow-up branch (lines 494–499)** — the current predicate is:
  ```sql
  EXISTS (SELECT 1 FROM message_queue prior
          WHERE prior.campaign_id = mq.campaign_id
            AND prior.recipient_phone = mq.recipient_phone
            AND prior.status = 'sent')
  ```
  **DRIFT / D-13 rewrite:** this keys on `campaign_id`, NOT `sender_id`. Sender-wide dedup means dropping `prior.campaign_id = mq.campaign_id` and adding `prior.sender_id = mq.sender_id`. This is a genuine semantic change (a phone contacted by this sender in campaign A now counts as "known" in campaign B). `[VERIFIED: app/services/queue.py:494-499]`
- **New-dialog cap subquery (lines 506–511):**
  ```sql
  (SELECT COUNT(DISTINCT opened.recipient_phone) FROM message_queue opened
    WHERE opened.sender_id = mq.sender_id
      AND opened.campaign_id = mq.campaign_id       -- ← D-06/D-13: DROP this line
      AND opened.status = 'sent'
      AND opened.finished_at >= NOW() - INTERVAL '24 hours') < c.max_new_dialogs_per_day  -- ← D-01/D-07: replace RHS with :account_budget bind
  ```
- **Pace subquery (lines 513–518):** same `opened.campaign_id = mq.campaign_id` filter to drop (D-06), counts from `:window_start_utc`, compared against `CAST(:expected_now AS DOUBLE PRECISION)`.
- **PeerFlood gate (quick-260708-icz):** `s.restriction_status <> 'spam_limited'` at line **505** guards the whole new-dialog branch — keep it; a spam_limited sender opens no new dialogs but the EXISTS/follow-up branch still services known peers. `[VERIFIED: app/services/queue.py:505]`

**Net for the planner:** all three subqueries (EXISTS at 494–499, cap COUNT at 506–511, pace COUNT at 513–518) must be rewritten to be sender-wide, and both `c.max_new_dialogs_per_day` references (line 407 pre-query, line 511 main SELECT) must become the account budget bind BEFORE the column can be dropped (D-07). Because migrations auto-apply at the same startup as the new code deploys, the code rewrite + `DROP COLUMN` migration must ship together.

### 2. `app/services/queue.py::_check_rate_limits` — remove the daily-message cap (D-04)

Spans **lines 566–699+** `[VERIFIED: app/services/queue.py]`. `rate_per_day` appears in exactly three spots:
- **Line 581:** selected in the sender-row query (`SELECT rate_per_min, rate_per_hour, rate_per_day, …`).
- **Line 618:** `max_per_day = sender_row.rate_per_day`.
- **Lines 671–687:** the entire "Messages sent in last 24 hours (daily cap)" block — SELECT COUNT (671–680) + the `if msgs_today >= max_per_day` gate (682–687).

**Remove all three.** The `rate_per_min` (616/620–633), `rate_per_hour` (617/635–651), and `MAX_NEW_CONTACTS_PER_HOUR` (line 664) checks are structurally independent and MUST NOT be touched. `frozen`-skip (609–614) and `spam_limited` handling stay. CONTEXT.md's "строка ~682" and "~618" are both correct.

### 3. `app/services/warmup.py` — new-pair detection + budget (D-08/D-09)

- **`_get_active_pool` (lines 167–227)** `[VERIFIED]` returns pool rows with `sender_id`, `workspace_id`, `enrolled_at`, and computed `enrolled_days` (line 224 — `(now - enrolled_at).days`). **Confusion warning (CONTEXT.md explicit):** `warmup_pool.enrolled_at` / `enrolled_days` is the *warmup-pool tenure*, distinct from the new `senders.level_updated_at` grade timer. They are unrelated axes.
- **`_create_new_sessions` (lines 541–629)** `[VERIFIED]` is full-mesh: builds `combinations(ws_group, 2)` per workspace (line 587), skips pairs with an active session (`active_pairs`, lines 566–572), and INSERTs a new `warmup_sessions` row for every remaining pair (607–622). **D-08 hook:** consult `sender_first_contacts` to classify each pair as known (free) vs new (charge initiator + check remaining budget after outreach reserve), and INSERT into the registry after creating a new-pair session.
- **Initiator determination:** `_process_session` (lines 305–313) decides who writes: on a NEW session `last_sender_id IS NULL` → the `else` branch fires → **`sender_a` writes first** (lines 312–313). The pair order in `combinations(ws_group, 2)` comes from `pool_sorted` (sorted by `workspace_id`, line 580), so `sender_a` is effectively arbitrary today. [SUPERSEDED] D-12 wants the **older/more-warmed** account to initiate — so the planner must (a) order the pair so the initiator = the account whose budget is charged and who sends first, and (b) charge that account's remaining budget. `[VERIFIED: app/services/warmup.py:305-313, 580-591]`
- **`LEVEL_CONFIG` (lines 36–42)** and **`_count_sent_today` (lines 229–239)** drive the warmup *message-per-day* ladder (5→120 msgs/day by pool day). This is the **[SUPERSEDED] D-09 scope only** — the ACTIVE D-04 removes `senders.rate_per_day`, NOT the warmup message ladder. **Leave `LEVEL_CONFIG` and the message-per-day logic alone** unless a decision says otherwise.
- **Window mismatch (PITFALL):** `_count_sent_today` counts `sent_at >= CURRENT_DATE` (calendar day, line 235), whereas the queue new-dialog cap counts `finished_at >= NOW() - INTERVAL '24 hours'` (trailing 24h). The **shared** account new-chat budget (D-03/D-09) must standardize on one window across both workers. Recommend trailing-24h (matches the outreach cap and Phase 12/13 semantics, avoids the midnight burst the queue was designed to prevent).

### 4. ORM models — `app/models/__init__.py`

- **`Sender` (lines 74–152)** `[VERIFIED]`:
  - `created_at = Column(DateTime(timezone=True), server_default=func.now())` — line **124**.
  - `rate_per_day = Column(Integer, nullable=False, server_default='150')` — line **123** (remove for D-04).
  - `rate_per_min` (121) / `rate_per_hour` (122) stay.
  - **Add (D-14):** `current_level = Column(Integer, nullable=False, server_default='1')` and `level_updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)`. Both MUST carry `server_default` (drift memory) so `create_all` and raw INSERTs (`_insert_sender_raw`, bulk import) don't break.
- **`Campaign.max_new_dialogs_per_day` (line 776)** `[VERIFIED]` — `Column(Integer, nullable=False, server_default="10")`. Remove for D-07. Note the surrounding comment cites the quick-260706-mdz default-10 history and migration 050.
- **Warmup models (lines 367–453)** `[VERIFIED]`:
  - `WarmupSession` (383–407): `sender_a_id`, `sender_b_id`, `last_sender_id`, `status`, `messages_sent`, `next_message_at`, `created_at`.
  - `WarmupMessage` (410–427): `session_id`, `from_sender_id`, `to_sender_id`, `sent_at`. **These are the backfill source for `sender_first_contacts`** — a warmed pair = distinct `{from_sender_id, to_sender_id}` unordered pairs in `warmup_messages` (or `{sender_a_id, sender_b_id}` in `warmup_sessions`).
  - `WarmupSettings` (430–453): the template for the new ladder settings table.
- **`Conversation` (lines 332–362)** `[VERIFIED]`: `sender_id`, `contact_phone`, `created_at`, `status`. Relevant only if the registry were phone-based; per the ACTIVE design the registry is **sender↔sender pair-based** (warmup), so the primary backfill source is `warmup_messages`/`warmup_sessions`, not `conversations`. (The [SUPERSEDED] `(sender_id, peer_phone)` shape is obsolete — outreach dedup is handled by the message_queue predicate in D-13, not the registry.)

### 5. Schemas — `app/schemas/__init__.py`

- **`RateLimits` (lines 75–79)** `[VERIFIED]`: has `per_day: int = 150` (line 79) → remove for D-04.
- **`SenderCreate` (90–104)** / **`SenderUpdate` (107–121)**: `rate_per_day: Optional[int] = Field(None, ge=1, le=300)` at lines 104 / 115 → remove.
- **`SenderResponse` (124–180)**: `rate_limits: RateLimits` (line 152). Extend with grade fields (D-12): `current_level`, `level_updated_at`, and remaining budget. `model_config = ConfigDict(from_attributes=True)` (126) — new fields must exist as attributes on the ORM/response or default (see the `_sender_to_response` builder note below).
- **Campaign schemas:** `CampaignCreate.max_new_dialogs_per_day` (**799–803**), `CampaignUpdate.max_new_dialogs_per_day` (**876**), `CampaignResponse.max_new_dialogs_per_day` (**940**) → remove all three for D-07. `[VERIFIED]`

### 6. Routers

- **`app/routers/senders.py`** `[VERIFIED]`:
  - `RATE_HARD_CAP` (line **71**) / `RATE_SOFT_CAP` (line **73**) — remove the `"rate_per_day"` key from each.
  - `_validate_rate_limits(rate_per_min, rate_per_hour, rate_per_day)` (**179–220**) — drop the `rate_per_day` param + its dict entry (lines 182, 193).
  - `_sender_to_response` (**125–176**) builds `RateLimits(per_minute=…, per_hour=…, per_day=sender.rate_per_day)` at line **161** — drop `per_day`; add grade fields to the `SenderResponse` construction.
  - Callers of `_validate_rate_limits`: lines **540–541** (create) and **628–629** (PATCH). Assignments `sender.rate_per_day = request.rate_per_day` at **560–561** and **647–648** → remove.
  - **PATCH endpoint** `@router.patch("/senders/{slug}")` at line **618** is the natural home for the D-15 manual override (extend `SenderUpdate` with an optional `current_level`, or add a dedicated sub-route). Workspace ownership is enforced by the existing AuthDep + `_assert_workspace_owns_sender` pattern.
- **`app/routers/campaigns.py`** `[VERIFIED]`: `_validate_max_new_dialogs` (**70–**), constants `DIALOG_LIMIT_SOFT_CAP=10` (62) / `DIALOG_LIMIT_HARD_CAP=30` (63), and references at lines **85, 97, 375, 464, 520, 654–655** → remove all for D-07.
- **`app/routers/warmup.py`** `[VERIFIED]`: the **exact template** for the new ladder settings endpoints. `WarmupSettingsUpdate` (493–503), `_resolve_settings` (506–523, code-defaults on absent row), `GET /settings` (526–546, returns resolved defaults when no row), `PUT /settings` (549–594, idempotent `INSERT … ON CONFLICT (workspace_id) DO UPDATE`). Clone this exactly for `GET/PUT /sender-grade-settings` (or similar), returning the 3-level ladder with code-defaults 5/30, 9/30, 13.

### 7. Migrations

Highest existing migration file: **055** (`055_messages_media_columns.sql`) `[VERIFIED: ls migrations/]`. **Next free slot is 056+.** CONTEXT.md said "after 054/055" — confirmed. Likely files (planner may combine):
- `056_*`: `ALTER TABLE senders ADD COLUMN current_level INT NOT NULL DEFAULT 1`, `ADD COLUMN level_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, then backfill `level_updated_at = created_at` for existing rows (`UPDATE … WHERE level_updated_at IS DISTINCT FROM created_at` or unconditional on the fresh columns). Optional CHECK `current_level BETWEEN 1 AND 3`. Drop `senders.rate_per_day` (D-04).
- `057_*`: `CREATE TABLE sender_first_contacts` + idempotent backfill from `warmup_messages`/`warmup_sessions` (`ON CONFLICT DO NOTHING`).
- `058_*`: `CREATE TABLE sender_grade_settings` (workspace PK, 3 level limits + 3 step-days, or a fixed-column layout — fixed 3 levels per D-16).
- `DROP COLUMN campaigns.max_new_dialogs_per_day` (D-07) — can live in 056 or its own file. Idempotent: `ALTER TABLE campaigns DROP COLUMN IF EXISTS max_new_dialogs_per_day`.

All must be idempotent and auto-apply cleanly (fail-fast on boot).

## Architecture Patterns

### System Data Flow

```
                       ┌─────────────────────────────────────────────┐
   workspace ladder ──►│  sender_grade_settings (per-workspace, 3 lvl)│
   editor (UI)         │  GET/PUT — code-defaults on absent row       │
                       └───────────────┬─────────────────────────────┘
                                       │ step_days[level], budget[level]
                                       ▼
   grade timer      ┌──────────────────────────────────────┐
   (auto-progress + │ senders.current_level                 │
    manual PATCH) ─►│ senders.level_updated_at              │──► effective budget = budget[current_level]
                    └──────────────────┬───────────────────┘
                                       │ (Python bind, per tick)
              ┌────────────────────────┴───────────────────────────┐
              ▼                                                      ▼
   ┌──────────────────────────┐                        ┌───────────────────────────┐
   │ QueueWorker._tick        │  spent = sender-wide    │ WarmupWorker._tick         │
   │ _process_next_for_sender │  DISTINCT recipient     │ _create_new_sessions       │
   │  • sender-wide dedup     │  count (trailing 24h)   │  • sender_first_contacts   │
   │  • account-budget cap    │◄────── shared ─────────►│    known(free)/new(charge) │
   │  • Phase-13 pace         │  budget/window          │  • reserve = budget −       │
   │  PRIORITY (D-09)         │                         │    spent − pending (D-09)  │
   └──────────┬───────────────┘                        └────────────┬──────────────┘
              ▼                                                      ▼
        message_queue (status='sent')                       warmup_sessions / _messages
              │                                                      │
              └───────────► both count against ◄─────────────────────┘
                            the SAME sender-wide daily budget
```

### Pattern 1: Workspace-scoped config with code-defaults (D-16)
**What:** `warmup_settings` pattern — one row per workspace, absent row resolves to hard-coded code defaults, idempotent upsert on PUT.
**Source:** `app/routers/warmup.py:506-594` + `migrations/038_warmup_settings.sql`.
**Apply to:** `sender_grade_settings` with fixed 3-level ladder, defaults 5/30d, 9/30d, 13.

### Pattern 2: Python-computed bind into the pick SELECT (D-05)
**What:** compute a fractional/absolute value in tested Python, pass as a `CAST(:x AS DOUBLE PRECISION)` bind — never f-string interpolate.
**Source:** Phase 13 `expected_now` at `queue.py:398-440` + the cast note at 434-437.
**Apply to:** the account grade budget as the cap RHS and the pace numerator.

### Pattern 3: Periodic worker tick reading DB state (auto-progression, Claude's discretion)
**What:** background asyncio task on an interval reading/writing durable DB flags (no in-memory state, survives restart).
**Source:** `WarmupWorker` (`warmup.py:84-133`, TICK_INTERVAL=30s, `asyncio.create_task(self._run(), name="warmup_worker")` at line 108); `QueueWorker` 3s poll (`queue.py:232`).
**Recommendation:** a lightweight periodic sweep `UPDATE senders SET current_level = current_level + 1, level_updated_at = NOW() WHERE current_level < 3 AND NOW() - level_updated_at >= (step for current_level)`. Because a single sweep advances at most one level, either accept eventual catch-up (a stalled account catches up one level per tick) or loop until no rows update. Alternatively compute the effective level lazily at gate time — but that duplicates ladder math in SQL and complicates multi-level catch-up + timer reset; the sweep is simpler and matches the established worker pattern.

### Anti-Patterns to Avoid
- **Interpolating budget/window into SQL** — always binds (Phase 13 rule).
- **Overloading `warmup_settings` with the ladder** — the ladder governs ALL senders (including those not in the warmup pool); a warmup-named table is misleading and couples two features. Use a dedicated table.
- **Touching `LEVEL_CONFIG` / warmup message-per-day ladder** — that is [SUPERSEDED] scope, not D-04.
- **Dropping `campaigns.max_new_dialogs_per_day` before the SQL that reads it is rewritten** — boot-time auto-apply means a lingering reference crashes the worker.
- **Two different day-windows** for the shared budget — pick trailing-24h everywhere.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Workspace-scoped ladder config + defaults | Bespoke settings loader | `warmup_settings` GET/PUT + `_resolve_settings` pattern | Proven, idempotent upsert, code-defaults on absent row |
| Fractional budget gate in SQL | New pacing math | Phase 13 `expected_now` + `_window_elapsed_fraction` | Already tested (tz/DST/midnight); D-05 only swaps the numerator |
| Soft/hard-cap validation + green corridor | New warning types | `RATE_SOFT_CAP`/`RATE_HARD_CAP`/`WarningItem`/`_validate_rate_limits` (or campaigns' `_validate_max_new_dialogs`) | Established 200-with-warnings vs 422 pattern |
| Background progression loop | New scheduler | `WarmupWorker`/`QueueWorker` asyncio-task lifecycle | Same start/stop, durable DB-flag reads |
| Idempotent backfill | Ad-hoc script | Migration with `ON CONFLICT DO NOTHING` | Auto-applied, re-runs safely on drift |

## Runtime State Inventory

> This phase is a rename/refactor of a throttling mechanism + a `DROP COLUMN`; a runtime-state pass is warranted.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `campaigns.max_new_dialogs_per_day` (per-campaign cap, live values across all campaigns) will be dropped (D-07); `senders.rate_per_day` dropped (D-04). No consumer outside code reads these directly. | DROP COLUMN migrations (idempotent). No data export needed (values are config, not history). Existing new-dialog *counts* live in `message_queue.status='sent'` and are unaffected. |
| Live service config | Grade ladder is NEW config (no existing rows). No n8n/external service references `rate_per_day` or `max_new_dialogs_per_day`. | None — new `sender_grade_settings` starts empty, resolves to code-defaults. |
| OS-registered state | None — no cron/systemd/Task Scheduler references these fields. | None. |
| Secrets/env vars | None — no env var names change. `OPENAI_MODEL` etc. untouched. | None. |
| Build artifacts | `lovable-handoff/openapi.json` + generated frontend types embed `max_new_dialogs_per_day` and `rate_per_day` (per_day). Frontend forms (campaign create, account rate-limit editor) reference removed fields. | Regenerate openapi.json (D-12); update sibling `aimly-tg-outreach` forms (D-11) — cross-repo, human UAT. Existing warmup message-ladder UI is untouched. |

**Verified nothing found:** No external datastore keys on `rate_per_day`/`max_new_dialogs_per_day`; grep of `app/` shows the references enumerated in §Integration Points are the complete set for these two fields.

## Common Pitfalls

### Pitfall 1: conftest applies migrations from a HARDCODED list, not a glob
**What goes wrong:** A new SQL-only operation (backfill, CHECK constraint, DROP COLUMN, index) works in prod (auto-applier globs) but the test schema never gets it, so integration tests pass/fail on a schema that doesn't match prod.
**Why:** `tests/conftest.py::_build_outreach_schema` builds via ORM `create_all` then applies an explicit, exists-guarded list of migration files (see the 045/046/053/054/055 blocks at lines 230–268). It does NOT iterate the directory. The file's own comments call this out ("the hardcoded list does NOT glob").
**How to avoid:**
- New **tables/columns added to the ORM** (`sender_first_contacts` model, `senders.current_level/level_updated_at`, `sender_grade_settings` model) are built by `create_all` automatically — no conftest change needed for their existence.
- New **SQL-only operations** — the `sender_first_contacts` backfill, any CHECK constraint, and the `DROP COLUMN` — need an exists-guarded block appended to conftest (copy the `_mig_054`/`_mig_055` pattern) IF a test depends on that behavior. The `DROP COLUMN` is usually invisible to tests (create_all just won't build a column the ORM no longer declares), so tests inherit the dropped state for free.
**Warning signs:** `column X does not exist` or a test asserting backfilled data finds none.

### Pitfall 2: ORM default vs server_default drift on the new NOT NULL columns
**What goes wrong:** `current_level`/`level_updated_at` declared NOT NULL without `server_default` → `create_all` builds a NOT NULL column with no DB default → raw-SQL INSERTs that omit them (bulk import `_insert_sender_raw`, onboarding) raise `NotNullViolation`; fresh-DB/post-incident rebuild breaks.
**Why:** documented repeatedly in project memory (`project-orm-default-vs-server-default-drift`); `create_all` ignores Python-side `default=`, only honors `server_default`.
**How to avoid:** every new NOT NULL column carries `server_default` in the ORM AND `DEFAULT` in the migration AND (for existing rows) an explicit backfill. `level_updated_at` default = `NOW()` for new rows, but backfill existing rows to `created_at` (D-14).
**Warning signs:** bulk-import or onboarding 500s after deploy; test-overlay INSERT failures.

### Pitfall 3: D-13 sender-wide dedup is a real semantic change, not a token swap
**What goes wrong:** naively "removing campaign_id" from the EXISTS subquery (lines 494–499) without adding `sender_id` makes the dedup match ANY sender's prior send to that phone — cross-sender leakage. Or leaving `campaign_id` in the cap COUNT (506–511) keeps the budget per-campaign despite the account-level intent.
**Why:** the current EXISTS keys ONLY on `campaign_id` + `recipient_phone` (no `sender_id` at all today, because per-campaign implied the scope). Sender-wide means `sender_id` + `recipient_phone`, campaign-agnostic.
**How to avoid:** rewrite all three subqueries to `WHERE prior.sender_id = mq.sender_id AND prior.recipient_phone = mq.recipient_phone AND prior.status='sent'` (drop every `campaign_id` filter). Verify with a test: same phone across two campaigns of one sender counts once.
**Warning signs:** budget consumed twice for the same phone across campaigns, or new dialogs blocked because another sender contacted the phone.

### Pitfall 4: Shared budget window mismatch (trailing-24h vs calendar-day)
**What goes wrong:** queue counts trailing-24h, warmup counts calendar-day; the "spent today" the outreach reserve computes (D-09) disagrees with what warmup thinks it spent → over/under-spend, midnight bursts.
**How to avoid:** define the shared budget window once (recommend trailing-24h, `NOW() - INTERVAL '24 hours'`, matching the outreach cap) and use it in both the queue reserve and the warmup new-pair check.
**Warning signs:** warmup opening new pairs that push a sender over budget right after midnight.

### Pitfall 5: Benign double-open race under READ COMMITTED
**What goes wrong:** two parallel `_process_next_for_sender` calls both see `count < budget` and both open a new dialog → ~1 extra new dialog per tick.
**Why:** documented in the existing Phase 12/13 comment (lines 461–465) — `FOR UPDATE OF mq SKIP LOCKED` locks only `mq`, not the counted rows.
**How to avoid:** accept it (self-correcting next tick, same posture as today) — do NOT add heavier locking that would serialize senders. Just don't make it worse when moving to sender-wide.

### Pitfall 6: DROP COLUMN ordering vs auto-apply at boot
**What goes wrong:** the `DROP COLUMN campaigns.max_new_dialogs_per_day` migration auto-applies on api boot; if any deployed code still references `c.max_new_dialogs_per_day`, the queue worker crashes.
**How to avoid:** the code rewrite (queue SQL lines 407 + 511, campaigns router, schemas) and the DROP migration ship in the same commit/deploy. Confirm no residual reference with a grep before the DROP migration lands.

## Code Examples

### Existing workspace-settings upsert (clone for the grade ladder — D-16)
```python
# Source: app/routers/warmup.py:549-594 (VERIFIED)
@router.put("/settings")
async def update_settings_endpoint(body: WarmupSettingsUpdate, db=Depends(get_db), ctx=Depends(auth_dep)):
    await db.execute(text("""
        INSERT INTO warmup_settings (workspace_id, enabled, topics, ...)
        VALUES (:wid, :enabled, CAST(:topics AS jsonb), ...)
        ON CONFLICT (workspace_id) DO UPDATE SET
            enabled = EXCLUDED.enabled, ...
    """), {...})
    await db.commit()
    return {"status": "saved", "settings": _resolve_settings(...)}   # code-defaults resolved
```

### Existing fractional-gate bind (preserve for the account budget — D-05)
```python
# Source: app/services/queue.py:434-440 (VERIFIED)
# NOTE: keep CAST(:expected_now AS DOUBLE PRECISION) in the SELECT — an untyped
# bind is inferred bigint and truncates fractional expected_now to 0, silently
# blocking all new dialogs.
expected_now = camp_row.c_cap * frac * random.uniform(PACE_JITTER_LOW, PACE_JITTER_HIGH)
# → replace camp_row.c_cap (was c.max_new_dialogs_per_day) with the account grade budget
```

## Validation Architecture

`nyquist_validation` is enabled (config.json `workflow.nyquist_validation: true`).

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (async fixtures), asyncpg |
| Config file | `tests/conftest.py` (session-scoped `_setup_database`, ephemeral `outreach_test` DB) |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_new_dialog_limit.py tests/test_queue_even_pacing.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

> **Baseline caution (project memory `project-test-baseline-red`):** the full suite is order-dependent and RED on clean main (~88 failed/115 errors) while the same files pass in isolation. Do NOT trust full-suite exit code as a phase gate — run the targeted subset below, and diff against a clean-tree run of the same subset.

### Phase Requirements → Test Map
| Decision | Behavior | Test Type | Automated Command | File Exists? |
|----------|----------|-----------|-------------------|-------------|
| D-13 | sender-wide dedup: phone contacted in campaign A blocks re-open in campaign B | integration | `pytest tests/test_queue_new_dialog_limit.py -k sender_wide` | ❌ Wave 0 (extend existing) |
| D-01/D-06 | account budget cap counts across campaigns, not per-campaign | integration | `pytest tests/test_queue_new_dialog_limit.py` | ✅ extend |
| D-05 | pacing numerator = account budget, campaign window preserved | integration | `pytest tests/test_queue_even_pacing.py` | ✅ extend |
| D-04 | rate_per_day gate removed; min/hour/15-per-hour intact | integration | `pytest tests/test_senders.py tests/test_send.py -k rate` | ✅ extend |
| D-14 | auto-progression advances level after step days; stops at 3 | unit/integration | `pytest tests/test_senders.py -k grade` | ❌ Wave 0 |
| D-15 | manual override sets level + resets timer | integration | `pytest tests/test_senders.py -k override` | ❌ Wave 0 |
| D-16 | ladder GET/PUT, code-defaults on absent row | integration | `pytest tests/test_grade_settings.py` (new, mirror `test_warmup_router.py`) | ❌ Wave 0 |
| D-08 | new warmup pair charges initiator; known pair free; backfill idempotent | integration | `pytest tests/test_warmup_worker.py -k pair` | ✅ extend |
| D-09 | outreach reserve leaves warmup only the remainder | integration | `pytest tests/test_warmup_worker.py -k reserve` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** the targeted file(s) for that task, `-x`.
- **Per wave merge:** all Phase-22-touched test files as a targeted set (NOT the full suite — baseline is RED).
- **Phase gate:** targeted set green + clean-tree diff shows no regression in the touched files.

### Wave 0 Gaps
- [ ] `tests/test_grade_settings.py` — ladder GET/PUT + code-defaults (mirror `tests/test_warmup_router.py`).
- [ ] Grade progression/override cases in `tests/test_senders.py` (D-14/D-15).
- [ ] Sender-wide dedup + account-budget cases extending `tests/test_queue_new_dialog_limit.py` (D-13/D-01/D-06).
- [ ] New-pair budget + reserve cases extending `tests/test_warmup_worker.py` (D-08/D-09).
- [ ] Idempotent-backfill assertion for `sender_first_contacts` (existing warmed pairs not counted as new).
- [ ] conftest exists-guarded blocks for any SQL-only migration (backfill/CHECK) the above tests depend on.
- [ ] Helpers for pacing/dialog tests are copied verbatim between `test_queue_new_dialog_limit.py` and `test_queue_even_pacing.py` — extend both consistently.

## Security Domain

`security_enforcement` not explicitly disabled → included. This phase touches config mutation endpoints, not auth/crypto.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Existing JWT/AuthDep unchanged |
| V4 Access Control | yes | New GET/PUT ladder + PATCH grade must be workspace-scoped via existing `auth_dep` / `_assert_workspace_owns_sender` — a tenant must not read/write another workspace's ladder or a sender it doesn't own |
| V5 Input Validation | yes | Pydantic bounds on ladder (chats/day and step-days ranges, 3 fixed levels) + `current_level` override (1..3); reuse the soft/hard-cap `WarningItem` pattern |
| V6 Cryptography | no | No crypto changes |

### Known Threat Patterns
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Cross-tenant ladder read/write | Information Disclosure / Tampering | `auth_dep` workspace scoping (mirror warmup settings endpoints) |
| Grade override to an out-of-range level | Tampering | Pydantic `ge=1, le=3` + optional DB CHECK |
| SQL injection via budget/window | Tampering | Bind params only (never f-string) — existing Phase 13 rule |
| Cross-tenant warmup pair via registry | Information Disclosure | Preserve the existing workspace-partitioned pairing (`_get_active_pool` JOIN on `workspace_id`); `sender_first_contacts` entries are same-workspace by construction |

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Per-campaign new-dialog cap (`campaigns.max_new_dialogs_per_day`, Phase 12) | Per-account grade budget (this phase) | Phase 22 | Budget sees the whole sender across campaigns + warmup |
| Per-campaign dedup (Phase 12 D-03) | Sender-wide dedup (D-13) | Phase 22 | Known peer in any campaign no longer spends budget |
| Daily message cap `rate_per_day`=150 (Phase 2) | Removed (D-04) | Phase 22 | Only min/hour + 15-unique-contacts/hr remain |
| Warmup full-mesh every session charges nothing tracked | New-pair-only budget via `sender_first_contacts` (D-08) | Phase 22 | Warmup competes for the shared budget behind outreach reserve |

**Deprecated/outdated after this phase:**
- `campaigns.max_new_dialogs_per_day`, `senders.rate_per_day`, `RateLimits.per_day`, `SenderCreate/Update.rate_per_day` — all removed.
- The `[SUPERSEDED]` warmup-pool-only design (weekly 3→4→6→8 ladder, `max_messages_per_day` on `warmup_settings`) — not implemented; `sender_first_contacts` + priority-reserve concepts carried into D-08/D-09 only.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Trailing-24h is the right shared-budget window (over calendar-day) | Pitfall 4 / warmup §3 | Low — matches the outreach cap; if the user wants calendar-day resets, one window constant changes. Confirm in planning. |
| A2 | `sender_first_contacts` is sender↔sender pair-based (warmup), backfilled from `warmup_messages`/`warmup_sessions`; outreach dedup stays in `message_queue` | code §4 / Pattern | Low-Med — CONTEXT.md D-08 leaves schema to discretion; the `[SUPERSEDED]` `(sender_id, peer_phone)` shape is explicitly obsolete. If the user intended one unified registry, the backfill source widens. |
| A3 | A periodic sweep (advance ≤1 level/tick) is acceptable for auto-progression vs lazy per-read recompute | Pattern 3 | Low — Claude's discretion; correctness at gate time is the only requirement. |
| A4 | New ORM tables/columns are picked up by conftest `create_all` without a conftest edit; only SQL-only ops need a manual block | Pitfall 1 | Low — verified by reading conftest's create_all-first flow; a mis-declared model would surface immediately as a test-collection error. |
| A5 | The initiator (charged account) should be the older/more-warmed one and must be `sender_a` so it writes first | code §3 | Low-Med — from `[SUPERSEDED]` D-12 (safety bonus); user marked initiator choice as Claude's discretion. |

## Open Questions

1. **Shared-budget window: trailing-24h or calendar-day?**
   - What we know: queue uses trailing-24h; warmup `_count_sent_today` uses calendar-day.
   - What's unclear: which the user expects for the unified budget.
   - Recommendation: trailing-24h (consistency + anti-midnight-burst); confirm at plan review.

2. **One migration or several (056–058)?**
   - What we know: next slot is 056; operations are separable.
   - Recommendation: separate idempotent files per concern (columns+drop, registry+backfill, ladder table) for clean rollback reasoning; planner's call.

3. **Manual override transport (extend `SenderUpdate` vs dedicated sub-route)?**
   - What we know: PATCH `/senders/{slug}` exists (line 618); a dedicated `/senders/{slug}/grade` mirrors the profile sub-routes.
   - Recommendation: dedicated PATCH sub-route to keep grade semantics (timer reset) explicit and out of the generic update path.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL 16 | migrations, all queries | ✓ (running prod `outreach-platform-db`) | 16 | — |
| Docker Compose test-overlay | tests | ✓ (`docker-compose.test.yml`) | — | — |
| Python/FastAPI/SQLAlchemy async | all code | ✓ (running api container) | 3.11 | — |

No new external dependencies. No missing dependencies.

## Sources

### Primary (HIGH confidence — VERIFIED against working-tree source 2026-07-08)
- `app/services/queue.py:359-699` — `_process_next_for_sender`, pacing pre-query, `_check_rate_limits`.
- `app/services/warmup.py:36-42, 84-133, 167-239, 305-313, 541-629` — LEVEL_CONFIG, worker, pool, session creation, initiator logic.
- `app/models/__init__.py:74-152, 332-453, 760-799` — Sender, Conversation, Warmup models, Campaign.
- `app/schemas/__init__.py:75-186, 790-945` — RateLimits, Sender/Campaign schemas.
- `app/routers/senders.py:60-220, 540-648` — rate caps, validation, response builder, PATCH.
- `app/routers/campaigns.py:62-655` — max_new_dialogs validation + references.
- `app/routers/warmup.py:491-594` — settings GET/PUT template.
- `tests/conftest.py:75-298` — schema build + hardcoded migration list.
- `migrations/038_warmup_settings.sql`, `ls migrations/` (highest = 055).
- CONTEXT.md D-01..D-17, `.planning/config.json`, `/root/CLAUDE.md`, `/root/apps/aimly/tg-outreach/CLAUDE.md`, project MEMORY.md entries.

### Secondary / Tertiary
- None — no external/library research required for this internal-code phase.

## Metadata

**Confidence breakdown:**
- Integration points / line accuracy: HIGH — every location read against current source; drift from CONTEXT.md noted.
- Architecture / patterns: HIGH — reuses established `warmup_settings` + Phase 13 patterns verbatim.
- Pitfalls: HIGH — conftest non-glob, ORM-default drift, D-13 semantic change, and window mismatch all verified in source + project memory.
- Discretionary items (registry schema, initiator, progression mechanism): MEDIUM — recommendations given, flagged in Assumptions Log for plan confirmation.

**Research date:** 2026-07-08
**Valid until:** 2026-08-07 (stable internal codebase; re-verify line numbers if the queue/warmup files change before planning).
