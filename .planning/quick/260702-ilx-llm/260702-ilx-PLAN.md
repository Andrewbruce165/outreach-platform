---
phase: quick-260702-ilx
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/llm_pricing.py
  - app/routers/analytics.py
  - app/schemas/__init__.py
  - tests/test_llm_pricing.py
  - tests/test_phase5_1_llm_aggregates.py
autonomous: true
requirements: [ILX-LLM-SPEND]
must_haves:
  truths:
    - "GET /analytics/llm returns a real non-zero spend_usd_cents when llm_calls rows exist with priced models"
    - "GET /analytics/workspace payload carries an llm_spend_usd_cents field the dashboard can read"
    - "Unknown models fall back to a safe default (0) with a logged warning, never crash"
    - "Existing frontend consumers of /analytics/workspace and /analytics/llm keep working (additive-only, response shape unchanged except new optional field)"
  artifacts:
    - path: "app/services/llm_pricing.py"
      provides: "Per-model USD pricing table + compute_spend_cents(model_token_rows) helper"
      contains: "def compute_spend_cents"
    - path: "app/routers/analytics.py"
      provides: "Real spend computation in /analytics/llm + llm_spend on /analytics/workspace"
      contains: "compute_spend_cents"
    - path: "tests/test_llm_pricing.py"
      provides: "Unit tests for pricing table + fallback"
      contains: "def test_"
  key_links:
    - from: "app/routers/analytics.py::llm_aggregates"
      to: "app/services/llm_pricing.py::compute_spend_cents"
      via: "GROUP BY model SQL → Python price multiply"
      pattern: "compute_spend_cents"
    - from: "app/routers/analytics.py::_compute_cards"
      to: "AnalyticsCards.llm_spend_usd_cents"
      via: "per-workspace GROUP BY model spend aggregate"
      pattern: "llm_spend_usd_cents"
---

<objective>
Surface real LLM spend on the workspace dashboard. Today `GET /analytics/llm` returns `spend_usd_cents=0` (explicit v1 stub) and the general dashboard (`GET /analytics/workspace`) carries no spend field at all. This plan adds a per-model pricing table in backend code, computes real USD spend from `llm_calls` token counts, and exposes it on both the LLM tab endpoint and the general workspace analytics payload.

Purpose: The user wants LLM costs visible on the общий дашборд.
Output: `app/services/llm_pricing.py` (pricing table + compute helper), real spend in `/analytics/llm`, additive `llm_spend_usd_cents` field on `AnalyticsCards`, and tests.

Backend-only. Do NOT touch the frontend repo (`/root/apps/aimly/aimly-tg-outreach`) — the Lovable frontend is regenerated separately; the summary must tell the frontend which field to read.
</objective>

<context>
@/root/apps/aimly/tg-outreach/CLAUDE.md
@/root/apps/aimly/tg-outreach/app/routers/analytics.py
@/root/apps/aimly/tg-outreach/app/schemas/__init__.py
@/root/apps/aimly/tg-outreach/tests/test_phase5_1_llm_aggregates.py

<interfaces>
<!-- llm_calls columns (app/models/__init__.py:817) the SQL aggregates over -->
llm_calls: model VARCHAR(50) NOT NULL, provider TEXT NULL ('openai'|'anthropic'),
           prompt_tokens INT NULL, completion_tokens INT NULL, total_tokens INT NULL,
           workspace_id UUID NOT NULL, campaign_id UUID NULL, created_at TIMESTAMPTZ.

<!-- Existing /analytics/llm SQL (analytics.py:492) aggregates a single SUM over the whole
     since-window. To price per-model we need a GROUP BY model variant that returns
     (model, sum_prompt_tokens, sum_completion_tokens) rows. -->

<!-- AnalyticsCards (schemas/__init__.py:1019) already has optional-with-default fields
     (contacts_messaged: int = 0). Add llm_spend_usd_cents: int = 0 the same way — additive,
     no break for existing frontend consumers. -->

