---
phase: quick-260706-mdz
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/050_lower_new_dialog_cap.sql
  - app/models/__init__.py
  - app/schemas/__init__.py
  - app/routers/campaigns.py
  - tests/test_campaign_new_dialog_limit_api.py
  - lovable-handoff/openapi.json
  - lovable-handoff/types/api.ts
autonomous: true
requirements: [QUICK-MDZ-NDLG]
must_haves:
  truths:
    - "New campaigns created without an explicit value default to max_new_dialogs_per_day = 10 (was 50)"
    - "All existing prod campaigns (6 rows) that were at the old default 50 are now 10; manually-set values are preserved"
    - "Creating or patching a campaign with a value >30 returns 422; a value 11–30 returns a warning recommending 10; <=10 is clean"
    - "Queue worker (services/queue.py) is unchanged and adapts to the new cap automatically"
  artifacts:
    - path: "migrations/050_lower_new_dialog_cap.sql"
      provides: "Idempotent DB default change (SET DEFAULT 10) + guarded UPDATE of old-default rows"
      contains: "ALTER COLUMN max_new_dialogs_per_day SET DEFAULT 10"
    - path: "app/models/__init__.py"
      provides: "ORM server_default kept in sync with migration (10)"
      contains: "server_default=\"10\""
    - path: "app/schemas/__init__.py"
      provides: "Pydantic Field bounds/defaults for the new corridor (default 10, le=30)"
    - path: "app/routers/campaigns.py"
      provides: "Green-corridor constants DIALOG_LIMIT_SOFT_CAP=10, DIALOG_LIMIT_HARD_CAP=30"
  key_links:
    - from: "app/schemas/__init__.py Field(le=30, default=10)"
      to: "app/routers/campaigns.py DIALOG_LIMIT_SOFT_CAP/HARD_CAP"
      via: "must agree — soft=10, hard=30"
      pattern: "DIALOG_LIMIT_(SOFT|HARD)_CAP"
    - from: "migrations/050_lower_new_dialog_cap.sql"
      to: "prod campaigns table"
      via: "auto-applied on `docker compose up -d --build api` (app/database.py::_apply_migrations)"
      pattern: "SET DEFAULT 10"
---

<objective>
Lower the per-account daily new-dialog cap from 50 to 10 across the whole stack: the DB
default for new campaigns, the value stored on all existing campaigns, and the green-corridor
guard rails (soft warn 50→10, hard reject 100→30).

Purpose: 50 new cold dialogs/account/day is too aggressive for Telegram anti-spam; 10 is the
new safe ceiling. This is a pure parameter/guardrail change — no worker logic changes.

Output: migration 050 (DB default + existing-row update), synced ORM server_default, tightened
Pydantic bounds + router corridor constants, updated tests, regenerated Lovable handoff, and a
deployed-and-verified prod state where every campaign reads 10.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@/root/CLAUDE.md
@/root/apps/aimly/tg-outreach/CLAUDE.md

# Locked user decisions (from interactive discussion — DO NOT revisit):
# D-1: Lower max_new_dialogs_per_day for ALL existing campaigns (all 6 prod rows,
#      including the running one) from 50 → 10, AND change the default for new campaigns to 10.
# D-2: Green corridor — soft cap (warning) 50 → 10; hard cap (422 reject) 100 → 30. Update
#      DIALOG_LIMIT_SOFT_CAP/DIALOG_LIMIT_HARD_CAP + Pydantic Field bounds (le=100 → le=30) +
#      defaults (50 → 10) + stale descriptions.
# D-3: DO NOT touch app/services/queue.py — the per-(sender,campaign) cap + Phase-13 even
#      pacing read the value at query time and adapt automatically. Protected 4/20/150 sender
#      rate limits and 20–55s intervals stay untouched.

<interfaces>
<!-- Exact current state extracted from the codebase — change these, do not explore further. -->

