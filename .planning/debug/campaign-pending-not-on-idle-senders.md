---
status: awaiting_human_verify
trigger: "campaign 0c28f9b0: 5 healthy senders have 0 pending rows; backlog on other 45. Why no auto-redistribution?"
created: 2026-07-10
updated: 2026-07-10
---

## Current Focus

hypothesis: CONFIRMED — no continuous even-split rebalance exists. rebalance_on_attach is edge-triggered (attach / restriction-clear only) and computes fair-share against the cold-pending pool AT THAT MOMENT; the enqueue worker only assigns NOT-YET-ASSIGNED contacts and never revisits existing cold-pending rows. Idle-but-eligible senders are never topped up from the standing backlog.
test: Confirmed via prod DB timeline (single-tick enqueue at 12:40:18) + code trace of every rebalance caller.
expecting: Option A APPROVED and IMPLEMENTED (rebalance_campaign_even + per-tick worker call). 58 targeted tests green. NOT deployed.
next_action: User deploys (cd /root/apps/aimly/tg-outreach && docker compose up -d --build api) and confirms the 214 pending rows spread across all ~50 eligible senders on the first worker tick.

## Symptoms

expected: Pending queue rows spread across ALL healthy attached senders, including idle ones.
actual: 5 healthy senders (8098841232, 8812666662, 8702513506, 8455819832, 8820014103) have 0 pending; backlog on other ~45.
errors: none
reproduction: prod DB campaign 0c28f9b0-5ad4-41c4-86f8-b476d09e57ef
started: noticed 2026-07-10; likely senders attached/healthy after enqueue.

## Eliminated

- hypothesis: The 5 senders are restricted / paused / in checker_rest so they are excluded from the eligible pool.
  evidence: All 5 are lifecycle_status=active, restriction_status=none, auth_status=ok, role=sender; checker_rest_until and restricted_until are NULL; sender_restriction_events has ZERO rows for all 5. They are fully eligible now.
  timestamp: 2026-07-10

- hypothesis: They were never attached to the campaign.
  evidence: All 5 are present in campaign_senders (added_at 12:37 for three, 13:47 for two). They ARE attached.
  timestamp: 2026-07-10

- hypothesis: Duplicate sender rows per telegram_id caused a miscount.
  evidence: SELECT telegram_id, COUNT(*) FROM senders → exactly 1 row each. No duplicates.
  timestamp: 2026-07-10

## Evidence

- timestamp: 2026-07-10
  checked: message_queue enqueue timing for campaign 0c28f9b0
  found: 367 of 369 rows share created_at = 2026-07-08 12:40:18.31638 (one enqueue tick); 2 stragglers at 10:43. Current state 214 pending / 155 sent, campaign status = running (not paused).
  implication: The entire backlog was assigned in a single least-loaded pass. Distribution was frozen at that instant except for later attach/failover moves.

- timestamp: 2026-07-10
  checked: each of the 5 target senders' queue rows (by UUID + by telegram_id)
  found: Each holds EXACTLY 3 rows, all status='sent', item_type='message', 0 pending. Matches the objective's "zero pending" precisely.
  implication: These senders finished their small share and have nothing left; the 214-row standing backlog sits entirely on the other ~45 senders.

- timestamp: 2026-07-10
  checked: campaign_senders.added_at vs enqueue time
  found: 8812666662/8820014103/8098841232 attached 12:37 (BEFORE 12:40 enqueue); 8702513506/8455819832 attached 13:47 (AFTER enqueue) yet hold rows with created_at 12:40:18 and scheduled_at reset to 13:03–13:50.
  implication: The two late-attached senders got their 3 rows via rebalance_on_attach (created_at preserved, scheduled_at reset = the rebalance/evac signature). Proves rebalance fires ON ATTACH and gives only the fair-share computed against the already-depleted cold-pending pool at 13:47 (most rows already sent → tiny share).

- timestamp: 2026-07-10
  checked: global vs per-campaign cca load (rotation._pick_least_loaded is GLOBAL across all campaigns)
  found: The 5 are the LEAST globally-loaded senders (global_cca 3–4 vs pool max 134, avg 16.7). Per-campaign avg = 6.9, max = 9; the 5 got only 3 each.
  implication: Even the globally-least-loaded senders ended underloaded in this campaign — enqueue-time distribution + one-shot attach rebalance did not, and cannot, top them up afterward.

- timestamp: 2026-07-10
  checked: every caller of rebalance_on_attach (grep app/)
  found: 3 call sites only — campaigns.py attach_sender (POST attach, running campaign), senders.py (sender auth/attach path), listener.py (restriction-clear "rebalance-back"). The enqueue worker's per-tick sweep only runs _sweep_stranded_cold_backlog → failover_cold_backlog, which moves cold-pending OFF INELIGIBLE senders onto eligible ones — it never evens load AMONG already-eligible senders.
  implication: There is NO periodic/continuous even-split across eligible senders. Redistribution is purely edge-triggered (attach / restriction-clear).

