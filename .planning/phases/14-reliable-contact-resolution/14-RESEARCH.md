# Phase 14: Reliable Contact Resolution - Research

**Researched:** 2026-06-26
**Domain:** Telethon contacts-API resolution reliability; checker pool health-probe, rotation, burst-cap; PostgreSQL claim-queue worker; confidence/source persistence
**Confidence:** HIGH (almost all findings are grounded in the actual codebase; Telethon API shapes verified against installed `telethon==1.42.0` + existing call-sites)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions (D-01..D-11 — do NOT relitigate)
- **D-01** Managed checker pool — dedicated `contact_check_worker` resolves contacts in batches via a pool of dedicated checker accounts with probe + cap + rotation. NOT lazy resolve-on-send. Gives "how many live" foreknowledge before a campaign starts. (RESV-02/03)
- **D-02** Resolve method = `ResolvePhoneRequest` with **fallback to `ImportContactsRequest`** when `ResolvePhone` is empty (mirrors the healthy-sender behaviour proven in `diag_resolve.py`). **Note:** `importContacts` writes into the checker's address book — after a run / periodically the checker address book must be cleaned (`contacts.DeleteContactsRequest` or equivalent) to avoid garbage accumulation and behavioural-profile drift.
- **D-03** Pool of **2–3 dedicated** checker accounts (`role='checker'`), not used for sending — throttle hits only resolution, never campaigns. **Status (2026-06-26): pool partially provisioned** — 2 proven-healthy resolvers converted `sender → checker` and **parked** (`auth_status='restricted'`, `lifecycle_status='paused'`, `restriction_status='none'`): `sender-7979031303` (resolved with both methods in `diag_resolve.py`) and `sender-8364639216` (48/49 in `known_live_probe.py`). Parked deliberately — the current cap-less worker would pick them up; **activate** (`auth_status='ok'`, `lifecycle_status='active'`) **only once cap/probe/rotation are in code**. Third `role='checker'` — the old broken `sender-8428118140` — stays parked (real shadow-ban). Conversion rollback: `role='sender', auth_status='ok', lifecycle_status='active'`.
- **D-04** Rotation (RESV-03) considers `restriction_status`, `restricted_until`, `lifecycle_status`, and rests accounts. Pool/rotation design must work at N=1 (single available checker → resolution pauses on cooldown, does not lie).
- **D-05** Degradation trigger = **≥2 consecutive control-set misses** (filters stochastic noise; calibration showed 48/49 live, a single miss can be noise). (RESV-01)
- **D-06** On detect — checker marked `restriction_status='spam_limited'`, event written to `sender_restriction_events`, taken out of rotation on cooldown.
- **D-07** Suspect batch — **all `not_registered` of the current batch roll back to `pending`** (not finalized), to be re-checked by another checker. `registered` (true positives) stay — a throttle produces no false positives. Batch ≤ ~30 + control probe each batch ⇒ "current batch" effectively = the window since the last clean probe.
- **D-08** Re-check 2110+699 contaminated + 14k pending via the **same pool's normal queue**, no separate backfill script. Priority — **mobiles first** (+79…, ~50% live) before landlines (+73/+74/+78, correctly absent). (RESV-04)
- **D-09** `not_registered` carries confidence/source — which checker, when. (RESV-06) Campaign treats `not_registered` as **final (skips the contact) only if it came from a clean-probe (high-confidence) checker**. Low confidence / suspect-checker result → re-check, never final. This closes the root bug (false "no" silently dropped leads).
- **D-10** Per-account burst-cap ≤ ~30 resolves/batch (under the empirical onset ~45–50), pace 2–3s between resolves, cooldown between batches, daily cap. All values as env-knobs (`CONTACT_CHECK_*` pattern), calibratable. (RESV-02)
- **D-11** `contact_check_worker` selection skips checkers with `restriction_status != 'none'` **OR** `lifecycle_status='paused'` (currently filters only `role='checker' AND auth_status='ok'` — the hole that let the broken checker keep lying). (RESV-05)

### Claude's Discretion
- Exact `CONTACT_CHECK_*` env-knob values (cap, pace, cooldown, daily limit) within D-10 bounds.
- Control-probe interleaving frequency within a batch and probe size (logic from D-05/D-07: probe each batch).
- Confidence/source storage schema (new `contacts_cache` / `contacts` columns vs JSONB) — D-09 impl; `source` already exists in `contacts` (NOT in `contacts_cache`).
- Checker cooldown/recovery mechanics (when to return to rotation after `spam_limited`).

