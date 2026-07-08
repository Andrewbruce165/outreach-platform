---
phase: quick-260708-icz
plan: 01
subsystem: queue / follow-up restriction policy
tags: [peerflood, spam_limited, restriction, queue, follow_up]
requires:
  - senders.restriction_status ('none' | 'spam_limited' | 'frozen')
  - listener reconcile sweep (clears spam_limited after ~1h recheck)
provides:
  - spam_limited senders keep servicing started dialogs + follow-up pings
  - spam_limited senders open NO new dialogs until reconcile clears the flag
  - frozen precedence fully preserved (all writes blocked)
affects:
  - app/services/queue.py::_check_rate_limits
  - app/services/queue.py::_process_next_for_sender (pick SELECT)
  - app/services/queue.py PEER_FLOOD handler
  - app/services/follow_up.py::_ping
tech-stack:
  added: []
  patterns:
    - "restriction_status gate split: 'frozen' = stop everything; 'spam_limited' = stop only NEW dialogs"
key-files:
  created: []
  modified:
    - app/services/queue.py
    - app/services/follow_up.py
    - tests/test_sender_restriction.py
decisions:
  - "PeerFlood (spam_limited) is a back-off-new-outreach signal, not stop-everything: no more bulk +24h pause of pending rows"
  - "New-dialog suppression enforced structurally in the pick SELECT (s.restriction_status <> 'spam_limited'), NOT by skipping the tick"
  - "frozen keeps full block: tick skip + ping skip + ACCOUNT_FROZEN bulk +24h pause untouched"
metrics:
  duration: ~35min
  completed: 2026-07-08
---

# Quick 260708-icz: PeerFlood/spam_limited — keep messaging existing chats Summary

Reworked the `spam_limited` (PeerFlood) response so a flagged sender keeps replying in already-started dialogs and keeps sending follow-up pings, but opens no NEW dialogs until the ~1h reconcile sweep clears the flag. `frozen` (ACCOUNT_FROZEN) still blocks everything.

## What Changed

**4 edits across 2 service files + 1 test update (as specified in the plan — no redesign).**

### EDIT 1 — `queue.py::_check_rate_limits` (~line 588)
The early-return that skipped the whole tick for any `restriction_status != "none"` now fires ONLY on `restriction_status == "frozen"`. A `spam_limited` sender falls through to the normal 4/20/150 + 15/h rate-limit checks, so follow-ups keep flowing; new-dialog gating is enforced downstream in EDIT 2.

### EDIT 2 — `queue.py::_process_next_for_sender` pick SELECT (~line 466)
Added `JOIN senders s ON s.id = mq.sender_id` after the campaigns join, and ANDed `s.restriction_status <> 'spam_limited'` onto the NEW-DIALOG branch only. The follow-up `EXISTS(...)` branch is untouched (still always passes). Phase-12 cap and Phase-13 pace subqueries preserved verbatim. `FOR UPDATE OF mq SKIP LOCKED` unchanged (locks only mq, not the joined rows). Leading comment block updated.

### EDIT 3 — `queue.py` PEER_FLOOD handler (~line 1094)
Removed the bulk 24h reschedule of pending rows (`pause_until = now + 24h` line and the `UPDATE message_queue SET scheduled_at = :pause_until WHERE sender_id=:sid AND status='pending'` statement + its bind). Everything else kept: `recheck_at`, the `UPDATE senders SET restriction_status='spam_limited', restricted_until=:recheck_at`, `record_restriction_event(...)`, `failover_cold_backlog(...)`, `rollback_suspect_resolve_fails(...)`, callback fire, `_fail_item`. Branch comment + `logger.critical` message rewritten to state the pending queue is no longer bulk-paused. `db2` session retained for the senders UPDATE + audit event.

### EDIT 4 — `follow_up.py::_ping` (~line 227)
Skip guard changed from `if r.sender_restriction_status and r.sender_restriction_status != "none":` to `if r.sender_restriction_status == "frozen":`. D-14 comment updated: spam_limited no longer blocks follow-up pings; only frozen skips. Rest of `_ping` unchanged.

