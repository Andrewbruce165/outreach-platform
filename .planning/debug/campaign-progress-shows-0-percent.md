---
slug: campaign-progress-shows-0-percent
status: resolved
trigger: "BUG 1 — Campaign progress shows 0% even when messages have been sent"
created: "2026-06-25"
updated: "2026-06-25"
---

# Debug Session: Campaign Progress Shows 0%

## Symptoms

**Expected behavior:**
Progress shows percentage of sent messages vs contacts found in Telegram (is_registered=true / tg_status='registered') — those are the reachable contacts and should form the denominator.

**Actual behavior:**
Progress shows 0% even when messages have been sent. The metric does not reflect real outreach activity.

**Additional context from user:**
- Need to verify how campaign-level analytics work end-to-end
- Need to verify how dashboard-level analytics work
- The denominator should be "contacts in campaign's folder that are found in Telegram"

**Error messages:** None reported (data display issue, not a crash)

**Timeline:** Unknown — may have been wrong since launch

**Reproduction:**
1. Open a campaign that has sent messages
2. Check progress indicator on campaign detail or list page
3. Observe it shows 0% despite sent messages

## Current Focus

hypothesis: "Frontend computes progress as finishes/sent. When a campaign sends messages but no conversation reaches status='finished', progress = 0/sent = 0%. The intended metric is contacts_messaged / registered_contacts_in_folder — a denominator that does not exist in the API at all."
test: "Confirmed against live DB: campaigns with sent>0 and finishes=0 render 0%"
expecting: "Fix = add registered-contacts denominator + distinct-contacts-messaged numerator to the campaign analytics API, change frontend formula"
next_action: "implement fix: extend campaign analytics endpoint with denominator/numerator, update frontend progress formula"

## Evidence

- timestamp: 2026-06-25
  checked: app/routers/analytics.py (campaign analytics endpoint /api/v1/analytics/campaigns/{id})
  found: Endpoint returns AnalyticsCards {sent, replied, leads, finishes}. NO progress field, NO denominator (registered contacts) concept anywhere in the backend. _compute_cards is scope-generic.
  implication: Progress % is computed entirely client-side; the denominator the user wants (registered contacts in folder) is not exposed by any endpoint.

- timestamp: 2026-06-25
  checked: aimly-tg-outreach/src/routes/_authenticated/campaigns.index.tsx:589
  found: "const progress = sent > 0 ? Math.min(1, finishes / sent) : 0;" — numerator=finishes (status='finished' conversations), denominator=sent (raw outbound message count).
  implication: ROOT CAUSE. Formula is finishes/sent, not the expected sent/registered. Any campaign that has sent messages but has zero finished conversations shows 0%.

- timestamp: 2026-06-25
  checked: live DB outreach_platform — sent vs finishes per campaign
  found: "Паша аналитика" sent=15 finishes=0 → 0%. "test-agent-camp" sent=6 finishes=0 → 0%. "Barter" sent=48 finishes=2 → 4%. Matches reported symptom exactly.
  implication: Reproduced. finishes=0 with sent>0 → progress 0%.

- timestamp: 2026-06-25
  checked: live DB — registered contacts in folder vs distinct contacts messaged
  found: Barter registered=48, distinct_contacts_messaged=42, raw_outbound=48 → correct progress 42/48=87.5%. "Паша" registered=2 distinct=2 raw=15 → using raw_outbound (15) overshoots denominator; distinct (2) is correct → 100%. One test campaign had distinct(3) > registered(2) because folder re-check shrank registered set → needs Math.min(1,...) clamp.
  implication: Correct numerator = COUNT(DISTINCT conversation_id) of outbound, NOT raw message count. Correct denominator = COUNT(contacts WHERE folder_id=campaign.folder_id AND tg_status='registered'). Clamp at 100% retained.

- timestamp: 2026-06-25
  checked: app/routers/campaigns.py:168-194 _compute_is_exhausted
  found: Backend already uses "contacts WHERE folder_id=:fid AND tg_status='registered'" as the canonical reachable-contacts set (migration 013 CHECK constraint).
  implication: Denominator definition is consistent with existing backend semantics — safe to reuse.

