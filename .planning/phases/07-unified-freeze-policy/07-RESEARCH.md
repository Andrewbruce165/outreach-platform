# Phase 7: Unified Freeze Policy - Research

**Researched:** 2026-06-23
**Domain:** Brownfield modification of an existing Telegram-outreach backend (Python 3.11, FastAPI, SQLAlchemy 2.0 async, Telethon, PostgreSQL 16). Antispam/spam-limit handling, queue worker, sender restriction state machine.
**Confidence:** HIGH — every claim below is cited to a concrete file:line read directly from this repo's HEAD. No external library research was needed; the phase mirrors an existing in-repo pattern.

## Summary

Phase 7 ("Unified Freeze Policy") is a small, surgical brownfield change with **no new migrations** (migration `028_sender_restriction.sql` already shipped the `restriction_status` / `restricted_until` columns). The entire feature is the convergence of two divergent code paths onto one already-working pattern.

The codebase has **two** ways a sender can hit a spam restriction today:
1. **PEER_FLOOD / ACCOUNT_FROZEN** (the *good* pattern) — caught in `queue.py` during a send attempt. It pauses the sender's pending items (reschedules `scheduled_at` +24h), flags the sender `restriction_status='spam_limited'` (or `'frozen'`) with a `restricted_until` recheck timestamp, and lets the **listener restriction-reconcile sweep** auto-resume the sender once @SpamBot confirms it's free. It does **not** touch `ai_enabled` — replies keep flowing.
2. **Antispam signal** (the *bad* pattern) — caught in `listener._handle_antispam_signal` when @SpamBot (or another antispam bot) messages our account. It does the opposite: sets queue items to terminal `status='failed'` (no auto-resume — this is exactly the b7cc7d06 incident: 37 contacts terminally killed), and disables `ai_enabled` across **all** of that sender's conversations.

This phase rewrites path (2) to match path (1), plus adds one filter clause to `rotation.py` so cold-contact assignment never parks a new contact on a restricted sender, plus a regression test. The "verify worker skips restricted senders" requirement is **already satisfied** in code (`queue.py:401`) and `rotation.py` already filters out `auth_status='banned'` senders — only the `restriction_status` clause is missing.

**Primary recommendation:** Rewrite `_handle_antispam_signal` (listener.py:881-957) to (a) pause pending items via `scheduled_at` reschedule instead of `status='failed'`, (b) write `restriction_status='spam_limited'` + `restricted_until` mirroring `queue.py:743-754`, and (c) **delete** the `UPDATE conversations SET ai_enabled=false` block entirely. Add `AND s.restriction_status = 'none'` to `rotation.py:117-121`. Extend the existing tests in `tests/test_spambot_selfcheck.py` and `tests/test_sender_restriction.py`.

<phase_requirements>
## Phase Requirements

The ROADMAP lists requirements as "TBD (derive on plan)". No formal REQ-ID exists in REQUIREMENTS.md for this post-v1 block (the v1 requirement table ends at ADMN-03). The planner should derive phase-local requirement IDs from the proposal's Phase A acceptance criteria. Suggested IDs (planner may rename):

| ID (suggested) | Description | Research Support |
|----|-------------|------------------|
| FRZ-01 | Antispam signal pauses (not terminally fails) the sender's pending queue items, mirroring PEER_FLOOD | `queue.py:743-754` (reference pattern); current bad behaviour `listener.py:930-946` |
| FRZ-02 | Antispam signal flags sender `restriction_status='spam_limited'` + `restricted_until` so the existing reconcile sweep auto-resumes | `queue.py:748-753`; reconcile `listener.py:1352-1449` |
| FRZ-03 | Antispam signal STOPS disabling `ai_enabled` in conversations — replies in existing dialogues continue | Current bad behaviour `listener.py:909-925`; design rationale proposal §"Finalised freeze policy" |
| FRZ-04 | Rotation candidate filter excludes restricted senders (`AND s.restriction_status='none'`) — no new cold contact lands on a limited account | `rotation.py:112-125` |
| FRZ-05 | Regression test: queue worker skips a restricted sender (already true in code — assert it) | `queue.py:401-406` |

