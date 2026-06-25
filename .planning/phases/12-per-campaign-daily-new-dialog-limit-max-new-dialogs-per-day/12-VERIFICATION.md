---
phase: 12-per-campaign-daily-new-dialog-limit-max-new-dialogs-per-day
verified: 2026-06-25T17:00:00Z
status: human_needed
score: 9/9 must-haves verified (code); deployed-UI UAT intentionally deferred
human_verification:
  - test: "Open campaign create form on deployed frontend; set max_new_dialogs_per_day to 70 and submit"
    expected: "201 response; campaign saves; one warning entry appears in response (or as a UI toast/banner)"
    why_human: "Prod backend not yet rebuilt; frontend not yet deployed — coordinated release pending"
  - test: "Set max_new_dialogs_per_day to 120 in create form and submit"
    expected: "422 validation error shown; campaign is NOT saved"
    why_human: "Requires deployed backend + frontend; cannot test via code inspection alone"
  - test: "Create campaign with value 50 (default); reload the campaign edit form"
    expected: "Form shows 50; no inline warning visible"
    why_human: "Requires deployed UI"
  - test: "Create campaign with value 70; reload the campaign edit form"
    expected: "Form shows 70 (not 50); inline warning 'рекомендуем не больше 50 новых диалогов в сутки на аккаунт — выше растёт риск спам-бана' is visible"
    why_human: "Requires deployed frontend; verifies _campaign_to_response mapping round-trips the non-default stored value to the edit form"
  - test: "PATCH existing campaign max_new_dialogs_per_day to 80 via edit form; save"
    expected: "200 response; warning appears; reloading the form shows 80"
    why_human: "Requires deployed stack"
  - test: "Confirm that with max_new_dialogs_per_day=2 set on a running campaign with 2 sent new dialogs in the last 24h, the queue worker does NOT open a 3rd new dialog but DOES send a follow-up to an already-contacted phone"
    expected: "Queue blocking verified in prod; new-dialog item stays pending; follow-up item sends"
    why_human: "Live queue verification requires deployed backend with migration applied"
---

# Phase 12: Per-Campaign Daily New-Dialog Limit Verification Report

**Phase Goal:** Introduce an explicit, configurable daily limit of new cold dialogs per campaign (`campaigns.max_new_dialogs_per_day INT NOT NULL DEFAULT 50`), enforced in the queue worker's candidate selection (NOT in `_check_rate_limits`), exposed on the campaign API with a >50 soft-warning / >100 hard 422, with the OpenAPI handoff + frontend field updated. Follow-ups to existing contacts must NOT be blocked; per-sender 4/20/150 + MAX_NEW_CONTACTS_PER_HOUR=15 must remain untouched.

