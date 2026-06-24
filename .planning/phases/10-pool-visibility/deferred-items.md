# Phase 10 — Deferred / Out-of-Scope Items

Logged during execution per the executor scope-boundary rule. These are NOT caused
by Plan 10-02 changes and are NOT fixed here.

## Pre-existing full-suite failures (test-infra, NOT regressions from 10-02)

Discovered while running the full suite for wave-merge safety. All confirmed
unrelated to restriction-event wiring (none touch `sender_restriction_events`,
`record_restriction_event`, or the restriction write-points). Representative
classes:

- `tests/test_migration_012.py`, `test_migration_014.py`, `test_migration_015.py`
  — ERROR `asyncpg ... cannot insert multiple commands into a prepared statement`:
  these tests feed a multi-statement raw migration file to asyncpg as a single
  prepared statement. Test-harness limitation, predates Phase 10.
- `tests/test_phase5_inbox.py`, `test_phase5_inbox_send_takeover.py`,
  `test_phase5_llm_calls_endpoint.py`, `test_phase5_bot_filter.py`,
  `test_phase5_analytics_correctness.py`, `test_phase5_1_*` — `conversations`
  status CHECK violations / bot-filter setup errors. Phase 5 infra-gated set.
- `tests/test_onboarding_reauth.py`, `test_onboarding.py` — onboarding session
  setup errors (same multi-statement migration loading path).
- `tests/test_warmup_workspace_isolation.py`, `test_send_campaign.py`,
  `test_send.py`, `test_template_render.py`, `test_queue_per_campaign_hours.py`,
  `test_check_contacts.py`, `test_campaign_webhooks.py`, `test_agents.py` —
  pre-existing failures in unrelated subsystems.

These are tracked here for the verifier; they are out of scope for Plan 10-02
(scope boundary: only auto-fix issues DIRECTLY caused by this plan's changes).

## Fragile source-grep test (not in scope, not touched)

- `tests/test_listener_reconcile.py::test_get_active_senders_query_shape` asserts
  `"is_active" not in inspect.getsource(...)`, but the substring appears in an
  EXISTING explanatory comment ("is_active dropped — filter by lifecycle_status").
  Plan 10-02 did not modify `_get_active_senders` (verified via `git diff`).
  Pre-existing; the assertion needs to grep code not comments — left for the owner.

## Wave 3 (Plan 10-03) — expected RED here

- `tests/test_restriction_audit.py::test_history_endpoint` (HLTH-03) — the
  `GET /senders/{slug}/restriction-events` endpoint is built in Plan 10-03, not
  10-02. Correctly RED at this wave per prior_wave_context.