### Deferred Ideas (OUT OF SCOPE)
- Onboarding new checker accounts — **done 2026-06-26** (2 senders → parked checkers). Planner only needs the **activation** step.
- Periodic checker address-book cleanup may become a recurring task — refine in plan.
- Observability/alerts on pool health + degradation rate; UI visibility of resolve/pool-health status — separate phases if needed.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RESV-01 | Health-probe on known-live control set; ≥2 consecutive misses → mark throttled + suspect batch | Gap 3; control set at `.planning/phases/14-…/control-set-known-live.txt` (49 numbers, phone→tg_id); `_tick()` batch loop in `contact_check_worker.py:95`; reuse `checker_service.check_phones` |
| RESV-02 | Per-account burst-cap + cooldown + pace + daily cap as `CONTACT_CHECK_*` env-knobs | Gap 6; `app/config.py` Settings pattern (lines 66-89); existing module-level knobs in `contact_check_worker.py:43-44` |
| RESV-03 | Pool of checkers + rotation respecting restriction_status/restricted_until/lifecycle_status | Gap 6; senders columns (model lines 91-99); `sender_restriction_events` + `record_restriction_event` helper |
| RESV-04 | Re-check contaminated (2110+699) + 14k pending via same queue, mobiles first | Gap 7; `_tick()` SELECT `ORDER BY c.created_at ASC` (line 144) — change ordering to mobile-first; D-09 finalization already implicit (campaign reads `tg_status='registered'`) |
| RESV-05 | Worker selection skips `restriction_status != 'none'` OR `lifecycle_status='paused'` | Gap 5; exact JOIN LATERAL at `contact_check_worker.py:132-139` |
| RESV-06 | `not_registered` carries confidence/source | Gap 4; `contacts` already has `source`; needs new confidence + checker-id + probe-state columns (migration 034) |
| RESV-07 | Fix docs: checker semantics section + freeze diagnosis in note | Gap below; section lives in **`/root/apps/aimly/tg-outreach/CLAUDE.md`** (NOT `/root/CLAUDE.md`), §"Семантика checker'а (is_registered)"; note already written |
</phase_requirements>

## Summary

The whole phase is a hardening of an already-working pipeline, not greenfield. Three pieces already exist and must be **reused, not rebuilt**: (1) `ContactCheckWorker._tick()` — a horizontally-safe claim-queue (`FOR UPDATE OF c SKIP LOCKED` + `tg_checked_at` 5-min claim window) that already groups pending contacts per checker and batches them; (2) `CheckerService.check_phones` / `check_usernames` — Telethon resolve with per-checker `asyncio.Lock`, FloodWait handling and a 2–3.5s polite delay already in place; (3) the Phase-10 restriction infrastructure (`senders.restriction_status`/`restricted_until`, `sender_restriction_events`, and the dual-mode `record_restriction_event` helper) — the exact mechanism D-06 needs for marking a throttled checker and logging the event.

The root bug is one line of SQL: the worker's `JOIN LATERAL` picks a checker on `role='checker' AND auth_status='ok'` only (`contact_check_worker.py:132-139`), so a semantically-correct `spam_limited`/`paused` flag wouldn't stop it — the operator had to nuke `auth_status` to silence the broken checker. RESV-05/D-11 is a minimal `AND` addition there. The other requirements bolt onto the existing loop: a control-probe interleaved into each `_tick`, a consecutive-miss counter per checker, an env-knob cap that shrinks the per-batch resolve count, a confidence/source write on the `not_registered` path, and a re-ordering of the claim SELECT to mobile-first.

**D-02's `importContacts` fallback is NOT yet in `checker.py`** — `_check_phones_locked` does `ResolvePhoneRequest` only and treats empty/`PhoneNotOccupiedError` as `not_registered`. A reference `ImportContactsRequest` implementation already exists in `app/services/telegram.py:582-608` (`check_contact`, legacy, no cleanup) — the planner can lift its call shape but MUST add address-book cleanup (`contacts.DeleteContactsRequest`) which no current code does.

**Primary recommendation:** Extend the existing `ContactCheckWorker._tick()` loop and `CheckerService` in place; reuse Phase-10 restriction infra verbatim for D-06; store confidence/source as new nullable columns on `contacts` (migration **034**); fix RESV-05 as a 2-line WHERE change. Do not add new background workers, new tables beyond the migration, or a separate backfill script.

## Standard Stack

