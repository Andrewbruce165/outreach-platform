# Phase 07 — Deferred / Out-of-Scope Discoveries

Logged during execution of 07-01-PLAN.md. These are NOT caused by this plan's
3-file change (listener.py / rotation.py / two test files) — they pre-exist in
the test environment and are out of scope per the executor scope boundary.

## Full-suite failures unrelated to this plan (2026-06-23)

Running the full suite (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`)
reports **62 failed, 590 passed, 1 skipped, 20 errors**. None of the failing
files overlap with antispam/rotation logic; the three files this plan touches
pass 21/21 in isolation.

Representative root causes (sampled):

- `tests/test_migration_014.py`, `tests/test_onboarding_reauth.py` (20 errors):
  asyncpg `PostgresSyntaxError: cannot insert multiple commands into a prepared
  statement` at fixture setup — a multi-statement SQL string sent through a
  prepared-statement path. Test-infrastructure / fixture issue, not product code.
- `tests/test_phase5_inbox.py`, `test_phase5_inbox_send_takeover.py`,
  `test_phase5_bot_filter.py` etc.: `conversations_status_check` CHECK-constraint
  violation in the ephemeral test DB (status value not permitted by the CHECK as
  built in the test schema) → schema-drift between ORM create_all and the
  migrations' CHECK definition in the throwaway `outreach_test` DB.
- `tests/test_queue_per_campaign_hours.py`, `test_send_campaign.py`,
  `test_warmup_workspace_isolation.py`, `test_template_render.py`,
  `test_migration_012.py`, `test_migration_015.py`, several `test_phase5_1_*`:
  fail independently of the antispam/rotation change set.

These were present before this plan (the same schema-drift/asyncpg failure class
is environmental). They are logged here and intentionally NOT fixed under this
plan's scope. They should be triaged separately (likely a test-DB schema-build /
conftest migration-apply issue), not as part of the unified-freeze-policy phase.

## CR-01 — Existing-assignment routing to restricted sender → Phase 9 (2026-06-23)

Code review (07-REVIEW.md) flagged CR-01 as critical: `rotation.get_or_assign_sender`
Step 1 (`app/services/rotation.py:71-97`) computes `is_eligible` from only
`lifecycle_status` + `auth_status`, so a contact already pinned to a sender that
later becomes `spam_limited`/`frozen` keeps routing to that sender on the happy-path
early-return, never reaching the new Step 3 `restriction_status = 'none'` filter.

**Decision (user, 2026-06-23): defer to Phase 9 — Cold-Contact Failover.** Rationale:
- FRZ-04 as scoped covers **new cold contacts** — already closed by the Step 3 filter.
- Outbound queue sends to a restricted sender are already paused at `queue.py:401`.
- Reassigning *existing* (warm) assignments away from their sender on a soft 6h
  spam-limit would conflict with **FRZ-03** (established dialogues must keep flowing
  on the same sender). Failover of stuck pinned contacts to a healthy sender is the
  explicit remit of Phase 9 (Cold-Contact Failover), not this phase.

Carry into Phase 9: decide failover policy for already-assigned contacts whose sender
is restricted (reassign cold/un-started ones; keep established dialogues per FRZ-03).

### Minor review items (07-REVIEW.md) — not fixed this phase
- WR-01: `ai_enabled` left on rests on the documented FRZ-03 assumption (Telegram does
  not block replies in established dialogues under soft spam-limit). Accepted design.
- WR-03: `restricted_until` reset to now+6h on every inbound antispam message with no
  GREATEST guard — repeated unsolicited warnings could defer the reconcile clear.
  Minor robustness; mirrors PEER_FLOOD. Revisit if observed in prod.
- WR-02: `len(paused.fetchall())` vs `rowcount` — cosmetic.
- WR-04: tests rely on commit-visible fixture semantics (SUT opens own AsyncSessionLocal) — correct today.
- INFO: stale "auto-cancel"/"cancellation" wording in comments/logs/test docstring left from the retired terminal-fail design.
