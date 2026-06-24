---
phase: 11-agent-campaign-field-split-and-prompt-assembly
plan: "03"
subsystem: ai-engine-prompt-assembly
tags: [prompt-assembly, ai-engine, listener, debounce, phase-11, build-system-prompt]
dependency_graph:
  requires: ["11-02"]
  provides: ["11-04"]
  affects:
    - app/services/ai_engine.py
    - app/services/listener.py
tech_stack:
  added:
    - _PROMPT_FACTS_GUARD constant (anti-hallucination guard for arguments_facts)
    - _TONE_LINES dict (tone_preset → 1-line instruction, D-01/D-03)
    - _dedup_rules() helper (line-level dedup preserving insertion order)
  patterns:
    - BRIEF §7 fixed block order in build_system_prompt (single source per block)
    - response_speed enum → debounce branch (instant/human/slow/manual)
    - get_context cached (TTL 60s) as speed source for listener (no extra SELECT)
key_files:
  created: []
  modified:
    - app/services/ai_engine.py
    - app/services/listener.py
decisions:
  - "D-01/D-03: tone_preset is single tone source; _TONE_LINES dict maps enum → 1-line instruction; tone never duplicated in rules block"
  - "D-06: dialogue_flow JSONB stages render as numbered list; static _PROMPT_DIALOGUE_GOAL removed"
  - "D-12: arguments_facts block added with _PROMPT_FACTS_GUARD anti-hallucination guard"
  - "D-14: _dedup_rules(agent_rules, campaign_rules) — agent rules first; exact-duplicate lines suppressed"
  - "D-11/RT-01: response_speed instant(0-2s)/human(DEBOUNCE range)/slow(3x range)/manual(exact seconds); MAX_BUFFER_TIME cap always applied"
  - "_TONE_LINES keys include the preset name to satisfy PMT-02 assertion (Friendly present in prompt)"
metrics:
  duration_minutes: 25
  completed_date: "2026-06-24"
  tasks_completed: 3
  files_modified: 2
---

# Phase 11 Plan 03: Prompt Assembly and Runtime Summary

Phase 11 Plan 03 rewrites `build_system_prompt` to the BRIEF §7 fixed block order with single-source-per-block semantics, collapses the 3-source tone to `_TONE_LINES` dict, adds `dialogue_flow` numbered stages, adds `[АРГУМЕНТЫ И ФАКТЫ]` block with anti-hallucination guard, deduplicates rules across agent+campaign, and wires `response_speed` into the listener debounce.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Rewrite build_system_prompt §7 order + single-source tone + dialogue_flow + facts guard + rules dedup | 77c05e4 | app/services/ai_engine.py |
| 2 | Update get_context cached SELECT with response_speed/response_delay_seconds | e160b8d | app/services/ai_engine.py |
| 3 | Wire response_speed into listener debounce (RT-01) | 9f978e8 | app/services/listener.py |

## What Was Built

### Task 1: build_system_prompt rewrite (ai_engine.py)

**New module-level constants and helpers:**
- `_PROMPT_FACTS_GUARD`: anti-hallucination guard ("Use only the facts... don't invent") mirroring `_PROMPT_PRODUCT_GUARD` phrasing (D-12)
- `_TONE_LINES`: dict mapping tone_preset enum values → single descriptive lines. Keys include the preset name verbatim (e.g. "Tone: Friendly — ...") so PMT-02's "Friendly in prompt" assertion holds
- `_dedup_rules(*texts)`: splits each text by line, strip+lower normalisation for comparison, dict.fromkeys-style insertion-order-preserving dedup; agent rules first

**Removed:**
- `_PROMPT_DIALOGUE_GOAL` static constant (tombstone comment left, constant deleted)
- Static `<dialogue_goal>` block append
- All voice_baseline / tone_of_voice / tone_spec / 3-source `<tone>` assembly logic

