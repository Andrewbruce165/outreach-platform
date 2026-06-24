---
phase: 10-pool-visibility
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - tests/test_restriction_audit.py
  - tests/test_pool_health.py
autonomous: true
requirements: [HLTH-01, HLTH-02, HLTH-03, POOLV-01, POOLV-02]
must_haves:
  truths:
    - "Two RED test files exist and import cleanly (--collect-only is green; runtime imports fail because the implementation is not built yet)"
    - "Every HLTH-01/02 + D-03 + HLTH-03 + POOLV-01/02 behavior from VALIDATION.md has a named test stub"
  artifacts:
    - path: "tests/test_restriction_audit.py"
      provides: "RED stubs for event writes, append-only, no-shift suppression, slice, proxy, category separation, history endpoint"
      contains: "import-inside-body"
      min_lines: 80
    - path: "tests/test_pool_health.py"
      provides: "RED stubs for pool_health 3-state arithmetic + attached_senders enrichment"
      contains: "test_pool_health_states"
      min_lines: 50
  key_links:
    - from: "tests/test_restriction_audit.py"
      to: "app.services.restriction_audit.record_restriction_event"
      via: "import-inside-body (RED until Wave 2)"
      pattern: "from app.services.restriction_audit import"
    - from: "tests/test_pool_health.py"
      to: "conftest test_running_campaign_factory + messages_log seed"
      via: "fixture reuse"
      pattern: "test_running_campaign_factory"
---

<objective>
Создать Wave-0 RED-тест-скелет для всей фазы 10: два файла тестов, покрывающих restriction-audit (HLTH-01/02 + D-03 + HLTH-03) и pool-visibility (POOLV-01/02). Тесты пишутся ДО реализации (import-inside-body — `--collect-only` чистый, в runtime падают, пока Wave 2/3 не построит код). Это контракт, по которому проверяются последующие планы.

Purpose: Закрепить ожидаемое поведение явными утверждениями до имплементации (паттерн Phase 8/9 — сначала Wave 0 test-scaffold, потом imp-планы). Каждый тест соответствует строке VALIDATION.md → Per-Task Verification Map.
Output: tests/test_restriction_audit.py, tests/test_pool_health.py (оба RED).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/10-pool-visibility/10-VALIDATION.md
@.planning/phases/10-pool-visibility/10-PATTERNS.md
@.planning/phases/10-pool-visibility/10-RESEARCH.md

<interfaces>
<!-- Contracts the test stubs assert against. Implemented in Wave 2/3 — tests are RED until then. -->

Helper (Wave 2, app/services/restriction_audit.py):
```python
async def record_restriction_event(
    sender_id: UUID, event_type: str, source: str,
    restricted_until: datetime | None, raw_text: str | None,
    category: str = "restriction", db: AsyncSession | None = None,
) -> None
```
event_type ∈ {spam_limited, frozen, flood_wait, cleared, banned, extension, privacy_restricted}
source ∈ {queue_error, spambot_reconcile, antispam_signal}
category ∈ {restriction, recipient_privacy}

Table (Wave 2, migration 030): sender_restriction_events
(id, workspace_id, sender_id, category, event_type, source, restricted_until, raw_text, activity_slice JSONB, proxy JSONB, created_at)

activity_slice JSONB shape: {sends_1h, sends_24h, unique_contacts_1h, unique_contacts_24h, rate:{configured_per_min/hour/day, actual_per_hour, actual_per_day}}

Read endpoint (Wave 3): GET /senders/{slug}/restriction-events → list[RestrictionEventResponse] newest-first, workspace-scoped.

