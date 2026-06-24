---
phase: 10-pool-visibility
plan: 03
subsystem: pool-visibility
tags: [wave-3, pool-health, restriction-history, poolv-01, poolv-02, hlth-03, presentation-free]
dependency_graph:
  requires:
    - "10-01 (RED test contract — tests/test_pool_health.py + test_history_endpoint)"
    - "10-02 (SenderRestrictionEvent ORM model + sender_restriction_events table)"
  provides:
    - "CampaignResponse.pool_health {active, paused, total, earliest_resume_at} (POOLV-01)"
    - "attached_senders[].restriction_status + restricted_until (POOLV-02)"
    - "GET /senders/{slug}/restriction-events — workspace-scoped newest-first history (HLTH-03)"
    - "PoolHealth + RestrictionEventResponse schemas"
  affects:
    - "Wave 4 frontend (Plan 10-04): derives green/yellow/red badge from numeric pool_health; renders restriction history per sender"
tech_stack:
  added: []
  patterns:
    - "one-pass aggregate with COUNT(*) FILTER + MIN() FILTER (no per-sender N+1)"
    - "computed-field-in-_campaign_to_response shape (mirror _compute_is_exhausted: compute → pass to constructor)"
    - "workspace-scoped read endpoint via _load_sender_by_slug (opaque 404, defence-in-depth workspace_id filter)"
    - "restriction-field defs copied VERBATIM from SenderResponse (name/type consistency)"
key_files:
  created: []
  modified:
    - app/schemas/__init__.py
    - app/routers/campaigns.py
    - app/routers/senders.py
decisions:
  - "OQ#4: earliest_resume_at = MIN(restricted_until) among restricted senders (recheck horizon, not literal resume time)"
  - "pool_health is a REQUIRED field on CampaignResponse — single constructor (_campaign_to_response) wires it; no other path builds CampaignResponse"
  - "API stays presentation-free: numeric pool_health only, badge color derived on frontend"
  - "History endpoint LIMIT 200 newest-first to bound the response; raw_text/proxy returned (same-tenant only, no secrets per Plan 02)"
metrics:
  duration: ~5min
  completed: 2026-06-24
  tasks: 3
  files: 3
---

# Phase 10 Plan 03: Pool-Health & History-Endpoint Summary

Completed the Phase-10 backend: campaign responses now expose a one-pass numeric `pool_health` aggregate (POOLV-01) and per-sender `restriction_status`/`restricted_until` enrichment (POOLV-02), and a new workspace-scoped read endpoint serves the append-only restriction-event history per account (HLTH-03) — all pure-additive on existing computed-field and slug-lookup patterns, API kept presentation-free (badge logic lives on the frontend).

## What Was Built

### Task 1 — schemas (commit `b40bab7`)
- `PoolHealth(BaseModel)`: `active:int, paused:int, total:int, earliest_resume_at:Optional[datetime]=None` (D-08 names).
- `CampaignSenderAttach` +`restriction_status: Literal["none","spam_limited","frozen"]="none"` +`restricted_until: Optional[datetime]=None` — copied VERBATIM from `SenderResponse` L133-134; existing `@computed_field id` preserved.
- `CampaignResponse` +`pool_health: PoolHealth` (required, near `attached_senders`/`is_exhausted`).
- `RestrictionEventResponse(BaseModel)` with `model_config = ConfigDict(from_attributes=True)` mirroring migration-030 columns (id, event_type, source, category, restricted_until, raw_text, activity_slice, proxy, created_at).

### Task 2 — campaigns.py pool_health + enrichment (commit `cdc9e3b`)
- `_build_attached_senders`: extended the SELECT with `JOIN senders s ON s.id = cs.sender_id` and `s.restriction_status, s.restricted_until` in the projection; passed both into each `CampaignSenderAttach`. Existing `locked_by_*` subqueries + `ORDER BY cs.added_at` preserved.
- New `_compute_pool_health(db, campaign_id) -> PoolHealth`: ONE aggregate SELECT — `COUNT(*)` total, `COUNT(*) FILTER (restriction_status='none')` active, `COUNT(*) FILTER (<>'none')` paused, `MIN(restricted_until) FILTER (<>'none')` earliest_resume_at. Empty pool → all zeros / None (COALESCE via `or 0`).
- `_campaign_to_response` computes `pool_health` (mirror of the `_compute_is_exhausted` compute-then-pass shape) and passes it to the `CampaignResponse(...)` constructor. `PoolHealth` added to the campaigns.py schema import.

### Task 3 — senders.py history endpoint (commit `34eadd4`)
- `GET /senders/{slug}/restriction-events` → `list[RestrictionEventResponse]`, signature `(slug, ctx=Depends(auth_dep), db=Depends(get_db))`.
- Body: `_load_sender_by_slug(db, ctx, slug)` (workspace-scoped opaque 404) then `select(SenderRestrictionEvent).where(sender_id==sender.id, workspace_id==ctx.workspace_id).order_by(created_at.desc()).limit(200)`. Defence-in-depth `workspace_id` filter on the event SELECT in addition to the slug lookup. Read-only over the append-only log.
- Imported `SenderRestrictionEvent` from `app.models` and `RestrictionEventResponse` from `app.schemas`.

## Verification

| Check | Result |
|-------|--------|
| schema import smoke (`PoolHealth/RestrictionEventResponse/CampaignResponse/CampaignSenderAttach` + from_attributes) | ok |
| `pytest tests/test_pool_health.py` (test-overlay) | 2/2 GREEN (states + enrichment) |
| `pytest tests/test_restriction_audit.py::test_history_endpoint` | GREEN (newest-first + cross-tenant 404) |
| `pytest test_pool_health.py + test_restriction_audit.py` (full Phase-10) | 12/12 GREEN |
| `pytest test_campaign_router.py + test_phase5_1_campaign_v2_router.py + test_pool_endpoints.py` (regression on now-required pool_health) | 37/37 GREEN |
| `grep "pool_health" app/routers/campaigns.py` | present (one aggregate, no N+1) |
| `grep "_load_sender_by_slug" + "restriction-events"` app/routers/senders.py | present |
| No `badge_state`/color field on the API | confirmed (presentation-free) |

## Deviations from Plan

None — plan executed exactly as written. All tests run via test-overlay (`docker-compose.test.yml`, ephemeral `db-test` in tmpfs) per CLAUDE.md; no bare `pytest`, no `down -v`.

Note: `pool_health` is a REQUIRED (non-default) field on `CampaignResponse`. Verified there is exactly one constructor (`_campaign_to_response`) and it was wired; ran the broader campaign/pool router suites (37 tests) to confirm no serialization regression from the new required field.

## Deferred Issues

None from this plan. The pre-existing wide-suite failures (asyncpg multi-statement migration tests, phase5 inbox/bot-filter infra, onboarding/warmup) logged in `.planning/phases/10-pool-visibility/deferred-items.md` by Plan 10-02 remain out of scope — none touch pool-health or the history endpoint.

## Known Stubs

None. `pool_health` is computed from live `campaign_senders JOIN senders`; the history endpoint reads the real `sender_restriction_events` table populated by Plan 10-02 write-points. Both are fully wired, not stubs.

## Self-Check: PASSED
- FOUND: app/schemas/__init__.py (PoolHealth, RestrictionEventResponse)
- FOUND: app/routers/campaigns.py (_compute_pool_health, pool_health)
- FOUND: app/routers/senders.py (restriction-events endpoint)
- FOUND: commit b40bab7
- FOUND: commit cdc9e3b
- FOUND: commit 34eadd4
