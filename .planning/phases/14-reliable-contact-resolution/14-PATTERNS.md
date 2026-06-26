# Phase 14: Reliable Contact Resolution - Pattern Map

**Mapped:** 2026-06-26
**Files analyzed:** 8 (6 modified, 1 new migration, 1 new model-mirror edit) + 3-4 new test files
**Analogs found:** 8 / 8 (this is a brownfield hardening phase — every change extends an existing analog in place)

> **Verification note:** All file:line anchors from RESEARCH.md were re-read against live code on 2026-06-26 and are **accurate**. One CONTEXT.md slip corrected: D's discretion says *"`source` уже есть в `contacts_cache`"* — that is **wrong**. `source` lives on **`contacts`** (`app/models/__init__.py:446`); `ContactCache` (table `contacts_cache`) has **no** `source` column. RESEARCH.md states this correctly. Plan against `contacts`.

---

## File Classification

| File (modify unless noted) | Role | Data Flow | Closest Analog (pattern to copy from) | Match |
|---|---|---|---|---|
| `app/services/contact_check_worker.py` | worker logic (claim-queue tick) | batch resolve; reads `contacts`+`senders`, writes `contacts` | **itself** — extend `_tick()` / `_apply_results()` in place | exact (self) |
| `app/services/checker.py` | Telethon service call | request-response per phone; reads/writes `contacts_cache` | **itself** `_check_phones_locked` + reference `telegram.py:582-608` for `ImportContactsRequest` shape | exact (self) + role-match |
| `app/config.py` (OR worker module-level) | config env-knob | startup config read | existing `CONTACT_CHECK_BATCH_SIZE/POLL_INTERVAL` (`contact_check_worker.py:43-44`) **and** `Settings` Field pattern (`config.py:67-89`) | exact — **see knob-location decision below** |
| `migrations/034_*.sql` (**NEW**) | raw SQL migration | DDL ALTER on `contacts` | `migrations/028_sender_restriction.sql` (idempotent ADD COLUMN + drop/recreate CHECK) | exact template |
| `app/models/__init__.py` `Contact` (427-457) | ORM model | create_all schema mirror | `Sender` restriction cols (`__init__.py:96-99`) — how migration cols are mirrored into ORM | exact |
| Phase-10 restriction infra (reuse) `app/services/restriction_audit.py` | restriction helper (call-site, not edit) | append-only write to `sender_restriction_events` + UPDATE `senders` | `record_restriction_event(... db=db)` dual-mode helper (`restriction_audit.py:48-87`) | exact reuse |
| `app/services/aimly/tg-outreach/CLAUDE.md` + `.planning/notes/checker-false-negatives.md` | docs | n/a | existing §"Семантика checker'а" prose | n/a |
| `tests/test_contact_check_worker.py` (extend) + new `tests/test_checker_probe.py` / `test_checker_cap.py` / `test_checker_pool.py` | test | n/a | existing `tests/test_contact_check_worker.py`, `test_contact_check_worker_skip_locked.py` | exact (extend) |

**Data-flow map (what touches which table):**
- `contacts` — read (claim SELECT) + write (`_apply_results`): worker. Read-only `tg_status='registered'` gate: `campaign_enqueue.py:163,237`, `campaigns.py:220`.
- `contacts_cache` — written by `checker._save_cache` (`is_registered`, `telegram_id`); read by `checker._lookup_cache`. **Daily-cap durability source** (Pitfall 5): count rows per `sender_id` since `date_trunc('day', now())`.
- `senders` — read (claim JOIN LATERAL filter) + write (`restriction_status`/`restricted_until`/`lifecycle_status` for D-06 mark + D-03 activation).
- `sender_restriction_events` — append-only write via `record_restriction_event` (D-06 throttle event).

---

## Pattern Assignments

### `app/services/contact_check_worker.py` (worker, batch resolve) — the hub of the phase

**Analog:** itself. Four distinct edits land here, all extending the existing `_tick()` / `_apply_results()`.

**(A) RESV-05/D-11 — the root-cause fix (JOIN LATERAL WHERE, lines 132-139).** Current code filters on `role` + `auth_status` only — this is the hole that let the broken checker keep lying:
```python
# CURRENT (contact_check_worker.py:132-139)
JOIN LATERAL (
    SELECT id, slug, session_string, proxy
    FROM senders
    WHERE workspace_id = c.workspace_id
      AND role = 'checker'
      AND auth_status = 'ok'
    LIMIT 1
) s ON TRUE
```
Add two `AND`s (D-11): `AND restriction_status = 'none'` and `AND lifecycle_status <> 'paused'`. This is the single most important change — it makes the D-06 restriction mark actually stop the worker, so you NEVER nuke `auth_status` again (Pitfall 2).