migrations/ — highest existing is 049_account_profile.sql. NEXT FREE NUMBER IS 050
  (the planning note's "046" was stale). Use 050_lower_new_dialog_cap.sql.

app/routers/campaigns.py:59-61
```python
# Phase 12 D-13/D-14: green corridor <=50; hard cap 100 (top of the "warmed" range).
DIALOG_LIMIT_SOFT_CAP = 50
DIALOG_LIMIT_HARD_CAP = 100
```
Also the _validate_max_new_dialogs docstring (lines 64-70) says "hard cap 100 → 422; soft cap
50 → warnings[]" and "Pydantic already clips the hard cap via Field(le=100)" — both stale.

app/schemas/__init__.py
  - CampaignCreate ~793-797: `max_new_dialogs_per_day: int = Field(default=50, ge=1, le=100,
    description="... Green corridor <=50; soft-warn >50; hard cap 100.")`
  - CampaignUpdate ~868: `max_new_dialogs_per_day: Optional[int] = Field(default=None, ge=1, le=100)`
  - CampaignResponse ~930: `max_new_dialogs_per_day: int = 50`

app/models/__init__.py:677-682 (Campaign model)
```python
# ... comment block references "DEFAULT 50", "migration-033 DB default", "ge=1/le=100"
max_new_dialogs_per_day = Column(Integer, nullable=False, server_default="50")
```

migrations/033_campaign_max_new_dialogs.sql — original column, DEFAULT 50 (do NOT edit; 050 supersedes).

Prod DB (for verification): container `outreach-platform-db`, user `outreach_user`, db `outreach_platform`.
Migrations auto-apply on api start via app/database.py::_apply_migrations (idempotent, advisory-locked, fail-fast).
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Migration 050 + sync ORM/Pydantic/router to the new corridor (default 10, soft 10, hard 30)</name>
  <files>migrations/050_lower_new_dialog_cap.sql, app/models/__init__.py, app/schemas/__init__.py, app/routers/campaigns.py</files>
  <action>
Implements D-1 and D-2. Five edits, all keeping the value in sync so there is no ORM-vs-migration
drift (see memory "ORM default= vs server_default= drift"):

1. CREATE `migrations/050_lower_new_dialog_cap.sql` (idempotent, matches the house migration style —
   see migrations/033 header for the pattern). Content:
   ```sql
   -- migrations/050_lower_new_dialog_cap.sql
   -- Quick 260706-mdz: lower per-account daily new-dialog cap 50 → 10.
   -- D-1: change DB default for new campaigns AND update every existing row still at the
   --      old default 50 (all 6 prod rows). Manually-set non-50 values are preserved.
   -- Idempotent: SET DEFAULT re-applies safely; UPDATE guarded WHERE = 50 (0 rows on re-run).
   -- Auto-applied via app/database.py::_apply_migrations (lexical order, advisory lock, fail-fast).
   BEGIN;
   ALTER TABLE campaigns ALTER COLUMN max_new_dialogs_per_day SET DEFAULT 10;
   UPDATE campaigns SET max_new_dialogs_per_day = 10 WHERE max_new_dialogs_per_day = 50;
   COMMIT;
   ```

2. app/models/__init__.py — Campaign model (~682): `server_default="50"` → `server_default="10"`.
   Update the preceding comment block (~677-681) so it reads correctly: DEFAULT 10 now, mention
   migration-050 supersedes migration-033's default, keep the "API enforces bounds; no DB CHECK"
   note but change "ge=1/le=100" → "ge=1/le=30".

3. app/schemas/__init__.py — CampaignCreate (~793): `default=50, ge=1, le=100` → `default=10, ge=1,
   le=30`; rewrite the description to "Daily new-dialog cap per sender within this campaign (D-12).
   Green corridor <=10; soft-warn >10; hard cap 30."

4. app/schemas/__init__.py — CampaignUpdate (~868): `Field(default=None, ge=1, le=100)` → `le=30`.
   CampaignResponse (~930): `max_new_dialogs_per_day: int = 50` → `= 10`.

5. app/routers/campaigns.py — `DIALOG_LIMIT_SOFT_CAP = 50` → `10`; `DIALOG_LIMIT_HARD_CAP = 100`
   → `30` (lines 60-61). Update the comment on line 59 and the `_validate_max_new_dialogs`
   docstring (lines 64-70): "hard cap 30 → 422; soft cap 10 → warnings[]" and "Field(le=30)".

DO NOT touch app/services/queue.py (D-3). Do NOT edit migration 033.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/tg-outreach && test -f migrations/050_lower_new_dialog_cap.sql && grep -q 'SET DEFAULT 10' migrations/050_lower_new_dialog_cap.sql && grep -q 'server_default="10"' app/models/__init__.py && grep -q 'DIALOG_LIMIT_SOFT_CAP = 10' app/routers/campaigns.py && grep -q 'DIALOG_LIMIT_HARD_CAP = 30' app/routers/campaigns.py && grep -q 'default=10, ge=1, le=30' app/schemas/__init__.py && ! grep -q 'le=100' app/schemas/__init__.py && echo OK</automated>
  </verify>
  <done>Migration 050 exists with SET DEFAULT 10 + guarded UPDATE; ORM server_default="10"; router constants 10/30; Pydantic default 10 / le=30 in all three campaign schemas; no `le=100` remains for this field; queue.py untouched.</done>
</task>

<task type="auto">
  <name>Task 2: Update the API test to the new corridor and run the suite green (test-overlay only)</name>
  <files>tests/test_campaign_new_dialog_limit_api.py</files>
  <action>
Update tests/test_campaign_new_dialog_limit_api.py so its assertions match the new corridor
(default 10, soft 10, hard 30). The queue tests (test_queue_new_dialog_limit.py,
test_queue_even_pacing.py) set the cap via raw UPDATE bypassing Pydantic and do NOT assert the
default or corridor — leave them untouched.

Edits to test_campaign_new_dialog_limit_api.py:
- Module docstring (lines 1-15): rewrite the bullet contract to default 10 / soft 10 / hard 30.
- `test_create_default_is_50` → rename to `test_create_default_is_10`; assert
  `body["campaign"]["max_new_dialogs_per_day"] == 10`.
- `test_create_soft_cap_warns`: change the posted value 70 → 20 (now >soft 10, <=hard 30); assert
  stored value 20, `len(warnings)==1`, `w["recommended_max"] == 10`. Update its docstring.
- `test_create_hard_cap_422`: change posted value 120 → 40 (now >hard 30); still expect 422.
  Update docstring ">hard 100" → ">hard 30".
- `test_patch_soft_cap_warns`: change patch value 80 → 20; assert stored 20 + one warning.
- `test_patch_hard_cap_422`: change patch value 150 → 40; expect 422.
- `test_get_carries_no_warnings_and_echoes_stored_value`: change created value 70 → 20 (non-default,
  within hard cap) so the round-trip still proves the mapping; assert `body["max_new_dialogs_per_day"] == 20`.

Then run the suite. Tests run ONLY via the test-overlay (CLAUDE.md guard — never plain
`docker compose run --rm api pytest`, it targets prod DATABASE_URL):
  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_new_dialog_limit_api.py tests/test_queue_new_dialog_limit.py tests/test_queue_even_pacing.py -q
Confirm all pass. If a queue test unexpectedly fails, it is because it relied on the old default —
fix only the specific assertion, do not weaken the queue logic.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/tg-outreach && docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_new_dialog_limit_api.py tests/test_queue_new_dialog_limit.py tests/test_queue_even_pacing.py -q 2>&1 | tail -15</automated>
  </verify>
  <done>test_campaign_new_dialog_limit_api.py asserts default 10 / soft-warn recommending 10 / 422 above 30; the three targeted test files pass green via the test-overlay; queue logic assertions unchanged.</done>
</task>

<task type="auto">
  <name>Task 3: Deploy to prod (migration auto-applies), verify DB state, regenerate Lovable handoff</name>
  <files>lovable-handoff/openapi.json, lovable-handoff/types/api.ts</files>
  <action>
Deploy and verify. Per D-3 the listener shares no changed runtime logic (only the campaigns
router/schemas + ORM server_default, which matters at api create_all time) — rebuild api only.

1. Rebuild api — this auto-applies migration 050 on startup:
     cd /root/apps/aimly/tg-outreach && docker compose up -d --build api
   Then confirm the api came up healthy and did not fail-fast on the migration:
     docker compose logs api --tail=40   (look for the applier logging 050 + no traceback)

2. Verify migration 050 is recorded:
     docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c \
       "SELECT filename FROM schema_migrations WHERE filename LIKE '050%';"

3. Verify every campaign row is now 10 and none remain at 50:
     docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c \
       "SELECT max_new_dialogs_per_day AS v, COUNT(*) FROM campaigns GROUP BY 1 ORDER BY 1;"
   Expected: all rows at 10 (D-1). Note: if any row was manually set to a non-50 value it is
   preserved by design — report it rather than force it to 10.

4. Verify the column default is 10:
     docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c \
       "SELECT column_default FROM information_schema.columns
        WHERE table_name='campaigns' AND column_name='max_new_dialogs_per_day';"
   Expected: 10.

5. Regenerate the Lovable handoff so openapi.json + types/api.ts pick up the new bounds/description
   (the old file still says "maximum 100 ... hard cap 100"). The api is already rebuilt with the new
   code, so run the deterministic exporter (it scrapes the running api — no hand-editing):
     bash scripts/export-handoff.sh
   Confirm lovable-handoff/openapi.json now shows the campaign field with `"maximum": 30`,
   `"default": 10`, and the "Green corridor <=10 ... hard cap 30" description.
   If the environment cannot run npx/jq/rsync and the exporter fails, STOP and report it as a
   deferred handoff-regeneration item (backend is already correct) — do NOT hand-edit openapi.json.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/tg-outreach && docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -tA -c "SELECT column_default FROM information_schema.columns WHERE table_name='campaigns' AND column_name='max_new_dialogs_per_day';" | grep -q '^10$' && docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -tA -c "SELECT COUNT(*) FROM campaigns WHERE max_new_dialogs_per_day = 50;" | grep -q '^0$' && echo OK</automated>
  </verify>
  <done>api rebuilt and healthy; schema_migrations contains 050; column default is 10; zero campaign rows remain at 50; lovable-handoff/openapi.json regenerated to maximum 30 / default 10 (or handoff regeneration explicitly reported deferred if tooling unavailable).</done>
</task>

</tasks>

<verification>
- migrations/050_lower_new_dialog_cap.sql exists, idempotent (SET DEFAULT + guarded UPDATE), applied on prod.
- No `le=100` remains for max_new_dialogs_per_day in app/schemas/__init__.py; router constants are 10/30.
- ORM server_default="10" matches the migration (no drift).
- Targeted test files pass via the test-overlay; queue logic (services/queue.py) untouched.
- Prod: every campaign row = 10, column default = 10.
- Lovable handoff regenerated (or deferral reported).
</verification>

<success_criteria>
- New campaigns default to 10 new dialogs/account/day; all existing prod campaigns are 10.
- Green corridor: values 11–30 warn (recommend 10), >30 reject 422, <=10 clean.
- Sender rate limits (4/20/150), 20–55s intervals, and queue.py are unchanged.
</success_criteria>

<output>
After completion, create `.planning/quick/260706-mdz-lower-per-account-daily-new-dialog-limit/260706-mdz-SUMMARY.md`
</output>