Source of truth for acceptance: `.planning/proposals/sender-pool-resilience.md` → "Phase A — Unified freeze policy".
</phase_requirements>

## Reference Pattern: How PEER_FLOOD Soft-Restriction Works End-to-End

This is the single most important section — `_handle_antispam_signal` must be rewritten to mirror it.

### Step 1 — Catch & flag (the WRITE path), `app/services/queue.py:733-774`

When a send attempt raises a PEER_FLOOD error, inside `QueueWorker.__send_item_inner`:

```python
# Source: app/services/queue.py:733-754
elif error_code == "PEER_FLOOD":
    # Spam restriction — worse than FloodWait, pause all tasks 24h.
    pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
    recheck_at = datetime.now(timezone.utc) + timedelta(
        seconds=get_settings().restriction_recheck_interval_seconds
    )
    async with AsyncSessionLocal() as db2:
        await db2.execute(text("""
            UPDATE message_queue SET scheduled_at = :pause_until
            WHERE sender_id = :sid AND status = 'pending'
        """), {"pause_until": pause_until, "sid": str(sender.id)})
        await db2.execute(text("""
            UPDATE senders
            SET restriction_status = 'spam_limited',
                restricted_until = :recheck_at
            WHERE id = :sid
        """), {"recheck_at": recheck_at, "sid": str(sender.id)})
        await db2.commit()
```

Key properties to replicate:
- Pending items are **paused** by pushing `scheduled_at` 24h forward, **NOT** set to `failed`. They stay `status='pending'` so they remain pickable.
- The 24h `pause_until` and the `recheck_at` are **two different timestamps**: `pause_until` is the empirical queue pause; `recheck_at` (= now + `restriction_recheck_interval_seconds`, default 6h) is when the reconcile sweep re-pings @SpamBot. Per CLAUDE.md, the empirical 24h pause must not be changed without discussion.
- It uses a **separate session** `AsyncSessionLocal()` (`db2`) so the write commits independently of the item's own transaction (`db`).
- `restricted_until` is `TIMESTAMPTZ` (migration 028). `restriction_status` is `TEXT NOT NULL DEFAULT 'none'` with `CHECK IN ('none','spam_limited','frozen')`.

`ACCOUNT_FROZEN` (queue.py:776-812) is the identical structure but writes `restriction_status='frozen'`. The antispam signal should write `'spam_limited'` (soft limit), not `'frozen'`.

### Step 2 — Pre-send skip (the worker already honours the flag), `app/services/queue.py:397-406`

```python
# Source: app/services/queue.py:397-406  (inside _check_rate_limits)
# Migration 028: don't burn sends on a restricted (spam_limited/frozen) account.
if sender_row.restriction_status != "none":
    logger.debug(
        f"Sender {sender_id}: restricted "
        f"({sender_row.restriction_status}, until={sender_row.restricted_until}) — skipping tick"
    )
    return False
```

The `SELECT` at `queue.py:375-383` already pulls `restriction_status, restricted_until`. **This means FRZ-05 ("worker skips restricted sender") is already implemented** — the phase only needs a regression test asserting it, not new code.

### Step 3 — Auto-resume (the RECONCILE sweep), `app/services/listener.py:1352-1449`

`TelegramListener._restriction_reconcile_tick()` runs on a background loop (`restriction_reconcile_interval_seconds`, default 15 min):

```python
# Source: app/services/listener.py:1366-1375 (selection)
rows = (await db.execute(text("""
    SELECT id, slug, restriction_status
    FROM senders
    WHERE restriction_status <> 'none'
      AND restricted_until IS NOT NULL
      AND restricted_until <= NOW()
"""))).fetchall()
```