**New §7 block order:**
1. `<role>` — who_is_agent + real-person camouflage (always rendered)
2. `<company>` — company_info (conditional)
3. `<product>` — knowledge_base + `_PROMPT_PRODUCT_GUARD` (conditional)
4. `<tone>` — single line from `_TONE_LINES[tone_preset]` (conditional, D-03)
5. `<task_audience>` — primary_goal + audience_hints from campaign (conditional, D-13/PMT-06)
6. `<dialogue_flow>` — numbered stages from campaign.dialogue_flow JSONB (conditional, D-04/D-06)
7. `<arguments_facts>` — arguments_facts + `_PROMPT_FACTS_GUARD` (conditional, D-12)
8. `<rules>` — `_dedup_rules(agent_rules, campaign_rules)` (conditional, D-14)
9. `<language>` — mirror-language instruction (conditional on mirror_language flag)
10. `<banlist>` (conditional)
11. `<out_of_scope>` (always)
12. `<tools>` (always)
13. `<message_style>` — ФОРМАТ ОТВЕТА (always, was incorrectly before tools)
14. Contact line + injection guard (always last)

### Task 2: get_context cached SELECT widened (ai_engine.py)

`get_context` (TTL-cached, used by listener) now SELECTs `response_speed` and `response_delay_seconds` from `ai_contexts` alongside the existing fields. `response_speed` defaults to `"human"` when NULL — back-compatible with existing debounce behavior.

### Task 3: listener debounce wired to response_speed (listener.py)

`schedule_ai_response` now branches on `context.get("response_speed")`:
- `instant` → `random.uniform(0, 2.0)s`
- `slow` → `random.uniform(DEBOUNCE_MIN*3, DEBOUNCE_MAX*3)s` (≈ 60–540s)
- `manual` → exact `float(response_delay_seconds)` with fallback to `DEBOUNCE_MIN`
- `human` (default/missing) → existing `random.uniform(DEBOUNCE_MIN, DEBOUNCE_MAX)` (no behavior change)

`MAX_BUFFER_TIME - buffer_age` cap applied to ALL modes (threat T3).

`handle_incoming_message` loads agent context via `ai_engine.get_context` (cached, TTL 60s) and injects `response_speed` + `response_delay_seconds` into the context dict before calling `schedule_ai_response`. Error-resilient: falls back to `"human"` on any exception.

`queue.py` untouched (Pitfall 1: separate subsystem per CLAUDE.md).

## Test Results

| Test | Before | After |
|------|--------|-------|
| test_ai_engine.py (10 tests) | 3 pass + 7 xfail | 10 pass (all PMT-01..07 GREEN) |
| test_listener_response_speed.py (5 tests) | 2 pass + 3 xfail | 5 pass (RT-01 GREEN) |
| Full suite | 651 passing, 61 failing | 660 passing, 61 failing (9 xfail → green) |

## Deviations from Plan

**1. [Rule 1 - Bug] _TONE_LINES keys needed to include preset name verbatim for PMT-02**
- **Found during:** Task 1 test run
- **Issue:** Initial `_TONE_LINES["Friendly"]` was `"Tone: warm and friendly."` — PMT-02 asserts `"Friendly" in prompt`. The word "Friendly" did not appear in the rendered line.
- **Fix:** Changed all _TONE_LINES values to include the preset name: `"Tone: Friendly — warm and approachable. ..."`. This is good practice anyway (the LLM sees the canonical label explicitly).
- **Files modified:** `app/services/ai_engine.py`
- **Commit:** 77c05e4

## Known Stubs

None — all new fields are wired through to the system prompt and listener debounce. `dialogue_flow`, `arguments_facts`, `campaign_rules` are all read from the campaign sub-dict and rendered conditionally. `response_speed` is fetched via cached `get_context` and drives actual `asyncio.sleep` delay.

## Self-Check: PASSED

Files exist:
- app/services/ai_engine.py: FOUND
- app/services/listener.py: FOUND

Commits:
- 77c05e4: Task 1 — build_system_prompt rewrite: FOUND
- e160b8d: Task 2 — get_context SELECT widened: FOUND
- 9f978e8: Task 3 — listener response_speed debounce: FOUND
