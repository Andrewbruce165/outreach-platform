---
slug: tg-accounts-no-analytics
status: resolved
trigger: "на странице TG Accounts нет накакой аналитики и статусов"
created: 2026-06-19
updated: 2026-06-19
---

# TG Accounts — No Analytics / Stats — Debug Session

## Symptoms

- **Page:** "Telegram accounts" (TG Accounts) on prod `aimly.agsventurelab.com`.
- **What user reports missing (multi-select):**
  - "TODAY" column empty — shows "— / 150" for every account.
  - "LAST USED" perceived wrong / empty.
  - Top cards / statuses perceived as fake / not reflecting real state.
  - No charts / send-history analytics on the page at all.
- **Environment:** Prod. DevTools (Network/Console) NOT checked by user.
- **Screenshot shows:** 3 accounts (ru-account-1 +79587869196 active/Sender 2d ago,
  ru-account-2 +79587859646 active/Sender "—", us-account-1 +16018728956 active/Checker 2d ago).
  Cards: Connected 3, Active 3, Warm-up 0, Paused 0, Errors 0. Limits 4·20·150 each. Proxy Direct.

## Initial Evidence (gathered by orchestrator before delegation)

### 1. Screenshot data is REAL, not mock — single clean workspace

Live `senders` table (DB `outreach_platform`):

```
name         | phone        | slug              | role    | last_used_at
ru-account-1 | +79587869196 | sender-8218483045 | sender  | 2026-06-17 12:42:03+00
ru-account-2 | +79587859646 | sender-8526195634 | sender  | (NULL)
us-account-1 | +16018728956 | sender-8428118140 | checker | 2026-06-17 12:42:10+00
```

All in workspace `bb96789d-ca84-4880-9568-90867aae6acd` (andrew.asachuk@gmail.com).
**Only ONE workspace now** — the May `ui-data-missing` 4-duplicate race has been cleaned up.
`name`/`phone` match the screenshot exactly; the UI is rendering real rows.

### 2. ROOT-CAUSE CANDIDATE (high confidence): `/senders` returns no "sent today" field

`GET /api/v1/senders` → `list_senders` → `_sender_to_response` (app/routers/senders.py:78-101)
builds `SenderResponse`. The schema (app/schemas/__init__.py:121-146) fields are:

```
id, slug, name, phone, status, auth_status, lifecycle_status,
rate_limits{per_minute,per_hour,per_day}, role, proxy, last_used_at, created_at
```

**There is NO per-sender "messages sent today" / daily usage counter anywhere in the
response.** The UI "TODAY · CEILING" column is `{sent_today} / {rate_per_day}`; the
denominator (150) comes from `rate_limits.per_day`, but the numerator has no backend
source → UI renders "—" for every account. This is a **never-implemented field**, not a
regression. `_sender_to_response` does zero aggregation over `messages_log`.

### 3. The underlying data EXISTS in messages_log

```
slug              | sent_today | sent_total
sender-8218483045 |     0      |    20      (last_used 06-17, 14 of them on 06-17)
sender-8428118140 |     0      |     1
sender-8526195634 |     0      |     0
```

`messages_log` is populated by queue worker (app/services/queue.py:643 "Write to messages_log").
So a `sent_today` aggregation is feasible — the data is there, just never surfaced by the API.
(Note: sent_today=0 for all today 06-19 because last activity was 06-17 — so even after the
fix, TODAY would legitimately show "0 / 150" today, not a number.)

### 4. LAST USED is actually correct, not a bug

`last_used_at`: ru-account-1=06-17, us-account-1=06-17 (both "2d ago" vs today 06-19 ✓),
ru-account-2=NULL ("—" because it was genuinely never used). User perception of "wrong/empty"
likely stems from the empty TODAY column making the whole page feel dead.

### 5. Cards/statuses are real