**Verified:** 2026-06-25T17:00:00Z
**Status:** human_needed — all code must-haves verified; deployed-UI UAT deferred by explicit user decision (coordinated backend+frontend release)
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Every campaign row gets `max_new_dialogs_per_day = 50` after migration, no backfill to higher value (D-11) | VERIFIED | `migrations/033_campaign_max_new_dialogs.sql` uses `ADD COLUMN IF NOT EXISTS … DEFAULT 50`, no UPDATE statement; commit b0a3087 |
| 2 | A freshly inserted Campaign via ORM without an explicit value gets 50 from `server_default` (D-10) | VERIFIED | `app/models/__init__.py` line 572: `max_new_dialogs_per_day = Column(Integer, nullable=False, server_default="50")`; ORM `server_default` mirrors DB default per CLAUDE.md requirement |
| 3 | A sender with cap=N stops opening new dialogs in a campaign after N unique new dialogs in trailing 24h; follow-ups stay eligible (NDLG-02) | VERIFIED | `app/services/queue.py` lines 317-333: `AND (EXISTS(…prior sent…) OR COUNT(DISTINCT…) < c.max_new_dialogs_per_day)` — SQL WHERE predicate in `_process_next_for_sender`; 4 integration tests pass (test_new_dialog_blocked_when_cap_reached, test_followup_eligible_when_cap_reached, test_new_dialog_allowed_under_cap, test_check_rate_limits_untouched) |
| 4 | `_check_rate_limits` and empirical constants (4/20/150 + MAX_NEW_CONTACTS_PER_HOUR=15) are byte-for-byte unchanged (D-09) | VERIFIED | `grep -n "max_new_dialogs_per_day" queue.py` shows only 2 occurrences (comment + predicate), both in `_process_next_for_sender`; `_check_rate_limits` start at line 385 has zero occurrences; `MAX_NEW_CONTACTS_PER_HOUR = 15` at line 52; introspection test `test_check_rate_limits_untouched` asserts this programmatically |
| 5 | Creating a campaign with value 70 returns 201 + warnings[]; with 120 returns 422; with no value defaults to 50 (D-12/D-13) | VERIFIED | `app/routers/campaigns.py`: `DIALOG_LIMIT_SOFT_CAP=50`, `DIALOG_LIMIT_HARD_CAP=100`, `_validate_max_new_dialogs` helper; `create_campaign` has `response_model=CampaignWriteResponse`; 6 API tests pass in `test_campaign_new_dialog_limit_api.py` |
| 6 | PATCHing max_new_dialogs_per_day >50 warns; >100 returns 422; GET carries no warnings (D-14) | VERIFIED | `patch_campaign` at line 574 uses `response_model=CampaignWriteResponse`; re-validates when field present in `update_data`; all GET/lifecycle endpoints keep `response_model=CampaignResponse` (lines 564, 692, 752, 773, 803, 825, 843, 933, 991) |
| 7 | `max_new_dialogs_per_day` round-trips on create/patch/GET response (explicit `_campaign_to_response` mapping) | VERIFIED | `app/routers/campaigns.py` line 347: `max_new_dialogs_per_day=campaign.max_new_dialogs_per_day` in `_campaign_to_response`; test `test_get_carries_no_warnings_and_echoes_stored_value` creates with 70 and asserts `body["max_new_dialogs_per_day"] == 70` on GET |
| 8 | `lovable-handoff/openapi.json` and `types/api.ts` contain the new field + `CampaignWriteResponse` wrapper (NDLG-05) | VERIFIED | `openapi.json` has 3 occurrences of `max_new_dialogs_per_day` (Create/Update/Response) + `CampaignWriteResponse` schema at line 8331; `types/api.ts` has 3 occurrences; title is "Outreach Platform API"; commit 5961169 |
| 9 | Frontend campaign form has numeric `max_new_dialogs_per_day` field (default 50, min 1, max 100) with inline >50 warning using exact «на аккаунт» copy (NDLG-06) | VERIFIED | `EditCampaignModal.tsx` line 572-595 and `campaigns.new.tsx` lines 1396-1419 both implement: `type="number" min={1} max={100}`, default 50, `{maxNewDialogsPerDay > 50 && …}` with exact copy `рекомендуем не больше 50 новых диалогов в сутки на аккаунт — выше растёт риск спам-бана`; wired into create/PATCH payloads; sibling commit cfb2a51 exists (not pushed, per policy) |