For each due sender that is currently connected, it calls `telegram_service.check_spambot(client, selfcheck_key=slug)` and acts on the verdict:
- **free** → `restriction_status='none'`, `restricted_until=NULL`, and **un-pause** the queue: `UPDATE message_queue SET scheduled_at = NOW() WHERE sender_id=:sid AND status='pending' AND scheduled_at > NOW()` (listener.py:1395-1411). This is why pausing (not failing) is essential — only `pending` rows are resurrected.
- **suspended** → `auth_status='banned'` (listener.py:1412-1418).
- **limited / unknown** → extend `restricted_until` (listener.py:1419-1435), preferring SpamBot's quoted release time +5min.

**Critical dependency for the rewrite:** the reconcile's resume step only finds items in `status='pending'`. If `_handle_antispam_signal` keeps setting `status='failed'`, those items will NEVER be resumed. The rewrite to "pause not fail" is what makes auto-resume work for the antispam path.

## Current (Bad) Behaviour to Replace

### `_handle_antispam_signal`, `app/services/listener.py:881-957`

Entry points (both funnel here):
- `listener.py:636` — proactive bot filter when `event.sender.bot=True` and `sender.id in ANTISPAM_BOT_IDS`.
- `listener.py:660` — keyword backup detector (`name` contains "spam"/"антиспам"/etc.).

Current body does THREE things; the rewrite changes #1 and #2, keeps #0:

```python
# Source: app/services/listener.py:901-905  (#0 — KEEP, do not touch)
# SpamBot self-check guard (quick task 260622-gxt): solicited reply during our
# own @SpamBot ping → skip everything. This early-return MUST be preserved.
if telegram_service.is_spambot_selfcheck(sender_slug):
    logger.info(f"🔕 [{sender_slug}] solicited SpamBot reply during self-check — skip auto-cancel")
    return
```

```python
# Source: app/services/listener.py:909-925  (#1 — DELETE THIS BLOCK)
# Disables AI across ALL conversations of the sender. Violates the freeze policy
# ("keep replying on existing dialogues"). Remove entirely.
UPDATE conversations
SET ai_enabled = false, paused_at = NOW(), paused_reason = :reason, updated_at = NOW()
WHERE sender_id = :sender_id AND ai_enabled = true
RETURNING id
```

```python
# Source: app/services/listener.py:930-946  (#2 — REPLACE with pause+flag)
# Terminally fails the queue (no auto-resume). Replace with the PEER_FLOOD pattern:
#   - reschedule pending scheduled_at +24h (status stays 'pending')
#   - write senders.restriction_status='spam_limited' + restricted_until
UPDATE message_queue
SET status = 'failed', error_message = :reason, finished_at = NOW()
WHERE sender_id = :sender_id AND status IN ('pending', 'processing')
RETURNING id
```

**Note on `'processing'` items:** the current code also cancels `status='processing'` items. The PEER_FLOOD path only touches `status='pending'`. An item in `processing` is the one currently being sent (the very send that may have triggered the bot reply); leave it to its own error-handling path rather than reaching across to it. The planner should decide whether to scope the pause to `status='pending'` only (recommended — matches PEER_FLOOD exactly and the reconcile resume query).

### `rotation.py` candidate filter, `app/services/rotation.py:112-125`

```python
# Source: app/services/rotation.py:112-125
candidates_rows = await db.execute(text("""
    SELECT s.id AS sid
    FROM campaign_senders cs
    JOIN senders s ON s.id = cs.sender_id
    WHERE cs.campaign_id = :cid
      AND s.lifecycle_status = 'active'
      AND s.auth_status = 'ok'
      AND s.role = 'sender'
      AND s.workspace_id = :wid
"""), {"cid": cid_str, "wid": workspace_id_str})
```

A `spam_limited` sender stays `lifecycle_status='active'` + `auth_status='ok'` (migration 028 header explains this is *by design* — restriction is orthogonal to auth). So this filter currently still hands new cold contacts to a restricted sender. **The change is one line:** add `AND s.restriction_status = 'none'` to the WHERE clause (e.g. after line 121). No other rotation logic changes.

## State Inventory: where `restriction_status` / `ai_enabled` live

