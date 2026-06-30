---
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - tests/test_checker.py
  - tests/test_send.py
  - tests/test_contact_check_worker.py
  - tests/test_restriction_audit.py
autonomous: true
requirements: [SRLD-01, SRLD-02, SRLD-03, SRLD-04, SRLD-05, SRLD-06, SRLD-07, SRLD-08]
must_haves:
  truths:
    - "Full test suite still collects with 0 errors after the new RED stubs are added"
    - "Each new test targets a SRLD requirement and FAILS for the right reason (behavior not yet built), not on import/collection"
  artifacts:
    - path: "tests/test_checker.py"
      provides: "RED stubs: SRLD-01 username_capture, SRLD-07 confidence_gated_cache (checker read)"
      contains: "username_capture"
    - path: "tests/test_send.py"
      provides: "RED stubs: SRLD-03 resolve_ladder, SRLD-04 import_gate, SRLD-05 lazy_import, SRLD-06 stale_username_fallthrough, SRLD-07 confidence_gated_cache (sender read)"
      contains: "stale_username_fallthrough"
    - path: "tests/test_contact_check_worker.py"
      provides: "RED stub: SRLD-02 captured_username persisted to tg_username_resolved"
      contains: "captured_username"
    - path: "tests/test_restriction_audit.py"
      provides: "RED stubs: SRLD-08 blocked event_type insert + block-rate aggregate"
      contains: "blocked"
  key_links:
    - from: "tests/test_send.py"
      to: "telegram_service.resolve_contact / send_message"
      via: "mock_telethon_client.calls introspection"
      pattern: "\\.calls"
    - from: "tests/test_checker.py"
      to: "resolve_phone_with_fallback"
      via: "in-body import (deferred)"
      pattern: "from app.services.checker import"
---

<objective>
Wave-0 RED test scaffold for Phase 17. Create failing tests for SRLD-01..08 BEFORE any behavior change lands, so every downstream task has an `<automated>` proof of correctness (Nyquist). No production code is touched in this plan.

Purpose: Lock the expected behavior of the sender-side resolve ladder, checker username capture, confidence-gated cache reads, and block capture as executable specs. The tests are RED now (behavior not built) and turn GREEN as plans 17-02/17-03/17-04 implement against them.

Output: New test functions appended to four existing test files, all collecting cleanly (`--collect-only` 0 errors), each failing for the right reason.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-CONTEXT.md
@.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-RESEARCH.md
@.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-VALIDATION.md

<interfaces>
<!-- Existing test infra the executor uses directly — no exploration needed. -->

conftest.py mock_telethon_client (Phase 14, conftest.py:986) — AsyncMock dispatching on request-type name:
```python
client = mock_telethon_client
client.set_response("ResolvePhoneRequest", _resolved_users(telegram_id=123))   # set per-request response
client.set_response("ResolveUsernameRequest", ...)
client.set_response("ImportContactsRequest", ...)
res = await client(SomeRequest(...))
# client.calls -> ordered list of (request_type_name, request_obj) tuples
names = [c[0] for c in client.calls]   # e.g. assert "ResolvePhoneRequest" not in names
```
test_checker.py already uses this fixture (see test_import_fallback_and_cleanup); helpers _resolved_users/_imported are defined inline in that test — mirror them.

test_send.py fixtures: `pytestmark = pytest.mark.asyncio`; uses `async_client`, `async_db_session`, `valid_supabase_jwt`, `test_workspace`, `test_running_campaign_factory`.

