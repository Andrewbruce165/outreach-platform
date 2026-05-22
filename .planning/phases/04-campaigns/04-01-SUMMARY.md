---
phase: 04-campaigns
plan: 01
subsystem: planning
tags: [audit, campaigns, webhook, function-calling, todo-inventory, migration-planning]

# Dependency graph
requires:
  - phase: 03-agents-ai-templates
    provides: dropped ai_contexts.webhook_functions / document_webhook_url / max_message_length columns (migration 015); 7 TODO(phase-4) markers added for Campaign-level reconnection
provides:
  - "Verified TODO(phase-4) inventory: 10 markers with file:line + closure plan"
  - "Recovered ai_contexts.webhook_functions internal storage shape from init commit 54430ec"
  - "Locked decisions for 6 open questions (5 from RESEARCH + 1 new D-04 override)"
  - "Q1: message_queue.campaign_id NULLable + ON DELETE SET NULL (overrides CONTEXT.md D-16)"
  - "Q6: campaigns.status VARCHAR(20)+CHECK (overrides CONTEXT.md D-04 SQLEnum)"
  - "Anti-pattern defence list locked for downstream planners (rate constants, model ID, brand prompt)"
  - "Per-plan scope distribution for 04-02..04-05 with explicit AUDIT section cross-references"
affects: [04-02-campaigns, 04-03-schedule, 04-04-queue-rewrite, 04-05-signals-tools]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Audit-as-artifact: aggregating live-code grep + git-history archaeology + canonical-refs cross-check into a single immutable downstream context document"

key-files:
  created:
    - .planning/phases/04-campaigns/04-01-AUDIT.md
  modified: []

key-decisions:
  - "Q1: message_queue.campaign_id NULLable + ON DELETE SET NULL (overrides CONTEXT.md D-16 NOT NULL — hard-delete UX over single-day BD-clean argument)"
  - "Q2: POST /campaigns/{id}/duplicate ships in Phase 4 Plan 04-02 — copy row + campaign_senders, exclude queue/assignment runtime state"
  - "Q3: On finish_conversation / transfer_to_manager — send LLM text_content to contact FIRST, then flip ai_enabled=false (clean farewell UX)"
  - "Q4: Workspace isolation on campaign_senders via API validation + NOT NULL workspace_id FK (defence-in-depth)"
  - "Q5: CampaignEnqueueWorker uses one DB transaction per contact; race protected by UNIQUE(campaign_id, contact_phone)"
  - "Q6 (new override): campaigns.status VARCHAR(20)+CHECK over SQLEnum (PG ALTER TYPE ADD VALUE cannot run in transaction; homogeneity with conversations.status; senders.role precedent)"
  - "TODO #10 (listener.py:707 document_webhook_url) — DO NOT restore; replace TODO with permanent comment per CONTEXT.md item 6"

patterns-established:
  - "Audit document as authoritative override layer: when CONTEXT.md decisions conflict with live-code reality discovered during audit, AUDIT.md wins for downstream planners; CONTEXT.md is not retroactively edited"
  - "Anti-pattern defence list: explicit enumeration of code/constants downstream plans MUST NOT modify (defends empirical work and known-out-of-scope concerns)"

requirements-completed: [CAMP-15, CAMP-16, CAMP-17]

# Metrics
duration: 12min
completed: 2026-05-22
---

# Phase 4 Plan 01: Pre-Implementation Audit Summary

**Locked-in pre-execution audit for Phase 4: 10 TODO(phase-4) markers inventoried with closure plans, ai_contexts.webhook_functions internal storage shape recovered from git init (54430ec) as baseline for campaigns.tools JSONB + Pydantic ToolSpec, 5 open questions resolved + 1 new D-04 override (campaigns.status VARCHAR+CHECK over SQLEnum due to PostgreSQL ALTER TYPE transaction limitation), anti-pattern defence list locked for downstream planners.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-05-22T08:00:42Z
- **Completed:** 2026-05-22T08:12:46Z
- **Tasks:** 1
- **Files modified:** 1 (created)
- **Files read during audit:** 14 (PROJECT.md, STATE.md, CLAUDE.md, 04-CONTEXT.md, 04-RESEARCH.md, 04-VALIDATION.md, 04-01-PLAN.md, config.json, ai_engine.py, queue.py, listener.py, rotation.py, agents.py, folders.py, `models/\_\_init\_\_.py`, CONCERNS.md + git show 54430ec for webhook_functions recovery)

## Accomplishments