## Eliminated Hypotheses

- hypothesis: "API returns wrong sent count"
  evidence: sent count is correct (matches outbound messages in DB); the bug is the formula consuming it, not the value.
  timestamp: 2026-06-25

## Resolution

root_cause: "Frontend progress formula is finishes/sent (campaigns.index.tsx:589). It measures 'fraction of sent that finished', not 'fraction of reachable contacts that were messaged'. When finishes=0 (the normal early/mid state of any outreach), progress is always 0% regardless of how many messages were sent. The intended metric — distinct contacts messaged / registered contacts in the campaign's folder — had no backend support (the registered-contacts denominator was not exposed by any endpoint)."

fix: |
  Added two campaign-scoped fields to the campaign analytics endpoint and rewrote
  the frontend progress formula to use them.

  Backend (AnalyticsCards is shared across all 4 scopes per D-16, so fields are
  optional with default 0 and only populated for campaign scope):
  - app/schemas/__init__.py — added contacts_messaged + registered_contacts (default 0).
  - app/routers/analytics.py::_compute_cards — when scope[0]=='campaign_id', compute
    contacts_messaged = COUNT(DISTINCT m.conversation_id) over outbound messages
    (distinct contacts, NOT raw message count — raw count overshoots the denominator),
    and registered_contacts = COUNT(contacts WHERE folder_id=campaign.folder_id AND
    tg_status='registered' AND (phone OR username)) — same reachable-set semantics as
    _compute_is_exhausted (migration 013).

  Frontend (aimly-tg-outreach/src/routes/_authenticated/campaigns.index.tsx):
  - progress = registered_contacts > 0 ? min(1, contacts_messaged / registered_contacts) : 0.
    Clamp retained because the registered set can shrink (re-check) below contacts
    already messaged.
  - Inline query type extended with the two new fields.

  Sync artifacts: lovable-handoff/openapi.json regenerated from live app;
  frontend src/types/api.ts + src/types-openapi.json AnalyticsCards updated.

  Tests: tests/test_phase5_analytics.py — two schema-shape assertions updated to
  include the new keys (D-16 parity preserved: all 4 scopes still return identical shape).

verification: |
  - Live-DB SQL validation: Barter 42/48, Pasha 2/2, test-agent 2/2 (numerator =
    distinct outbound conversations, denominator = registered contacts in folder).
  - Ran _compute_cards through the rebuilt API container against prod DB:
    * workspace scope → contacts_messaged=0, registered_contacts=0 (progress bar suppressed)
    * Pasha   → 0% (old finishes/sent) → 100% (new) — the exact reported bug, fixed
    * test-agent → 0% → 100% — fixed
    * Barter  → 4% → 88% — meaningful progress
  - API container rebuilt + restarted cleanly (no migration needed; Pydantic-only change),
    /api/v1/health → healthy.
  - pytest (test-overlay) tests/test_phase5_analytics.py → 14 passed.
  - Dashboard-level analytics verified unaffected: /api/v1/analytics/funnel returns
    stage counts (sent→replied→engaged→lead→handoff), no progress %, no denominator bug.
    The bug was isolated to the campaign-list progress bar.
  - Pre-existing unrelated test failures (test_send_campaign / queue / webhook —
    `_hash_api_key` ImportError) confirmed present with my changes stashed; not caused
    by this fix.

files_changed:
  - app/schemas/__init__.py (backend, repo: outreach-platform)
  - app/routers/analytics.py (backend, repo: outreach-platform)
  - tests/test_phase5_analytics.py (backend, repo: outreach-platform)
  - lovable-handoff/openapi.json (backend, repo: outreach-platform)
  - ../aimly-tg-outreach/src/routes/_authenticated/campaigns.index.tsx (frontend, repo: aimly-tg-outreach)
  - ../aimly-tg-outreach/src/types/api.ts (frontend, repo: aimly-tg-outreach)
  - ../aimly-tg-outreach/src/types-openapi.json (frontend, repo: aimly-tg-outreach)
