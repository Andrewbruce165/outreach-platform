---
phase: 19-no-reply-follow-up-and-auto-finish
verified: 2026-07-03T10:30:00Z
status: passed
score: 9/9 must-haves verified
human_verification:
  - test: "Live end-to-end no_reply/ping/auto-finish on a running campaign with a real silent dialog"
    expected: "After the configured interval, conversation flips to no_reply in inbox, a ping is sent from the SAME sender account; a reply reverts it to active with no further pings; auto-finish closes a silent dialog with finish webhook reason='no_reply'"
    why_human: "Requires real time elapsed + a live Telegram dialog; steps 4-5 of 19-05's how-to-verify were optional and not exercised — the human-verify checkpoint was approved based on persistence/bounds checks (steps 1-3) only. Not a gap, but worth a follow-up UAT pass before considering the live loop battle-tested."
---

# Phase 19: No Reply Follow-Up and Auto-Finish Verification Report

**Phase Goal:** Contacts we messaged and who haven't replied get a "no reply" state; campaigns gain an Enable Follow Up toggle with a user-defined ping interval and an auto-finish after N hours without reply, configurable in the campaign create/edit form.
**Verified:** 2026-07-03
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `conversations.status` accepts `'no_reply'` (D-01) | ✓ VERIFIED | `migrations/045_follow_up.sql` extends CHECK to include `no_reply` while preserving `bot_ignored`; ORM `Conversation.status` comment updated; `tests/test_follow_up.py::test_no_reply_status_allowed` passes |
| 2 | Campaign gains follow-up settings with bounds (D-08/D-12) | ✓ VERIFIED | `campaigns` table has `follow_up_enabled` (default false), `follow_up_interval_hours` (default 24, 4–168), `follow_up_max_pings` (default 2, 1–5), `auto_finish_hours` (default 72, 24–720) — migration + ORM + Pydantic `Field(ge/le)` in `app/schemas/__init__.py:703-706,773-776,835-838`; `test_campaign_follow_up_fields` passes |
| 3 | Ping counter persisted, timer anchored to last outbound (D-02/D-04) | ✓ VERIFIED | `conversations.pings_sent` column; `FollowUpWorker.tick()` derives `last_outbound_at` lazily from `messages` (`app/services/follow_up.py`) |
| 4 | FollowUpWorker drives ping/auto-finish state machine (D-02/04/07/08/13) | ✓ VERIFIED | `app/services/follow_up.py` — `class FollowUpWorker`, `tick()` gates on `cp.status='running' AND cp.follow_up_enabled=true`, `FOR UPDATE OF c SKIP LOCKED`, auto-finish branch evaluated before ping branch; `test_ping_on_interval`/`test_auto_finish` pass |
| 5 | Ping text AI-generated, no tools (D-07) | ✓ VERIFIED | `ai_engine.generate_followup_ping` (`app/services/ai_engine.py:1805`) resolves Phase-18 provider via `resolve_llm_config`, calls provider with no tools; `test_generate_followup_ping_returns_text` passes |
| 6 | Auto-finish fires webhook reason=no_reply (D-09/D-10) | ✓ VERIFIED | `follow_up.py::_auto_finish` calls `notify_signal(event_type='finish', reason='no_reply', ...)`; `test_finish_reason_marker` passes |
| 7 | Incoming reply reverts no_reply→active + cancels pings (D-03/D-17) | ✓ VERIFIED | `app/services/listener.py::handle_no_reply_revert` reverts DB row + local `conv["status"]`, cancels pending `message_queue` rows before AI-dispatch check; `test_reply_cancels_pings` passes |
| 8 | Pre-send re-check cancels stale pings (D-17 second guard) | ✓ VERIFIED | `app/services/queue.py:800-810` gated on `extra_data.kind=='followup'`, cancels on reply-since or non-active/no_reply status; guard tests pass |
| 9 | Campaign form exposes Follow Up block + openapi regen (D-08/D-12/D-13) | ✓ VERIFIED | `lovable-handoff/openapi.json` contains all 4 `follow_up_*` fields (12 occurrences) + `no_reply`; sibling repo `aimly-tg-outreach` commit `f5b975e` adds the block to `campaigns.new.tsx` + `EditCampaignModal.tsx`; `inbox.tsx` recognizes `no_reply` as a filterable/displayed status |