Connected/Active/Warm-up/Paused/Errors counts are derived frontend-side from the senders
list by `status`. `status` is correctly derived (auth_status='ok' → lifecycle_status). Not fake.

## Current Focus

- hypothesis: CONFIRMED feature gap (resolved). TODAY column numerator hardcoded in frontend
  (`accounts.tsx:313-314`), and `SenderResponse` has no per-sender "sent today" field.
- reasoning_checkpoint: **RESOLVED by user (2026-06-19).**
  - Scope = **Вариант A (minimal)**: only the TODAY column. Add `sent_today: int` to
    `SenderResponse`, surface on `GET /api/v1/senders`. Larger per-account analytics/history
    widget (Вариант B) is **out of scope**.
  - Numerator = **rolling 24h window**, identical definition to the rate-limiter daily cap
    (`app/services/queue.py:450-466`): `COUNT(*) FROM message_queue WHERE sender_id=:sid
    AND status='sent' AND finished_at >= now()-interval '24 hours'`. NOT calendar-day
    `created_at::date`, NOT messages_log — numerator must match denominator window
    (no "151/150" desync).
- next_action: present plan (RU) for confirmation → implement backend + frontend → tests via
  test-overlay → specialist review (python + typescript) → mark resolved. No push/deploy
  unless user asks.

## Evidence

- timestamp: 2026-06-19
  observation: `SenderResponse` schema (app/schemas/__init__.py:121-146) has no daily-sent /
  usage-today field; `_sender_to_response` (senders.py:78-101) does no messages_log aggregation.
  → UI "TODAY" column numerator has no backend source → "—/150" for all accounts.

- timestamp: 2026-06-19
  observation: screenshot rows match live DB senders exactly (name+phone), single workspace
  bb96789d…, data is real not mock.

- timestamp: 2026-06-19
  observation: messages_log holds real counts (ru-account-1: 20 total, last_used 06-17);
  sent_today=0 across the board today (06-19) because last activity was 06-17.

- timestamp: 2026-06-19
  observation: last_used_at values are correct and explain the "—"/"2d ago" display; not a bug.

- timestamp: 2026-06-19 (debugger, frontend verification)
  checked: frontend `src/routes/_authenticated/accounts.tsx` (the TG Accounts page).
  found: TODAY column is HARDCODED. Line 313 renders `<span>— / {dailyLimit}</span>` and
  line 314 `<CorridorBar value={0} limit={dailyLimit} />`. `dailyLimit = sender.rate_limits.per_day`
  (line 259). The page's ONLY data source is `useQuery(["senders"]) → GET /api/v1/senders`
  (line 34-38). It calls NO analytics endpoint. There is no `sent_today` field read anywhere.
  implication: the gap is on BOTH sides — backend has no field AND frontend doesn't even try
  to read one. A pure backend fix is invisible until the frontend (separate repo) is also
  updated to render `sender.sent_today` instead of the hardcoded `—`/`value={0}`.

- timestamp: 2026-06-19 (debugger, analytics endpoint audit)
  checked: `app/routers/analytics.py` + openapi spec advertises `/api/v1/analytics/senders/{sender_id}`.
  found: the per-sender analytics endpoint EXISTS and returns `AnalyticsCards`
  {sent, replied{conversation_count,message_count}, leads, finishes} — but it is **all-time
  only** (D-14 explicitly: "No ?from=&to= query params") and has NO "today"/"daily" figure.
  So it cannot feed the TODAY column either. The accounts page does not call it anyway.
  implication: there is no existing endpoint or field that yields "messages sent today" per
  sender. The TODAY counter requires a NEW field/aggregation.