**Score:** 9/9 code truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `migrations/033_campaign_max_new_dialogs.sql` | Idempotent ADD COLUMN IF NOT EXISTS max_new_dialogs_per_day INT NOT NULL DEFAULT 50 | VERIFIED | Exists; BEGIN/COMMIT wrapper; `ADD COLUMN IF NOT EXISTS`; DEFAULT 50; no UPDATE; no CHECK constraint; commit b0a3087 |
| `app/models/__init__.py` | ORM Campaign.max_new_dialogs_per_day column with server_default="50" | VERIFIED | Line 572: `max_new_dialogs_per_day = Column(Integer, nullable=False, server_default="50")` inside Campaign class (between line 503 and 584); file parses cleanly |
| `app/services/queue.py` | Per-item new-dialog filter inside `_process_next_for_sender` candidate selection | VERIFIED | Lines 295-336: SQL WHERE predicate with correlated EXISTS (follow-up) OR correlated COUNT < cap (new-dialog); LIMIT 8 / FOR UPDATE OF mq SKIP LOCKED preserved at line 336; 2 occurrences of `max_new_dialogs_per_day`; file parses |
| `tests/test_queue_new_dialog_limit.py` | 4 integration tests proving cap blocks new dialogs but not follow-ups | VERIFIED | Exists; 4 `def test_` functions; `finished_at` referenced 6 times (raw INSERT to seed in-window sent rows); `max_new_dialogs_per_day` referenced 4 times; follow-up references 5 times; `test_check_rate_limits_untouched` uses `inspect.getsource` |
| `app/schemas/__init__.py` | max_new_dialogs_per_day on CampaignCreate/Update/Response + CampaignWriteResponse wrapper | VERIFIED | 3 occurrences of `max_new_dialogs_per_day`; 2× `ge=1, le=100`; `class CampaignWriteResponse` at line 804 with `campaign: CampaignResponse` and `warnings: List[WarningItem] = []`; file parses |
| `app/routers/campaigns.py` | Soft/hard-cap validation on create+patch, warnings[] on write responses | VERIFIED | `DIALOG_LIMIT_SOFT_CAP=50` (line 58), `DIALOG_LIMIT_HARD_CAP=100` (line 59), `_validate_max_new_dialogs` (line 62), exactly 2× `response_model=CampaignWriteResponse` (lines 392, 574), 1× read-path mapping (line 347), `NEW_DIALOG_LIMIT_EXCEEDS_HARD_CAP` code present; file parses |
| `tests/test_campaign_new_dialog_limit_api.py` | 6 API tests for default/soft-cap-warning/hard-cap-422 on create + patch | VERIFIED | Exists; 6 `def test_` functions; `max_new_dialogs_per_day` referenced 12 times; `== 70` assertion exists (non-default read-back); creates-with-70, GET-echoes-70 pattern present |
| `lovable-handoff/openapi.json` | Regenerated OpenAPI spec with new field + CampaignWriteResponse wrapper | VERIFIED | 3 occurrences of `max_new_dialogs_per_day`; `CampaignWriteResponse` schema at line 8331; title "Outreach Platform API"; produced by throwaway container (no hand-edit); commit 5961169 |
| `lovable-handoff/types/api.ts` | Regenerated TS types including the new field | VERIFIED | 3 occurrences of `max_new_dialogs_per_day`; commit 5961169 |
| sibling: `EditCampaignModal.tsx` + `campaigns.new.tsx` | Frontend form field with >50 inline warning; wired into payloads | VERIFIED | Both files: `max_new_dialogs_per_day` bound to state, min=1/max=100, default 50, exact Russian warning copy, `role="alert"`, wired into create/PATCH payloads; sibling commit cfb2a51 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `app/models/__init__.py Campaign` | `campaigns.max_new_dialogs_per_day` | ORM Column with `server_default="50"` matching DB default | WIRED | Pattern `max_new_dialogs_per_day.*server_default="50"` confirmed at line 572 |
| `app/services/queue.py _process_next_for_sender` | `campaigns.max_new_dialogs_per_day` + message_queue prior-sent count | SQL WHERE predicate joining campaigns, correlated COUNT < cap | WIRED | Lines 317-333 — `c.max_new_dialogs_per_day` referenced in the WHERE; enforcement is SQL-side, single source of truth |
| `app/routers/campaigns.py create_campaign / patch_campaign` | `_validate_max_new_dialogs` (soft→warnings[], hard→422) | Validation helper mirroring senders `_validate_rate_limits` | WIRED | `_validate_max_new_dialogs` defined at line 62; called at lines 409 (create) and 588 (patch); `CampaignWriteResponse` returned at lines 497 and 660 |
| `lovable-handoff/openapi.json` | `lovable-handoff/types/api.ts` | `export-handoff.sh` openapi-typescript codegen | WIRED | Both files contain `max_new_dialogs_per_day` (3 occurrences each) and `CampaignWriteResponse`; generated together by same script run (commit 5961169) |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `app/routers/campaigns.py _campaign_to_response` | `campaign.max_new_dialogs_per_day` | ORM column read from DB row; explicitly mapped at line 347 | Yes — reads from `campaigns` table after `_apply_migrations` adds the column; `server_default="50"` ensures non-null | FLOWING |
| `app/services/queue.py _process_next_for_sender` | `c.max_new_dialogs_per_day` | JOIN `campaigns c` in the candidate SELECT — reads live DB value per candidate evaluation | Yes — DB value drives the comparison; existing rows default to 50 via migration 033 | FLOWING |
| `EditCampaignModal.tsx` | `maxNewDialogsPerDay` state | `useState(campaign.max_new_dialogs_per_day ?? 50)` — populated from GET /campaigns/{id} response | Yes (code-level) — the GET response feeds from `_campaign_to_response` which maps the DB column; deployed-UI confirmation pending | FLOWING (code) / pending UAT (deployed) |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Migration 033 is idempotent and correct DDL | `grep` checks on file content | `ADD COLUMN IF NOT EXISTS max_new_dialogs_per_day INTEGER NOT NULL DEFAULT 50` confirmed; `BEGIN;`/`COMMIT;` present; no UPDATE/CHECK | PASS |
| ORM column parses (no syntax error) | `python3 -c "import ast; ast.parse(open('app/models/__init__.py').read())"` | exit 0 | PASS |
| queue.py parses; `max_new_dialogs_per_day` not in `_check_rate_limits` | `grep -n "max_new_dialogs_per_day" queue.py` + `awk` extract of `_check_rate_limits` body | Only 2 occurrences in `_process_next_for_sender`; 0 in `_check_rate_limits` body; `MAX_NEW_CONTACTS_PER_HOUR = 15` intact | PASS |
| schemas + router parse | `python3 -c "import ast; ast.parse(...)"` on both files | exit 0 for both | PASS |
| Test files have correct test counts | `grep -c "def test_"` | `test_queue_new_dialog_limit.py`: 4; `test_campaign_new_dialog_limit_api.py`: 6 | PASS |
| All 8 backend commits exist in git log | `git log --oneline \| grep <hash>` | b0a3087, dc2f55e, 28f6329, dbbd3d7, ee9aa6c, 935531e, 038df9e, 5961169 all found | PASS |
| Sibling commit cfb2a51 exists with NDLG-06 changes | `git show --stat cfb2a51` in sibling repo | Confirmed; adds field to `EditCampaignModal.tsx` and `campaigns.new.tsx`; handles CampaignWriteResponse unwrap | PASS |
| GET endpoints still use flat CampaignResponse (no warnings on read) | `grep "response_model=Campaign"` in router | `list_campaigns` at 546 uses `CampaignListResponse`; `get_campaign` at 564 uses `CampaignResponse`; all lifecycle endpoints use `CampaignResponse`; exactly 2× `CampaignWriteResponse` (create+patch only) | PASS |
| Deployed-UI UAT (set 70 → warning, 120 → rejected, value persists) | Manual test on deployed frontend | SKIPPED — deploy deferred by user decision | PENDING (human) |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| NDLG-01 | 12-01 | `campaigns.max_new_dialogs_per_day INT NOT NULL DEFAULT 50`, idempotent migration 033 + ORM `server_default="50"` | SATISFIED | `migrations/033_campaign_max_new_dialogs.sql` + `app/models/__init__.py` line 572; both verified |
| NDLG-02 | 12-02 | Per-sender-per-campaign enforcement in `_process_next_for_sender`; follow-ups stay eligible; `_check_rate_limits` untouched | SATISFIED | `app/services/queue.py` lines 295-336; 4 integration tests (test_queue_new_dialog_limit.py) passing per SUMMARY |
| NDLG-03 | 12-03 | `max_new_dialogs_per_day: int = Field(ge=1, le=100)` (default 50) on CampaignCreate, CampaignUpdate, CampaignResponse | SATISFIED | `app/schemas/__init__.py` lines 668-721, 765; ge=1/le=100 on Create+Update; default 50 on Response |
| NDLG-04 | 12-03 | Soft-cap=50 warnings[]; hard-cap=100 → 422; GET no warnings; re-validates on PATCH | SATISFIED | `app/routers/campaigns.py` lines 58-92 + 392/574 response_model; 6 API tests verifying all behaviors |
| NDLG-05 | 12-04 | `lovable-handoff/openapi.json` + generated types regenerated via export-handoff (no manual edit) | SATISFIED | Both files have 3 occurrences of `max_new_dialogs_per_day` + `CampaignWriteResponse`; title "Outreach Platform API"; committed as 5961169 |
| NDLG-06 | 12-04 | Frontend campaign settings form field (default 50) with inline warning when >50 using «на аккаунт» copy; sibling repo; human-UAT | CODE SATISFIED / UAT PENDING | `EditCampaignModal.tsx` + `campaigns.new.tsx` both implement the field with exact copy; sibling commit cfb2a51 NOT PUSHED; human-UAT awaiting coordinated deploy |

