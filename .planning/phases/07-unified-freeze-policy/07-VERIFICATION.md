---
phase: 07-unified-freeze-policy
verified: 2026-06-23T10:30:00Z
status: passed
score: 7/7 must-haves verified
re_verification: null
gaps: []
human_verification: []
---

# Phase 07: Unified Freeze Policy — Verification Report

**Phase Goal:** Единая политика мягкого спам-ограничения. Переписать `listener._handle_antispam_signal` по образцу PEER_FLOOD: вместо терминального `failed` — пауза pending этого sender'а + `restriction_status='spam_limited'`/`restricted_until` (reconcile авто-возобновляет); перестать выключать `ai_enabled` во всех диалогах (ответы в идущих диалогах продолжаются); добавить `AND s.restriction_status='none'` в фильтр кандидатов rotation; регресс-тест что воркер скипает restricted sender'а. Миграций нет (028 уже есть).
**Verified:** 2026-06-23T10:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Unsolicited antispam signal pauses pending queue items (status stays 'pending', scheduled_at +24h) instead of failing them — reconcile can auto-resume | VERIFIED | `listener.py:924-931`: UPDATE message_queue SET scheduled_at = :pause_until WHERE sender_id = :sid AND status = 'pending'. Test `test_antispam_guard_pauses_and_flags_when_no_selfcheck` asserts `q_status == "pending"` and `scheduled_future is True`. |
| 2 | Antispam signal flags sender `restriction_status='spam_limited'` with `restricted_until = now + restriction_recheck_interval_seconds` | VERIFIED | `listener.py:936-943`: UPDATE senders SET restriction_status = 'spam_limited', restricted_until = :recheck_at WHERE id = :sid AND restriction_status <> 'frozen'. `recheck_at` uses `get_settings().restriction_recheck_interval_seconds`. Test asserts `restriction == "spam_limited"`. |
| 3 | Antispam signal NO LONGER disables ai_enabled on any conversation — replies in established dialogues keep flowing | VERIFIED | `listener.py:881-956`: `_handle_antispam_signal` contains no `ai_enabled = false` write. The remaining `ai_enabled = false` at line 1011 is in a separate `_handle_bot_message` method (unrelated). Test asserts `ai_enabled is True`. |
| 4 | SpamBot self-check early-return is the FIRST statement — solicited reply never re-flags the sender (no clear→reflag loop) | VERIFIED | `listener.py:900-909`: `if telegram_service.is_spambot_selfcheck(sender_slug): ... return` appears before any DB write, immediately after the two local variable assignments. Test `test_antispam_guard_skips_when_selfcheck_active` passes. |
| 5 | Rotation never assigns a new cold contact to a sender with `restriction_status != 'none'` | VERIFIED | `rotation.py:121`: `AND s.restriction_status = 'none'` present in Step 3 candidate WHERE clause. Test `test_rotation_skips_restricted_senders` seeds a `spam_limited` sender (still active/auth-ok) alongside an active sender and asserts only the active sender is returned. CR-01 (Step 1 existing-assignment path) explicitly deferred to Phase 9 by user decision — out of scope. |
| 6 | spam_limited write does not downgrade an existing 'frozen' sender | VERIFIED | `listener.py:941`: `WHERE id = :sid AND restriction_status <> 'frozen'` guard on the senders UPDATE. |
| 7 | Queue worker skips a restricted sender at pre-send (FRZ-05) — asserted by regression test | VERIFIED | `queue.py:401`: `if sender_row.restriction_status != "none": return False`. Test `test_queue_pre_send_skips_restricted` (source-shape inspection) passes 100%. |

**Score: 7/7 truths verified**

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/services/listener.py` | `_handle_antispam_signal` rewritten: self-check early-return first; ai_enabled block deleted; pause+flag mirror of PEER_FLOOD; frozen-guard | VERIFIED | Contains `restriction_status = 'spam_limited'`, `timedelta(hours=24)`, `restriction_status <> 'frozen'`. No `status = 'failed'`, no `ai_enabled = false` inside this method. |
| `app/services/rotation.py` | Candidate filter excludes restricted senders | VERIFIED | `AND s.restriction_status = 'none'` at line 121 inside the Step 3 WHERE clause. |
| `tests/test_spambot_selfcheck.py` | Cancel-path test renamed + asserts pause contract | VERIFIED | Test renamed to `test_antispam_guard_pauses_and_flags_when_no_selfcheck`. No `assert q_status == "failed"` or `assert ai_enabled is False`. Asserts `pending`, `scheduled_at > NOW()`, `ai_enabled is True`, `restriction_status == "spam_limited"`. |
| `tests/test_rotation_campaign.py` | New `test_rotation_skips_restricted_senders` regression | VERIFIED | Present at lines 100-117. Seeds `restriction_status="spam_limited"`, asserts active sender returned. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `listener.py::_handle_antispam_signal` | `senders.restriction_status` / `restricted_until` + `message_queue.scheduled_at` | Separate `AsyncSessionLocal()` + single commit | WIRED | `listener.py:919-946`: async with AsyncSessionLocal() → two UPDATE statements → commit. Pattern mirrors queue.py:739-754. |
| `listener.py::_restriction_reconcile_tick` | Paused pending items written by `_handle_antispam_signal` | reconcile resume query `WHERE status='pending' AND scheduled_at > NOW()` | WIRED | `listener.py:1404-1406`: UPDATE message_queue SET scheduled_at = NOW() WHERE sender_id = :sid AND status = 'pending' AND scheduled_at > NOW(). Exact contract the test relies on. |
| `rotation.py` candidate query | `senders.restriction_status` | WHERE clause `AND s.restriction_status = 'none'` | WIRED | `rotation.py:121`: clause present in Step 3 SELECT. |

### Data-Flow Trace (Level 4)

Not applicable. This phase modifies state-machine handlers (listener + rotation) and test contracts — no dynamic data-rendering components. The relevant data flows are DB state writes verified directly by integration tests with DB assertions.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 21 targeted tests pass under test-overlay | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_spambot_selfcheck.py tests/test_rotation_campaign.py tests/test_sender_restriction.py -v` | 21 passed in 2.67s | PASS |
| `test_antispam_guard_pauses_and_flags_when_no_selfcheck` asserts new contract | included above | q_status=pending, scheduled_future=True, ai_enabled=True, restriction=spam_limited | PASS |
| `test_rotation_skips_restricted_senders` (FRZ-04 regression) | included above | active sender returned, not spam_limited one | PASS |
| `test_queue_pre_send_skips_restricted` (FRZ-05 regression) | included above | source-shape assert passes | PASS |

