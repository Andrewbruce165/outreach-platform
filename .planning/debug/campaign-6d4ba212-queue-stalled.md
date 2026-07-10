---
status: awaiting_human_verify
trigger: "campaign 6d4ba212 status=running but queue stalled since 2026-07-09 13:55; 203 rows failed past_stop_date, no new queue rows, 0 pending/processing"
created: 2026-07-10
updated: 2026-07-10
---

## Current Focus

hypothesis: CONFIRMED (but parent premise was STALE). State changed between parent snapshot and now: the 203 rows are NO LONGER failed — they are status='pending' item_type='file', spread across 29 senders, scheduled_at 10:26 today. A `POST /campaigns/{id}/requeue-failed` was run (clears error_message/finished_at, sets pending + scheduled_at=NOW). The CURRENT blocker is NOT a terminal-failed / assignment-exclusion bug: it is the new-dialog RATE CAP. Every attached sender is current_level=1 (workspace budget level1_chats_per_day lowered 5→3 today 09:37) and each already opened 5 distinct new chats in the trailing 24h (yesterday's 12:00–13:24 burst). All 203 pending rows are NEW dialogs (0 follow-up-eligible), so the pick SELECT's new-dialog branch `opened_24h(5) < budget(3)` = FALSE excludes every row. Campaign resumes (throttled) only as yesterday's burst ages out of the rolling 24h window (~13:00–13:24 UTC today).
test: prod SQL (follow-up vs new-dialog split, per-sender cap coverage, age-out histogram) + code trace of dispatch pick predicate + grade_ladder.
expecting: RESOLVED-diagnosis. Immediate data already recovered (rows pending); remaining stall is the rate limiter (WAD) + latent stop_date/terminal-fail bugs.
next_action: human-verify checkpoint — decide which of the 3 code fixes to apply; no prod write performed (read-only role).

## Symptoms

expected: Campaign in status=running should keep sending to registered, un-contacted contacts of its folder while there are un-contacted registered contacts and healthy senders.
actual: Since 2026-07-09 13:55 the message_queue for this campaign is frozen: 0 rows with status NOT IN ('sent','failed'). Campaign technically "running" for >1 day with zero sends.
errors: 203 message_queue rows failed with error_message='past_stop_date', ALL with identical created_at/finished_at = 2026-07-09 12:12:25.746329+00 (single moment).
started: 2026-07-09 13:55 (last finished_at among status='sent').
reproduction: Read-only prod DB via `echo "SQL;" | sudo aimly-tg-outreach-deploy db-query`.

## Eliminated

- hypothesis: The 203 rows are still status='failed' with error_message='past_stop_date' and are terminal (never retried because their contacts already have campaign_contact_assignments and the enqueue worker only assigns NOT-YET-ASSIGNED contacts).
  evidence: Current DB shows 0 failed rows for this campaign. The 203 are status='pending', item_type='file', error_message=NULL, finished_at=NULL, created_at preserved at 12:12:25, scheduled_at=2026-07-10 10:26:33. This is exactly the signature of POST /campaigns/{id}/requeue-failed (campaigns.py:744-749). So the terminal-failed state was already undone by a manual requeue between the parent snapshot and this investigation. (The assignment-exclusion mechanism IS real — campaign_enqueue.py:352-355 — but it is not what is blocking NOW.)
  timestamp: 2026-07-10

- hypothesis: The send worker / dispatch tick is dead or crashing (0 sends system-wide since 07:01).
  evidence: api logs at 10:44 show the worker actively processing campaign 0c28f9b0 (ResolveUsername, NOT_REGISTERED re-rotation). Worker is alive; it simply is not PICKING 6d4ba212 rows because the pick SELECT excludes them (rate cap). 0c28f9b0 is separately churning on unresolvable contacts.
  timestamp: 2026-07-10

## Evidence

- timestamp: 2026-07-10
  checked: campaigns row for 6d4ba212
  found: status=running, start_date=2026-07-09 00:00:00+00, stop_date=2026-07-31 00:00:00+00, work_hour 10-20, tz Europe/Moscow, work_days_mask=31 (Mon-Fri), pause_reason NULL, created 2026-07-09 09:21:28, updated 2026-07-10 10:12:36.
  implication: stop_date now future; updated this morning (someone edited it). All date columns are timestamptz; stop_date stored as 00:00:00+00 = midnight UTC of the picked calendar day.

- timestamp: 2026-07-10
  checked: message_queue current breakdown for 6d4ba212
  found: 166 sent (file) + 203 pending (file). ZERO failed rows now. Pending: created_at all 12:12:25 (09.07), scheduled_at all 2026-07-10 10:26:33, spread across 29 senders. Attachment sales_deck_mobile.pdf uploaded 2026-07-09 09:22:07 → item_type was 'file' from creation.
  implication: The 203 were re-pended (requeue-failed). They are eligible by status/schedule/window/stop_date — the block is elsewhere.

- timestamp: 2026-07-10
  checked: system-wide send liveness
  found: last sent finished_at anywhere = 2026-07-10 07:01:54 UTC (0c28f9b0). 0 sends in the last 3h globally. Only 2 running campaigns: 0c28f9b0 (214 pending) + 6d4ba212 (203 pending) = 417 < QUEUE_TICK_BATCH(500), so the dispatch SELECT returns all of them.
  implication: Batch limit is NOT starving 6d4ba212. Something in the per-sender pick predicate excludes its rows.

- timestamp: 2026-07-10
  checked: workspace sender_grade_settings (ws bb96789d) + sender levels
  found: level1_chats_per_day=3, level2=9, level3=13; row updated 2026-07-10 09:37:44 (lowered from the default 5). ALL 29 pending senders are current_level=1 → budget_for_level=3 (grade_ladder.py:57-59).
  implication: New-dialog daily budget per sender = 3. Senders opened 5 yesterday under the old budget; the mid-flight reduction to 3 leaves them over-cap.

- timestamp: 2026-07-10
  checked: per-sender new-dialog cap counter (distinct phones sent, trailing 24h) — the exact subquery from queue.py:530-534
  found: every pending sender = exactly 5 opened_24h, 0 sent since today's window start. 29/29 senders are at/over the budget of 3.
  implication: New-dialog branch gate `opened_24h < account_budget` = `5 < 3` = FALSE for every sender.

- timestamp: 2026-07-10
  checked: DECISIVE — follow-up vs new-dialog split of the 203 pending rows
  found: 203 total pending, 0 follow-up-eligible (no prior status='sent' to same sender+phone), 203 new-dialog.
  implication: There is NO follow-up row to bypass the cap (queue.py:518-523). Since 100% are new dialogs and 100% of senders are cap-blocked, the pick SELECT returns nothing for this campaign → 0 dispatch. Root cause proven.

- timestamp: 2026-07-10
  checked: age-out schedule of yesterday's sends (when the rolling 24h cap releases)
  found: yesterday's sends by UTC hour — 09:00=2, 10:00=2, 12:00=24, 13:00=138. Bulk of the burst was 13:00–13:24 UTC.
  implication: The trailing-24h cap only drops below 3 as the 12:00/13:00 buckets age out (~12:00–13:24 UTC today). So the campaign self-resumes today around midday UTC, then dribbles at ~3 new chats/sender/day (level-1 throttle). Not permanently stuck.

- timestamp: 2026-07-10
  checked: stop_date origin — schema + dispatch comparison
  found: CampaignCreate/Update.stop_date = Optional[datetime] (schemas/__init__.py:802/879/939). A UI calendar date "2026-07-31" → Pydantic naive midnight → stored 2026-07-31 00:00:00+00. Dispatch (queue.py:291 and :558) fails a row when `now_utc >= c.stop_date`. _fail_past_stop_date_items (queue.py:1424) sets status='failed', error_message='past_stop_date', does NOT touch campaign_contact_assignments and does NOT re-enqueue.
  implication: stop_date fires at 00:00 UTC = 03:00 MSK of the picked day — effectively ~a day early. When stop_date is (even briefly) in the past, ONE tick terminally fails the entire pending batch with no automatic recovery; the enqueue worker will never re-create those rows because their contacts still have campaign_contact_assignments (campaign_enqueue.py:352-355). Recovery requires a manual POST requeue-failed — which is what happened here. This is the mechanism of the original 12:12:25 09.07 mass-fail (exact stop_date history is unrecoverable — no campaign field-change audit table exists).

## Resolution

root_cause: |
  TWO layers.

  (1) IMMEDIATE / current stall (largely rate-limiter WAD, not a code defect):
  The 203 rows are NOT terminally failed anymore — a POST /campaigns/{id}/requeue-failed
  already re-pended them (status='pending', scheduled_at=NOW, error cleared; created_at
  preserved). They are blocked purely by the sender-wide NEW-DIALOG rate cap: all 29
  attached senders are current_level=1 with a daily new-chat budget of 3 (workspace lowered
  level1_chats_per_day 5→3 today at 09:37), and each already opened 5 distinct new chats in
  the trailing 24h (yesterday's 12:00–13:24 burst). All 203 pending rows are NEW dialogs
  (0 follow-up-eligible), so the pick SELECT's new-dialog branch (queue.py:529-534)
  `opened_24h(5) < budget(3)` = FALSE excludes every row and there is no follow-up to bypass
  it. The campaign self-resumes (throttled) as yesterday's burst ages out of the rolling 24h
  window (~12:00–13:24 UTC today), then dribbles at ~3 new chats/sender/day.

  (2) LATENT / systemic code bugs (the real fix targets):
  (2a) stop_date semantics: stop_date is Optional[datetime]; a UI calendar date becomes
       midnight UTC and the dispatch compares `now_utc >= stop_date`, so a campaign stops at
       00:00 UTC = 03:00 MSK of the stop day (≈ a full day early, and in the middle of the
       night MSK). This is the mechanism behind the original 12:12:25 09.07 mass-fail of 203
       rows with error_message='past_stop_date'.
  (2b) past_stop_date is TERMINAL with no auto-recovery: _fail_past_stop_date_items fails the
       whole pending batch in a single tick; the enqueue worker never rebuilds those rows
       (their contacts keep their campaign_contact_assignments — campaign_enqueue.py:352-355).
       ANY running campaign whose stop_date passes (or is briefly misset) silently loses its
       entire pending batch until a human manually calls requeue-failed. This is systemic.
fix: |
  PROPOSED (NOT applied — read-only DB role; awaiting human decision). Three independent items:

  A. NO code change for the immediate stall (recommended default): it is the safety rate
     limiter. The campaign will self-resume around midday UTC today. If faster throughput is
     desired the correct levers are (i) attach more level-1 senders, (ii) promote senders to
     a higher grade, or (iii) raise level1_chats_per_day in workspace sender_grade_settings —
     a product/config decision, NOT a queue code edit (CLAUDE.md: don't touch queue limits
     without discussion). Note the workspace just LOWERED it 5→3 today, which tightened the cap.

  B. stop_date end-of-day semantics (real bug 2a): treat a stop_date supplied as a bare
     calendar date as END of that day in the campaign timezone (e.g. coerce 00:00 → next-day
     00:00 in campaign tz, or compare against stop_date + 1 day). Needs product sign-off on the
     intended meaning of the UI date picker before coding.

  C. past_stop_date auto-recovery (real bug 2b): either (i) do NOT terminally fail on
     past_stop_date — leave rows pending and skip (so a later stop_date correction auto-resumes),
     or (ii) have the enqueue worker/failover treat 'failed:past_stop_date' rows as re-pendable
     when the campaign is running and stop_date is now future. Needs design decision.

  D. Data (this campaign only): NO prod write needed — the 203 are already pending and will
     flow once the 24h cap releases. If the operator wants them out sooner, that is lever A(i/ii/iii),
     not a DB edit.
verification: |
  Diagnosis self-verified via prod read-only SQL (decisive test: 203/203 new-dialog, 29/29
  senders at/over cap, age-out histogram) + full code trace of the dispatch pick predicate
  (queue.py:495-552), grade_ladder.budget_for_level, requeue-failed, and stop_date schema.
  AWAITING human verification of: (1) confirm the campaign resumes on its own this afternoon
  UTC (watch message_queue sent count for 6d4ba212 after ~13:30 UTC), and (2) decision on
  which of code fixes B / C to implement.
files_changed: []