| Concern | Location | Notes |
|---|---|---|
| `restriction_status` column | `senders` table — `migrations/028_sender_restriction.sql`; ORM `app/models/__init__.py:93` | `TEXT NOT NULL DEFAULT 'none'`, CHECK `('none','spam_limited','frozen')`, partial index `idx_senders_restriction` on `restricted_until WHERE restriction_status <> 'none'` |
| `restricted_until` column | `senders` table — migration 028 | `TIMESTAMPTZ NULL` |
| WRITE `spam_limited` | `queue.py:748-753` (PEER_FLOOD), `senders.py:635` (manual spambot-check) | antispam path will become the 3rd writer |
| WRITE `frozen` | `queue.py:789-794` (ACCOUNT_FROZEN) | not touched by this phase |
| CLEAR to `none` | `listener.py:1399` (reconcile free), `senders.py:652` (manual spambot free) | |
| READ / skip | `queue.py:401` (worker pre-send skip — already done) | |
| Derived UI status | `senders.py:73-84` `_derive_status`: `frozen`→'frozen', `spam_limited`→'limited' | unchanged by this phase |
| `ai_enabled` column | `conversations` table — ORM `app/models/__init__.py:259` (`default=True`) | toggled by: `_handle_antispam_signal` (to be REMOVED), conversations.py:325 (manual manager takeover — KEEP), `_handle_bot_message` listener.py:993 (bot_ignored — KEEP) |
| Antispam path touches `ai_enabled` | ONLY `listener.py:909-925` | This is the only place to remove. Verified by grep: no other antispam-triggered `ai_enabled=false`. |

**ai_engine has NO restriction check** (proposal §"What already works", verified): replies flow regardless of `restriction_status`, gated only by `ai_enabled` / manager takeover. So removing the `ai_enabled=false` block is sufficient to keep replies alive — nothing else gates them on the restriction flag.

## Architecture Patterns

### Pattern: separate `AsyncSessionLocal()` for the flag write
Both PEER_FLOOD (`queue.py:743`) and reconcile (`listener.py:1394`) open their own `async with AsyncSessionLocal() as db2:` for restriction writes. `_handle_antispam_signal` already does this (`listener.py:908`). Keep that structure — one session, one commit, wrapped in the existing `try/except` that logs and swallows so a transient DB error never poisons the listener event loop (`listener.py:907,956`).

### Pattern: idempotent / safe re-entry
Setting `restriction_status='spam_limited'` when already `spam_limited` is harmless (plain UPDATE). But to mirror the reconcile's "extend" semantics, re-asserting on a fresh signal naturally pushes `restricted_until` forward, which is the desired behaviour (a second warning means re-arm the recheck timer). No `WHERE restriction_status='none'` guard is needed — overwriting `frozen` with `spam_limited` would be a downgrade, but the antispam-bot signal is a soft limit so the planner should consider whether to guard against clobbering an existing `frozen` (recommended: `WHERE id=:sid AND restriction_status <> 'frozen'`, or leave unguarded since frozen accounts won't be sending anyway). Flag this as a small decision for the plan.

### Anti-pattern to avoid
- **Do not** change queue interval / rate-limit constants or the empirical 24h pause (CLAUDE.md hard rule — "не трогать интервалы без явного обсуждения"). Use `pause_until = now + timedelta(hours=24)` exactly as PEER_FLOOD does.
- **Do not** introduce Alembic or ORM migrations. None are needed (028 covers it). If any migration is somehow required it must be raw SQL in `migrations/NNN_*.sql`, idempotent, auto-applied (CLAUDE.md).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| Auto-resume of a restricted sender | A new timer/scheduler in the antispam path | The existing `_restriction_reconcile_tick` (listener.py:1352) — it already polls `restricted_until <= NOW()` and un-pauses | Writing the same two columns the reconcile reads is the entire integration. |
| Pausing the queue | A new `paused` status or flag column | Reschedule `scheduled_at` forward + keep `status='pending'` | This is the established PEER_FLOOD mechanism; the reconcile resume query depends on it. |
| Keeping replies alive | A `restriction_status` check in ai_engine | Nothing — just delete the `ai_enabled=false` block | ai_engine never checks restriction; replies already flow. |
| Self-check false-positive suppression | New guard logic | Existing `is_spambot_selfcheck` early-return (listener.py:901) | Already shipped in 260622-gxt; just preserve it. |

