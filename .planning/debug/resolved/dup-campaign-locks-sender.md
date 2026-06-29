---
status: resolved
trigger: |
  при дублировании кампании создали новую компанию b234e3cb-215d-43c8-ab8e-2e6f2efca3dd
  перенеслись аккаунты рассылки с ней же. не могу из ui удалить ru-account-3 у Полины в тг
  +79587860771
created: 2026-06-29
updated: 2026-06-29
---

# Debug: campaign duplication carries senders → sender undeletable

## Symptoms

- **Expected**: Duplicating a campaign should NOT carry over the attached sender accounts (рассылки). A sender account (ru-account-3, Polina, +79587860771) should be deletable from the UI.
- **Actual**: Duplicating a campaign created a new campaign `b234e3cb-215d-43c8-ab8e-2e6f2efca3dd` AND copied the sender attachments to it. The delete button for ru-account-3 is now **disabled** (greyed out) in the UI.
- **UI location**: inside the campaign card ("Внутри кампании").
- **Delete symptom**: button is inactive/disabled.
- **Account**: ru-account-3, owner Polina, phone +79587860771.
- **Duplicated campaign id**: `b234e3cb-215d-43c8-ab8e-2e6f2efca3dd`.

## Goals (user-confirmed, all three)

1. **Operational**: be able to delete / detach ru-account-3 now.
2. **Bug fix**: campaign duplication must NOT carry over attached senders.
3. **Bug fix / UX**: sender attachment to a (duplicated) campaign blocks deletion — need an unlink path so an attached sender can be detached/deleted.

## Current Focus

- hypothesis: CONFIRMED (partially refined). Root cause #1: duplicate endpoint copies campaign_senders. Root cause #2 (UI disable): the delete button is disabled because `_build_attached_senders` reports `locked_by_campaign_id` whenever the sender is attached to ANY OTHER running campaign — and ru-account-3 is attached to a *running* campaign (b7cc7d06), not the duplicate itself. The duplicate (draft) does not by itself trigger the API delete guard.
- test: DB inspection of campaign_senders for sender 488c1c64 + read of all code paths.
- expecting: confirmed
- next_action: CHECKPOINT — present fix options to user before applying any change.

## Evidence

- timestamp: 2026-06-29
  checked: app/routers/campaigns.py:843-927 (duplicate_campaign)
  found: Lines 910-919 explicitly copy campaign_senders rows from src to the new campaign (status='draft'). This is intentional ("for parity with src") per C-11, but it is the source of goal #2 — the carry-over of attached senders.
  implication: To stop carry-over, remove/guard the campaign_senders copy block in duplicate_campaign.

- timestamp: 2026-06-29
  checked: DB campaign b234e3cb-215d-43c8-ab8e-2e6f2efca3dd
  found: status='draft', workspace bb96789d, name 'Barter - first outreach_логистика (очищенная база noco)'. Created 2026-06-29 09:12 (the duplicate).
  implication: Duplicate is draft, so the API delete guard (status='running') does NOT fire on the duplicate.

- timestamp: 2026-06-29
  checked: DB sender ru-account-3 (slug sender-8539506204, id 488c1c64-dab8-4ee0-83a0-993851b08086, phone +79587860771, owner Полина)
  found: lifecycle_status=active, restriction_status=none. Attached to THREE campaigns: 425e3eed test-agent-camp (paused), b7cc7d06 'пищевка + химия' (RUNNING), b234e3cb 'логистика' (draft, the duplicate).
  implication: The real blocker is the running campaign b7cc7d06 — NOT the duplicate. The duplicate added a 3rd attachment but is draft.

- timestamp: 2026-06-29
  checked: app/routers/senders.py:206-234 (_check_sender_not_in_running_campaign) + delete_sender:488-528
  found: DELETE /senders/{slug} raises 409 SENDER_USED_BY_RUNNING_CAMPAIGN only when sender is attached to a campaign with status='running'. draft/paused do NOT block. The 409 detail lists the blocking campaigns.
  implication: API-level hard-delete is blocked by b7cc7d06 (running), not the duplicate.

