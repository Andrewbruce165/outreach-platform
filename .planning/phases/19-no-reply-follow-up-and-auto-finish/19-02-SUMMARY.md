---
phase: 19-no-reply-follow-up-and-auto-finish
plan: 02
subsystem: api
tags: [campaigns, pydantic, ai_engine, llm-provider, follow-up]

# Dependency graph
requires:
  - phase: 19-01
    provides: "migration 045 (no_reply status + campaign follow-up columns + conversations.pings_sent) and ORM mirror with server_default"
  - phase: 18-switchable-llm-provider
    provides: "resolve_llm_config / get_provider / platform_fallback_config provider resolution reused by generate_followup_ping"
provides:
  - "4 follow-up campaign fields (follow_up_enabled/interval_hours/max_pings/auto_finish_hours) on CampaignCreate/Update/Response with Pydantic bounds"
  - "Router passthrough of the 4 fields through create/update(PATCH)/response/duplicate"
  - "ai_engine.generate_followup_ping(session, conversation_id) — provider-routed, tool-free AI ping text (D-07)"
affects: [19-03, 19-04, follow-up-worker, listener]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "API-layer Pydantic bounds (no DB CHECK) — mirrors max_new_dialogs_per_day / recontact_min_age_days precedent"
    - "Follow-up ping reuses generate_response's front half (context + Phase-18 provider) but drops tools and appends a proactive directive"

key-files:
  created: []
  modified:
    - app/schemas/__init__.py
    - app/routers/campaigns.py
    - app/services/ai_engine.py
    - tests/test_follow_up.py

key-decisions:
  - "D-12: follow-up bounds enforced only at the API layer (Pydantic Field ge/le), not via DB CHECK — matches existing campaign numeric-field precedent"
  - "D-07: generate_followup_ping added as an AIEngine method (not module-level) to reuse self.get_conversation_history + self.build_system_prompt; provider resolved via resolve_llm_config exactly like generate_response"
  - "Ping generated with tools=None so a proactive nudge can never trigger lead/handoff/finish signals"
  - "generate_followup_ping returns None when no campaign/agent context (no agent_id) or on provider error — caller (FollowUpWorker) skips and retries next tick"

patterns-established:
  - "Ping text generated at send time (not enqueue) so a reply arriving between schedule and send is reflected in dialog history"

requirements-completed: [NORP-02, NORP-05]

# Metrics
duration: 15min
completed: 2026-07-03
---

# Phase 19 Plan 02: Follow-Up Campaign API + AI Ping Generator Summary

**Exposed the 4 follow-up campaign fields through the API with Pydantic bounds and added `ai_engine.generate_followup_ping`, a provider-routed, tool-free AI ping generator reusing the Phase-18 LLM resolution.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-07-03T08:06:00Z
- **Completed:** 2026-07-03T08:20:00Z
- **Tasks:** 3 completed
- **Files modified:** 4

## Accomplishments

### Task 1 — Follow-up fields on campaign schemas (NORP-02, commit 5e5c9c6)
Added `follow_up_enabled` / `follow_up_interval_hours` / `follow_up_max_pings` / `auto_finish_hours` to `CampaignCreate` (with `Field(ge/le)` bounds), `CampaignUpdate` (all Optional for PATCH), and `CampaignResponse` (echoing defaults). Bounds are exactly interval 4–168, max_pings 1–5, auto_finish 24–720; toggle defaults OFF.

### Task 2 — Router passthrough (NORP-02, commit 661f63d)
Wired all 4 fields through `create_campaign` (from payload), `_campaign_to_response` (from ORM row), and `duplicate_campaign` (from src). PATCH is covered automatically by the existing generic `model_dump(exclude_unset=True)` → `setattr` loop. Round-trip verified by `test_campaign_follow_up_fields` (422 on out-of-range, 201 + echo on valid).

### Task 3 — generate_followup_ping (NORP-05, TDD; commits ca21149 test / c9deb11 impl)
Added `AIEngine.generate_followup_ping(session, conversation_id)`. It resolves campaign+agent context via `get_context_for_conversation`, resolves the Phase-18 provider via `resolve_llm_config` (platform default when no BYO row), assembles the same system prompt + dialog history as `generate_response`, appends a `<followup_directive>` (one short on-topic nudge, not a reply, no verbatim opener repeat), and calls the provider with `tools=None`. Returns the text, or `None` when there is no agent context or the provider errors. The decrypted BYO key is never logged; the call is persisted via `log_llm_call` (provider + key_source).

## Deviations from Plan

**None functional.** Two minor notes:
- Task 2 acceptance grep in the plan looked for `follow_up_enabled=c.follow_up_enabled`, but the existing `_campaign_to_response` names its ORM parameter `campaign`, so the actual (and correct) line is `follow_up_enabled=campaign.follow_up_enabled`. The intent (map all 4 ORM columns) is satisfied.
- Worktree dependency: this worktree branch was based on a commit predating Plan 19-01. Merged `main` into the worktree branch to pull in migration 045 + ORM columns + the RED scaffold (the declared `depends_on: [19-01]`). No conflicts.

## Test Results

Run via test-overlay with `--no-deps` + `--env-file` (prod `db` container name is globally reserved; ephemeral `db-test` started separately):

- `test_campaign_follow_up_fields` (NORP-02) — PASS
- `test_generate_followup_ping_returns_text` (NORP-05) — PASS (stubbed provider, asserts non-empty text + `tools in (None, [])`)
- `test_generate_followup_ping_no_context_returns_none` (NORP-05) — PASS
- Campaign regression suites (test_campaign_router / _model / _new_dialog_limit_api / phase5_1_v2_router) + rest of test_follow_up: 50 passed.
- 5 remaining `test_follow_up.py` failures (`test_ping_on_interval`, `test_auto_finish`, `test_finish_reason_marker`, `test_reply_cancels_pings`, `test_paused_frozen`) are the intentional RED scaffolds for plans 19-03 (`handle_no_reply_revert`) and 19-04 (`FollowUpWorker`) — out of scope for this plan.

## Known Stubs

None. `generate_followup_ping` is fully wired to the live provider resolution; the campaign fields round-trip through real ORM columns (migration 045).

## Self-Check: PASSED
