---
status: resolved
trigger: "cnc-followup-campaign-100pct-not-registered — campaign bb654c73-41a5-442b-b6b7-14cdcb97475d got RECIPIENT_NOT_IN_TELEGRAM on 100% of 20 queue items, across up to 8 different senders each"
created: 2026-07-27T07:45:00Z
updated: 2026-07-28T00:00:00Z
---

## Current Focus

hypothesis: CONFIRMED — stale cross-sender negative `contacts_cache` rows written by four
  throttle-degraded checkers at 06:48–06:56 short-circuit `resolve_contact` before ANY
  Telegram call. The later healthy re-check (07:03–07:04) stamped tg_confidence='high' /
  tg_probe_state='clean', which CLOSED the D-12 read-suppression gate and re-enabled the poison.
test: per-contact comparison of `fresh_false_rows` vs `suppression_active` vs observed outcome
expecting: contacts with poison + closed gate → instant 100% fail on every sender;
  contacts without poison → real Telegram interaction. CONFIRMED, 20/20.
next_action: propose fix (read-only investigation — do NOT patch); checkpoint with user

## Symptoms

expected: |
  Checker sender-8514716383 confirmed 18/20 contacts as tg_status='registered',
  tg_confidence='high', tg_probe_state='clean' (single batch 2026-07-27 07:04:35 UTC).
  10 have captured tg_username_resolved. Expected at least partial send success,
  esp. for @username contacts (ResolveUsername bypasses phone privacy).

actual: |
  ALL 20 message_queue items got RECIPIENT_NOT_IN_TELEGRAM on EVERY attempt, including
  @username contacts. _reroute_resolve_fail (queue.py:1514) rotated items across up to 8 of
  12 healthy senders. 100% failure on every sender. 109 total attempts over ~18 min
  (07:17:56 → 07:35:52, user paused).

errors: |
  - error_message "RECIPIENT_NOT_IN_TELEGRAM" (extra_data.resolve_fail_code)
  - one item (3f1cb610, +79104097409): "Аккаунт заморожен Telegram (FROZEN_*)" at 07:18:02
  - sender-8735089760 restriction_status='frozen', restricted_until 08:18:03
  - all other 12 attached senders read restriction_status='none'

reproduction: |
  read-only: echo "SQL" | sudo aimly-tg-outreach-deploy db-query
  logs: sudo aimly-tg-outreach-deploy logs api

started: 2026-07-27 07:17:56 UTC (first enqueue tick of this campaign)

## Eliminated

- hypothesis: (i) sender-side degradation — the 12 senders were throttled/shadow-restricted
    on the contacts API
  evidence: NOT the primary cause. 5 of the 12 (8965165137, 8685507421, 8909362310,
    7867638054, 8539506204) successfully ran ResolveUsernameRequest and wrote
    contacts_cache rows with access_hash at 06:00:04–06:00:18 the SAME morning; several of
    the same accounts (8606728473, 8697070545, 8706625176, 8965165137, 8229306450)
    completed tier-3 ImportContacts phone resolves as recently as 2026-07-14 / 07-20.
    Decisive: during 07:17–07:35 these senders made ZERO Telegram calls (no contacts_cache
    writes, no app.services.telegram log lines) — the failure happened in a DB read.
  timestamp: 2026-07-27T08:00:00Z

- hypothesis: (ii) checker false POSITIVE — the 18 numbers are not actually registered and
    sender-8514716383 wrongly confirmed them
  evidence: The confirming resolve was `ResolvePhoneRequest` (checker.py:115), which returned
    real telegram_ids and real public handles (dleder=395718440, zenitlogistics=700773528,
    Ginger_Mew=5183186706 …). ResolvePhone cannot fabricate a user object. The checker was
    also NOT throttled at 07:03–07:04 (it was only flagged at 07:33, 29 min later).
  timestamp: 2026-07-27T08:00:00Z

- hypothesis: verdict lookup fails (workspace/phone mismatch) so tier-2/tier-3 are skipped
  evidence: exact-equality join `contacts.phone = message_queue.recipient_phone AND
    workspace_id` matches 19/20 rows (the 20th is the '@VladZettel' identity key), all
    tg_status='registered', octet_length=12 (no hidden characters), exactly 1 contacts row
    per phone. `_load_contact_verdict` would have returned correctly.
  timestamp: 2026-07-27T07:58:00Z

- hypothesis: deployed code differs from the repo (stale image)
  evidence: api container created 13 days ago (~2026-07-14); last commit touching
    telegram.py/queue.py is 2026-07-13. Uncommitted working-tree diff is confined to the
    2FA recovery-email path, untouched by resolve/send.
  timestamp: 2026-07-27T07:52:00Z