- timestamp: 2026-07-10
  checked: rebalance.py docstring + fair_share math
  found: Explicit v1 scope note — "the ±1-of-total/P even-split is guaranteed for the NEWLY-ATTACHED sender only. This single pass does not re-balance pre-existing donors against each other." fair_share = ceil(cold_pending_now / P); need = fair_share - load[new_sid]; only moves rows ONTO the one new sender.
  implication: By design, a sender that becomes eligible (or is under-picked) after the backlog is mostly sent is left underloaded forever. This is the documented, intended v1 limitation — now surfacing as the reported symptom.

## Resolution

root_cause: |
  The platform has NO continuous/periodic rebalance that evens out standing cold-pending
  backlog across all *already-eligible* senders. Redistribution is edge-triggered only:
    (a) rebalance_on_attach — fires on explicit attach (campaigns.py / senders.py) or on
        restriction-clear in the listener; it back-fills the ONE newly-eligible sender to
        ceil(cold_pending_NOW / P). Because it uses the cold-pending pool size AT THAT
        MOMENT, a sender that becomes eligible after most of the batch is already sent gets
        only a tiny share (observed: the 13:47 late-attached senders got 3 rows each).
    (b) failover / _sweep_stranded_cold_backlog — moves cold-pending OFF ineligible senders
        onto eligible ones; it never evens load among senders that are already eligible.
  The enqueue worker's least-loaded rotation only assigns NOT-YET-ASSIGNED contacts; it never
  revisits an existing cold-pending row. So once the 12:40:18 single-tick enqueue + the
  attach-time shares were sent, the 5 idle senders had nothing left and the remaining 214
  pending rows stay stuck on the ~45 senders that were assigned them at enqueue. rebalance.py's
  own docstring confirms this is the intended (but now painful) v1 scope limit.
fix: |
  Option A (user-approved) IMPLEMENTED:
  1. app/services/rebalance.py — new `rebalance_campaign_even(campaign_id, db)`:
     continuous even-split of cold-pending rows across ALL eligible senders of a campaign.
     - Eligible-pool filter copied verbatim from rotation.py / rebalance_on_attach Step 1.
     - Idle eligible senders seeded with load=0 so they count as receivers.
     - Minimal-move targets: total = P*floor + r → the r currently most-loaded senders keep
       ceil, rest get floor → an already-even pool computes zero surplus → idempotent no-op.
     - Only rows on ELIGIBLE senders move (stranded ineligible-donor rows remain the
       sweep/failover's job); only item_type IN ('message','file') + _COLD_PENDING_PREDICATE.
     - Same worker-safe machinery: status='pending' + FOR UPDATE OF mq SKIP LOCKED,
       queue row + sticky CCA updated in lock-step, BATCH_CAP (500) per pass.
     - scheduled_at NOT reset (donors are healthy — no inherited freeze pause).
     - Transaction-neutral (CR-01): caller commits.
  2. app/services/campaign_enqueue.py — new `_rebalance_even_running_campaigns()` on
     CampaignEnqueueWorker: own session, iterates running campaigns, calls
     rebalance_campaign_even + commits per campaign. Invoked in `_tick` AFTER
     _sweep_stranded_cold_backlog (evacuated rows are counted on their new eligible
     senders) and BEFORE enqueue, wrapped in try/except (worker-must-not-die).
  NOT changed: per-account rate limits, queue intervals, FloodWait retry, enqueue rotation.
  Prod DB untouched — the 214 pending rows redistribute on the first tick after deploy.
verification: |
  Self-verified (test-overlay ONLY, per CLAUDE.md):
    docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest \
      tests/test_rebalance.py tests/test_campaign_enqueue_worker.py \
      tests/test_rotation_campaign.py tests/test_failover.py -q
    → 56 passed (incl. 8 NEW: EVEN-01..06 unit + 2 worker-pass tests)
    docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest \
      tests/test_queue_enqueue.py -q → 2 passed
  New tests cover: idle-sender backfill (3-way even), idempotency (2nd pass = 0 moves),
  scheduled_at preservation, non-cold rows (sent/processing/engaged) never move, frozen
  attached sender receives nothing, P<2 no-op, worker pass skips paused campaigns.
  PENDING human verification: deploy api, watch first tick log
  ("rebalance: even-split moved N cold-pending rows...") and re-run the distribution SQL
  on campaign 0c28f9b0 — the 5 idle senders must pick up ~4 pending rows each.
files_changed:
  - app/services/rebalance.py (new rebalance_campaign_even)
  - app/services/campaign_enqueue.py (_rebalance_even_running_campaigns + _tick wiring)
  - tests/test_rebalance.py (6 new EVEN-* tests)
  - tests/test_campaign_enqueue_worker.py (2 new worker even-split tests)