### Requirements Coverage

FRZ-01..05 are phase-internal requirements derived in 07-RESEARCH.md (not present in REQUIREMENTS.md v1 traceability table, which covers TENT/AUTH/ONBD/SNDR/CONT/FLDR/AGNT/CAMP/INBX/ANLX/ADMN). They are recorded in ROADMAP.md Phase 07 entry.

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| FRZ-01 | 07-01-PLAN.md | Antispam signal pauses pending items (not fails) | SATISFIED | `listener.py`: UPDATE message_queue SET scheduled_at = :pause_until WHERE status='pending'. Test asserts q_status == "pending". |
| FRZ-02 | 07-01-PLAN.md | Antispam signal flags sender spam_limited + restricted_until | SATISFIED | `listener.py`: UPDATE senders SET restriction_status = 'spam_limited', restricted_until = :recheck_at. Test asserts restriction_status == "spam_limited". |
| FRZ-03 | 07-01-PLAN.md | Antispam signal does NOT disable ai_enabled | SATISFIED | `_handle_antispam_signal` contains no ai_enabled write. Test asserts ai_enabled is True after handler runs. |
| FRZ-04 | 07-01-PLAN.md | Rotation excludes restricted senders from new cold-contact assignment | SATISFIED | `rotation.py:121`: AND s.restriction_status = 'none'. Scoped to Step 3 (new assignment). CR-01 (Step 1 existing-assignment) explicitly deferred to Phase 9 per user decision in 07-REVIEW.md and deferred-items.md. |
| FRZ-05 | 07-01-PLAN.md | Queue worker pre-send skips restricted sender (already existed, needs regression test) | SATISFIED | `queue.py:401`: restriction_status != "none" → return False. `test_queue_pre_send_skips_restricted` inspects source shape and passes. |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `app/services/listener.py` | 907 | Log message says "skip auto-cancel" — stale terminology from pre-phase-07 terminal-fail design | Info | Cosmetic. Behaviour is correct; wording is misleading. Flagged as IN-01 in 07-REVIEW.md, explicitly deferred. |
| `tests/test_spambot_selfcheck.py` | 1-13 | Module docstring still describes old "cancelling the sender's own queue + disabling AI" semantics | Info | Cosmetic. Renamed test and new assertions are correct. Flagged as IN-02 in 07-REVIEW.md, explicitly deferred. |
| `app/services/listener.py` | 932 | `len(paused.fetchall())` vs `rowcount` — materialises IDs purely for count logging | Info | No correctness impact. Flagged as WR-02 in 07-REVIEW.md, explicitly deferred. |
| `app/services/listener.py` | 940 | `restricted_until` reset to now+6h on every inbound antispam message — no GREATEST guard | Warning | Could defer reconcile clear if SpamBot sends repeated messages. Mirrors PEER_FLOOD pattern shape. Explicitly accepted in deferred-items.md as "revisit if observed in prod". |

No blocker anti-patterns found. All four items above are explicitly logged and accepted in 07-REVIEW.md and deferred-items.md.

### Human Verification Required

None. All phase-07 goals are mechanically verifiable:
- State-machine writes verified by integration tests with DB assertions
- Rotation filter verified by source inspection + integration test
- Pre-send skip verified by source-shape test

The WR-01 concern (FRZ-03 assumption that Telegram allows replies during spam-limit) is an accepted design decision documented in deferred-items.md, not an unverified unknown.

### Gaps Summary

No gaps. All 7 must-have truths verified, all 4 required artifacts substantive and wired, all 3 key links confirmed, all 5 FRZ requirements satisfied, all 21 targeted tests pass under the test-overlay.

The one out-of-scope item (CR-01: existing-assignment routing at rotation.py Step 1) was explicitly reviewed, discussed, and deferred to Phase 9 by user decision. It is not a gap in Phase 07's goal — FRZ-04 as scoped covers new cold contacts only, and outbound sends to a restricted sender are already gated at queue.py:401.

---

_Verified: 2026-06-23T10:30:00Z_
_Verifier: Claude (gsd-verifier)_