CampaignResponse additions (Wave 3): pool_health = {active, paused, total, earliest_resume_at}; attached_senders[].restriction_status + .restricted_until.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: tests/test_restriction_audit.py — RED stubs for HLTH-01/02 + D-03 + HLTH-03</name>
  <read_first>
    - tests/test_failover.py (import-inside-body RED stub pattern + module docstring test→req map format, L1-70; _freeze_sender helper L62-70) — PRIMARY analog per 10-PATTERNS.md §tests
    - tests/conftest.py (fixtures: async_db_session L187, test_running_campaign_factory L703, test_queue_item_factory L600)
    - tests/test_rebalance.py (factory usage L51, L102-105)
    - app/models/__init__.py L108-124 (messages_log columns: workspace_id, sender_id, recipient_phone, message_text, message_type {sent,failed}, created_at — to seed slice rows)
    - .planning/phases/10-pool-visibility/10-VALIDATION.md (Per-Task Verification Map rows HLTH-01a..e, HLTH-02a..c, D-03, HLTH-03 — one test per row)
    - .planning/phases/10-pool-visibility/10-RESEARCH.md §Activity-Slice Design (slice JSONB shape) + §Write-Point Inventory (D-01 extension gate)
  </read_first>
  <action>
    Create tests/test_restriction_audit.py. Module-level: `import pytest`, `from sqlalchemy import text`, `pytestmark = pytest.mark.asyncio`, module docstring with a test→requirement map (mirror tests/test_failover.py:19-31 format). Each test does `from app.services.restriction_audit import record_restriction_event` INSIDE the body (RED until Wave 2 — keeps `--collect-only` clean). Reuse conftest fixtures `async_db_session`, `test_running_campaign_factory`, `test_queue_item_factory`; copy a local `_freeze_sender(db, sender_id, status, until)` helper from test_failover.py:62-70. For slice tests, seed `messages_log` rows directly via `async_db_session.execute(text(...))` with `message_type='sent'` and windowed `created_at`.

    Write these named test stubs (fully-asserting, RED at runtime) — one per VALIDATION.md row:
    - `test_peer_flood_writes_event` (HLTH-01): calling record_restriction_event(sender_id, "spam_limited", "queue_error", recheck_at, raw, db=session) then COMMIT inserts exactly one row in sender_restriction_events with category='restriction', event_type='spam_limited', source='queue_error'.
    - `test_reconcile_cleared_writes_event` (HLTH-01): event_type='cleared', source='spambot_reconcile', restricted_until IS NULL.
    - `test_reconcile_no_shift_no_event` (HLTH-01, D-01): the gate logic — when new restricted_until is NOT a meaningful forward shift over old, NO 'extension' row is written. Assert COUNT(*) WHERE event_type='extension' == 0 after a no-shift recheck simulation.
    - `test_reconcile_shift_writes_extension` (HLTH-01, D-01): when new restricted_until > old + 1 minute, exactly ONE 'extension' row appears.
    - `test_events_append_only` (HLTH-01): after writing spam_limited then cleared for the same sender, BOTH rows still exist (COUNT==2); no UPDATE/DELETE collapses history.
    - `test_event_carries_activity_slice` (HLTH-02): after seeding N 'sent' messages_log rows in the last hour, a restriction event row's `activity_slice` JSONB has `sends_1h`==N and a `rate` object with configured_per_min/hour/day from senders.
    - `test_event_carries_proxy_snapshot` (HLTH-02): with senders.proxy set, the event row's `proxy` JSONB column equals that proxy.
    - `test_slice_windows_sent_only` (HLTH-02): seed both 'sent' and 'failed' rows; `sends_1h` counts only message_type='sent'; windowing by created_at is correct (rows older than 1h excluded from sends_1h but present in sends_24h).
    - `test_recipient_privacy_separate_category` (D-03): record_restriction_event(..., event_type="privacy_restricted", category="recipient_privacy") inserts a row with category='recipient_privacy'; the sender's restriction_status is unchanged (still 'none'); a `WHERE category='restriction'` filter excludes this row.
    - `test_history_endpoint` (HLTH-03): GET /senders/{slug}/restriction-events (via httpx AsyncClient with auth — mirror an existing senders integration test) returns the workspace's events newest-first; a sender from another workspace is NOT visible (workspace-scoped).

    No fenced implementation in this PLAN — copy the stub body shape from test_failover.py per read_first. Assertions must be real (no `assert True` placeholders), but the production symbol they call does not exist yet → RED at runtime, clean at collect.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_restriction_audit.py --collect-only</automated>
  </verify>
  <acceptance_criteria>
    - `pytest tests/test_restriction_audit.py --collect-only` (test-overlay) exits 0 and lists all 10 named tests above (no collection errors).
    - Running `pytest tests/test_restriction_audit.py -x` (test-overlay) FAILS (RED) with ImportError/ModuleNotFoundError on `app.services.restriction_audit` — proving the import is inside test bodies and the implementation is not yet present.
    - Module docstring contains a test→requirement map (HLTH-01/02, D-03, HLTH-03).
    - `grep -c "from app.services.restriction_audit import" tests/test_restriction_audit.py` returns the per-test count (import appears inside each test body, not at module top).
  </acceptance_criteria>
  <done>tests/test_restriction_audit.py exists with 10 fully-asserting RED stubs; --collect-only green; runtime RED on missing implementation.</done>
</task>