This is a brownfield phase — the stack is fixed by the project. No new libraries.

### Core (already in repo)
| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| Telethon | 1.42.0 (verified `requirements.txt`) | MTProto contacts resolution | `functions.contacts.{ResolvePhoneRequest, ImportContactsRequest, ResolveUsernameRequest, DeleteContactsRequest}`; `types.{InputPhoneContact, InputUser}` |
| SQLAlchemy (async) | 2.0 | Claim-queue + writes | raw `text()` SQL throughout the worker/checker |
| PostgreSQL | 16 | `contacts`, `contacts_cache`, `senders`, `sender_restriction_events` | `FOR UPDATE … SKIP LOCKED` claim pattern already used |
| pydantic-settings | (in repo) | `CONTACT_CHECK_*` env-knobs | `app/config.py::Settings` |

**No new installs.** `npm`/`pip install` is N/A for this phase.

## Architecture Patterns

### Where each requirement lands (file:line)

| Req | File | Anchor | Change |
|-----|------|--------|--------|
| RESV-05/D-11 | `app/services/contact_check_worker.py` | `132-139` (JOIN LATERAL WHERE) | add `AND s.restriction_status = 'none' AND s.lifecycle_status <> 'paused'` |
| RESV-02/D-10 | `app/config.py` | `Settings` (after line 89) | add `CONTACT_CHECK_BURST_CAP`, `CONTACT_CHECK_PACE_*`, `CONTACT_CHECK_COOLDOWN_SECONDS`, `CONTACT_CHECK_DAILY_CAP` knobs |
| RESV-02/D-10 | `app/services/contact_check_worker.py` | `43-44`, `_tick` LIMIT (`149`) | cap batch ≤ ~30; enforce per-checker daily cap + cooldown via `sender_restriction_events`/`restricted_until` |
| RESV-01/D-05/D-07 | `app/services/contact_check_worker.py` | `_tick` after resolve, before `_apply_results` | interleave control-probe; track ≥2-consecutive-miss per checker; on detect → mark + suspect-rollback |
| RESV-01/D-02 | `app/services/checker.py` | `_check_phones_locked` `206-247` | add `ImportContactsRequest` fallback when `ResolvePhone` empty; add `DeleteContactsRequest` cleanup |
| RESV-06/D-09 | `migrations/034_*.sql` + `app/models/__init__.py` `Contact` (427-457) | new nullable cols | `tg_confidence`, `tg_resolved_by` (checker sender_id), `tg_probe_state` |
| RESV-06/D-09 | `app/services/contact_check_worker.py` | `_apply_results` `294-306` (not_registered branch) | write confidence/source; suspect → `pending` not `not_registered` |
| RESV-04/D-08 | `app/services/contact_check_worker.py` | `_tick` SELECT `ORDER BY` (144) | mobile-first ordering (`+79…` before others) |
| RESV-07 | `app/services/aimly/tg-outreach/CLAUDE.md` §"Семантика checker'а" + `.planning/notes/checker-false-negatives.md` | — | correct "checker healthy 2026-06-23" claim |

### Pattern 1: Reuse the existing claim-queue, do not build a new worker
**What:** `_tick()` already does `SELECT … FOR UPDATE OF c SKIP LOCKED` + a `tg_checked_at = NOW()` claim, then groups by `checker_id` and calls `check_phones`. It is documented safe at horizontal scale (`contact_check_worker.py:95-165`). A pool of 2–3 checkers lays directly onto this — the SELECT already returns the right checker per workspace.
**When to use:** all pool/rotation/cap work extends this method.
**Landmine:** `tg_status` CHECK constraint (migration 013) does NOT allow `'processing'` — the claim uses `tg_checked_at`, not a status change. Do not introduce a `processing` status; reuse the timestamp claim.

