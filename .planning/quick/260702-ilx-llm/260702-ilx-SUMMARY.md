---
phase: quick-260702-ilx
plan: 01
subsystem: analytics
tags: [analytics, llm, pricing, dashboard]
requires: [llm_calls table, /analytics/llm, /analytics/workspace]
provides: [app.services.llm_pricing.compute_spend_cents, AnalyticsCards.llm_spend_usd_cents, real LLMAggregatesResponse.spend_usd_cents]
affects: [app/routers/analytics.py, app/schemas/__init__.py]
tech-stack:
  added: []
  patterns: [longest-prefix model pricing, additive optional schema field, round-once cent accumulation]
key-files:
  created: [app/services/llm_pricing.py, tests/test_llm_pricing.py]
  modified: [app/routers/analytics.py, app/schemas/__init__.py, tests/test_phase5_1_llm_aggregates.py, tests/test_phase5_analytics.py]
decisions: []
metrics:
  duration: ~7min
  completed: 2026-07-02
---

# Quick Task 260702-ilx: LLM Spend on Dashboard Summary

Real per-model USD LLM spend now surfaces on both `/analytics/llm` (`spend_usd_cents`) and the general workspace dashboard (`/analytics/workspace` → `AnalyticsCards.llm_spend_usd_cents`), replacing the v1 `spend_usd_cents=0` stub. Pricing lives entirely in code (`app/services/llm_pricing.py`); no schema/migration change.

## What was built

- **`app/services/llm_pricing.py`** — `PRICING` dict (USD per 1M tokens) + `DEFAULT_PRICE` (zero) + `compute_spend_cents(rows)`. Model resolution is by **longest-prefix match** (so `gpt-5-mini-2025-08-07` prices as `gpt-5-mini`, and `claude-3-5-sonnet-...` beats a shorter key). Unknown models → 0 cents + one `WARNING`, never raises. `None` token counts treated as 0. Floats accumulated and rounded **once** at the end (no per-row drift).
- **`/analytics/llm`** — kept the existing single-SUM query (response shape unchanged) and added a second `GROUP BY lc.model` query with the same `WHERE`/scope/window; result priced via `compute_spend_cents` into `spend_usd_cents`. Docstring updated (stub wording removed).
- **`AnalyticsCards`** — additive `llm_spend_usd_cents: int = 0` (mirrors the existing `contacts_messaged: int = 0` optional-with-default pattern → no existing frontend consumer breaks). `_compute_cards` runs an **all-time** `GROUP BY model` on `llm_calls` scoped by its own columns: `campaign_id`→`lc.campaign_id`, `ai_context_id`→`lc.agent_id`, `sender_id`→`lc.sender_id`. Unknown/unmapped scope → skip (leave 0, no raise).

## Prod models found & how each is priced

`SELECT model, provider, COUNT(*) FROM llm_calls GROUP BY model, provider` (read-only) returned exactly two distinct models:

| model string | provider | count | priced as | input / output (USD per 1M) |
|---|---|---|---|---|
| `gpt-5-mini-2025-08-07` | (null) | 51 | `gpt-5-mini` (prefix match) | 0.25 / 2.00 |
| `claude-sonnet-5` | anthropic | 6 | `claude-sonnet-5` (exact key added) | 3.00 / 15.00 |

`claude-sonnet-5` was NOT covered by the plan's initial key list, so a dedicated `claude-sonnet-5` key was added (priced at the Claude Sonnet tier: 3.00 / 15.00). All prod rows are now priced; none fall to the DEFAULT_PRICE=0 path. Other known keys (`gpt-4o-mini`, `gpt-4o`, `claude-3-5-sonnet`, `claude-3-5-haiku`) are retained for legacy/future rows.

## Frontend integration (for the Lovable regen)

- **Общий дашборд card:** read `llm_spend_usd_cents` from `GET /api/v1/analytics/workspace` (and the same field on `/analytics/campaigns/{id}`, `/analytics/agents/{id}`, `/analytics/senders/{id}` — identical `AnalyticsCards` shape). All-time spend.
- **LLM tab:** read `spend_usd_cents` from `GET /api/v1/analytics/llm` (respects the `since` window and `scope`).
- Both values are **integer cents** — divide by 100 for USD.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed two pre-existing exact key-set assertions**
- **Found during:** Task 2 verification run.
- **Issue:** `tests/test_phase5_analytics.py::test_workspace_endpoint_returns_4_metrics` and `::test_all_4_endpoints_same_schema` assert the `AnalyticsCards` top-level key set verbatim; the additive `llm_spend_usd_cents` field (intended, additive-only per plan) broke the equality.
- **Fix:** Added `llm_spend_usd_cents` to both expected key sets + a `== 0` assertion in the workspace test.
- **Files modified:** `tests/test_phase5_analytics.py`
- **Commit:** dd42be3

**2. [Rule 3 - Blocking] Test-overlay could not run as-documented from the worktree**
- **Issue:** The base `docker-compose.yml` hardcodes `container_name: outreach-platform-db`/`-api`, which conflict with the **running prod** containers when the overlay is invoked from the per-agent worktree (a different compose project). Also, the worktree had no `.env`, so compose `${VAR}` interpolation failed (`telegram_api_id` empty → Settings validation error).
- **Fix (test-infra only, no prod writes):** brought up the ephemeral `db-test` explicitly, then ran `run --rm --no-deps api pytest` (so the prod-named `db` dependency is not started), and copied prod `.env` into the worktree purely for compose variable substitution (`.env` is gitignored, NOT committed, and the overlay does NOT mount it into the container). Ephemeral `db-test` torn down after the run. Prod DB never touched (only the read-only `SELECT ... GROUP BY model` inspection).

## Test Results

- `tests/test_llm_pricing.py`: **12 passed** (RED→GREEN TDD).
- Task 2 suite `tests/test_phase5_1_llm_aggregates.py tests/test_phase5_analytics.py tests/test_phase5_analytics_correctness.py tests/test_llm_pricing.py`: **45 passed, 0 failed** (4 pre-existing PytestWarnings for non-async tests marked asyncio — out of scope).

## Not deployed

Backend-only. User must run `docker compose up -d --build api` on the server to deploy (listener not affected, no migration to apply).

## Self-Check: PASSED

- FOUND: app/services/llm_pricing.py
- FOUND: tests/test_llm_pricing.py
- FOUND: .planning/quick/260702-ilx-llm/260702-ilx-SUMMARY.md
- FOUND commit: a909e2a (Task 1)
- FOUND commit: dd42be3 (Task 2)
