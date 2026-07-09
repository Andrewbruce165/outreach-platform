---
status: resolved
trigger: "после repflooderror уходят на отдых часовой, а когда вернуться работы нет. нужно сделать так, чтобы когда аккаунт возвращался в active он получал новую очередь и продолжил рассылку в рамках своих лимитов"
created: 2026-07-09
updated: 2026-07-09
---

# Debug: PeerFloodError → hour rest → sender returns to active but has no queued work

## Symptoms

DATA_START
- **Expected behavior:** When a sender hits PeerFloodError it rests (~1 hour). When the restriction expires and the sender returns to `active`, it should receive a fresh share of the campaign queue and continue sending within its rate limits.
- **Actual behavior:** After the rest period ends, the sender returns to active but has no pending work — its queue was emptied/rebalanced away while it was restricted, and nothing rebalances work back to it. Campaign throughput stays degraded even though the sender is healthy again.
- **Error messages:** PeerFloodError from Telegram (triggers the ~1h restriction). No error on return — just silence/no sends.
- **Timeline:** Ongoing behavior in the current queue/rebalance design; noticed during live campaign operation (reported 2026-07-09).
- **Reproduction:** Run a campaign with a sender pool; one sender hits PeerFloodError → gets `restriction_status` set + `restricted_until` ~1h. Wait for restriction to clear. Observe the sender has 0 pending message_queue rows and never picks up new work.
DATA_END

## Known context (orchestrator prefill — verify, don't trust blindly)

- Project memory: `rebalance_on_attach` requires ≥2 eligible senders — attaching/returning a healthy sender when pool has <2 eligible moves 0 rows (P<2 no-op); backlog stalls until reconcile clears the flag. Likely the same family of bug: no rebalance-back path when a restricted sender recovers.
- Queue lives in PostgreSQL `message_queue`; rate limits 4/min, 20/h, 150/day per sender (`app/services/queue.py`). Restriction state: `senders.restriction_status`, `restricted_until`; audit log `sender_restriction_events` (event_type incl. `flood_wait`? check mig 031), reconcile job clears expired restrictions.
- Desired fix direction (user requirement): when a sender transitions back to active (restriction expired/cleared), it should be re-included in distribution and receive queued work again, respecting its own limits.
- Prod: docker compose stack at /root/apps/aimly/tg-outreach (db container `outreach-platform-db`, user `outreach_user`, db `outreach_platform`). Read-only SQL for evidence is fine; NEVER `down -v`.

## Evidence

- timestamp: 2026-07-09
  checked: app/services/queue.py PEER_FLOOD branch (lines 1109-1171)
  found: On PeerFlood the sender is flagged `spam_limited` + `restricted_until = now + 1h`, then `failover_cold_backlog(sender.id)` is called. failover MOVES all cold-pending rows (never-sent, no started dialog) OFF this sender onto the healthy pool — reassigns `message_queue.sender_id` + `campaign_contact_assignments.sender_id` + resets `scheduled_at = NOW()`. Engaged dialogs stay put.
  implication: The sender's cold backlog physically leaves it while restricted. This is deliberate (cold contacts shouldn't stall 1h) but it is ONE-directional.

- timestamp: 2026-07-09
  checked: app/services/failover.py (_failover) + app/services/rebalance.py (rebalance_on_attach)
  found: failover is the "shed work on restriction" op. rebalance_on_attach is its symmetric "pull a fair share of cold-pending back onto a sender" op (evacuate ineligible-donor rows + fair-share backfill to ±1 of total/P). rebalance_on_attach is only ever called from campaigns.py attach_sender (line 1265) — NEVER on restriction-recovery.
  implication: There is no code path that returns work to a sender when its restriction clears.

- timestamp: 2026-07-09
  checked: app/services/listener.py _restriction_reconcile_tick "free" branch (lines 1732-1753)
  found: On SpamBot "free" verdict it sets `restriction_status='none', restricted_until=NULL` and un-pauses ONLY `UPDATE message_queue SET scheduled_at=NOW() WHERE sender_id=:sid AND status='pending' AND scheduled_at > NOW()`. That query matches only rows STILL assigned to this sender. For a PeerFlood recovery those rows are just engaged dialogs (weren't paused anyway); the cold backlog was moved away by failover and never returns.
  implication: ROOT CAUSE confirmed — asymmetric distribution. Fix = call rebalance_on_attach for each of the sender's campaigns inside the "free" branch (same tx), so the recovered, now-eligible sender pulls back a fair share of cold-pending backlog.

- timestamp: 2026-07-09
  checked: prod DB (senders, message_queue, sender_restriction_events, campaigns)
  found: Recheck interval = 1h (config default, RESTRICTION_RECHECK_INTERVAL). Recent recurring spam_limited flags on sender-7979031303 / -8364639216 / -8017533134 / -8525079460. Currently all campaigns are paused/done so no live sending to reproduce end-to-end right now; frozen senders -8633043411/-8853982699 have 0 pending (backlog already shed).
  implication: Live E2E reproduction not possible until a campaign is running; code-path proof is definitive. Fix must be verified via unit tests + reasoning.

## Eliminated

- hypothesis: The un-pause query itself is broken (wrong WHERE clause).
  evidence: The query is correct for rows that stayed on the sender; the problem is that failover REMOVED the cold rows from the sender entirely, so there is nothing for the un-pause to match. The bug is a missing rebalance-back, not a broken un-pause.
  timestamp: 2026-07-09

## Resolution

root_cause: PeerFlood recovery is asymmetric. On PeerFlood, `failover_cold_backlog` moves the sender's cold-pending backlog onto the healthy pool. On restriction-clear (SpamBot "free"), `_restriction_reconcile_tick` only clears the flag and un-pauses rows still assigned to the sender — it never pulls a share of cold-pending backlog back. A sender doing cold outreach therefore returns to `active` with no cold work and campaign throughput stays degraded.
fix: In listener.py `_restriction_reconcile_tick` "free" branch, after clearing the restriction (in the same transaction), call `rebalance_on_attach(campaign_id, sender_id, db)` for every campaign the recovered sender belongs to. This is the exact symmetric inverse of failover — it evacuates ineligible-donor rows and back-fills the now-eligible recovered sender to its fair ±1 of total/P share of cold-pending backlog, respecting its own rate limits (limits unchanged).
verification: 55 targeted tests GREEN via test-overlay (test_sender_restriction incl. 2 new rebalance-back tests, test_rebalance, test_failover, test_spambot_selfcheck, test_restriction_audit). Live E2E deferred — all campaigns currently paused/done. Deployed to prod via api+listener rebuild. Awaiting user confirmation on next live PeerFlood recovery.
files_changed: [app/services/listener.py, app/routers/senders.py, tests/test_sender_restriction.py]

## Current Focus

- hypothesis: CONFIRMED — missing rebalance-back on restriction recovery.
- test: Add rebalance_on_attach call in the "free" branch; add regression test; run test-overlay.
- expecting: Recovered sender pulls a fair share of cold-pending backlog back.
- next_action: apply fix + test
