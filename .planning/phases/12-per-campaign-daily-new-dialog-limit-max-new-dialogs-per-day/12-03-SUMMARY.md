---
phase: 12-per-campaign-daily-new-dialog-limit-max-new-dialogs-per-day
plan: 03
subsystem: api
tags: [campaigns, rate-limit, new-dialog-cap, soft-cap, hard-cap, warnings, d-14]
requires:
  - "12-01: campaigns.max_new_dialogs_per_day column + Campaign ORM field (server_default 50)"
  - "senders D-14 pattern (RATE_SOFT_CAP/RATE_HARD_CAP, _validate_rate_limits, WarningItem, SenderCreateResponse)"
provides:
  - "max_new_dialogs_per_day on CampaignCreate/CampaignUpdate/CampaignResponse (Field ge=1, le=100, default 50)"
  - "CampaignWriteResponse wrapper {campaign, warnings[]} for create + patch"
  - "DIALOG_LIMIT_SOFT_CAP=50 / DIALOG_LIMIT_HARD_CAP=100 + _validate_max_new_dialogs helper in campaigns router"
  - "soft-cap warnings[] + hard-cap 422 (NEW_DIALOG_LIMIT_EXCEEDS_HARD_CAP) on create + patch"
affects:
  - "Lovable frontend: create/patch responses are now wrapped {campaign, warnings[]} — clients must read body.campaign (NDLG-04)"
  - "future UI green-corridor warning surface for the new-dialog cap"
tech-stack:
  added: []
  patterns:
    - "module-level soft/hard cap constants + validation helper mirroring senders D-14"
    - "write-response wrapper {entity, warnings[]} while GET keeps the flat response model"
    - "explicit field-by-field _campaign_to_response mapping (no model_validate) — read path must map the new column"
key-files:
  created:
    - tests/test_campaign_new_dialog_limit_api.py
  modified:
    - app/schemas/__init__.py
    - app/routers/campaigns.py
    - tests/test_campaign_router.py
    - tests/test_phase5_1_campaign_v2_router.py
    - tests/test_campaign_draft_optional.py
    - tests/test_campaigns_model.py
    - tests/test_sender_lock.py
decisions:
  - "D-12/D-13: green corridor <=50; hard cap 100 (top of the warmed range) — bounds enforced at API layer, not DB CHECK (consistent with 12-01 D-12)"
  - "D-14: write path (create+patch) returns warnings[] via CampaignWriteResponse; GET/list and lifecycle endpoints (start/pause/resume/finish/stop/duplicate/senders) keep flat CampaignResponse and carry no warnings"
  - "explicit _campaign_to_response mapping for max_new_dialogs_per_day — the helper builds CampaignResponse field-by-field, so without the mapping every response would silently return the 50 default"
metrics:
  duration: ~12min
  tasks: 3
  files: 8
  completed: 2026-06-25
---

# Phase 12 Plan 03: max_new_dialogs_per_day API + Soft/Hard-Cap Enforcement Summary

Exposed `max_new_dialogs_per_day` on the campaign API and applied the exact senders D-14 soft/hard-cap machinery to the campaign write path: value >50 and <=100 returns 201/200 with a `warnings[]` entry, value >100 returns 422, default is 50, and GET paths carry no warnings. Create/patch now return the `CampaignWriteResponse` wrapper `{campaign, warnings[]}`; GET, list, and all lifecycle endpoints keep the flat `CampaignResponse`.

## What Was Built

### Task 1 — Schema fields + CampaignWriteResponse wrapper (`app/schemas/__init__.py`)
- `CampaignCreate.max_new_dialogs_per_day: int = Field(default=50, ge=1, le=100, ...)` (placed beside `recontact_min_age_days`).
- `CampaignUpdate.max_new_dialogs_per_day: Optional[int] = Field(default=None, ge=1, le=100)`.
- `CampaignResponse.max_new_dialogs_per_day: int = 50`.
- New `CampaignWriteResponse(BaseModel)` wrapper `{campaign: CampaignResponse, warnings: List[WarningItem] = []}`, modeled on `SenderCreateResponse`, placed before `CampaignListResponse` (which is left untouched).
- Commit: `ee9aa6c`