Existing DB columns the tests assert against (NO migration — all exist):
- contacts.tg_status ('pending'|'registered'|'not_registered'|'error'|'unchecked')
- contacts.tg_username_resolved  (mig 013, resolve-provenance — D-07 reuse target)
- contacts.username              (CSV provenance — must NOT be clobbered)
- contacts.tg_probe_state ('clean'|'suspect'|NULL), tg_confidence ('high'|NULL), tg_resolved_by (UUID) — mig 034
- contacts_cache.username (String(50)), contacts_cache.is_registered, contacts_cache.source
- sender_restriction_events.event_type VARCHAR(20) — FREE-FORM, NO CHECK; category VARCHAR(20) CHECK IN ('restriction','recipient_privacy','flood_wait')
- messages_log.sender_id, messages_log.message_type ('sent'), messages_log.created_at
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: RED stubs for checker-side behavior (SRLD-01, SRLD-07-checker)</name>
  <files>tests/test_checker.py</files>
  <read_first>
    - tests/test_checker.py (existing Phase 14 file — mirror its style, `pytestmark = pytest.mark.asyncio`, deferred in-body imports, inline `_resolved_users`/`_imported` helpers)
    - tests/conftest.py:986-1039 (mock_telethon_client fixture: set_response / calls)
    - app/services/checker.py:69-146 (resolve_phone_with_fallback — currently returns {is_registered, telegram_id}, drops user.username — SRLD-01)
    - app/services/checker.py:175-202 (_lookup_cache — serves is_registered=false blind, no confidence filter — SRLD-07)
    - .planning/phases/17-...-/17-RESEARCH.md §"Phase Requirements → Test Map" (SRLD-01, SRLD-07 rows)
  </read_first>
  <action>
    Append two async test functions to tests/test_checker.py (deferred in-body imports to keep --collect-only clean).

    (a) `test_username_capture_in_resolve_phone` (SRLD-01):
    - Use `mock_telethon_client`. Build a `_resolved_users` helper (mirror the one in test_import_fallback_and_cleanup) whose `users[0]` carries BOTH `.id=123` AND `.username="durov"`.
    - `client.set_response("ResolvePhoneRequest", _resolved_users(telegram_id=123, username="durov"))`.
    - Call `from app.services.checker import resolve_phone_with_fallback; res = await resolve_phone_with_fallback(client, "+79990001111")`.
    - Assert `res["username"] == "durov"` AND `res["is_registered"] is True` AND `res["telegram_id"] == 123`.
    - Add a second case for the ImportContacts fallback path: ResolvePhone empty, ImportContacts returns a user with `.username="hidden_user"` → assert `res["username"] == "hidden_user"`.
    - This is RED today: `resolve_phone_with_fallback` returns no "username" key → KeyError/None.

    (b) `test_confidence_gated_cache_checker_read` (SRLD-07, checker side):
    - Seed the DB (use an async session fixture consistent with test_checker.py; if test_checker.py has no DB fixture, mirror the worker test's `async_db_session` usage — the SUMMARY can note the chosen fixture) with: a `contacts_cache` row `(workspace_id, sender_id, phone='+79990002222', is_registered=false, source=<suspect-checker>)` AND a matching `contacts` row for that phone with `tg_probe_state='suspect'` (or `tg_confidence` NULL).
    - Call `CheckerService()._lookup_cache(workspace_id, '+79990002222')`.
    - Assert it returns `None` (suspect false NOT served → forces live re-resolve). This is RED today: `_lookup_cache` returns the row blind.
    - Add the negative control: same cache row but matching contact `tg_probe_state='clean'` AND `tg_confidence='high'` → `_lookup_cache` returns the row (clean false IS served).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_checker.py -k "username_capture or confidence_gated_cache" --collect-only -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "def test_username_capture" tests/test_checker.py` returns ≥1 hit.
    - `grep -n "confidence_gated_cache" tests/test_checker.py` returns ≥1 hit.
    - `--collect-only` exits 0 (no import/collection error) for the new tests.
    - Running them (without `--collect-only`) FAILS (RED) — they assert behavior not yet built.
  </acceptance_criteria>
  <done>Two RED tests in test_checker.py collect cleanly and fail on assertion (not on import).</done>
</task>

<task type="auto">
  <name>Task 2: RED stubs for sender resolve ladder (SRLD-03, SRLD-04, SRLD-05, SRLD-06, SRLD-07-sender)</name>
  <files>tests/test_send.py</files>
  <read_first>
    - tests/test_send.py (existing file — `pytestmark = pytest.mark.asyncio`, fixtures async_client/async_db_session/test_running_campaign_factory)
    - tests/conftest.py:986-1039 (mock_telethon_client — `.calls` introspection)
    - app/services/telegram.py:494-556 (resolve_contact — cache→username-branch→ResolvePhone, NO import fallback — SRLD-03/04/05)
    - app/services/telegram.py:558-603 (_resolve_username — finalizes False on USERNAME_NOT_OCCUPIED — SRLD-06)
    - app/services/telegram.py:387-458 (_get_cached_contact — cross-sender is_registered=false shortcut at :442 — SRLD-07)
    - .planning/phases/17-...-/17-RESEARCH.md §"Phase Requirements → Test Map" + §"Code Examples" (D-09 fall-through target)
  </read_first>
  <action>
    Append async test functions to tests/test_send.py (deferred in-body imports). Drive `TelegramService().resolve_contact(client, workspace_id, sender_id, phone, ...)` with `mock_telethon_client`, asserting on `client.calls` request-type names.

    (a) `test_resolve_ladder_no_sender_resolvephone` (SRLD-03):
    - Seed a `contacts` row for the phone with `tg_status='registered'` AND a captured `tg_username_resolved='captured_handle'`.
    - `client.set_response("ResolveUsernameRequest", <users with id+access_hash>)`.
    - Call `resolve_contact`. Assert `"ResolvePhoneRequest" NOT in [c[0] for c in client.calls]` (sender's own ResolvePhone removed) AND `"ResolveUsernameRequest" in calls` (tier-2 fired on the captured username).
    - RED today: resolve_contact still calls ResolvePhoneRequest for phone keys.

    (b) `test_import_gate_registered_only` (SRLD-04):
    - Case A: contact `tg_status='registered'`, NO captured username, `client.set_response("ImportContactsRequest", <user found>)` → assert `"ImportContactsRequest" in calls` and `res["is_registered"] is True`.
    - Case B: contact `tg_status='not_registered'` → assert `"ImportContactsRequest" NOT in calls` AND result is unregistered (no import attempted on not_registered). RED today.

    (c) `test_lazy_import_no_delete_on_sender` (SRLD-05):
    - With the registered import path firing ImportContacts, assert `"DeleteContactsRequest" NOT in [c[0] for c in client.calls]` (D-04: sender keeps the contact, unlike the checker). RED if a Delete is wired on the sender.

    (d) `test_stale_username_fallthrough` (SRLD-06):
    - contact `tg_status='registered'` with `tg_username_resolved='gone_handle'`.
    - `client.set_response("ResolveUsernameRequest", <raises UsernameNotOccupiedError>)` (use a callable response that raises, or assert the resolve_contact path falls through).
    - `client.set_response("ImportContactsRequest", <user found>)`.
    - Assert: ResolveUsername was attempted, then `"ImportContactsRequest" in calls` (fell through to tier-3), final `res["is_registered"] is True`, and the contact is NEVER cached/finalized as not_registered. RED today (`_resolve_username` caches False and returns).

    (e) `test_confidence_gated_cache_sender_read` (SRLD-07, sender side):
    - Seed a cross-sender `contacts_cache` row `is_registered=false` for the phone written by a suspect source, with the matching `contacts` row `tg_probe_state='suspect'`.
    - Call `resolve_contact`. Assert the cross-sender false shortcut does NOT short-circuit — a live Telegram resolve is attempted (`"ResolveUsernameRequest" or "ImportContactsRequest" in calls`), i.e. `_get_cached_contact` did not return the blind false. RED today (telegram.py:442 returns false blind).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send.py -k "resolve_ladder or import_gate or lazy_import or stale_username_fallthrough or confidence_gated_cache" --collect-only -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "resolve_ladder\|import_gate\|lazy_import\|stale_username_fallthrough\|confidence_gated_cache" tests/test_send.py` returns ≥5 hits.
    - `--collect-only` exits 0 for the new tests.
    - Each test FAILS (RED) on assertion when run, not on collection.
    - At least one test asserts `"ResolvePhoneRequest" not in` the sender's `client.calls` (SRLD-03 anchor).
  </acceptance_criteria>
  <done>Five RED tests in test_send.py collect cleanly and fail on assertion.</done>
</task>

<task type="auto">
  <name>Task 3: RED stubs for username persistence (SRLD-02) + block capture (SRLD-08)</name>
  <files>tests/test_contact_check_worker.py, tests/test_restriction_audit.py, tests/test_send.py</files>
  <read_first>
    - tests/test_contact_check_worker.py (existing file — mirror its DB/worker fixtures)
    - app/services/contact_check_worker.py:864-892 (_apply_results is_registered branch — already writes tg_username_resolved = res.get("username") at :875; live once checker stops dropping username)
    - tests/test_restriction_audit.py (existing Phase 10 file — mirror record_restriction_event usage)
    - app/services/restriction_audit.py:48 (record_restriction_event signature: sender_id, event_type, source, restricted_until, raw_text, category=, db=)
    - app/models/__init__.py (SenderRestrictionEvent: event_type free-form, category CHECK)
    - .planning/phases/17-...-/17-RESEARCH.md §"Code Examples" (D-15 block capture + block-rate query)
  </read_first>
  <action>
    (a) Append `test_captured_username_persisted` to tests/test_contact_check_worker.py (SRLD-02):
    - Drive the worker's result-application path (mirror an existing worker test that calls `_apply_results`) with a checker `summary['results']` entry that includes `"username": "captured_handle"` and `is_registered=True` for a seeded `contacts` row that has a DIFFERENT CSV `contacts.username='csv_handle'`.
    - After application, SELECT the contact row. Assert `tg_username_resolved == 'captured_handle'` AND `contacts.username == 'csv_handle'` (CSV provenance NOT clobbered — Pitfall 5).
    - RED today only because the checker drops username upstream; the worker SQL is already correct, so this test PASSES once a username is present in results — design it to drive `_apply_results` directly with the username in the summary so it is GREEN-able by 17-02 (which makes the checker emit username). Mark it clearly as the SRLD-02 persistence contract.

    (b) Append `test_blocked_event_inserts_no_check_violation` to tests/test_restriction_audit.py (SRLD-08):
    - Seed a sender. Call `await record_restriction_event(sender_id, "blocked", "queue_error", None, "Получатель заблокировал отправителя", category="restriction", db=db)`.
    - Assert exactly one `sender_restriction_events` row with `event_type='blocked'`, `category='restriction'` exists and NO CHECK violation was raised (the call succeeds). This GREEN-confirms event_type is free-form — keep it so 17-04 wiring has a target.

    (c) Append `test_user_blocked_records_event` to tests/test_send.py (SRLD-08, send path):
    - Drive the send path so the resolve succeeds but `client.send_message(...)` raises `UserIsBlockedError` (mock the client send to raise). Assert the returned error code is `"USER_IS_BLOCKED"` (the structured error code 17-04 adds to telegram.py).
    - RED today (no UserIsBlockedError catch → falls into generic SEND_FAILED).

    (d) Append `test_block_rate_aggregate` to tests/test_restriction_audit.py (SRLD-08, read-only metric):
    - Seed N `event_type='blocked'` events + M `messages_log` rows (`message_type='sent'`) for a sender in a window. Assert the block-rate query/helper (the one 17-04 adds — import it in-body; RED until then) returns `blocks_7d == N`, `sends_7d == M`. RED today (helper/endpoint not built).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_contact_check_worker.py tests/test_restriction_audit.py tests/test_send.py -k "captured_username or blocked or user_blocked or block_rate" --collect-only -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "captured_username" tests/test_contact_check_worker.py` returns ≥1 hit.
    - `grep -n "def test_blocked_event_inserts\|def test_block_rate_aggregate" tests/test_restriction_audit.py` returns ≥2 hits.
    - `grep -n "user_blocked\|USER_IS_BLOCKED" tests/test_send.py` returns ≥1 hit.
    - `--collect-only` exits 0 for all four new tests.
    - test_blocked_event_inserts_no_check_violation passes immediately (proves event_type free-form, no migration needed); the other three are RED on assertion.
  </acceptance_criteria>
  <done>Four new tests collect cleanly; the no-CHECK-violation test is GREEN, the rest RED on assertion.</done>
</task>

</tasks>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --collect-only -q` reports 0 collection errors (full suite still collects).
- New SRLD tests are present and fail for the right reason (assertion, not import) when run.
- No production file under `app/` was modified by this plan.
</verification>

<success_criteria>
- 11 new RED/contract tests appended across 4 test files covering SRLD-01..08.
- Full suite collection clean (~837 + new = no errors).
- Each downstream behavior task (17-02/17-03/17-04) now has a concrete `pytest -k` target that flips GREEN on implementation.
</success_criteria>

<output>
After completion, create `.planning/phases/17-sender-side-resolve-ladder-with-username-capture-and-import-fallback/17-01-SUMMARY.md` noting: which DB-session fixture test_checker.py uses for the cache-gate test, the exact `-k` selectors per requirement, and any helper (`_resolved_users(username=...)`) added.
</output>