- timestamp: 2026-06-29
  checked: app/routers/campaigns.py:238-274 (_build_attached_senders) + schemas/__init__.py:607-627 (CampaignSenderAttach)
  found: attached_senders[] carries locked_by_campaign_id/name set to the FIRST OTHER running campaign the sender belongs to (status='running', id != current card). The UI uses this to grey out the delete/detach button inside a campaign card. Inside the duplicate card, ru-account-3.locked_by_campaign_id = b7cc7d06 (the running campaign) → button disabled.
  implication: Goal #3 (UI disabled inside the card) is the locked_by_campaign_id signal, driven by the running campaign. A detach endpoint already exists (DELETE /campaigns/{id}/senders/{sender_id}, campaigns.py:991) but the UI hides it when locked_by_campaign_id is set.

- timestamp: 2026-06-29
  checked: app/routers/campaigns.py:991-1054 (detach_sender)
  found: Detach endpoint exists. Guards: D-03 min-pool (only blocks last sender of a RUNNING campaign), D-04 cold-pending (blocks if un-sent pending rows in THIS campaign for this sender). For the draft duplicate b234e3cb: cnt>1 is not required (status != running), and cold-pending only checks queue rows for campaign b234e3cb (a fresh draft → no queue items copied per C-11) → detach from the duplicate would succeed at the API level.
  implication: Operationally (goal #1), detaching ru-account-3 from the duplicate b234e3cb via the detach endpoint is safe and unblocked at the API. The UI just hides the button. The running-campaign attachment (b7cc7d06) is a separate, legitimate lock.

## Eliminated

- hypothesis: The duplicate campaign (draft) itself triggers the API delete-guard 409.
  evidence: delete guard and detach guard both key on status='running'; the duplicate is 'draft'. The actual API block comes from the separate running campaign b7cc7d06.
  timestamp: 2026-06-29

## Resolution

root_cause: |
  Two coupled defects.
  (A) Carry-over: duplicate_campaign (campaigns.py:910-919) copies campaign_senders into
      the new draft campaign. So duplicating attached ru-account-3 to b234e3cb.
  (B) UI lock leakage: _build_attached_senders (campaigns.py:238-274) reports
      locked_by_campaign_id for a sender whenever it is in ANY other running campaign.
      ru-account-3 is in a running campaign (b7cc7d06), so inside the duplicate's card the
      delete/detach button is greyed out — even though detaching from the *draft* duplicate
      is itself safe. The genuine running-campaign lock is correct; what's wrong is that the
      UI offers no detach path while locked, and the duplicate created an extra attachment
      that shouldn't exist.
fix: |
  User decisions: Goal #1 = no operational change now (fix code, user detaches via UI later);
  running campaign b7cc7d06 left untouched; Goal #2 = remove sender copy; Goal #3 = frontend-only.

  (Goal #2 — backend, this repo) Removed the campaign_senders copy block from
  duplicate_campaign (app/routers/campaigns.py). The duplicate now starts with an empty
  sender pool. Updated docstring + file-header endpoint comment. Rewrote the test
  test_duplicate_endpoint_copies_row_not_senders_queue_assignments
  (tests/test_campaign_router.py) to assert attached_senders == [] and campaign_senders
  count == 0 for the duplicate.

  (Goal #3 — frontend, separate repo /root/apps/aimly/aimly-tg-outreach) Changed the
  detach (✕) button in src/routes/_authenticated/campaigns.$id.tsx from
  `disabled={locked || busy}` to `disabled={busy}`, with an accurate tooltip explaining the
  sender is also in a running campaign and detaching here only affects this campaign. The
  backend already enforces the real guards (MIN_POOL_GUARD / DETACH_BLOCKED_PENDING → 409)
  and the UI surfaces those via the action banner (detachMut.onError).

  (Goal #1) No DB/API mutation performed. After deploy, the user detaches ru-account-3 from
  the draft duplicate b234e3cb through the now-enabled UI button.
verification: |
  Backend: docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
    tests/test_campaign_router.py -q → 24 passed.
  Frontend: npx tsc --noEmit → exit 0.
  NOT yet deployed (prod rebuild + Cloudflare frontend deploy pending user go-ahead).
files_changed:
  - app/routers/campaigns.py
  - tests/test_campaign_router.py
  - "(separate repo) ../aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx"