## Evidence

- timestamp: 2026-07-27T07:46:00Z
  checked: knowledge-base.md
  found: no 2+ keyword overlap with existing entries
  implication: novel pattern

- timestamp: 2026-07-27T07:48:00Z
  checked: contacts_cache writes in workspace bb96789d during 07:17–07:35
  found: ZERO rows written by any sender. Only checker rows at 07:03–07:04 and one
    '@Mayfair22' at 07:00:21.
  implication: `_resolve_username` (writes cache on BOTH success and empty result) and
    tier-3 ImportContacts never ran, or always raised. First hint the ladder short-circuited.

- timestamp: 2026-07-27T07:50:00Z
  checked: live api logs (232 lines, INFO level, 9 consecutive warmup sends)
  found: warmup is ALSO failing 100% right now — "🔥 Warmup: +16184955131 не зарегистрирован
    в Telegram" for our OWN sender phones. ZERO `app.services.telegram` log lines across all
    9 attempts, so ZERO Telegram calls are made.
  implication: SEPARATE, still-live bug — `resolve_contact` returns is_registered=False with
    no network call for any phone that has no `contacts` row (warmup targets have none:
    verified 0 rows for all 6). Warmup-pair cache rows last written 2026-06-30, 7-day TTL →
    warmup Telethon delivery has been 100% dead since ~2026-07-07 while `warmup_messages`
    keeps recording ~100 rows/hour (warmup.py:615-637 saves the row and only logs a warning).

- timestamp: 2026-07-27T07:56:00Z
  checked: message_queue outcomes split by identity-key type, 30 days
  found: username-key sends: 892 sent (last 2026-07-27 06:59:46). Phone-key sends: 126 sent
    (last 2026-07-20 07:00:48), and NO prior phone-key failure ever carried the
    "не зарегистрирован" text. Today is the first mass phone-key NOT_REGISTERED.
  implication: not a long-standing structural failure of the phone ladder; something changed
    for these specific contacts.

- timestamp: 2026-07-27T08:02:00Z
  checked: ALL contacts_cache rows (any age) for the 20 campaign recipients
  found: EVERY one of the 18 checker-verified phones carries FOUR fresh
    `is_registered = false` rows written 06:48:45–06:56:25 by checkers sender-8364639216,
    sender-8017533134, sender-8525079460, sender-7979031303 — EXACTLY 30 false rows each
    (= burst_cap, one full batch) — and each of those four was flagged
    spam_limited/antispam_signal at 06:50:32 / 06:52:54 / 06:54:42 / 06:56:25, i.e. to the
    millisecond of its own last false write.
  implication: four checkers burned through a full batch each while sliding into a Telegram
    contacts-API throttle, emitting 100% false negatives, and durably wrote them into
    contacts_cache (checker.py::_save_cache runs per phone, BEFORE the batch-level suspect
    determination). Nothing ever purges those rows.

- timestamp: 2026-07-27T08:05:00Z
  checked: per-contact `fresh_false_rows` vs D-12 suppression predicate vs observed outcome
  found: |
    18 contacts → fresh_false_rows=4, tg_confidence='high', tg_probe_state='clean'
                  → suppression_active=FALSE → served the stale false from cache
                  → these are exactly the 18 items that rotated endlessly (7–8 senders each)
    +79104097409 → fresh_false_rows=0, tg_confidence NULL → suppression_active=TRUE
                  → LIVE resolve → produced the only genuine Telegram error of the incident
                    (ACCOUNT_FROZEN on sender-8735089760 at 07:18:02)
    @VladZettel  → fresh_false_rows=0, no contacts row → suppression_active=TRUE
                  → LIVE resolve → genuine stale-handle result (2 attempts only)
  implication: DECISIVE. The single contact whose suppression gate was open is the single
    contact that actually talked to Telegram. Everything else short-circuited in
    `_get_cached_contact` step 3.

- timestamp: 2026-07-27T08:07:00Z
  checked: code paths that purge poisoned contacts_cache false rows
  found: only `send_suspect._rollback` step 4 (fires ONLY from queue.py's inline PEER_FLOOD /
    ACCOUNT_FROZEN handlers, i.e. for a SENDER) and `senders.py:808` (full sender deletion).
    `contact_check_worker._flag_checker_degraded` / `_maybe_degrade_on_signal` roll back the
    checker's `contacts` verdicts to pending/suspect but NEVER touch `contacts_cache`.
  implication: a degraded CHECKER's cache poison is permanent for 7 days by design gap.