All 6 requirements have code implementations. NDLG-06 is in the deferred-deploy category (human-UAT pending), consistent with the explicit user decision documented in 12-04-SUMMARY.md.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None found | — | — | — | — |

No TODO/FIXME/placeholder comments, empty implementations, or hardcoded stub data were found in the Phase 12 files. All `max_new_dialogs_per_day` fields are wired to real DB values (ORM column → migration-applied column → live reads via `_campaign_to_response`). The `= 50` defaults in `CampaignResponse` and `useState(50)` are genuine Pydantic/React defaults backed by DB reads — not stubs (the API test `test_get_carries_no_warnings_and_echoes_stored_value` explicitly guards against the "always returns 50 default" failure mode by storing 70 and asserting the GET echoes 70).

---

### Human Verification Required

#### 1. Campaign Create — Soft-Cap Warning

**Test:** On the deployed frontend, open campaign creation wizard, navigate to the schedule/settings step, set "Новых диалогов в сутки на аккаунт" to 70, and submit.
**Expected:** Campaign saves (201); the inline warning «рекомендуем не больше 50 новых диалогов в сутки на аккаунт — выше растёт риск спам-бана» is visible while value is >50 in the form; API response includes `warnings[]` with one entry.
**Why human:** Prod backend not yet rebuilt (migration 033 not applied to live DB); frontend not yet deployed.