<task type="auto">
  <name>Task 2: tests/test_pool_health.py — RED stubs for POOLV-01/02</name>
  <read_first>
    - tests/test_pool_endpoints.py (campaign-response integration test pattern + auth client + factory usage) — analog per 10-PATTERNS.md
    - tests/test_rebalance.py (test_running_campaign_factory usage L102-105)
    - tests/conftest.py (test_running_campaign_factory L703 returns camp, senders; per-test JWT sub note from STATE.md Phase 08-03)
    - tests/test_failover.py L62-70 (_freeze_sender helper — UPDATE senders.restriction_status/restricted_until)
    - .planning/phases/10-pool-visibility/10-RESEARCH.md §Pattern 3 (pool_health aggregate SQL + 3-state mapping)
    - .planning/phases/10-pool-visibility/10-PATTERNS.md §MOD campaigns.py (_build_attached_senders / _campaign_to_response enrichment targets)
    - app/schemas/__init__.py L685-729 (CampaignResponse computed fields — target shape)
  </read_first>
  <action>
    Create tests/test_pool_health.py. Module-level: `import pytest`, `from sqlalchemy import text`, `pytestmark = pytest.mark.asyncio`, docstring with test→requirement map (POOLV-01/02). Use `test_running_campaign_factory(sender_count=...)` to build a campaign with an attached pool; copy a local `_freeze_sender(db, sender_id, status, until)` from test_failover.py:62-70 to force restriction states. Fetch the campaign via the GET-campaign endpoint (httpx AsyncClient + auth, mirror test_pool_endpoints.py) so `pool_health` and enriched `attached_senders` are exercised through the real `_campaign_to_response`.

    Test stubs:
    - `test_pool_health_states` (POOLV-01): build a 3-sender pool. (a) all active → pool_health == {active:3, paused:0, total:3, earliest_resume_at:None}. (b) freeze one (spam_limited, restricted_until=T) → {active:2, paused:1, total:3, earliest_resume_at:T}. (c) freeze all three with distinct restricted_until → {active:0, paused:3, total:3, earliest_resume_at: MIN(...)}. Assert the numeric contract; the green/yellow/red badge is derived ON THE FRONTEND (no badge_state field in API).
    - `test_attached_senders_enriched` (POOLV-02): after freezing one sender, the campaign response's `attached_senders[]` entry for that sender carries `restriction_status='spam_limited'` and the matching `restricted_until`; active senders carry `restriction_status='none'` and `restricted_until=None`.

    Assertions are real and reference `pool_health` / `attached_senders[].restriction_status` keys that do not exist in the response yet (Wave 3) → RED at runtime, clean at collect. No fenced implementation in PLAN — copy shape from test_pool_endpoints.py.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_health.py --collect-only</automated>
  </verify>
  <acceptance_criteria>
    - `pytest tests/test_pool_health.py --collect-only` (test-overlay) exits 0 and lists `test_pool_health_states` and `test_attached_senders_enriched`.
    - `pytest tests/test_pool_health.py -x` (test-overlay) FAILS (RED) — the campaign response has no `pool_health` key / `attached_senders[].restriction_status` yet (KeyError/AttributeError/assertion failure).
    - Tests use `test_running_campaign_factory` and a `_freeze_sender` helper (no reinvented fixtures): `grep -q "test_running_campaign_factory" tests/test_pool_health.py` succeeds.
  </acceptance_criteria>
  <done>tests/test_pool_health.py exists with 2 fully-asserting RED stubs; --collect-only green; runtime RED on missing pool_health/enrichment.</done>
</task>

</tasks>

<threat_model>
ASVS L1 (block_on=high). This plan creates TEST FILES ONLY — no production endpoints, no data writes to prod.
- **Prod-DB protection:** tests MUST run via test-overlay only (`docker-compose.test.yml`, ephemeral `db-test` in tmpfs). NEVER bare `docker compose run --rm api pytest` (conftest DROP SCHEMA → prod, 2026-05-26 incident). NEVER `down -v`.
- **Workspace isolation assertion baked into tests:** `test_history_endpoint` MUST assert a foreign-workspace sender's events are NOT returned — encoding the cross-tenant-leak guard that Wave 3's endpoint must satisfy.
- No secrets in test fixtures (use factory-generated JWT subs as in Phase 08-03).
</threat_model>

<verification>
- Both files collect cleanly under test-overlay (`--collect-only` exits 0).
- Both files are RED at runtime (implementation absent) — confirms genuine TDD scaffold, not vacuous passes.
- All 12 VALIDATION.md Per-Task rows (HLTH-01a..e, HLTH-02a..c, D-03, HLTH-03, POOLV-01, POOLV-02) have a corresponding named test.
</verification>

<success_criteria>
- tests/test_restriction_audit.py: 10 named RED stubs, import-inside-body, fully-asserting.
- tests/test_pool_health.py: 2 named RED stubs (3-state + enrichment), uses existing factories.
- `--collect-only` green for both; `-x` run RED for both. No watch-mode flags. Feedback < 90s.
</success_criteria>

<output>
After completion, create `.planning/phases/10-pool-visibility/10-01-SUMMARY.md`
</output>
