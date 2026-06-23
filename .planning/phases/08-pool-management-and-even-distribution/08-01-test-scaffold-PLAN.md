---
phase: 08-pool-management-and-even-distribution
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - tests/conftest.py
  - tests/test_pool_endpoints.py
  - tests/test_rebalance.py
autonomous: true
requirements: [POOL-01, POOL-02, POOL-03, POOL-04, POOL-05, POOL-06, POOL-06b, POOL-07, POOL-08, POOL-08b]
must_haves:
  truths:
    - "A test factory can seed message_queue + campaign_contact_assignments rows for a running campaign"
    - "Both new test files are collectable by pytest (no import errors)"
    - "Every POOL-01..08b requirement has a named test function that exists and runs (RED, expected-fail, until later waves land)"
  artifacts:
    - path: tests/conftest.py
      provides: "test_queue_item_factory fixture (seeds message_queue + optional CCA rows)"
      contains: "test_queue_item_factory"
    - path: tests/test_pool_endpoints.py
      provides: "POOL-01..06b integration test stubs"
      min_lines: 60
    - path: tests/test_rebalance.py
      provides: "POOL-07/08/08b integration test stubs"
      min_lines: 40
  key_links:
    - from: tests/test_rebalance.py
      to: tests/conftest.py::test_queue_item_factory
      via: "pytest fixture injection"
      pattern: "test_queue_item_factory"
---

<objective>
Create the Wave 0 test scaffolding so every Phase 8 behavior (POOL-01..08b) has an automated test slot BEFORE implementation lands. This closes the Nyquist Wave-0 gap: there is currently no fixture to seed `message_queue` rows, and the two test files do not exist.

Purpose: enables the feedback-sampling contract from 08-VALIDATION.md — every later task verifies against a real `pytest ... -x` command, not a "MISSING" placeholder.
Output: `tests/conftest.py` gains `test_queue_item_factory`; `tests/test_pool_endpoints.py` and `tests/test_rebalance.py` are created with named, collectable test functions.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/08-pool-management-and-even-distribution/08-RESEARCH.md
@.planning/phases/08-pool-management-and-even-distribution/08-PATTERNS.md
@.planning/phases/08-pool-management-and-even-distribution/08-VALIDATION.md

<interfaces>
<!-- Existing conftest fixtures the new factory and tests build on. Verified in source this session. -->
From tests/conftest.py (verified line numbers):
- async_client (conftest:198) — httpx AsyncClient against the app
- valid_supabase_jwt (conftest:206) — callable producing a signed JWT for auth headers
- test_workspace (conftest:349) — Workspace ORM row, has .id
- test_sender_factory (conftest:359) — async factory; kwargs role/slug/workspace_id; returns Sender
- test_campaign_factory (conftest:476) — async factory; returns Campaign
- test_contacts_factory (conftest:441) — uses defaults.update(overrides) + count pattern
- attach_sender_to_campaign (conftest:583) — raw-SQL INSERT into campaign_senders + ON CONFLICT DO NOTHING + commit
- test_running_campaign_factory (conftest:600) — returns (camp, senders); sender_count=N param

message_queue columns (from app/models/__init__.py::MessageQueue, recipient_phone at models:204):
  workspace_id, campaign_id, sender_id, recipient_phone, status, scheduled_at
campaign_contact_assignments columns (migration 016): workspace_id, campaign_id, contact_phone, sender_id
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add test_queue_item_factory fixture to conftest.py</name>
  <files>tests/conftest.py</files>
  <read_first>
    - tests/conftest.py:583-596 (attach_sender_to_campaign — raw-SQL INSERT + commit shape to mirror)
    - tests/conftest.py:441-470 (test_contacts_factory — count + defaults.update(overrides) override pattern)
    - tests/conftest.py:600 (test_running_campaign_factory — returns (camp, senders))
    - app/models/__init__.py around line 204 (MessageQueue.recipient_phone + status enum + column names — confirm exact names before writing INSERT)
    - 08-PATTERNS.md §"tests/conftest.py → test_queue_item_factory" (the analog factory skeleton)
  </read_first>
  <action>
    Add an async pytest fixture `test_queue_item_factory` following the EXACT style of `attach_sender_to_campaign` (conftest:583) — raw `text()` INSERT then `await async_db_session.commit()`. Inner callable signature: `_make(campaign_id, sender_id, recipient_phone, status="pending", *, with_cca=True, with_conversation=False, **overrides)`.
    - INSERT one row into `message_queue (workspace_id, campaign_id, sender_id, recipient_phone, status, scheduled_at)` with `scheduled_at = NOW()` and `workspace_id = test_workspace.id`.
    - When `with_cca=True`, also INSERT a matching `campaign_contact_assignments (workspace_id, campaign_id, contact_phone, sender_id)` row keyed on the same `recipient_phone`, using `ON CONFLICT (campaign_id, contact_phone) DO UPDATE SET sender_id = EXCLUDED.sender_id` (mirror the sticky upsert in rotation.py:150-163) so the sticky assignment matches the queue row — this is what the rebalance tests assert stays in sync.
    - When `with_conversation=True`, also INSERT a `conversations` row for `(workspace_id, contact_phone=recipient_phone)` so tests can mark a recipient as "engaged" (used by POOL-06b / POOL-08b). Confirm the conversations required columns from app/models before writing this INSERT.
    - Return the inner `_make`. Confirm `message_queue` column names against `app/models/__init__.py::MessageQueue` — do NOT guess column names.
    Do NOT touch any existing fixture or the conftest DB-guard (lines 46-77).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --collect-only -q 2>&1 | tail -20</automated>
  </verify>
  <acceptance_criteria>
    - `test_queue_item_factory` is defined as a `@pytest_asyncio.fixture` in tests/conftest.py.
    - The fixture INSERTs into `message_queue` with column names that match `app/models/__init__.py::MessageQueue` (no `column does not exist` error at collect/run time).
    - With `with_cca=True` it upserts a `campaign_contact_assignments` row keyed `(campaign_id, contact_phone)`.
    - `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --collect-only -q` completes with NO collection errors (exit 0 from the collect step).
    - No existing fixture body changed; conftest DB-guard (lines 46-77) untouched.
  </acceptance_criteria>
  <done>conftest exposes test_queue_item_factory; full test suite still collects without import/fixture errors.</done>
