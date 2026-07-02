---
phase: quick-260702-kf2
plan: 01
subsystem: analytics
tags: [analytics, kpi, since-window, backend]
requires: []
provides:
  - "optional ?since=1d|7d|30d|90d on all 4 analytics cards-endpoints"
affects:
  - "app/routers/analytics.py::_compute_cards + workspace/campaign/agent/sender endpoints"
tech-stack:
  added: []
  patterns:
    - "period filter via :days bind-param + (:days || ' days')::INTERVAL (mirrors /analytics/llm)"
key-files:
  created:
    - tests/test_phase5_analytics_since.py
  modified:
    - app/routers/analytics.py
decisions:
  - "since=None → all-time byte-identical (empty period clauses, no :days in SQL)"
  - "leads/finishes filtered by conversations.updated_at (best status-change proxy; no dedicated timestamp)"
  - "contacts_messaged/registered_contacts period-agnostic (campaign progress numerator/denominator)"
metrics:
  duration: ~10min
  completed: 2026-07-02
requirements: [KF2-SINCE-01]
---

# Quick Task 260702-kf2: KPI ?since on Analytics Cards Endpoints Summary

Added an optional `?since=1d|7d|30d|90d` query-param to all 4 analytics cards-endpoints (workspace/campaigns/agents/senders), temporally filtering period-sensitive KPIs while keeping `since=None` byte-identical to the current all-time behaviour.

## What Changed

- `app/routers/analytics.py::_compute_cards` gained a `since: Optional[str] = None` parameter. When set, three empty-by-default clauses are populated and injected into the relevant COUNT/spend SQL:
  - `msg_time_clause` → `m.created_at >= NOW() - (:days || ' days')::INTERVAL` on **sent** and **replied**
  - `conv_time_clause` → `c.updated_at >= NOW() - (:days || ' days')::INTERVAL` on **leads** and **finishes**
  - `llm_time_clause` → `lc.created_at >= NOW() - (:days || ' days')::INTERVAL` on **llm_spend**
- The days value is bound as `:days` (added to both `params` and `llm_params`), never f-string-interpolated — same pattern as the existing `/analytics/llm` endpoint. When `since=None` the clauses stay empty strings and `:days` never appears in any SQL, so `params` staying without the key is safe.
- `contacts_messaged` / `registered_contacts` (campaign progress numerator/denominator) are deliberately **not** period-filtered — documented inline.
- All 4 endpoint signatures now expose `since: Optional[Literal["1d","7d","30d","90d"]] = None` and pass `since=since` into `_compute_cards`. Invalid values → 422 (Literal validation).
- Response shape `AnalyticsCards` unchanged.

## Endpoint Signatures (for frontend wiring)

All 4 accept the same optional query-param:

| Endpoint | Param | Type | Default |
| -------- | ----- | ---- | ------- |
| `GET /api/v1/analytics/workspace` | `since` | `1d \| 7d \| 30d \| 90d` (optional) | omitted → all-time |
| `GET /api/v1/analytics/campaigns/{campaign_id}` | `since` | `1d \| 7d \| 30d \| 90d` (optional) | omitted → all-time |
| `GET /api/v1/analytics/agents/{agent_id}` | `since` | `1d \| 7d \| 30d \| 90d` (optional) | omitted → all-time |
| `GET /api/v1/analytics/senders/{sender_id}` | `since` | `1d \| 7d \| 30d \| 90d` (optional) | omitted → all-time |

Omitting `since` yields the current all-time response. Any value outside the 4-member set → 422.

## Tests

New file `tests/test_phase5_analytics_since.py` (10 tests): regression (stale 40d rows counted all-time without since), sent/replied window (stale excluded / fresh included), leads/finishes by `updated_at`, llm_spend by `lc.created_at`, `contacts_messaged` period-agnostic, all-4-endpoints smoke `?since=30d`, and bad-since 422.

Verified via test-overlay from the worktree (isolated compose project `kf2wt`, ephemeral `db-test`, `--no-deps`):

```
tests/test_phase5_analytics_since.py ..........            [ 23%]
tests/test_phase5_analytics.py ..............              [ 55%]
tests/test_phase5_analytics_correctness.py .........       [ 76%]
tests/test_phase5_1_llm_aggregates.py ..........           [100%]
======================== 43 passed, 4 warnings ========================
```

(The 4 warnings are pre-existing — sync test functions in `test_phase5_analytics.py` carrying the module-level asyncio mark; untouched by this task.)

RED confirmation before Task 2: the since-filtering tests failed as expected (`assert 1 == 0` on `sent`) while the regression test passed.

## Deviations from Plan

None - plan executed exactly as written.

## Notes for Orchestrator

- NOT deployed (api not rebuilt against prod compose) — deploy left to orchestrator per instructions.
- No DB migration, no schema change, no prod DB writes.
- Backward-compat verified: existing analytics suites stay green unmodified with `since=None`.

## Commits

- `3f56f61` test(260702-kf2): add failing since-window tests for analytics cards
- `0bd38c5` feat(260702-kf2): optional ?since window on analytics cards endpoints

## Self-Check: PASSED