- Inventoried all 10 `TODO(phase-4)` markers across `app/` (matches CONTEXT.md expectation: 10) with explicit closure plan per marker (3 → 04-02, 3 → 04-04, 4 → 04-05).
- Catalogued the 8 reusable webhook/tools/signals functions and classified each as REUSE / EXTEND / REWRITE / KEEP for Phase 4.
- Recovered the legacy `ai_contexts.webhook_functions` JSONB shape from the initial telegram-api import (`git show 54430ec`) — including the crucial insight that `parameters` is an **array** of param-spec objects (NOT an OpenAI JSON Schema), and that `build_tools` performs the conversion to OpenAI format on every call. This locks the shape for migration 016's `campaigns.tools` column and the Pydantic `ToolSpec` model.
- Enumerated every global schedule constant in `queue.py` (lines 38-65) and classified each as "remove in 04-03" (5 items) vs "DO NOT TOUCH per CLAUDE.md" (11 items).
- Resolved 5 RESEARCH.md open questions + discovered and resolved 1 additional override (Q6).
- Wrote per-plan ownership table (Section 8) that explicitly cross-references which audit sections each downstream plan MUST read.

## Task Commits

1. **Task 1: Inventory existing webhook+tools+signals code + write 04-01-AUDIT.md** — `8319e9c` (docs)

## Files Created/Modified

- `.planning/phases/04-campaigns/04-01-AUDIT.md` — 9-section audit document with 6 tables (TODO inventory, webhook+tools inventory, schedule constants, open question resolutions, anti-pattern defence, per-plan distribution) + recovered JSON shape baseline + explicit override notes.

## Decisions Made

- **Q1 (NULLable over NOT NULL for `message_queue.campaign_id`):** overrides CONTEXT.md D-16. Rationale: D-07 hard delete of `done` campaigns with `ON DELETE SET NULL` requires NULLable on the child column. The "БД чистая" argument from CONTEXT.md is true on day-one but wrong over the data lifetime.
- **Q6 (VARCHAR+CHECK over SQLEnum for `campaigns.status`):** overrides CONTEXT.md D-04. Rationale: PostgreSQL `ALTER TYPE ... ADD VALUE` cannot run inside a transaction block (RESEARCH.md Pitfall 2); `senders.role` and `conversations.status` precedents already established String+CHECK; Lovable UI works with raw strings; no API-layer benefit from SQLEnum.
- **Q3 (send LLM text_content BEFORE status flip):** for `finish_conversation` / `transfer_to_manager`, deliver the LLM's farewell line before setting `ai_enabled=false`. Order: send → UPDATE → fire webhook.
- **TODO #10 (`listener.py:707` document_webhook_url):** explicit non-restoration. Plan 04-05 closes the TODO by deletion + permanent comment, NOT by re-implementing document_webhook delivery (that lives in `campaigns.tools` if a client wants it).
- **Audit-document supremacy:** when CONTEXT.md and AUDIT.md conflict, AUDIT.md wins for downstream planners. CONTEXT.md is not retroactively edited — this preserves the planning-history audit trail.

## Deviations from Plan

None — plan executed exactly as written. Plan 04-01 was a single-task analytical plan and the task produced the artifact exactly as specified in the `<action>` block.

## Issues Encountered

None. One worth flagging for context: the recovered `webhook_functions` shape revealed that the legacy `webhook_method` field was read by neither `build_tools` nor `execute_webhook` (the latter always uses POST). The AUDIT documents this and recommends keeping `webhook_method: Literal["POST"]` as a Pydantic-default placeholder for forward compatibility rather than dropping the field outright. This is a recommendation, not a deviation.

## User Setup Required

None — no external service configuration required for this audit plan.

## Next Phase Readiness

**Ready for Plan 04-02 execution.** Downstream planners (04-02, 04-03, 04-04, 04-05) must include `@.planning/phases/04-campaigns/04-01-AUDIT.md` in their `<context>` block — Section 8 of AUDIT specifies exactly which sections each plan reads.

Key blockers/concerns lifted by this audit:

- Migration 016 shape for `campaigns.tools` JSONB is now locked (Section 4).
- The `message_queue.campaign_id` NOT NULL vs NULLable conflict is resolved (Q1: NULLable).
- The `campaigns.status` SQLEnum vs VARCHAR conflict is resolved (Q6: VARCHAR).
- All 10 closure plans for TODO(phase-4) markers are explicit.

No new blockers introduced.

## Self-Check: PASSED

- FOUND: `.planning/phases/04-campaigns/04-01-AUDIT.md`
- FOUND: `.planning/phases/04-campaigns/04-01-SUMMARY.md`
- FOUND: commit `8319e9c` (Task 1: write phase 4 pre-implementation audit)

---
*Phase: 04-campaigns*
*Completed: 2026-05-22*
