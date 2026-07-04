---
phase: 260704-bty-batch-h
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/routers/queue.py
  - app/routers/proxy_pool.py
autonomous: true
requirements: [WR-01]
must_haves:
  truths:
    - "app/routers/queue.py no longer exists"
    - "app/routers/proxy_pool.py no longer exists"
    - "The app still imports cleanly (no dangling references to the removed router modules)"
    - "The test-overlay suite still passes after deletion"
  artifacts: []
  key_links:
    - from: "app/main.py"
      to: "app.routers.*"
      via: "include_router calls (neither removed module was ever mounted)"
      pattern: "include_router"
---

<objective>
Batch H (WR-01) from the checker+campaigns fix plan: delete two dead router files —
`app/routers/queue.py` and `app/routers/proxy_pool.py`.

Purpose: Both files import `from app.routers.auth import verify_api_key`, but
`app/routers/auth.py` does not exist anywhere in the repo — the files are therefore
unimportable. Neither is mounted in `app/main.py` (only the *service* module
`app.services.queue` is imported there, never these routers). They are pure dead code
and their broken imports are a trip hazard for future greps/refactors.

Output: Two files removed. No behavior change, no DB change, no migration.

Non-goals (DO NOT touch):
- The `ProxyPool` ORM model in `app/models` and the `proxy_pool` DB table — out of scope.
- `app/services/queue.py` (the live queue service) — unrelated, still imported by main + tests.
- `tests/conftest.py` / `tests/test_migration_012.py` references to the string `"proxy_pool"` —
  that is the DB **table** name in schema/truncate lists, NOT the router module.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.planning/reviews/260703-checker-campaigns-FIXPLAN.md

# Verified facts (orchestrator + planner, 2026-07-04):
# - app/routers/queue.py (5544 bytes) and app/routers/proxy_pool.py (8559 bytes) both exist.
# - app/routers/auth.py does NOT exist → both files are unimportable (broken import).
# - Neither router is mounted in app/main.py (no include_router for them).
# - grep across app/ and tests/ finds NO importer of either router module.
#   The only matches for "queue" are `from app.services import queue` (the SERVICE
#   module, in 4 test files) — a different module, MUST be left untouched.
</context>

<tasks>

<task type="auto">
  <name>Task 1: Re-confirm no importers, then delete the two dead router files</name>
  <files>app/routers/queue.py, app/routers/proxy_pool.py</files>
  <action>
    First, re-confirm nothing else imports these two ROUTER modules (defense against
    drift since planning). Run from the repo root:

      grep -rn "routers\.queue\|routers\.proxy_pool\|from app\.routers import queue\|from app\.routers import proxy_pool" app/ tests/ | grep -vE "app/routers/(queue|proxy_pool)\.py"

    Expected: NO output (empty). The grep intentionally excludes the two files being
    deleted (their own broken `from app.routers.auth import ...` line will match otherwise).

    Note: `from app.services import queue` in tests/test_queue_*.py is the live queue
    SERVICE — a DIFFERENT module. It MUST NOT match the pattern above and MUST NOT be
    changed. If it appears, you filtered wrong.

    If (and only if) the grep is empty, delete both dead router files:

      rm app/routers/queue.py app/routers/proxy_pool.py

    Do NOT touch app/services/queue.py, the ProxyPool ORM model, the proxy_pool DB
    table, or any conftest/test reference to the string "proxy_pool".
  </action>
  <verify>
    <automated>test ! -e app/routers/queue.py && test ! -e app/routers/proxy_pool.py && test -f app/services/queue.py && echo DELETED_OK</automated>
  </verify>
  <done>Both app/routers/queue.py and app/routers/proxy_pool.py are gone; app/services/queue.py still present.</done>
</task>

<task type="auto">
  <name>Task 2: Verify app still imports cleanly and test-overlay suite passes</name>
  <files>(no files modified — verification only)</files>
  <action>
    Prove the deletion broke nothing.

    1) Confirm the app package imports cleanly (no dangling reference to the removed
       routers) inside the test container:

         docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "import app.main; print('IMPORT_OK')"

    2) Run the full test-overlay suite (per CLAUDE.md — ALWAYS via the test overlay,
       NEVER plain `docker compose run --rm api pytest`, which would target prod DB):

         docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest

    Both must succeed (import prints IMPORT_OK; pytest exit code 0). If anything fails,
    it means something DID depend on the removed modules — investigate before committing;
    do not force the deletion through.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "import app.main; print('IMPORT_OK')"</automated>
  </verify>
  <done>`import app.main` succeeds and the full test-overlay pytest suite exits 0.</done>
</task>

</tasks>

<verification>
- `app/routers/queue.py` and `app/routers/proxy_pool.py` no longer exist.
- `app/services/queue.py` is untouched and still present.
- `import app.main` succeeds in the test container (IMPORT_OK).
- Full test-overlay suite passes (exit 0), confirming no consumer depended on the removed modules.
- No DB / migration changes; ProxyPool model and proxy_pool table untouched.
</verification>

<success_criteria>
- Two dead router files removed in a single atomic commit touching only those two paths.
- App imports cleanly and the test suite is green.
- No migration, no DB change, no touch to the ProxyPool model or proxy_pool table.
</success_criteria>

<output>
After completion, create `.planning/quick/260704-bty-batch-h-routers-queue-py-routers-proxy-p/260704-bty-SUMMARY.md`.

Commit only the two deleted files (parallel Phase-20 work in the repo — never `git add -A`):

  node ".claude/get-shit-done/bin/gsd-tools.cjs" commit "refactor(quick-260704-bty): remove dead routers/queue.py + routers/proxy_pool.py (WR-01)" --files app/routers/queue.py app/routers/proxy_pool.py
</output>