- timestamp: 2026-06-19 (debugger, day-boundary / МСК resolution — IMPORTANT)
  checked: rate-limiter daily-cap logic in `app/services/queue.py:450-466`.
  found: the daily cap that `rate_per_day` (the 150 ceiling) is actually enforced against is a
  **rolling 24-hour window**, NOT a calendar day at any timezone:
      SELECT COUNT(*) FROM message_queue
      WHERE sender_id=:sid AND status='sent' AND finished_at >= (now - 24h)
  Source-of-truth for "sent in the cap window" is `message_queue` (status='sent', finished_at),
  NOT `messages_log` and NOT a `created_at::date = CURRENT_DATE` calendar comparison.
  Also note: working-hours window is now PER-CAMPAIGN timezone (`c.timezone`, queue.py:158-198),
  the global hardcoded 09-20 МСК is gone. So there is no single workspace "day boundary".
  implication: to make the TODAY numerator CONSISTENT with the ceiling it's compared against
  (`{sent_today}/{rate_per_day}`), `sent_today` MUST reuse the rolling-24h definition over
  message_queue, NOT a calendar-day count over messages_log.

- timestamp: 2026-06-19 (session-manager, checkpoint resolution + code anchors)
  checked: `list_senders` (senders.py:210-226 — `select(Sender).where(workspace_id).order_by(name)`),
  `_sender_to_response` (senders.py:78-101), `SenderResponse` (schemas/__init__.py:121-139),
  cap query (queue.py:450-460).
  found: list endpoint is a single workspace-scoped `select(Sender)` ordered by name; no aggregation.
  decision: implement aggregate `sent_today` per sender in ONE GROUP BY join against
  `message_queue` (no N+1), pass a `sent_today` map into `_sender_to_response`.

## Root Cause

CONFIRMED feature gap (not a regression, not stochastic):
1. `SenderResponse` (app/schemas/__init__.py:121-139) and `_sender_to_response`
   (app/routers/senders.py:78-101) expose NO per-sender "sent today / daily usage" field and
   do zero aggregation — the TODAY column numerator has no backend source.
2. The frontend `accounts.tsx:313-314` HARDCODES the numerator to `—` / `value={0}`; it never
   reads or requests such a field. So both sides must change.
3. The existing per-sender analytics endpoint is all-time only (D-14) and unused by this page —
   it cannot supply a "today" number.
Secondary perceptions (LAST USED wrong, cards fake) are NOT bugs — the dead TODAY column makes
the whole page feel inert.

## Fix Required

Scope = Вариант A (minimal), numerator = rolling-24h (matches rate-limiter cap window).