### Task 2 — Cap validation + warnings on create/patch (`app/routers/campaigns.py`)
- Module-level `DIALOG_LIMIT_SOFT_CAP = 50` / `DIALOG_LIMIT_HARD_CAP = 100`.
- `_validate_max_new_dialogs(value) -> List[WarningItem]`: >100 raises 422 with senders-style detail `{code: "NEW_DIALOG_LIMIT_EXCEEDS_HARD_CAP", field, value, hard_cap, message}`; >50 appends a `WarningItem(field, value, recommended_max=50)`; `None` returns `[]`.
- Imports extended: `List` (from typing), `CampaignWriteResponse`, `WarningItem`.
- `create_campaign`: `response_model=CampaignWriteResponse`; validate before building the row; persist `max_new_dialogs_per_day=payload.max_new_dialogs_per_day`; return `CampaignWriteResponse(campaign=resp, warnings=warnings)`.
- `patch_campaign`: `response_model=CampaignWriteResponse`; re-validate when the field is present in `update_data`; the existing `setattr` loop persists the plain column; return the wrapper.
- `_campaign_to_response`: added explicit `max_new_dialogs_per_day=campaign.max_new_dialogs_per_day` mapping (the helper builds `CampaignResponse` field-by-field — without this, every response silently returns the 50 default). This single helper feeds GET, create, and patch.
- GET (`get_campaign`), list (`list_campaigns`), and lifecycle endpoints (start/pause/resume/finish/stop/duplicate/senders) keep `CampaignResponse` / `CampaignListResponse` — no warnings (D-14).
- Commit: `935531e`

### Task 3 — API tests + suite adaptation (`tests/`)
- New `tests/test_campaign_new_dialog_limit_api.py` (6 tests): default-50, soft-cap-warns (70), hard-cap-422 (120) on create; soft-cap-warns (80), hard-cap-422 (150) on patch; GET echoes a NON-default stored value (70) with no `warnings`/`campaign` keys (guards the `_campaign_to_response` mapping).
- Adapted existing campaign router/model suites to the wrapper change (read `body["campaign"]` on create/patch): `test_campaign_router.py`, `test_phase5_1_campaign_v2_router.py`, `test_campaign_draft_optional.py`, `test_campaigns_model.py`, `test_sender_lock.py`.
- Commit: `038df9e`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Adapted 5 existing campaign test suites to the CampaignWriteResponse shape**
- **Found during:** Task 3
- **Issue:** Changing create/patch `response_model` to `CampaignWriteResponse` nested the campaign body under `campaign`, breaking every existing test that read `r.json()["id"]` / `["status"]` / `["timezone"]` etc. directly off the create or patch response. The plan flagged this possibility ("update any that break").
- **Fix:** Added a small `_camp()` unwrap helper (or in-helper unwrap for `_make_campaign` / `_mk`) to each affected file and pointed create/patch reads at `body["campaign"]`. GET and lifecycle reads (flat `CampaignResponse`) left untouched.
- **Files modified:** `tests/test_campaign_router.py`, `tests/test_phase5_1_campaign_v2_router.py`, `tests/test_campaign_draft_optional.py`, `tests/test_campaigns_model.py`, `tests/test_sender_lock.py`
- **Commit:** `038df9e`

## Verification

- New suite: `tests/test_campaign_new_dialog_limit_api.py` — 6 passed.
- Regression suites (run via test-overlay with the worktree volumes mounted): `test_campaign_router.py` + `test_phase5_1_campaign_v2_router.py` + `test_campaign_draft_optional.py` + `test_campaigns_model.py` + `test_sender_lock.py` — 49 passed, 0 failed.
- `python -c "import ast; ast.parse(...)"` clean for both `app/schemas/__init__.py` and `app/routers/campaigns.py`.
- All grep acceptance criteria met: 3 schema fields, 2× `ge=1, le=100`, 1 `CampaignWriteResponse`, 1 `DIALOG_LIMIT_SOFT_CAP/HARD_CAP`, 1 `_validate_max_new_dialogs`, exactly 2 `response_model=CampaignWriteResponse`, 1 read-path mapping, 6 `def test_`, 2 `== 70`.
- Tests ran ONLY via the test-overlay (`docker-compose.test.yml`) — never bare pytest. A scratchpad volume-override compose file pointed the api container's `/app/app`, `/app/tests`, `/app/migrations` at this worktree so the worktree code/tests were exercised (the overlay otherwise mounts the main checkout). The override lives outside the repo and is not committed.

## Known Stubs

None — the cap is fully wired end-to-end (schema bounds + explicit persistence + explicit read-path mapping + warnings on the write path). The queue-side enforcement of the cap is the responsibility of plan 12-02 (per 12-01 affects map), not this plan.

## Self-Check: PASSED

- FOUND: app/schemas/__init__.py
- FOUND: app/routers/campaigns.py
- FOUND: tests/test_campaign_new_dialog_limit_api.py
- FOUND commit ee9aa6c (Task 1)
- FOUND commit 935531e (Task 2)
- FOUND commit 038df9e (Task 3)
