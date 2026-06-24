---
phase: 09-cold-contact-failover
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - tests/test_failover.py
  - tests/conftest.py
autonomous: true
requirements: [FAIL-01, FAIL-02, FAIL-03, FAIL-04, FAIL-05, FAIL-06, FAIL-07, FAIL-08]
nyquist_compliant: true

must_haves:
  truths:
    - "tests/test_failover.py collects cleanly (--collect-only has 0 import errors) while failover.py does not yet exist"
    - "Every FAIL-0x requirement has a named, fully-asserting RED test that fails only because app.services.failover is missing"
    - "An empty conversation (created, zero messages) is reproducible as a fixture state distinct from an engaged (has-message) conversation"
  artifacts:
    - path: "tests/test_failover.py"
      provides: "10 RED test stubs mapping FAIL-01..FAIL-08, import-inside-body pattern"
      contains: "from app.services.failover import failover_cold_backlog"
      min_lines: 120
    - path: "tests/conftest.py"
      provides: "test_queue_item_factory extended with with_message flag for has-message conversation"
      contains: "with_message"
  key_links:
    - from: "tests/test_failover.py"
      to: "app.services.failover.failover_cold_backlog"
      via: "import inside each test body (not module top-level)"
      pattern: "from app.services.failover import failover_cold_backlog"
    - from: "tests/test_failover.py"
      to: "tests/conftest.py fixtures"
      via: "test_running_campaign_factory / test_queue_item_factory params"
      pattern: "test_running_campaign_factory|test_queue_item_factory"
---

<objective>
Create the Wave 0 RED test scaffold for cold-contact failover BEFORE any implementation exists. Produces `tests/test_failover.py` with fully-asserting test stubs (one per FAIL-0x behavior) and the one conftest fixture extension needed to distinguish an empty conversation (D-05, movable) from an engaged conversation (FAIL-05, not movable).

Purpose: Lock the failover contract as executable tests so the implementation plan (09-02) is graded against red→green, not against prose. The import-inside-body pattern (mirrored from `tests/test_rebalance.py:51`) keeps `pytest --collect-only` clean while `app/services/failover.py` does not yet exist.
Output: `tests/test_failover.py` (collects clean, all tests RED), `tests/conftest.py` (with_message flag added to test_queue_item_factory).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/09-cold-contact-failover/09-CONTEXT.md
@.planning/phases/09-cold-contact-failover/09-RESEARCH.md
@.planning/phases/09-cold-contact-failover/09-PATTERNS.md
@.planning/phases/09-cold-contact-failover/09-VALIDATION.md
@tests/test_rebalance.py
@tests/conftest.py

<interfaces>
<!-- The contract the RED tests assert against. failover.py does NOT exist yet -->
<!-- these tests fail on ImportError until 09-02 ships it. Signature is fixed here. -->

Helper under test (to be implemented in 09-02, app/services/failover.py):
```python
async def failover_cold_backlog(
    frozen_sender_id: UUID,
    db: AsyncSession | None = None,
) -> int:
    """Move the frozen sender's cold-pending backlog onto healthy pool senders.
    db is None  → helper opens+commits its OWN session (queue.py callers).
    db passed   → transaction-neutral, caller commits (listener antispam path).
    Returns total rows moved (0 if nothing movable or no healthy receiver — D-13).
    """
```

Helpers to COPY VERBATIM from tests/test_rebalance.py:26-41:
```python
async def _pending_counts(db, campaign_id):  # {sender_id: count of pending mq rows}
async def _cca_sender_for(db, campaign_id, contact_phone):  # CCA.sender_id for a phone
```

Fixtures to REUSE (tests/conftest.py):
- test_running_campaign_factory(sender_count=N) (conftest.py:~680) — campaign + N attached active senders
- test_queue_item_factory (conftest.py:~600) — inserts message_queue row, supports with_conversation
- async_db_session
</interfaces>
</context>