### TEST UPDATE — `tests/test_sender_restriction.py::test_queue_pre_send_skips_restricted`
Now asserts `'restriction_status == "frozen"' in src` AND `'restriction_status != "none"' not in src` of `_check_rate_limits`. Docstring updated to describe frozen-only semantics.

Untouched by design (confirmed): `test_check_rate_limits_untouched`, `test_peer_flood_sets_one_hour_recheck_with_matching_audit` (still passes — ACCOUNT_FROZEN handler keeps its `timedelta(hours=24)`), `test_paused_frozen` (paused campaign, not restricted sender). ACCOUNT_FROZEN handler, FLOOD_WAIT handler, and all empirical rate-limit constants/intervals untouched (CLAUDE.md guard). No schema change / no migration.

## Frozen Precedence (verification)
- `frozen`: `_check_rate_limits` skips the whole tick → never reaches the pick SELECT; `_ping` skips; ACCOUNT_FROZEN handler still bulk-pauses all pending +24h. Full stop preserved.
- `spam_limited`: tick proceeds; pick SELECT follow-up branch passes, new-dialog branch blocked by `s.restriction_status <> 'spam_limited'`; FollowUpWorker pings still enqueue; PEER_FLOOD handler flags spam_limited + audit + failover + rollback + callback + _fail_item, no bulk +24h reschedule. New-contact sends resume automatically once the ~1h reconcile sweep clears the flag (no code re-enable).

## Tests
Ran via the test overlay (never plain pytest — prod DROP SCHEMA guard):
```
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --no-deps api pytest \
  tests/test_sender_restriction.py tests/test_follow_up.py \
  tests/test_queue_new_dialog_limit.py tests/test_restriction_audit.py \
  -k "restrict or peer_flood or frozen or new_dialog or ping or followup or rate_limits" -q
```
Result: **40 passed, 4 deselected** (targeted subset only — full suite is known RED/order-dependent per project memory).

**Overlay-in-worktree notes (for reproducibility):**
- Host Docker address pool was exhausted (`all predefined address pools have been fully subnetted`). Removed 16 stale empty `agent-*_default` networks (0 containers, leftovers from completed worktree sessions) to free pool space. Did NOT prune host-wide.
- The base `api` service `depends_on: db`, whose hardcoded `container_name: outreach-platform-db` collides globally with the running prod container. Worked around by starting only the ephemeral `db-test` (`up -d db-test`) then running `api` with `--no-deps` (DATABASE_URL is still the overlay's `db-test/outreach_test` literal, so tests hit the ephemeral tmpfs DB, not prod).
- Worktree has no `.env` (gitignored); passed `--env-file /root/apps/aimly/tg-outreach/.env` for compose `${VAR}` interpolation only. The overlay's literal `DATABASE_URL` override still points at `db-test`, so prod was never touched.
- Torn down afterward: removed the ephemeral `db-test` container, my `agent-aa859ad96ea588c51_default` network, and the `agent-aa859ad96ea588c51_postgres_data` volume.

## Deviations from Plan
None — plan executed exactly as written (pre-approved 4-edit spec).

## Deploy — PENDING (must run on main tree after merge)
Deploy was intentionally NOT run from the worktree: `docker compose up -d --build api/listener` rebuilds the SHARED prod containers, out of scope for an isolated worktree executor. After these commits are merged to main, run on the prod tree:
```
cd /root/apps/aimly/tg-outreach && git pull
docker compose up -d --build api
docker compose up -d --build listener
```
Then confirm both containers healthy. On the next live PeerFlood the account should keep replying in started dialogs + keep sending follow-up pings while opening no new dialogs until the ~1h reconcile clears the flag; a frozen account should stop everything.

## Commits
- `f39a4f4` feat(260708-icz): scope spam_limited to new contacts in queue.py
- `d8cb080` feat(260708-icz): keep follow-up pings for spam_limited senders

## Self-Check: PASSED
All 3 modified files and both commits (f39a4f4, d8cb080) verified present.
