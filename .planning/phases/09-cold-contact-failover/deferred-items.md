# Deferred Items — Phase 09 (cold-contact-failover)

Out-of-scope discoveries logged by the 09-02 executor. NOT fixed here (scope
boundary: only auto-fix issues directly caused by the current task's changes).

## Pre-existing full-suite failures (63 failed + 20 errors)

**Found during:** 09-02 Task 3 full-suite green gate.

**Status:** PRE-EXISTING — proven not caused by this plan.

**Proof:** Ran the full suite at the clean base commit `4de6583` (this plan's
base, with `tests/test_failover.py` excluded). Result was identical:
`63 failed, 608 passed, 1 skipped, 20 errors`. With this plan's work applied the
result is `63 failed, 620 passed, 1 skipped, 20 errors` — i.e. this plan added
exactly +12 passing tests (the 9 unit + 3 FAIL-02 failover tests) and changed
ZERO of the pre-existing failures. None of the failing files were touched by this
plan (`git diff 4de6583 HEAD` only touches `app/services/failover.py`,
`app/services/queue.py` freeze blocks, `app/services/listener.py` antispam handler,
and `tests/test_failover.py`).

**Failing files (baseline == post-plan, unchanged):**
- test_onboarding_reauth.py (10 errors), test_migration_014.py (10), test_phase5_inbox.py (9)
- test_queue_per_campaign_hours.py (6), test_phase5_inbox_send_takeover.py (6)
- test_phase5_llm_calls_endpoint.py (5), test_warmup_workspace_isolation.py (4)
- test_send_campaign.py (4), test_phase5_bot_filter.py (4), test_phase5_1_funnel.py (3)
- test_migration_012.py (3), test_template_render.py (2), test_send.py (2)
- test_phase5_1_auth_unchanged.py (2), test_onboarding.py (2), test_migration_015.py (2)
- 9 further single-failure files (analytics, llm_aggregates, cors, agents_v2, listener_reconcile, check_contacts, campaign_webhooks, agents, full_suite)

**Observed root-cause categories (samples):**
- `test_send.py` / `test_send_campaign.py`: 422 VALIDATION_ERROR — the `/api/v1/send`
  schema now requires `campaign_id` but these tests still POST without it (stale
  test vs. current API contract).
- `test_migration_014.py` / `test_onboarding_reauth.py`: SQLAlchemy setup ERRORs
  (fixture/migration-state related).
- Many phase5 / phase5_1 failures: API-contract / schema drift unrelated to failover.

**Recommendation:** These are a separate test-maintenance / schema-drift cleanup
effort. They predate Phase 09 and should be triaged as their own quick-task or
debug cycle (the verifier should confirm the 63/20 baseline rather than treat it
as a 09-02 regression).