**Backend** (`/root/apps/aimly/tg-outreach`):
1. `app/schemas/__init__.py` — add `sent_today: int = 0` to `SenderResponse`.
2. `app/routers/senders.py::list_senders` — add a single aggregate query:
   ```sql
   SELECT sender_id, COUNT(*) AS sent_today
     FROM message_queue
    WHERE sender_id = ANY(:sender_ids)
      AND status = 'sent'
      AND finished_at >= now() - interval '24 hours'
    GROUP BY sender_id
   ```
   (scoped to the workspace's sender ids already fetched; no per-row N+1). Build a
   `{sender_id: count}` dict and pass it into `_sender_to_response(sender, sent_today=...)`.
3. `_sender_to_response` — accept `sent_today: int = 0` param, set on `SenderResponse`.
   Single-sender GET (`get_sender`) and create/update responses default to 0 (or compute) —
   keep minimal: default 0 for non-list paths (TODAY column only renders on the list page).
4. No DB migration (read-only aggregation).
5. `lovable-handoff/openapi.json` — regenerate so `sent_today` appears in `SenderResponse`.

**Frontend** (`/root/apps/aimly/aimly-tg-outreach`, separate repo):
6. `src/routes/_authenticated/accounts.tsx:313-314` — replace hardcoded `— / {dailyLimit}`
   with `{sender.sent_today} / {dailyLimit}` and `CorridorBar value={sender.sent_today}`.
7. Update the sender TS type to include `sent_today: number`.

**Verification:** tests via test-overlay
(`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`),
then python specialist review (backend) + typescript specialist review (frontend).
Atomic commits per CLAUDE.md; NO push/deploy unless user asks.

## Resolution

- root_cause: |
    Feature gap (not a regression). `SenderResponse` (app/schemas/__init__.py) and
    `_sender_to_response` (app/routers/senders.py) exposed no per-sender "sent today"
    field and did zero aggregation, so the TG Accounts TODAY column had no backend
    numerator → rendered "— / 150" for every account. The frontend `accounts.tsx`
    also hardcoded the numerator to `—` / `value={0}` and never requested such a
    field. The data existed in `message_queue` but was never surfaced. Both sides
    needed changing. (Secondary perceptions — LAST USED wrong, cards fake — were
    NOT bugs; the inert TODAY column made the whole page feel dead.)

- fix: |
    Вариант A (minimal), numerator = rolling-24h (matches the rate-limiter cap window).
    BACKEND (/root/apps/aimly/tg-outreach):
      - Added `sent_today: int = 0` to `SenderResponse`.
      - `list_senders` runs ONE GROUP BY aggregate over `message_queue`
        (status='sent' AND finished_at >= now()-interval '24 hours') → {sender_id: count}
        map (no N+1), scoped to the workspace's senders. Definition is identical to the
        rate-limiter daily cap (queue.py:450-466), so {sent_today}/{rate_per_day} never
        desyncs.
      - `_sender_to_response` gained a `sent_today: int = 0` param (default 0 on
        single-GET / create / update / pause / resume / assign-proxy paths).
      - Regenerated lovable-handoff/openapi.json. No DB migration (read-only).
    FRONTEND (/root/apps/aimly/aimly-tg-outreach, separate repo):
      - accounts.tsx TODAY column renders `{sender.sent_today ?? 0} / {dailyLimit}`
        and `CorridorBar value={sender.sent_today ?? 0}`.
      - Added optional `sent_today?: number` to generated SenderResponse TS type
        (src/types/api.ts) + mirrored in src/types-openapi.json.

- verification: |
    Backend: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api
    pytest tests/test_senders.py -q` → 21 passed (incl. 2 new: sent_today present;
    counts only status='sent' finished within 24h, excludes >24h / non-sent / NULL
    finished_at rows → expected 2).
    Frontend: `tsc --noEmit` → 0 errors.
    Specialist review: python (security-auditor) → LOOKS_GOOD (window matches cap, no
    N+1, workspace-isolated via sender_id=ANY scoped to workspace senders, parameter
    binding — no injection). typescript review → LOOKS_GOOD (`?? 0` correct guard for
    optional field; type addition matches openapi-typescript output; CorridorBar guards
    limit>0 and clamps).
    Commits: backend `e3241fe` on branch fix/senders-sent-today
    (Andrewbruce165/outreach-platform); frontend `fa8370e` on branch
    fix/accounts-sent-today (AGS-Venture-Lab/aimly-tg-outreach). NOT pushed/deployed.

- files_changed:
    backend:
      - app/schemas/__init__.py (SenderResponse.sent_today)
      - app/routers/senders.py (list_senders aggregate + _sender_to_response param)
      - lovable-handoff/openapi.json (regenerated)
      - tests/test_senders.py (2 new tests + _insert_queue_item helper)
    frontend:
      - src/routes/_authenticated/accounts.tsx (TODAY column render)
      - src/types/api.ts (sent_today optional field)
      - src/types-openapi.json (sent_today property mirror)

- note: |
    sent_today will legitimately show 0 for all three accounts today (last activity
    was 06-17; rolling 24h window is empty as of 06-19) — this is correct behaviour,
    not a remaining bug. Numbers appear once accounts send within the trailing 24h.

- deploy_when_ready: |
    Backend (this repo): merge fix/senders-sent-today → main, then on server
      git pull && docker compose up -d --build api
    Frontend (separate repo): merge fix/accounts-sent-today → main, deploy via Cloudflare
    (wrangler) per the frontend repo's flow.