**(B) RESV-04/D-08 — mobile-first ordering (line 144).** Current `ORDER BY c.created_at ASC` → prepend mobile-priority:
```python
ORDER BY (c.phone LIKE '+79%') DESC,   -- mobiles (+79…) ~50% live, drain first
         c.created_at ASC
```

**(C) RESV-02/D-10 — burst-cap (lines 43-44, 58-59, LIMIT at 149).** The cap is the batch `LIMIT :n` already wired to `self.batch_size` (default 5). Knob mechanics: shrink/raise the effective per-batch resolve count to ≤~30, enforce per-checker cooldown via `restricted_until` (already gated by the RESV-05 WHERE), and per-checker daily-cap counted from a **durable** source (`contacts_cache` writes per `sender_id` today — NOT an in-memory counter; Pitfall 5).

**(D) RESV-01/D-05/D-07 + RESV-06/D-09 — probe + suspect rollback + confidence (in `_apply_results`, lines 232-307).** The `not_registered` branch (294-306) currently writes `tg_status='not_registered'`. Mirror the existing three-branch UPDATE style; add the confidence/source write and the suspect→pending decision:
```python
# CURRENT not_registered branch (294-306) — the finalization point
UPDATE contacts SET tg_status = 'not_registered', tg_checked_at = NOW(), updated_at = NOW()
WHERE id = :cid
```
- **Clean-probe checker:** write `tg_status='not_registered'`, `tg_confidence='high'`, `tg_resolved_by=:checker_id`, `tg_probe_state='clean'`.
- **Degraded checker (≥2 consecutive control misses):** write `tg_status='pending'` + clear `tg_checked_at` (rollback for re-check) — **never** `not_registered` (D-07/D-09 closes the root bug). **Keep the `registered` branch untouched** — throttle yields no false positives (Pitfall 3).
- The finalization rule needs **no campaign change**: campaigns only enqueue `tg_status='registered'` (`campaign_enqueue.py:163,237`, `campaigns.py:220`). The decision is `pending` vs `not_registered` in `_apply_results`; confidence cols are for analytics/dedup safety (RESV-06), not the enqueue gate.

**Claim-pattern landmine (preserve):** the `FOR UPDATE OF c SKIP LOCKED` + `tg_checked_at = NOW()` 5-min claim window (lines 101-164) is horizontally safe — a 2-3 checker pool lays directly onto it. Do **NOT** introduce a `'processing'` tg_status (CHECK constraint from migration 013 forbids it; the timestamp IS the claim). Documented at lines 92, 98-111.

---

### `app/services/checker.py` (Telethon service call, per-phone resolve)

**Analog:** itself (`_check_phones_locked`, 174-295) + reference shape `telegram.py:582-608`.

**(A) RESV-01/D-02 — importContacts fallback (lines 206-247).** Current resolve does `ResolvePhoneRequest` only; empty/`PhoneNotOccupiedError` → `not_registered` (lines 207-244):
```python
# CURRENT (checker.py:208-217)
from telethon.tl.functions.contacts import ResolvePhoneRequest
result = await client(ResolvePhoneRequest(phone=phone))
if result and result.users:
    is_registered = True; telegram_id = result.users[0].id
else:
    is_registered = False; telegram_id = None
```
Add fallback when `ResolvePhone` is empty / raises `PhoneNotOccupiedError`. Lift the call shape from the existing **legacy** `telegram.py:589` (which has NO cleanup):
```python
# Reference shape — app/services/telegram.py:589 (legacy check_contact, no cleanup)
res = await client(ImportContactsRequest(contacts=[
    InputPhoneContact(client_id=0, phone=phone, first_name="Check", last_name="")
]))
if res.users:
    is_registered = True; telegram_id = res.users[0].id
```
**MUST add address-book cleanup (D-02 — no code does this today):** `await client(DeleteContactsRequest(id=[res.users[0]]))` per import (or batch end), gated behind the burst-cap. Periodic janitor: `ResetSavedContactsRequest()` (heavier — recurring task, not per-batch). Pitfall 4: uncleaned imports shift the behavioural profile → faster throttle (this is how the original checker died).

**(B) RESV-01/D-05 — live probe path (cache landmine, lines 194-204).** `_check_phones_locked` consults `_lookup_cache` first (line 195) and returns cached results without hitting Telegram. A probe that hits cache **tests nothing** (Pitfall 1). The control-probe path MUST force a live `ResolvePhoneRequest`, bypassing `_lookup_cache`. Control set: 49 `phone,telegram_id` pairs in `.planning/phases/14-…/control-set-known-live.txt`; keep probe size small (3-5) so it doesn't blow the ≤30 cap. Do **NOT** mutate the control rows in `contacts` (they're `registered` Barter rows).