<threat_model>
ASVS L1. This plan adds TEST code only — no production surface, no endpoints, no user input. Threats:
- **Prod-DB wipe via wrong pytest invocation (HIGH if mishandled, mitigated):** Tests MUST run only via the test-overlay (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`). The conftest guard (tests/conftest.py:46-77) already blocks the bare command; do NOT weaken or bypass it. No new prod-touching code here.
- **PII in test fixtures (LOW):** test phones are synthetic factory values, never real numbers; no logging assertions print PII.
No HIGH-severity production threat introduced. Cleared.
</threat_model>

<tasks>

<task type="auto">
  <name>Task 1: Extend test_queue_item_factory with a with_message flag</name>
  <files>tests/conftest.py</files>
  <read_first>
    - tests/conftest.py — read the WHOLE test_queue_item_factory (~conftest.py:599-674) incl. its with_conversation branch (~663-672); read test_conversation_factory (~696) and test_running_campaign_factory (~680) to learn the insert style; read migration 017 messages-table columns by grepping `migrations/017_phase5.sql` for the `messages` CREATE TABLE (columns: id, conversation_id NOT NULL, direction VARCHAR(20), sent_by, content/body, created_at).
    - app/models/__init__.py:108-127 (messages_log) and the migration 017 messages table — confirm `messages` is keyed by conversation_id and has NO recipient_phone.
  </read_first>
  <action>
    In tests/conftest.py, extend the existing `test_queue_item_factory` fixture: add an optional keyword `with_message: bool = False`. Behavior:
    - When `with_conversation=True` (existing branch that inserts a `conversations` row) AND `with_message=True`, additionally INSERT one row into the `messages` table after the conversation insert, using the conversation's id as `conversation_id`, `direction='inbound'` (a received reply = engaged), and whatever NOT NULL columns migration 017 requires (sent_by, content/body, created_at NOW()). Use a raw `text()` INSERT consistent with the surrounding fixture style.
    - When `with_message=False` (default) leave behavior unchanged: `with_conversation=True` still produces an EMPTY conversation (zero messages) — this is the D-05 movable case and MUST remain producible.
    Do NOT change any existing parameter defaults or signatures elsewhere. The empty-conversation case must stay reproducible exactly as today.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --collect-only -q 2>&1 | tail -5</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "with_message" tests/conftest.py` shows the new keyword in test_queue_item_factory's signature and an `INSERT INTO messages` guarded by it.
    - `--collect-only` exits 0 with no collection/import errors (the full suite, ~683+ tests, still collects).
    - Existing rebalance/pool tests are untouched: `git diff --stat tests/conftest.py` shows only additions inside test_queue_item_factory (no edits to test_running_campaign_factory or other fixtures).
  </acceptance_criteria>
  <done>test_queue_item_factory accepts with_message=True and inserts a messages row; empty-conversation case still producible with with_conversation=True, with_message=False; collection clean.</done>
</task>

<task type="auto">
  <name>Task 2: Write tests/test_failover.py RED stubs (FAIL-01..FAIL-08)</name>
  <files>tests/test_failover.py</files>
  <read_first>
    - tests/test_rebalance.py (whole file, 1-204) — clone module header (`pytestmark = pytest.mark.asyncio`), the import-inside-body pattern at line 51, and copy `_pending_counts`/`_cca_sender_for` (lines 26-41) verbatim.
    - .planning/phases/09-cold-contact-failover/09-RESEARCH.md §"Phase Requirements → Test Map" and §"D-06 Resolution" — the exact movable predicate and the FAIL→test mapping.
    - .planning/phases/09-cold-contact-failover/09-PATTERNS.md §"tests/test_failover.py" — the test→requirement table and fixture-reuse notes.
    - tests/conftest.py — test_running_campaign_factory(sender_count=N), test_queue_item_factory (now with with_message), async_db_session.
  </read_first>
  <action>
    Create tests/test_failover.py modeled on test_rebalance.py. Module top: `import pytest`, `from sqlalchemy import text`, `pytestmark = pytest.mark.asyncio`. Copy `_pending_counts` and `_cca_sender_for` verbatim from test_rebalance.py. Every test imports the helper INSIDE its body: `from app.services.failover import failover_cold_backlog` (so --collect-only stays clean while the module is absent). Write these FULLY-ASSERTING tests (no `pass`, no `pytest.skip`) — they must be RED only because failover.py is missing:

    - `test_failover_spreads_to_healthy_pool` (FAIL-01): running campaign, sender_count>=3, one sender frozen (restriction_status set, its cold pending paused +24h), N cold-pending rows on it. Assert after call: those rows' sender_id distributed across the >=2 healthy senders (more than one distinct receiver when N>=2), return value == N moved.
    - `test_failover_excludes_frozen_as_receiver` (FAIL-01/D-09, Pitfall 1): pool of exactly the frozen + healthy senders where frozen has stale CCA rows pointing at itself. Assert NO moved row's new sender_id equals the frozen sender id; frozen sender holds 0 movable rows afterward.
    - `test_failover_skips_engaged` (FAIL-03): mix of cold-pending rows AND rows whose contact has a sent/processing queue row OR an engaged conversation (with_message=True). Assert only the cold ones moved; engaged ones keep sender_id == frozen.
    - `test_failover_moves_empty_conversation` (FAIL-03/D-05): cold-pending row whose contact has a conversation with ZERO messages (with_conversation=True, with_message=False). Assert the row IS moved (empty conversation is still cold).
    - `test_failover_cca_in_sync` (FAIL-04): assert for every moved row, `campaign_contact_assignments.sender_id` for that (campaign_id, contact_phone) equals the moved row's new message_queue.sender_id; and moved rows have scheduled_at <= NOW() (not the +24h freeze value).
    - `test_failover_leaves_engaged` (FAIL-05): contact who already exchanged a message (with_message=True) — assert its pending row stays on the frozen sender, not moved.
    - `test_failover_idempotent` (FAIL-06): call twice; second call returns 0 and changes nothing (counts identical to after first call).
    - `test_failover_no_receiver_keeps_paused` (FAIL-07/D-13): sender_count==1 (only the frozen sender, no healthy receiver) → return 0, rows stay on frozen sender with their paused scheduled_at unchanged.
    - `test_failover_logs_count_no_pii` (FAIL-08): use `caplog` at INFO; after a real move assert the log text contains the moved COUNT and sender UUID(s) but does NOT contain any recipient phone string used in the fixture (assert the phone substring not in caplog.text).

    Each test should set up state via the conftest factories and raw `text()` UPDATEs (mirror test_rebalance.py for the freeze setup: set senders.restriction_status + push pending scheduled_at +24h). Do NOT write the integration call-site tests here (FAIL-02 lives in 09-02 alongside the call-site edits).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_failover.py -x 2>&1 | tail -15</automated>
  </verify>
  <acceptance_criteria>
    - `pytest --collect-only tests/test_failover.py` lists exactly these 9 tests with ZERO import errors (helper import is inside bodies).
    - Running `pytest tests/test_failover.py -x` (overlay) fails with `ModuleNotFoundError`/`ImportError` for `app.services.failover` (RED for the right reason — not a fixture/syntax error).
    - `grep -c "def test_failover" tests/test_failover.py` == 9; no `pytest.skip` and no bare `pass` body (`grep -n "pytest.skip\|^    pass$" tests/test_failover.py` returns nothing).
    - `_pending_counts` and `_cca_sender_for` present (`grep -n "_pending_counts\|_cca_sender_for" tests/test_failover.py`).
  </acceptance_criteria>
  <done>tests/test_failover.py collects clean, 9 fully-asserting tests all RED due to missing app.services.failover, helpers copied from test_rebalance.py, FAIL-01/03/04/05/06/07/08 covered.</done>
</task>

</tasks>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --collect-only -q` exits 0 (full suite collects, no errors).
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_failover.py -x` fails ONLY on ImportError of app.services.failover (correct RED).
- No production code modified (`git diff --stat app/` empty).
</verification>

<success_criteria>
- tests/test_failover.py exists with 9 RED test stubs covering FAIL-01, FAIL-03, FAIL-04, FAIL-05, FAIL-06, FAIL-07, FAIL-08 (FAIL-02 deferred to 09-02 call-site tests).
- conftest test_queue_item_factory gains with_message flag; empty-conversation case preserved.
- --collect-only clean; tests RED for the right reason.
</success_criteria>

<output>
After completion, create `.planning/phases/09-cold-contact-failover/09-01-SUMMARY.md`.
</output>