## Common Pitfalls

### Pitfall 1: Leaving `status='failed'` breaks auto-resume
**What goes wrong:** if pending items are set to `failed` instead of paused, the reconcile sweep's resume query (`WHERE status='pending' AND scheduled_at > NOW()`, listener.py:1405-1407) will never find them. The account un-restricts but the campaign stays dead — the exact b7cc7d06 symptom.
**How to avoid:** items must remain `status='pending'`; only `scheduled_at` moves.
**Warning sign:** regression test where a restricted-then-freed sender resumes sending must assert items return to sendable.

### Pitfall 2: Self-check guard must stay FIRST
**What goes wrong:** if the rewrite reorders logic and the `is_spambot_selfcheck` check no longer runs before the flag write, our own @SpamBot reconcile ping (which lands in the listener stream) would re-flag the sender we're trying to clear — an infinite limited loop.
**How to avoid:** keep the early-return at listener.py:901-905 as the very first statement. The reconcile passes `selfcheck_key=slug` (listener.py:1389) specifically to arm this window.
**Note:** the guard is **in-memory only** (`TelegramService._spambot_selfcheck`, telegram.py:236). It covers the reconcile sweep (same listener process) but NOT the manual `/spambot-check` API endpoint (api process) — documented limitation in 260622-gxt, out of scope here.

### Pitfall 3: `processing` items
**What goes wrong:** cancelling/pausing `status='processing'` reaches across to an item another coroutine is mid-send on, causing a lost-update race.
**How to avoid:** scope the pause to `status='pending'` only (matches PEER_FLOOD). The in-flight `processing` item will finish/fail through its own path.

### Pitfall 4: Two timestamps, two purposes
**What goes wrong:** conflating `pause_until` (24h queue pause) with `restricted_until` (6h recheck) — e.g. setting `restricted_until` to +24h means the reconcile waits a full day before re-checking @SpamBot.
**How to avoid:** `restricted_until = now + restriction_recheck_interval_seconds` (default 6h, config.py:79); `scheduled_at = now + 24h`. Copy `queue.py:739-742` verbatim.

### Pitfall 5: Test DB safety
**What goes wrong:** running pytest without the overlay drops the prod schema (the 2026-05-26 incident).
**How to avoid:** ALWAYS `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. The conftest guard (tests/conftest.py:46-77) will RuntimeError otherwise, but the correct path is the overlay.

## Code Examples

### Target rewrite of `_handle_antispam_signal` (illustrative — planner refines)
```python
# Mirrors queue.py:733-754 PEER_FLOOD. KEEP the self-check early-return above this.
async with AsyncSessionLocal() as session:
    pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
    recheck_at = datetime.now(timezone.utc) + timedelta(
        seconds=get_settings().restriction_recheck_interval_seconds
    )
    # pause (not fail) pending — reconcile will resume on SpamBot 'free'
    await session.execute(text("""
        UPDATE message_queue SET scheduled_at = :pause_until
        WHERE sender_id = :sid AND status = 'pending'
    """), {"pause_until": pause_until, "sid": str(sender_id)})
    await session.execute(text("""
        UPDATE senders
        SET restriction_status = 'spam_limited', restricted_until = :recheck_at
        WHERE id = :sid
    """), {"recheck_at": recheck_at, "sid": str(sender_id)})
    await session.commit()
# NO conversations / ai_enabled update — replies keep flowing.
```

### Rotation filter one-liner (rotation.py:117-121)
```python
WHERE cs.campaign_id = :cid
  AND s.lifecycle_status = 'active'
  AND s.auth_status = 'ok'
  AND s.role = 'sender'
  AND s.restriction_status = 'none'   # <-- ADD THIS
  AND s.workspace_id = :wid