- timestamp: 2026-07-27T08:08:00Z
  checked: blast radius
  found: 45 distinct phones currently carry a fresh (<7d) `is_registered=false` cache row
    from a now-restricted account; 19 of them already have a `registered`/`high`/`clean`
    contacts verdict → suppression gate closed → unsendable from cache until ~2026-08-03.
  implication: systemic, not campaign-specific.

- timestamp: 2026-07-27T08:09:00Z
  checked: item 3f1cb610 attribution (reporter's concern 4b)
  found: `failover_cold_backlog` (failover.py:203) moves `sender_id` + `scheduled_at` only —
    it does NOT clear `error_message`, `attempts` or `started_at`. The FROZEN error belonged
    to sender-8735089760 (whose restriction_status='frozen' and restriction event ARE both
    correctly recorded at 07:18:03); the row was then failed over onto sender-8685507421
    carrying the old error text.
  implication: FROZEN detection worked correctly. The apparent "restriction_status stayed
    none" is a forensic/cosmetic misattribution bug in failover, not a detection failure.

- timestamp: 2026-07-28 (re-check on user request "почему не запускается кампания")
  checked: campaigns.status, message_queue, poisoned contacts_cache rows
  found: campaign still status='paused' (untouched since 07-27 07:35). 20 queue items still
    'pending'. All 4×30 = 120 poisoned is_registered=false rows from the four throttled
    checkers are STILL alive (TTL expires ~2026-08-03). No fix applied; working-tree diff in
    telegram.py is unrelated (account-profile/SpamBot work).
  implication: diagnosis unchanged — resuming the campaign now reproduces the incident.

## Resolution

root_cause: |
  `TelegramService._get_cached_contact` (app/services/telegram.py:534-555) — the step-3
  cross-sender negative cache lookup — serves a stale `contacts_cache.is_registered=false`
  row written by ANY account in the workspace within 7 days, and returns
  `{"is_registered": False, "from_cache": True}` BEFORE the resolve ladder runs.

  The query filters `is_registered = false` only. It never checks whether a NEWER
  `is_registered = true` row exists for the same phone, and never compares the row's
  `updated_at` against `contacts.tg_checked_at`. Its ONLY guard is the D-12 "suspect"
  predicate evaluated on the CURRENT contacts row
  (`tg_probe_state='suspect' OR tg_confidence IS DISTINCT FROM 'high'`), and that guard is
  inverted by success: the moment a healthy checker re-resolves the phone as
  registered/high/clean, the gate CLOSES and the stale false becomes authoritative again.

  Trigger chain on 2026-07-27:
    06:48:45–06:56:25  four checkers (8364639216, 8017533134, 8525079460, 7979031303) each
                       burn a full 30-phone batch while sliding into a Telegram contacts-API
                       throttle → 100% false negatives → 30 durable `is_registered=false`
                       rows each (checker.py::_save_cache writes per phone, before the
                       batch-level suspect verdict). Each is flagged spam_limited at the
                       millisecond of its own last write. Their `contacts` verdicts are
                       correctly rolled back to pending — but nothing purges the cache.
    07:03:20–07:04:35  healthy checker 8514716383 re-resolves the same numbers via
                       ResolvePhone → real telegram_ids + real @handles →
                       contacts.tg_status='registered', tg_confidence='high',
                       tg_probe_state='clean'. This stamp closes the D-12 suppression gate.
    07:17:56 onward    every send attempt for those 18 contacts returns the poisoned false
                       from cache with ZERO Telegram calls, on EVERY sender, forever.

  Direction of the problem: (iii) code bug in the resolve/cache layer. NOT sender degradation
  (i) and NOT a checker false positive (ii).

  Two aggravating defects observed in the same incident:
  - `_reroute_resolve_fail` (queue.py:1514) re-rotates on a resolve failure that came
    `from_cache` — a workspace-level DB read whose answer cannot change by switching account.
    This turned 20 doomed items into 109 attempts across 12 accounts in 18 minutes, and only
    stopped because a human paused the campaign. One sender froze mid-run (07:18:03).
  - `send_suspect.rollback_suspect_resolve_fails` cannot fire here: it only claws back rows
    with `status='failed'` (rerouted rows stay `pending`, finished_at NULL) and only runs
    from the queue's PEER_FLOOD / ACCOUNT_FROZEN handlers on a SENDER — never for a CHECKER
    flagged via antispam_signal.

  Separate, still-live bug found while investigating: `resolve_contact` returns
  `{"is_registered": False}` with zero Telegram calls for any phone that has no `contacts`
  row with tg_status='registered' (sender ResolvePhone removed by D-01, tier-3 gated by
  D-03, tier-2 needs a captured handle). Warmup — which targets our own sender phones, none
  of which exist in `contacts` — has therefore been 100% dead since ~2026-07-07 while
  `warmup_messages` keeps recording ~100 phantom rows/hour. `_load_contact_verdict`'s own
  docstring says "callers treat a None verdict permissively"; the code does the opposite.

fix: |
  APPLIED 2026-07-28 (commit 2e3b62d), all 5 proposed items:
  1. _get_cached_contact — newest-row + verdict-recency (vs contacts.tg_checked_at)
     on both the per-sender and cross-sender false reads; D-12 suspect gate kept.
  2. _flag_checker_degraded (+ REST-ONLY branch) purges the checker's fresh
     is_registered=false cache rows in the same TX (SUSPECT_RESOLVE_WINDOW_MINUTES).
  3. send_message/send_file stamp from_cache on RECIPIENT_NOT_IN_TELEGRAM;
     queue skips _reroute_resolve_fail for cache-sourced fails.
  4. warmup: fallback resolve via senders.telegram_id + get_dialogs entity warm-up,
     resolve cached; phantom warmup_messages row + session counters rolled back
     when Telethon did not deliver.
  5. failover clears error_message/attempts/started_at on row move.
  OPS: 120 poisoned cache rows purged from prod (backup outreach_20260728_111705).
  Campaign bb654c73 left PAUSED per user instruction — resume is manual.
verification: |
  tests/test_poisoned_cache_recency.py — 5 new regression tests (incident shape,
  preserved fresh-false behavior, newest-true supersede, degrade purge, from_cache
  flag) + send/queue/checker/warmup/failover suites green (only pre-existing
  test_restricted_sender_excluded failure, stale vs deliberate spam_limited warmup).
files_changed:
  - app/services/telegram.py
  - app/services/queue.py
  - app/services/contact_check_worker.py
  - app/services/warmup.py
  - app/services/failover.py
  - tests/test_poisoned_cache_recency.py

## Proposed fix (NOT applied)

1. PRIMARY — app/services/telegram.py::_get_cached_contact, step 3 (lines 534-555).
   Take the LATEST cache row for the workspace+phone instead of "any false row", and require
   it to be at least as recent as the checker verdict:
     - `ORDER BY updated_at DESC LIMIT 1` over all rows for (workspace_id, phone), then
       short-circuit only if that newest row is `is_registered = false`;
     - AND require `cc.updated_at >= contacts.tg_checked_at` (a newer checker verdict wins);
     - keep the existing D-12 suspect suppression on top.
   This alone makes the incident impossible: the 07:04 `true` row is newer than the 06:48–06:56
   `false` rows.

2. SOURCE — app/services/contact_check_worker.py::_flag_checker_degraded /
   _maybe_degrade_on_signal. When a checker is degraded, DELETE its
   `contacts_cache` rows with `is_registered=false` written in the degradation window —
   mirroring `send_suspect._rollback` step 4, which already does exactly this for senders.
   Today the checker path rolls back `contacts` but leaves the cache poisoned.

3. AMPLIFIER — app/services/telegram.py::send_message / send_file + queue.py:1347.
   Propagate `from_cache` (or a `resolve_source`) on the RECIPIENT_NOT_IN_TELEGRAM error and
   skip `_reroute_resolve_fail` when the verdict came from cache: rotating accounts cannot
   change a workspace-scoped DB read. Would have capped this incident at 20 attempts
   instead of 109 and avoided the mid-run freeze.

4. SEPARATE (warmup) — allow tier-3 ImportContacts when the verdict is None/unknown (only an
   explicit `not_registered` should skip it), per `_load_contact_verdict`'s own documented
   contract; or give warmup a dedicated path (its targets are our own senders and
   `senders.telegram_id` is already in the DB). Also make warmup stop writing
   `warmup_messages` rows for sends Telethon never delivered.

5. COSMETIC — app/services/failover.py:203: clear `error_message`, `attempts`, `started_at`
   when moving a row to a new sender, so a failed-over row does not carry the previous
   account's error.

## Operational note (requires attention)

The poison is still live. 45 distinct phones currently hold a fresh `is_registered=false`
row from a now-restricted account; 19 already have a `registered`/`high`/`clean` verdict, so
the suppression gate is closed and they are unsendable-from-cache until roughly 2026-08-03
(7 days after 06:48–06:56). Any campaign in workspace bb96789d that touches them will
reproduce this incident and burn contacts-API budget / risk further freezes. Purging
`contacts_cache` rows with `is_registered=false` written by the four flagged checkers
between 2026-07-27 06:48 and 06:57 would clear it immediately (write access required —
not performed by this investigation).