</task>

<task type="auto">
  <name>Task 2: Create tests/test_pool_endpoints.py with POOL-01..06b stubs</name>
  <files>tests/test_pool_endpoints.py</files>
  <read_first>
    - tests/test_campaign_router.py (endpoint integration style — async_client + Authorization header from valid_supabase_jwt; how it asserts status codes and JSON body)
    - tests/conftest.py:476 (test_campaign_factory), :359 (test_sender_factory), :600 (test_running_campaign_factory returns (camp, senders)), :583 (attach_sender_to_campaign)
    - 08-VALIDATION.md §"Per-Task Verification Map" (exact test-function names — they are contract: 8-att-01..8-det-04 map to these names)
    - 08-RESEARCH.md §"Validation Architecture → Phase Requirements → Test Map" (POOL-01..06b expected status codes + error codes)
  </read_first>
  <action>
    Create `tests/test_pool_endpoints.py` with these EXACT async test function names (names are the contract used by every later verify command):
    - `test_attach_adds_sender` (POOL-01): POST `/api/v1/campaigns/{id}/senders` body `{"sender_id": ...}` on a draft/paused/running campaign → 200, response `attached_senders` contains the sender, and a `campaign_senders` row exists.
    - `test_attach_locked_sender_409` (POOL-02): attach a sender already attached to ANOTHER running campaign → 409, `detail.code == "SENDER_LOCK_CONFLICT"`, `detail.conflicts` is a non-empty list of `{sender_id, campaign_id, campaign_name}`.
    - `test_attach_foreign_sender_404` (POOL-03): attach a sender_id not owned by the workspace → 404, `detail.code == "SENDER_NOT_FOUND"`; assert no foreign data leaked in body.
    - `test_detach_removes_sender` (POOL-04): DELETE `/api/v1/campaigns/{id}/senders/{sid}` → 200, `campaign_senders` row gone.
    - `test_detach_last_running_409` (POOL-05): detach the only sender of a RUNNING campaign → 409, `detail.code == "MIN_POOL_GUARD"`.
    - `test_detach_cold_pending_409` (POOL-06): sender has an un-sent cold pending queue row (seed via `test_queue_item_factory` with_cca=True, with_conversation=False) on a 2-sender running campaign → detach → 409, `detail.code == "DETACH_BLOCKED_PENDING"`.
    - `test_detach_engaged_only_ok` (POOL-06b): sender's only pending recipient also has a `conversations` row (seed with_conversation=True) on a 2-sender running campaign → detach → 200 (engaged dialogs do NOT block detach, D-05).
    Each test must be fully written to ASSERT the documented behavior (not `pass`/`xfail`) so it currently FAILS RED (endpoints don't exist yet) — that is the Wave-0 expectation. Use the auth header pattern from test_campaign_router.py. Add a module docstring mapping each test to its POOL-ID.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_endpoints.py --collect-only -q 2>&1 | tail -20</automated>
  </verify>
  <acceptance_criteria>
    - File defines exactly these 7 test functions by name: test_attach_adds_sender, test_attach_locked_sender_409, test_attach_foreign_sender_404, test_detach_removes_sender, test_detach_last_running_409, test_detach_cold_pending_409, test_detach_engaged_only_ok.
    - `pytest tests/test_pool_endpoints.py --collect-only -q` (via overlay) collects all 7 with NO import/collection errors.
    - Running them (via overlay) yields RED (assertion/HTTP 404 failures) — endpoints not implemented yet; this is expected and acceptable for Wave 0.
    - Module docstring maps each test → POOL-ID.
  </acceptance_criteria>
  <done>7 named, collectable, fully-asserting tests exist for POOL-01..06b; they fail RED pending Plan 02.</done>
</task>

<task type="auto">
  <name>Task 3: Create tests/test_rebalance.py with POOL-07/08/08b stubs</name>
  <files>tests/test_rebalance.py</files>
  <read_first>
    - tests/test_rotation_campaign.py (service-level integration style for rotation/assignment, the closest analog)
    - tests/conftest.py:600 (test_running_campaign_factory), and the new test_queue_item_factory (Task 1)
    - 08-RESEARCH.md §"Rebalance Algorithm" + §"Measurable acceptance signals" (evenness ±1 of total/P; idempotency 2nd call moves 0; never move sent/processing/engaged)
    - 08-VALIDATION.md §"Per-Task Verification Map" rows 8-reb-01..03 (exact test names)
  </read_first>
  <action>
    Create `tests/test_rebalance.py` with these EXACT async test function names:
    - `test_rebalance_evens_cold_pending` (POOL-07): running campaign, sender A holds N cold-pending rows (seed via test_queue_item_factory), sender B newly attached → after `rebalance_on_attach(campaign_id, B_id, db)` assert each pool sender's pending count is within ±1 of total/P via `SELECT sender_id, COUNT(*) FROM message_queue WHERE campaign_id=:cid AND status='pending' GROUP BY sender_id`.
    - `test_rebalance_idempotent` (POOL-08): call `rebalance_on_attach` twice; assert the second call moves 0 rows (return value/log count == 0) and the distribution is unchanged.
    - `test_rebalance_skips_non_cold` (POOL-08b): seed for one recipient a `sent` row AND a `conversations` row (with_conversation=True) plus a `processing` row; assert rebalance NEVER moves those — only true cold-pending rows move.
    Import the target as `from app.services.rebalance import rebalance_on_attach`. Tests must be fully asserting (RED now — the module does not exist yet). Add a module docstring mapping each test → POOL-ID. Also assert the CCA sync invariant in at least one test: after a move, the moved recipient's `campaign_contact_assignments.sender_id` equals the new sender (per D-08 Pitfall 3).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_rebalance.py --collect-only -q 2>&1 | tail -20</automated>
  </verify>
  <acceptance_criteria>
    - File defines exactly: test_rebalance_evens_cold_pending, test_rebalance_idempotent, test_rebalance_skips_non_cold.
    - Imports `rebalance_on_attach` from `app.services.rebalance`.
    - At least one test asserts the CCA-in-sync invariant (moved recipient's CCA.sender_id == new sender).
    - `pytest tests/test_rebalance.py --collect-only -q` (via overlay) collects all 3 with NO syntax/collection errors (an ImportError on `app.services.rebalance` at RUN time is acceptable Wave-0 RED, but the file itself must collect — guard the import so collection succeeds, e.g. import inside test bodies or rely on pytest collecting despite a top-level import error being a collection error: prefer importing inside each test function so --collect-only is clean).
    - Module docstring maps each test → POOL-ID.
  </acceptance_criteria>
  <done>3 named, fully-asserting rebalance tests exist; they fail/error RED pending Plan 03's rebalance.py.</done>
</task>

</tasks>

<threat_model>
ASVS L1 surface for this plan (test scaffolding):
- **T1 — Tests bypass the test-overlay and hit prod (V1 Architecture / data-integrity).** Mitigation: every `<verify>`/`<acceptance_criteria>` command in this plan uses the mandatory overlay `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest ...`. NEVER run bare `docker compose run --rm api pytest` (2026-05-26 DROP SCHEMA incident). The conftest DB-guard (lines 46-77) is left untouched as defence-in-depth — executor MUST NOT modify it.
- **T2 — Fixture seeds cross-workspace rows that mask isolation bugs (V4 Access Control).** Mitigation: `test_queue_item_factory` derives `workspace_id` from the `test_workspace` fixture only; tests asserting POOL-02/POOL-03 isolation must create the "other" workspace/campaign through existing factories, not by hand-writing a foreign workspace_id into the same campaign.
No new endpoints/auth introduced in this plan — full endpoint threat model lives in Plan 02.
</threat_model>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --collect-only -q` → 0 collection errors across the whole suite.
- All 10 named test functions exist (7 in test_pool_endpoints.py, 3 in test_rebalance.py) and map 1:1 to POOL-01..08b.
- conftest DB-guard and existing fixtures unchanged (git diff shows only an added fixture).
</verification>

<success_criteria>
- test_queue_item_factory present and usable (seeds message_queue + CCA + optional conversations).
- Both test files collect cleanly via the overlay; tests are fully-asserting RED stubs (no `pass`/`xfail`).
- Every POOL-01..08b requirement has a named test slot consumed by later waves' verify commands.
</success_criteria>

<output>
After completion, create `.planning/phases/08-pool-management-and-even-distribution/08-01-SUMMARY.md`
</output>