```

## State of the Art

| Old Approach (current code) | New Approach (this phase) | Impact |
|---|---|---|
| Antispam → `status='failed'` (terminal) | Antispam → pause `pending` + flag `spam_limited` | Auto-resume via existing reconcile; no terminal kill |
| Antispam → `ai_enabled=false` on all dialogues | Antispam → leave `ai_enabled` untouched | Replies in established conversations continue (Telegram allows them) |
| Rotation ignores `restriction_status` | Rotation excludes `restriction_status != 'none'` | New cold contacts never parked on a limited account |

**No deprecated libraries or external APIs involved.** This is purely internal logic convergence.

## Open Questions

1. **Guard against clobbering `frozen` with `spam_limited`?**
   - What we know: antispam-bot signal is a *soft* limit. A `frozen` (hard) sender is more severe.
   - What's unclear: whether a soft-signal write should downgrade an existing `frozen` flag.
   - Recommendation: add `AND restriction_status <> 'frozen'` to the senders UPDATE, OR accept that frozen accounts aren't sending anyway so the race is benign. Small plan-level decision.

2. **Scope pause to `pending` only, or include `processing`?**
   - What we know: PEER_FLOOD touches only `pending`; current antispam touches `pending`+`processing`.
   - Recommendation: scope to `pending` only (match PEER_FLOOD, avoid the Pitfall 3 race). Confirm in plan.

3. **`ANTISPAM_BOT_IDS` / keyword detector unchanged?**
   - Both entry points (listener.py:636, 660) funnel into the same handler. The rewrite changes only the handler body, so detection is unaffected. No change needed.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode=auto` in pyproject — async tests need no explicit marker) |
| Config file | `pyproject.toml` (asyncio_mode); fixtures in `tests/conftest.py` |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_spambot_selfcheck.py tests/test_sender_restriction.py tests/test_rotation_campaign.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

⚠️ NEVER run `docker compose run --rm api pytest` without the test overlay — DATABASE_URL leaks to prod and conftest does DROP SCHEMA (2026-05-26 incident). The test overlay spins an ephemeral tmpfs `db-test`.

### Phase Requirements → Test Map
| Req | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FRZ-01/02/03 | Antispam signal pauses pending (status stays pending) + flags `spam_limited` + leaves `ai_enabled` true | unit (DB-backed) | extend `tests/test_spambot_selfcheck.py::test_antispam_guard_cancels_when_no_selfcheck` — assert `q_status=='pending'`, `restriction_status=='spam_limited'`, `ai_enabled is True` | ✅ exists, MUST update assertions (currently asserts `failed` + `ai_enabled False`) |
| FRZ-04 | Rotation excludes restricted sender | unit (DB-backed) | new test in `tests/test_rotation_campaign.py` — seed a `spam_limited` sender in `campaign_senders`, assert `get_or_assign_sender` doesn't pick it | ✅ file exists, ❌ test to add |
| FRZ-05 | Worker skips restricted sender | unit (source-inspect, already present) | `tests/test_sender_restriction.py::test_queue_pre_send_skips_restricted` | ✅ already passing |
| Self-check guard preserved | Solicited reply still skips | unit (DB-backed) | `tests/test_spambot_selfcheck.py::test_antispam_guard_skips_when_selfcheck_active` — assert `pending`/`ai_enabled True` (already correct for new behaviour) | ✅ exists, may pass as-is |

### Sampling Rate
- **Per task commit:** quick run command above (3 test files).
- **Per wave merge:** full suite.
- **Phase gate:** full suite green before `/gsd:verify-work`.

### Wave 0 Gaps
- [ ] **CRITICAL:** `tests/test_spambot_selfcheck.py::test_antispam_guard_cancels_when_no_selfcheck` currently asserts the OLD behaviour (`q_status=='failed'`, `ai_enabled is False`, listener.py:157-158). This test WILL BREAK with the rewrite and MUST be updated to the new contract (`pending` + `spam_limited` flag + `ai_enabled True`). This is expected and part of the phase, not a regression.
- [ ] New rotation-filter test in `tests/test_rotation_campaign.py` (covers FRZ-04). `test_sender_factory(**overrides)` accepts `restriction_status='spam_limited'` directly (conftest.py:367-388 — `defaults.update(overrides)`).
- [ ] Fixtures already exist: `async_db_session`, `test_workspace`, `test_sender_factory` (conftest.py:187, 349, 359). `_seed_queue_and_conversation` helper already in test_spambot_selfcheck.py — reuse pattern.