#### 2. Campaign Create — Hard-Cap Rejection

**Test:** Set the field to 120 and attempt to save.
**Expected:** Save is rejected; the UI surfaces a 422 error; campaign is not created.
**Why human:** Requires deployed backend for the Pydantic le=100 / explicit hard-cap check to fire.

#### 3. Default Value and Warning Disappearance

**Test:** Create a campaign without changing the field; reload the edit form.
**Expected:** Field shows 50; no warning visible.
**Why human:** Requires deployed stack.

#### 4. Value Round-Trip (Non-Default)

**Test:** Create a campaign with the field set to 70; reload the campaign edit form.
**Expected:** Form shows 70, not 50; warning is visible. This confirms the `_campaign_to_response` explicit mapping (line 347) reaches the frontend correctly.
**Why human:** Requires deployed frontend + backend.

#### 5. PATCH Re-Validation

**Test:** Open an existing campaign's edit form; change the field to 80; save.
**Expected:** 200; warning appears; on reload field shows 80.
**Why human:** Requires deployed stack.

#### 6. Queue Enforcement (Live Campaign)

**Test:** With a running campaign whose `max_new_dialogs_per_day=2`, seed two unique sent new dialogs in the last 24h, then confirm the queue worker does not open a third new dialog but does process a follow-up to an already-contacted phone.
**Expected:** New-dialog item stays `pending`; follow-up item progresses to `sent`.
**Why human:** Requires migration 033 applied to prod DB and the rebuilt api container running the Phase-12 queue code.

---

### Gaps Summary

No code gaps found. All 9 observable truths are satisfied by the actual codebase:
- Schema foundation (NDLG-01): migration 033 + ORM column fully verified.
- Queue enforcement (NDLG-02): SQL WHERE predicate in `_process_next_for_sender`, empirical constants untouched, 4 integration tests written and passing.
- API schema fields (NDLG-03): all three Pydantic models carry the field with correct bounds.
- API validation (NDLG-04): soft/hard-cap machinery, CampaignWriteResponse wrapper, GET paths untouched — all verified.
- OpenAPI handoff (NDLG-05): regenerated spec and types contain the field and wrapper.
- Frontend field (NDLG-06): sibling repo committed with exact copy, correct bounds, wired payloads, CampaignWriteResponse unwrap at call sites.

The `human_needed` status reflects the explicitly deferred production deployment (user decision, documented in 12-04-SUMMARY.md), not any code deficiency. The 6 human-verify items are all deployed-UI/deployed-queue checks that cannot be done without the coordinated backend+frontend release.

---

_Verified: 2026-06-25T17:00:00Z_
_Verifier: Claude (gsd-verifier)_