<!-- LLMAggregatesResponse (schemas/__init__.py:1139) has spend_usd_cents: int — keep the
     field name and type, just fill it with a real value. -->
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Pricing table + compute_spend_cents helper</name>
  <files>app/services/llm_pricing.py, tests/test_llm_pricing.py</files>
  <behavior>
    - compute_spend_cents([("gpt-5-mini", 1_000_000, 1_000_000)]) returns cents for known model using its input+output rate.
    - Prices are USD per 1M tokens; result is rounded to integer cents.
    - Multiple rows sum: compute_spend_cents([(modelA, p, c), (modelB, p, c)]) == sum of each row priced.
    - Unknown model → contributes 0 cents AND logs a warning (assert via caplog), never raises.
    - Empty input → 0.
    - None token counts treated as 0 (defensive — llm_calls.prompt_tokens is nullable).
  </behavior>
  <action>
    Create app/services/llm_pricing.py with:
      1. `PRICING` dict constant: `{model_name: {"input": usd_per_1m, "output": usd_per_1m}}`.
         Cover the models actually used. KNOWN values (as of 2026-07, USD per 1M tokens):
           - "gpt-5-mini": input 0.25, output 2.00
           - "gpt-4o-mini": input 0.15, output 0.60  (legacy rows may exist)
           - "gpt-4o": input 2.50, output 10.00
           - "claude-3-5-sonnet": input 3.00, output 15.00
           - "claude-3-5-haiku": input 0.80, output 4.00
         IMPORTANT — model strings in llm_calls are often versioned/suffixed
         (e.g. "gpt-5-mini-2025-08-07", "claude-3-5-sonnet-20241022"). Match by
         PREFIX: normalize by checking `model_str.startswith(key)` against the
         longest matching key first (sort keys by length desc so
         "claude-3-5-sonnet" wins over a hypothetical "claude-3"). Task 2 will
         supply the actual distinct model strings from prod — extend/adjust keys
         to cover whatever it finds.
      2. `DEFAULT_PRICE = {"input": 0.0, "output": 0.0}` — safe zero fallback.
      3. `def compute_spend_cents(rows: list[tuple[str, int | None, int | None]]) -> int:`
         where each row is (model, prompt_tokens, completion_tokens).
         For each row: resolve price by longest-prefix match; if no match, use
         DEFAULT_PRICE and `logger.warning("llm_pricing: unknown model %s — spend counted as 0", model)`.
         cents_for_row = round((prompt_tokens or 0)/1e6 * price["input"] * 100
                              + (completion_tokens or 0)/1e6 * price["output"] * 100).
         Accumulate as a float and round ONCE at the end to avoid per-row rounding drift
         (sum floats, round total → int cents). Return int total cents.
         Module-level `logger = logging.getLogger(__name__)`.
    Write tests/test_llm_pricing.py: pure sync unit tests (no DB, no async) covering
    every bullet in <behavior>. Use caplog for the unknown-model warning assertion.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_llm_pricing.py -x -q</automated>
  </verify>
  <done>tests/test_llm_pricing.py passes; compute_spend_cents prices known models, sums rows, falls back to 0 + warns on unknown, handles None/empty.</done>
</task>