**Reuse, don't rebuild:** per-checker `asyncio.Lock` (`_get_lock`, 77-80), FloodWait handling (262-270), and the 2-3.5s polite delay (line 259) already exist. Don't add a parallel retry loop or lock manager (CLAUDE.md: don't touch send/pace intervals without discussion — note `CONTACT_CHECK_*` resolve pace is a SEPARATE knob set from `queue.py` send constants).

---

### `app/config.py` — env-knobs (RESV-02/D-10)

**Knob-location decision (planner must pick one, be consistent):** there are TWO established knob patterns in this repo:
1. **Module-level `os.environ.get`** — the existing `CONTACT_CHECK_*` knobs already use this (`contact_check_worker.py:43-44`):
```python
CONTACT_CHECK_BATCH_SIZE = int(os.environ.get("CONTACT_CHECK_BATCH_SIZE", "5"))
CONTACT_CHECK_POLL_INTERVAL = int(os.environ.get("CONTACT_CHECK_POLL_INTERVAL", "5"))
```
2. **`Settings` Field** in `config.py:67-89` (typed, documented, `validation_alias`):
```python
campaign_enqueue_batch_size: int = Field(
    default=500, validation_alias="CAMPAIGN_ENQUEUE_BATCH_SIZE",
    description="Max contacts processed per campaign per tick.")
```
**Recommendation:** the new D-10 knobs (`CONTACT_CHECK_BURST_CAP`, `CONTACT_CHECK_PACE_LOW/HIGH`, `CONTACT_CHECK_COOLDOWN_SECONDS`, `CONTACT_CHECK_DAILY_CAP`) follow the **`Settings` Field** pattern (config.py, after line 89) — typed + documented like the Phase-4 `campaign_enqueue_*` knobs. The two pre-existing module-level knobs can stay or be migrated; the planner decides. (RESEARCH.md anchors these at config.py:66-89.) Pace 2-3.5s already lives at `checker.py:259` as `random.uniform` — unify via knob or keep consistent.

---

### `migrations/034_contact_resolution_confidence.sql` (**NEW** — RESV-06/D-09)

**Analog:** `migrations/028_sender_restriction.sql` — the canonical idempotent ADD-COLUMN + drop/recreate-CHECK template. Mirror it exactly (Pitfall 6: non-idempotent migration → api fail-fast won't start):
```sql
-- 028 pattern to copy (lines 17-23):
ALTER TABLE senders ADD COLUMN IF NOT EXISTS restriction_status TEXT NOT NULL DEFAULT 'none';
ALTER TABLE senders DROP CONSTRAINT IF EXISTS senders_restriction_status_chk;
ALTER TABLE senders ADD CONSTRAINT senders_restriction_status_chk
    CHECK (restriction_status IN ('none', 'spam_limited', 'frozen'));
```
New file (next free number confirmed **034** — last is `033_campaign_max_new_dialogs.sql`):
```sql
-- migrations/034_contact_resolution_confidence.sql
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_confidence  TEXT NULL;   -- 'high'|'low'|NULL
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_resolved_by UUID NULL;   -- checker sender_id
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_probe_state TEXT NULL;   -- 'clean'|'suspect'|NULL
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_tg_confidence_chk;
ALTER TABLE contacts ADD CONSTRAINT contacts_tg_confidence_chk
    CHECK (tg_confidence IS NULL OR tg_confidence IN ('high','low'));
```
Auto-applied at api start (`app/database.py::_apply_migrations`). `contacts.source` (line 446) already exists but is import-provenance — keep it; `tg_resolved_by` is resolver-provenance (D-09).

---

### `app/models/__init__.py` `Contact` (427-457) — ORM mirror

**Analog:** how `Sender` mirrors its migration-028 cols (`__init__.py:96-99`):
```python
# Sender pattern (96-99) — migration col mirrored into ORM with server_default + comment
restriction_status = Column(String(20), nullable=False, server_default='none')
restricted_until = Column(DateTime(timezone=True), nullable=True)
```
Add the three new nullable cols to `Contact` (after line 453) so the create_all test-overlay schema includes them (the migration is the prod source of truth; the ORM mirror is for tests, which build via `Base.metadata.create_all`, not migrations):
```python
tg_confidence  = Column(String(10), nullable=True)
tg_resolved_by = Column(UUID(as_uuid=True), nullable=True)
tg_probe_state = Column(String(10), nullable=True)
```

---

### Docs (RESV-07)

**Target:** `/root/apps/aimly/tg-outreach/CLAUDE.md` §"Семантика checker'а (is_registered)" — **NOT** `/root/CLAUDE.md` (RESEARCH corrects CONTEXT here). The section currently asserts checker `sender-8428118140` was *healthy, not broken (2026-06-23)* — now **false** (shadow-banned). Correct the claim; **keep** the privacy-false-negative caveat (still TRUE — same prose also lives verbatim in `checker.py:15-42` docstring). Note `.planning/notes/checker-false-negatives.md` is already written (freeze diagnosis).

---

## Shared Patterns (cross-cutting — apply to multiple edits)

### Restriction mark + audit (D-04 / D-06) — Phase-10 infra VERBATIM
**Source:** `app/services/restriction_audit.py:48-87` (`record_restriction_event`, dual-mode helper) + `senders.restriction_status/restricted_until` (model 96-99, migration 028).
**Apply to:** the D-06 throttle-detect path in `contact_check_worker.py`. Do NOT invent a parallel "checker health" table — that's two sources of truth (RESEARCH "Don't Hand-Roll").
```python
# Transaction-neutral form (caller commits) — restriction_audit.py:48, dual-mode db= param
await record_restriction_event(
    sender_id=checker_id, event_type="spam_limited",
    source="antispam_signal",          # free-form, no CHECK (migration 030 line 32)
    restricted_until=cooldown_until,
    raw_text="control-probe: N consecutive misses",
    db=db,                              # passed → CALLER commits; None → self-commits
)
# + UPDATE senders SET restriction_status='spam_limited', restricted_until=:until WHERE id=:checker_id
```
**Landmines:**
- `activity_slice` is computed from `messages_log WHERE message_type='sent'` (audit.py:138-150). Checkers never send → slice is all-zeros. Harmless, but don't read it as "resolve activity".
- `record_restriction_event` uses `.one_or_none()` for the sender row (audit.py:112-119) — a missing sender skips the write rather than aborting the caller's TX. Same-TX guarantee: pass `db=db` so the event + status UPDATE land atomically.

### Checker recovery (Claude's discretion — D-04 open question)
**Source:** the sender path is SpamBot reconcile (`restriction_reconcile_interval_seconds`, config:85). A checker is NOT a sender — SpamBot won't reflect a contacts-API throttle, so **do not reuse** the sender reconcile. Recommended: fixed `restricted_until` cooldown; on the next tick after `restricted_until < NOW()`, run a fresh control-probe; if it passes, write a `cleared` event + `restriction_status='none'`. The RESV-05 WHERE (`restriction_status='none'`) is the gate that returns it to rotation.

### Idempotent migration (every DDL change)
**Source:** `migrations/028` / `030`. `ADD COLUMN IF NOT EXISTS`; CHECK via `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT`. Non-idempotent = api fail-fast (CLAUDE.md). Next number 034.

### Async-everywhere / test-overlay (all code edits)
**Source:** CLAUDE.md. All DB via `AsyncSessionLocal` + `async with`; no `time.sleep`/`print`/sync requests. Tests ONLY via overlay: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` — bare `pytest` drops prod schema (conftest guard). Extend `tests/test_contact_check_worker.py` (exists, Phase 2); new files `test_checker_probe.py`, `test_checker_cap.py`, `test_checker_pool.py` mirror its fixtures.

---

## No Analog Found

None. Every Phase-14 change extends an existing analog in place. The two genuinely-new artifacts (migration 034, the control-probe logic) both copy established patterns (migration 028 template; the existing `_tick` resolve loop + `check_phones` call).

| Item | Role | Data Flow | Note |
|---|---|---|---|
| control-probe interleave logic | worker logic (new function inside worker) | live resolve, bypass cache | No pre-existing "probe" code — but built from `checker.check_phones` + a live-only `ResolvePhoneRequest`; in-memory consecutive-miss dict mirrors `CheckerService._locks` (checker.py:75) singleton-state pattern. |

---

## Pre-plan DB verification (RESEARCH Open Questions — DB state can shift)

The planner MUST run these before/at plan time (state changes daily):
1. **699 Barter contaminated rows** (RESEARCH OQ#2): confirm current `tg_status` and decide rollback scope so the 49 known-live control numbers are NOT rolled to `pending`.
2. **Parked checkers** (`sender-7979031303`, `sender-8364639216`): confirm still `auth_status='restricted'`, `lifecycle_status='paused'`, `restriction_status='none'`. **Activation** (`auth_status='ok'`, `lifecycle_status='active'`) is a plan step, run ONLY after cap/probe/rotation are in code (D-03).
3. **Broken checker** `sender-8428118140`: confirm stays parked/`spam_limited` (real shadow-ban).

---

## Metadata

**Analog search scope:** `app/services/{contact_check_worker,checker,restriction_audit,telegram}.py`, `app/config.py`, `app/models/__init__.py`, `app/services/campaign_enqueue.py`, `app/routers/campaigns.py`, `migrations/028,030`, `tests/`.
**Files scanned:** 11 read + grep over migrations/ and tests/.
**File:line claims from RESEARCH.md:** all re-verified accurate against live code 2026-06-26.
**Pattern extraction date:** 2026-06-26