**Score:** 9/9 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `migrations/045_follow_up.sql` | no_reply CHECK + 5 new columns, idempotent | ✓ VERIFIED | Present, idempotent (`DO $$ ... EXCEPTION duplicate_object`, `ADD COLUMN IF NOT EXISTS`), preserves `bot_ignored` |
| `app/models/__init__.py` | ORM mirrors w/ server_default | ✓ VERIFIED | `pings_sent`, `follow_up_enabled/interval_hours/max_pings`, `auto_finish_hours` all present w/ matching server_default |
| `app/schemas/__init__.py` | 4 fields on Create/Update/Response w/ bounds | ✓ VERIFIED | Lines 703-706 (Create), 773-776 (Update, Optional), 835-838 (Response) |
| `app/routers/campaigns.py` | create/update/response/duplicate passthrough | ✓ VERIFIED | Lines 350-353 (`_campaign_to_response`), 476-479 (create), 972-975 (duplicate); PATCH covered by generic `exclude_unset` loop |
| `app/services/ai_engine.py` | `generate_followup_ping` | ✓ VERIFIED | Line 1805, calls `get_context_for_conversation` + `resolve_llm_config`, `tools=None` |
| `app/services/listener.py` | revert + cancel on reply | ✓ VERIFIED | `handle_no_reply_revert` (line 1766) wired into `handle_incoming_message` (line 956) before AI-dispatch |
| `app/services/queue.py` | pre-send followup guard | ✓ VERIFIED | Lines 800-810, gated on `extra_data.kind=='followup'` |
| `app/services/follow_up.py` | FollowUpWorker | ✓ VERIFIED | 289 lines, `class FollowUpWorker`, `tick()`, module singleton |
| `app/config.py` | `follow_up_tick_seconds` knob | ✓ VERIFIED | Line 127, default 300 |
| `app/main.py` | lifespan registration | ✓ VERIFIED | import + `.start()`/`await .stop()` present |
| `app/services/webhook_notify.py` | no_reply reason marker | ✓ VERIFIED | `reason` forwarded to payload; documented at line 140-143 |
| `lovable-handoff/openapi.json` | 4 fields + no_reply | ✓ VERIFIED | 12 occurrences of follow_up_* fields, `no_reply` present, valid JSON |
| sibling repo campaign form | Follow Up block | ✓ VERIFIED | `campaigns.new.tsx`, `EditCampaignModal.tsx`, `types/api.ts` — commit `f5b975e` |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `migrations/045_follow_up.sql` | `conversations_status_check` | DROP/ADD CONSTRAINT | ✓ WIRED | Preserves `bot_ignored`, adds `no_reply` |
| `follow_up.py` | `ai_engine.generate_followup_ping` | worker calls before enqueue | ✓ WIRED | Line 247 |
| `follow_up.py` | `queue.enqueue_message` | metadata `{"kind":"followup"}` | ✓ WIRED | Line 273 |
| `follow_up.py` | `webhook_notify.notify_signal` | auto-finish reason='no_reply' | ✓ WIRED | Line 209 |
| `main.py` | `follow_up.py` | start/stop in lifespan | ✓ WIRED | Confirmed |
| `listener.py` | `message_queue` | UPDATE status='cancelled' on reply | ✓ WIRED | Confirmed inside `handle_no_reply_revert` |
| `queue.py` | `messages` | SELECT inbound newer than ping's created_at | ✓ WIRED | Confirmed gated on `kind=='followup'` |
| `lovable-handoff/openapi.json` | `CampaignCreate/Update/Response` | offline `app.openapi()` regen | ✓ WIRED | 12 occurrences confirm regen included the fields |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase 19 test module green | `pytest tests/test_follow_up.py -q` | 12 passed | ✓ PASS |
| Full backend suite green (excl. known pre-existing failure) | `pytest -q` (full suite, test-overlay) | 939 passed, 1 skipped, 1 failed (WARM-14, pre-existing, documented in deferred-items.md) | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| NORP-01 | 19-01 | no_reply added to status CHECK/ORM | ✓ SATISFIED | migration 045 + ORM + test |
| NORP-02 | 19-01/19-02 | Campaign follow-up fields w/ bounds | ✓ SATISFIED | migration + schemas + router |
| NORP-03 | 19-01 | pings_sent counter, timer anchored to last outbound | ✓ SATISFIED | migration + follow_up.py lazy derivation |
| NORP-04 | 19-04 | FollowUpWorker ping/auto-finish state machine | ✓ SATISFIED | follow_up.py tick() |
| NORP-05 | 19-02 | AI-generated ping text, no tools | ✓ SATISFIED | ai_engine.generate_followup_ping |
| NORP-06 | 19-04 | Auto-finish → finished + cancel pings + finish webhook reason=no_reply | ✓ SATISFIED | follow_up.py `_auto_finish` |
| NORP-07 | 19-03 | Incoming message reverts no_reply→active + cancels pings | ✓ SATISFIED | listener.py `handle_no_reply_revert` |
| NORP-08 | 19-03 | Pre-send re-check cancels stale ping | ✓ SATISFIED | queue.py followup guard |
| NORP-09 | 19-04 | Pings respect working hours/rate limits, bypass new-dialog cap/pacing | ✓ SATISFIED | ping enqueued via `enqueue_message` which is follow-up by construction (prior sent opener); no queue interval constants modified (grep confirmed byte-identical per 19-03 SUMMARY) |
| NORP-10 | 19-04 | Same-sender-only; frozen sender waits, auto-finish still closes | ✓ SATISFIED | follow_up.py line 227 checks `sender_restriction_status`; auto-finish branch unconditional |
| NORP-11 | 19-04 | Toggle-on retroactive with smoothing; past-threshold dialogs finish immediately | ✓ SATISFIED | auto-finish-first evaluation order in tick() naturally finishes already-past-threshold dialogs on first tick after enable (D-15) |
| NORP-12 | 19-04 | Paused campaign freezes timers; done cancels pings | ✓ SATISFIED | tick() SELECT gates on `cp.status='running'`; done-side cancel pre-existing (campaigns.py `_cancel_pending_queue`, confirmed via 19-04 PLAN interfaces) |
| NORP-13 | 19-05 | Campaign form Follow Up block + openapi regen | ✓ SATISFIED | openapi.json + sibling repo form, human-verified (approved) |