*No framework install needed; pytest infra is mature.*

## Environment Availability

Step 2.6: SKIPPED for runtime tooling — this is a pure code change. The only "environment" dependency is the Docker test overlay, which is part of the repo (`docker-compose.test.yml`, verified present). No external services/CLIs introduced.

## Project Constraints (from CLAUDE.md)

- **Communicate in Russian; code & commits in English** (root + project CLAUDE.md). Discuss before non-trivial code.
- **Async everywhere** — all DB via `async/await` + `AsyncSession`. The handler is already async.
- **Migrations: raw SQL only, idempotent, auto-applied** at api start (`app/database.py::_apply_migrations`). NEVER Alembic. **This phase needs no migration (028 exists).**
- **Never** `time.sleep()`, synchronous `requests`, or `print()` instead of `logging`.
- **Do not change queue intervals / rate-limit constants** without explicit discussion — the 24h pause and `4/20/150` limits are empirical. Copy `pause_until = now + 24h` verbatim from PEER_FLOOD.
- **Do not break FloodWait retry logic** without explicit request — this phase touches PEER_FLOOD's *neighbour* (antispam) but the planner must not alter the FloodWait/PEER_FLOOD branches themselves; only add the antispam branch's mirror in the listener.
- **Tests ONLY via test-overlay** (`docker-compose.test.yml`) — conftest guard blocks bare runs; DROP-SCHEMA incident precedent.
- **Sessions encrypted, API_KEY not in logs** (general; not directly relevant here).

## Sources

### Primary (HIGH confidence — direct repo reads at HEAD)
- `app/services/listener.py:881-957` — current `_handle_antispam_signal` (the rewrite target)
- `app/services/listener.py:624-661` — antispam detection entry points
- `app/services/listener.py:1352-1449` — `_restriction_reconcile_tick` (auto-resume mechanism)
- `app/services/queue.py:375-406` — sender restriction SELECT + pre-send skip (FRZ-05 already done)
- `app/services/queue.py:733-812` — PEER_FLOOD / ACCOUNT_FROZEN reference pattern
- `app/services/rotation.py:112-125` — candidate filter (FRZ-04 target)
- `app/models/__init__.py:93, 259, 16-21` — senders.restriction_status, conversations.ai_enabled, QueueItemStatus enum
- `migrations/028_sender_restriction.sql` — column definitions, CHECK, index
- `app/config.py:78-89` — recheck/reconcile intervals
- `app/services/telegram.py:236-326` — self-check registry + check_spambot
- `app/routers/senders.py:73-84, 635-655` — `_derive_status`, manual spambot-check writer
- `app/routers/conversations.py:325-345` — manual manager takeover (KEEP its ai_enabled write)
- `tests/test_spambot_selfcheck.py` — antispam guard tests (MUST update)
- `tests/test_sender_restriction.py` — restriction/reconcile tests (extend)
- `tests/conftest.py:187, 349, 359-388` — fixtures
- `.planning/proposals/sender-pool-resilience.md` — design intent, Phase A scope, freeze policy table
- `.planning/STATE.md` Quick Tasks 260619-frz, 260622-gxt, 260622-j52 — feature history & incident

### Secondary / Tertiary
- None. No external/web sources required; the phase is fully specified by in-repo code and the proposal.

## Metadata

**Confidence breakdown:**
- Reference pattern (PEER_FLOOD) — HIGH — read verbatim from queue.py.
- Rewrite target & ai_enabled removal — HIGH — every touch point grep-verified.
- Rotation filter — HIGH — exact WHERE clause read.
- FRZ-05 already-done claim — HIGH — code at queue.py:401 confirmed.
- Test plan — HIGH — existing tests read; one will intentionally break and needs updating.

**Research date:** 2026-06-23
**Valid until:** ~30 days (stable internal code; only risk is concurrent edits to listener.py/queue.py by other phases).
