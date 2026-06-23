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