All 13 NORP requirement IDs from the PLAN frontmatters are accounted for in REQUIREMENTS.md's Phase 19 block (NORP-01..13) — no orphans, no unmapped IDs.

### Anti-Patterns Found

None found. No TODO/FIXME/placeholder markers in the Phase 19 files (`migrations/045_follow_up.sql`, `app/services/follow_up.py`, `app/services/ai_engine.py::generate_followup_ping`, `app/services/listener.py::handle_no_reply_revert`, `app/services/queue.py` followup guard, `app/schemas/__init__.py`, `app/routers/campaigns.py`). No empty-return stubs, no hardcoded static data paths. The one known static condition — `follow_up.py`'s D-16 pause-semantics wall-clock simplification — is explicitly documented as an intentional v1 scope decision, not a stub.

### Human Verification Required

### 1. Live no_reply → ping → auto-finish loop under real elapsed time

**Test:** On a running campaign with a genuinely silent dialog, enable Follow Up with a short interval; wait for the interval to elapse; observe the dialog transition to `no_reply` in the inbox, confirm a ping is sent from the same sender account, confirm a reply reverts it to `active` with no further ping, and confirm auto-finish eventually closes a still-silent dialog with `finished` + a `reason='no_reply'` finish webhook payload (if a finish webhook URL is configured).
**Expected:** Full state machine behaves as designed end-to-end against live Telegram traffic.
**Why human:** Requires real wall-clock time elapsing plus a live Telegram dialog; this was the *optional* steps 4-5 of the 19-05 human-verify checkpoint, which the user approved without running (steps 1-3, covering persistence + bounds validation, were exercised and approved). Not a gap — flagging as an optional follow-up UAT item per the task instructions.

### Gaps Summary

No gaps found. All 9 derived must-have truths verified against the actual codebase (migrations, ORM, schemas, routers, services, listener, queue, worker, lifespan, webhook, frontend). All 13 NORP requirement IDs are satisfied with concrete code evidence. The full backend test suite passes (939 passed, 1 skipped) except the pre-existing, documented, out-of-scope `test_warmup_worker.py::test_restricted_sender_excluded` (WARM-14) failure, which predates Phase 19 and is not a regression. The Phase 19 test module (`tests/test_follow_up.py`) is 12/12 green. The one open item is an optional live end-to-end UAT pass (steps 4-5 of the 19-05 checkpoint) that the user knowingly deferred when approving the checkpoint — tracked here for visibility, not counted as a gap.

---

*Verified: 2026-07-03*
*Verifier: Claude (gsd-verifier)*