### Pattern 2: D-06 marking = Phase-10 restriction infra verbatim
**What:** to take a throttled checker out of rotation, do exactly what the send path does for `spam_limited`:
```python
# Source: app/services/restriction_audit.py:48 (dual-mode helper)
await record_restriction_event(
    sender_id=checker_id,
    event_type="spam_limited",
    source="antispam_signal",      # free-form; D-06 detect is an antispam signal
    restricted_until=cooldown_until,
    raw_text="control-probe: N consecutive misses",
    db=db,                          # transaction-neutral — caller commits
)
# + UPDATE senders SET restriction_status='spam_limited', restricted_until=:until, lifecycle_status=... WHERE id=:checker_id
```
The RESV-05 selection fix then automatically excludes this checker on the next tick. Cooldown recovery: the existing reconcile sweep (`restriction_reconcile_interval_seconds`, config line 85) clears `spam_limited` via SpamBot — but a checker is NOT a sender and SpamBot won't reflect a contacts-API throttle, so the planner must decide checker recovery separately (Claude's discretion). Simplest: a fixed `restricted_until` cooldown; the RESV-05 WHERE already gates on `restriction_status='none'`, so a `cleared` event + status reset after cooldown returns it to rotation.
**Landmine:** `record_restriction_event` computes `activity_slice` from `messages_log WHERE message_type='sent'` (audit.py:138). Checkers never send → slice will be all-zeros. Harmless, but don't read the slice as "resolve activity"; if resolve-volume context is wanted, put it in `raw_text` or `activity_slice` JSONB via a custom write.

### Pattern 3: Health-probe interleaving (D-05/D-07)
**What:** the control set is 49 `phone,telegram_id` pairs (`control-set-known-live.txt`). Each tick, after (or instead of part of) the real batch, the checker resolves a small sample of control numbers; a control number returning `not_registered` is a **miss**. Track misses **per checker, consecutive** (in-memory dict on the worker singleton, like `CheckerService._locks`, plus optionally a durable counter). ≥2 consecutive misses → degraded.
**Suspect-batch boundary (precise):** "current batch" per D-07 = the window of contacts resolved by that checker **since its last clean probe**. With probe-each-batch + batch ≤ ~30, the practical boundary is the just-resolved batch. On detect: the `not_registered` results from that batch → `pending` (rollback), `registered` kept. Because the worker writes results in `_apply_results` AFTER the resolve, the cleanest implementation is: probe FIRST (or alongside), and if degraded, write the batch's not_registered as `pending` instead of `not_registered`.
**Landmine:** probe numbers are themselves resolves and count toward the burst-cap. Keep probe size small (e.g. 3–5) so it doesn't blow the ≤30 cap. The control set is in the `contacts` table too (`tg_status='registered'`, Barter folder) — probing must NOT mutate those rows; resolve them via `check_phones` against the live API (bypass/ignore cache for probe, since the point is to test the live account, not the cache).
**Landmine — cache shortcut defeats the probe:** `_check_phones_locked` consults `contacts_cache` first (checker.py:194-204) and returns cached results without hitting Telegram. A probe that hits cache tests nothing. The probe path MUST force a live `ResolvePhoneRequest` (skip `_lookup_cache`).

### Pattern 4: D-02 importContacts fallback + address-book cleanup
**What:** when `ResolvePhoneRequest` returns empty / raises `PhoneNotOccupiedError`, retry via `ImportContactsRequest` (the healthy senders resolved this way in diagnosis). Reference shape already in repo:
```python
# Source: app/services/telegram.py:589 (existing legacy check_contact)
from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
from telethon.tl.types import InputPhoneContact
res = await client(ImportContactsRequest(contacts=[
    InputPhoneContact(client_id=0, phone=phone, first_name="c", last_name="")
]))
# res: contacts.ImportedContacts — .imported (list), .users (list), .retry_contacts
if res.users:
    user = res.users[0]; is_registered = True; telegram_id = user.id
    # CLEANUP (D-02 — NOT done anywhere today):
    await client(DeleteContactsRequest(id=[user]))   # accepts InputUser; user works
```
**`DeleteContactsRequest(id=[…])`** takes `List[InputUser]` and returns Bool (verified Telethon 1.42 API). Invoke cleanup per-imported-contact or batch at end of batch. Periodic safety net: `contacts.ResetSavedContactsRequest()` clears the whole imported address book (heavier — use only as a recurring janitor, not per-batch).
**Landmine:** the diagnosis (`checker-false-negatives.md` §2) found **no method asymmetry** for the broken checker — `importContacts` did NOT recover where `resolvePhone` was empty, because the account was throttled. So the fallback helps a **healthy** account near privacy edges, not a throttled one. Don't expect it to "fix" a degraded checker — that's what the probe + rollback are for.
**Landmine:** importContacts is more spam-sensitive than resolvePhone and mutates the address book; gate it behind the cap and clean up, or Telegram penalises the behavioural profile (the whole reason the original checker died).