<task type="auto">
  <name>Task 2: Inspect prod distinct models + wire spend into both endpoints</name>
  <files>app/routers/analytics.py, app/schemas/__init__.py, tests/test_phase5_1_llm_aggregates.py</files>
  <action>
    STEP A — Inspect prod distinct models (read-only, informs pricing coverage):
      docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c \
        "SELECT model, provider, COUNT(*) FROM llm_calls GROUP BY model, provider ORDER BY COUNT(*) DESC;"
      For any model string NOT covered by a prefix key in PRICING (Task 1), add/adjust
      a key in app/services/llm_pricing.py so it prices correctly (use nearest known
      price for a versioned variant). If a genuinely unknown/unpriceable model appears,
      leave it to the DEFAULT_PRICE=0 + warning path (do not guess wildly).

    STEP B — /analytics/llm real spend (replace the spend_usd_cents=0 stub, analytics.py:449):
      Keep the existing single-SUM `row` query for total_calls / avg_latency_ms /
      token sums UNCHANGED (response shape unchanged). ADD a second query in the same
      function, same WHERE clause + scope_clause + params:
        SELECT lc.model,
               COALESCE(SUM(lc.prompt_tokens),0)::BIGINT     AS p,
               COALESCE(SUM(lc.completion_tokens),0)::BIGINT AS c
        FROM llm_calls lc
        WHERE lc.workspace_id = :wid
          AND lc.created_at >= NOW() - (:days || ' days')::INTERVAL
          {scope_clause}
        GROUP BY lc.model
      Import `from app.services.llm_pricing import compute_spend_cents`.
      spend = compute_spend_cents([(r.model, r.p, r.c) for r in model_rows]).
      Pass spend into LLMAggregatesResponse(spend_usd_cents=spend). Update the
      endpoint docstring — remove the "v1 stub / 0 / deferred to v2" wording.

    STEP C — General dashboard field (additive, analytics.py::_compute_cards + AnalyticsCards):
      Add `llm_spend_usd_cents: int = 0` to AnalyticsCards (schemas/__init__.py:1019),
      mirroring the existing `contacts_messaged: int = 0` optional-with-default pattern
      (so existing frontend consumers and all 4 endpoints stay valid). Update the class
      docstring noting it's all-time LLM spend in cents, campaign/workspace/agent/sender
      scope respected.
      In _compute_cards, add a GROUP BY model query scoped the SAME way as the other
      counts — reuse the workspace_id + the scope tuple. Since _compute_cards scopes via
      conversations (c.*), but llm_calls has its own workspace_id/campaign_id/agent_id/
      sender_id columns, query llm_calls directly:
        base WHERE lc.workspace_id = :wid, plus IF scope is not None map the scope column:
          "campaign_id" -> lc.campaign_id, "ai_context_id" -> lc.agent_id,
          "sender_id" -> lc.sender_id  (all NULLable on llm_calls; simple `AND lc.<col> = :scope_val`).
        This is ALL-TIME (no since-window) to match _compute_cards' all-time semantics (D-14).
        GROUP BY lc.model, then compute_spend_cents(...) and pass
        llm_spend_usd_cents=spend into the AnalyticsCards(...) constructor.
      Guard: unknown scope column → skip spend (leave 0), do not raise.

    STEP D — Extend tests in tests/test_phase5_1_llm_aggregates.py:
      - Add a test: insert 2 llm_calls rows with a KNOWN model (e.g. "gpt-4o-mini")
        and known token counts, GET /analytics/llm, assert spend_usd_cents > 0 and
        equals compute_spend_cents of those rows (import the helper, compute expected).
      - Add a test: insert a row with an UNKNOWN model string → spend_usd_cents == 0
        (or unchanged), endpoint still 200 (fallback path, no crash).
      - Add a test: GET /analytics/workspace returns 200 and the body contains
        "llm_spend_usd_cents" key (additive field present). Insert a priced row and
        assert it is > 0.
      Follow the existing helpers (_bind, _auth_headers, test_sender_factory) in that file.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase5_1_llm_aggregates.py tests/test_phase5_analytics.py tests/test_phase5_analytics_correctness.py tests/test_llm_pricing.py -q</automated>
  </verify>
  <done>/analytics/llm returns real spend_usd_cents from per-model GROUP BY; AnalyticsCards carries llm_spend_usd_cents; workspace endpoint exposes it; unknown models fall back to 0; all listed test files pass green.</done>
</task>

</tasks>

<verification>
- Full analytics + pricing test suite green via test-overlay.
- `grep -n "spend_usd_cents=0" app/routers/analytics.py` returns nothing (stub removed).
- `grep -n "llm_spend_usd_cents" app/schemas/__init__.py app/routers/analytics.py` shows the additive field wired in both.
- No changes under /root/apps/aimly/aimly-tg-outreach (frontend untouched).
- No migration added (pricing lives in code, no schema change) — confirm no new file in migrations/.
</verification>

<success_criteria>
- Per-model pricing table exists in app/services/llm_pricing.py covering the models found in prod llm_calls, with a safe 0-default + logged warning for unknowns.
- GET /analytics/llm returns real USD spend in spend_usd_cents (shape otherwise unchanged).
- GET /analytics/workspace (AnalyticsCards) exposes llm_spend_usd_cents as an additive optional field — no existing consumer breaks.
- Tests cover known-model spend math and unknown-model fallback.
- Backend-only; deploy left to the user.
</success_criteria>

<output>
After completion, create `.planning/quick/260702-ilx-llm/260702-ilx-SUMMARY.md`.
In the summary, explicitly state:
  - Which model strings were found in prod and how each is priced (or defaulted).
  - The frontend should read `llm_spend_usd_cents` from GET /analytics/workspace for the
    общий дашборд card, and `spend_usd_cents` from GET /analytics/llm for the LLM tab
    (both are cents — divide by 100 for USD).
  - NOT deployed: user must run `docker compose up -d --build api` (listener not affected).
</output>