### Anti-Patterns to Avoid
- **Building a second worker / a backfill script** — D-08 is explicit: re-check via the same queue. Rolling 14k+2809 to `pending` (already done for 2110) and letting `_tick` drain them is the design.
- **Adding a `'processing'` tg_status** — blocked by the CHECK constraint; use the existing `tg_checked_at` claim window.
- **Caching the probe** — defeats the health check (see Pattern 3).
- **Reading `is_registered=false` as "no Telegram account"** — privacy false-negatives exist (checker.py docstring); confidence/source (D-09) is precisely to stop downstream code trusting it blindly.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Per-checker concurrency | new lock manager | `CheckerService._get_lock(slug)` (checker.py:77) | already one `asyncio.Lock` per checker_slug |
| Restriction state + audit | new table / ad-hoc UPDATE | `record_restriction_event` + `senders.restriction_status/restricted_until` (Phase 10) | dual-mode helper, idempotent forward-shift gate, append-only log |
| Claim/race safety for pool | distributed lock / new status | `FOR UPDATE OF c SKIP LOCKED` + `tg_checked_at` window (worker:101-111) | proven horizontally safe |
| FloodWait + polite pacing | new retry loop | `CheckerService` already handles `FloodWaitError` + 2–3.5s delay | reusing avoids drift (CLAUDE.md: don't touch intervals without discussion) |
| Phone resolve shapes | new Telethon wrapper | `_check_phones_locked` + `telegram.py:589` reference | call shapes already correct for 1.42 |

**Key insight:** Phase 10 already built every piece of restriction plumbing this phase needs for D-04/D-06. Inventing parallel "checker health" state would create two sources of truth.

## Runtime State Inventory

This phase touches stored data + a manual DB pre-step (Часть 1) already executed. Categories:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `contacts` 14489 pending / 53 registered / 0 not_registered (post-cleanup 2026-06-26). 2216 false `contacts_cache` rows already deleted. **+699 Barter contacts** still to roll into pending (D-08) — verify their current `tg_status` before planning the re-check (note says "registered kept", but the 699 contaminated need explicit handling). | Data migration: ensure 699 contaminated → `pending`; confirm count in plan. Code: confidence/source backfill is NOT needed (new cols nullable). |
| Live service config | None — checker pool is DB-state (`senders` rows), not external service config. The 2 parked checkers (`sender-7979031303`, `sender-8364639216`) live in `senders`. | Activation step (D-03): `UPDATE senders SET auth_status='ok', lifecycle_status='active' …` **only after** cap/probe/rotation are in code. |
| OS-registered state | None — worker runs in the api container lifespan (`app/main.py`), no cron/systemd for resolution. | None. |
| Secrets/env vars | New `CONTACT_CHECK_*` env vars (cap/pace/cooldown/daily). Code-only defaults via `app/config.py` (no secret). | Add to `.env` / docker-compose env if overriding defaults; defaults make it work without env changes. |
| Build artifacts | None — pure Python edits + raw SQL migration, auto-applied at api start. | `docker compose up -d --build api` picks up migration 034. |

**Canonical question — what runtime state still has old data after a code merge?** The `contacts_cache` rows already deleted; remaining work is forward-only (new resolves write confidence/source; old `pending` re-drains). The only stateful pre-req is the **699 Barter contaminated rows** — confirm they are `pending` in the plan.

## Common Pitfalls

### Pitfall 1: Probe hits cache, tests nothing
**What goes wrong:** control-probe returns cached `is_registered` and never exercises the live account; a throttled checker passes the probe.
**Why:** `_check_phones_locked` does `_lookup_cache` first (checker.py:194).
**Avoid:** dedicated probe path that calls `ResolvePhoneRequest` directly, bypassing `_lookup_cache`.
**Warning sign:** probe always passes even as live hit-rate collapses.

### Pitfall 2: Marking checker via `auth_status` again
**What goes wrong:** re-introducing the hack of nuking `auth_status` to stop a checker (what Часть 1 had to do manually).
**Why:** the RESV-05 fix is the proper gate; `auth_status` means "session valid", orthogonal to restriction (migration 028 comment).
**Avoid:** mark via `restriction_status='spam_limited'` + `restricted_until` + event; keep `auth_status='ok'`. The selection fix makes this sufficient.

### Pitfall 3: Suspect rollback overwrites true positives
**What goes wrong:** rolling the whole batch to `pending` and losing `registered` rows.
**Why:** D-07 — only `not_registered` of the suspect batch rolls back; throttle yields no false positives.
**Avoid:** in `_apply_results`, when checker is flagged degraded, write the `not_registered` branch as `tg_status='pending'` (clear `tg_checked_at`), leave the `registered` branch as-is.

### Pitfall 4: importContacts pollutes the address book → profile drift
**What goes wrong:** D-02 fallback imports thousands of contacts, never cleaned → the checker's behavioural profile shifts → faster throttle.
**Why:** `ImportContactsRequest` persists contacts; no cleanup exists in the codebase.
**Avoid:** `DeleteContactsRequest(id=[user])` per import (or batch end) + periodic `ResetSavedContactsRequest` janitor; gate importContacts behind the burst-cap.

### Pitfall 5: Cap/cooldown not enforced because state is in-memory only
**What goes wrong:** worker restart resets in-memory daily-count/cooldown; checker over-resolves after a deploy.
**Why:** the api container restarts on every `up -d --build`.
**Avoid:** derive daily-cap from a durable source — count today's resolves from `contacts_cache.updated_at` per `sender_id`, or count `sender_restriction_events` — rather than a process-local counter. Cooldown via `senders.restricted_until` is already durable (use it).

### Pitfall 6: Migration not idempotent → api won't start
**What goes wrong:** `ALTER TABLE contacts ADD COLUMN …` without `IF NOT EXISTS` fails the auto-applier on re-run; api fail-fasts.
**Why:** CLAUDE.md migration rules — applier re-runs on drift, file must be idempotent.
**Avoid:** `ADD COLUMN IF NOT EXISTS`; CHECK constraints via `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` (mirror migration 028 lines 20-23). Next free number is **034**.

## Code Examples

### RESV-05/D-11 — the one-line root-cause fix
```sql
-- app/services/contact_check_worker.py:132-139 — JOIN LATERAL, add the two ANDs
SELECT id, slug, session_string, proxy
FROM senders
WHERE workspace_id = c.workspace_id
  AND role = 'checker'
  AND auth_status = 'ok'
  AND restriction_status = 'none'          -- NEW (D-11)
  AND lifecycle_status <> 'paused'         -- NEW (D-11)
LIMIT 1
```

### RESV-04/D-08 — mobile-first claim ordering
```sql
-- app/services/contact_check_worker.py:144 — replace ORDER BY c.created_at ASC
ORDER BY (c.phone LIKE '+79%') DESC,   -- mobiles (+79…) first, ~50% live
         c.created_at ASC
```

### RESV-02/D-10 — env-knobs (app/config.py, after line 89)
```python
contact_check_burst_cap: int = Field(default=30, validation_alias="CONTACT_CHECK_BURST_CAP")
contact_check_pace_low: float = Field(default=2.0, validation_alias="CONTACT_CHECK_PACE_LOW")
contact_check_pace_high: float = Field(default=3.0, validation_alias="CONTACT_CHECK_PACE_HIGH")
contact_check_cooldown_seconds: int = Field(default=900, validation_alias="CONTACT_CHECK_COOLDOWN_SECONDS")
contact_check_daily_cap: int = Field(default=400, validation_alias="CONTACT_CHECK_DAILY_CAP")
# NB: pace 2–3.5s already lives in checker.py:259 as random.uniform — keep consistent / unify via knob.
```

### RESV-06/D-09 — migration 034 (idempotent, mirrors 028)
```sql
-- migrations/034_contact_resolution_confidence.sql
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_confidence TEXT NULL;        -- 'high'|'low'|NULL
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_resolved_by UUID NULL;       -- checker sender_id
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tg_probe_state TEXT NULL;       -- 'clean'|'suspect'|NULL

ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_tg_confidence_chk;
ALTER TABLE contacts ADD CONSTRAINT contacts_tg_confidence_chk
    CHECK (tg_confidence IS NULL OR tg_confidence IN ('high','low'));
-- Mirror the new nullable cols in app/models/__init__.py Contact (create_all path).
```
*(`contacts.source` already exists (line 446) but is import-provenance, not resolver-provenance — keep it; add `tg_resolved_by` for the checker identity per D-09.)*

### D-09 finalization read point — already exists, no campaign change needed
```sql
-- app/services/campaign_enqueue.py:163 & 237, app/routers/campaigns.py:220
AND c.tg_status = 'registered'   -- campaigns ONLY enqueue registered contacts
```
Because campaigns select `registered` (never `not_registered`), the D-09 rule is enforced upstream: a suspect-checker result must be written as `pending` (re-checkable) **not** `not_registered`. There is no "campaign reads confidence" code to add — the finalization decision is made in the worker (`_apply_results`) by choosing `pending` vs `not_registered`. The confidence/source columns are for analytics/dedup safety (RESV-06), not for the enqueue gate.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Single checker, no probe, cap-less | Pool + health-probe + cap + rotation | This phase | stops silent 15–20× under-count |
| `is_registered=false` trusted as final | confidence/source; suspect → `pending` | This phase | false-negatives re-checked, not dropped |
| Stop a bad checker by nuking `auth_status` | `restriction_status`/`lifecycle_status` gate in selection | RESV-05 | semantic flags actually stop the worker |

**Deprecated/outdated:**
- The `/root/apps/aimly/tg-outreach/CLAUDE.md` §"Семантика checker'а" claim "checker `sender-8428118140` … healthy, not broken (2026-06-23)" is now **false** — it was shadow-banned. RESV-07 fixes it. (The privacy-false-negative caveat in the same section remains TRUE and should stay.)

## Open Questions

1. **Checker cooldown/recovery trigger** (Claude's discretion per CONTEXT)
   - Known: `restricted_until` is durable; RESV-05 WHERE gates on `restriction_status='none'`. SpamBot reconcile (the sender recovery path) won't reflect a contacts-API throttle.
   - Unclear: who flips `spam_limited → none` for a checker and when.
   - Recommendation: fixed cooldown via `restricted_until`; on the next tick after `restricted_until < NOW()`, run a fresh control-probe; if it passes, write a `cleared` event + `restriction_status='none'`. Don't reuse the sender SpamBot reconcile for checkers.

2. **699 Barter contaminated rows current state**
   - Known: 2110 already → pending; note mentions +699 to re-check.
   - Unclear: are the 699 currently `registered` (the control set is drawn FROM Barter `registered`) or `not_registered`?
   - Recommendation: the plan must run a count query first and explicitly decide rollback scope so the 49 control numbers (known-live) are NOT rolled back.

3. **Daily-cap durability source**
   - Recommendation: count `contacts_cache` writes per `sender_id` since `date_trunc('day', now())` rather than an in-memory counter (survives container restart — Pitfall 5).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Telethon | resolve calls | ✓ | 1.42.0 | — |
| PostgreSQL 16 | all state | ✓ (prod `outreach-platform-db`) | 16 | — |
| 2 parked checker accounts | pool (D-03) | ✓ (parked in `senders`) | — | activate when code ready |
| Live Telegram contacts-API | probe + resolve | ✓ (runtime) | — | none — control-probe IS the availability check |

**Missing dependencies with no fallback:** none — pool is provisioned (parked), infra exists.
**Missing with fallback:** none.

## Validation Architecture

`.planning/config.json` not inspected for the key; treating Nyquist as enabled (default). Tests run **only** via test-overlay (CLAUDE.md): `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. Never bare `docker compose run --rm api pytest` (conftest guard drops prod schema).

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (async, in repo; suite GREEN at 756 as of Phase 13) |
| Config file | `tests/conftest.py` (`_setup_database` on ephemeral `outreach_test` / `db-test` tmpfs) |
| Quick run | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_contact_check_worker.py -x` |
| Full suite | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

### Phase Requirements → Test Map
| Req | Behavior | Test Type | Command | File Exists? |
|-----|----------|-----------|---------|-------------|
| RESV-05 | selection excludes `spam_limited`/`paused` checker | unit (SQL) | `pytest tests/test_contact_check_worker.py::test_selection_skips_restricted -x` | ❌ Wave 0 |
| RESV-01 | ≥2 consecutive control misses → flag + suspect rollback | unit | `pytest tests/test_checker_probe.py::test_two_misses_flags -x` | ❌ Wave 0 |
| RESV-01/D-07 | suspect batch: not_registered→pending, registered kept | unit | `pytest tests/test_checker_probe.py::test_suspect_rollback_keeps_registered -x` | ❌ Wave 0 |
| RESV-02 | burst-cap limits batch size; daily-cap durable | unit | `pytest tests/test_checker_cap.py::test_burst_cap -x` | ❌ Wave 0 |
| RESV-03/D-04 | rotation picks eligible checker; pauses at N=1 on cooldown | integration | `pytest tests/test_checker_pool.py::test_rotation_n1_pauses -x` | ❌ Wave 0 |
| RESV-06/D-09 | not_registered carries confidence/source; suspect→pending | unit | `pytest tests/test_contact_check_worker.py::test_confidence_written -x` | ❌ Wave 0 |
| RESV-02/D-02 | importContacts fallback + DeleteContacts cleanup invoked | unit (mock client) | `pytest tests/test_checker.py::test_import_fallback_and_cleanup -x` | ❌ Wave 0 |
| RESV-04 | mobile-first ordering in claim SELECT | unit | `pytest tests/test_contact_check_worker.py::test_mobile_first_order -x` | ❌ Wave 0 |
| RESV-04 (live) | control set actually resolves on a healthy checker | manual smoke | run probe against `control-set-known-live.txt` on `sender-8364639216` | n/a (live) |

### Sampling Rate
- **Per task commit:** `pytest tests/test_contact_check_worker.py tests/test_checker*.py -x`
- **Per wave merge:** full suite
- **Phase gate:** full suite green + one live control-probe smoke before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_contact_check_worker.py` — selection-skip, mobile-first, confidence (may exist from Phase 2 — check; extend if so)
- [ ] `tests/test_checker_probe.py` — probe miss-counting + suspect rollback
- [ ] `tests/test_checker_cap.py` — burst/daily cap, durable across restart
- [ ] `tests/test_checker_pool.py` — rotation + N=1 cooldown
- [ ] Telethon client mock fixture for `ResolvePhone`/`ImportContacts`/`DeleteContacts` (check `tests/conftest.py` for existing checker mock)

*(Live control-probe is a manual smoke, not automated — it exercises real Telegram throttle which can't be reproduced in CI.)*

## Project Constraints (from CLAUDE.md)

- **Async everywhere** — all DB via async/await + AsyncSession; no `time.sleep()`, no sync `requests`, no `print()`.
- **Migrations** — raw idempotent SQL `NNN_short_name.sql` (next: **034**), auto-applied at api start; fail-fast if non-idempotent. Never Alembic.
- **Queue intervals** — do NOT change the empirical send rate-limits (4/20/150) or send-pace constants. (This phase touches RESOLVE pace `CONTACT_CHECK_*`, a separate knob set — confirm it doesn't touch `queue.py` send constants.)
- **Tests** — ONLY via test-overlay; bare `pytest` drops prod schema.
- **Security** — sessions encrypted; API_KEY/sessions never in logs (don't log decrypted session strings in the probe path).
- **Process** — explain-before-coding in Russian for non-trivial changes (project rule for the implementer/human loop).
- **Deploy** — `docker compose up -d --build api` (and `listener`); restart alone does NOT pick up code changes.

## Sources

### Primary (HIGH confidence)
- Codebase (read directly): `app/services/contact_check_worker.py`, `app/services/checker.py`, `app/services/telegram.py:479-608`, `app/services/restriction_audit.py:48-167`, `app/models/__init__.py`, `app/config.py`, `app/routers/campaigns.py:200-235`, `app/services/campaign_enqueue.py`, `migrations/028_…`, `migrations/030_…`, `requirements.txt` (telethon 1.42.0)
- `.planning/phases/14-…/14-CONTEXT.md` (D-01..D-11), `.planning/notes/checker-false-negatives.md`, `.planning/REQUIREMENTS.md:164-170`, `.planning/ROADMAP.md:357-381`, `.planning/STATE.md`, `control-set-known-live.txt`

### Secondary (MEDIUM confidence)
- WebSearch (Telethon API) — `DeleteContactsRequest(id=List[InputUser])→Bool`, `ImportContactsRequest(contacts=List[InputContact])→contacts.ImportedContacts (.imported/.users/.retry_contacts)`. Cross-verified against existing repo call-sites (telegram.py uses the same shapes).

### Tertiary (LOW confidence)
- None relied upon.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — brownfield, versions read from `requirements.txt`.
- Architecture / file:line anchors: HIGH — read every target file.
- Telethon contacts-API shapes: HIGH — matched to installed version + existing call-sites; the `tl.telethon.dev` doc page returned corrupted content, but shapes are confirmed by repo usage.
- Pitfalls: HIGH — derived from the actual diagnosis note + code.
- Checker recovery mechanics: MEDIUM — flagged as open question / Claude's discretion.

**Research date:** 2026-06-26
**Valid until:** 2026-07-26 (stable brownfield; re-verify the 699-row state and parked-checker status at plan time — DB state can shift)

## Sources (web)

- [DeleteContactsRequest - Telethon API](https://tl.telethon.dev/methods/contacts/delete_contacts.html)
- [ImportContactsRequest - Telethon API](https://tl.telethon.dev/methods/contacts/import_contacts.html)
